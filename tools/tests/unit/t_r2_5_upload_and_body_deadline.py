#!/usr/bin/env python3
"""Round-2 regression test for R2-5 (k04a review #2, #3, #5): /api/upload and
the JSON body read.

BEFORE:
  * /api/upload caught only FileExistsError from os.open: an over-long
    sanitized X-Filename (or the -N suffix pushing it past 255 bytes) raised
    ENAMETOOLONG and dropped the connection with no response; so did a
    missing inbox (ENOENT).
  * a 413 sent while a large body was still unread closed the socket with
    that body in the receive buffer, so the client saw a TCP reset (on
    Linux the reset also discards the JSON answer; macOS loopback still
    delivers it, then ECONNRESET — which is what this asserts against).
  * Handler.timeout (20 s) bounds each socket call, so a client sending one
    byte at a time just under it held a thread for as long as it liked (C13).

AFTER: the name is capped at 200 UTF-8 bytes before the suffix, any OSError
from os.open is a 400 (name) / 500 JSON; after a refusal up to ~1 MB of the
body is drained before the close; _load_body and the upload loop read against
a wall-clock deadline (Handler.body_deadline / upload_deadline).

REAL Handler, ast-extracted from dashboard/server.py, on an ephemeral loopback
port; the inbox is a temp dir. HERMES_T_SERVER_SRC=<old server.py> shows it
failing.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def upload(port, name_bytes, body=b"hello"):
    head = (b"POST /api/upload HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n"
            b"X-Filename: " + name_bytes + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
    return H.raw(port, head + body)


def big_refusal(port, path, declared, send):
    """Declare `declared` bytes, push `send` of them without waiting, then
    read the answer. Returns (status|None, error-or-None)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    head = ("POST %s HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n"
            "Content-Length: %d\r\n\r\n" % (path, declared)).encode()
    err = []

    def pump():
        try:
            s.sendall(head + b"x" * send)
        except OSError as e:
            err.append(e)
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    buf = b""
    try:
        t.join(5)
        time.sleep(0.3)
        while True:
            c = s.recv(65536)
            if not c:
                break
            buf += c
    except OSError as e:
        err.append(e)
    finally:
        s.close()
    st = int(buf.split()[1]) if buf.startswith(b"HTTP/") else None
    return st, (err[0] if err else None), buf


def slow(port, path, declared, every, stop_after, extra=b""):
    """Send the head, then one body byte every `every` s for `stop_after` s,
    then go quiet with the socket open. Returns (status|None, seconds)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    s.sendall(("POST %s HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n%s"
               "Content-Length: %d\r\n\r\n" % (path, extra.decode(), declared)).encode())
    t0 = time.monotonic()
    done = threading.Event()
    buf = [b""]

    def reader():
        try:
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf[0] += c
        except OSError:
            pass
        done.set()
    threading.Thread(target=reader, daemon=True).start()
    while time.monotonic() - t0 < stop_after and not done.is_set():
        try:
            s.send(b"{")
        except OSError:
            break
        time.sleep(every)
    done.wait(4.0)
    took = time.monotonic() - t0
    s.close()
    b = buf[0]
    return (int(b.split()[1]) if b.startswith(b"HTTP/") else None), took


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-5-") as d:
        ns = H.http_namespace(d)
        ns["register_post"]("/t/echo", lambda ctx: {"ok": True})

        class Quick(ns["Handler"]):
            timeout = 5                  # per socket call, as before
            body_deadline = 1.0          # R2-5 wall clock (ignored by old code)
            upload_deadline = 1.0

        srv = H.Server(Quick)
        port = srv.port
        inbox = ns["INBOX"]
        try:
            print("\n1. a long X-Filename")
            long_latin = "é".encode("latin-1") * 250 + b".txt"    # 500+ UTF-8 bytes
            st, _, body = upload(port, long_latin)
            j = H.as_json(body) or {}
            check("THE FIX: a 250-char Latin-1 name is stored (200), not dropped",
                  st == 200 and j.get("ok") is True, (st, body[:160]))
            stored = os.path.basename(j.get("path") or "")
            check("...under a name of at most 200 UTF-8 bytes that keeps .txt",
                  stored and len(stored.encode("utf-8")) <= 200
                  and stored.endswith(".txt"), (stored, len(stored.encode())))
            st, _, body = upload(port, long_latin)
            j2 = H.as_json(body) or {}
            check("the same name again gets its -N suffix and still fits",
                  st == 200 and j2.get("path") != j.get("path")
                  and len(os.path.basename(j2.get("path") or "").encode()) <= 255,
                  (st, body[:160]))
            st, _, body = upload(port, b"a" * 300)
            check("a 300-byte ASCII name is stored too",
                  st == 200 and (H.as_json(body) or {}).get("ok") is True,
                  (st, body[:160]))
            os.rename(inbox, inbox + ".gone")
            st, _, body = upload(port, b"x.txt")
            os.rename(inbox + ".gone", inbox)
            j = H.as_json(body) or {}
            check("THE FIX: a missing inbox is a 500 JSON, not a dropped connection",
                  st == 500 and j.get("ok") is False and "ENOENT" in str(j.get("error")),
                  (st, body[:160]))

            print("\n2. a refusal with a large unread body is read, not reset")
            for path, declared in (("/api/upload", 300 * 1024 * 1024),
                                   ("/t/echo", 9 * 1024 * 1024)):
                results = [big_refusal(port, path, declared, 700 * 1024)
                           for _ in range(3)]
                check("THE FIX: %s 413 with 700 KB still unread arrives and the "
                      "socket closes cleanly, no reset (3/3)" % path,
                      all(r[0] == 413 and r[1] is None for r in results),
                      [(r[0], repr(r[1])) for r in results])

            print("\n3. the body read has a wall-clock deadline")
            st, took = slow(port, "/t/echo", 100, 0.25, 3.0)
            check("THE FIX: a JSON body trickling 1 byte / 0.25 s is cut off "
                  "near body_deadline (408)", st == 408 and took < 2.6,
                  (st, round(took, 2)))
            before = set(os.listdir(inbox))
            st, took = slow(port, "/api/upload", 100, 0.25, 3.0,
                            extra=b"X-Filename: slow.bin\r\n")
            check("THE FIX: a trickling upload is cut off near upload_deadline",
                  st == 500 and took < 2.6, (st, round(took, 2)))
            check("...and its partial file is removed",
                  set(os.listdir(inbox)) == before, set(os.listdir(inbox)) - before)
            st, _, body = H.req(port, "POST", "/t/echo", b'{"a": 1}')
            check("an ordinary body is unaffected",
                  st == 200 and (H.as_json(body) or {}).get("ok") is True, (st, body))
            check("no request reached socketserver's handle_error",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
