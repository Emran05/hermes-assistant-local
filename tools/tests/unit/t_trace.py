"""Unit test for dashboard/aux_trace.py assembly.

Builds a scratch HOME with a synthetic metrics JSONL, a temp Recorder DB and a
tool-budget JSONL, execs aux_convos.py (for the real _cv_redact) + aux_trace.py
into a stub server.py globals dict, and asserts on the two output formats.

Never starts a model, never touches the real ~/.hermes.
"""
import datetime, json, os, re, shutil, sqlite3, sys, tempfile, time, urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
CHECKS = [0]

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (CHECKS[0] - len(FAILS), len(FAILS))))


def ck(name, cond, extra=""):
    CHECKS[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------
# scratch HOME
# --------------------------------------------------------------------------
HOME = tempfile.mkdtemp(prefix="hermes-trace-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
MET = os.path.join(HOME, ".hermes", "metrics")
CHATS = os.path.join(DATA, "chats")
for d in (DATA, MET, CHATS, os.path.join(DATA, "spill")):
    os.makedirs(d, exist_ok=True)

SECRET = "sk-ant-api03-DEADBEEFdeadbeef0123456789ABCDEFabcdef"
TG_SECRET = "1234567890:AAHfakefakefakefakefakefakefakefake1"

# A fixed local noon today, so day bucketing is unambiguous.
TODAY = datetime.date.today()
NOON = datetime.datetime.combine(TODAY, datetime.time(12, 0, 0)).timestamp()

# turn A: 12:00:00 -> 12:00:04    turn B: 12:01:00 -> 12:01:10
A_START, A_END = NOON, NOON + 4.0
B_START, B_END = NOON + 60.0, NOON + 70.0
# an orphan cluster (Telegram) at 12:10, and a second one 10 min later
O1 = NOON + 600.0
O2 = NOON + 1200.0

met_path = os.path.join(MET, "metrics-%04d-%02d-%02d.jsonl" % (TODAY.year, TODAY.month, TODAY.day))
with open(met_path, "w") as f:
    f.write(json.dumps({"ts": NOON - 5, "kind": "ram", "gb": 3.1}) + "\n")
    f.write(json.dumps({"ts": A_END, "kind": "turn", "job": "aaaaaaaaaaaa",
                        "ttft_ms": 900, "setup_ms": 40, "serve_ttft_ms": 860,
                        "turn_ms": 4000, "decode_ms": 3100, "est_tokens_out": 120,
                        "est_tok_per_sec": 38.7, "path": "serve", "ok": True,
                        "ttft_clean": True, "approvals": 1, "approved": 1,
                        "denied": 0, "model": "mlx-community/Qwen3.8-27B-4bit"}) + "\n")
    f.write(json.dumps({"ts": B_END, "kind": "turn", "job": "bbbbbbbbbbbb",
                        "ttft_ms": 1500, "setup_ms": 50, "serve_ttft_ms": 1450,
                        "turn_ms": 10000, "decode_ms": 8000, "est_tokens_out": 400,
                        "est_tok_per_sec": 50.0, "path": "serve", "ok": False,
                        "ttft_clean": True, "approvals": 0, "approved": 0,
                        "denied": 0, "model": "mlx-community/Qwen3.8-27B-4bit"}) + "\n")
    f.write(json.dumps({"ts": B_END, "kind": "count", "name": "turns", "n": 1}) + "\n")

# chat store: both turns belong to ONE conversation
with open(os.path.join(CHATS, "chat-2099-01-01-test.json"), "w") as f:
    json.dump({"title": "trace test", "messages": [
        {"role": "user", "text": "first", "ts": A_START - 0.05},
        {"role": "bot", "text": "ok", "ts": A_END},
        {"role": "user", "text": "second", "ts": B_START - 0.02},
        {"role": "bot", "text": "no", "ts": B_END}]}, f)

# recorder db
REC = os.path.join(DATA, "recorder.db")
con = sqlite3.connect(REC)
con.executescript("""
CREATE TABLE actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tool_call_id TEXT UNIQUE, ts REAL NOT NULL,
  session TEXT DEFAULT '', source TEXT DEFAULT '', tool TEXT NOT NULL,
  args TEXT DEFAULT '', target TEXT DEFAULT '', kind TEXT NOT NULL,
  reversible TEXT NOT NULL, status TEXT NOT NULL, duration_s REAL,
  summary TEXT DEFAULT '', snapshot_ref TEXT DEFAULT '', after_state TEXT DEFAULT '',
  undone_ts REAL, undo_note TEXT DEFAULT '', origin TEXT DEFAULT '');
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
""")


def act(tcid, ts, session, source, tool, args, target, kind, rev, status, dur,
        summary, undone=None, origin="statedb"):
    con.execute("INSERT INTO actions(tool_call_id,ts,session,source,tool,args,target,"
                "kind,reversible,status,duration_s,summary,undone_ts,origin) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tcid, ts, session, source, tool, args, target, kind, rev, status,
                 dur, summary, undone, origin))


# inside turn A
act("a1", A_START + 0.5, "hsess1", "hub", "web_search",
    json.dumps({"query": "curl -H 'Authorization: Bearer " + SECRET + "'"}),
    "curl -H 'Authorization: Bearer " + SECRET + "'", "net", "n/a", "done", 1.2,
    "Did 5 searches; key " + SECRET + " leaked into the summary too")
act("a2", A_START + 2.0, "hsess1", "hub", "write_file",
    json.dumps({"path": "/tmp/x"}), "/tmp/x", "write", "yes", "done", 0.1,
    "wrote 20 lines", undone=A_END + 100)
# inside turn B
act("b1", B_START + 3.0, "hsess1", "hub", "terminal",
    json.dumps({"command": "echo hi"}), "echo hi", "shell", "no", "error", 0.4,
    "exit 1")
# orphan cluster 1 (telegram) — no metrics row exists for it
act("o1", O1, "tg-sess", "telegram", "web_search", "{}", "weather", "net", "n/a", "done", 0.9, "5 results")
act("o2", O1 + 3.0, "tg-sess", "telegram", "read_file", "{}", "notes.md", "read", "n/a", "done", 0.05, "read 3kb")
# orphan cluster 2 — same session, 10 minutes later, must be a SECOND synthetic turn
act("o3", O2, "tg-sess", "telegram", "terminal", "{}", "ls", "shell", "no", "done", 0.2, "ok")
con.commit()
con.close()

# tool-budget: one truncation inside turn B, one outside the export range
with open(os.path.join(DATA, "tool-budget.jsonl"), "w") as f:
    f.write(json.dumps({
        "ts": datetime.datetime.fromtimestamp(B_START + 4.0).isoformat(timespec="seconds"),
        "tool": "terminal", "orig_chars": 90000, "kept_chars": 24000,
        "omitted_chars": 66000, "est_tokens_saved": 18333,
        "spill_path": os.path.join(DATA, "spill", "terminal-x.txt"),
        "session": "hsess1"}) + "\n")
    f.write(json.dumps({
        "ts": datetime.datetime.fromtimestamp(NOON - 7 * 86400).isoformat(timespec="seconds"),
        "tool": "read_file", "orig_chars": 5, "kept_chars": 4, "omitted_chars": 1,
        "est_tokens_saved": 0, "spill_path": "", "session": ""}) + "\n")


# --------------------------------------------------------------------------
# stub server.py globals
# --------------------------------------------------------------------------
src = open(os.path.join(DASH, "server.py")).read()
lo = src.index("GET_ROUTES = {}")
hi = src.index("# Rich per-widget providers")
PLUMBING = src[lo:hi]

G = {"__builtins__": __builtins__, "os": os, "re": re, "json": json, "time": time,
     "sqlite3": sqlite3, "urllib": urllib, "shutil": shutil, "sys": sys,
     "HOME": HOME, "DATA": DATA, "HERE": DASH, "CHATS": CHATS,
     "PREWARM_SESSION": "__prewarm__",
     "SESSION_RE": re.compile(r"^[A-Za-z0-9._-]{1,80}$"),
     "CHAT_JOBS": {}}
G["chat_path"] = lambda s: os.path.join(CHATS, s + ".json")
G["load_chat"] = lambda s: json.load(open(G["chat_path"](s)))
G["save_chat"] = lambda s, c: json.dump(c, open(G["chat_path"](s), "w"))
exec(compile(PLUMBING, "server-plumbing", "exec"), G)
exec(compile(open(os.path.join(DASH, "aux_convos.py")).read(), "aux_convos.py", "exec"), G)
exec(compile(open(os.path.join(DASH, "aux_trace.py")).read(), "aux_trace.py", "exec"), G)

RouteCtx = G["RouteCtx"]
export = G["GET_ROUTES"]["/api/trace/export"]
summary = G["GET_ROUTES"]["/api/trace/summary"]
SINCE, UNTIL = NOON - 3600, NOON + 3600


def ctx(**kw):
    return RouteCtx(query={k: [str(v)] for k, v in kw.items()})


# --------------------------------------------------------------------------
# 1. JSONL
# --------------------------------------------------------------------------
r = export(ctx(since=SINCE, until=UNTIL, format="jsonl"))
ck("jsonl: RawResponse", isinstance(r, G["RawResponse"]), type(r))
body = r.body.decode()
rows = [json.loads(x) for x in body.splitlines()]
cd = r.headers.get("Content-Disposition", "")
stamp = TODAY.strftime("%Y%m%d")
ck("jsonl: content-type ndjson", r.content_type.startswith("application/x-ndjson"), r.content_type)
ck("jsonl: filename", ('filename="hermes-trace-%s-%s.jsonl"' % (stamp, stamp)) in cd, cd)

spans = [x for x in rows if x["type"] == "span"]
traces = [x for x in rows if x["type"] == "trace"]
events = [x for x in rows if x["type"] == "event"]
turns = [x for x in spans if x["kind"] == "turn"]
tools = [x for x in spans if x["kind"] == "tool"]
by_id = {x["span_id"]: x for x in spans}

ck("jsonl: meta line first", rows[0]["type"] == "meta" and rows[0]["service"] == "hermes-assistant")
ck("jsonl: 2 real + 2 synthetic turns", len(turns) == 4, [t["name"] for t in turns])
ck("jsonl: 6 tool spans", len(tools) == 6, len(tools))
ck("jsonl: 1 event (the out-of-range one is dropped)", len(events) == 1, events)

# --- parent/child nesting by window ---------------------------------------
def parent_of(tool_name):
    for t in tools:
        if t["gen_ai.tool.name"] == tool_name:
            return by_id.get(t["parent_span_id"])
    return None


pa = parent_of("web_search")   # first match = a1, inside turn A
ck("nesting: a1 -> turn A", pa is not None and pa["hermes.job"] == "aaaaaaaaaaaa",
   pa and pa.get("hermes.job"))
pb = parent_of("terminal")
ck("nesting: b1 -> turn B", pb is not None and pb.get("hermes.job") == "bbbbbbbbbbbb",
   pb and pb.get("hermes.job"))
ck("nesting: every tool span has a parent that is a turn span",
   all(by_id.get(t["parent_span_id"], {}).get("kind") == "turn" for t in tools))
ck("nesting: every child sits inside its parent's window",
   all(by_id[t["parent_span_id"]]["start"] - 1.01 <= t["start"]
       <= by_id[t["parent_span_id"]]["end"] + 1.01 for t in tools))
ck("nesting: turn spans have no parent", all(t["parent_span_id"] is None for t in turns))

# --- one trace per conversation per day -----------------------------------
real = [t for t in turns if t["hermes.turn.synthetic"] is False]
ck("trace: both dashboard turns share one trace id",
   len(real) == 2 and real[0]["trace_id"] == real[1]["trace_id"])
ck("trace: session resolved from the chat store",
   all(t.get("hermes.session") == "chat-2099-01-01-test" for t in real),
   [t.get("hermes.session") for t in real])
syn = [t for t in turns if t["hermes.turn.synthetic"] is True]
ck("trace: 2 synthetic turns (10-min gap splits the cluster)", len(syn) == 2, len(syn))
ck("trace: synthetic turns share the recorder-session trace id",
   len(syn) == 2 and syn[0]["trace_id"] == syn[1]["trace_id"])
ck("trace: synthetic trace != dashboard trace", syn[0]["trace_id"] != real[0]["trace_id"])
ck("trace: 2 trace roll-ups", len(traces) == 2, len(traces))
ck("trace: roll-up counts", sorted((t["turns"], t["tool_spans"]) for t in traces) ==
   [(2, 3), (2, 3)], sorted((t["turns"], t["tool_spans"]) for t in traces))

# --- event on the right span ----------------------------------------------
ev = events[0]
ck("event: attached to turn B", ev["span_id"] == pb["span_id"])
ck("event: name + fields", ev["name"] == "hermes.tool_budget.truncation"
   and ev["hermes.budget.omitted_chars"] == 66000 and ev["hermes.budget.spilled"] is True, ev)
ck("event: the spill PATH itself is never exported",
   "spill_path" not in ev and "terminal-x.txt" not in body and DATA not in body)

# --- enrichment / attribute names -----------------------------------------
ta = real[0]
ck("attrs: genai names present", ta["gen_ai.operation.name"] == "chat"
   and ta["gen_ai.request.model"].startswith("mlx-community/")
   and ta["gen_ai.usage.output_tokens"] == 120)
ck("attrs: estimated tokens flagged", ta["hermes.tokens_estimated"] is True)
ck("attrs: approvals carried", ta["hermes.approvals"] == 1 and ta["hermes.approved"] == 1)
ck("attrs: failed turn is status error",
   [t for t in real if t["hermes.job"] == "bbbbbbbbbbbb"][0]["status"] == "error")
undone = [t for t in tools if t["gen_ai.tool.name"] == "write_file"][0]
ck("attrs: undone tool", undone["hermes.tool.undone"] is True
   and undone["hermes.tool.reversible"] == "yes")

# --- redaction -------------------------------------------------------------
ck("redact: no sk-ant- key anywhere in the jsonl", SECRET not in body)
ck("redact: no bare 'sk-ant' prefix either", "sk-ant" not in body)
ck("redact: [redacted] present", "[redacted]" in body)
ck("redact: no args field", not any(k == "args" or k.endswith(".args") for x in rows for k in x))
leaky = [t for t in tools if t["gen_ai.tool.name"] == "web_search"][0]
ck("redact: target scrubbed", "[redacted]" in leaky["hermes.tool.target"], leaky["hermes.tool.target"])
ck("redact: summary scrubbed", "[redacted]" in leaky["hermes.tool.summary"], leaky["hermes.tool.summary"])

# --- ids -------------------------------------------------------------------
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")
ck("ids: trace ids are 32 lowercase hex", all(HEX32.match(s["trace_id"]) for s in spans))
ck("ids: span ids are 16 lowercase hex", all(HEX16.match(s["span_id"]) for s in spans))
ck("ids: no all-zero id", all(s["trace_id"].strip("0") and s["span_id"].strip("0") for s in spans))
again = export(ctx(since=SINCE, until=UNTIL, format="jsonl")).body.decode()
ck("ids: deterministic across runs",
   [json.loads(x) for x in again.splitlines() if '"meta"' not in x[:20]] ==
   [json.loads(x) for x in body.splitlines() if '"meta"' not in x[:20]])

# --------------------------------------------------------------------------
# 2. OTLP/JSON
# --------------------------------------------------------------------------
r2 = export(ctx(since=SINCE, until=UNTIL, format="otel"))
ob = r2.body.decode()
doc = json.loads(ob)
ck("otel: content-type json", r2.content_type.startswith("application/json"))
ck("otel: filename ext", 'filename="hermes-trace-%s-%s.otlp.json"' % (stamp, stamp)
   in r2.headers.get("Content-Disposition", ""))
rs = doc["resourceSpans"]
ck("otel: resourceSpans is a list", isinstance(rs, list) and len(rs) == 1, len(rs))
res_attrs = {a["key"]: a["value"] for a in rs[0]["resource"]["attributes"]}
ck("otel: service.name", res_attrs["service.name"]["stringValue"] == "hermes-assistant")
ck("otel: service.version is the VERSION file",
   res_attrs["service.version"]["stringValue"] == open(os.path.join(REPO, "VERSION")).read().strip(),
   res_attrs["service.version"])
osp = [s for r_ in rs for ss in r_["scopeSpans"] for s in ss["spans"]]
ck("otel: same span count as jsonl", len(osp) == len(spans), (len(osp), len(spans)))
ck("otel: scope name/version present",
   rs[0]["scopeSpans"][0]["scope"]["name"] == "hermes.dashboard.trace")

bad = []
for s in osp:
    if not HEX32.match(s["traceId"]):
        bad.append(("traceId", s["traceId"]))
    if not HEX16.match(s["spanId"]):
        bad.append(("spanId", s["spanId"]))
    if "parentSpanId" in s and not HEX16.match(s["parentSpanId"]):
        bad.append(("parentSpanId", s["parentSpanId"]))
    for k in ("startTimeUnixNano", "endTimeUnixNano"):
        if not isinstance(s[k], str) or not s[k].isdigit():
            bad.append((k, s[k]))
    if s["kind"] not in (1, 3):
        bad.append(("kind", s["kind"]))
    if s["status"]["code"] not in (0, 1, 2):
        bad.append(("status", s["status"]))
    for a in s["attributes"]:
        if set(a) != {"key", "value"} or len(a["value"]) != 1:
            bad.append(("attr", a))
        v = a["value"]
        if "intValue" in v and not isinstance(v["intValue"], str):
            bad.append(("intValue not a string", a))
        if "boolValue" in v and not isinstance(v["boolValue"], bool):
            bad.append(("boolValue", a))
        if "doubleValue" in v and not isinstance(v["doubleValue"], float):
            bad.append(("doubleValue", a))
    for e in s.get("events", []):
        if not isinstance(e["timeUnixNano"], str) or not e["timeUnixNano"].isdigit():
            bad.append(("event nanos", e))
ck("otel: OTLP/JSON shape valid for every span", not bad, bad[:4])
ck("otel: end >= start on every span",
   all(int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"]) for s in osp))
ck("otel: nanos match the jsonl epochs",
   sorted(int(s["startTimeUnixNano"]) for s in osp) ==
   sorted(int(round(s["start"] * 1e9)) for s in spans))
parents = {s["spanId"] for s in osp}
ck("otel: every parentSpanId resolves",
   all(s["parentSpanId"] in parents for s in osp if "parentSpanId" in s))
ck("otel: 4 root spans", sum(1 for s in osp if "parentSpanId" not in s) == 4)
ck("otel: redaction holds", SECRET not in ob and "sk-ant" not in ob)
ck("otel: one event carried", sum(len(s.get("events", [])) for s in osp) == 1)

# --------------------------------------------------------------------------
# 3. caps + validation
# --------------------------------------------------------------------------
res = export(ctx(since=NOON - 40 * 86400, until=NOON, format="jsonl"))
ck("cap: 40-day range refused with 400",
   isinstance(res, tuple) and res[1] == 400 and "31 days" in res[0]["error"], res)
res = export(ctx(since=NOON - 31 * 86400 + 60, until=NOON, format="jsonl"))
ck("cap: 31 days exactly is allowed", isinstance(res, G["RawResponse"]), res)
res = export(ctx(since=NOON, until=NOON - 10, format="jsonl"))
ck("cap: until<=since refused", isinstance(res, tuple) and res[1] == 400)
res = export(ctx(since="nope", until=UNTIL))
ck("cap: non-numeric since refused", isinstance(res, tuple) and res[1] == 400)
res = export(ctx(since=SINCE, until=UNTIL, format="csv"))
ck("cap: unknown format refused", isinstance(res, tuple) and res[1] == 400)

# scrubber missing -> refuse, never ship unscrubbed
saved = G.pop("_cv_redact")
res = export(ctx(since=SINCE, until=UNTIL))
ck("safety: export refuses without the scrubber",
   isinstance(res, tuple) and res[1] == 503, res)
G["_cv_redact"] = saved

# --------------------------------------------------------------------------
# 4. summary
# --------------------------------------------------------------------------
s = summary(ctx(since=SINCE, until=UNTIL))
ck("summary: counts", (s["traces"], s["turns"], s["tool_spans"], s["truncations"],
                       s["undone"]) == (2, 4, 6, 1, 1), s)
ck("summary: tokens out totalled", s["tokens_out"] == 520, s["tokens_out"])
ck("summary: estimated turns flagged", s["turns_estimated_tokens"] == 2)
ck("summary: tool errors", s["tool_errors"] == 1)
ck("summary: max_days", s["max_days"] == 31)
s2 = summary(ctx(since=NOON - 40 * 86400, until=NOON))
ck("summary: honours the 31-day cap", isinstance(s2, tuple) and s2[1] == 400)
s3 = summary(ctx(since=NOON + 7200, until=NOON + 10800))
ck("summary: empty range is zeroes, not an error",
   s3["ok"] and s3["turns"] == 0 and s3["traces"] == 0, s3)

# --------------------------------------------------------------------------
# 5. the span cap — cuts on a turn boundary and says so in BOTH shapes
#    (_TR_MAX_SPANS is a module global resolved at call time, so the test seam
#    is simply rebinding it in the exec'd namespace)
# --------------------------------------------------------------------------
REAL_CAP = G["_TR_MAX_SPANS"]
FULL_KIDS = {}
for t_ in tools:
    FULL_KIDS[t_["parent_span_id"]] = FULL_KIDS.get(t_["parent_span_id"], 0) + 1
ck("cap fixture: the day has 10 spans in 4 turns", len(spans) == 10 and len(turns) == 4,
   (len(spans), len(turns)))


def capped(n, fmt="jsonl"):
    G["_TR_MAX_SPANS"] = n
    try:
        return export(ctx(since=SINCE, until=UNTIL, format=fmt))
    finally:
        G["_TR_MAX_SPANS"] = REAL_CAP


r9 = capped(9)
rows9 = [json.loads(x) for x in r9.body.decode().splitlines()]
sp9 = [x for x in rows9 if x["type"] == "span"]
turns9 = [x for x in sp9 if x["kind"] == "turn"]
tools9 = [x for x in sp9 if x["kind"] == "tool"]
ids9 = set(x["span_id"] for x in sp9)
ck("cap: a cap of 9 stops at 8 — the 9th span would have orphaned a turn",
   len(sp9) == 8, len(sp9))
ck("cap: no exported tool span has a missing parent",
   all(t["parent_span_id"] in ids9 for t in tools9),
   [t["span_id"] for t in tools9 if t["parent_span_id"] not in ids9])
kids9 = {}
for t_ in tools9:
    kids9[t_["parent_span_id"]] = kids9.get(t_["parent_span_id"], 0) + 1
ck("cap: every exported turn kept ALL of its children",
   all(kids9.get(t["span_id"], 0) == FULL_KIDS.get(t["span_id"], 0) for t in turns9),
   [(t["span_id"], kids9.get(t["span_id"], 0), FULL_KIDS.get(t["span_id"], 0))
    for t in turns9])
ck("cap: 3 whole turns survive", len(turns9) == 3, len(turns9))
note = [x for x in rows9 if x["type"] == "note"]
ck("cap: the jsonl note line is still written",
   len(note) == 1 and note[0]["truncated"] is True and note[0]["max_spans"] == 9, note)
ck("cap: the roll-up of a trace with no spans is dropped",
   all(t["trace_id"] in set(x["trace_id"] for x in sp9)
       for t in rows9 if t["type"] == "trace"),
   [t["trace_id"] for t in rows9 if t["type"] == "trace"])
ck("cap: X-Hermes-Trace-Truncated header", r9.headers.get("X-Hermes-Trace-Truncated") == "1",
   r9.headers)
ck("cap: the header names the cap so the card need not hard-code it",
   r9.headers.get("X-Hermes-Trace-Span-Cap") == "9", r9.headers)

r4 = capped(4)
sp4 = [json.loads(x) for x in r4.body.decode().splitlines()]
sp4 = [x for x in sp4 if x["type"] == "span"]
ck("cap: a cap of 4 rewinds to the single whole first turn",
   len(sp4) == 3 and sum(1 for x in sp4 if x["kind"] == "turn") == 1,
   [(x["kind"], x["name"]) for x in sp4])
ck("cap: and that turn still has both its tool spans",
   sum(1 for x in sp4 if x["kind"] == "tool") == 2, sp4)

# OTLP: a header is not part of the file, so the fact travels in the resource
o9 = capped(9, "otel")
doc9 = json.loads(o9.body.decode())
ra9 = {a["key"]: a["value"] for a in doc9["resourceSpans"][0]["resource"]["attributes"]}
ck("cap/otel: resource says the export is truncated",
   ra9.get("hermes.export.truncated") == {"boolValue": True}, ra9.get("hermes.export.truncated"))
ck("cap/otel: resource carries the span cap as an int64 string",
   ra9.get("hermes.export.span_cap") == {"intValue": "9"}, ra9.get("hermes.export.span_cap"))
ck("cap/otel: service.name survives alongside it",
   ra9.get("service.name", {}).get("stringValue") == "hermes-assistant", ra9)
osp9 = [s for r_ in doc9["resourceSpans"] for ss in r_["scopeSpans"] for s in ss["spans"]]
p9 = set(s["spanId"] for s in osp9)
ck("cap/otel: no orphan child in the OTLP body either",
   all(s["parentSpanId"] in p9 for s in osp9 if "parentSpanId" in s))

whole = export(ctx(since=SINCE, until=UNTIL, format="otel"))
raw = {a["key"] for a in json.loads(whole.body.decode())["resourceSpans"][0]["resource"]["attributes"]}
ck("cap/otel: a COMPLETE export claims nothing about truncation",
   "hermes.export.truncated" not in raw and "hermes.export.span_cap" not in raw, sorted(raw))
ck("cap: a complete export sets no truncation header",
   "X-Hermes-Trace-Truncated" not in whole.headers and
   "X-Hermes-Trace-Span-Cap" not in whole.headers, whole.headers)
ck("cap: the real cap is restored", G["_TR_MAX_SPANS"] == REAL_CAP, G["_TR_MAX_SPANS"])

# --------------------------------------------------------------------------
# 6. real turns and inferred turns are counted apart
# --------------------------------------------------------------------------
s = summary(ctx(since=SINCE, until=UNTIL))
ck("summary: turns_synthetic counts the Recorder-only clusters",
   s["turns_synthetic"] == 2, s.get("turns_synthetic"))
ck("summary: turns stays the TOTAL (nothing that already read it moves)",
   s["turns"] == 4, s["turns"])
ck("summary: so the card can show 2 measured turns and 2 inferred",
   s["turns"] - s["turns_synthetic"] == 2)
s_empty = summary(ctx(since=NOON + 7200, until=NOON + 10800))
ck("summary: an empty range reports 0 inferred turns, not a missing key",
   s_empty.get("turns_synthetic") == 0, s_empty.get("turns_synthetic"))

# --------------------------------------------------------------------------
print()
print("scratch HOME:", HOME)
if FAILS:
    print("FAILED %d: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("all checks passed")
shutil.rmtree(HOME, ignore_errors=True)
