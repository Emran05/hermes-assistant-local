"""Minimal stdlib client for the `hermes serve` JSON-RPC/WebSocket backend.

Speaks RFC 6455 (client side, masked frames) over a loopback TCP socket —
no third-party deps, matching the dashboard's stdlib-only rule. One
connection per agent turn: connect, ensure a session, prompt.submit, then
consume `event` notifications until message.complete / error.

Auth: `hermes serve` runs with a pinned HERMES_DASHBOARD_SESSION_TOKEN
(written by install-services.sh to ~/.hermes/dashboard/serve-token); we pass
it as ?token= on the WS URL, same as the official SPA does on loopback.
"""

import base64
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time

SERVE_HOST = "127.0.0.1"
SERVE_PORT = int(os.environ.get("HERMES_SERVE_PORT", "9119"))
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard",
                          "serve-token")
TURN_TIMEOUT = int(os.environ.get("HUB_TURN_TIMEOUT", "600"))
RECORDER_HOOK = None   # set by dashboard/aux_recorder.py; called (sid, etype, payload)
# set by dashboard/aux_branch.py; called (chat_meta) -> extra `session.create`
# params. Returns {} for an ordinary conversation (so the minting path below is
# unchanged) and {"messages": [...seed...], "parent_session_id": ...} for a
# branched one, which is how a fork carries its pre-branch history without a
# single change to hermes-agent.
SEED_HOOK = None

try:
    import permissions as _perm   # P1.3 graduated permission tiers (policy engine)
except Exception:                 # engine missing/broken must never kill a turn
    _perm = None


def read_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


class WSError(Exception):
    pass


