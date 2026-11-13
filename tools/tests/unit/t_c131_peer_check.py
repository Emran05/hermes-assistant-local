#!/usr/bin/env python3
"""Regression test for C131 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: the dashboard's only access control was header-based
(_request_allowed: Host / Origin / Sec-Fetch-Site), so any peer that could
reach the socket and wrote `Host: 127.0.0.1:7788` got the whole API.

AFTER: Handler._guard() first checks the TCP peer address (_peer_allowed):
loopback (127/8, ::1, ::ffff:127.x) or an explicit HERMES_DASH_ALLOWED_PEERS
entry, else 403 {"error": "forbidden peer"} — even with a forged Host.

What this cannot fix, and SECURITY.md now says so: on the default 127.0.0.1
bind every connection the kernel delivers was made on this Mac and arrives
from 127.0.0.1 — including OrbStack/Docker containers via
host.docker.internal (their host-side proxy connects to loopback) and other
macOS accounts. The suite asserts that the loopback case is still allowed,
because refusing it would lock the owner out, and that the documentation
states the limit. Closing it needs a secret (the B02 nonce).

The non-loopback peer is produced by a test server whose finish_request()
hands the REAL Handler a fake client_address, so the real _guard() decides.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c131-") as d:
        os.environ.pop("HERMES_DASH_ALLOWED_PEERS", None)
        ns = H.http_namespace(d)
        calls = []
        ns["register_get"]("/t/data", lambda ctx: calls.append(1) or {"ok": True})
        ns["register_post"]("/t/act", lambda ctx: calls.append(2) or {"ok": True})

        for peer in (("10.1.2.3", 4444), ("192.168.1.20", 5555),
                     ("172.17.0.2", 3333), ("fe80::1%en0", 1, 0, 4)):
            srv = H.Server(ns["Handler"], fake_peer=peer)
            try:
                st, _, body = H.req(srv.port, "GET", "/t/data", None)
                st2, _, body2 = H.req(srv.port, "POST", "/t/act", b"{}")
            finally:
                srv.close()
            check("peer %s with a forged loopback Host -> 403 forbidden peer"
                  % peer[0],
                  st == 403 and st2 == 403
                  and (H.as_json(body) or {}).get("error") == "forbidden peer",
                  (st, body[:100], st2))
        check("...and no route ran for any of them", calls == [], calls)

        for peer in (("127.0.0.1", 1), ("127.8.9.10", 2), ("::1", 3, 0, 0),
                     ("::ffff:127.0.0.1", 4, 0, 0)):
            srv = H.Server(ns["Handler"], fake_peer=peer)
            try:
                st, _, body = H.req(srv.port, "GET", "/t/data", None)
            finally:
                srv.close()
            check("loopback peer %s is still served" % peer[0], st == 200,
                  (st, body[:100]))

        srv = H.Server(ns["Handler"], fake_peer=("127.0.0.1", 9))
        try:
            st, _, body = H.req(srv.port, "GET", "/t/data", None,
                                headers={"Host": "evil.example"})
        finally:
            srv.close()
        check("a loopback peer still needs a loopback Host (guard unchanged)",
              st == 403 and (H.as_json(body) or {}).get("error") == "forbidden host",
              (st, body[:100]))

        # explicit opt-in for a deliberate non-loopback bind
        if "_build_allowed_peers" in ns:
            os.environ["HERMES_DASH_ALLOWED_PEERS"] = "10.0.0.0/8, bogus, 192.168.1.20"
            try:
                ns["ALLOWED_PEERS"] = ns["_build_allowed_peers"]()
            finally:
                os.environ.pop("HERMES_DASH_ALLOWED_PEERS", None)
            pa = ns["_peer_allowed"]
            check("HERMES_DASH_ALLOWED_PEERS admits a listed CIDR",
                  pa(("10.9.9.9", 1)) == (True, ""))
            check("...and a listed single address",
                  pa(("192.168.1.20", 1)) == (True, ""))
            check("...but nothing else", pa(("192.168.1.21", 1))[0] is False)
            check("a bad entry is ignored, not fatal", len(ns["ALLOWED_PEERS"]) == 2,
                  ns["ALLOWED_PEERS"])
            check("garbage peer addresses are refused",
                  pa(("not-an-ip", 1))[0] is False and pa(())[0] is False)
        else:
            check("_build_allowed_peers / _peer_allowed exist in server.py", False)

    sec = (H.REPO / "SECURITY.md").read_text()
    check("SECURITY.md no longer claims the boundary is the user account",
          "the trust boundary is your user account" not in sec)
    check("SECURITY.md names the container and Guest reach honestly",
          "host.docker.internal" in sec and "Guest" in sec)

    H.finish()


if __name__ == "__main__":
    main()
