#!/usr/bin/env python3
"""Regression test for E124 (docs/audits/2026-11-09-codebase-audit.md).

BEFORE: server.py exec'd expanders_extra.py and every aux_*.py as a plain
string, so each code object's filename was "<string>": every aux traceback,
and the C28 `[route] ... at <file>:<line> in <func>` line for every aux route,
said `at <string>:293` and could not name the module.

AFTER: each file is compiled with its real path. Checked by running the REAL
loader (the `_AUX_FILES` assignment and the `for _auxf in _AUX_FILES` loop,
ast-extracted from server.py) over a scratch HERE, then a real aux route
through the REAL Handler on an ephemeral loopback port. Also pinned: the
order (expanders_extra.py first, then aux_*.py sorted), that the loop still
sits just before the HTTP section (CLAUDE.md's exec-include order rule), and
that a failing aux file still only logs and never takes the server down.
"""
import ast
import io
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def loader_nodes(tree):
    nodes, idx = [], None
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_AUX_FILES" for t in node.targets):
            nodes.append(node)
        elif (isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
              and node.iter.id == "_AUX_FILES"):
            nodes.append(node)
            idx = i
    return nodes, idx


def main():
    tree = ast.parse(H.SRC.read_text(), filename=str(H.SRC))
    nodes, idx = loader_nodes(tree)
    check("server.py has the _AUX_FILES assignment and its loop", len(nodes) == 2)
    # the loop stays right before the same-origin guard / Handler section
    after = [n for n in tree.body[idx + 1:]
             if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Assign))]
    first_names = []
    for n in after[:3]:
        first_names.append(getattr(n, "name", None) or
                           ",".join(t.id for t in n.targets if isinstance(t, ast.Name)))
    check("the loader still sits just before the HTTP section (order rule)",
          "_SAFE_METHODS" in first_names, first_names)

    with tempfile.TemporaryDirectory(prefix="hermes-e124-") as d:
        ns = H.http_namespace(d)
        here = ns["HERE"]
        order = []
        ns["_ORDER"] = order
        files = {
            "expanders_extra.py": "_ORDER.append('expanders_extra.py')\n",
            "aux_b.py": (
                "_ORDER.append('aux_b.py')\n"
                "def _e124_boom(ctx):\n"
                "    x = {}\n"
                "    return x['missing']\n"
                "register_get('/t/e124', _e124_boom)\n"),
            "aux_a.py": "_ORDER.append('aux_a.py')\n",
            "aux_c_broken.py": "_ORDER.append('aux_c_broken.py')\nraise RuntimeError('nope')\n",
            "aux_d.py": "_ORDER.append('aux_d.py')\nE124_FILE = __file__\n",
            "aux_e_syntax.py": "def (:\n",
            "not_aux.py": "_ORDER.append('not_aux.py')\n",
        }
        for name, src in files.items():
            with open(os.path.join(here, name), "w") as f:
                f.write(src)
        ns["__file__"] = "/srv/dashboard/server.py"
        err = io.StringIO()
        real_err, sys.stderr = sys.stderr, err
        try:
            exec(compile(ast.fix_missing_locations(
                ast.Module(body=nodes, type_ignores=[])), str(H.SRC), "exec"), ns)
        finally:
            sys.stderr = real_err
        load_log = err.getvalue()
        check("load order is unchanged: expanders_extra.py, then aux_*.py sorted",
              order == ["expanders_extra.py", "aux_a.py", "aux_b.py",
                        "aux_c_broken.py", "aux_d.py"], order)
        check("a raising aux file is logged and the loader carries on",
              "[aux_c_broken.py] failed to load: RuntimeError: nope" in load_log
              and "aux_d.py" in order, load_log)
        check("a syntax error in an aux file is logged, not raised",
              "[aux_e_syntax.py] failed to load: SyntaxError" in load_log, load_log)
        check("__file__ inside an aux module is still server.py's (aux_update, "
              "aux_graphify depend on it)", ns.get("E124_FILE") == "/srv/dashboard/server.py",
              ns.get("E124_FILE"))

        fn = ns["GET_ROUTES"].get("/t/e124")
        try:
            fn(None)
            tb = []
        except KeyError as e:
            tb = traceback.extract_tb(e.__traceback__)
        check("a traceback from an aux function names its real file (was <string>)",
              bool(tb) and tb[-1].filename == os.path.join(here, "aux_b.py"),
              [t.filename for t in tb])
        check("...and the right line", bool(tb) and tb[-1].lineno == 4,
              [t.lineno for t in tb])

        srv = H.Server(ns["Handler"])
        err = io.StringIO()
        sys.stderr = err
        try:
            st, _, body = H.req(srv.port, "GET", "/t/e124", None)
        finally:
            sys.stderr = real_err
            srv.close()
        log = err.getvalue()
        print(log, end="")
        check("the route still answers a 500 JSON", st == 500, (st, body[:120]))
        check("the C28 [route] line names the aux file and line",
              "at aux_b.py:4 in _e124_boom" in log, log)
        check("...not <string>", "<string>" not in log, log)

    H.finish()


if __name__ == "__main__":
    main()