class WSClient:
    """Tiny RFC 6455 client: text frames only, handles ping/close/fragments.

    ALL parser state lives on the INSTANCE (`_buf` raw bytes, `_frag`/
    `_frag_opcode` the fragmented message in progress) and a frame is consumed
    out of `_buf` only once its whole payload is buffered. So every ordinary
    read boundary is resumable: a poll timeout mid-header, mid-payload or
    between two fragments returns None with nothing lost, and the caller's
    next `recv_text()` continues the same message. (Audit 2026-09-10 A05 —
    the old reader consumed a header before it could time out, kept the
    fragment prefix in a local, and dropped bytes that shared a read with the
    HTTP 101.)
    """

    # Class-level defaults so an instance built without __init__ (the audit's
    # AST-extracted probes, unit fixtures) still parses correctly.
    _frag = b""            # payload of the fragmented message in progress
    _frag_opcode = None    # its opcode (0x1 text / 0x2 binary); None = idle
    MAX_FRAME_BYTES = 16 * 1024 * 1024     # one frame, before reassembly
    MAX_MESSAGE_BYTES = 64 * 1024 * 1024   # one reassembled message

    def __init__(self, host, port, path, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("connection closed during handshake")
            resp += chunk
            if len(resp) > 65536:
                raise WSError("oversized handshake response")
        head, _, rest = resp.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise WSError(f"handshake rejected: {status}")
        # Whatever followed the header terminator is already WebSocket: the
        # server is free to put the 101 and its first frame in ONE segment,
        # and this used to be reset to b"" — i.e. the first event of the turn
        # was silently discarded whenever that happened.
        self._buf = rest
        self._frag = b""
        self._frag_opcode = None
        self._lock = threading.Lock()

    def _fail(self, msg):
        """Close and raise — the stream is out of sync or over a bound."""
        try:
            self.close()
        except Exception:
            pass
        raise WSError(msg)

    def _fill(self, timeout):
        """One socket read appended to `_buf`. False on timeout; raises on EOF."""
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not chunk:
            raise WSError("connection closed")
        self._buf += chunk
        return True

    def _take_frame(self):
        """One WHOLE buffered frame as (fin, opcode, payload), else None.

        Consumes from `_buf` only when every byte of the frame is present, so
        returning None never loses anything the socket already handed us.
        """
        buf = self._buf
        if len(buf) < 2:
            return None
        b1, b2 = buf[0], buf[1]
        n, off = b2 & 0x7F, 2
        if n == 126:
            if len(buf) < off + 2:
                return None
            n = struct.unpack("!H", buf[off:off + 2])[0]
            off += 2
        elif n == 127:
            if len(buf) < off + 8:
                return None
            n = struct.unpack("!Q", buf[off:off + 8])[0]
            off += 8
        mask = b""
        if b2 & 0x80:  # masked server frame — never valid, but be lenient
            if len(buf) < off + 4:
                return None
            mask, off = buf[off:off + 4], off + 4
        if n > self.MAX_FRAME_BYTES:   # refuse BEFORE buffering the payload
            self._fail("frame too large: %d bytes (max %d)"
                       % (n, self.MAX_FRAME_BYTES))
        if len(buf) < off + n:
            return None
        data = buf[off:off + n]
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._buf = buf[off + n:]
        return b1 & 0x80, b1 & 0x0F, data

    def send_text(self, text):
        payload = text.encode()
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        with self._lock:
            self.sock.sendall(header + mask + masked)

    def _send_control(self, opcode, payload=b""):
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(struct.pack("!BB", 0x80 | opcode,
                                          0x80 | len(payload)) + mask + masked)

    def recv_text(self, timeout):
        """Next complete text message, or None on timeout.

        `timeout` bounds the WHOLE message on a monotonic deadline. On timeout
        every byte already read and every fragment already received stays on
        the instance, so the next poll resumes the same message instead of
        losing its prefix or escaping as socket.timeout into the CLI fallback.
        """
        # Local import: `time` is a module global here, but this class is also
        # exec'd on its own by the audit probe / unit fixtures, whose namespace
        # only carries socket/struct/secrets/threading/base64.
        import time as _time
        deadline = _time.monotonic() + max(0.0, float(timeout))
        while True:
            frame = self._take_frame()
            if frame is None:
                left = deadline - _time.monotonic()
                if left <= 0 or not self._fill(left):
                    return None
                continue
            fin, opcode, data = frame
            if opcode >= 0x8:                      # control frame
                if not fin or len(data) > 125:     # never fragmented, ≤125 B
                    self._fail("invalid control frame")
                if opcode == 0x9:
                    self._send_control(0xA, data)  # ping -> pong
                elif opcode == 0x8:
                    raise WSError("server closed connection")
                continue                           # pong / reserved: ignore
            if opcode == 0x0:
                if self._frag_opcode is None:
                    self._fail("continuation frame with nothing to continue")
            elif self._frag_opcode is not None:
                self._fail("new data frame inside a fragmented message")
            else:
                self._frag_opcode = opcode
            if len(self._frag) + len(data) > self.MAX_MESSAGE_BYTES:
                self._fail("message too large: over %d bytes"
                           % self.MAX_MESSAGE_BYTES)
            self._frag += data
            if not fin:
                continue
            payload, op = self._frag, self._frag_opcode
            self._frag, self._frag_opcode = b"", None
            if op == 0x1:
                return payload.decode(errors="replace")
            # binary message — ignore it and keep waiting for a text one

    def close(self):
        try:
            self._send_control(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class ServeSession:
    """One JSON-RPC conversation with hermes serve over a fresh WS."""

    def __init__(self):
        token = read_token()
        if not token:
            raise WSError(f"no serve token at {TOKEN_FILE}")
        self.ws = WSClient(SERVE_HOST, SERVE_PORT, f"/api/ws?token={token}")
        self._next_id = 1
        self._events = []

    def call(self, method, params, timeout=30, on_sent=None):
        """One JSON-RPC request/response.

        `on_sent` fires the instant the request bytes have been written to the
        socket, BEFORE we start waiting for the reply. run_turn uses it to mark
        a prompt as submitted: a call that times out waiting for its response
        may still have been received and acted on, and the chat fallback has to
        know that (2026-09-10 audit A04) — a retry after a submitted prompt can
        repeat a tool action.
        """
        rid = self._next_id
        self._next_id += 1
        self.ws.send_text(json.dumps({"jsonrpc": "2.0", "id": rid,
                                      "method": method, "params": params}))
        if on_sent is not None:
            try:
                on_sent()
            except Exception:
                pass          # bookkeeping must never break a turn
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv_text(timeout=min(2.0, deadline - time.time()))
            if raw is None:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("id") == rid:
                if "error" in obj:
                    raise WSError(obj["error"].get("message", "rpc error"))
                return obj.get("result") or {}
            if obj.get("method") == "event":
                self._events.append(obj.get("params") or {})
        raise WSError(f"timeout waiting for {method}")

    def next_event(self, timeout):
        if self._events:
            return self._events.pop(0)
        raw = self.ws.recv_text(timeout=timeout)
        if raw is None:
            return None
        try:
            obj = json.loads(raw)
        except ValueError:
            return None
        if obj.get("method") == "event":
            return obj.get("params") or {}
        return None

    def close(self):
        self.ws.close()


def turn_status(session_id, timeout=8):
    """Best-effort `session.status` for a session whose stream we lost.

    Opens a FRESH connection — the point is that the old one is gone. Returns
    the RPC result dict, or None if serve is unreachable / does not know the
    session. Never raises. Used by server.py's chat fallback to say something
    truthful about a turn that was already submitted when the stream dropped,
    instead of silently re-running it (2026-09-10 audit A04).
    """
    if not session_id:
        return None
    srv = None
    try:
        srv = ServeSession()
        return srv.call("session.status", {"session_id": session_id},
                        timeout=timeout)
    except Exception:
        return None
    finally:
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass


def run_turn(job, chat_meta, prompt, save_meta, source="hub"):
    """Drive one agent turn; mutates `job` dict in place as events stream.

    job fields consumed by the poll endpoint: state, text, status, approval,
    reply, ok. `chat_meta` carries serve_sid/serve_key persistence via
    save_meta() so the conversation resumes across turns and serve restarts.

    This function sets `state="done"` plus reply/ok when the turn ends, but it
    does NOT set `done`. server.py's _finish_chat_job publishes that, and only
    after the reply has been written to chats/<session>.json — `done` used to
    reach the UI first, so a reply could be shown as delivered and then fail to
    persist with nothing to say so (2026-09-10 audit A03).

    It also stamps three bookkeeping flags the chat fallback needs: `submit_sent`
    (the prompt.submit request left the socket), `submitted` (serve acknowledged
    it) and `tool_events` (how many tool.* events arrived). See A04.

    `source` is written to the serve session's `sessions.source` column in
    state.db (the gateway takes it verbatim: `str(params.get("source") or
    "tui")`). It stays "hub" for every real dashboard turn; server.py's
    prewarm-after-wake passes its own value so that synthetic turn can be told
    apart from genuine user activity in `_newest_external_turn_ts()`.
    """
    srv = ServeSession()
    try:
        sid = chat_meta.get("serve_sid") or ""
        key = chat_meta.get("serve_key") or ""
        alive = False
        if sid:
            try:
                srv.call("session.status", {"session_id": sid}, timeout=10)
                alive = True
            except WSError:
                alive = False
        if not alive and key:
            try:
                res = srv.call("session.resume", {"session_id": key},
                               timeout=20)
                sid = res.get("session_id") or sid
                alive = bool(sid)
            except WSError:
                alive = False
        if not alive:
            params = {"title": chat_meta.get("title") or "Hub chat",
                      "cwd": os.path.expanduser("~"), "source": source}
            # A branched conversation is born with the transcript it forked
            # from (aux_branch.br_seed_params). Ordinary chats get {} back and
            # take exactly the call they always took; a hook that raises is a
            # branch with no context, never a lost turn — but that used to be
            # a bare `except: pass` with nothing in the log and nothing on
            # chat_meta, so a branch silently starting with no memory of its
            # source was indistinguishable from a normal one until the user
            # noticed. Log it, and for a branch specifically (chat_meta has
            # `forked_from` — set by aux_branch, just a dict key here, no
            # import needed) stamp `seeded` on chat_meta; save_meta() below
            # persists it so aux_branch's tree/node views can report it.
            if SEED_HOOK is not None:
                try:
                    seed_update = SEED_HOOK(chat_meta) or {}
                except Exception as e:
                    print(f"[hermes_rpc] SEED_HOOK raised ({type(e).__name__}: "
                          f"{e}) — session created with no seeded context",
                          file=sys.stderr, flush=True)
                    seed_update = {}
                params.update(seed_update)
                if chat_meta.get("forked_from"):
                    chat_meta["seeded"] = bool(seed_update.get("messages"))
            res = srv.call("session.create", params, timeout=20)
            sid = res.get("session_id") or ""
            key = res.get("stored_session_id") or res.get("session_key") or ""
            if not sid:
                raise WSError("session.create returned no session_id")
        chat_meta["serve_sid"], chat_meta["serve_key"] = sid, key
        save_meta()

        # `submit_sent` is set from inside srv.call the moment the request has
        # been written to the socket — deliberately BEFORE the response is
        # confirmed. From here on the prompt may have reached the agent, so
        # server.py's _chat_worker must never re-run it through the one-shot
        # CLI (2026-09-10 audit A04). `submitted` is the confirmed form.
        def _sent():
            job["submit_sent"] = True

        srv.call("prompt.submit", {"session_id": sid, "text": prompt},
                 timeout=30, on_sent=_sent)
        job["submitted"] = True
        job["_submitted_ts"] = time.time()   # metrics P1.5: setup/serve TTFT split

        text, status = "", ""
        deadline = time.time() + TURN_TIMEOUT
        while time.time() < deadline:
            choice = job.pop("pending_choice", None)
            if choice:
                try:
                    srv.call("approval.respond",
                             {"session_id": sid, "choice": choice}, timeout=15)
                except WSError as e:
                    job["status"] = f"approval failed: {e}"
                job["approval"] = None
                job["state"] = "running"
                if _perm and job.get("_approval_payload"):
                    try:
                        _perm.audit(job, job.pop("_approval_payload"),
                                    {"tier": "ask"}, "user-" + str(choice))
                    except Exception:
                        pass
            ev = srv.next_event(timeout=1.0)
            if ev is None:
                continue
            if ev.get("session_id") not in ("", sid):
                continue
            etype = ev.get("type") or ""
            payload = ev.get("payload") or {}
            if etype in ("tool.start", "tool.generating", "tool.complete"):
                # The agent has begun DOING things. Independently of
                # `submitted`, this is the signal that a re-run could repeat a
                # side effect (2026-09-10 audit A04).
                job["tool_events"] = int(job.get("tool_events") or 0) + 1
            if RECORDER_HOOK and etype in ("tool.start", "tool.complete"):
                try:
                    RECORDER_HOOK(sid, etype, payload)
                except Exception:
                    pass   # recorder must never break a turn
            if etype == "message.delta":
                text += (payload.get("text") or payload.get("delta") or "")
                job["text"] = text
            elif etype in ("tool.start", "tool.generating"):
                job["status"] = "using " + (payload.get("name")
                                            or payload.get("tool") or "a tool")
            elif etype == "tool.complete":
                job["status"] = ""
            elif etype == "status.update":
                job["status"] = (payload.get("text") or payload.get("kind")
                                 or "")
            elif etype == "approval.request":
                # P1.3: consult the graduated-permission policy. Sends only
                # "once"/"deny" upstream (never session/always), so hermes's
                # own allowlists never grow. decide() never raises; every
                # failure path falls through to ASK (fail-safe).
                v = _perm.decide(payload) if _perm else {"tier": "ask", "reason": ""}
                if v.get("tier") == "auto":
                    try:
                        srv.call("approval.respond",
                                 {"session_id": sid, "choice": "once"}, timeout=15)
                        job["status"] = "auto-approved · " + v.get("reason", "")
                        if _perm:
                            _perm.audit(job, payload, v, "auto-approved")
                        continue
                    except WSError:
                        pass               # fail open to ASK
                elif v.get("tier") == "never":
                    try:
                        srv.call("approval.respond",
                                 {"session_id": sid, "choice": "deny"}, timeout=15)
                        job["status"] = "blocked by policy · " + v.get("reason", "")
                        if _perm:
                            _perm.audit(job, payload, v, "auto-denied")
                        continue
                    except WSError:
                        pass               # user can still deny by hand
                payload["_policy"] = v
                job["_approval_payload"] = payload
                job["approval"] = payload
                job["state"] = "approval"
                if _perm:
                    _perm.audit(job, payload, v, "asked")
            elif etype == "message.complete":
                final = payload.get("text") or text
                job.update(reply=final or "(empty response)", ok=True,
                           state="done")
                return
            elif etype == "error":
                msg = payload.get("message") or payload.get("text") or "agent error"
                job.update(reply=msg, ok=False, state="done")
                return
        job.update(reply=f"The agent took longer than {TURN_TIMEOUT}s and was "
                         "stopped. (Its session may still finish in the "
                         "background.)", ok=False, state="done")
        try:
            srv.call("session.interrupt", {"session_id": sid}, timeout=5)
        except WSError:
            pass
    finally:
        srv.close()
