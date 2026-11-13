#!/usr/bin/env python3
"""F21 — MCP tool annotations, outputSchema + structuredContent, version check.

docs/plans/feature-research-2026-10-21.md F21: every tool carries annotations
(readOnlyHint / idempotentHint / openWorldHint:false, destructiveHint where a
write is enabled) and a title; hermes_search, calendar_next and needs_you
declare an outputSchema and every SUCCESSFUL call returns structuredContent
that validates against it (a strict client rejects a mismatch), while every
error is isError:true with no structuredContent; 2025-11-25 is negotiated, the
stateless 2026-07-28 is not, and a client that negotiated an older revision is
offered only what that revision has.

Two halves, both against the REAL dashboard/hermes_mcp.py:
  * stdio: the server as a subprocess against a FAKE dashboard this test runs
    on an ephemeral loopback port (nothing else is contacted);
  * in process: dash_get stubbed with hostile and oversized payloads.
jsonschema is not available, so a small stdlib validator covers exactly the
keywords the schemas use — and fails on any keyword it does not know.
"""
import io
import contextlib
import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
SERVER = os.path.join(DASH, "hermes_mcp.py")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:400]) if detail and not cond else ""))


# ---------------------------------------------------------------------------
# a deliberately small JSON-schema validator
# ---------------------------------------------------------------------------
_KNOWN = {"type", "properties", "required", "additionalProperties", "items",
          "maxItems", "maxLength", "minimum", "maximum", "enum", "description"}


def validate(inst, schema, path="$"):
    errs = []
    unknown = set(schema) - _KNOWN
    if unknown:
        return ["%s: schema uses keywords this validator does not know: %s"
                % (path, sorted(unknown))]
    t = schema.get("type")
    if t:
        ok = {"object": isinstance(inst, dict), "array": isinstance(inst, list),
              "string": isinstance(inst, str), "boolean": isinstance(inst, bool),
              "integer": isinstance(inst, int) and not isinstance(inst, bool),
              "number": isinstance(inst, (int, float)) and not isinstance(inst, bool)
              }.get(t)
        if not ok:
            return ["%s: %r is not %s" % (path, inst, t)]
    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s: %r not in enum" % (path, inst))
    if isinstance(inst, str) and len(inst) > schema.get("maxLength", 1 << 30):
        errs.append("%s: %d chars > maxLength %d" % (path, len(inst), schema["maxLength"]))
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if inst != inst or inst in (float("inf"), float("-inf")):
            errs.append("%s: non-finite number" % path)
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append("%s: %r < minimum" % (path, inst))
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append("%s: %r > maximum" % (path, inst))
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append("%s: missing required %r" % (path, k))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], path + "." + k)
            elif extra is False:
                errs.append("%s: unexpected property %r" % (path, k))
            elif isinstance(extra, dict):
                errs += validate(v, extra, path + "." + k)
    if isinstance(inst, list):
        if len(inst) > schema.get("maxItems", 1 << 30):
            errs.append("%s: %d items > maxItems %d" % (path, len(inst), schema["maxItems"]))
        if "items" in schema:
            for i, v in enumerate(inst):
                errs += validate(v, schema["items"], "%s[%d]" % (path, i))
    return errs


def keywords(schema):
    out = set(schema)
    for sub in (schema.get("properties") or {}).values():
        out |= keywords(sub)
    for k in ("items", "additionalProperties"):
        if isinstance(schema.get(k), dict):
            out |= keywords(schema[k])
    return out


# ---------------------------------------------------------------------------
# a fake dashboard (loopback, ephemeral port)
# ---------------------------------------------------------------------------
TODAY = datetime.date.today()
# two hours from now on TODAY's date, clamped to 23:59 (after 22:00, now + 2h
# would be tomorrow listed under today, i.e. in the past, and be dropped)
_NOW = datetime.datetime.now()
SOON = min(_NOW + datetime.timedelta(hours=2),
           _NOW.replace(hour=23, minute=59, second=0, microsecond=0)).strftime("%-I:%M %p")
