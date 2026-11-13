#!/usr/bin/env python3
"""Regression test for E122 (docs/audits/2026-11-09-codebase-audit.md).

BEFORE: Handler.timeout (C13) bounds each socket call, and R2-5 put a wall
clock on BODIES only. The request line and headers are read by the stdlib
one readline at a time, so a client trickling one header byte every few
seconds reset the 20 s timer on every byte and held a thread and an fd for
as long as it liked (the audit held 150 of them past 40 s; ~245 exhaust the
hub, C13).

AFTER: Handler.header_deadline is a wall-clock budget for the request line
plus every header; past it the connection is closed. A request whose headers
arrive slowly but within the budget is still served, the socket timeout is
restored for the body (so the B12/R2-5 body rules and their 408 are
unchanged), and the trickling threads are released.

Driven through the REAL Handler with real sockets on an ephemeral loopback
port; only the wall-clock attributes are lowered on a subclass so the suite
takes seconds, and the real values are asserted separately.
"""
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check

DEADLINE = 1.5


def trickle(port, head, byte_every, give_up):
    """Send `head` at once, then one byte of filler every `byte_every` s.
    Returns (seconds until the server closed, or None if still open at
    `give_up`, whatever the server answered)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    t0 = time.monotonic()
    got = b""
    try:
        s.sendall(head)
        s.settimeout(byte_every)
        while time.monotonic() - t0 < give_up:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    return time.monotonic() - t0, got
                got += chunk
            except socket.timeout:
                pass
            except (ConnectionResetError, BrokenPipeError):
                return time.monotonic() - t0, got
            try:
                s.sendall(b"a")
            except (ConnectionResetError, BrokenPipeError, OSError):
                return time.monotonic() - t0, got
        return None, got
    finally:
        s.close()


def chunked_send(port, parts, gap):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        for i, p in enumerate(parts):
            if i:
                time.sleep(gap)
            s.sendall(p)
        s.shutdown(socket.SHUT_WR)
        buf = b""
        s.settimeout(10)
        while True:
            c = s.recv(65536)
            if not c:
                break
            buf += c
    finally:
        s.close()
    if not buf.startswith(b"HTTP/"):
        return None, buf
    return int(buf.split(b" ", 2)[1]), buf.partition(b"\r\n\r\n")[2]


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-e122-") as d:
        ns = H.http_namespace(d)
        ns["register_post"]("/t/echo", lambda ctx: {"ok": True, "got": ctx.body})
        ns["register_get"]("/t/ok", lambda ctx: {"ok": True})
        Handler = ns["Handler"]
        real = getattr(Handler, "header_deadline", None)
        check("Handler.header_deadline exists (a wall clock for the headers)",
              isinstance(real, (int, float)) and real > 0, real)
        check("...about 20 s: no longer than the body budget, no shorter than "
              "one socket timeout", isinstance(real, (int, float))
              and Handler.timeout <= real <= Handler.body_deadline, real)

        class Quick(Handler):
            header_deadline = DEADLINE
            timeout = 4.0              # > DEADLINE: only the new budget can fire

        before = threading.active_count()
        srv = H.Server(Quick)
        try:
            # 1. one header byte every 0.4 s: under the per-call timeout forever
            took, got = trickle(srv.port, b"GET /t/ok HTTP/1.1\r\nHost: 127.0.0.1:7788\r\nX: ",
                                0.4, DEADLINE + 4)
            check("a trickled header is dropped at the deadline (was: held forever)",
                  took is not None and took < DEADLINE + 1.5, took)
            check("...without being served", b"200" not in got[:20], got[:60])
            # 2. a trickled REQUEST LINE too
            took, got = trickle(srv.port, b"GET /t/ok", 0.4, DEADLINE + 4)
            check("a trickled request line is dropped at the deadline",
                  took is not None and took < DEADLINE + 1.5, took)
            # 3. idle connection: still closed by the timeout / deadline
            s = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
            t0 = time.monotonic()
            s.settimeout(DEADLINE + 4)
            try:
                closed = s.recv(1) == b""
            except socket.timeout:
                closed = False
            except ConnectionResetError:
                closed = True
            s.close()
            check("an idle connection is still closed", closed
                  and time.monotonic() - t0 < DEADLINE + 1.5, time.monotonic() - t0)

            # 4. slow but legitimate: headers in pieces, inside the budget
            st, body = chunked_send(srv.port, [
                b"GET /t/ok HTTP/1.0\r\n", b"Host: 127.0.0.1:7788\r\n",
                b"Accept: */*\r\n", b"\r\n"], 0.3)
            check("a slow-but-legit GET (0.9 s of headers) is served",
                  st == 200 and b'"ok": true' in body, (st, body[:80]))
            # 5. headers near the deadline, then a body after a pause LONGER
            #    than what was left of the header budget: the body is governed
            #    by the socket timeout + body_deadline again, not the leftover
            st, body = chunked_send(srv.port, [
                b"POST /t/echo HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n",
                b"Content-Length: 9\r\n\r\n", b'{"a": 1}\n'], 1.2)
            check("a body after slow headers is still read (socket timeout "
                  "restored after the headers)", st == 200
                  and b'"got": {"a": 1}' in body, (st, body[:120]))
            # 6. B12 body rules unchanged
            st, _, body = H.req(srv.port, "POST", "/t/echo", b"{bad")
            check("a bad JSON body is still a 400", st == 400, (st, body[:80]))
            st, _, body = H.req(srv.port, "POST", "/t/echo", b"{}",
                                headers={"Content-Length": "999999999"})
            check("an oversize body is still a 413", st == 413, (st, body[:80]))
            st, _, body = H.req(srv.port, "POST", "/t/echo", b"{}",
                                headers={"Content-Length": "10"},
                                half_close=False, wait=10)
            check("a stalled body is still a 408", st == 408, (st, body[:80]))
            # 7. long headers are still 431, a long line still 414
            st, _, _ = H.raw(srv.port, b"GET /" + b"a" * 70000 + b" HTTP/1.0\r\n\r\n")
            check("a request line over 64 KB is still 414", st == 414, st)
            st, _, _ = H.raw(srv.port, b"GET /t/ok HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n"
                             + b"X: " + b"b" * 70000 + b"\r\n\r\n")
            check("a header line over 64 KB is still refused (431)", st == 431, st)

            # 8. many tricklers are all released
            socks = []
            for _ in range(15):
                c = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
                c.sendall(b"GET / HTTP/1.0\r\nX: ")
                socks.append(c)
            stop = time.monotonic() + DEADLINE + 2.5
            while time.monotonic() < stop:
                for c in socks:
                    try:
                        c.sendall(b"z")
                    except OSError:
                        pass
                time.sleep(0.3)
            settle = time.monotonic() + 3
            while time.monotonic() < settle and threading.active_count() > before + 2:
                time.sleep(0.1)
            check("15 trickling connections do not pin 15 threads past the deadline",
                  threading.active_count() <= before + 2,
                  (before, threading.active_count()))
            for c in socks:
                c.close()
        finally:
            srv.close()
        check("the server raised nothing", not srv.errors, srv.errors[:1])

    H.finish()


if __name__ == "__main__":
    main()
