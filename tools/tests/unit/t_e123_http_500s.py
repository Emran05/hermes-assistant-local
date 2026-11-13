#!/usr/bin/env python3
"""Regression test for E123 (docs/audits/2026-11-09-codebase-audit.md), the
part that lives in server.py's HTTP plumbing.

BEFORE:
  * `GET /api/sessions?limit=²` (a Unicode digit: str.isdigit() accepts it,
    int() then raises) or a limit of thousands of digits (int()'s 4300-digit
    limit) answered HTTP 500;
  * `GET /aux_x<NUL>.js` raised "embedded null byte" (a ValueError) past
    _static_js's `except OSError` and answered 500;
  * an aux route that caught its own failure and RETURNED a 5xx
    (`({"error": "internal: ..."}, 500)` — memory, clip, youmodel, google,
    claudebridge, messages) printed no `[route]` line, so C28's "route
    failures reach dashboard.log and doctor" did not hold for them.

AFTER: the limit is parsed as ASCII digits with a length cap (anything else
is ignored, as a non-number always was); an odd byte in a static path is a
404; and _dispatch_aux logs exactly one `[route]` line for EVERY 5xx an aux
route answers, raised or returned, matched by doctor's own error regex. A
4xx is a client error and is not logged. Real Handler, ephemeral port.
"""
import ast
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def doctor_err_re():
    tree = ast.parse((H.REPO / "dashboard" / "doctor.py").read_text())
    ns = {"re": re}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("_ERR_RE", "_ERR_SKIP_RE")
                for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         "doctor.py", "exec"), ns)
    return ns["_ERR_RE"], ns["_ERR_SKIP_RE"]


def capture(fn):
    err = io.StringIO()
    real, sys.stderr = sys.stderr, err
    try:
        out = fn()
    finally:
        sys.stderr = real
    return out, err.getvalue()


def route_lines(log):
    return [ln for ln in log.splitlines() if ln.startswith("[route] ")]


