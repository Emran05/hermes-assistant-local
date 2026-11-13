#!/usr/bin/env python3
"""C75 — the MCP privacy gating, in the unit tier, with non-boolean values.

2026-10-21 audit C75: hermes_mcp.py's allowlist gating (which tools are
listed, which write arguments exist, messages off by default) was tested only
in the live tier — never in CI — and never with a value that is not a JSON
boolean, which is how B06's `"false"`-means-on bug survived.

Drives the REAL dashboard/hermes_mcp.py over its real stdio protocol, one
subprocess per scope, with HERMES_MCP_ALLOW in a throwaway dir and
HERMES_MCP_DASHBOARD on a closed loopback port.  Every call made here is
refused or listed BEFORE any dashboard request, so nothing needs a network.
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


TMP = tempfile.mkdtemp(prefix="hermes-c75-")


class Client(object):
    def __init__(self, allow_path):
        env = dict(os.environ, HERMES_MCP_ALLOW=allow_path,
                   HERMES_MCP_DASHBOARD="http://127.0.0.1:9")
        self.p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, bufsize=0)
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "c75", "version": "1"}})
        self.raw({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def raw(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def call(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.raw(msg)
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout: " +
                               self.p.stderr.read().decode("utf-8", "replace")[-400:])
        return json.loads(line.decode("utf-8"))

    def tools(self):
        r = self.call("tools/list")
        return dict((t["name"], t) for t in (r.get("result") or {}).get("tools") or [])

    def tool(self, name, args=None):
        return self.call("tools/call", {"name": name, "arguments": args or {}})

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        return self.p.stderr.read().decode("utf-8", "replace")


def props(tools, name):
    return set(((tools.get(name) or {}).get("inputSchema") or {}).get("properties") or {})


def text(resp):
    res = resp.get("result") or {}
    return bool(res.get("isError")), ((res.get("content") or [{}])[0].get("text") or "")


def write(name, obj_or_text):
    p = os.path.join(TMP, name)
    with open(p, "w") as fh:
        fh.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
    return p


# ---------------------------------------------------------------------------
print("--- A. strings, numbers and null are NOT true")
A = write("a.json", {
    "tools": {"messages_search": "false", "trace_summary": "off", "doctor": 0,
              "notes_search": None, "calendar_next": "true"},
    "writes": {"evals": "true", "memory_facts": 1, "prompt_budget": "yes",
               "tool_budget": True}})
c = Client(A)
tools = c.tools()
for name, raw in (("messages_search", '"false"'), ("trace_summary", '"off"'),
                  ("doctor", "0"), ("notes_search", "null"), ("calendar_next", '"true"')):
    check("tools.%s = %s -> not listed" % (name, raw), name not in tools, sorted(tools))
check("an untouched default-on tool is listed", "hermes_search" in tools, sorted(tools))
for tname, arg, raw in (("evals", "run", '"true"'), ("memory_facts", "add", "1"),
                        ("prompt_budget", "profile", '"yes"')):
    check("writes.%s = %s -> %r absent from the schema" % (tname, raw, arg),
          arg not in props(tools, tname), sorted(props(tools, tname)))
check("writes.tool_budget = true -> max_chars IS in the schema",
      "max_chars" in props(tools, "tool_budget"), sorted(props(tools, "tool_budget")))
for name in ("messages_search", "trace_summary", "doctor"):
    r = c.tool(name, {"q": "x"} if name == "messages_search" else {})
    check("%s is not callable (-32602, same as an unknown tool)" % name,
          (r.get("error") or {}).get("code") == -32602, r)
err, t = text(c.tool("hermes_search", {"q": "lease", "source": "message"}))
check("hermes_search(source=message) is refused", err and "messages_search" in t, t[:200])
err, t = text(c.tool("evals", {"run": True}))
check("evals(run) is refused and names writes.evals",
      err and "writes.evals" in t, t[:200])
err, t = text(c.tool("memory_facts", {"add": {"text": "x"}}))
check("memory_facts(add) is refused and names writes.memory_facts",
      err and "writes.memory_facts" in t, t[:200])
err, t = text(c.tool("prompt_budget", {"profile": "lean"}))
check("prompt_budget(profile) is refused and names writes.prompt_budget",
      err and "writes.prompt_budget" in t, t[:200])
log = c.close()
check("each non-boolean value is named on stderr",
      all(k in log for k in ("tools.messages_search", "writes.evals", "tools.doctor")),
      log[-400:])

# ---------------------------------------------------------------------------
print("--- B. no file at all: created 0600, messages and every write off")
B = os.path.join(TMP, "fresh", "mcp-allow.json")
c = Client(B)
tools = c.tools()
check("messages_search not listed by default", "messages_search" not in tools)
for tname, arg in (("evals", "run"), ("memory_facts", "add"),
                   ("prompt_budget", "profile"), ("tool_budget", "max_chars")):
    check("default: %s has no %r argument" % (tname, arg), arg not in props(tools, tname))
c.close()
check("the default allowlist was created", os.path.exists(B))
check("created 0600 (%s)" % (oct(os.stat(B).st_mode & 0o777) if os.path.exists(B) else "-"),
      os.path.exists(B) and os.stat(B).st_mode & 0o777 == 0o600)
created = json.load(open(B)) if os.path.exists(B) else {}
check("the created file says messages_search false",
      (created.get("tools") or {}).get("messages_search") is False, created.get("tools"))

# ---------------------------------------------------------------------------
print("--- C. a corrupt or wrong-shaped file falls back to the defaults")
for label, body in (("not JSON", '{"tools": {"messages_search": true'),
                    ("a list", '[{"tools": {"messages_search": true}}]'),
                    ("tools is a list", '{"tools": ["messages_search"]}')):
    c = Client(write("c-%s.json" % label.replace(" ", "-"), body))
    tools = c.tools()
    check("%s: messages_search stays off" % label, "messages_search" not in tools,
          sorted(tools))
    check("%s: evals has no run argument" % label, "run" not in props(tools, "evals"))
    c.close()

# ---------------------------------------------------------------------------
print("--- D. control: a literal true does switch them on")
c = Client(write("d.json", {"tools": {"messages_search": True},
                            "writes": {"evals": True}}))
tools = c.tools()
check("messages_search: true -> listed", "messages_search" in tools, sorted(tools))
check("writes.evals: true -> run in the schema", "run" in props(tools, "evals"))
c.close()

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
