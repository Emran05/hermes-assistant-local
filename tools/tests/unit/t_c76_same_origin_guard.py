#!/usr/bin/env python3
"""Regression test for C76 (docs/audits/2026-10-21-codebase-audit.md).

The API has no token, so the same-origin guard IS its CSRF / DNS-rebinding
protection (CLAUDE.md "API same-origin guard"). The audit deleted documented
rules one at a time — the https-Origin refusal, the `Origin: null` refusal,
the GET exemption for cross-site navigation, the port-less Host forms — and
every test stayed green, because the only guard checks were a handful of
end-to-end requests in convos_harness.py and the live verify_round2.sh.

This suite drives the REAL decision function `_request_allowed` (and
`_build_allowed_hosts`, `_hdr`, `Handler._guard`) extracted from
dashboard/server.py by ast, one assertion per documented rule, each written
so that deleting or loosening that rule fails at least one check:
  * Host must be one of {127.0.0.1, localhost, [::1]} with or without
    :DASH_PORT, or listed in HERMES_DASH_ALLOWED_HOSTS — else "forbidden host";
  * an Origin, when present, must be exactly http://<allowed host> — on GET as
    well as POST; https, null, file:, userinfo and look-alike hosts fail; a
    port-less loopback Origin (http://localhost = port 80, another local web
    server) passes only when it is the request's own Host (round-2 R2-4);
  * Sec-Fetch-Site: cross-site is refused on state-changing verbs only (a
    cross-site NAVIGATION to the hub is a GET with no Origin and must work);
  * a request with no Origin (curl, launchd, the app's MessagesSync) passes;
  * every do_* verb on Handler runs the guard before anything else, and a
    refusal is a 403 carrying the reason.
"""
import ast
import os
import re
import sys
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


with open(SERVER, encoding="utf-8") as f:
    SRC = f.read()
TREE = ast.parse(SRC, filename=SERVER)


