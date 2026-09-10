#!/usr/bin/env python3
"""Byte-boundary regression suite for the serve WebSocket client (A05).

Audit 2026-09-10 finding A05 ("WebSocket parsing loses data across ordinary
read boundaries") reproduced three defects in `dashboard/hermes_rpc.py`'s
hand-rolled RFC 6455 client:

  1. the HTTP 101 and the first frame can share one TCP read, and the
     constructor reset `_buf` to b"" — the first event of the turn vanished;
  2. fragment assembly lived in a LOCAL `message`, so a poll timeout between
     two fragments returned None and forgot the prefix (`abc` + `def` came
     back as `def`);
  3. only the first two-byte header read caught socket.timeout, so a timeout
     while reading an extended length or a payload escaped as an exception
     AFTER bytes had been consumed — straight into server.py's CLI fallback.

The first three sections below are the audit's own probes turned into
assertions of the CORRECT behaviour; the rest are the byte-boundary cases the
audit asked for (header split across reads, the 126/127 length forms, masked
vs unmasked frames, control frames, protocol errors, and the size caps).

Everything runs against a fake socket: no dashboard, no serve backend, no
network, no model. Run:  python3 t_a05_ws_frames.py [--repo /path/to/repo]
"""
import collections
import importlib.util
import os
import socket
import struct
import sys
import threading

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def raises(name, exc, fn, extra=""):
    try:
        got = fn()
    except exc:
        check(name, True)
        return
    except Exception as e:                      # noqa: BLE001 - report it
        check(name, False, "raised %r, wanted %s" % (e, exc.__name__))
        return
    check(name, False, "returned %r, wanted %s %s" % (got, exc.__name__, extra))


def section(t):
    print("\n== %s ==" % t)


# --------------------------------------------------------------------------
# locate the repo (never hardcode a home path — CI greps for those)
# --------------------------------------------------------------------------
def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    env = os.environ.get("HERMES_REPO")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    if os.path.exists(os.path.join(cand, "dashboard", "server.py")):
        return cand
    return os.path.join(os.path.expanduser("~"), "HermesAssistant")


REPO = _find_repo()
RPC_PATH = os.path.join(REPO, "dashboard", "hermes_rpc.py")

# hermes_rpc is import-safe (constants + classes, no threads, no sockets at
# import time), so load the REAL module the way server.py does rather than a
# copy of it.
sys.path.insert(0, os.path.join(REPO, "dashboard"))
_spec = importlib.util.spec_from_file_location("hermes_rpc_under_test", RPC_PATH)
rpc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rpc)
WSClient, WSError = rpc.WSClient, rpc.WSError


# --------------------------------------------------------------------------
# fake socket: a queue of chunks; a socket.timeout instance in the queue is
# raised instead of returned, exactly like the audit probe's fake.
# --------------------------------------------------------------------------
class FakeSocket:
    def __init__(self, chunks):
        self.chunks = collections.deque(chunks)
        self.sent = []
        self.reads = 0
        self.closed = False
        self.timeouts = []

    def recv(self, size):
        self.reads += 1
        item = self.chunks.popleft() if self.chunks else b""
        if isinstance(item, Exception):
            raise item
        return item

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


def frame(opcode, payload, fin=True, mask=False, force_len=None):
    """One RFC 6455 frame. `force_len` picks an over-long length form."""
    b1 = (0x80 if fin else 0) | opcode
    n = len(payload) if force_len is None else force_len
    flag = 0x80 if mask else 0
    if force_len is not None or n >= 65536:
        head = struct.pack("!BBQ", b1, flag | 127, n)
    elif n >= 126:
        head = struct.pack("!BBH", b1, flag | 126, n)
    else:
        head = struct.pack("!BB", b1, flag | n)
    if mask:
        m = b"\x37\xfa\x21\x3d"
        return head + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload))
    return head + payload


def client(chunks, bare=False, **attrs):
    """A WSClient over a fake socket, skipping the handshake.

    `bare=True` sets ONLY the two attributes the audit probe sets, so the
    class-level parser defaults (_frag/_frag_opcode) are exercised too.
    """
    c = WSClient.__new__(WSClient)
    c.sock = FakeSocket(chunks)
    c._buf = b""
    c._lock = threading.Lock()
    if not bare:
        c._frag = b""
        c._frag_opcode = None
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