FAKE = {
    "/api/search": {"ok": True, "q": "lease", "sources": {"note": 1, "chat": 1, "message": 3},
                    "results": [
                        {"source": "note", "title": "Lease checklist", "ts": 1.79e9,
                         "snippet": "sign it", "ref": ""},
                        {"source": "chat", "title": "Moving", "ts": 1.79e9,
                         "snippet": "the lease", "ref": "chat-2026-10-01-abcd"},
                        {"source": "message", "title": "Alex", "snippet": "lease by 5"}]},
    "/api/expand": {"available": True, "days": [
        {"date": TODAY.isoformat(), "events": [
            {"title": "Dentist", "time": SOON, "end": ""},
            {"title": "Holiday", "all_day": True}]}]},
    "/api/needsyou": {"ok": True, "now": [
        {"source": "calendar", "sender": "Dentist", "summary": "Dentist soon",
         "reason": "event in <2 h", "confidence": 0.9}],
        "today": [], "later": [], "today_count": 0, "later_count": 0, "never_count": 2,
        "sources": {"calendar": "present", "message": "absent"},
        "source_notes": {"message": "Full Disk Access needed"},
        "metrics": {"now_precision": 0.75}},
}


class FakeDash(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(FAKE.get(self.path.split("?", 1)[0], {"ok": False})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeDash)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
FAKE_URL = "http://127.0.0.1:%d" % httpd.server_address[1]

TMP = tempfile.mkdtemp(prefix="hermes-f21-")


class Client(object):
    def __init__(self, allow, version):
        env = dict(os.environ, HERMES_MCP_ALLOW=allow, HERMES_MCP_DASHBOARD=FAKE_URL)
        self.p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, bufsize=0)
        self.n = 0
        self.init = self.call("initialize", {"protocolVersion": version, "capabilities": {},
                                             "clientInfo": {"name": "f21", "version": "1"}})
        self.p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                        "params": params or {}}) + "\n").encode())
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(self.p.stderr.read().decode("utf-8", "replace")[-500:])
        return json.loads(line.decode("utf-8"))

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def allow_file(name, obj):
    p = os.path.join(TMP, name)
    with open(p, "w") as fh:
        json.dump(obj, fh)
    return p


DEFAULT = allow_file("default.json", {})
WRITES = allow_file("writes.json", {"writes": {"prompt_budget": True, "tool_budget": True,
                                               "memory_facts": True, "evals": True}})
STRUCTURED = ("hermes_search", "calendar_next", "needs_you")
CALLS = {"hermes_search": {"q": "lease"}, "calendar_next": {"hours": 24}, "needs_you": {}}

# ---------------------------------------------------------------------------
print("--- protocol negotiation")
for want, got in (("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"),
                  ("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"),
                  ("2026-07-28", "2025-11-25"), ("1999-01-01", "2025-11-25"),
                  (None, "2025-11-25"), (["2025-06-18"], "2025-11-25")):
    c = Client(DEFAULT, want)
    v = (c.init.get("result") or {}).get("protocolVersion")
    check("client asks %r -> server answers %r" % (want, got), v == got, c.init)
    c.close()

# ---------------------------------------------------------------------------
print("--- tools/list on the latest revision: annotations + outputSchema")
c = Client(DEFAULT, "2025-11-25")
tools = (c.call("tools/list").get("result") or {}).get("tools") or []
by = dict((t["name"], t) for t in tools)
check("15 tools listed", len(tools) == 15, sorted(by))
for t in tools:
    a = t.get("annotations") or {}
    check("%s: openWorldHint is false" % t["name"], a.get("openWorldHint") is False, a)
    check("%s: read-only by default (readOnlyHint, idempotentHint)" % t["name"],
          a.get("readOnlyHint") is True and a.get("idempotentHint") is True, a)
    check("%s: titled, in the tool and its annotations" % t["name"],
          bool(t.get("title")) and a.get("title") == t.get("title"), (t.get("title"), a))
    check("%s: outputSchema only where promised" % t["name"],
          ("outputSchema" in t) == (t["name"] in STRUCTURED), sorted(t))
for name in STRUCTURED:
    sch = by[name]["outputSchema"]
    check("%s: outputSchema is an object schema" % name, sch.get("type") == "object", sch)
    check("%s: outputSchema uses only keywords this validator checks" % name,
          keywords(sch) <= _KNOWN, sorted(keywords(sch) - _KNOWN))

print("--- tools/call over stdio: structuredContent validates against tools/list")
for name in STRUCTURED:
    r = c.call("tools/call", {"name": name, "arguments": CALLS[name]})
    res = r.get("result") or {}
    sc = res.get("structuredContent")
    check("%s: isError false" % name, res.get("isError") is False, res)
    check("%s: text content kept as the fallback" % name,
          (res.get("content") or [{}])[0].get("type") == "text" and
          bool((res.get("content") or [{}])[0].get("text")), res)
    check("%s: carries structuredContent" % name, isinstance(sc, dict), res)
    errs = validate(sc, by[name]["outputSchema"]) if isinstance(sc, dict) else ["none"]
    check("%s: structuredContent validates" % name, not errs, errs[:5])
