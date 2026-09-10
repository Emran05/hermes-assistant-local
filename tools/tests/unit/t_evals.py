#!/usr/bin/env python3
"""Unit tests for dashboard/aux_evals.py — no model, no network, no dashboard.

Execs the aux module into a fake server.py globals namespace with every
foreign global stubbed, and with _ev_thread_started pre-set so the scheduler
thread never starts.
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_evals.py")

TMP = tempfile.mkdtemp(prefix="evals-test-")
SETTINGS = os.path.join(TMP, "settings.json")

FAILS = []
CHECKS = [0]

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (CHECKS[0] - len(FAILS), len(FAILS))))


def check(name, cond, extra=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- fake server
G = {}
CHAT_JOBS = {}
STATE = {"active": "test/Model-27B", "online": True, "paused": False,
         "suspended": False, "woke": 0}


def _read_json(p, d=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def _write_json(p, obj):
    with open(p, "w") as f:
        json.dump(obj, f)


_STATE_LOCK = threading.Lock()


def settings_update(mutate_fn):
    """Stand-in for server.py's settings_update() — ONE locked
    read-modify-write of settings.json, the only way an aux module is allowed
    to write it since the 2026-09-10 audit (A01)."""
    with _STATE_LOCK:
        s = _read_json(SETTINGS, {})
        if not isinstance(s, dict):
            s = {}
        out = mutate_fn(s)
        if isinstance(out, dict):
            s = out
        _write_json(SETTINGS, s)
        return json.loads(json.dumps(s))


G.update({
    "DATA": TMP,
    "MODEL_URL": "http://127.0.0.1:8080/v1/models",
    "SETTINGS_FILE": SETTINGS,
    "CHAT_JOBS": CHAT_JOBS,
    "_state_lock": _STATE_LOCK,
    "settings_update": settings_update,
    "get_settings": lambda: _read_json(SETTINGS, {}) or {},
    "write_json": _write_json,
    "read_json": _read_json,
    "active_model": lambda: STATE["active"],
    "model_online": lambda: STATE["online"],
    "agent_paused": lambda: STATE["paused"],
    "agent_idle_suspended": lambda: STATE["suspended"],
    "agent_wake": lambda wait=True, timeout=90: (STATE.__setitem__("woke", STATE["woke"] + 1),
                                                 STATE["online"])[1],
    "register_get": lambda p, f: G.setdefault("_GETS", {}).__setitem__(p, f),
    "register_post": lambda p, f: G.setdefault("_POSTS", {}).__setitem__(p, f),
    "_cached": lambda k, ttl, fn: fn(),        # no caching in tests
    "_ev_thread_started": True,                # no scheduler thread
})

with open(AUX) as f:
    exec(compile(f.read(), AUX, "exec"), G)

print("\n== module loaded ==")
check("routes registered", sorted(G["_GETS"]) == ["/api/evals"] and
      sorted(G["_POSTS"]) == ["/api/evals/run", "/api/evals/settings"],
      (sorted(G["_GETS"]), sorted(G["_POSTS"])))

# ------------------------------------------------------------- 1. checkers
print("\n== 1. ev_check_json ==")
J = G["ev_check_json"]
KEYS = G["EV_JSON_KEYS"]
cases = [
    ('{"city":"Tokyo","country":"Japan","population":13960000}', True, "exact keys"),
    ('```json\n{"city":"Tokyo","country":"Japan","population":14}\n```', True, "fenced"),
    ('Sure! Here it is:\n{"city":"Tokyo","country":"Japan","population":14}', True, "prose preamble"),
    ('{"city":"Tokyo","country":"Japan"}', False, "missing a key"),
    ('{"city":"Tokyo","country":"Japan","population":14,"region":"Kanto"}', False, "extra key"),
    ('{"City":"Tokyo","Country":"Japan","Population":14}', False, "wrong case keys"),
    ('Tokyo is in Japan and has 14 million people.', False, "prose, no JSON"),
    ('[{"city":"Tokyo"}]', False, "array not object"),
    ('', False, "empty"),
    ('{"city":"Tokyo","country":"Japan","population":}', False, "malformed json"),
]
for text, want, why in cases:
    got, detail = J(text, KEYS)
    check("json: " + why, got == want, "got=%s detail=%s" % (got, detail))

print("\n== 2. ev_check_table ==")
T = G["ev_check_table"]
COLS = G["EV_TABLE_COLS"]
good = ("| Language | Year | Creator |\n|---|---|---|\n"
        "| Python | 1991 | Guido van Rossum |\n"
        "| Rust | 2010 | Graydon Hoare |\n"
        "| Go | 2009 | Robert Griesemer |")
good_fenced = "```markdown\n" + good + "\n```"
good_bold = good.replace("| Language |", "| **Language** |")
good_align = ("| Language | Year | Creator |\n|:---|:---:|---:|\n"
              "| Python | 1991 | Guido |\n| Rust | 2010 | Graydon |\n| Go | 2009 | Rob |")
good_lead = "Here you go:\n\n" + good
two_rows = ("| Language | Year | Creator |\n|---|---|---|\n"
            "| Python | 1991 | Guido |\n| Rust | 2010 | Graydon |")
wrong_cols = ("| Name | Year | Author |\n|---|---|---|\n"
              "| Python | 1991 | Guido |\n| Rust | 2010 | G |\n| Go | 2009 | R |")
reordered = ("| Year | Language | Creator |\n|---|---|---|\n"
             "| 1991 | Python | Guido |\n| 2010 | Rust | G |\n| 2009 | Go | R |")
no_sep = ("| Language | Year | Creator |\n| Python | 1991 | Guido |\n"
          "| Rust | 2010 | G |\n| Go | 2009 | R |")
prose = "Python was created in 1991 by Guido van Rossum, Rust in 2010, Go in 2009."
for text, want, why in [
        (good, True, "clean 3-row table"),
        (good_fenced, True, "fenced"),
        (good_bold, True, "bold header cell"),
        (good_align, True, "alignment colons"),
        (good_lead, True, "leading prose"),
        (two_rows, False, "only 2 rows"),
        (wrong_cols, False, "wrong column names"),
        (reordered, False, "columns out of order"),
        (no_sep, False, "no separator row"),
        (prose, False, "prose, no table"),
        ("", False, "empty")]:
    got, detail = T(text, COLS, 3)
    check("table: " + why, got == want, "got=%s detail=%s" % (got, detail))

print("\n== 3. ev_check_one_sentence ==")
S = G["ev_check_one_sentence"]
for text, want, why in [
        ("Paris is the capital of France and matters as its political, "
         "cultural and economic centre.", True, "one sentence, 16 words"),
        ("Paris", True, "single word, no period"),
        ("Paris is the capital of France. It is also its largest city.",
         False, "two sentences"),
        ("Paris is the capital of France! Why does it matter?", False,
         "two sentences, mixed punctuation"),
        ("- Paris is the capital of France.", False, "dash list marker"),
        ("1. Paris is the capital of France.", False, "numbered list marker"),
        ("* Paris is the capital.", False, "star list marker"),
        (" ".join(["word"] * 31) + ".", False, "31 words"),
        (" ".join(["word"] * 30) + ".", True, "exactly 30 words"),
        ("", False, "empty"),
        ("It is, e.g. the seat of government and a global cultural hub.",
         True, "lowercase after abbreviation is not a split")]:
    got, detail = S(text, 30)
    check("sentence: " + why, got == want, "got=%s detail=%s" % (got, detail))

# ------------------------------------------------------- 4. runner DB writes
print("\n== 4. runner + db ==")


def stub_cases(spec):
    """spec: [(case_id, passed, sleep_ms)] -> patch evals_cases()."""
    def mk(passed, ms, cid):
        def fn(_mid):
            time.sleep(ms / 1000.0)
            return passed, "stub detail for " + cid
        return fn
    G["evals_cases"] = lambda: [(cid, "stub " + cid, mk(p, ms, cid))
                                for cid, p, ms in spec]


real_cases = G["evals_cases"]
stub_cases([("simple_call", True, 10), ("nested_args", True, 30),
            ("restraint", False, 20), ("chain", True, 50),
            ("impossible", True, 5), ("deflection_probe", False, 40),
            ("json_strict", True, 15), ("table_format", True, 25),
            ("one_sentence", True, 35)])

res = G["evals_run"](trigger="manual")
check("run ok", res.get("ok") is True, res)
check("9 cases", res.get("total") == 9, res.get("total"))
check("7 passed", res.get("passed") == 7, res.get("passed"))
check("median is an int", isinstance(res.get("median_latency_ms"), int))
check("run_id > 0", res.get("run_id", 0) > 0, res.get("run_id"))

dbp = os.path.join(TMP, "evals.db")
check("db exists", os.path.exists(dbp))
check("db is 0600", oct(os.stat(dbp).st_mode & 0o777) == "0o600",
      oct(os.stat(dbp).st_mode & 0o777))
con = sqlite3.connect(dbp)
con.row_factory = sqlite3.Row
r = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
check("runs row model", r["model_id"] == STATE["active"], r["model_id"])
check("runs row trigger", r["trigger"] == "manual", r["trigger"])
check("runs total/passed", (r["total"], r["passed"]) == (9, 7))
check("latency_ms_total >= sum of cases",
      r["latency_ms_total"] >= 200, r["latency_ms_total"])
check("error is NULL", r["error"] is None, r["error"])
rows = con.execute('SELECT "case",passed,latency_ms,detail FROM results '
                   "WHERE run_id=? ORDER BY rowid", (r["id"],)).fetchall()
check("9 result rows", len(rows) == 9, len(rows))
check("results carry the case ids",
      [x[0] for x in rows][:3] == ["simple_call", "nested_args", "restraint"],
      [x[0] for x in rows][:3])
check("failing case recorded as 0",
      dict((x[0], x[1]) for x in rows)["restraint"] == 0)
check("details stored", all(x[3].startswith("stub detail") for x in rows))
con.close()

# a raising case must be recorded as a failure, not crash the run
stub_cases([("simple_call", True, 1)])
G["evals_cases"] = lambda: [("boom", "explodes",
                             lambda _m: (_ for _ in ()).throw(RuntimeError("kaboom")))]
res2 = G["evals_run"](trigger="manual")
check("raising case -> run still ok", res2.get("ok") is True, res2)
check("raising case counted as a failure", res2.get("passed") == 0, res2)
check("raising case detail names the error",
      "kaboom" in res2["cases"][0]["detail"], res2["cases"][0]["detail"])

# model asleep -> refuse, and NOTHING is written
STATE["online"] = False
before = sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
res3 = G["evals_run"](trigger="manual")
after = sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
check("asleep -> ok False", res3.get("ok") is False, res3)
check("asleep -> reason 'model asleep'", res3.get("reason") == "model asleep", res3)
check("asleep -> wrote no run row", before == after, (before, after))
check("asleep -> did NOT wake (allow_wake default False)", STATE["woke"] == 0,
      STATE["woke"])

# allow_wake=True is the only path that calls agent_wake
STATE["online"] = True
res4 = G["evals_run"](trigger="scheduled", allow_wake=True)
check("allow_wake path runs", res4.get("ok") is True, res4)

# never switches models
res5 = G["evals_run"](model_id="some/Other-Model")
check("refuses a non-active model", res5.get("ok") is False and
      res5.get("reason") == "not the active model", res5)

# paused
STATE["paused"] = True
res6 = G["evals_run"]()
check("refuses while paused", res6.get("reason") == "agent paused", res6)
STATE["paused"] = False

# history + last-run reads
hist = G["_ev_history"](60)
check("history returned", len(hist) >= 3, len(hist))
check("history is oldest-first",
      all(hist[i]["ts"] <= hist[i + 1]["ts"] for i in range(len(hist) - 1)))
last, lcases = G["_ev_last_run"]()
check("last run is the newest", last["id"] == max(h["id"] for h in hist))
check("last run cases returned", len(lcases) >= 1, len(lcases))

G["evals_cases"] = real_cases

# ------------------------------------------------------------ 5. gate table
print("\n== 5. scheduler gate truth table ==")
GATE = G["evals_gate"]
CFG = dict(G["EV_DEFAULTS"])          # enabled, 13:00, require_ac, no wake, 1d


def at(hh, mm=0):
    lt = list(time.localtime())
    lt[3], lt[4], lt[5] = hh, mm, 0
    return time.mktime(tuple(lt))


def env(ac=True, online=True, quiet=False, paused=False, busy=False):
    return {"ac": ac, "online": online, "quiet": quiet, "paused": paused,
            "busy": busy}


TODAY = G["_ev_today"]()
YDAY = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
NOW = at(14, 0)      # after the 13:00 slot, before the 23:00 cutoff

TABLE = [
    # (label, cfg-overrides, guard, now, env, expect_run, expect_wake, expect_reason)
    ("AC + awake + clear             -> RUN", {}, "", NOW, env(), True, False, "model online"),
    ("battery + awake                -> skip", {}, "", NOW, env(ac=False), False, False, "on battery"),
    ("battery + asleep               -> skip", {}, "", NOW, env(ac=False, online=False), False, False, "on battery"),
    ("battery + asleep + wake_if_ac  -> skip (never wake on battery)",
     {"wake_if_ac": True}, "", NOW, env(ac=False, online=False), False, False, "on battery"),
    ("battery + require_ac off + awake -> RUN",
     {"require_ac": False}, "", NOW, env(ac=False), True, False, "model online"),
    ("battery + require_ac off + asleep + wake_if_ac -> skip (no AC)",
     {"require_ac": False, "wake_if_ac": True}, "", NOW, env(ac=False, online=False),
     False, False, "model asleep"),
    ("AC + asleep + wake off         -> skip", {}, "", NOW, env(online=False), False, False, "model asleep"),
    ("AC + asleep + wake_if_ac       -> RUN + WAKE",
     {"wake_if_ac": True}, "", NOW, env(online=False), True, True, "waking on AC"),
    ("quiet hours (AC + awake)       -> skip", {}, "", NOW, env(quiet=True), False, False, "quiet hours"),
    ("before the slot                -> skip", {}, "", at(9, 0), env(), False, False, "before 13:00"),
    ("guard set today                -> skip", {}, TODAY, NOW, env(), False, False, "already ran today"),
    ("guard set yesterday, days=1    -> RUN", {}, YDAY, NOW, env(), True, False, "model online"),
    ("guard set yesterday, days=7    -> skip", {"days": 7}, YDAY, NOW, env(), False, False,
     "ran 1 day ago (every 7)"),
    ("disabled                       -> skip", {"enabled": False}, "", NOW, env(), False, False, "disabled"),
    ("paused                         -> skip", {}, "", NOW, env(paused=True), False, False, "agent paused"),
    ("chat turn running              -> skip", {}, "", NOW, env(busy=True), False, False,
     "a chat turn is running"),
    ("23:30, slot was 13:00          -> mark done, no run", {}, "", at(23, 30), env(), False, False,
     "slept through the window"),
    ("22:00 (no quiet hours set)     -> still RUN", {}, "", at(22, 0), env(), True, False, "model online"),
]
for label, over, guard, now, e, want_run, want_wake, want_reason in TABLE:
    cfg = dict(CFG)
    cfg.update(over)
    g = GATE(cfg, guard, now, e)
    ok = (g["run"] == want_run and g["wake"] == want_wake and
          g["reason"] == want_reason)
    check(label, ok, g)

g = GATE(dict(CFG), "", at(23, 30), env())
check("slept-through sets mark_done", g["mark_done"] is True, g)
g = GATE(dict(CFG), "", NOW, env(ac=False))
check("battery skip does NOT mark_done (keeps checking)", g["mark_done"] is False, g)

# _ev_tick end-to-end against the same stubs
print("\n== 6. _ev_tick end-to-end ==")
stub_cases([("a", True, 1), ("b", False, 1)])
_write_json(SETTINGS, {"evals": {"enabled": True, "at_hour": 0,
                                 "at_minute": 0, "require_ac": False,
                                 "wake_if_ac": False, "days": 1}})
G["_ev_meta_set"]("last_sched_date", "")
n0 = sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
out = G["_ev_tick"]()
n1 = sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
check("tick ran the suite", out.get("run") is True and n1 == n0 + 1, (out, n0, n1))
check("tick recorded trigger=scheduled",
      sqlite3.connect(dbp).execute(
          "SELECT trigger FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "scheduled")
check("tick set the guard", G["_ev_meta_get"]("last_sched_date") == TODAY,
      G["_ev_meta_get"]("last_sched_date"))
out2 = G["_ev_tick"]()
n2 = sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
check("second tick same day is a no-op", out2.get("run") is False and n2 == n1,
      (out2, n1, n2))
G["evals_cases"] = real_cases

# --------------------------------------------------------- 7. settings round-trip
print("\n== 7. settings ==")
_write_json(SETTINGS, {"weather": {"city": "keepme"}})
cfg = G["evals_settings"]()
check("defaults when absent", cfg == G["EV_DEFAULTS"], cfg)
cfg = G["evals_set_settings"]({"at_hour": 99, "at_minute": -3, "days": 400,
                               "enabled": False, "wake_if_ac": True})
check("hour clamped to 23", cfg["at_hour"] == 23, cfg)
check("minute clamped to 0", cfg["at_minute"] == 0, cfg)
check("days clamped to 30", cfg["days"] == 30, cfg)
check("enabled written", cfg["enabled"] is False, cfg)
check("wake_if_ac written", cfg["wake_if_ac"] is True, cfg)
check("require_ac kept at default", cfg["require_ac"] is True, cfg)
check("unrelated settings survive",
      _read_json(SETTINGS, {}).get("weather", {}).get("city") == "keepme",
      _read_json(SETTINGS, {}))

# --------------------------------------------------------- 8. route payloads
print("\n== 8. routes (no model) ==")


class Ctx:
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}

    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d


_write_json(SETTINGS, {})
G["evals_power"] = lambda: {"ac": False, "battery": True, "pct": 71}
payload = G["_GETS"]["/api/evals"](Ctx())
check("GET keys", set(["ok", "settings", "last", "results", "history",
                       "power", "can_run", "reason"]) <= set(payload), sorted(payload))
check("GET can_run False on battery", payload["can_run"] is False, payload["can_run"])
check("GET reason mentions battery", "battery" in payload["reason"], payload["reason"])
check("GET history non-empty", len(payload["history"]) >= 3, len(payload["history"]))
run = G["_POSTS"]["/api/evals/run"](Ctx({}))
check("POST run refuses on battery", run.get("ok") is False and
      run.get("reason") == "on battery", run)
run = G["_POSTS"]["/api/evals/run"](Ctx({"force": True}))
check("POST run refuses on battery even with force", run.get("ok") is False and
      run.get("reason") == "on battery", run)
STATE["online"] = False
G["evals_power"] = lambda: {"ac": True, "battery": True, "pct": 100}
run = G["_POSTS"]["/api/evals/run"](Ctx({}))
check("POST run on AC, asleep, no force -> refuses, wakeable",
      run.get("ok") is False and run.get("wakeable") is True, run)
STATE["online"] = True

# ----------------------------------------- 9. infra errors are a third state
print("\n== 9. infrastructure errors are a third state ==")


def raising_cases(exc, n=3, ok_first=False):
    def mk(i):
        def fn(_mid):
            if ok_first and i == 0:
                return True, "fine"
            raise exc
        return fn
    G["evals_cases"] = lambda: [("case%d" % i, "stub case %d" % i, mk(i))
                                for i in range(n)]


def last_run_row():
    c = sqlite3.connect(dbp)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    rs = c.execute('SELECT "case",passed,error,detail FROM results '
                   "WHERE run_id=? ORDER BY rowid", (r["id"],)).fetchall()
    c.close()
    return r, rs


def n_runs():
    c = sqlite3.connect(dbp)
    n = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    c.close()
    return n


raising_cases(ConnectionRefusedError(61, "Connection refused"), 3)
res = G["evals_run"](trigger="manual")
check("infra run still completes", res.get("ok") is True, res)
check("all three counted as errors", res.get("errors") == 3, res)
check("errors are not passes", res.get("passed") == 0, res)
check("every case carries error=True",
      all(c.get("error") is True for c in res["cases"]), res["cases"])
check("detail says infrastructure",
      res["cases"][0]["detail"].startswith("infrastructure error:"),
      res["cases"][0]["detail"])
r, rs = last_run_row()
check("runs.errors column written", r["errors"] == 3, r["errors"])
check("all-error run stores an error string",
      bool(r["error"]) and "reached the model server" in r["error"], r["error"])
check("results.error column written", all(x["error"] == 1 for x in rs), [dict(x) for x in rs])
check("an all-error run is a gap, not a zero (errors == total)",
      r["errors"] == r["total"] and r["passed"] == 0, (r["errors"], r["total"]))

# a timeout is infrastructure too
raising_cases(TimeoutError("timed out"), 2)
res = G["evals_run"](trigger="manual")
check("timeout counts as an error", res.get("errors") == 2, res)

# a genuine logic bug in a case is NOT infrastructure
G["evals_cases"] = lambda: [("boom", "explodes",
                             lambda _m: (_ for _ in ()).throw(ValueError("nope")))]
res = G["evals_run"](trigger="manual")
check("a ValueError is a FAILURE, not an error",
      res.get("errors") == 0 and res.get("passed") == 0, res)
check("failure detail says case error",
      res["cases"][0]["detail"].startswith("case error:"), res["cases"][0]["detail"])
r, _rs = last_run_row()
check("a plain failure stores no run error", r["error"] is None, r["error"])

# mixed: one real answer, two unreachable -> not an all-error run
raising_cases(ConnectionRefusedError(61, "Connection refused"), 3, ok_first=True)
res = G["evals_run"](trigger="manual")
check("mixed run: 1 passed, 2 errors", (res.get("passed"), res.get("errors")) == (1, 2), res)
r, _rs = last_run_row()
check("mixed run is not marked all-error", r["error"] is None, r["error"])

# ------------------------------------ 10. a slept-through day writes a row
print("\n== 10. slept through the window ==")
ran = {"n": 0}


def counting_cases():
    def fn(_mid):
        ran["n"] += 1
        return True, "ran"
    G["evals_cases"] = lambda: [("counted", "counts its own calls", fn)]


counting_cases()
_write_json(SETTINGS, {"evals": {"enabled": True, "at_hour": 13, "at_minute": 0,
                                 "require_ac": False, "wake_if_ac": False,
                                 "days": 1}})
G["_ev_meta_set"]("last_sched_date", "")
G["_EV_MEM_GUARD"] = ""
before = n_runs()
out = G["_ev_tick"](now_ts=at(23, 30))
check("tick marks the day done without running",
      out["run"] is False and out["mark_done"] is True and
      out["reason"] == "slept through the window", out)
check("no case was run", ran["n"] == 0, ran["n"])
check("a row WAS written for the skipped day", n_runs() == before + 1,
      (before, n_runs()))
r, rs = last_run_row()
check("skip row: trigger", r["trigger"] == "skipped", r["trigger"])
check("skip row: total 0", (r["total"], r["passed"], r["errors"]) == (0, 0, 0),
      (r["total"], r["passed"], r["errors"]))
check("skip row: the reason is the error", r["error"] == "slept through the window",
      r["error"])
check("skip row: no per-case rows", len(rs) == 0, len(rs))
last, lcases = G["_ev_last_run"]()
check("the skip row is what the card reads as `last`",
      last["total"] == 0 and last["error"] == "slept through the window" and lcases == [],
      (last, lcases))
check("the guard flipped so it is written once", G["_ev_meta_get"]("last_sched_date") == TODAY,
      G["_ev_meta_get"]("last_sched_date"))
out2 = G["_ev_tick"](now_ts=at(23, 31))
check("a second tick that day writes nothing more", n_runs() == before + 1, n_runs())

# ------------------------------------------- 11. a dead store never loops
print("\n== 11. a dead store refuses to run ==")
counting_cases()
ran["n"] = 0
DEADDIR = os.path.join(TMP, "not-a-db")
os.makedirs(DEADDIR, exist_ok=True)
G["EV_DB"] = DEADDIR                      # sqlite cannot open a directory
G["_ev_inited"] = False
G["_ev_store_error"] = ""
check("_ev_init reports failure", G["_ev_init"]() is False)
check("the failure text is kept", bool(G["_ev_store_error"]), G["_ev_store_error"])
G["_EV_MEM_GUARD"] = ""
_write_json(SETTINGS, {"evals": {"enabled": True, "at_hour": 0, "at_minute": 0,
                                 "require_ac": False, "wake_if_ac": False,
                                 "days": 1}})
out = G["_ev_tick"](now_ts=at(14, 0))
check("tick refuses with 'store unavailable'",
      out["run"] is False and out["reason"] == "store unavailable", out)
check("no completion was fired", ran["n"] == 0, ran["n"])
out = G["_ev_tick"](now_ts=at(14, 1))
check("and it keeps refusing rather than looping", ran["n"] == 0, ran["n"])
payload = G["_GETS"]["/api/evals"](Ctx())
check("GET /api/evals carries store_error", bool(payload.get("store_error")),
      payload.get("store_error"))
check("GET /api/evals has no last run to show", payload["last"] is None, payload["last"])
G["EV_DB"] = dbp
G["_ev_inited"] = False
check("the store recovers when the db comes back", G["_ev_init"]() is True)
check("store_error is cleared on recovery", G["_ev_store_error"] == "",
      G["_ev_store_error"])

# --------------------------- 12. the in-memory guard survives a lost write
print("\n== 12. the in-memory guard holds ==")
counting_cases()
ran["n"] = 0
_write_json(SETTINGS, {"evals": {"enabled": True, "at_hour": 0, "at_minute": 0,
                                 "require_ac": False, "wake_if_ac": False,
                                 "days": 1}})
G["_ev_meta_set"]("last_sched_date", "")
G["_EV_MEM_GUARD"] = ""
out = G["_ev_tick"]()
check("the suite ran once", out["run"] is True and ran["n"] == 1, (out, ran["n"]))
check("the in-memory guard was set too", G["_EV_MEM_GUARD"] == TODAY, G["_EV_MEM_GUARD"])
G["_ev_meta_set"]("last_sched_date", "")     # the store loses the guard
n_before = n_runs()
out = G["_ev_tick"]()
check("a lost guard does NOT buy a second run",
      out["run"] is False and out["reason"] == "already ran today", out)
check("still one completion", ran["n"] == 1, ran["n"])
check("and no second row", n_runs() == n_before, n_runs())
G["evals_cases"] = real_cases

# ------------------------------------- 13. the settings route validates
print("\n== 13. POST /api/evals/settings validates ==")
_write_json(SETTINGS, {})
r = G["_POSTS"]["/api/evals/settings"](Ctx({"at_hour": "half past"}))
check("a non-numeric hour is a 400",
      isinstance(r, tuple) and r[1] == 400 and "number" in r[0]["error"], r)
check("and nothing was written", "evals" not in _read_json(SETTINGS, {}),
      _read_json(SETTINGS, {}))
r = G["_POSTS"]["/api/evals/settings"](Ctx({"days": True}))
check("a JSON true is not a number", isinstance(r, tuple) and r[1] == 400, r)
r = G["_POSTS"]["/api/evals/settings"](Ctx({"at_minute": None}))
check("null is not a number", isinstance(r, tuple) and r[1] == 400, r)
r = G["_POSTS"]["/api/evals/settings"]((Ctx({"at_hour": 99, "at_minute": -3,
                                             "days": 400})))
check("out-of-range values are accepted and clamped in the reply",
      r.get("ok") is True and (r["settings"]["at_hour"], r["settings"]["at_minute"],
                               r["settings"]["days"]) == (23, 0, 30), r)
stored = _read_json(SETTINGS, {}).get("evals", {})
check("settings.json holds the CLAMPED values, not the raw ones",
      (stored.get("at_hour"), stored.get("at_minute"), stored.get("days")) == (23, 0, 30),
      stored)
r = G["_POSTS"]["/api/evals/settings"](Ctx({"enabled": "sure"}))
check("a truthy string is stored as a real bool",
      _read_json(SETTINGS, {})["evals"]["enabled"] is True,
      _read_json(SETTINGS, {})["evals"])

# ----------------------------------- 14. an evals.db from 1.2.3 migrates
print("\n== 14. migration of a pre-`errors` db ==")
OLD = os.path.join(TMP, "old.db")
oc = sqlite3.connect(OLD)
oc.executescript("""
CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
  model_id TEXT NOT NULL DEFAULT '', trigger TEXT NOT NULL DEFAULT 'manual',
  total INTEGER NOT NULL DEFAULT 0, passed INTEGER NOT NULL DEFAULT 0,
  latency_ms_total INTEGER NOT NULL DEFAULT 0,
  median_latency_ms INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE results(run_id INTEGER NOT NULL, "case" TEXT NOT NULL,
  passed INTEGER NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0,
  detail TEXT);
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
""")
oc.execute("INSERT INTO runs(ts,model_id,trigger,total,passed,latency_ms_total,"
           "median_latency_ms,error) VALUES(?,?,?,?,?,?,?,NULL)",
           (time.time() - 3600, "old/Model", "scheduled", 9, 8, 900, 100))
oc.execute('INSERT INTO results(run_id,"case",passed,latency_ms,detail) '
           "VALUES(1,'simple_call',1,100,'from 1.2.3')")
oc.commit()
oc.close()
G["EV_DB"] = OLD
G["_ev_inited"] = False
check("_ev_init migrates the old db", G["_ev_init"]() is True, G["_ev_store_error"])
h = G["_ev_history"](10)
check("the 1.2.3 run is still readable", len(h) == 1 and h[0]["passed"] == 8, h)
check("its errors default to 0", h[0]["errors"] == 0, h)
lastm, casesm = G["_ev_last_run"]()
check("its per-case row reads error False",
      len(casesm) == 1 and casesm[0]["error"] is False, casesm)
G["EV_DB"] = dbp
G["_ev_inited"] = False
G["_ev_init"]()

print("\n=========================================")
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
else:
    print("ALL TESTS PASSED")
print("=========================================")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
