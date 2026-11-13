"""Shared harness for the HTTP-core / JSON-store regression suites
(t_b12_*, t_b17_*, t_c12_*, t_c13_*, t_c27_*, t_c28_*, t_c107_*, t_c110_*,
t_c131_*). The leading underscore keeps run.sh from treating it as a suite.

Everything under test is the REAL code: the named functions, classes and
top-level assignments are ast-extracted from dashboard/server.py and exec'd
into a throwaway namespace, the way t_a01/t_a03 do it. Nothing touches the
live ~/.hermes, the dashboard port, a model or the network; the one server
these suites start is a ThreadingHTTPServer on 127.0.0.1:<ephemeral>.

HERMES_T_SERVER_SRC=<path> points the extraction at another copy of
server.py (e.g. `git show ade42af:dashboard/server.py > old.py`), which is how
each suite was shown to FAIL on the code before the fix: names that copy does
not have are skipped rather than asserted, so the old Handler really runs.
"""
import ast
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
SRC = Path(os.environ.get("HERMES_T_SERVER_SRC")
           or (REPO / "dashboard" / "server.py"))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def finish():
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


def extract(names, ns, optional=(), src=None):
    """exec the named top-level defs/classes/assignments of server.py into ns.
    Every name in `names` must exist; names in `optional` are skipped when the
    copy under test does not have them (the pre-fix code)."""
    src = Path(src or SRC)
    tree = ast.parse(src.read_text(), filename=str(src))
    want = set(names) | set(optional)
    found, got = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in want:
            found.append(node)
            got.add(node.name)
        elif isinstance(node, ast.Assign):
            tg = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if tg & want:
                found.append(node)
                got |= tg & want
    missing = set(names) - got
    assert not missing, "missing %s in %s" % (sorted(missing), src)
    mod = ast.fix_missing_locations(ast.Module(body=found, type_ignores=[]))
    exec(compile(mod, str(src), "exec"), ns)
    return got


HTTP_NAMES = ["_SAFE_METHODS", "_build_allowed_hosts", "ALLOWED_HOSTS", "_hdr",
              "_request_allowed", "GET_ROUTES", "POST_ROUTES", "register_get",
              "register_post", "RouteCtx", "_check_header", "RawResponse",
              "Handler"]
HTTP_NEW = ["_build_allowed_peers", "ALLOWED_PEERS", "_peer_allowed",
            "MAX_JSON_BODY", "_content_length", "_parse_json_body"]


def http_namespace(scratch, **extra):
    """The real request plumbing with nothing else: no aux modules, no
    routes but what a suite registers, HERE/INBOX in a temp dir."""
    import re
    here = os.path.join(scratch, "here")
    inbox = os.path.join(scratch, "inbox")
    os.makedirs(here, exist_ok=True)
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(here, "index.html"), "w") as f:
        f.write("<!doctype html><title>t</title>")
    ns = dict(os=os, sys=sys, json=json, re=re, time=time, threading=threading,
              traceback=traceback, urllib=urllib,
              BaseHTTPRequestHandler=BaseHTTPRequestHandler,
              DASH_PORT=7788, HERE=here, INBOX=inbox)
    ns.update(extra)
    extract(HTTP_NAMES, ns, optional=HTTP_NEW)
    return ns


class Server:
    """ThreadingHTTPServer on an ephemeral loopback port around ns['Handler']
    (or a subclass). `fake_peer` makes every connection look like it came
    from that address, to drive the real _guard() with a non-loopback peer."""

    def __init__(self, handler, fake_peer=None):
        outer = self

        class _S(ThreadingHTTPServer):
            daemon_threads = True

            def finish_request(self, request, client_address):
                self.RequestHandlerClass(
                    request, outer.fake_peer or client_address, self)

            def handle_error(self, request, client_address):
                outer.errors.append(traceback.format_exc())

        self.fake_peer = fake_peer
        self.errors = []
        self.httpd = _S(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def raw(port, data, wait=3.0, half_close=True, linger=None):
    """Send raw bytes, return (status|None, headers dict, body bytes).
    status None = the server answered nothing (dropped / still waiting)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    try:
        s.sendall(data)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        if linger:
            time.sleep(linger)
        buf = b""
        s.settimeout(wait)
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, ConnectionResetError):
            pass
    finally:
        s.close()
    if not buf.startswith(b"HTTP/"):
        return None, {}, buf
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdrs.setdefault(k.strip().lower(), []).append(v.strip())
    return status, hdrs, body


def req(port, method, path, body=b"", headers=None, **kw):
    h = {"Host": "127.0.0.1:7788"}
    if body is not None and "Content-Length" not in (headers or {}):
        h["Content-Length"] = str(len(body))
    h.update(headers or {})
    lines = ["%s %s HTTP/1.0" % (method, path)]
    lines += ["%s: %s" % (k, v) for k, v in h.items()]
    data = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (body or b"")
    return raw(port, data, **kw)


def as_json(body):
    try:
        return json.loads(body)
    except Exception:
        return None
