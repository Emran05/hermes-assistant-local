#!/usr/bin/env python3
"""Regression test for C13 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: Handler had no `timeout`, so socketserver never set one on the
connection: a client that connected and sent nothing (or stopped halfway
through a body) held a thread and a file descriptor until it went away. The
audit exhausted the hub with ~245 idle connections.

AFTER: Handler.timeout bounds every socket read/write. An idle connection is
closed; a stalled body is answered 408 and closed; the thread is released.

The real class attribute is asserted (it must be set, and short enough to
matter); the behaviour is driven through the REAL Handler with that one
attribute lowered on a subclass so the suite takes a second, not 20.
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


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c13-") as d:
        ns = H.http_namespace(d)
        ns["register_post"]("/t/echo", lambda ctx: {"ok": True})
        real = ns["Handler"].timeout
        check("Handler.timeout is set (socketserver's default is none at all)",
              isinstance(real, (int, float)) and real > 0, real)
        check("...and short enough to reap idle sockets (<= 60 s)",
              isinstance(real, (int, float)) and 0 < real <= 60, real)

        class Quick(ns["Handler"]):
            pass

        # The timeout itself is the attribute under test above; here only
        # the wall clock is shortened. On the old code the attribute is None
        # and this subclass keeps it None, so the behaviour checks fail too.
        if isinstance(real, (int, float)) and real:
            Quick.timeout = 0.5

        before = threading.active_count()
        srv = H.Server(Quick)
        try:
            # 1. idle: connect, send nothing
            s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
            s.settimeout(3.0)
            t0 = time.time()
            try:
                closed = s.recv(1) == b""
            except socket.timeout:
                closed = False
            except ConnectionResetError:
                closed = True
            s.close()
            check("an idle connection is closed by the server",
                  closed, "still open after %.1fs" % (time.time() - t0))

            # 2. stalled body: headers promise 10 bytes, 2 arrive, then nothing
            st, _, body = H.req(srv.port, "POST", "/t/echo", b"{}",
                                headers={"Content-Length": "10"},
                                half_close=False, wait=3.0)
            check("a stalled request body is answered 408 and closed",
                  st == 408, (st, body[:200]))

            # 3. many idle sockets do not pin threads forever
            socks = [socket.create_connection(("127.0.0.1", srv.port))
                     for _ in range(20)]
            time.sleep(0.3)
            peak = threading.active_count()
            deadline = time.time() + 5
            while time.time() < deadline and threading.active_count() > before + 2:
                time.sleep(0.1)
            settled = threading.active_count()
            for x in socks:
                x.close()
            check("20 idle connections release their threads",
                  peak >= before + 10 and settled <= before + 2,
                  (before, peak, settled))

            # 4. a normal request is unaffected
            st, _, body = H.req(srv.port, "POST", "/t/echo", b"{}")
            check("a normal request still answers", st == 200, (st, body[:100]))
        finally:
            srv.close()

    H.finish()


if __name__ == "__main__":
    main()