def top(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node
    raise AssertionError("%s not found in server.py" % name)


def load(env_hosts=None):
    """A namespace with the real guard functions; ALLOWED_HOSTS is rebuilt by
    the real _build_allowed_hosts under the given HERMES_DASH_ALLOWED_HOSTS."""
    saved = os.environ.get("HERMES_DASH_ALLOWED_HOSTS")
    if env_hosts is None:
        os.environ.pop("HERMES_DASH_ALLOWED_HOSTS", None)
    else:
        os.environ["HERMES_DASH_ALLOWED_HOSTS"] = env_hosts
    try:
        ns = {"os": os, "sys": sys, "urllib": urllib, "DASH_PORT": 7788}
        body = [top(n) for n in ("_SAFE_METHODS", "_build_allowed_hosts", "ALLOWED_HOSTS",
                                 "_hdr", "_request_allowed")]
        exec(compile(ast.Module(body=body, type_ignores=[]), SERVER, "exec"), ns)
    finally:
        if saved is None:
            os.environ.pop("HERMES_DASH_ALLOWED_HOSTS", None)
        else:
            os.environ["HERMES_DASH_ALLOWED_HOSTS"] = saved
    return ns


NS = load()
allowed = NS["_request_allowed"]


def ok(method, headers):
    return allowed(method, headers)[0]


def why(method, headers):
    return allowed(method, headers)[1]


H = "127.0.0.1:7788"

print("\n1. Host: the loopback names, with and without the port")
for host in ("127.0.0.1:7788", "localhost:7788", "[::1]:7788",
             "127.0.0.1", "localhost", "[::1]"):
    for m in ("GET", "POST"):
        check("%s with Host %s is allowed" % (m, host), ok(m, {"Host": host}))
check("Host is compared case-insensitively (LOCALHOST:7788)", ok("GET", {"Host": "LOCALHOST:7788"}))
check("the header NAME is case-insensitive too (a plain dict with 'host')",
      ok("GET", {"host": H}))
for host in ("evil.example", "evil.example:7788", "127.0.0.1:7789", "localhost:8080",
             "7788.evil.com", "127.0.0.1.evil.com:7788", "localhost.evil:7788",
             "0.0.0.0:7788", "[::ffff:127.0.0.1]:7788", "127.1:7788", "localhost.:7788"):
    check("Host %s is refused (rebinding)" % host, not ok("GET", {"Host": host}))
check("a missing Host is refused", not ok("GET", {}))
check("an empty Host is refused", not ok("GET", {"Host": "  "}))
check("headers=None is refused, not a crash", not ok("GET", None))
check("the reason is 'forbidden host'", why("GET", {"Host": "evil.example"}) == "forbidden host",
      why("GET", {"Host": "evil.example"}))
check("a bad Host is refused even with a matching Origin",
      not ok("GET", {"Host": "evil.example", "Origin": "http://evil.example"}))

print("\n2. HERMES_DASH_ALLOWED_HOSTS adds hosts, with the port rule applied")
NS2 = load(" mybox.tailnet.ts.net , other.lan:9000 ,")
a2 = NS2["_request_allowed"]
check("an extra name is allowed bare", a2("GET", {"Host": "mybox.tailnet.ts.net"})[0])
check("an extra name is allowed with :DASH_PORT", a2("GET", {"Host": "mybox.tailnet.ts.net:7788"})[0])
check("an extra name with its own port keeps exactly that port",
      a2("GET", {"Host": "other.lan:9000"})[0] and not a2("GET", {"Host": "other.lan:7788"})[0])
check("it is an Origin host too",
      a2("POST", {"Host": "mybox.tailnet.ts.net:7788", "Origin": "http://mybox.tailnet.ts.net:7788"})[0])
check("the loopback names are still there", a2("GET", {"Host": H})[0])
check("without the env var the extra name is refused", not ok("GET", {"Host": "mybox.tailnet.ts.net"}))
check("ALLOWED_HOSTS is exactly the documented six with no env",
      NS["ALLOWED_HOSTS"] == {"127.0.0.1", "127.0.0.1:7788", "localhost", "localhost:7788",
                              "[::1]", "[::1]:7788"}, sorted(NS["ALLOWED_HOSTS"]))

print("\n3. Origin, when present, must be http://<allowed host> — on every verb")
for origin in ("http://127.0.0.1:7788", "http://localhost:7788", "http://[::1]:7788"):
    for m in ("GET", "POST"):
        check("%s with Origin %s is allowed" % (m, origin), ok(m, {"Host": H, "Origin": origin}))
bad_origins = (
    "https://127.0.0.1:7788",          # scheme: https is a different origin
    "https://localhost:7788",
    "null",                            # sandboxed iframe, file://, data:
    "http://evil.example",
    "http://evil.example:7788",
    "http://localhost:7788.evil.com",
    "http://user@localhost:7788",      # userinfo
    "http://127.0.0.1:7789",
    "file://",
    "chrome-extension://abcdef",
    "ws://127.0.0.1:7788",
    "http://[::1",                      # unparseable
)
for origin in bad_origins:
    for m in ("GET", "POST", "HEAD"):
        check("%s with Origin %r is refused" % (m, origin), not ok(m, {"Host": H, "Origin": origin}))
check("the reason is 'cross-origin request refused'",
      why("POST", {"Host": H, "Origin": "https://127.0.0.1:7788"}) == "cross-origin request refused")
check("Origin is compared case-insensitively on the host",
      ok("POST", {"Host": H, "Origin": "http://LOCALHOST:7788"}))

print("\n3b. a port-less loopback Origin means port 80 — any other local web server (R2-4)")
# ALLOWED_HOSTS carries the port-less names so a Host header may omit the
# port, but `Origin: http://localhost` is the page at http://localhost:80, not
# the hub. It used to pass, so any local dev server could POST to every route.
for origin in ("http://localhost", "http://127.0.0.1", "http://[::1]",
               "http://LOCALHOST"):
    for m in ("GET", "POST"):
        check("%s with Origin %s and Host %s is refused" % (m, origin, H),
              not ok(m, {"Host": H, "Origin": origin}))
check("...with the cross-origin reason",
      why("POST", {"Host": H, "Origin": "http://localhost"}) == "cross-origin request refused",
      why("POST", {"Host": H, "Origin": "http://localhost"}))
check("Origin http://localhost refused against Host localhost:7788 too",
      not ok("POST", {"Host": "localhost:7788", "Origin": "http://localhost"}))
check("Origin http://127.0.0.1 refused against Host localhost (another loopback name)",
      not ok("POST", {"Host": "localhost", "Origin": "http://127.0.0.1"}))
check("a port-less Origin that IS the request's own Host is same-origin (hub on :80)",
      ok("POST", {"Host": "localhost", "Origin": "http://localhost"}))
check("...case-insensitively",
      ok("POST", {"Host": "LocalHost", "Origin": "http://localhost"}))
check("the with-port forms still cross-match (Host 127.0.0.1:7788, Origin localhost:7788)",
      ok("POST", {"Host": H, "Origin": "http://localhost:7788"}))
# E125 (2026-11-09): this row used to assert the bug — a port-less extra
# name's Origin is its port-80 server, so it counts only as the request's
# own Host, exactly like the loopback names (R2-4).
check("an HERMES_DASH_ALLOWED_HOSTS entry is NOT an Origin without its port "
      "on a request to its :DASH_PORT Host (E125)",
      not a2("POST", {"Host": "mybox.tailnet.ts.net:7788",
                      "Origin": "http://mybox.tailnet.ts.net"})[0])
check("...it is when it is the request's own port-less Host",
      a2("POST", {"Host": "mybox.tailnet.ts.net",
                  "Origin": "http://mybox.tailnet.ts.net"})[0])
check("...and with an explicit port of its own",
      a2("POST", {"Host": "other.lan:9000", "Origin": "http://other.lan:9000"})[0])
check("the env var does not re-admit a port-less loopback Origin",
      not a2("POST", {"Host": H, "Origin": "http://localhost"})[0])

print("\n4. Sec-Fetch-Site: cross-site is refused on state-changing verbs only")
for m in ("POST", "PUT", "DELETE", "PATCH", "post"):
    check("%s with Sec-Fetch-Site: cross-site and no Origin is refused" % m,
          not ok(m, {"Host": H, "Sec-Fetch-Site": "cross-site"}))
check("the value is case-insensitive (Cross-Site)",
      not ok("POST", {"Host": H, "Sec-Fetch-Site": " Cross-Site "}))
for m in ("GET", "HEAD", "OPTIONS", "get"):
    check("%s with Sec-Fetch-Site: cross-site and no Origin is ALLOWED (a navigation)" % m,
          ok(m, {"Host": H, "Sec-Fetch-Site": "cross-site"}))
check("POST with Sec-Fetch-Site: same-origin is allowed",
      ok("POST", {"Host": H, "Origin": "http://127.0.0.1:7788", "Sec-Fetch-Site": "same-origin"}))
check("a cross-site POST is refused even with an allowed-looking Origin",
      not ok("POST", {"Host": H, "Origin": "http://127.0.0.1:7788", "Sec-Fetch-Site": "cross-site"}))

print("\n5. no Origin = not a browser: curl, launchd and MessagesSync keep working")
for m in ("GET", "POST"):
    check("%s with only a Host header is allowed" % m, ok(m, {"Host": H}))
check("the app's MessagesSync ingest shape is allowed",
      ok("POST", {"Host": "127.0.0.1:7788", "Content-Type": "application/json",
                  "X-Hermes-Token": "t"}))
check("an allowed request carries an empty reason", allowed("POST", {"Host": H}) == (True, ""))

print("\n6. every verb runs the guard first, and a refusal is a 403 with the reason")
handler = top("Handler")
verbs = [n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name.startswith("do_")]
check("Handler defines do_GET and do_POST", {"do_GET", "do_POST"} <= {v.name for v in verbs},
      [v.name for v in verbs])


def guarded_first(fn):
    """True when the first statement is `if not self._guard(): return`."""
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(
        getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str))]
    if not body or not isinstance(body[0], ast.If):
        return False
    t = body[0].test
    return (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
            and isinstance(t.operand, ast.Call)
            and isinstance(t.operand.func, ast.Attribute)
            and t.operand.func.attr == "_guard"
            and isinstance(body[0].body[0], ast.Return))


