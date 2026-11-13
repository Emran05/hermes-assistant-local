#!/usr/bin/env python3
"""2026-10-08 audit, P3 "MCP / harness" — four small hermes_mcp.py fixes.

  * a lone surrogate (JSON "\\ud800", from the dashboard or echoed from a
    client's own tool name) crashed the server in _send() or failed every
    read of the chat that held one in _bound();
  * a notification-shaped (id-less) tools/call still RAN the tool — writes
    included — while nobody got the answer;
  * memory_facts(add={pinned: "false"}) pinned the fact (bool("false"));
  * context_recent said "primary-lane turns" over a route that merges both
    lanes unless asked for `lane=primary`.

The REAL dashboard/hermes_mcp.py, as a subprocess for the crash and in
process (dash_get / dash_post stubbed) for the rest.  Offline.
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


TMP = tempfile.mkdtemp(prefix="hermes-p3mcp-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
with open(ALLOW, "w") as fh:
    json.dump({"writes": {"evals": True, "memory_facts": True}}, fh)

print("--- a lone surrogate in a client's request does not end the server")
reqs = ['{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"\\ud800x"}}',
        '{"jsonrpc":"2.0","id":2,"method":"nope\\udfff"}',
        '{"jsonrpc":"2.0","id":3,"method":"ping"}']
r = subprocess.run([sys.executable, SERVER], input=("\n".join(reqs) + "\n").encode(),
                   capture_output=True, timeout=20,
                   env=dict(os.environ, HERMES_MCP_ALLOW=ALLOW,
                            HERMES_MCP_DASHBOARD="http://127.0.0.1:9"))
replies = []
for line in r.stdout.decode("utf-8", "strict").splitlines():
    try:
        replies.append(json.loads(line))
    except ValueError:
        replies.append(None)
ids = [x.get("id") for x in replies if isinstance(x, dict)]
check("all three requests answered (ids 1, 2, 3)", ids == [1, 2, 3],
      (ids, r.stderr.decode("utf-8", "replace")[-300:]))
check("the unknown tool is -32602, the unknown method -32601",
      len(replies) == 3 and replies[0].get("error", {}).get("code") == -32602 and
      replies[1].get("error", {}).get("code") == -32601, replies)
check("the process exited cleanly at EOF", r.returncode == 0, r.returncode)

os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ.setdefault("HERMES_MCP_DASHBOARD", "http://127.0.0.1:9")
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

OUT = io.BytesIO()
M._OUT = OUT


def sent():
    rows = [json.loads(l) for l in OUT.getvalue().decode("utf-8").splitlines() if l]
    OUT.seek(0)
    OUT.truncate()
    return rows


print("--- a chat holding a lone surrogate stays readable")
M.dash_get = lambda path, params=None, timeout=None: {
    "title": "t", "messages": [{"role": "user", "text": "half an emoji \ud83d here", "ts": 1.0e9}]}
M.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
          "params": {"name": "chat_get", "arguments": {"session": "chat-x"}}})
rows = sent()
res = (rows[0].get("result") or {}) if rows else {}
check("chat_get answers (not an internal error)",
      rows and res.get("isError") is False and "half an emoji" in res["content"][0]["text"],
      rows)

print("--- an id-less tools/call has no side effect")
POSTED = []
M.dash_post = lambda path, payload, timeout=None: POSTED.append(path) or {"ok": True}
with contextlib.redirect_stderr(io.StringIO()):
    M.handle({"jsonrpc": "2.0", "method": "tools/call",
              "params": {"name": "evals", "arguments": {"run": True}}})
    M.handle({"jsonrpc": "2.0", "method": "tools/call",
              "params": {"name": "memory_facts", "arguments": {"add": {"text": "x"}}}})
check("the eval run was NOT started", "/api/evals/run" not in POSTED, POSTED)
check("no fact was stored", "/api/memory/facts" not in POSTED, POSTED)
check("and nothing was written to stdout", sent() == [])
M.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
          "params": {"name": "evals", "arguments": {"run": True}}})
check("control: the same call WITH an id does run", POSTED == ["/api/evals/run"], POSTED)
sent()

print("--- memory_facts pinned must be a real boolean")
POSTED[:] = []
BODIES = []
M.dash_post = lambda path, payload, timeout=None: BODIES.append(payload) or {
    "ok": True, "verdict": "added", "fact": {"id": 1, "kind": "fact",
                                            "pinned": payload.get("pinned", False),
                                            "text": payload["text"]}}
for raw in ("false", "true", 0, 1, "no"):
    try:
        M.t_memory_facts({"add": {"text": "likes tea", "pinned": raw}})
        msg = ""
    except M.ToolError as e:
        msg = str(e)
    check("pinned=%r is refused, not read as a boolean" % (raw,),
          "must be true or false" in msg and not BODIES, (msg, BODIES))
    BODIES[:] = []
M.t_memory_facts({"add": {"text": "likes tea", "pinned": False}})
check("pinned=false is sent as false", BODIES and BODIES[0].get("pinned") is False, BODIES)

print("--- context_recent asks for the lane it says it shows")
SEEN = []
M.dash_get = lambda path, params=None, timeout=None: SEEN.append((path, params)) or {
    "ok": True, "total": 1, "window": 262144,
    "turns": [{"ts": "10:00", "lane": "primary", "prompt_tokens": 100}]}
t = M.t_context_recent({"n": 3})
check("the route is asked for lane=primary (it merges BOTH lanes otherwise)",
      SEEN and SEEN[0][0] == "/api/context/recent" and
      (SEEN[0][1] or {}).get("lane") == "primary", SEEN)
check("and the header still says primary-lane turns", "primary-lane turns" in t, t[:120])

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