sc = (c.call("tools/call", {"name": "hermes_search", "arguments": {"q": "lease"}})
      .get("result") or {}).get("structuredContent") or {}
check("hermes_search: message rows and counts stay out (messages_search off)",
      [r["source"] for r in sc.get("results", [])] == ["note", "chat"] and
      "message" not in sc.get("matches_by_source", {}), sc)
check("hermes_search: chat hit carries its session id",
      sc.get("results", [{}, {}])[1].get("session") == "chat-2026-10-01-abcd", sc)
sc = (c.call("tools/call", {"name": "calendar_next", "arguments": {}})
      .get("result") or {}).get("structuredContent") or {}
check("calendar_next: both events, all-day flagged",
      sorted((e["title"], e["all_day"]) for e in sc.get("events", [])) ==
      [("Dentist", False), ("Holiday", True)], sc)
sc = (c.call("tools/call", {"name": "needs_you", "arguments": {}})
      .get("result") or {}).get("structuredContent") or {}
check("needs_you: one now item, counts without never, messages hidden",
      len(sc.get("now", [])) == 1 and sc.get("messages_hidden") is True and
      "never" not in sc.get("counts", {}), sc)
check("needs_you: the Messages note stays private while messages are off",
      "unread_sources" not in sc, sc)
r = c.call("tools/call", {"name": "doctor", "arguments": {}})
check("a tool without an outputSchema never sends structuredContent",
      "structuredContent" not in (r.get("result") or {}), r)
c.close()

print("--- an older revision is offered only what it has")
for version, ann, out in (("2025-06-18", True, True), ("2025-03-26", True, False),
                          ("2024-11-05", False, False)):
    c = Client(DEFAULT, version)
    tools = (c.call("tools/list").get("result") or {}).get("tools") or []
    check("%s: annotations %s" % (version, "offered" if ann else "withheld"),
          all(("annotations" in t) == ann for t in tools))
    check("%s: outputSchema %s" % (version, "offered" if out else "withheld"),
          all(("outputSchema" in t) == (out and t["name"] in STRUCTURED) for t in tools))
    r = c.call("tools/call", {"name": "needs_you", "arguments": {}})
    check("%s: structuredContent %s" % (version, "sent" if out else "withheld"),
          ("structuredContent" in (r.get("result") or {})) == out, r)
    c.close()

print("--- an enabled write changes the hints, and only for that tool")
c = Client(WRITES, "2025-11-25")
by = dict((t["name"], t) for t in (c.call("tools/list").get("result") or {}).get("tools") or [])
for name, destructive, idempotent in (("prompt_budget", True, True),
                                      ("tool_budget", True, True),
                                      ("memory_facts", False, False),
                                      ("evals", False, False)):
    a = by[name].get("annotations") or {}
    check("%s (write on): readOnlyHint false, destructiveHint %s, idempotentHint %s"
          % (name, destructive, idempotent),
          a.get("readOnlyHint") is False and a.get("destructiveHint") is destructive
          and a.get("idempotentHint") is idempotent and a.get("openWorldHint") is False, a)
check("hermes_search stays read-only", by["hermes_search"]["annotations"]["readOnlyHint"])
c.close()
httpd.shutdown()

# ---------------------------------------------------------------------------
print("--- in process: hostile payloads still validate; errors carry none")
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "inproc.json")
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402
SCHEMA = dict((t["name"], t["outputSchema"]) for t in M.TOOLS if t.get("outputSchema"))
STATE = {}
M.dash_get = lambda path, params=None, timeout=None: json.loads(json.dumps(STATE[path]))

