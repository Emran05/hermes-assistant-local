#!/usr/bin/env python3
"""Regression test for B17 (docs/audits/2026-10-08-codebase-audit.md).

BEFORE: no response carried X-Frame-Options or a CSP frame-ancestors, so any
web page could load the hub in an <iframe> and overlay its real Approve /
Send / model-switch buttons (clickjacking). The same-origin guard does not
help: a framed page is a top-level navigation's subresource, not a fetch.

AFTER: Handler.end_headers() adds `X-Frame-Options: DENY` and
`Content-Security-Policy: frame-ancestors 'none'` to EVERY response — the
app shell, static JS, JSON, aux RawResponse bodies, send_error() pages and
the guard's own 403. Nothing legitimate frames the hub: the Swift app loads
it top-level and the Quick Ask popover only loads /aux_quickask.js etc. as
scripts (app/main.swift QUICKASK_HTML), and no page in dashboard/ has an
<iframe>, <frame>, <object> or <embed> — both asserted below.

Runs the REAL Handler (ast-extracted) on an ephemeral loopback port.
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def framed_ok(hdrs):
    xfo = [v.upper() for v in hdrs.get("x-frame-options", [])]
    csp = " ".join(hdrs.get("content-security-policy", []))
    return xfo == ["DENY"] and "frame-ancestors 'none'" in csp


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-b17-") as d:
        ns = H.http_namespace(d)
        with open(os.path.join(ns["HERE"], "aux_t.js"), "w") as f:
            f.write("void 0;\n")
        ns["register_get"]("/t/json", lambda ctx: {"ok": True})
        ns["register_get"]("/t/raw", lambda ctx: ns["RawResponse"](
            "# md\n", "text/markdown; charset=utf-8",
            {"Content-Disposition": "attachment; filename=x.md"}))
        srv = H.Server(ns["Handler"])
        try:
            cases = [
                ("the app shell GET /", ("GET", "/", None, None), 200),
                ("a static aux_*.js", ("GET", "/aux_t.js", None, None), 200),
                ("an aux JSON route", ("GET", "/t/json", None, None), 200),
                ("an aux RawResponse download", ("GET", "/t/raw", None, None), 200),
                ("a send_error() 404", ("GET", "/nope", None, None), 404),
                ("the guard's own 403",
                 ("GET", "/", None, {"Host": "evil.example"}), 403),
                ("a POST 404", ("POST", "/nope", b"{}", None), 404),
            ]
            for label, (m, p, b, h), want in cases:
                st, hdrs, body = H.req(srv.port, m, p, b, headers=h)
                check("%s (%s) carries X-Frame-Options DENY + frame-ancestors "
                      "'none'" % (label, want),
                      st == want and framed_ok(hdrs), (st, hdrs))
            st, hdrs, _ = H.req(srv.port, "GET", "/t/json", None)
            check("no CORS header is ever added",
                  not any(k.startswith("access-control-") for k in hdrs), hdrs)
            check("the headers are sent once, not duplicated",
                  len(hdrs.get("x-frame-options", [])) == 1
                  and len(hdrs.get("content-security-policy", [])) == 1, hdrs)
        finally:
            srv.close()

    # Nothing in the repo frames the hub, so 'none' (not 'self') is safe.
    dash = H.REPO / "dashboard"
    framers = []
    for p in sorted(dash.iterdir()):
        if p.suffix in (".html", ".js"):
            if re.search(r"<\s*(iframe|frame|object|embed)\b",
                         p.read_text(errors="replace"), re.I):
                framers.append(p.name)
    check("no dashboard page embeds a same-origin frame", not framers, framers)
    swift = (H.REPO / "app" / "main.swift").read_text(errors="replace")
    m = re.search(r'let QUICKASK_HTML = """(.*?)"""', swift, re.S)
    check("the Quick Ask popover loads scripts, not a framed page",
          m is not None and "<iframe" not in m.group(1).lower()
          and "/aux_quickask.js" in m.group(1), m and m.group(1)[:200])
    check("the main window loads the hub top-level (webView.load(DASH_URL))",
          "webView.load(URLRequest(url: DASH_URL))" in swift)

    H.finish()


if __name__ == "__main__":
    main()