for v in verbs:
    check("%s starts with `if not self._guard(): return`" % v.name, guarded_first(v))
for node in TREE.body:   # aux modules exec into these globals; none may add an unguarded verb
    if isinstance(node, ast.ClassDef) and node.name != "Handler":
        for n in node.body:
            if isinstance(n, ast.FunctionDef) and n.name.startswith("do_"):
                check("%s.%s is guarded too" % (node.name, n.name), guarded_first(n))
# the files server.py exec()s into its own globals: an HTTP verb (do_GET,
# do_PUT ...) defined there would be an unguarded door into the same server
for aux in sorted(os.listdir(os.path.join(REPO, "dashboard"))):
    if aux.endswith(".py") and (aux.startswith("aux_") or aux == "expanders_extra.py"):
        with open(os.path.join(REPO, "dashboard", aux), encoding="utf-8") as f:
            t = ast.parse(f.read())
        for n in ast.walk(t):
            if isinstance(n, ast.FunctionDef) and re.match(r"do_[A-Z]+$", n.name):
                check("%s %s is guarded" % (aux, n.name), guarded_first(n))

guard_fn = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == "_guard")
gns = {"os": os, "sys": sys, "_request_allowed": allowed}
# _guard checks the connection's peer address first (C131): the real check
exec(compile(ast.Module(body=[top(n) for n in ("_build_allowed_peers", "ALLOWED_PEERS",
                                              "_peer_allowed")] + [guard_fn],
                        type_ignores=[]), SERVER, "exec"), gns)


class FakeHeaders(dict):
    def get(self, k, d=None):
        for kk, v in self.items():
            if kk.lower() == k.lower():
                return v
        return d


class Fake:
    def __init__(self, command, headers):
        self.command, self.path, self.headers, self.sent = command, "/api/x", FakeHeaders(headers), []
        self.client_address = ("127.0.0.1", 50123)     # a loopback peer, as in real use

    def _json(self, obj, status=200):
        self.sent.append((status, obj))


import io, contextlib  # noqa: E402
err = io.StringIO()
with contextlib.redirect_stderr(err):
    f1 = Fake("POST", {"Host": H, "Origin": "null"})
    r1 = gns["_guard"](f1)
    f2 = Fake("POST", {"Host": H})
    r2 = gns["_guard"](f2)
check("_guard refuses with False", r1 is False)
check("...and answers 403 with the reason",
      f1.sent == [(403, {"error": "cross-origin request refused"})], f1.sent)
check("...and logs one stderr line", err.getvalue().count("[guard] refused") == 1, err.getvalue())
check("_guard passes an allowed request through untouched", r2 is True and f2.sent == [], f2.sent)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
