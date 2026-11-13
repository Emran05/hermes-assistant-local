#!/usr/bin/env python3
"""Regression test for E20 (doctor half) — docs/audits/2026-11-09-codebase-audit.md.

doctor.py probed the dashboard and the model lanes with urllib's default
opener, which honours a configured web proxy (the macOS system one has no
loopback exemption), so those loopback probes were sent to the proxy. Now
every doctor probe goes through an opener built with an empty ProxyHandler.

The proxy is simulated through the environment (http_proxy, no no_proxy),
which urllib treats exactly like the system setting: getproxies() returns it
and proxy_bypass() does not exempt 127.0.0.1. Two loopback servers on port 0:
a "proxy" that records whatever reaches it and the real target. The REAL
doctor.py is loaded by path with HOME pointed at a scratch folder.
"""
import http.server
import importlib.util
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  -- %s" % (name, str(extra)[:600]))


HITS = {"proxy": [], "target": []}


def handler(kind):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            HITS[kind].append(self.path)
            body = b'{"data": []}' if kind == "target" else b"proxied"
            self.send_response(200 if kind == "target" else 502)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


servers = {}
for kind in ("proxy", "target"):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler(kind))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[kind] = s
PROXY = "http://127.0.0.1:%d" % servers["proxy"].server_address[1]
TARGET = "http://127.0.0.1:%d/v1/models" % servers["target"].server_address[1]

home = tempfile.mkdtemp(prefix="t-e20-")
os.environ["HOME"] = home
for k in ("no_proxy", "NO_PROXY"):
    os.environ.pop(k, None)
os.environ["http_proxy"] = PROXY
os.environ["HTTP_PROXY"] = PROXY

check("the simulated proxy is what urllib would use for 127.0.0.1",
      urllib.request.getproxies().get("http") == PROXY
      and not urllib.request.proxy_bypass("127.0.0.1:1"),
      urllib.request.getproxies())

spec = importlib.util.spec_from_file_location(
    "doctor_e20", os.path.join(REPO, "dashboard", "doctor.py"))
doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc)

print("\n1. doctor's probes connect directly")
st, body = doc._http(TARGET, timeout=3)
check("_http reaches the target (200, its body)", st == 200 and '"data"' in body, (st, body))
check("nothing reached the proxy", HITS["proxy"] == [], HITS["proxy"])
HITS["target"][:] = []
check("_lane_up says the lane is up", doc._lane_up(TARGET) is True)
check("...by talking to the lane itself", HITS["target"] == ["/v1/models"] and HITS["proxy"] == [],
      HITS)

print("\n2. an opener server.py installed is not borrowed either")
urllib.request.install_opener(urllib.request.build_opener())   # proxy-honouring, like server.py's
HITS["target"][:] = []
st, _ = doc._http(TARGET, timeout=3)
check("still direct with an installed proxy-honouring opener",
      st == 200 and HITS["proxy"] == [] and HITS["target"] == ["/v1/models"], HITS)
# the control: the default path really would have gone to the proxy
try:
    urllib.request.urlopen(TARGET, timeout=3).close()
except urllib.error.HTTPError:
    pass                                        # the proxy answers 502
check("control: plain urlopen() here is captured by the proxy", len(HITS["proxy"]) == 1, HITS)

print("\n3. a refused connection is still (0, error), not a hang or a raise")
st, err = doc._http("http://127.0.0.1:9/x", timeout=2)
check("closed port -> (0, error text)", st == 0 and err, (st, err))

for s in servers.values():
    s.shutdown()
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
