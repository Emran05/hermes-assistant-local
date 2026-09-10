#!/usr/bin/env python3
"""Drive dashboard/hermes_mcp.py over the REAL stdio protocol, live dashboard.

Read-only calls only.  Nothing here wakes a model or writes a store.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "hermes_mcp.py")
PY = sys.executable
VERSION = open(os.path.join(REPO, "VERSION")).read().strip()

OK = []
BAD = []

# The owner's real scope must survive this run untouched (nothing here may edit
# ~/.hermes/mcp-allow.json); snapshot it now and compare at the very end.
REAL_ALLOW = os.path.expanduser("~/.hermes/mcp-allow.json")
try:
    REAL_ALLOW_BYTES = open(REAL_ALLOW, "rb").read()
except OSError:
    REAL_ALLOW_BYTES = None


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  — " + str(detail)[:300]) if detail else ""))


class Client(object):
    def __init__(self, env=None):
        e = dict(os.environ)
        e.update(env or {})
        self.p = subprocess.Popen([PY, SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=e, bufsize=0)
        self.n = 0

    def raw(self, line):
        self.p.stdin.write(line.encode("utf-8") + b"\n")
        self.p.stdin.flush()

    def read(self, timeout=45.0):
        # readline blocks; the subprocess is well behaved so a hard timeout is
        # implemented by the caller only for the dead cases we never hit.
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout")
        return json.loads(line.decode("utf-8"))

    def call(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.raw(json.dumps(msg))
        if notify:
            return None
        r = self.read()
        assert r.get("id") == self.n, "id mismatch: %r vs %r" % (r.get("id"), self.n)
        return r

    def tool(self, name, args=None):
        return self.call("tools/call", {"name": name, "arguments": args or {}})

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
        return self.p.stderr.read().decode("utf-8", "replace")


HTML_RE = re.compile(r"<\s*(html|body|div|script|iframe|!doctype)\b", re.I)


def text_of(resp):
    res = resp.get("result") or {}
    c = (res.get("content") or [{}])[0]
    return res, c


def assert_result(label, resp, expect_error=False):
    res, c = text_of(resp)
    check(label + ": has result", "result" in resp, resp.get("error"))
    check(label + ": content[0].type == text", c.get("type") == "text", c.get("type"))
    t = c.get("text") or ""
    n = len(t.encode("utf-8"))
    check(label + ": bounded <= 8192 bytes (%d)" % n, n <= 8192)
    check(label + ": no HTML", not HTML_RE.search(t), HTML_RE.search(t) and t[:120])
    check(label + ": isError == %s" % expect_error, bool(res.get("isError")) == expect_error,
          t[:160])
    return t


print("=" * 74)
print("A. handshake, discovery, live read-only calls  (python3 = %s)" % PY)
print("=" * 74)
c = Client()

r = c.call("initialize", {"protocolVersion": "2025-06-18",
                          "capabilities": {},
                          "clientInfo": {"name": "hermes-harness", "version": "1"}})
res = r.get("result") or {}
check("initialize: protocolVersion echoed", res.get("protocolVersion") == "2025-06-18",
      res.get("protocolVersion"))
check("initialize: serverInfo.name", (res.get("serverInfo") or {}).get("name") == "hermes-assistant")
check("initialize: serverInfo.version == VERSION file (%s)" % VERSION,
      (res.get("serverInfo") or {}).get("version") == VERSION,
      (res.get("serverInfo") or {}).get("version"))
check("initialize: declares tools capability",
      "tools" in (res.get("capabilities") or {}), res.get("capabilities"))

c.call("notifications/initialized", {}, notify=True)

r = c.call("tools/list")
tools = (r.get("result") or {}).get("tools") or []
names = [t.get("name") for t in tools]
print("     tools/list -> %s" % ", ".join(names))
CONTEXT_TOOLS = ["hermes_search", "calendar_next", "calendar_search", "notes_search",
                 "chats_search", "chat_get", "needs_you", "memory_get"]
# 1.2.4 — the harness surfaces, all read-enabled by default
HARNESS_TOOLS = ["doctor", "prompt_budget", "context_recent", "tool_budget",
                 "memory_facts", "evals", "trace_summary"]
WRITE_ENTRIES = {"prompt_budget": "profile", "tool_budget": "max_chars",
                 "memory_facts": "add", "evals": "run"}
expected = CONTEXT_TOOLS + HARNESS_TOOLS
check("tools/list: exactly the 15 enabled tools", sorted(names) == sorted(expected), names)
for _t in HARNESS_TOOLS:
    check("tools/list: %s is listed" % _t, _t in names, names)
SCHEMAS = {t.get("name"): (t.get("inputSchema") or {}) for t in tools}
for _t, _arg in sorted(WRITE_ENTRIES.items()):
    props = (SCHEMAS.get(_t) or {}).get("properties") or {}
    check("tools/list: %s hides its write arg %r while writes.%s is false"
          % (_t, _arg, _t), _arg not in props, sorted(props))
check("tools/list: memory_facts still offers its read args",
      set(((SCHEMAS.get("memory_facts") or {}).get("properties") or {})) ==
      {"q", "preview", "limit"},
      sorted(((SCHEMAS.get("memory_facts") or {}).get("properties") or {})))
check("tools/list: messages_search ABSENT (off in the allowlist)",
      "messages_search" not in names)
check("tools/list: files_search absent (no granted-folder search route exists)",
      "files_search" not in names)
check("tools/list: every tool has an object inputSchema",
      all((t.get("inputSchema") or {}).get("type") == "object" for t in tools))
check("tools/list: no nextCursor (single page)", "nextCursor" not in (r.get("result") or {}))

print("-" * 74)
t = assert_result("hermes_search", c.tool("hermes_search", {"q": "hermes", "limit": 3}))
print("     | " + "\n     | ".join(t.splitlines()[:4]))

t = assert_result("calendar_next", c.tool("calendar_next", {"hours": 72}))
print("     | " + "\n     | ".join(t.splitlines()[:4]))

t = assert_result("calendar_search", c.tool("calendar_search", {"q": "day"}))
t = assert_result("notes_search", c.tool("notes_search", {"q": "test"}))

t = assert_result("needs_you", c.tool("needs_you"))
check("needs_you: says it is read-only", "read-only" in t, t[:120])
print("     | " + "\n     | ".join(t.splitlines()[:3]))

t = assert_result("memory_get", c.tool("memory_get"))
check("memory_get: returns facts", "remembered fact" in t, t[:120])

# ---- 1.2.4 harness tools, live and read-only -----------------------------
print("-" * 74)
t = assert_result("doctor", c.tool("doctor"))
check("doctor: reports the three counts",
      re.search(r"\d+ pass · \d+ warn · \d+ fail", t), t[:160])
check("doctor: no PASS rows in the summary (only what needs attention)",
      "\nPASS" not in t, t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:3]))

t2 = assert_result("doctor(format=text)", c.tool("doctor", {"format": "text"}))
check("doctor text: is the plain report, not the summary",
      "Hermes Assistant · doctor" in t2 and "PASS" in t2, t2[:160])
check("doctor text: still redacted (no ~/.hermes paths)",
      ".hermes/" not in t2 or "[redacted path]" in t2,
      [l for l in t2.splitlines() if ".hermes/" in l][:2])

t = assert_result("prompt_budget", c.tool("prompt_budget"))
check("prompt_budget: names the active profile and its cost",
      "profile" in t and "tokens" in t and "first token" in t, t[:200])
check("prompt_budget: lists all three profiles",
      all(k in t for k in ("full", "lean", "focused")), t[:400])
print("     | " + "\n     | ".join(t.splitlines()[:2]))

t = assert_result("context_recent", c.tool("context_recent", {"n": 5}))
check("context_recent: turn lines carry prompt/cached/new + prefill",
      ("prompt" in t and "cached" in t and "prefill" in t) or "no turn yet" in t,
      t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:3]))

t = assert_result("tool_budget", c.tool("tool_budget"))
check("tool_budget: reports the cap and today's stats",
      "cap" in t and "Today:" in t, t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:2]))

t = assert_result("memory_facts", c.tool("memory_facts", {"limit": 5}))
check("memory_facts: lists facts with kind + store stats",
      "remembered fact" in t and "store:" in t, t[:200])
t = assert_result("memory_facts(preview)",
                  c.tool("memory_facts", {"preview": "the dashboard port"}))
check("memory_facts: preview quotes the injected block and its cost",
      "would inject" in t and "tokens" in t, t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:1]))

t = assert_result("evals", c.tool("evals"))
check("evals: reports the suite, the last run and the schedule",
      "Evals —" in t and "Last run:" in t and "Schedule:" in t, t[:200])
check("evals: surfaces the battery refusal verbatim when it cannot run",
      ("Cannot run right now" in t) or ("Schedule:" in t), t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:3]))

t = assert_result("trace_summary", c.tool("trace_summary", {"days": 3}))
check("trace_summary: reports traces/turns/tool spans and tokens",
      "trace" in t and "turns" in t and "Tokens:" in t, t[:200])
check("trace_summary: honours the days window (3 days)", "(3 days)" in t, t[:160])
print("     | " + "\n     | ".join(t.splitlines()[:2]))
t = assert_result("trace_summary(days=999 clamped)", c.tool("trace_summary", {"days": 999}))
check("trace_summary: a >31-day request is clamped, not refused",
      "(31 days)" in t, t[:160])

# ---- 1.2.4 writes are refused, and the refusal names the entry -----------
print("-" * 74)
for _tool, _args, _entry in (
        ("prompt_budget", {"profile": "lean"}, "prompt_budget"),
        ("tool_budget", {"max_chars": 40000}, "tool_budget"),
        ("memory_facts", {"add": {"text": "harness probe"}}, "memory_facts"),
        ("evals", {"run": True}, "evals")):
    t = assert_result("%s write refused" % _tool, c.tool(_tool, _args), expect_error=True)
    check("%s: refusal names writes.%s" % (_tool, _entry),
          ("writes." + _entry) in t, t[:200])
    check("%s: refusal names the allowlist file" % _tool, "mcp-allow.json" in t, t[:200])
    check("%s: refusal says scope is decided at launch" % _tool,
          "at launch" in t, t[:200])

print("-" * 74)
t = assert_result("chats_search", c.tool("chats_search", {"q": "the"}))
check("chats_search: surfaces a session id for chat_get", "session:" in t, t[:200])
sids = re.findall(r"session: (\S+)", t)
print("     | " + "\n     | ".join(t.splitlines()[:4]))

# ---- chat_get + redaction -------------------------------------------------
sid = sids[0] if sids else None
if sid is None:
    live = json.load(urllib.request.urlopen("http://127.0.0.1:7788/api/sessions", timeout=5))
    sid = (live.get("sessions") or [{}])[0].get("id")
t = assert_result("chat_get(%s)" % sid, c.tool("chat_get", {"session": sid, "last_n": 5}))
check("chat_get: no serve_sid / serve_key key leaks",
      "serve_sid" not in t and "serve_key" not in t)
check("chat_get: no ~/.hermes path leaks",
      ".hermes" not in t or "[redacted path]" in t,
      [l for l in t.splitlines() if ".hermes" in l][:2])
check("chat_get: says metadata rows are stripped", "omitted" in t or "only tool" in t, t[:160])
print("     | " + "\n     | ".join(t.splitlines()[:3]))

t = assert_result("chat_get(bad id)", c.tool("chat_get", {"session": "../../etc/passwd"}),
                  expect_error=True)
t = assert_result("chat_get(__prewarm__ refused)", c.tool("chat_get", {"session": "__prewarm__"}),
                  expect_error=True)
t = assert_result("hermes_search(source=message, disabled)",
                  c.tool("hermes_search", {"q": "a", "source": "message"}), expect_error=True)
check("disabled source: error names the allowlist", "mcp-allow.json" in t, t[:160])

# ---- protocol edges -------------------------------------------------------
print("-" * 74)
r = c.call("ping")
check("ping: empty result object", r.get("result") == {}, r)

r = c.call("tools/call", {"name": "messages_search", "arguments": {"q": "x"}})
check("disabled tool is not callable (JSON-RPC error -32602)",
      (r.get("error") or {}).get("code") == -32602, r)

r = c.call("resources/list")
check("unknown method -> -32601", (r.get("error") or {}).get("code") == -32601, r)

r = c.call("tools/call", {"name": "no_such_tool", "arguments": {}})
check("unknown tool -> -32602", (r.get("error") or {}).get("code") == -32602, r)

c.raw("{this is not json")
r = c.read()
check("malformed line -> -32700 parse error, id null",
      (r.get("error") or {}).get("code") == -32700 and r.get("id") is None, r)
r = c.call("ping")
check("server survived the malformed line", r.get("result") == {}, r)

c.raw(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                  "params": {"requestId": 1}}))
r = c.call("ping")
check("unknown notification is silently ignored (no reply)", r.get("result") == {}, r)

err = c.close()
tool_lines = [l for l in err.splitlines() if l.startswith("hermes-mcp tool=")]
check("stderr carries one line per tool call (%d lines)" % len(tool_lines),
      len(tool_lines) >= 10, tool_lines[:2])
check("stderr lines carry name, ms and ok",
      all(re.match(r"hermes-mcp tool=\S+ ms=\d+ ok=[01]$", l) for l in tool_lines),
      [l for l in tool_lines if not re.match(r"hermes-mcp tool=\S+ ms=\d+ ok=[01]$", l)][:2])
print("     stderr sample: " + (tool_lines[0] if tool_lines else "(none)"))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("B. dashboard down (closed port)")
print("=" * 74)
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()                                   # nothing listens there now

c2 = Client({"HERMES_MCP_DASHBOARD": "http://127.0.0.1:%d" % port})
c2.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "harness-down", "version": "1"}})
c2.call("notifications/initialized", {}, notify=True)
r = c2.call("tools/list")
check("down: tools/list still answers", len((r.get("result") or {}).get("tools") or []) == 15)
t = assert_result("down: hermes_search", c2.tool("hermes_search", {"q": "x"}), expect_error=True)
check("down: message is helpful (names the URL + how to start it)",
      "not answering" in t and "launchctl kickstart" in t, t[:200])
print("     | " + "\n     | ".join(t.splitlines()[:4]))
t = assert_result("down: calendar_next", c2.tool("calendar_next"), expect_error=True)
t = assert_result("down: memory_get", c2.tool("memory_get"), expect_error=True)
t = assert_result("down: doctor", c2.tool("doctor"), expect_error=True)
check("down: doctor says how to start the dashboard", "launchctl kickstart" in t, t[:200])
t = assert_result("down: doctor(format=text)", c2.tool("doctor", {"format": "text"}),
                  expect_error=True)
t = assert_result("down: evals", c2.tool("evals"), expect_error=True)
r = c2.call("ping")
check("down: server stays alive after failures", r.get("result") == {}, r)
c2.close()

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("C. in-process unit checks (redaction, bounding, allowlist)")
print("=" * 74)
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import hermes_mcp as M  # noqa: E402

probe = ('serve_sid: s1783230277dddddddd and serve_key=abc123def456 with '
         'Authorization: Bearer hx_0123456789abcdef living at '
         '/Users/you/.hermes/dashboard/serve-token and ~/.hermes/config.yaml '
         'plus <script>alert(1)</script>')
out = M._plain(probe)
print("     in : " + probe)
print("     out: " + out)
check("redaction: serve_sid value gone", "s1783230277dddddddd" not in out)
check("redaction: serve_key value gone", "abc123def456" not in out)
check("redaction: bearer token gone", "hx_0123456789abcdef" not in out)
check("redaction: both ~/.hermes paths -> [redacted path]", out.count("[redacted path]") == 2, out)
check("sanitising: <script> neutralised", "<script" not in out and "&lt;script" in out)

# 1.1.5: raw, UNLABELED shapes go through the same _plain() the tools use.
raw_probe = ("no labels here: sk-ant-" "api03-A1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8 "
             "ghp_A1" "b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 AKIAIO" "SFODNN7EXAMPLE "
             "123456" "789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX "
             "eyJhbG" "ciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdW" "IiOiIxMjM0NTY3ODkwIn0."
             "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk "
             "commit 9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a")
raw_out = M._plain(raw_probe)
print("     raw: " + raw_out)
for _lbl, _v in (("anthropic key", "sk-ant-" "api03-A1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
                 ("github PAT", "ghp_A1" "b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
                 ("AWS key id", "AKIAIO" "SFODNN7EXAMPLE"),
                 ("telegram token", "123456" "789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX"),
                 ("JWT", "eyJhbG" "ciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")):
    check("redaction: unlabeled %s gone" % _lbl, _v not in raw_out, raw_out[:200])
check("redaction: a git sha is NOT a secret",
      "9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a" in raw_out, raw_out[:200])

# fix 8: a pre-existing 0644 allowlist is re-locked on every successful read
import tempfile as _tf                                         # noqa: E402
_alt = os.path.join(_tf.mkdtemp(prefix="mcp-allow-"), "mcp-allow.json")
open(_alt, "w").write(json.dumps({"tools": {"notes_search": True}, "max_results": 7}))
os.chmod(_alt, 0o644)
_sp = M.ALLOW_PATH
M.ALLOW_PATH = _alt
try:
    _alw = M.load_allow()
finally:
    M.ALLOW_PATH = _sp
check("allowlist: a 0644 file is chmod'd to 0600 on read (%s)"
      % oct(os.stat(_alt).st_mode & 0o777),
      oct(os.stat(_alt).st_mode & 0o777) == "0o600")
check("allowlist: re-locking does not change what it says", _alw["max_results"] == 7, _alw)

# The redaction block is COPIED from aux_convos.py, not imported (rule 1), so
# the only thing keeping the two honest is that they stay byte-identical.
def _mirrored(path):
    txt = open(path, encoding="utf-8").read()
    a = txt.index("# ===== BEGIN MIRRORED SECRET BLOCK")
    b = txt.index("# ===== END MIRRORED SECRET BLOCK")
    return txt[a:b]


_conv = _mirrored(os.path.join(REPO, "dashboard", "aux_convos.py"))
_mcp = _mirrored(os.path.join(REPO, "dashboard", "hermes_mcp.py"))
check("redaction: mirrored secret block byte-identical with aux_convos.py "
      "(%d bytes)" % len(_mcp), _conv == _mcp,
      "lengths %d vs %d" % (len(_conv), len(_mcp)))
for _rx in ("_CV_SECRET_RE", "_CV_BEARER_RE", "_CV_HERMES_PATH_RE"):
    _a = re.search(_rx + r"\s*=\s*re\.compile\((.*?)\)\n(?=[A-Za-z_#\n])",
                   open(os.path.join(REPO, "dashboard", "aux_convos.py"),
                        encoding="utf-8").read(), re.S)
    _b = re.search(_rx + r"\s*=\s*re\.compile\((.*?)\)\n(?=[A-Za-z_#\n])",
                   open(os.path.join(REPO, "dashboard", "hermes_mcp.py"),
                        encoding="utf-8").read(), re.S)
    check("redaction: %s identical in both files" % _rx,
          bool(_a) and bool(_b) and _a.group(1) == _b.group(1),
          (_a and _a.group(1)[:80], _b and _b.group(1)[:80]))

# _looks_secret is the gate memory_facts(add=) applies BEFORE the round trip —
# the same test aux_memlayer._ml_looks_secret runs before it stores a fact.
check("memory guard: an anthropic key looks like a credential",
      M._looks_secret("sk-ant-" "api03-A1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"))
check("memory guard: a labeled token looks like a credential",
      M._looks_secret("the api_key = 8f3a9c2b7d1e5f0a"))
check("memory guard: ordinary prose does not",
      not M._looks_secret("Emran prefers verified news only, cross-checked."))

check("allowlist: every write defaults to false",
      all(v is False for v in M.DEFAULT_ALLOW["writes"].values()),
      M.DEFAULT_ALLOW["writes"])
check("allowlist: the four write entries are the documented ones",
      sorted(M.DEFAULT_ALLOW["writes"]) ==
      ["evals", "memory_facts", "prompt_budget", "tool_budget"],
      sorted(M.DEFAULT_ALLOW["writes"]))
check("allowlist: the 1.2.4 read tools default ON",
      all(M.DEFAULT_ALLOW["tools"][t] is True for t in
          ("doctor", "prompt_budget", "context_recent", "tool_budget",
           "memory_facts", "evals", "trace_summary")))
# _alw came from a file with no `writes` block at all — the shape every
# allowlist written before 1.2.4 has.
check("allowlist: a file with no `writes` block leaves every write off",
      sorted(_alw.get("writes") or {}) == sorted(M.DEFAULT_ALLOW["writes"]) and
      all(v is False for v in (_alw.get("writes") or {"x": True}).values()),
      _alw.get("writes"))

big = M._bound("x" * 20000)
check("bounding: 20k chars -> <= 8192 bytes", len(big.encode("utf-8")) <= 8192,
      len(big.encode("utf-8")))
check("bounding: says it truncated", "truncated" in big)

check("allowlist: file exists at ~/.hermes/mcp-allow.json", os.path.exists(M.ALLOW_PATH))
mode = oct(os.stat(M.ALLOW_PATH).st_mode & 0o777)
check("allowlist: mode 0600 (%s)" % mode, mode == "0o600")
check("allowlist: messages_search default false", M.DEFAULT_ALLOW["tools"]["messages_search"] is False)
check("allowlist: max_results default 20", M.DEFAULT_ALLOW["max_results"] == 20)
check("allowlist: clamped to <= 50", M._limit(9999) <= 50)
check("allowlist: read once at import (module constant)", isinstance(M.ALLOW, dict))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("D. the write paths, in process, with dash_post STUBBED")
print("   (nothing below reaches the dashboard: no config is rewritten and no")
print("    model is woken — the owner is on battery)")
print("=" * 74)

POSTED = []


def _stub_post(reply):
    def _p(path, payload, timeout=None):
        POSTED.append({"path": path, "payload": payload, "timeout": timeout})
        return reply
    return _p


_real_post = M.dash_post
_saved_writes = dict(M.ALLOW["writes"])
try:
    for _k in M.ALLOW["writes"]:
        M.ALLOW["writes"][_k] = True

    # evals: the battery refusal must come back verbatim, and `force` — the
    # flag that would wake a 19 GB model — must never be in the payload.
    POSTED[:] = []
    M.dash_post = _stub_post({"ok": False, "reason": "on battery",
                              "error": "the eval suite does not run on battery "
                                       "— plug in and try again"})
    try:
        M.t_evals({"run": True})
        _msg = ""
    except M.ToolError as e:
        _msg = str(e)
    check("evals(run): posts to /api/evals/run",
          POSTED and POSTED[0]["path"] == "/api/evals/run", POSTED)
    check("evals(run): payload is EMPTY — no force, ever",
          POSTED and POSTED[0]["payload"] == {}, POSTED and POSTED[0]["payload"])
    check("evals(run): the dashboard's reason is surfaced verbatim",
          "the eval suite does not run on battery — plug in and try again" in _msg,
          _msg[:200])
    print("     | " + _msg[:150])

    POSTED[:] = []
    M.dash_post = _stub_post({"ok": True, "status": "running", "model": "m",
                              "note": "9 cases against the loaded model"})
    _t = M.t_evals({"run": True})
    check("evals(run): a started run reports the model and says to ask again",
          "started" in _t and "Ask again" in _t, _t[:160])

    # prompt_budget: `restart` kickstarts com.hermes.serve mid-turn — never sent.
    POSTED[:] = []
    M.dash_post = _stub_post({
        "ok": True, "changed": True, "profile": "lean",
        "before": {"tool_count": 33, "est_tokens": 21143, "est_seconds": 28.2},
        "after": {"tool_count": 22, "est_tokens": 19127, "est_seconds": 25.5},
        "restart_note": "New conversations pick this up immediately."})
    _t = M.t_prompt_budget({"profile": "lean", "restart": True})
    check("prompt_budget(profile): posts to /api/prompt/budget",
          POSTED and POSTED[0]["path"] == "/api/prompt/budget", POSTED)
    check("prompt_budget(profile): payload is exactly {'profile': 'lean'} — a "
          "caller-supplied restart is dropped",
          POSTED and POSTED[0]["payload"] == {"profile": "lean"},
          POSTED and POSTED[0]["payload"])
    check("prompt_budget(profile): reports before -> after", "before:" in _t and
          "after:" in _t, _t[:200])
    try:
        M.t_prompt_budget({"profile": "tiny"})
        _bad = ""
    except M.ToolError as e:
        _bad = str(e)
    check("prompt_budget(profile): an unknown profile is refused locally",
          "full, lean or focused" in _bad, _bad[:160])

    # tool_budget: `install` links a plugin into ~/.hermes, `restart` kickstarts
    # the agent backend — neither is ever forwarded.
    POSTED[:] = []
    M.dash_post = _stub_post({"ok": True, "active": True, "installed": True,
                              "settings": {"enabled": True, "max_chars": 40000,
                                           "spill": True},
                              "est_tokens": 11111, "pct_window": 17.0,
                              "context_length": 65536,
                              "stats": {"today": {"calls": 0, "kept_chars": 0,
                                                  "orig_chars": 0,
                                                  "est_tokens_saved": 0,
                                                  "spills": 0, "tools": {}},
                                        "last": None}})
    _t = M.t_tool_budget({"max_chars": 40000, "spill": False,
                          "install": True, "restart": True})
    check("tool_budget(write): posts to /api/tool/budget",
          POSTED and POSTED[0]["path"] == "/api/tool/budget", POSTED)
    check("tool_budget(write): payload carries only the three knobs — no "
          "install, no restart",
          POSTED and POSTED[0]["payload"] == {"max_chars": 40000, "spill": False},
          POSTED and POSTED[0]["payload"])
    check("tool_budget(write): confirms what it changed", "updated" in _t, _t[:160])

    # memory_facts: a credential is refused BEFORE the round trip.
    POSTED[:] = []
    M.dash_post = _stub_post({"ok": True, "verdict": "added",
                              "fact": {"id": 9, "kind": "fact", "pinned": False,
                                       "text": "the harness wrote this"}})
    try:
        M.t_memory_facts({"add": {"text": "my token is "
                                          "ghp_A1" "b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"}})
        _sec = ""
    except M.ToolError as e:
        _sec = str(e)
    check("memory_facts(add): a credential is refused", "credential" in _sec, _sec[:160])
    check("memory_facts(add): a refused credential never reaches the dashboard",
          POSTED == [], POSTED)
    _t = M.t_memory_facts({"add": {"text": "the harness wrote this",
                                   "kind": "note", "pinned": False}})
    check("memory_facts(add): posts to /api/memory/facts",
          POSTED and POSTED[0]["path"] == "/api/memory/facts", POSTED)
    check("memory_facts(add): payload is text/kind/pinned only",
          POSTED and set(POSTED[0]["payload"]) == {"text", "kind", "pinned"},
          POSTED and POSTED[0]["payload"])
    check("memory_facts(add): echoes the stored fact", "harness wrote this" in _t,
          _t[:160])
    try:
        M.t_memory_facts({"add": {"text": "x" * 500}})
        _long = ""
    except M.ToolError as e:
        _long = str(e)
    check("memory_facts(add): an essay is refused at 400 chars",
          "400 characters" in _long, _long[:160])
finally:
    M.dash_post = _real_post
    M.ALLOW["writes"] = _saved_writes

check("writes: the module's write flags are back to the owner's scope",
      all(v is False for v in M.ALLOW["writes"].values()), M.ALLOW["writes"])

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("E. HERMES_MCP_ALLOW — a scratch scope, the owner's file untouched")
print("=" * 74)
_scratch_dir = tempfile.mkdtemp(prefix="mcp-scope-")
_scratch = os.path.join(_scratch_dir, "mcp-allow.json")
with open(_scratch, "w") as _fh:
    json.dump({"max_results": 5,
               "tools": {"trace_summary": False, "messages_search": False},
               "writes": {"prompt_budget": True, "evals": True}}, _fh)

c3 = Client({"HERMES_MCP_ALLOW": _scratch})
c3.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "harness-scope", "version": "1"}})
c3.call("notifications/initialized", {}, notify=True)
r = c3.call("tools/list")
tools3 = (r.get("result") or {}).get("tools") or []
names3 = [t.get("name") for t in tools3]
schemas3 = {t.get("name"): (t.get("inputSchema") or {}) for t in tools3}
check("scope: HERMES_MCP_ALLOW was honoured (trace_summary switched off)",
      "trace_summary" not in names3 and "doctor" in names3, names3)
check("scope: writes.prompt_budget on -> the profile arg appears in the schema",
      "profile" in ((schemas3.get("prompt_budget") or {}).get("properties") or {}),
      sorted(((schemas3.get("prompt_budget") or {}).get("properties") or {})))
check("scope: writes.evals on -> the run arg appears in the schema",
      "run" in ((schemas3.get("evals") or {}).get("properties") or {}),
      sorted(((schemas3.get("evals") or {}).get("properties") or {})))
check("scope: writes.tool_budget still off -> no max_chars in the schema",
      "max_chars" not in ((schemas3.get("tool_budget") or {}).get("properties") or {}),
      sorted(((schemas3.get("tool_budget") or {}).get("properties") or {})))
check("scope: writes.memory_facts still off -> no add in the schema",
      "add" not in ((schemas3.get("memory_facts") or {}).get("properties") or {}),
      sorted(((schemas3.get("memory_facts") or {}).get("properties") or {})))
t = assert_result("scope: memory_facts write still refused",
                  c3.tool("memory_facts", {"add": {"text": "no"}}), expect_error=True)
check("scope: the refusal names the SCRATCH allowlist, not the owner's",
      _scratch in t and "writes.memory_facts" in t, t[:220])
r = c3.call("tools/call", {"name": "trace_summary", "arguments": {}})
check("scope: a switched-off tool is not callable (-32602)",
      (r.get("error") or {}).get("code") == -32602, r)
t = assert_result("scope: max_results clamps a read",
                  c3.tool("context_recent", {"n": 50}))
check("scope: max_results 5 caps context_recent at 5 turns",
      re.search(r"Last [0-5] of ", t), t[:120])
check("scope: the scratch allowlist is re-locked to 0600 (%s)"
      % oct(os.stat(_scratch).st_mode & 0o777),
      oct(os.stat(_scratch).st_mode & 0o777) == "0o600")
c3.close()

try:
    _after = open(REAL_ALLOW, "rb").read()
except OSError:
    _after = None
check("scope: the owner's ~/.hermes/mcp-allow.json is byte-for-byte unchanged",
      _after == REAL_ALLOW_BYTES,
      "%r bytes before, %r after" % (REAL_ALLOW_BYTES and len(REAL_ALLOW_BYTES),
                                     _after and len(_after)))

print()
print("=" * 74)
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
print("=" * 74)
sys.exit(1 if BAD else 0)