TEXT, BIN, CLOSE, PING, PONG, CONT = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0

# --------------------------------------------------------------------------
section("the audit's three probes, asserted as correct behaviour")
# --------------------------------------------------------------------------
# 1. upgrade response sharing a read with the first frame
fake = FakeSocket([b"HTTP/1.1 101 Switching Protocols\r\n\r\n" + frame(TEXT, b"ok")])
_real_connect = socket.create_connection
socket.create_connection = lambda *a, **k: fake
try:
    up = WSClient("127.0.0.1", 1, "/fake")
finally:
    socket.create_connection = _real_connect
check("upgrade remainder is kept in _buf", up._buf == frame(TEXT, b"ok"),
      repr(up._buf))
check("the frame that shared the 101's read is delivered",
      up.recv_text(1) == "ok")

# the same bytes, but the handshake arrives in three reads
fake2 = FakeSocket([b"HTTP/1.1 101 Switching ", b"Protocols\r\nX: y\r\n",
                    b"\r\n" + frame(TEXT, b"hi")])
socket.create_connection = lambda *a, **k: fake2
try:
    up2 = WSClient("127.0.0.1", 1, "/fake")
finally:
    socket.create_connection = _real_connect
check("split handshake still keeps the remainder", up2.recv_text(1) == "hi")

# a non-101 response is still a handshake failure
fake3 = FakeSocket([b"HTTP/1.1 401 Unauthorized\r\n\r\n"])
socket.create_connection = lambda *a, **k: fake3
try:
    raises("non-101 handshake still raises WSError", WSError,
           lambda: WSClient("127.0.0.1", 1, "/fake"))
finally:
    socket.create_connection = _real_connect

# 2. fragmented message with a poll timeout between the fragments
c = client([frame(TEXT, b"abc", fin=False), socket.timeout(),
            frame(CONT, b"def")])
check("timeout between fragments returns None", c.recv_text(1) is None)
check("the fragment prefix survives the timeout", c.recv_text(1) == "abcdef",
      "lost the prefix")

c = client([frame(TEXT, b"abc", fin=False), socket.timeout(),
            frame(CONT, b"def")], bare=True)
check("class-level fragment defaults work on a bare instance",
      (c.recv_text(1), c.recv_text(1)) == (None, "abcdef"))

# 3. timeout in the middle of a frame
c = client([b"\x81\x03", socket.timeout(), b"abc"])
check("timeout after a header returns None, not socket.timeout",
      c.recv_text(1) is None)
check("the consumed header is still buffered afterwards",
      c.recv_text(1) == "abc")

c = client([frame(TEXT, b"x" * 300)[:3], socket.timeout(),
            frame(TEXT, b"x" * 300)[3:]])
check("timeout inside a 126 extended length is resumable",
      (c.recv_text(1), c.recv_text(1)) == (None, "x" * 300))

# --------------------------------------------------------------------------
section("byte boundaries")
# --------------------------------------------------------------------------
c = client([b"\x81", b"\x03ab", b"c"])
check("header split across reads", c.recv_text(1) == "abc")

c = client([frame(TEXT, b"y" * 200)])
check("126 length form (200 bytes)", c.recv_text(1) == "y" * 200)

payload = b"0123456789abcdef" * 4800                   # 76 800 bytes -> 127 form
raw = frame(TEXT, payload)
check("a >64 KiB payload really uses the 127 length form",
      (raw[1] & 0x7F) == 127)
c = client([raw[:9], raw[9:40000], raw[40000:]])       # split inside the length
check("127 length form across three reads",
      c.recv_text(1) == payload.decode())

c = client([frame(TEXT, b"\xff\xfe bad utf-8")])
check("invalid UTF-8 is replaced, never raised",
      c.recv_text(1).endswith(" bad utf-8"))

c = client([frame(TEXT, b"masked", mask=True)])
check("masked server frame is unmasked (lenient)", c.recv_text(1) == "masked")

c = client([frame(TEXT, b"plain")])
check("unmasked server frame", c.recv_text(1) == "plain")

