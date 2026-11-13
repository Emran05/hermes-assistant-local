#!/usr/bin/env python3
"""C55 — hermes_mcp.py only ever talks to the dashboard on THIS Mac.

2026-10-21 audit C55: HERMES_MCP_DASHBOARD was taken verbatim, so a poisoned
MCP client config could point every search and write payload at another host.
Two neighbours of the same leak (2026-10-08 audit, P3 "MCP / harness"):
urllib honoured HTTP_PROXY for the loopback URL, and followed redirects.

Imports the REAL dashboard/hermes_mcp.py (and starts it once as a subprocess);
the only sockets are loopback listeners this test opens itself.  Offline.
"""
import io
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
SERVER = os.path.join(DASH, "hermes_mcp.py")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


class Listener(object):
    """A loopback TCP listener that records every request it is sent."""

    def __init__(self, reply=None):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(8)
        self.port = self.s.getsockname()[1]
        self.hits = []
        self.reply = reply
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            try:
                c.settimeout(2)
                self.hits.append(c.recv(4096))
                if self.reply:
                    c.sendall(self.reply)
            except OSError:
                pass
            finally:
                c.close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TMP = tempfile.mkdtemp(prefix="hermes-c55-")
PROXY = Listener()
# Set BEFORE the import, so whatever opener the module builds sees it.
for k in ("NO_PROXY", "no_proxy"):
    os.environ.pop(k, None)
for k in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ[k] = "http://127.0.0.1:%d" % PROXY.port
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "mcp-allow.json")
os.environ["HERMES_MCP_DASHBOARD"] = "http://collector.example:7788"

sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

print("--- the override is taken only when it is loopback http")
check("a non-loopback override is ignored at import",
      M.DASH_URL == "http://127.0.0.1:7788", M.DASH_URL)
base = getattr(M, "_dash_base", None)
check("there is a pure decision function", callable(base))
if callable(base):
    for raw, want_ok in (
            ("", True), ("http://127.0.0.1:7788", True), ("http://127.0.0.1:9", True),
            ("http://localhost:7799/", True), ("http://[::1]:7788", True),
            ("HTTP://LOCALHOST:7788", True),
            ("http://collector.example:7788", False), ("https://127.0.0.1:7788", False),
            ("http://127.0.0.1.collector.example", False),
            ("http://user:pw@127.0.0.1:7788", False), ("http://127.0.0.1@evil.example", False),
            ("http://127.0.0.1:99999", False), ("http://127.0.0.1:7788/api", False),
            ("http://127.0.0.1:7788?x=1", False), ("file:///etc/passwd", False),
            ("http://10.0.0.5:7788", False), ("http://0.0.0.0:7788", False),
            ("ftp://127.0.0.1", False), ("127.0.0.1:7788", False)):
        url, refusal = base(raw)
        if want_ok:
            check("accepted: %r" % raw, refusal == "" and
                  url == ((raw.rstrip("/") or "http://127.0.0.1:7788")), (url, refusal))
        else:
            check("refused, falls back to loopback: %r" % raw,
                  refusal and url == "http://127.0.0.1:7788", (url, refusal))

r = subprocess.run([sys.executable, SERVER], input=b"", capture_output=True, timeout=20,
                   env=dict(os.environ, HERMES_MCP_DASHBOARD="http://collector.example:7788"))
err = r.stderr.decode("utf-8", "replace")
check("the server logs that it ignored the override",
      "collector.example" in err and "not a loopback" in err, err[-300:])
check("the server's start line names the loopback dashboard",
      "dashboard=http://127.0.0.1:7788" in err and
      "dashboard=http://collector.example" not in err, err[-300:])

print("--- a proxy in the environment is never used for the loopback call")
M.DASH_URL = "http://127.0.0.1:%d" % closed_port()
try:
    M.dash_get("/api/search", {"q": "the lease"}, timeout=3)
    res = "answered"
except M.ToolError as e:
    res = str(e)
time.sleep(0.3)
check("the call fails as 'dashboard down', not through the proxy",
      "not answering" in res, res[:200])
check("the proxy saw nothing", PROXY.hits == [], PROXY.hits[:1])

print("--- a redirect is an answer, never a hop")
ELSEWHERE = Listener(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                     b"Content-Length: 11\r\n\r\n{\"ok\":true}")
REDIR = Listener(("HTTP/1.0 302 Found\r\nLocation: http://127.0.0.1:%d/collect\r\n"
                  "Content-Length: 0\r\n\r\n" % ELSEWHERE.port).encode())
M.DASH_URL = "http://127.0.0.1:%d" % REDIR.port
try:
    M.dash_get("/api/search", {"q": "the lease"}, timeout=3)
    res = "answered"
except M.ToolError as e:
    res = str(e)
time.sleep(0.3)
check("the redirecting answer is reported as HTTP 302", "302" in res, res[:200])
check("the Location target was never contacted", ELSEWHERE.hits == [], ELSEWHERE.hits[:1])
check("the proxy still saw nothing", PROXY.hits == [], PROXY.hits[:1])

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
