#!/usr/bin/env python3
"""Regression test for B12 (docs/audits/2026-10-08-codebase-audit.md) and the
two /api/upload items in that audit's P3 "HTTP / persistence" prose.

BEFORE: Handler._body_json() did `int(Content-Length)` and `json.loads()` with
no validation, inside whichever route asked for it.
  * `Content-Length: abc` raised ValueError -> the connection was dropped with
    no response at all (curl: 000), same for /api/upload.
  * `[1]` (valid JSON, not an object) was handed to the route: `data.get()`
    raised and the connection was dropped; aux routes got a list in ctx.body.
  * a negative Content-Length made rfile.read(-n) wait for EOF, holding the
    thread for as long as the client kept its socket open.
  * nothing capped the size: an 8 MB+ declared body was read into memory.
  * a truncated /api/upload was kept on disk and answered {"ok": true}.

AFTER: do_POST's preamble (_load_body) reads and validates every JSON body
ONCE, before any route code: malformed / non-object -> 400, oversize -> 413,
both as JSON; route code keeps calling _body_json().get(), always a dict.

Runs the REAL Handler (ast-extracted from dashboard/server.py) on an
ephemeral loopback port. No dashboard, no model, no ~/.hermes.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-b12-") as d:
        calls = {"echo": [], "layout": 0}

        def get_layout():
            calls["layout"] += 1
            return {"order": []}

        ns = H.http_namespace(d, get_layout=get_layout,
                              save_layout=lambda lay: None, WIDGETS={})

        def echo(ctx):
            calls["echo"].append(ctx.body)
            return {"ok": True, "type": type(ctx.body).__name__,
                    "body": ctx.body}

        ns["register_post"]("/t/echo", echo)
        srv = H.Server(ns["Handler"])
        port = srv.port
        try:
            # -- the valid shapes still work ---------------------------------
            st, _, body = H.req(port, "POST", "/t/echo", b'{"a": 1}')
            j = H.as_json(body) or {}
            check("a JSON object reaches the route as a dict",
                  st == 200 and j.get("type") == "dict"
                  and j.get("body") == {"a": 1}, (st, body[:200]))
            st, _, body = H.req(port, "POST", "/t/echo", b"")
            check("an empty body is {} (unchanged)",
                  st == 200 and (H.as_json(body) or {}).get("body") == {},
                  (st, body[:200]))
            st, _, body = H.req(port, "POST", "/t/echo", b"  \n ")
            check("a whitespace-only body is {} (unchanged)",
                  st == 200 and (H.as_json(body) or {}).get("body") == {},
                  (st, body[:200]))

            # -- malformed / non-object: 400 JSON, route never runs ----------
            n_before = len(calls["echo"])
            for label, payload in (("a JSON array", b"[1]"),
                                   ("JSON null", b"null"),
                                   ("a JSON string", b'"x"'),
                                   ("malformed JSON", b"{bad"),
                                   ("invalid UTF-8", b'{"a": "\xff\xfe"}'),
                                   ("10k-deep nesting", b"[" * 10000)):
                st, _, body = H.req(port, "POST", "/t/echo", payload)
                j = H.as_json(body)
                check("%s -> 400 JSON" % label,
                      st == 400 and isinstance(j, dict) and j.get("ok") is False
                      and j.get("error"), (st, body[:200]))
            check("none of those reached the aux route",
                  len(calls["echo"]) == n_before, calls["echo"][n_before:])

            st, _, body = H.req(port, "POST", "/api/layout", b"[1]")
            check("[1] to the inline /api/layout -> 400 JSON (was: dropped)",
                  st == 400 and isinstance(H.as_json(body), dict),
                  (st, body[:200]))
            check("...and the layout route body never ran",
                  calls["layout"] == 0, calls)

            # -- Content-Length that is not a plain integer ------------------
            for label, cl in (("Content-Length: abc", "abc"),
                              ("Content-Length: 1e3", "1e3"),
                              ("Content-Length: +5", "+5")):
                st, _, body = H.req(port, "POST", "/t/echo", b"{}",
                                    headers={"Content-Length": cl})
                check("%s -> 400 JSON (was: dropped)" % label,
                      st == 400 and isinstance(H.as_json(body), dict),
                      (st, body[:200]))

            # negative: the old code waited for EOF -> keep the socket open
            st, _, body = H.req(port, "POST", "/t/echo", b"{}",
                                headers={"Content-Length": "-5"},
                                half_close=False, wait=3.0)
            check("Content-Length: -5 -> 400 at once (was: thread hung "
                  "until the client closed)", st == 400, (st, body[:200]))

            # conflicting duplicates (smuggling shape)
            data = (b"POST /t/echo HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n"
                    b"Content-Length: 2\r\nContent-Length: 7\r\n\r\n{}")
            st, _, body = H.raw(port, data)
            check("two different Content-Length headers -> 400",
                  st == 400, (st, body[:200]))

            # -- oversize: 413 without reading the body ----------------------
            cap = ns.get("MAX_JSON_BODY", 8 * 1024 * 1024)
            st, _, body = H.req(port, "POST", "/t/echo", b"{}",
                                headers={"Content-Length": str(cap + 1)},
                                half_close=False, wait=3.0)
            j = H.as_json(body)
            check("a body over MAX_JSON_BODY -> 413 JSON, answered before "
                  "reading it", st == 413 and isinstance(j, dict)
                  and j.get("ok") is False, (st, body[:200]))
            check("MAX_JSON_BODY leaves room for the largest real body "
                  "(512 KB ingest, 256 KB config import)",
                  cap >= 4 * 1024 * 1024, cap)

            # -- body shorter than declared ----------------------------------
            st, _, body = H.req(port, "POST", "/t/echo", b'{"a"',
                                headers={"Content-Length": "40"})
            check("a body shorter than its Content-Length -> 400",
                  st == 400, (st, body[:200]))

            # -- /api/upload --------------------------------------------------
            inbox = ns["INBOX"]
            st, _, body = H.req(port, "POST", "/api/upload", b"hello",
                                headers={"Content-Length": "abc",
                                         "X-Filename": "a.txt"})
            check("upload with Content-Length: abc -> 400 (was: dropped)",
                  st == 400, (st, body[:200]))
            st, _, body = H.req(port, "POST", "/api/upload", b"0123456789",
                                headers={"Content-Length": "100",
                                         "X-Filename": "short.txt"})
            check("a truncated upload is refused (was: kept, ok:true)",
                  st == 400 and (H.as_json(body) or {}).get("ok") is False,
                  (st, body[:200]))
            check("...and the partial file is not left in the inbox",
                  not any(f.startswith("short") for f in os.listdir(inbox)),
                  os.listdir(inbox))
            st, _, body = H.req(port, "POST", "/api/upload", b"0123456789",
                                headers={"X-Filename": "ok.txt"})
            j = H.as_json(body) or {}
            check("a complete upload still lands, byte for byte",
                  st == 200 and j.get("ok") is True
                  and open(j.get("path", "/nonexistent"), "rb").read()
                  == b"0123456789", (st, body[:200]))
            st2, _, body2 = H.req(port, "POST", "/api/upload", b"second",
                                  headers={"X-Filename": "ok.txt"})
            j2 = H.as_json(body2) or {}
            check("a second upload of the same name gets its own file",
                  st2 == 200 and j2.get("path") != j.get("path")
                  and open(j.get("path"), "rb").read() == b"0123456789",
                  (j, j2))
            check("uploaded files are owner-only (0600)",
                  (os.stat(j.get("path")).st_mode & 0o777) == 0o600,
                  oct(os.stat(j.get("path")).st_mode & 0o777))

            check("no request crashed the handler (no handle_error traceback)",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()

    # -- the pure helpers ----------------------------------------------------
    if "_content_length" in ns:
        cl = ns["_content_length"]
        check("_content_length: missing header is 0", cl({}) == (0, None))
        check("_content_length: '12' is 12", cl({"Content-Length": "12"}) == (12, None))
        check("_content_length: unicode digits refused",
              cl({"Content-Length": "١٢"})[1] is not None)
        pj = ns["_parse_json_body"]
        check("_parse_json_body: object passes through",
              pj(b'{"k": [1]}') == ({"k": [1]}, None))
        check("_parse_json_body: a list is refused", pj(b"[]")[0] is None)
    else:
        check("_content_length / _parse_json_body exist in server.py", False)

    H.finish()


if __name__ == "__main__":
    main()
