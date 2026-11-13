#!/usr/bin/env python3
"""C71 — a non-finite number in mcp-allow.json (or a tool argument) falls back.

2026-10-21 audit C71: JSON's `1e999` is a float infinity and `int(inf)` raises
OverflowError, which load_allow() did not catch — `"max_results": 1e999` (or
Infinity) stopped hermes_mcp.py from starting at all, against the documented
"a bad value falls back to the default".

Starts the REAL dashboard/hermes_mcp.py as a subprocess (stdio, one ping) per
value, then checks the in-process clamps.  HERMES_MCP_ALLOW points at a
throwaway file; HERMES_MCP_DASHBOARD at a closed loopback port.  Offline.
"""
import io
import contextlib
import json
import os
import subprocess
import sys
import tempfile

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


TMP = tempfile.mkdtemp(prefix="hermes-c71-")
ENV = dict(os.environ, HERMES_MCP_DASHBOARD="http://127.0.0.1:9")

# raw JSON text for max_results -> the value the server must settle on
CASES = [("1e999", 20), ("-1e999", 20), ("Infinity", 20), ("-Infinity", 20),
         ("NaN", 20), ('"abc"', 20), ("true", 20), ("null", 20), ("[5]", 20),
         ("7.9", 7), ("3", 3), ("500", 50), ("-4", 1)]

print("--- the server starts whatever max_results says")
for i, (raw, want) in enumerate(CASES):
    p = os.path.join(TMP, "allow-%d.json" % i)
    with open(p, "w") as fh:
        fh.write('{"max_results": %s, "tools": {"hermes_search": true}}' % raw)
    env = dict(ENV, HERMES_MCP_ALLOW=p)
    try:
        r = subprocess.run([sys.executable, SERVER],
                           input=b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                           capture_output=True, env=env, timeout=20)
        rc, out, err = r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        rc, out, err = -1, b"", "timeout"
    try:
        reply = json.loads(out.decode("utf-8").splitlines()[0])
    except (ValueError, IndexError):
        reply = None
    check("max_results %s: server starts and answers ping" % raw,
          rc == 0 and reply == {"jsonrpc": "2.0", "id": 1, "result": {}},
          "rc=%s stdout=%r stderr=%s" % (rc, out[:80], err[-200:]))
    check("max_results %s: no traceback on stderr" % raw, "Traceback" not in err,
          err[-300:])

sys.path.insert(0, DASH)
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "import.json")
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402
assert M.ALLOW_PATH.startswith(TMP), M.ALLOW_PATH

print("--- load_allow() settles on a sane count")
for i, (raw, want) in enumerate(CASES):
    saved = M.ALLOW_PATH
    M.ALLOW_PATH = os.path.join(TMP, "allow-%d.json" % i)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            got = M.load_allow()["max_results"]
    except Exception as e:                      # the old code raised here
        got = "raised %s" % type(e).__name__
    finally:
        M.ALLOW_PATH = saved
    check("max_results %s -> %r" % (raw, want), got == want, got)

print("--- client arguments: a non-finite number is not an internal error")
check("_limit(inf) is the cap", M._limit(float("inf")) == M.ALLOW["max_results"],
      M._limit(float("inf")))
check("_limit(nan) is the cap", M._limit(float("nan")) == M.ALLOW["max_results"])
check("_int(inf) falls back", M._int(float("inf"), 7) == 7)
check("_n(inf) is '?'", M._n(float("inf")) == "?")

M.dash_get = lambda path, params=None, timeout=None: {
    "title": "t", "messages": [{"role": "user", "text": "hi", "ts": 1.0e9}]}
r = M.do_tools_call({"name": "chat_get", "arguments": {"session": "chat-x", "last_n": 1e999}})
check("chat_get(last_n=inf) answers, not an internal error",
      r.get("isError") is False and "hi" in r["content"][0]["text"], r)

M.ALLOW["writes"]["tool_budget"] = True
try:
    M.t_tool_budget({"max_chars": float("inf")})
    msg = ""
except M.ToolError as e:
    msg = str(e)
except Exception as e:
    msg = "internal %s" % type(e).__name__
finally:
    M.ALLOW["writes"]["tool_budget"] = False
check("tool_budget(max_chars=inf) is a plain refusal", "must be a number" in msg, msg)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
