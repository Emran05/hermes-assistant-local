#!/usr/bin/env python3
"""Regression test for C81 + C82 (docs/audits/2026-10-21-codebase-audit.md).

C81 — the "offline" unit tier was not offline.  tools/tests/lib/usercustomize.py
refused only 127.0.0.1:{7788,8080,9119}; every other destination was open, so
ny_harness (and the aux loops it started by exec'ing server.py) resolved and
fetched Yahoo Finance and a dozen news feeds on every run, and the second
model lane (:8081) was not refused at all.

C82 — the guard's own self-check proved nothing: run.sh ran
`python3 -c 'import usercustomize ...'`, and an explicit import succeeds even
when the interpreter skipped usercustomize at start-up (venv,
PYTHONNOUSERSITE=1, python3 -s), i.e. exactly when the suites run unguarded.
The Node guard parsed only a string or `input.url`, so `fetch(new URL(...))`
went straight to the real fetch (it reached the live dashboard while this fix
was being written).

This suite starts fresh python3/node processes with the unit tier's own
environment and drives the REAL guards (tools/tests/lib/usercustomize.py,
offline-guard.js) and the REAL self-checks (guard_selfcheck.py/.js).  Every
"blocked" probe targets either TEST-NET-1 (192.0.2.1, a documentation range
that is never a real host) or a listener this suite opened itself, so a broken
guard can never reach a service the owner is running.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
LIB = os.path.join(REPO, "tools", "tests", "lib")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  %s" % (name, extra))


def tier_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONNOUSERSITE", "HERMES_TESTS_BLOCKED_PORTS",
                        "HERMES_TESTS_EGRESS_LOG", "NODE_OPTIONS", "PYTHONPATH")}
    env["HERMES_TESTS_OFFLINE"] = "1"
    env["PYTHONPATH"] = LIB
    env["NODE_OPTIONS"] = "--require " + os.path.join(LIB, "offline-guard.js")
    env.update(extra)
    return env


def py(code, **extra):
    p = subprocess.run([sys.executable, "-c", code], env=tier_env(**extra),
                       capture_output=True, text=True, timeout=60)
    return p


def node(code, **extra):
    p = subprocess.run(["node", "-e", code], env=tier_env(**extra),
                       capture_output=True, text=True, timeout=60)
    return p


def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s, s.getsockname()[1]


TMP = tempfile.mkdtemp(prefix="t-c81-")
import atexit, shutil  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)   # leave no temp dir behind

# ---------------------------------------------------------------------------
print("\n1. python: non-loopback egress is refused (C81)")
# ---------------------------------------------------------------------------
PROBE = r'''
import json, socket, urllib.request, urllib.error
out = {}
def tag(e): return "HERMES_TESTS_OFFLINE" in str(e)
s = socket.socket(); s.settimeout(1.5)
try: s.connect(("192.0.2.1", 8999)); out["connect_ip"] = "CONNECTED"
except OSError as e: out["connect_ip"] = tag(e)
try: socket.getaddrinfo("example.invalid", 80); out["gai"] = "RESOLVED"
except OSError as e: out["gai"] = tag(e)
try: socket.gethostbyname("example.invalid"); out["ghbn"] = "RESOLVED"
except OSError as e: out["ghbn"] = tag(e)
try: urllib.request.urlopen("http://example.invalid/", timeout=2); out["urlopen"] = "OPENED"
except Exception as e: out["urlopen"] = tag(e)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try: u.sendto(b"x", ("192.0.2.1", 8999)); out["udp"] = "SENT"
except OSError as e: out["udp"] = tag(e)
s6 = socket.socket(socket.AF_INET6); s6.settimeout(1.5)
try: s6.connect(("2001:db8::1", 8999)); out["connect_v6"] = "CONNECTED"
except OSError as e: out["connect_v6"] = tag(e)
print(json.dumps(out))
'''
egress_log = os.path.join(TMP, "egress.log")
p = py(PROBE, HERMES_TESTS_EGRESS_LOG=egress_log, HERMES_TESTS_SUITE="probe")
try:
    got = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    got = {}
for key, what in (("connect_ip", "TCP connect to an IP literal"),
                  ("gai", "getaddrinfo of a name"),
                  ("ghbn", "gethostbyname of a name"),
                  ("urlopen", "urllib.request.urlopen"),
                  ("udp", "UDP sendto"),
                  ("connect_v6", "TCP connect over IPv6")):
    check("%s is refused BY THE GUARD" % what, got.get(key) is True,
          (got.get(key), p.stderr[-300:]))
lines = open(egress_log).read().splitlines() if os.path.exists(egress_log) else []
check("every refusal is logged to HERMES_TESTS_EGRESS_LOG", len(lines) >= 5, lines)
check("log lines name the suite", all(l.startswith("probe pid=") for l in lines), lines[:2])

# ---------------------------------------------------------------------------
print("\n2. python: loopback stays usable; the model/dashboard ports do not")
# ---------------------------------------------------------------------------
srv, port = listener()
p = py("import socket,sys; c=socket.create_connection(('localhost', %d), timeout=3);"
       "c.close(); c=socket.create_connection(('127.0.0.1', %d), timeout=3); print('ok')"
       % (port, port))
check("a suite's own server on an ephemeral loopback port is reachable",
      p.returncode == 0 and p.stdout.strip() == "ok", p.stderr[-300:])
p = py("import socket\ntry:\n socket.create_connection(('127.0.0.1', %d), timeout=3)\n"
       " print('CONNECTED')\nexcept OSError as e:\n print('HERMES_TESTS_OFFLINE' in str(e))"
       % port, HERMES_TESTS_BLOCKED_PORTS=str(port))
check("HERMES_TESTS_BLOCKED_PORTS is honoured (and read per call)",
      p.stdout.strip() == "True", (p.stdout, p.stderr[-200:]))
srv.close()
VERDICTS = r'''
import json, usercustomize as u
v = lambda a: u._verdict(a) is not None
print(json.dumps({
  "ports": sorted(u._ports()),
  "8081": v(("127.0.0.1", 8081)), "7788": v(("localhost", 7788)),
  "8080": v(("::1", 8080)), "9119": v(("127.0.0.1", 9119)),
  "lo5000": v(("127.0.0.1", 5000)), "localhost5000": v(("localhost", 5000)),
  "mapped": v(("::ffff:127.0.0.1", 5000)), "lo127_8": v(("127.8.9.10", 5000)),
  "lan": v(("192.168.1.10", 5000)), "zero": v(("0.0.0.0", 5000)),
  "name": v(("example.com", 443)), "unix": v("/tmp/x.sock"),
}))
'''
p = py(VERDICTS)
try:
    v = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    v = {}
check("default blocked ports are 7788, 8080, 8081 and 9119",
      v.get("ports") == [7788, 8080, 8081, 9119], (v.get("ports"), p.stderr[-200:]))
for k in ("8081", "7788", "8080", "9119"):
    check("loopback :%s is refused" % k, v.get(k) is True, v.get(k))
for k, what in (("lo5000", "127.0.0.1:5000"), ("localhost5000", "localhost:5000"),
                ("mapped", "::ffff:127.0.0.1:5000"), ("lo127_8", "127.8.9.10:5000"),
                ("unix", "an AF_UNIX path")):
    check("%s is allowed" % what, v.get(k) is False, v.get(k))
for k, what in (("lan", "a LAN address"), ("zero", "0.0.0.0"), ("name", "a public name")):
    check("%s is refused" % what, v.get(k) is True, v.get(k))

p = subprocess.run([sys.executable, "-c",
                    "import socket,usercustomize as u;"
                    "print(u.HERMES_OFFLINE_GUARD, hasattr(socket.socket.connect,'__hermes_offline_guard__'))"],
                   env=dict(tier_env(), HERMES_TESTS_OFFLINE="0"),
                   capture_output=True, text=True, timeout=30)
check("inert without HERMES_TESTS_OFFLINE=1", p.stdout.strip() == "False False",
      (p.stdout, p.stderr[-200:]))

# ---------------------------------------------------------------------------
print("\n3. node: every call shape is refused (C81 + C82 fetch(new URL()))")
# ---------------------------------------------------------------------------
NODE_PROBE = r'''
const http = require("http"), https = require("https"), net = require("net");
const tag = (e) => !!(e && /HERMES_TESTS_OFFLINE/.test(String(e.message)));
const out = {};
(async () => {
  const T = "http://192.0.2.1:8999/";
  for (const [k, fn] of [["fetch_str", () => fetch(T)], ["fetch_url", () => fetch(new URL(T))],
                         ["fetch_req", () => fetch(new Request(T))]]) {
    try { await fn(); out[k] = "CONNECTED"; } catch (e) { out[k] = tag(e); }
  }
  for (const [k, fn] of [["http_get_url", () => http.get(new URL(T))],
                         ["http_req_opts", () => http.request({ host: "192.0.2.1", port: 8999 })],
                         ["https_req_str", () => https.request("https://example.invalid/")],
                         ["net_connect", () => net.connect({ host: "192.0.2.1", port: 8999 })],
                         ["net_connect_name", () => net.connect(443, "example.invalid")],
                         ["tls_connect", () => require("tls").connect(443, "example.invalid")]]) {
    try { const r = fn(); if (r && r.on) r.on("error", () => {}); if (r && r.destroy) r.destroy(); out[k] = "NO THROW"; }
    catch (e) { out[k] = tag(e); }
  }
  // WebSocket goes through undici, not the http module: only the socket
  // layer sees it, so the egress log is the evidence
  await new Promise((res) => { const ws = new WebSocket("ws://192.0.2.1:8999/");
    ws.onerror = () => res(); ws.onopen = () => { out.ws = "OPEN"; res(); }; setTimeout(res, 4000); });
  const V = globalThis.__HERMES_OFFLINE_VERDICT__;
  out.v8081 = V("127.0.0.1", 8081) !== null; out.v7788 = V("localhost", 7788) !== null;
  out.v8080 = V("::1", 8080) !== null; out.v9119 = V("[::1]", 9119) !== null;
  out.vlo = V("127.0.0.1", 5000) === null; out.vmapped = V("::ffff:127.0.0.1", 5000) === null;
  out.vlan = V("10.1.2.3", 5000) !== null;
  console.log(JSON.stringify(out));
})();
'''
nlog = os.path.join(TMP, "node-egress.log")
p = node(NODE_PROBE, HERMES_TESTS_EGRESS_LOG=nlog, HERMES_TESTS_SUITE="nprobe")
try:
    n = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    n = {}
for k, what in (("fetch_str", "fetch(string)"), ("fetch_url", "fetch(new URL())"),
                ("fetch_req", "fetch(new Request())"), ("http_get_url", "http.get(new URL())"),
                ("http_req_opts", "http.request(options)"),
                ("https_req_str", "https.request(string)"),
                ("net_connect", "net.connect(options)"),
                ("net_connect_name", "net.connect(port, name)"),
                ("tls_connect", "tls.connect(port, name)")):
    check("node %s to a non-loopback target is refused BY THE GUARD" % what,
          n.get(k) is True, (n.get(k), p.stderr[-300:]))
nlines = open(nlog).read().splitlines() if os.path.exists(nlog) else []
check("node: the WebSocket (undici) connect was refused at the socket layer",
      "ws" not in n and any("connect egress to 192.0.2.1:8999" in l for l in nlines),
      (n.get("ws"), nlines[-3:]))
for k, what in (("v8081", "loopback :8081"), ("v7788", "localhost:7788"),
                ("v8080", "::1 :8080"), ("v9119", "[::1]:9119"), ("vlan", "a LAN address")):
    check("node verdict refuses %s" % what, n.get(k) is True, n.get(k))
check("node verdict allows 127.0.0.1:5000", n.get("vlo") is True, n.get("vlo"))
check("node verdict allows ::ffff:127.0.0.1:5000", n.get("vmapped") is True, n.get("vmapped"))

srv, port = listener()
p = node("const net=require('net'); const c=net.connect(%d,'127.0.0.1',()=>{console.log('ok');c.destroy();});"
         "c.on('error',e=>console.log('ERR',e.message));" % port)
check("node: a suite's own loopback server stays reachable", p.stdout.strip() == "ok",
      (p.stdout, p.stderr[-200:]))
srv.close()

# ---------------------------------------------------------------------------
print("\n4. the self-checks report the truth (C82)")
# ---------------------------------------------------------------------------
SC_PY = os.path.join(LIB, "guard_selfcheck.py")
SC_JS = os.path.join(LIB, "guard_selfcheck.js")
r = subprocess.run([sys.executable, SC_PY], env=tier_env(), capture_output=True,
                   text=True, timeout=60)
check("python self-check passes when the guard loaded", r.returncode == 0, r.stderr)
r = subprocess.run([sys.executable, SC_PY], env=tier_env(PYTHONNOUSERSITE="1"),
                   capture_output=True, text=True, timeout=60)
check("python self-check FAILS under PYTHONNOUSERSITE=1 (was a vacuous pass)",
      r.returncode != 0, (r.returncode, r.stderr))
r = subprocess.run([sys.executable, "-s", SC_PY], env=tier_env(),
                   capture_output=True, text=True, timeout=60)
check("python self-check FAILS under python3 -s (no user site)",
      r.returncode != 0, (r.returncode, r.stderr))
r = subprocess.run([sys.executable, SC_PY], env=dict(tier_env(), HERMES_TESTS_OFFLINE="0"),
                   capture_output=True, text=True, timeout=60)
check("python self-check FAILS when the guard is switched off", r.returncode != 0,
      r.returncode)
r = subprocess.run(["node", SC_JS], env=tier_env(), capture_output=True, text=True,
                   timeout=60)
check("node self-check passes when the guard is preloaded", r.returncode == 0, r.stderr)
r = subprocess.run(["node", SC_JS], env=dict(tier_env(), NODE_OPTIONS=""),
                   capture_output=True, text=True, timeout=60)
check("node self-check FAILS when NODE_OPTIONS lost the preload", r.returncode != 0,
      (r.returncode, r.stderr))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