LONG = "x" * 5000
CASES = [
    ("search: empty", "/api/search", {"ok": True, "results": [], "sources": {}},
     "hermes_search"),
    ("search: garbage rows and counts", "/api/search",
     {"ok": True, "results": [None, 7, {"source": LONG, "title": LONG, "ts": "x",
                                        "snippet": LONG, "ref": LONG}],
      "sources": {"note": "3", "chat": True, "calendar": -2, LONG: 5}},
     "hermes_search"),
    ("search: 50 huge rows", "/api/search",
     {"ok": True, "results": [{"source": "chat", "title": LONG, "snippet": LONG,
                               "ref": LONG, "ts": 1.79e9}] * 50, "sources": {"chat": 50}},
     "hermes_search"),
    # review: an upstream that answers MORE rows than asked must not break the
    # schema's maxItems (the text may list them; the structured copy may not)
    ("search: 80 rows, more than maxItems", "/api/search",
     {"ok": True, "results": [{"source": "note", "title": "t%d" % i, "snippet": "s"}
                              for i in range(80)], "sources": {"note": 80}},
     "hermes_search"),
    ("calendar: nothing", "/api/expand", {"available": True, "days": []}, "calendar_next"),
    ("calendar: garbage", "/api/expand",
     {"available": True, "days": [None, {"date": "nope", "events": [{"title": "a"}]},
                                  {"date": TODAY.isoformat(),
                                   "events": [{"title": LONG, "time": SOON, "end": LONG},
                                              {"title": "", "time": SOON}, 3]}]},
     "calendar_next"),
    ("calendar: 400 all-day events", "/api/expand",
     {"available": True, "days": [{"date": TODAY.isoformat(), "events": [
         {"title": "e%d %s" % (i, "y" * 130), "all_day": True} for i in range(400)]}]},
     "calendar_next"),
    ("needs_you: building", "/api/needsyou", {"ok": True, "building": True}, "needs_you"),
    ("needs_you: garbage", "/api/needsyou",
     {"ok": True, "now": [None, {"source": LONG, "sender": LONG, "summary": LONG,
                                 "reason": LONG, "confidence": float("nan"),
                                 "deadline_ts": 1.79e9}],
      "today": "nope", "later": [{"confidence": True}], "today_count": "x",
      "sources": {LONG: LONG}, "source_notes": {"reminder": LONG, "x": None},
      "metrics": {"now_precision": float("inf")}},
     "needs_you"),
    ("needs_you: 40 huge items per bucket", "/api/needsyou",
     {"ok": True, "now": [{"source": "calendar", "sender": LONG, "summary": LONG,
                           "reason": LONG, "confidence": 0.5}] * 40,
      "today": [{"source": "watchtower", "sender": LONG, "summary": LONG}] * 40,
      "later": [{"source": "reminder", "sender": LONG, "summary": LONG}] * 40,
      "today_count": 90, "later_count": 40, "never_count": 3}, "needs_you"),
    # review: an int past a double's range made _finite() raise OverflowError
    ("needs_you: numbers past a float", "/api/needsyou",
     {"ok": True, "now": [{"source": "calendar", "sender": "a", "summary": "b",
                           "reason": "r", "confidence": 2 ** 1100}],
      "today": [], "later": [], "metrics": {"now_precision": 2 ** 1100}}, "needs_you"),
]
M.ALLOW["max_results"] = 50
for label, path, payload, tool in CASES:
    STATE[path] = payload
    for msgs in (False, True):
        M.ALLOW["tools"]["messages_search"] = msgs
        r = M.do_tools_call({"name": tool, "arguments": CALLS[tool]})
        sc = r.get("structuredContent")
        tag = "%s (messages %s)" % (label, "on" if msgs else "off")
        check(tag + ": answered", r.get("isError") is False, r["content"][0]["text"][:200])
        errs = validate(sc, SCHEMA[tool]) if isinstance(sc, dict) else ["no structuredContent"]
        check(tag + ": validates", not errs, errs[:4])
        try:
            # measured the way _send() writes it: UTF-8, ensure_ascii=False
            n = len(json.dumps(sc, allow_nan=False, ensure_ascii=False).encode("utf-8"))
        except ValueError as e:
            n = "not JSON: %s" % e
        check(tag + ": strict JSON within MAX_STRUCT (%s bytes)" % n,
              isinstance(n, int) and n <= M.MAX_STRUCT)
M.ALLOW["tools"]["messages_search"] = False

for label, path, payload, tool in (
        ("search: ok false", "/api/search", {"ok": False, "error": "index busy"},
         "hermes_search"),
        ("calendar: unavailable", "/api/expand", {"available": False, "reason": "no icalBuddy"},
         "calendar_next"),
        ("needs_you: ok false", "/api/needsyou", {"ok": False, "error": "boom"}, "needs_you"),
        ("needs_you: not an object", "/api/needsyou", [1, 2], "needs_you")):
    STATE[path] = payload
    r = M.do_tools_call({"name": tool, "arguments": CALLS[tool]})
    check(label + ": isError with no structuredContent",
          r.get("isError") is True and "structuredContent" not in r, r)
r = M.do_tools_call({"name": "hermes_search", "arguments": {"q": ""}})
check("a bad argument: isError with no structuredContent",
      r.get("isError") is True and "structuredContent" not in r, r)


def down(path, params=None, timeout=None):
    raise M.ToolError("The Hermes dashboard is not answering")


M.dash_get = down
for tool in STRUCTURED:
    r = M.do_tools_call({"name": tool, "arguments": CALLS[tool]})
    check("%s with the dashboard down: isError, no structuredContent" % tool,
          r.get("isError") is True and "structuredContent" not in r, r)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
