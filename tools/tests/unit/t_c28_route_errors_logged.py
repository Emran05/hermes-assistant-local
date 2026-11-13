#!/usr/bin/env python3
"""Regression test for C28 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: Handler._dispatch_aux() turned an aux route's exception into a 500
JSON body and printed nothing, so a route failing on every call never
reached dashboard.log — and doctor's "Recent errors" scan, which reads that
log, could not see it either.

AFTER: one stderr line per failure, `[route] <VERB> <path> failed: <Type>:
<message> at <file>:<line> in <function>` — single-line, message capped, and
matched by doctor's own error regex (asserted against the real _ERR_RE).
The 500 JSON answer is unchanged.

Runs the REAL Handler (ast-extracted) on an ephemeral loopback port.
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


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c28-") as d:
        ns = H.http_namespace(d)

        def boom_get(ctx):
            raise ValueError("missing\nkey")

        def boom_post(ctx):
            raise RuntimeError("kaboom " + "x" * 2000)

        def bad_header(ctx):
            r = ns["RawResponse"]("x")
            r.headers["X-Bad"] = "a\r\nInjected: 1"   # mutated after construction
            return r

        ns["register_get"]("/t/boom", boom_get)
        ns["register_post"]("/t/boom", boom_post)
        ns["register_get"]("/t/badheader", bad_header)
        srv = H.Server(ns["Handler"])
        err = io.StringIO()
        real_err = sys.stderr
        sys.stderr = err
        try:
            st1, _, b1 = H.req(srv.port, "GET", "/t/boom", None)
            st2, _, b2 = H.req(srv.port, "POST", "/t/boom", b"{}")
            st3, _, b3 = H.req(srv.port, "GET", "/t/badheader", None)
        finally:
            sys.stderr = real_err
            srv.close()
        log = err.getvalue()
        print(log, end="")
        lines = [ln for ln in log.splitlines() if ln.startswith("[route] ")]

        check("the 500 JSON answer is unchanged (GET)",
              st1 == 500 and (H.as_json(b1) or {}).get("ok") is False
              and "ValueError" in (H.as_json(b1) or {}).get("error", ""),
              (st1, b1[:120]))
        check("the 500 JSON answer is unchanged (POST)",
              st2 == 500 and "RuntimeError" in (H.as_json(b2) or {}).get("error", ""),
              (st2, b2[:120]))
        check("a bad RawResponse header is still a clean 500",
              st3 == 500, (st3, b3[:120]))
        check("each failure logs exactly one [route] line", len(lines) == 3, lines)
        get_ln = next((ln for ln in lines if "GET /t/boom " in ln), "")
        post_ln = next((ln for ln in lines if "POST /t/boom " in ln), "")
        check("the line names verb, path and exception type",
              "failed: ValueError" in get_ln and "failed: RuntimeError" in post_ln,
              lines)
        check("the line says where it was raised (function name)",
              "in boom_get" in get_ln and "in boom_post" in post_ln, lines)
        check("a multi-line message stays on one line",
              "\n" not in get_ln and "missing key" in get_ln, get_ln)
        check("a huge message is capped", 0 < len(post_ln) < 600, len(post_ln))
        if lines:
            err_re, skip_re = doctor_err_re()
            check("doctor's Recent-errors scan counts the line",
                  all(err_re.search(ln) and not skip_re.match(ln) for ln in lines),
                  lines)
        else:
            check("doctor's Recent-errors scan counts the line", False, "no lines")

    H.finish()


if __name__ == "__main__":
    main()
