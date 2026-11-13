#!/usr/bin/env python3
"""E81 — a hostile input line is answered and survived, never fatal.

2026-11-09 audit E81: hermes_mcp.main() caught only ValueError around
json.loads, so a deeply nested line (`[[[[…`) raised RecursionError and the
server exited with status 1 — the next request was never answered, against
"a malformed line is -32700 and the loop continues".  The line length was
unbounded too: a client could make the server read any amount into memory.

The REAL dashboard/hermes_mcp.py as a subprocess, stdin piped.  Offline (the
dashboard URL is a closed loopback port and no tool is called).
"""
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "hermes_mcp.py")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


TMP = tempfile.mkdtemp(prefix="hermes-e81-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
with open(ALLOW, "w") as fh:
    json.dump({}, fh)
ENV = dict(os.environ, HERMES_MCP_ALLOW=ALLOW, HERMES_MCP_DASHBOARD="http://127.0.0.1:9")


def run(lines):
    data = b"".join(l + b"\n" for l in lines)
    r = subprocess.run([sys.executable, SERVER], input=data, capture_output=True,
                       timeout=120, env=ENV)
    rows, clean = [], True
    for line in r.stdout.split(b"\n"):
        if not line:
            continue
        try:
            rows.append(json.loads(line.decode("utf-8", "strict")))
        except ValueError:
            clean = False
    return r, rows, clean


PING = lambda i: json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping"}).encode()

print("--- a deeply nested line")
for label, deep in (("array", b"[" * 200000), ("object", b'{"a":' * 100000),
                    ("closed array", b"[" * 50000 + b"]" * 50000)):
    r, rows, clean = run([PING(1), deep, PING(2)])
    err = r.stderr.decode("utf-8", "replace")
    check("%s: the process exits 0 at EOF" % label, r.returncode == 0, (r.returncode, err[-300:]))
    check("%s: stdout is only JSON-RPC lines" % label, clean, r.stdout[:200])
    # a closed 50k-deep array parses (it is then a refused batch, -32600)
    check("%s: the nested line is answered with an error, id null" % label,
          len(rows) == 3 and rows[1].get("id") is None and
          (rows[1].get("error") or {}).get("code") in (-32700, -32600), rows[1:2])
    check("%s: the request after it is answered" % label,
          [x.get("id") for x in rows if "result" in x] == [1, 2], rows)

print("--- an over-long line")
huge = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping",
                   "params": {"pad": "x" * (3 * 1024 * 1024)}}).encode()
r, rows, clean = run([PING(1), huge, PING(2), b"[" * 10, PING(3)])
err = r.stderr.decode("utf-8", "replace")
check("over-long: exits 0", r.returncode == 0, (r.returncode, err[-300:]))
check("over-long: stdout is only JSON-RPC lines", clean)
ids = [x.get("id") for x in rows]
check("over-long: refused with an error, never answered as id 9",
      9 not in ids and any((x.get("error") or {}).get("code") in (-32700, -32600)
                           for x in rows if x.get("id") is None), rows)
check("over-long: its tail is not parsed as further requests (one error for it)",
      len(rows) == 5, [(x.get("id"), (x.get("error") or {}).get("code")) for x in rows])
check("over-long: the pings around it are answered",
      [x.get("id") for x in rows if "result" in x] == [1, 2, 3], ids)

print("--- an over-long final line with no newline")
r = subprocess.run([sys.executable, SERVER], input=PING(1) + b"\n" + b"y" * (2 * 1024 * 1024),
                   capture_output=True, timeout=120, env=ENV)
check("no trailing newline: exits 0", r.returncode == 0,
      r.stderr.decode("utf-8", "replace")[-300:])
check("no trailing newline: ping answered, then one error",
      [json.loads(l).get("id") for l in r.stdout.splitlines() if l] == [1, None], r.stdout[:300])

print("--- the cap itself (review R4): MAX_LINE bytes is allowed, one more is not")
CAP = 1024 * 1024


def sized(n, i):
    base = {"jsonrpc": "2.0", "id": i, "method": "ping", "params": {"pad": ""}}
    base["params"]["pad"] = "x" * (n - len(json.dumps(base)))
    out = json.dumps(base).encode()
    assert len(out) == n, (len(out), n)
    return out


for n, answered in ((CAP - 1, True), (CAP, True), (CAP + 1, False), (CAP + 2, False)):
    r, rows, clean = run([sized(n, 7), PING(8)])
    got = [(x.get("id"), (x.get("error") or {}).get("code")) for x in rows]
    want = ([(7, None), (8, None)] if answered else [(None, -32600), (8, None)])
    check("a %d-byte message (cap %+d) is %s" % (n, n - CAP,
          "answered" if answered else "refused once"), got == want and clean, got)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