def main():
    rows = [{"id": "s%d" % i} for i in range(50)]
    with tempfile.TemporaryDirectory(prefix="hermes-e123-") as d:
        ns = H.http_namespace(d, list_sessions=lambda _arg: list(rows),
                              SESSIONS_LIST_CAP=30)
        with open(os.path.join(ns["HERE"], "aux_ok.js"), "w") as f:
            f.write("1;")
        R = ns["RawResponse"]
        ns["register_post"]("/t/internal", lambda ctx: (
            {"ok": False, "error": "internal: list indices must be\nintegers"}, 500))
        ns["register_get"]("/t/raw503", lambda ctx: R("down", status=503))
        ns["register_get"]("/t/tuple502", lambda ctx: ({"reason": "upstream"}, 502))
        ns["register_get"]("/t/noerr500", lambda ctx: ({"ok": False}, 500))
        ns["register_get"]("/t/huge500", lambda ctx: (
            {"error": "internal: " + "y" * 5000}, 500))
        ns["register_get"]("/t/ctl500", lambda ctx: (
            {"error": "internal: \x1b[2J\x1b]0;pwned\x07 x\x00y\u202ez\x85w"}, 500))

        def raises_ctl(ctx):
            raise ValueError("bad \x1b[31mred\x00")
        ns["register_get"]("/t/raisesctl", raises_ctl)
        ns["register_post"]("/t/bad400", lambda ctx: ({"ok": False, "error": "bad"}, 400))
        ns["register_get"]("/t/ok", lambda ctx: {"ok": True})

        def raises(ctx):
            raise ValueError("boom")
        ns["register_get"]("/t/raises", raises)
        srv = H.Server(ns["Handler"])
        try:
            # -- numeric query params ------------------------------------
            def sess(q):
                st, _, b = H.req(srv.port, "GET", "/api/sessions" + q, None)
                return st, H.as_json(b) or {}
            for q, want in (("?limit=%C2%B2", 30), ("?limit=%D9%A3", 30),
                            ("?limit=" + "9" * 5000, 30), ("?limit=1" + "0" * 4400, 30),
                            ("?limit=-3", 30), ("?limit=+5", 30), ("?limit=%205", 30),
                            ("?limit=0", 30), ("?limit=abc", 30), ("?limit=", 30),
                            ("?limit=5", 5), ("?limit=05", 5), ("?limit=40", 40),
                            ("?limit=999999", 50), ("?limit=all", 50), ("", 30)):
                (st, j), log = capture(lambda: sess(q))
                check("GET /api/sessions%s -> 200 with %d rows (not 500)"
                      % (q[:30], want),
                      st == 200 and len(j.get("sessions", [])) == want
                      and j.get("total") == 50, (st, j.get("error")))

            # -- odd bytes in a static path ------------------------------
            for raw_path, label in ((b"/aux_x\x00.js", "a raw NUL"),
                                    (b"/aux_x%00.js", "an encoded NUL"),
                                    (b"/aux_\xff\xfe.js", "latin-1 bytes"),
                                    (b"/aux_x\x01\x7f.js", "control bytes"),
                                    (b"/aux_.js", "an empty name")):
                data = (b"GET " + raw_path + b" HTTP/1.0\r\nHost: 127.0.0.1:7788\r\n\r\n")
                (res, log) = capture(lambda: H.raw(srv.port, data))
                st = res[0]
                check("GET with %s in an aux_*.js path -> 404/400, never 500"
                      % label, st in (400, 404), (st, res[2][:120]))
                check("...and no [route] failure is logged for it (%s)" % label,
                      not route_lines(log), log)
            st, _, b = H.req(srv.port, "GET", "/aux_ok.js", None)
            check("a real aux_*.js file is still served", st == 200 and b == b"1;",
                  (st, b[:40]))

            # -- every aux 5xx is logged, once ---------------------------
            def logged(method, path, body=b"{}"):
                (res, log) = capture(lambda: H.req(
                    srv.port, method, path, body if method == "POST" else None))
                return res, route_lines(log)

            (st, _, b), lines = logged("POST", "/t/internal")
            check("a self-caught internal 500 still answers its own body",
                  st == 500 and "internal:" in (H.as_json(b) or {}).get("error", ""),
                  (st, b[:120]))
            check("...and now logs exactly one [route] line", len(lines) == 1, lines)
            ln = lines[0] if lines else ""
            check("...naming verb, path and status",
                  ln.startswith("[route] POST /t/internal failed: HTTP 500"), ln)
            check("...with its error on one line",
                  "internal: list indices must be integers" in ln and "\n" not in ln, ln)
            (st, _, _), lines = logged("GET", "/t/raw503")
            check("a RawResponse 503 is logged", st == 503 and len(lines) == 1
                  and "GET /t/raw503 failed: HTTP 503" in lines[0], lines)
            (st, _, _), lines = logged("GET", "/t/tuple502")
            check("a (body, 502) is logged with its reason", st == 502 and len(lines) == 1
                  and "upstream" in lines[0], lines)
            (st, _, _), lines = logged("GET", "/t/noerr500")
            check("a 500 with no error text is still logged", st == 500
                  and len(lines) == 1, lines)
            (st, _, _), lines = logged("GET", "/t/huge500")
            check("a huge error text is capped", len(lines) == 1
                  and len(lines[0]) < 600, [len(x) for x in lines])
            (st, _, _), lines = logged("GET", "/t/raises")
            check("a raised exception is still exactly one line (no double log)",
                  st == 500 and len(lines) == 1 and "ValueError: boom" in lines[0], lines)
            # review R4: the error text can carry request-controlled bytes;
            # a terminal escape or NUL must not reach dashboard.log raw
            def clean(ln):
                return not any(ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f
                               or c in "\u202e\u2028\u2029" for c in ln)
            (st, _, _), lines = logged("GET", "/t/ctl500")
            check("a returned 5xx's control characters are escaped in the log",
                  st == 500 and len(lines) == 1 and clean(lines[0])
                  and "\\x1b" in lines[0], lines)
            (st, _, _), lines = logged("GET", "/t/raisesctl")
            check("...and a raised exception's too",
                  st == 500 and len(lines) == 1 and clean(lines[0])
                  and "\\x1b" in lines[0], lines)
            (st, _, _), lines = logged("POST", "/t/bad400")
            check("a 4xx answer is a client error and is NOT logged",
                  st == 400 and not lines, lines)
            (st, _, _), lines = logged("GET", "/t/ok")
            check("a 200 is not logged", st == 200 and not lines, lines)

            err_re, skip_re = doctor_err_re()
            (_, _, _), lines = logged("POST", "/t/internal")
            check("doctor's Recent-errors scan counts the new line",
                  bool(lines) and all(err_re.search(x) and not skip_re.match(x)
                                      for x in lines), lines)
        finally:
            srv.close()
        check("the server itself raised nothing", not srv.errors, srv.errors[:1])

    H.finish()


if __name__ == "__main__":
    main()
