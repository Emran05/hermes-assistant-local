#!/usr/bin/env python3
"""Backend harness for conversation checkpoints + branching (aux_branch.py).

Loads dashboard/server.py against a THROWAWAY HOME (nothing touches the real
~/.hermes) and exercises:
  * br_seed_history — the pure `session.create` seed builder — including a
    round-trip through the REAL `_coerce_seed_history` lifted out of
    ~/.hermes/hermes-agent/tui_gateway/server.py by AST, so "the gateway will
    accept this" is asserted against the gateway's own code, not a copy of it.
  * br_seed_params — the hermes_rpc SEED_HOOK: {} for an ordinary chat, seed +
    parent link for a branched one, trailing user turn dropped.
  * POST /api/sessions/branch — child contents, forked_from, the source's
    `branches` row, and (the whole point) that the source's message array is
    byte-identical afterwards.
  * save_chat's append-only guard, and the truncate=True opt-out.
  * GET /api/sessions/tree lineage, and the counts on GET /api/sessions.

NO model is started and NO gateway session is created: the seed is asserted by
shape, never by submitting a prompt.

Run:  python3 t_132_branch.py [--repo /path/to/HermesAssistant]
"""
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def section(t):
    print("\n== %s ==" % t)


# --------------------------------------------------------------------------
# locate the repo (never hardcode a home path — CI greps for those)
# --------------------------------------------------------------------------
def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    env = os.environ.get("HERMES_REPO")
    if env:
        return os.path.abspath(env)
    # tools/tests/unit/<this file>  ->  repo root
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    if os.path.exists(os.path.join(cand, "dashboard", "server.py")):
        return cand
    return os.path.join(os.path.expanduser("~"), "HermesAssistant")


REPO = _find_repo()
DASH = os.path.join(REPO, "dashboard")
REAL_HOME = os.path.expanduser("~")
REAL_CHATS = os.path.join(REAL_HOME, ".hermes", "dashboard", "chats")
AGENT_GATEWAY = os.path.join(REAL_HOME, ".hermes", "hermes-agent",
                             "tui_gateway", "server.py")

# --------------------------------------------------------------------------
# a real conversation, copied — the builder is tested on real data, and the
# real file is only ever READ.
# --------------------------------------------------------------------------
REAL_SAMPLE = None
try:
    cands = []
    for fn in os.listdir(REAL_CHATS):
        if not fn.endswith(".json") or fn.startswith("__"):
            continue
        p = os.path.join(REAL_CHATS, fn)
        try:
            with open(p) as f:
                c = json.load(f)
        except Exception:
            continue
        if len(c.get("messages") or []) >= 4:
            cands.append((len(c["messages"]), fn, c))
    cands.sort(reverse=True)
    if cands:
        REAL_SAMPLE = cands[0][2]
        print("real conversation sampled (read-only): %s, %d messages"
              % (cands[0][1], cands[0][0]))
except Exception as e:
    print("no real conversation available (%s) — synthetic only" % type(e).__name__)

# --------------------------------------------------------------------------
# throwaway HOME + server.py
# --------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="hermes-branch-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)

sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_branch_test",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_branch_test"] = srv
spec.loader.exec_module(srv)
print("server.py loaded (HOME=%s)" % TMP)

srv.agent_wake = lambda *a, **k: None
srv._mlx_start = lambda *a, **k: False

Ctx = srv.RouteCtx
GET, POST = srv.GET_ROUTES, srv.POST_ROUTES


def get(path, **q):
    r = GET[path](Ctx(query={k: [str(v)] for k, v in q.items()}))
    return r[0] if isinstance(r, tuple) else r


def post(path, body):
    r = POST[path](Ctx(body=body))
    return r[0] if isinstance(r, tuple) else r


def status(path, body):
    r = POST[path](Ctx(body=body))
    return r[1] if isinstance(r, tuple) else 200