c = client([frame(TEXT, b"one") + frame(TEXT, b"two") + frame(TEXT, b"three")])
check("three messages in one read are delivered in order",
      [c.recv_text(1) for _ in range(3)] == ["one", "two", "three"])

c = client([frame(TEXT, b"a", fin=False) + frame(CONT, b"b", fin=False),
            frame(CONT, b"c")])
check("three-fragment message reassembles", c.recv_text(1) == "abc")

c = client([b"", ])
raises("EOF mid-stream is a WSError", WSError, lambda: c.recv_text(1))

c = client([socket.timeout(), socket.timeout()])
check("a quiet socket just returns None", c.recv_text(0.01) is None)

c = client([])
check("timeout<=0 returns None without reading",
      c.recv_text(0) is None and c.sock.reads == 0)

# --------------------------------------------------------------------------
section("control frames")
# --------------------------------------------------------------------------
c = client([frame(TEXT, b"ab", fin=False) + frame(PING, b"pp")
            + frame(CONT, b"cd")])
check("a ping between fragments does not corrupt the message",
      c.recv_text(1) == "abcd")
check("the ping was answered with a masked pong",
      len(c.sock.sent) == 1 and c.sock.sent[0][0] == 0x80 | PONG
      and bool(c.sock.sent[0][1] & 0x80))

c = client([frame(PONG, b"x") + frame(TEXT, b"after")])
check("an unsolicited pong is ignored", c.recv_text(1) == "after")

c = client([frame(BIN, b"\x00\x01") + frame(TEXT, b"text")])
check("a binary message is skipped, the next text one is returned",
      c.recv_text(1) == "text")

c = client([frame(CLOSE, b"\x03\xe8")])
raises("a close frame raises WSError", WSError, lambda: c.recv_text(1))

c = client([frame(PING, b"z" * 130)])
raises("an over-long control frame is refused", WSError, lambda: c.recv_text(1))

c = client([frame(PING, b"z", fin=False)])
raises("a fragmented control frame is refused", WSError, lambda: c.recv_text(1))

# --------------------------------------------------------------------------
section("protocol errors and size caps")
# --------------------------------------------------------------------------
c = client([frame(CONT, b"orphan")])
raises("a continuation with nothing to continue is refused", WSError,
       lambda: c.recv_text(1))

c = client([frame(TEXT, b"a", fin=False) + frame(TEXT, b"b")])
raises("a new data frame inside a fragmented message is refused", WSError,
       lambda: c.recv_text(1))

c = client([frame(TEXT, b"", force_len=64 * 1024 * 1024)])
raises("a frame over MAX_FRAME_BYTES is refused", WSError,
       lambda: c.recv_text(1))
check("the oversized frame's payload was never buffered",
      c.sock.reads <= 1 and len(c._buf) < 4096, "reads=%d" % c.sock.reads)
check("the connection is closed after an oversized frame", c.sock.closed)

c = client([frame(TEXT, b"123456", fin=False), frame(CONT, b"789")],
           MAX_MESSAGE_BYTES=8)
raises("a message over MAX_MESSAGE_BYTES is refused", WSError,
       lambda: (c.recv_text(1), c.recv_text(1)))
check("the connection is closed after an oversized message", c.sock.closed)

check("the caps are documented on the class",
      WSClient.MAX_FRAME_BYTES == 16 * 1024 * 1024
      and WSClient.MAX_MESSAGE_BYTES == 64 * 1024 * 1024,
      "%s / %s" % (WSClient.MAX_FRAME_BYTES, WSClient.MAX_MESSAGE_BYTES))

# --------------------------------------------------------------------------
section("public API unchanged (run_turn / aux_branch must not need edits)")
# --------------------------------------------------------------------------
check("recv_text/send_text/close still exist",
      all(callable(getattr(WSClient, n, None))
          for n in ("recv_text", "send_text", "close")))
c = client([])
c.send_text("hello")
sent = c.sock.sent[0]
check("send_text still writes one masked text frame",
      sent[0] == 0x81 and bool(sent[1] & 0x80) and (sent[1] & 0x7F) == 5
      and bytes(b ^ sent[2:6][i % 4]
                for i, b in enumerate(sent[6:])) == b"hello")

# --------------------------------------------------------------------------
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
sys.exit(1 if FAILS else 0)
