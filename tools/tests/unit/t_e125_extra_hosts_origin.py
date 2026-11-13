#!/usr/bin/env python3
"""Regression test for E125 (docs/audits/2026-11-09-codebase-audit.md).

HERMES_DASH_ALLOWED_HOSTS names (a tunnel, a second bind) were handled by the
same-origin guard differently from the loopback names:
  (a) a tunnel serves https, so its pages send `Origin: https://<name>` and
      every POST was refused — the documented tunnel use could change nothing;
  (b) a name configured without a port is stored bare and with :DASH_PORT,
      and the bare form was accepted as an ORIGIN on any request — so
      `http://<name>` (port 80, a different server) could POST to the hub.
      R2-4 closed exactly that for 127.0.0.1/localhost/[::1] only.

AFTER: the R2-4 rule applies to every name — a port-less Origin counts only
when it is exactly this request's own Host — and `https://<configured name>`
is accepted for CONFIGURED names only, and only as the request's own Host;
never for a loopback name. Every row of t_c76's table still holds; these rows
are added on top. Drives the REAL _request_allowed/_build_allowed_hosts.
"""
import ast
import os
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


TREE = ast.parse(open(SERVER, encoding="utf-8").read(), filename=SERVER)


def top(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node
    raise AssertionError(name)


def load(env_hosts):
    saved = os.environ.get("HERMES_DASH_ALLOWED_HOSTS")
    if env_hosts is None:
        os.environ.pop("HERMES_DASH_ALLOWED_HOSTS", None)
    else:
        os.environ["HERMES_DASH_ALLOWED_HOSTS"] = env_hosts
    try:
        ns = {"os": os, "sys": sys, "urllib": urllib, "DASH_PORT": 7788}
        body = [top(n) for n in ("_SAFE_METHODS", "_build_allowed_hosts",
                                 "ALLOWED_HOSTS", "_hdr", "_request_allowed")]
        exec(compile(ast.Module(body=body, type_ignores=[]), SERVER, "exec"), ns)
    finally:
        if saved is None:
            os.environ.pop("HERMES_DASH_ALLOWED_HOSTS", None)
        else:
            os.environ["HERMES_DASH_ALLOWED_HOSTS"] = saved
    return ns["_request_allowed"]


A = load("hub.example.ts.net,mytunnel.example,lan.box:9000,localhost:9001")
A0 = load(None)


def ok(fn, m, host, origin=None, **extra):
    h = {"Host": host}
    if origin is not None:
        h["Origin"] = origin
    h.update(extra)
    return fn(m, h)[0]


print("\n1. (b) a port-less configured name's Origin is port 80 unless it IS the Host")
for m in ("GET", "POST"):
    check("%s Host mytunnel.example:7788, Origin http://mytunnel.example refused" % m,
          not ok(A, m, "mytunnel.example:7788", "http://mytunnel.example"))
    check("%s Host 127.0.0.1:7788, Origin http://mytunnel.example refused" % m,
          not ok(A, m, "127.0.0.1:7788", "http://mytunnel.example"))
    check("%s Host hub.example.ts.net:7788, Origin http://mytunnel.example refused" % m,
          not ok(A, m, "hub.example.ts.net:7788", "http://mytunnel.example"))
    check("%s Host hub.example.ts.net, Origin http://mytunnel.example refused" % m,
          not ok(A, m, "hub.example.ts.net", "http://mytunnel.example"))
check("the refusal reason is the cross-origin one",
      A("POST", {"Host": "mytunnel.example:7788", "Origin": "http://mytunnel.example"})[1]
      == "cross-origin request refused")
check("a port-less Origin that IS the request's own Host is same-origin",
      ok(A, "POST", "mytunnel.example", "http://mytunnel.example"))
check("...case-insensitively", ok(A, "POST", "MyTunnel.Example", "http://mytunnel.example"))
check("the with-port form still cross-matches (Host 127.0.0.1:7788)",
      ok(A, "POST", "127.0.0.1:7788", "http://mytunnel.example:7788"))
check("a configured name with its own port is accepted with that port",
      ok(A, "POST", "lan.box:9000", "http://lan.box:9000"))
check("...and never as a port-less Origin",
      not ok(A, "POST", "lan.box:9000", "http://lan.box"))

print("\n2. (a) https for configured names, as the request's own Host only")
for m in ("GET", "POST"):
    check("%s Host mytunnel.example, Origin https://mytunnel.example allowed" % m,
          ok(A, m, "mytunnel.example", "https://mytunnel.example"))
check("...with Sec-Fetch-Site: same-origin too",
      ok(A, "POST", "mytunnel.example", "https://mytunnel.example",
         **{"Sec-Fetch-Site": "same-origin"}))
check("https Origin of a configured name with an explicit own-Host port",
      ok(A, "POST", "lan.box:9000", "https://lan.box:9000"))
check("...or with :DASH_PORT when that is the request's own Host",
      ok(A, "POST", "mytunnel.example:7788", "https://mytunnel.example:7788"))
for host, origin in (
        ("127.0.0.1:7788", "https://mytunnel.example"),          # not its Host
        ("hub.example.ts.net", "https://mytunnel.example"),
        ("mytunnel.example:7788", "https://mytunnel.example"),
        ("mytunnel.example", "https://mytunnel.example:7788"),
        ("mytunnel.example", "https://evil.example"),
        ("mytunnel.example", "https://mytunnel.example.evil.com"),
        ("mytunnel.example", "https://user@mytunnel.example"),
        ("mytunnel.example", "wss://mytunnel.example"),
        ("mytunnel.example", "ftp://mytunnel.example")):
    check("POST Host %s, Origin %s refused" % (host, origin),
          not ok(A, "POST", host, origin))
check("cross-site Sec-Fetch-Site still refused on a POST from the tunnel name",
      not ok(A, "POST", "mytunnel.example", "https://mytunnel.example",
             **{"Sec-Fetch-Site": "cross-site"}))

print("\n3. https is NEVER accepted for a loopback name, configured or not")
for fn, tag in ((A, "with the env"), (A0, "without the env")):
    for host in ("127.0.0.1", "localhost", "[::1]", "127.0.0.1:7788",
                 "localhost:7788", "[::1]:7788"):
        check("%s: Host %s, Origin https://%s refused" % (tag, host, host),
              not ok(fn, "POST", host, "https://" + host))
check("a loopback name configured with its own port is not https either",
      not ok(A, "POST", "localhost:9001", "https://localhost:9001"))
check("...but is plain http with that port",
      ok(A, "POST", "localhost:9001", "http://localhost:9001"))

print("\n4. without the env var nothing changes for the tunnel names")
check("Host mytunnel.example is a forbidden host",
      A0("GET", {"Host": "mytunnel.example"}) == (False, "forbidden host"))
check("Origin https://mytunnel.example on the loopback Host is refused",
      not ok(A0, "POST", "127.0.0.1:7788", "https://mytunnel.example"))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