def msgs_sha(sid):
    return hashlib.sha256(json.dumps(srv.load_chat(sid)["messages"],
                                     sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------
# A. the seed builder, and the gateway's own acceptor
# --------------------------------------------------------------------------
section("A. seed builder + _coerce_seed_history round-trip")

check("routes registered", "/api/sessions/branch" in POST
      and "/api/sessions/tree" in GET)
check("SEED_HOOK wired on hermes_rpc",
      getattr(sys.modules.get("hermes_rpc"), "SEED_HOOK", None)
      is srv.br_seed_params)

mixed = [
    {"role": "user", "text": "what is in my downloads folder", "ts": 1},
    {"role": "bot", "text": "Three files: a.pdf, b.png, notes.md", "ts": 2},
    {"role": "bot", "text": "", "ts": 3},                       # empty -> drop
    {"role": "bot", "text": "the model was asleep", "ts": 4, "err": True},
    {"role": "tool", "text": "ls -la", "ts": 5},                # no such role
    {"role": "user", "text": "   ", "ts": 6},                   # blank -> drop
    {"role": "system", "text": "you are Hermes", "ts": 7},
]
seed = srv.br_seed_history(mixed)
check("seed keeps only user/bot/system with real text", len(seed) == 3, seed)
check("bot maps to assistant",
      [m["role"] for m in seed] == ["user", "assistant", "system"], seed)
check("seed uses `content`, never `text`",
      all(set(m) == {"role", "content"} for m in seed), seed)
check("error bubbles are never seeded",
      not any("asleep" in m["content"] for m in seed))
check("turn count counts user messages only", srv.br_turn_count(mixed) == 2)

# lift the gateway's own acceptor out of its source (no import: that module
# pulls the whole agent runtime) and run our output through it.
COERCE = None
try:
    with open(AGENT_GATEWAY) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_coerce_seed_history":
            ns = {"Any": object, "isinstance": isinstance, "list": list}
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(ast.fix_missing_locations(mod), AGENT_GATEWAY, "exec"), ns)
            COERCE = ns["_coerce_seed_history"]
            break
except Exception as e:
    print("  (could not lift _coerce_seed_history: %r)" % (e,))

if COERCE is None:
    # ~/.hermes/hermes-agent is a separate install this repo does not ship —
    # CI's runner (and any fresh checkout) has no such tree. That is a
    # missing fixture, not a bug in the branch feature, so it is reported and
    # skipped rather than failed; every assertion below that depends on it is
    # itself guarded on `COERCE is not None`.
    print("  --   gateway _coerce_seed_history not available on this machine "
          "(no ~/.hermes/hermes-agent) — shape round-trip skipped")
else:
    check("our seed survives the gateway acceptor unchanged",
          COERCE(seed) == seed, (COERCE(seed), seed))
    # feeding the gateway the RAW dashboard rows must land on the same thing
    # our builder produces — minus what only we know to drop (error bubbles).
    raw = [dict(m, content=m["text"],
                role={"user": "user", "bot": "assistant"}.get(m["role"], m["role"]))
           for m in mixed if not m.get("err")]
    check("the gateway keeps exactly what our builder keeps",
          COERCE(raw) == srv.br_seed_history(mixed),
          (COERCE(raw), srv.br_seed_history(mixed)))
    if REAL_SAMPLE:
        rseed = srv.br_seed_history(REAL_SAMPLE["messages"])
        check("real conversation seeds cleanly", COERCE(rseed) == rseed
              and len(rseed) > 0, len(rseed))
    # the shape the gateway CANNOT carry — documented, and asserted
    check("tool rows have no representation in the seed",
          COERCE([{"role": "tool", "content": "ls -la output"},
                  {"role": "assistant", "tool_calls": [{"id": "1"}],
                   "content": ""}]) == [])

# --------------------------------------------------------------------------
# B. build a source conversation through the NORMAL save path
# --------------------------------------------------------------------------
section("B. branch a conversation")

SRC = "chat-branch-harness-src"
base = (REAL_SAMPLE["messages"][:6] if REAL_SAMPLE else [
    {"role": "user", "text": "plan my day", "ts": 100.0},
    {"role": "bot", "text": "Three things are due today.", "ts": 101.0},
    {"role": "user", "text": "reschedule the second one", "ts": 102.0},
    {"role": "bot", "text": "Moved it to Thursday.", "ts": 103.0},
])
srv.save_chat(SRC, {"messages": json.loads(json.dumps(base)),
                    "title": "Harness source", "serve_sid": "sid-abc",
                    "serve_key": "key-abc"})
before_sha = msgs_sha(SRC)
before_n = len(srv.load_chat(SRC)["messages"])
# What is actually ON DISK after that save: save_chat stamps a stable `id` on
# every message (2026-09-10 audit A03), so the persisted rows are the fixture
# plus an id. Compare children and restores against THIS, not the raw fixture.
saved_base = json.loads(json.dumps(srv.load_chat(SRC)["messages"]))

r = post("/api/sessions/branch", {"session": SRC, "at_index": 2})
check("branch ok", r.get("ok") is True, r)
NEW = r.get("session", "")
check("new id is a fresh conversation", bool(NEW) and NEW != SRC, NEW)
check("response reports the carried count", r.get("messages") == 2, r)
check("response carries the tool-results caveat",
      "tool results" in (r.get("note") or "").lower(), r.get("note"))

child = srv.load_chat(NEW)
check("child has exactly the first 2 messages", len(child["messages"]) == 2,
      len(child["messages"]))
check("child messages are byte-identical to the source prefix",
      child["messages"] == saved_base[:2])
check("timestamps are kept, not re-stamped",
      [m.get("ts") for m in child["messages"]] == [m.get("ts") for m in base[:2]])
check("child records forked_from",
      child.get("forked_from", {}).get("session") == SRC
      and child["forked_from"].get("at_index") == 2
      and isinstance(child["forked_from"].get("ts"), float), child.get("forked_from"))
check("child does NOT inherit the parent's agent session",
      not child.get("serve_sid") and not child.get("serve_key"), child.keys())

src_after = srv.load_chat(SRC)
check("SOURCE MESSAGES UNTOUCHED (sha)", msgs_sha(SRC) == before_sha,
      "%s != %s" % (msgs_sha(SRC)[:12], before_sha[:12]))
check("source length unchanged", len(src_after["messages"]) == before_n)
check("source gained one branches row",
      src_after.get("branches") == [{"child": NEW, "at_index": 2,
                                     "ts": child["forked_from"]["ts"]}],
      src_after.get("branches"))
check("source keeps its own serve session",
      src_after.get("serve_sid") == "sid-abc")

# deep copy, not an alias
child["messages"][0]["text"] = "MUTATED"
srv.save_chat(NEW, child)
check("mutating the child cannot reach the source", msgs_sha(SRC) == before_sha)
child["messages"][0]["text"] = base[0]["text"]
srv.save_chat(NEW, child)

# a second branch off the same source appends, never replaces
r2 = post("/api/sessions/branch", {"session": SRC, "at_index": 4,
                                   "title": "Second cut"})
NEW2 = r2.get("session", "")
check("second branch ok", r2.get("ok") is True and NEW2 != NEW, r2)
check("branches is append-only", len(srv.load_chat(SRC).get("branches")) == 2)
check("explicit title honoured", srv.load_chat(NEW2).get("title") == "Second cut")
check("source STILL untouched after two branches", msgs_sha(SRC) == before_sha)

# validation
check("at_index 0 refused", status("/api/sessions/branch",
                                   {"session": SRC, "at_index": 0}) == 400)
check("at_index past the end refused",
      status("/api/sessions/branch", {"session": SRC,
                                      "at_index": before_n + 1}) == 400)
check("non-numeric at_index refused",
      status("/api/sessions/branch", {"session": SRC, "at_index": "two"}) == 400)
check("unknown conversation refused",
      status("/api/sessions/branch", {"session": "chat-does-not-exist",
                                      "at_index": 1}) == 400)
check("path traversal refused",
      status("/api/sessions/branch", {"session": "../../etc/passwd",
                                      "at_index": 1}) == 400)
check("prewarm key refused",
      status("/api/sessions/branch", {"session": srv.PREWARM_SESSION,
                                      "at_index": 1}) == 400)

# --------------------------------------------------------------------------
# C. save_chat is append-only
# --------------------------------------------------------------------------
section("C. append-only guard")

shrunk = srv.load_chat(SRC)
shrunk["messages"] = shrunk["messages"][:1]
try:
    srv.save_chat(SRC, shrunk)
    check("shortening a conversation raises", False, "no exception")
except ValueError as e:
    check("shortening a conversation raises", True)
    check("the error names the loss", "drop" in str(e) and SRC in str(e), str(e))
check("the file survived the refused write", msgs_sha(SRC) == before_sha)

try:
    srv.save_chat(SRC, shrunk, truncate=True)
    check("truncate=True is the explicit opt-out",
          len(srv.load_chat(SRC)["messages"]) == 1)
except Exception as e:
    check("truncate=True is the explicit opt-out", False, repr(e))
# put it back — from a FRESH load, because the guard now also refuses a save
# built on a snapshot that predates a branch (that is the whole lost-update
# class it exists to catch, and this harness tripped it once for real).
restored = dict(srv.load_chat(SRC), messages=json.loads(json.dumps(saved_base)))
srv.save_chat(SRC, restored, truncate=True)
check("restored", msgs_sha(SRC) == before_sha)
try:
    srv.save_chat(SRC, dict(src_after))          # stale: 1 branch, not 2
    check("a stale snapshot cannot un-register a branch", False, "no exception")
except ValueError as e:
    # Either guard is a correct refusal: the rev check (the general
    # lost-update case, added by 2026-09-10 audit A03) usually fires first,
    # the branch-record length check is the older, narrower one.
    check("a stale snapshot cannot un-register a branch",
          "branch record" in str(e) or "revision" in str(e), str(e))

check("metadata-only saves still work (rename/pin path)",
      (lambda: (srv.save_chat(SRC, dict(srv.load_chat(SRC), title="Renamed")),
                srv.load_chat(SRC)["title"] == "Renamed")[1])())

# --------------------------------------------------------------------------
# D. the seed hook
# --------------------------------------------------------------------------
section("D. SEED_HOOK (session.create params)")

check("an ordinary conversation changes the create call not at all",
      srv.br_seed_params(srv.load_chat(SRC)) == {},
      srv.br_seed_params(srv.load_chat(SRC)))

# the state the hook actually sees: POST /api/chat has appended the new user
# turn to the branch before _chat_worker runs.
live = srv.load_chat(NEW)
live["messages"].append({"role": "user", "text": "try it the other way",
                         "ts": time.time()})
srv.save_chat(NEW, live)
params = srv.br_seed_params(srv.load_chat(NEW))
check("a branch seeds", "messages" in params, params)
check("the seed is the pre-branch prefix only",
      [m["content"] for m in params["messages"]]
      == [m["text"] for m in base[:2]], params.get("messages"))
check("the turn being submitted is not seeded twice",
      not any("other way" in m["content"] for m in params["messages"]))
check("stale parent key is not sent as parent_session_id",
      "parent_session_id" not in params, params)
if COERCE is not None:
    check("the exact params survive the gateway acceptor",
          COERCE(params["messages"]) == params["messages"])
print("  session.create params for the branch:\n    %s"
      % json.dumps({"title": srv.load_chat(NEW).get("title"),
                    "cwd": "~", "source": "hub", **params},
                   indent=2).replace("\n", "\n    "))

# --------------------------------------------------------------------------
# E. tree + sessions payload
# --------------------------------------------------------------------------
section("E. lineage")

t = get("/api/sessions/tree", session=NEW)
check("tree ok", t.get("ok") is True, t)
check("tree names the parent",
      [a["id"] for a in t["ancestors"]] == [SRC], t.get("ancestors"))
check("the ancestor row carries the cut point",
      t["ancestors"][0]["at_index"] == 2 and t["ancestors"][0]["turn"] == 1,
      t["ancestors"][0])
check("a leaf has no children", t["children"] == [], t["children"])

ts = get("/api/sessions/tree", session=SRC)
check("the source lists both children",
      sorted(c["id"] for c in ts["children"]) == sorted([NEW, NEW2]),
      ts.get("children"))
check("each child row carries its cut point and title",
      all(c["at_index"] in (2, 4) and c["title"] for c in ts["children"]),
      ts["children"])
check("the source has no ancestors", ts["ancestors"] == [])
check("unknown session refused",
      (GET["/api/sessions/tree"](Ctx(query={"session": ["nope"]})))[1] == 400)

# a deleted parent must not vanish the branch
os.remove(srv.chat_path(NEW2))
t3 = get("/api/sessions/tree", session=SRC)
check("a deleted child is reported, not dropped",
      any(c["id"] == NEW2 and c["exists"] is False for c in t3["children"]),
      t3["children"])

sessions = {s["id"]: s for s in srv.list_sessions()}
check("GET /api/sessions carries forked_from on the branch",
      sessions[NEW].get("forked_from", {}).get("at_index") == 2, sessions.get(NEW))
check("GET /api/sessions resolves the parent's title",
      sessions[NEW].get("forked_title") == srv.load_chat(SRC).get("title"),
      sessions[NEW].get("forked_title"))
check("GET /api/sessions carries the human turn number",
      sessions[NEW].get("forked_turn") == 1, sessions[NEW].get("forked_turn"))
check("GET /api/sessions counts branches on the source",
      sessions[SRC].get("branches") == 2, sessions[SRC].get("branches"))
check("an ordinary conversation gains no branch keys",
      (lambda: (srv.save_chat("chat-plain-harness",
                              {"messages": [{"role": "user", "text": "hi",
                                             "ts": 1}], "title": "Plain"}),
                not any(k in {s["id"]: s for s in srv.list_sessions()}
                        ["chat-plain-harness"]
                        for k in ("forked_from", "branches", "forked_turn")))[1])())

# --------------------------------------------------------------------------
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
try:
    shutil.rmtree(TMP)
except Exception:
    pass
sys.stdout.flush()          # os._exit skips buffers; the aux threads are why
sys.stderr.flush()          # we cannot wait for a clean interpreter shutdown
os._exit(1 if FAILS else 0)
