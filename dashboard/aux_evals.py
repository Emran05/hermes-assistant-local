# aux_evals.py — 1.2.3 (A) the local eval suite with history.
#
# exec'd into server.py's globals by the aux-module loader.  Sorted load order
# puts this file BEFORE aux_promotion.py and aux_watchtower.py, so every
# foreign global it borrows (`PRO_CASES`, `_pro_completion`, `_wt_load`,
# `_in_quiet`, `_slept_through`) is resolved BY NAME AT CALL TIME through
# `_ev_g()`, never captured at module load — the discipline aux_index.py and
# aux_onboarding.py document.  Uses these server.py globals: DATA, MODEL_URL,
# CHAT_JOBS, SETTINGS_FILE, _state_lock, get_settings, write_json,
# active_model, model_online, agent_paused, agent_idle_suspended, agent_wake,
# register_get, register_post.  ZERO server.py edits.
#
# WHAT IT IS.  `docs/plans/harness-line.md` lists one backlog item: "extend the
# 6-case drill into a scheduled local eval suite with a history chart".  The
# drill (aux_promotion.py) answers "can this model call tools at all" ONCE per
# model, overwriting its record each time; it has no history and no schedule,
# and drilling a non-active model deliberately SWITCHES the model server.  This
# module is the other half: the same six cases plus three harness-quality
# format checks, run on a schedule against the model that is ALREADY loaded,
# appended to a time series you can chart.
#
#   nine cases, all deterministic (temperature 0), all model-direct against
#   /v1/chat/completions with the drill's synthetic 3-tool schema, no tool ever
#   executed, no agent, no approval surface, no Flight Recorder rows:
#     simple_call nested_args restraint chain impossible deflection_probe
#       — REUSED from aux_promotion.py, resolved by name at call time so there
#         is exactly one copy of the drill in the tree.  Change them there.
#     json_strict   — a JSON object with three named keys, nothing else
#     table_format  — a 3-row GFM table with named columns
#     one_sentence  — one sentence, <=30 words, no list markers
#   The last three are the "harness quality" half: not "can it call a tool" but
#   "does it obey a format contract", which is what every downstream parser in
#   this dashboard actually depends on.
#
# WHY IT NEVER TOUCHES promotion.json.  The Drill button's badge is `N/6`
# against `PRO_THRESHOLD = 5`, and `models_payload()` / the `switch_model()`
# warning read `pass` out of that record.  A 9-case score cannot be written
# there without changing what `of` means, and writing back only the six-case
# subset would overwrite a real drill's `parser`, `license_note`, `swap_used`
# and `drilled_at` with a different provenance (this run never swaps, so
# `swap_used` would always be false even for a model the drill swapped to).
# There is also no reusable writer to call — the record is built inline inside
# `promotion_drill_run`.  So promotion.json stays owned by the drill, evals.db
# is owned by this module, and the Drill button's badge cannot drift.
#
# STORE.  ~/.hermes/dashboard/evals.db (0600, WAL sidecars too):
#   runs(id, ts, model_id, trigger, total, passed, errors, latency_ms_total,
#        median_latency_ms, error)
#   results(run_id, "case", passed, error, latency_ms, detail)   -- "case" is
#                                                       quoted (SQL keyword)
#   meta(k, v)   -- the scheduler's once-per-N-days guard lives here, so the
#                   whole feature is one file to delete.
#   `_ev_migrate` ADDs the two `error` columns to a db written before them, so
#   an existing history survives the upgrade meaning exactly what it meant.
#
# PASS / FAIL / ERROR — three states, not two.  A case that raises a CONNECTION
# error (refused, timed out, a body that is not JSON) never got an answer to
# judge, so it is not a failure: nine infrastructure errors used to be stored
# as 0/9 and charted as a quality collapse.  `errors` counts them per run, a
# run in which EVERY case errored is stored with `error` set and charted as a
# GAP rather than a zero, and the card's per-case table says "error".
#
# A DEAD STORE MUST NEVER BECOME A LOOP.  `_ev_init` keeps why it failed in
# `_ev_store_error` and `_ev_tick` refuses to run while it is set: with no db
# there is no `last_sched_date`, the once-per-N-days guard reads as "never
# ran", and the 60s loop would fire all nine completions every minute from the
# scheduled time until quiet hours, store nothing, and still show "never" on
# the card.  `_EV_MEM_GUARD` is the belt-and-braces half — the same YYYY-MM-DD
# the meta row holds, consulted only when the persisted one is empty — so a
# store that opens and then loses its writes still cannot buy more than one run
# per `days`.
#
# SCHEDULE (settings.json `evals`).  A straight port of aux_watchtower's
# `_brief_tick` gate ladder plus one AC-power gate and one model-state gate:
#   store available -> enabled -> quiet hours -> at/after HH:MM ->
#   once-per-N-days guard ->
#   slept-through -> agent not paused -> on AC (if require_ac) ->
#   model already online (or wake_if_ac AND on AC) -> no chat turn in flight.
# Defaults are ON but cost nothing on a laptop: `require_ac: true` and
# `wake_if_ac: false` mean the suite runs only while the owner is plugged in
# AND the model is already loaded because they were using it.  On battery it
# logs a skip and does nothing.  Background work in this codebase NEVER wakes
# the model (idle_suspend_loop's rule); `wake_if_ac` is the one opt-in
# exception and it is gated on AC power in the gate function itself, so no
# combination of flags can wake a model on battery.
#
# ROUTES
#   GET  /api/evals            settings + last run + 60-run history + cases
#   POST /api/evals/run        manual run (refuses on battery, always)
#   POST /api/evals/settings   schedule write-back (clamped, 400 on garbage)

import os
import re
import sys
import json
import time
import sqlite3
import threading
import http.client as _ev_httpclient      # named, so `http` is not rebound
import subprocess
import urllib.request

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
EV_DB = os.path.join(DATA, "evals.db")                       # noqa: F821
EV_CHAT_URL = MODEL_URL.replace("/v1/models",                # noqa: F821
                                "/v1/chat/completions")
EV_CASE_TIMEOUT = 150            # s per completion (covers a cold-ish model)
EV_MAX_TOKENS = 400
EV_HISTORY_LIMIT = 60            # runs returned by GET /api/evals
EV_CUTOFF_MIN = 23 * 60          # after 23:00 a missed slot is stale, not late
EV_SLEPT_GRACE = 120             # minutes past the slot before "stale" applies

EV_DEFAULTS = {"enabled": True, "at_hour": 13, "at_minute": 0,
               "require_ac": True, "wake_if_ac": False, "days": 1}

# ONE table of bounds for the three numeric settings, so the read path
# (evals_settings) and the write path (evals_set_settings) cannot drift.  The
# route used to persist whatever JSON it was handed and clamp only on read, so
# settings.json could sit on at_hour 99 for ever and every other reader of
# the file — a backup, an import, a human — saw a value it would never use.
EV_INT_BOUNDS = {"at_hour": (0, 23), "at_minute": (0, 59), "days": (1, 30)}

# Exceptions that mean "the model server did not answer", never "the model got
# it wrong": urllib.error.URLError and socket.timeout are OSError subclasses,
# http.client raises HTTPException, and a truncated body raises JSONDecodeError
# out of json.loads.
_EV_INFRA_EXC = (OSError, _ev_httpclient.HTTPException, json.JSONDecodeError)


def _ev_log(msg):
    try:
        print("[aux_evals] " + str(msg), file=sys.stderr, flush=True)
    except Exception:
        pass


def _ev_g(name, default=None):
    """Resolve a foreign global BY NAME AT CALL TIME.

    aux files exec in sorted order, so aux_promotion (the drill cases) and
    aux_watchtower (quiet hours) both load AFTER this one.  Capturing either at
    module load would bind None forever.
    """
    v = globals().get(name, default)
    return v if v is not None else default


# --------------------------------------------------------------------------
# settings.json `evals` — read fresh per call (the file is tiny), clamped
# --------------------------------------------------------------------------
def _ev_int(v, lo, hi, dflt):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return dflt
    return lo if n < lo else (hi if n > hi else n)


def _ev_clamp_key(k, v):
    """Clamp one numeric setting into its bounds, falling back to the default."""
    lo, hi = EV_INT_BOUNDS[k]
    return _ev_int(v, lo, hi, EV_DEFAULTS[k])


def _ev_is_num(v):
    """True when `v` is a number this module will accept.  A JSON `true` is
    NOT hour 1 — it is a typo, and the route answers 400 rather than storing
    it."""
    if isinstance(v, bool):
        return False
    try:
        int(v)
    except (TypeError, ValueError):
        return False
    return True


def evals_settings():
    try:
        raw = (get_settings() or {}).get("evals")            # noqa: F821
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        raw = {}
    return {"enabled": bool(raw.get("enabled", EV_DEFAULTS["enabled"])),
            "at_hour": _ev_clamp_key("at_hour", raw.get("at_hour")),
            "at_minute": _ev_clamp_key("at_minute", raw.get("at_minute")),
            "require_ac": bool(raw.get("require_ac",
                                       EV_DEFAULTS["require_ac"])),
            "wake_if_ac": bool(raw.get("wake_if_ac",
                                       EV_DEFAULTS["wake_if_ac"])),
            "days": _ev_clamp_key("days", raw.get("days"))}


def evals_set_settings(patch):
    """Merge + clamp + persist through server.py's settings_update() — one
    locked read-modify-write of settings.json touching only `evals`
    (2026-09-10 audit A01).

    The clamp happens HERE, before the write — clamping only on read left
    settings.json holding values the module would never honour."""
    patch = patch if isinstance(patch, dict) else {}

    def _apply(s):
        cur = s.get("evals")
        cur = dict(cur) if isinstance(cur, dict) else {}
        for k in EV_DEFAULTS:
            if k in patch:
                cur[k] = (_ev_clamp_key(k, patch[k]) if k in EV_INT_BOUNDS
                          else bool(patch[k]))
        s["evals"] = cur

    settings_update(_apply)                                  # noqa: F821
    return evals_settings()


# --------------------------------------------------------------------------
# power.  expand_battery() carries the same `ac` bool, but it also shells out
# to system_profiler AND a Bluetooth scan — far too heavy for a 60s loop — and
# on a Mac with no InternalBattery its regex yields ac=False, which would mean
# a Mac Studio never runs a scheduled eval.  So: the cheap pmset call on its
# own, cached, with "no internal battery" reading as AC, and expand_battery as
# the fallback if pmset is unavailable.
# --------------------------------------------------------------------------
def _ev_power_probe():
    out = ""
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"],
                             capture_output=True, text=True,
                             timeout=3).stdout or ""
    except Exception:
        out = ""
    if out:
        src = re.search(r"drawing from '([^']+)'", out)
        has_bat = "InternalBattery" in out
        pct = None
        m = re.search(r"(\d+)%", out)
        if m:
            pct = int(m.group(1))
        return {"ac": (not has_bat) or bool(src and "AC" in src.group(1)),
                "battery": has_bat, "pct": pct, "source": "pmset"}
    fn = _ev_g("expand_battery")
    if callable(fn):
        try:
            info = fn() or {}
            return {"ac": bool(info.get("ac")), "battery": True,
                    "pct": info.get("pct"), "source": "expand_battery"}
        except Exception:
            pass
    # Unknown power state is treated as BATTERY: the failure mode of a wrong
    # "on AC" is waking a 19GB model on someone's lap.
    return {"ac": False, "battery": True, "pct": None, "source": "unknown"}


def evals_power():
    fn = _ev_g("_cached")
    if callable(fn):
        try:
            return fn("evals_power", 60, _ev_power_probe)
        except Exception:
            pass
    return _ev_power_probe()


# --------------------------------------------------------------------------
# db
# --------------------------------------------------------------------------
_EV_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                REAL NOT NULL,
  model_id          TEXT NOT NULL DEFAULT '',
  trigger           TEXT NOT NULL DEFAULT 'manual',
  total             INTEGER NOT NULL DEFAULT 0,
  passed            INTEGER NOT NULL DEFAULT 0,
  errors            INTEGER NOT NULL DEFAULT 0,
  latency_ms_total  INTEGER NOT NULL DEFAULT 0,
  median_latency_ms INTEGER NOT NULL DEFAULT 0,
  error             TEXT
);
CREATE INDEX IF NOT EXISTS runs_ts ON runs(ts DESC);
CREATE TABLE IF NOT EXISTS results(
  run_id     INTEGER NOT NULL,
  "case"     TEXT NOT NULL,
  passed     INTEGER NOT NULL DEFAULT 0,
  error      INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS results_run ON results(run_id);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

_ev_inited = False
_ev_store_error = ""       # why the db could not be opened; "" when it is fine


def _ev_secure():
    """0600 on the db AND its WAL sidecars — details quote model output."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = EV_DB + suffix
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass


def _ev_conn():
    """A fresh connection (sqlite objects are not thread-portable).

    The file is created by us with 0600 BEFORE sqlite ever sees it — connecting
    first and chmod'ing after leaves a world-readable window.
    """
    try:
        os.makedirs(DATA, mode=0o700, exist_ok=True)         # noqa: F821
    except OSError:
        pass
    if not os.path.exists(EV_DB):
        try:
            os.close(os.open(EV_DB, os.O_CREAT | os.O_EXCL | os.O_RDWR,
                             0o600))
        except FileExistsError:
            pass
    con = sqlite3.connect(EV_DB, timeout=10.0)
    con.execute("PRAGMA busy_timeout=8000")
    con.row_factory = sqlite3.Row
    return con


def _ev_migrate(con):
    """Add the two `error` columns to a db written before they existed.

    CREATE TABLE IF NOT EXISTS never alters a table that is already there, so
    an evals.db from 1.2.3 would keep answering "no such column: errors" on
    every read.  Rows written before the migration mean 0 errors, which is
    exactly what they meant."""
    for table, col, decl in (("runs", "errors", "INTEGER NOT NULL DEFAULT 0"),
                             ("results", "error", "INTEGER NOT NULL DEFAULT 0")):
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
        if cols and col not in cols:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def _ev_init():
    global _ev_inited, _ev_store_error
    if _ev_inited:
        return True
    try:
        con = _ev_conn()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_EV_SCHEMA)
            _ev_migrate(con)
            con.commit()
        finally:
            con.close()
        _ev_secure()
        _ev_inited = True
        _ev_store_error = ""
    except Exception as e:
        # Kept, not just logged: `_ev_tick` refuses to run while it is set and
        # GET /api/evals shows it instead of a card that says "never".
        _ev_store_error = type(e).__name__ + ": " + str(e)[:200]
        _ev_log("db init failed: %r" % e)
    return _ev_inited


def _ev_meta_get(k, default=""):
    try:
        con = _ev_conn()
        try:
            r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default
        finally:
            con.close()
    except Exception:
        return default


def _ev_meta_set(k, v):
    try:
        con = _ev_conn()
        try:
            con.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, str(v)))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        _ev_log("meta write failed: %r" % e)


# --------------------------------------------------------------------------
# model-direct completion (temperature 0).  The drill's `_pro_completion` when
# aux_promotion is loaded — one code path, one set of headers — and an
# identical local fallback so this module still works if the drill is removed.
# --------------------------------------------------------------------------
def _ev_completion(messages, max_tokens=EV_MAX_TOKENS,
                   timeout=EV_CASE_TIMEOUT):
    mid = active_model()                                     # noqa: F821
    fn = _ev_g("_pro_completion")
    if callable(fn):
        return fn(mid, messages, None, max_tokens=max_tokens, timeout=timeout)
    body = {"model": mid, "messages": messages, "temperature": 0,
            "max_tokens": max_tokens}
    req = urllib.request.Request(
        EV_CHAT_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ev_text(resp):
    try:
        return (resp["choices"][0]["message"] or {}).get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


# --------------------------------------------------------------------------
# the three harness-quality checkers.  PURE — they take a response string and
# return (passed, detail), so the scratch harness drives every branch with
# canned good/bad answers and never touches a model.
# --------------------------------------------------------------------------
_EV_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*\n?|\n?\s*```\s*$")
_EV_SEPCELL_RE = re.compile(r"^:?-{2,}:?$")
_EV_MARKER_RE = re.compile(r"^\s*(?:[-*+•]\s+|\d+[.)]\s+)", re.M)
_EV_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]*[A-Z0-9])")


def _ev_strip_fence(text):
    t = str(text or "").strip()
    if "```" in t:
        t = _EV_FENCE_RE.sub("", t).strip()
    return t


def _ev_parse_json_obj(text):
    """Best-effort single JSON object out of a model answer.  None on miss."""
    t = _ev_strip_fence(text)
    if not t:
        return None
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return v
    except (ValueError, TypeError):
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            v = json.loads(t[i:j + 1])
            if isinstance(v, dict):
                return v
        except (ValueError, TypeError):
            pass
    return None


def ev_check_json(text, keys):
    """PASS = the answer parses as ONE JSON object with EXACTLY these keys."""
    obj = _ev_parse_json_obj(text)
    if obj is None:
        return False, "not a JSON object: " + repr(str(text or "")[:120])
    got = sorted(obj.keys())
    want = sorted(keys)
    if got == want:
        return True, "exactly the 3 required keys: " + ", ".join(got)
    missing = [k for k in want if k not in got]
    extra = [k for k in got if k not in want]
    bits = []
    if missing:
        bits.append("missing " + ", ".join(missing))
    if extra:
        bits.append("extra " + ", ".join(extra))
    return False, "wrong keys (" + "; ".join(bits) + ") — got " + \
        ", ".join(got or ["<none>"])


def _ev_split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _ev_norm_col(c):
    return re.sub(r"[*_`]", "", str(c or "")).strip().lower()


def ev_check_table(text, cols, min_rows=3):
    """PASS = a GFM header row + separator + >=min_rows data rows, with the
    requested column names (case- and emphasis-insensitive, in order)."""
    lines = [ln for ln in _ev_strip_fence(text).splitlines()]
    want = [str(c).strip().lower() for c in cols]
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or "|" not in lines[i + 1]:
            continue
        head = _ev_split_row(lines[i])
        sep = _ev_split_row(lines[i + 1])
        if len(sep) != len(head) or not sep:
            continue
        if not all(_EV_SEPCELL_RE.match(c) for c in sep):
            continue
        got = [_ev_norm_col(c) for c in head]
        if got != want:
            return False, "columns are %s, expected %s" % (
                ", ".join(got or ["<none>"]), ", ".join(want))
        rows = 0
        for ln in lines[i + 2:]:
            if "|" not in ln or not ln.strip():
                break
            cells = _ev_split_row(ln)
            if not any(c for c in cells):
                break
            rows += 1
        if rows >= min_rows:
            return True, "header + separator + %d rows (%s)" % (
                rows, ", ".join(want))
        return False, "only %d data row%s under the header (needed %d)" % (
            rows, "" if rows == 1 else "s", min_rows)
    return False, "no GFM table (header + --- separator) found in: " + \
        repr(str(text or "")[:120])


def ev_count_sentences(text):
    t = " ".join(str(text or "").split())
    if not t:
        return 0
    return len([p for p in _EV_SENT_SPLIT_RE.split(t) if p.strip()])


def ev_check_one_sentence(text, max_words=30):
    """PASS = exactly one sentence, <=max_words words, no list markers."""
    raw = str(text or "").strip()
    if not raw:
        return False, "empty answer"
    if _EV_MARKER_RE.search(raw):
        return False, "list markers in a one-sentence answer: " + \
            repr(raw[:120])
    n_sent = ev_count_sentences(raw)
    words = len(raw.split())
    if n_sent != 1:
        return False, "%d sentences, expected 1 (%d words)" % (n_sent, words)
    if words > max_words:
        return False, "one sentence but %d words (limit %d)" % (words,
                                                                max_words)
    return True, "one sentence, %d words" % words


# --------------------------------------------------------------------------
# the three new cases (deterministic, model-direct, no tools)
# --------------------------------------------------------------------------
EV_SYSTEM = ("You are a careful assistant running locally on the user's Mac. "
             "Follow the user's formatting instructions exactly and output "
             "nothing else — no preamble, no commentary, no explanation.")

EV_JSON_KEYS = ["city", "country", "population"]
EV_TABLE_COLS = ["Language", "Year", "Creator"]


def _ev_case_json_strict():
    r = _ev_completion([
        {"role": "system", "content": EV_SYSTEM},
        {"role": "user", "content":
            "Reply with a single JSON object and nothing else. It must have "
            "exactly these three keys and no others: \"city\", \"country\", "
            "\"population\". Fill them in for Tokyo."}])
    return ev_check_json(_ev_text(r), EV_JSON_KEYS)


def _ev_case_table_format():
    r = _ev_completion([
        {"role": "system", "content": EV_SYSTEM},
        {"role": "user", "content":
            "Reply with a GitHub-flavored Markdown table and nothing else. "
            "It must have exactly these three columns, in this order: "
            "Language | Year | Creator. Give exactly three data rows, one "
            "each for Python, Rust and Go."}])
    return ev_check_table(_ev_text(r), EV_TABLE_COLS, 3)


def _ev_case_one_sentence():
    r = _ev_completion([
        {"role": "system", "content": EV_SYSTEM},
        {"role": "user", "content":
            "What is the capital of France and why does it matter? Answer in "
            "one sentence."}], max_tokens=160)
    return ev_check_one_sentence(_ev_text(r), 30)


# --------------------------------------------------------------------------
# the case registry.  The six drill cases are RESOLVED, never copied: change
# them in aux_promotion.py and this suite changes with them.
# --------------------------------------------------------------------------
EV_DRILL_IDS = [
    ("simple_call", "_pro_case_simple", "calls a simple function on request"),
    ("nested_args", "_pro_case_nested", "correct nested arguments"),
    ("restraint", "_pro_case_restraint", "no tool call when none needed"),
    ("chain", "_pro_case_chain", "reads a tool result, then a second call"),
    ("impossible", "_pro_case_impossible",
     "refuses gracefully on an impossible ask"),
    ("deflection_probe", "_pro_case_deflection",
     "really runs a command instead of faking it"),
]

EV_LOCAL_CASES = [
    ("json_strict", "a JSON object with exactly three named keys",
     _ev_case_json_strict),
    ("table_format", "a 3-row Markdown table with the named columns",
     _ev_case_table_format),
    ("one_sentence", "one sentence, at most 30 words, no list markers",
     _ev_case_one_sentence),
]


def evals_cases():
    """[(case_id, label, fn)] — fn(model_id) -> (passed, detail).

    Resolved at CALL time.  `PRO_CASES` (id, label, fn) is preferred because it
    carries the drill's own labels; the per-function fallback keeps the suite
    working if that list is ever renamed.  A drill case that cannot be resolved
    is reported as a failing case rather than silently dropped — a suite that
    quietly shrinks from 9 to 3 would show as a perfect score.
    """
    out = []
    pro = _ev_g("PRO_CASES")
    by_id = {}
    if isinstance(pro, (list, tuple)):
        for row in pro:
            try:
                by_id[row[0]] = (row[1], row[2])
            except Exception:
                pass
    for cid, fname, label in EV_DRILL_IDS:
        hit = by_id.get(cid)
        fn = hit[1] if hit else _ev_g(fname)
        lab = (hit[0] if hit else None) or label
        if callable(fn):
            out.append((cid, lab, fn))
        else:
            out.append((cid, lab, None))
    for cid, label, fn in EV_LOCAL_CASES:
        # the local three take no model id; adapt to the drill's fn(mid) shape
        out.append((cid, label, (lambda f: (lambda _mid: f()))(fn)))
    return out


def _ev_run_case(cid, label, fn, mid):
    """-> {case, passed, error, latency_ms, detail}.  Never raises.

    THREE states.  `passed` is the model's answer.  `error` means there was no
    answer to judge — the model server refused the connection, timed out, or
    sent something that is not JSON — and such a case is NOT a quality
    failure; charting it as one turned "the server was down" into "the model
    got nine cases wrong".  A case that is simply missing (aux_promotion did
    not load) stays a FAILURE on purpose: a suite that quietly shrank from
    nine cases to three would otherwise score a perfect run.
    """
    t0 = time.time()
    if not callable(fn):
        return {"case": cid, "label": label, "passed": False, "error": False,
                "latency_ms": 0,
                "detail": "case unavailable — aux_promotion.py did not load"}
    infra = False
    try:
        passed, detail = fn(mid)
    except Exception as e:
        passed = False
        infra = isinstance(e, _EV_INFRA_EXC)
        detail = ("infrastructure error: " if infra else "case error: ") + \
            type(e).__name__ + ": " + str(e)[:160]
    return {"case": cid, "label": label, "passed": bool(passed),
            "error": bool(infra),
            "latency_ms": int(round((time.time() - t0) * 1000)),
            "detail": str(detail)[:600]}


def _ev_median(vals):
    v = sorted(int(x) for x in vals)
    n = len(v)
    if not n:
        return 0
    return v[n // 2] if n % 2 else int(round((v[n // 2 - 1] + v[n // 2]) / 2))


# --------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------
_EV_LOCK = threading.Lock()
_EV_STATE = {"running": False, "started": 0.0, "note": "", "trigger": "",
             "error": None}


def _ev_busy_jobs():
    try:
        return any(not v.get("done")
                   for v in list(CHAT_JOBS.values()))        # noqa: F821
    except Exception:
        return False


def _ev_store_run(mid, trigger, cases, err, ts=None):
    """Append one run + its per-case rows.  Returns the run id (0 on failure).

    `cases` may be EMPTY, and that is how a day the scheduler skipped is
    recorded: total 0 with the reason in `error`.  Without the row the card
    kept showing the run before it as though it were the latest, and the one
    thing that actually happened — the window was slept through — was written
    nowhere at all.
    """
    if not _ev_init():
        return 0
    lat = [c.get("latency_ms", 0) for c in cases]
    errors = sum(1 for c in cases if c.get("error"))
    if cases and errors == len(cases) and not err:
        err = ("no case reached the model server — "
               + str(cases[0].get("detail", ""))[:240])
    row = (float(ts if ts is not None else time.time()), str(mid or ""),
           str(trigger or "manual"), len(cases),
           sum(1 for c in cases if c.get("passed")), errors,
           int(sum(lat)), _ev_median(lat), (str(err)[:400] if err else None))
    try:
        con = _ev_conn()
        try:
            cur = con.execute(
                "INSERT INTO runs(ts,model_id,trigger,total,passed,errors,"
                "latency_ms_total,median_latency_ms,error) "
                "VALUES(?,?,?,?,?,?,?,?,?)", row)
            rid = int(cur.lastrowid or 0)
            con.executemany(
                'INSERT INTO results(run_id,"case",passed,error,latency_ms,'
                'detail) VALUES(?,?,?,?,?,?)',
                [(rid, c.get("case", ""), 1 if c.get("passed") else 0,
                  1 if c.get("error") else 0,
                  int(c.get("latency_ms", 0)), str(c.get("detail", ""))[:600])
                 for c in cases])
            con.commit()
        finally:
            con.close()
        _ev_secure()
        return rid
    except Exception as e:
        _ev_log("run write failed: %r" % e)
        return 0


def evals_run(model_id=None, trigger="manual", allow_wake=False):
    """Run the 9 cases against the ACTIVE model and record the result.

    NEVER switches models — that is the drill's job and it restarts the model
    server.  `allow_wake` is honoured only by callers that have already proven
    the machine is on AC power; this function does not itself decide that.
    Returns {ok, run_id, model, total, passed, median_latency_ms, cases}
    or {ok: False, reason}.
    """
    mid = active_model()                                     # noqa: F821
    if model_id and model_id != mid:
        return {"ok": False, "reason": "not the active model",
                "active": mid, "asked": model_id}
    try:
        paused = agent_paused()                              # noqa: F821
    except Exception:
        paused = False
    if paused:
        return {"ok": False, "reason": "agent paused"}
    with _EV_LOCK:
        if _EV_STATE.get("running"):
            return {"ok": False, "reason": "a run is already in progress"}
        _EV_STATE.update(running=True, started=time.time(), note="starting",
                         trigger=str(trigger or "manual"), error=None)
    cases = []
    err = None
    try:
        online = False
        try:
            online = model_online()                          # noqa: F821
        except Exception:
            online = False
        if not online:
            if not allow_wake:
                return {"ok": False, "reason": "model asleep",
                        "wakeable": True, "model": mid}
            _EV_STATE["note"] = "waking the model"
            try:
                online = bool(agent_wake(wait=True,           # noqa: F821
                                         timeout=120))
            except Exception as e:
                err = "wake failed: " + type(e).__name__ + ": " + str(e)[:120]
                online = False
            if not online:
                return {"ok": False, "reason": "model asleep",
                        "wakeable": True, "model": mid,
                        "error": err or "the model did not come up"}
        for cid, label, fn in evals_cases():
            _EV_STATE["note"] = "case: " + cid
            cases.append(_ev_run_case(cid, label, fn, mid))
    except Exception as e:
        err = type(e).__name__ + ": " + str(e)[:200]
    finally:
        with _EV_LOCK:
            _EV_STATE.update(running=False, note="", error=err)
    if not cases:
        return {"ok": False, "reason": err or "no cases ran", "model": mid}
    rid = _ev_store_run(mid, trigger, cases, err)
    passed = sum(1 for c in cases if c.get("passed"))
    errors = sum(1 for c in cases if c.get("error"))
    med = _ev_median([c.get("latency_ms", 0) for c in cases])
    # metrics breadcrumb, same shape the drill emits (never raises)
    try:
        rec = _ev_g("metrics_record")
        if callable(rec):
            rec("evals", model=mid, passed=passed, of=len(cases),
                errors=errors, trigger=str(trigger or "manual"),
                median_ms=med, ok=(err is None))
    except Exception:
        pass
    _ev_log("run trigger=%s model=%s %d/%d%s median=%dms%s"
            % (trigger, mid, passed, len(cases),
               (" errors=%d" % errors) if errors else "", med,
               (" error=" + str(err)) if err else ""))
    return {"ok": True, "run_id": rid, "model": mid, "trigger": trigger,
            "total": len(cases), "passed": passed, "errors": errors,
            "median_latency_ms": med,
            "latency_ms_total": sum(c.get("latency_ms", 0) for c in cases),
            "error": err, "cases": cases}


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def _ev_history(limit=EV_HISTORY_LIMIT):
    """Last N runs, returned OLDEST FIRST so the chart can plot them directly."""
    if not _ev_init():
        return []
    try:
        con = _ev_conn()
        try:
            rows = con.execute(
                "SELECT id,ts,model_id,trigger,total,passed,errors,"
                "median_latency_ms,latency_ms_total,error "
                "FROM runs ORDER BY ts DESC, id DESC LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            con.close()
    except Exception as e:
        _ev_log("history read failed: %r" % e)
        return []
    out = [{"id": r["id"], "ts": r["ts"], "model": r["model_id"],
            "trigger": r["trigger"], "total": r["total"],
            "passed": r["passed"], "errors": r["errors"],
            "median_latency_ms": r["median_latency_ms"],
            "latency_ms_total": r["latency_ms_total"],
            "error": r["error"]} for r in rows]
    out.reverse()
    return out


def _ev_last_run():
    if not _ev_init():
        return None, []
    try:
        con = _ev_conn()
        try:
            r = con.execute(
                "SELECT id,ts,model_id,trigger,total,passed,errors,"
                "median_latency_ms,latency_ms_total,error "
                "FROM runs ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
            if not r:
                return None, []
            cs = con.execute(
                'SELECT "case" AS case_id,passed,error,latency_ms,detail '
                "FROM results WHERE run_id=? ORDER BY rowid",
                (r["id"],)).fetchall()
        finally:
            con.close()
    except Exception as e:
        _ev_log("last-run read failed: %r" % e)
        return None, []
    last = {"id": r["id"], "ts": r["ts"], "model": r["model_id"],
            "trigger": r["trigger"], "total": r["total"],
            "passed": r["passed"], "errors": r["errors"],
            "median_latency_ms": r["median_latency_ms"],
            "latency_ms_total": r["latency_ms_total"], "error": r["error"]}
    labels = {cid: lab for cid, lab, _fn in evals_cases()}
    cases = [{"case": c["case_id"], "label": labels.get(c["case_id"], ""),
              "passed": bool(c["passed"]), "error": bool(c["error"]),
              "latency_ms": c["latency_ms"],
              "detail": c["detail"] or ""} for c in cs]
    return last, cases


# --------------------------------------------------------------------------
# the scheduler gate.  PURE: every input is passed in, so the scratch harness
# drives battery/AC x asleep/awake x quiet/not x guard set/not without a Mac
# in the right state.  Returns {run, wake, reason, mark_done}.
# --------------------------------------------------------------------------
def _ev_today(ts=None):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _ev_days_since(date_str, now_ts):
    """Whole local days between a YYYY-MM-DD guard and now.  None if unset."""
    if not date_str:
        return None
    try:
        y, m, d = (int(x) for x in str(date_str).split("-"))
    except Exception:
        return None
    try:
        then = time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))
    except (OverflowError, ValueError):
        return None
    lt = time.localtime(now_ts)
    now_noon = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                            12, 0, 0, 0, 0, -1))
    return int(round((now_noon - then) / 86400.0))


def evals_gate(cfg, guard_date, now_ts, env):
    """The scheduler's decision, as data.

    cfg        : evals_settings() shape
    guard_date : "YYYY-MM-DD" of the last completed/skipped day, or ""
    env        : {quiet, ac, online, paused, busy}
    """
    env = env or {}
    if not cfg.get("enabled", True):
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "disabled"}
    if env.get("quiet"):
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "quiet hours"}
    lt = time.localtime(now_ts)
    cur_min = lt.tm_hour * 60 + lt.tm_min
    sched_min = int(cfg.get("at_hour", 13)) * 60 + int(cfg.get("at_minute", 0))
    if cur_min < sched_min:
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "before %02d:%02d" % (cfg.get("at_hour", 13),
                                                cfg.get("at_minute", 0))}
    days = max(1, int(cfg.get("days", 1)))
    since = _ev_days_since(guard_date, now_ts)
    if since is not None and since < days:
        return {"run": False, "wake": False, "mark_done": False,
                "reason": ("already ran today" if since <= 0 else
                           "ran %d day%s ago (every %d)"
                           % (since, "" if since == 1 else "s", days))}
    # Woke long past the slot: a 1pm suite at 11pm is a stale catch-up, not a
    # late run.  Mark the day done WITHOUT running (the brief tick's rule).
    slept = _ev_g("_slept_through")
    stale = (slept(cur_min, sched_min, EV_CUTOFF_MIN, EV_SLEPT_GRACE)
             if callable(slept) else
             (cur_min >= EV_CUTOFF_MIN and cur_min - sched_min > EV_SLEPT_GRACE))
    if stale:
        return {"run": False, "wake": False, "mark_done": True,
                "reason": "slept through the window"}
    if env.get("paused"):
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "agent paused"}
    ac = bool(env.get("ac"))
    if cfg.get("require_ac", True) and not ac:
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "on battery"}
    if env.get("busy"):
        return {"run": False, "wake": False, "mark_done": False,
                "reason": "a chat turn is running"}
    if env.get("online"):
        return {"run": True, "wake": False, "mark_done": True,
                "reason": "model online"}
    # The ONE wake path, and it is gated on AC here rather than at the call
    # site: no combination of flags can wake a model on battery.
    if cfg.get("wake_if_ac") and ac:
        return {"run": True, "wake": True, "mark_done": True,
                "reason": "waking on AC"}
    return {"run": False, "wake": False, "mark_done": False,
            "reason": "model asleep"}


def _ev_env(cfg):
    """Live inputs for the gate.  Cheap: one cached pmset, two file stats and
    one 3s loopback GET, and the GET is skipped while the model is marked
    asleep (probing a suspended server every 60s is pointless)."""
    quiet = False
    try:
        load = _ev_g("_wt_load")
        inq = _ev_g("_in_quiet")
        if callable(load) and callable(inq):
            quiet = bool(inq(time.time(), (load() or {}).get("quiet_hours",
                                                             {})))
    except Exception:
        quiet = False
    try:
        paused = bool(agent_paused())                        # noqa: F821
    except Exception:
        paused = False
    online = False
    try:
        suspended = bool(agent_idle_suspended())             # noqa: F821
    except Exception:
        suspended = False
    if not suspended and not paused:
        try:
            online = bool(model_online())                    # noqa: F821
        except Exception:
            online = False
    return {"quiet": quiet, "ac": bool(evals_power().get("ac")),
            "online": online, "paused": paused, "busy": _ev_busy_jobs(),
            "suspended": suspended}


_ev_last_reason = ""
_EV_MEM_GUARD = ""        # the guard date in memory, for a store that loses it


def _ev_mark_done(now_ts):
    """Flip BOTH guards for today: the durable meta row and the in-memory copy
    that survives a store which accepts a connection and then loses the
    write."""
    global _EV_MEM_GUARD
    _EV_MEM_GUARD = _ev_today(now_ts)
    _ev_meta_set("last_sched_date", _EV_MEM_GUARD)


def _ev_tick(now_ts=None):
    """One scheduler pass.  Logs one line per DECISION CHANGE — the loop runs
    every 60s and an unchanging "on battery" would be 1,440 lines a day."""
    global _ev_last_reason
    now_ts = time.time() if now_ts is None else now_ts
    cfg = evals_settings()
    if not _ev_init():
        # No store means no once-per-N-days guard: `_ev_meta_get` answers "",
        # `_ev_days_since` reads that as "never ran", and this loop would fire
        # nine completions EVERY MINUTE from the scheduled time until quiet
        # hours while storing nothing.  A model-burning loop is a far worse
        # failure than a missed day, so a suite it cannot record does not run.
        g = {"run": False, "wake": False, "mark_done": False,
             "reason": "store unavailable"}
        if g["reason"] != _ev_last_reason:
            _ev_last_reason = g["reason"]
            _ev_log("schedule: store unavailable (%s)"
                    % (_ev_store_error or "unknown"))
        return g
    # The in-memory guard is consulted ONLY when the persisted one is empty, so
    # a healthy store behaves exactly as before.
    guard = _ev_meta_get("last_sched_date", "") or _EV_MEM_GUARD
    env = _ev_env(cfg)
    g = evals_gate(cfg, guard, now_ts, env)
    if g["reason"] != _ev_last_reason:
        _ev_last_reason = g["reason"]
        _ev_log("schedule: %s (ac=%s online=%s quiet=%s guard=%s)"
                % (g["reason"], env["ac"], env["online"], env["quiet"],
                   guard or "-"))
    if g["mark_done"] and not g["run"]:
        # A day marked done WITHOUT running left no trace anywhere: the card
        # went on showing the previous run as if it were the latest, and the
        # only thing that had happened was invisible.  One row, total 0, the
        # reason in `error` — charted as a gap, shown as "skipped: …".
        try:
            mid = active_model()                             # noqa: F821
        except Exception:
            mid = ""
        _ev_store_run(mid, "skipped", [], g["reason"], ts=now_ts)
        _ev_mark_done(now_ts)
        return g
    if not g["run"]:
        return g
    res = evals_run(trigger="scheduled", allow_wake=g["wake"])
    if res.get("ok"):
        # The guard flips only after a completed run, so a crash mid-suite
        # retries on the next tick instead of skipping the day.
        _ev_mark_done(now_ts)
        _ev_last_reason = ""
    else:
        _ev_log("scheduled run did not start: %s" % res.get("reason"))
    g["result"] = res
    return g


def evals_loop():
    _ev_log("loop started")
    time.sleep(20)          # let the hub finish booting before the first probe
    while True:
        try:
            _ev_tick()
        except Exception as e:
            _ev_log("loop: %r" % e)
        time.sleep(60)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _ev_can_run():
    """(ok, reason) for a MANUAL run right now, and whether waking would help.

    On battery the answer is always no — `force` does not unlock it, because
    the only thing force could buy is a wake, and waking a 19GB model on
    battery is exactly what this feature must never do.
    """
    pw = evals_power()
    if not pw.get("ac"):
        return False, "on battery — plug in to run the suite", False
    try:
        if agent_paused():                                   # noqa: F821
            return False, "the agent is paused", False
    except Exception:
        pass
    if _EV_STATE.get("running"):
        return False, "a run is already in progress", False
    if _ev_busy_jobs():
        return False, "a chat turn is running right now", False
    try:
        if model_online():                                   # noqa: F821
            return True, "", False
    except Exception:
        pass
    return False, "the model is asleep", True


def _ev_get(ctx):
    cfg = evals_settings()
    last, cases = _ev_last_run()
    pw = evals_power()
    ok, reason, wakeable = _ev_can_run()
    return {"ok": True, "settings": cfg, "defaults": EV_DEFAULTS,
            "model": active_model(),                         # noqa: F821
            "cases_total": len(evals_cases()),
            "running": bool(_EV_STATE.get("running")),
            "note": _EV_STATE.get("note") or "",
            "last": last, "results": cases,
            "history": _ev_history(EV_HISTORY_LIMIT),
            "power": {"ac": bool(pw.get("ac")), "battery": pw.get("battery"),
                      "pct": pw.get("pct")},
            "can_run": ok, "reason": reason, "wakeable": wakeable,
            # "" when the db is healthy; the card renders it in place of the
            # "never" that a store which cannot be written would otherwise
            # show for ever.
            "store_error": _ev_store_error,
            "next_guard": _ev_meta_get("last_sched_date", "") or _EV_MEM_GUARD}


def _ev_run_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    force = bool(body.get("force"))
    pw = evals_power()
    if not pw.get("ac"):
        # deliberate: force does NOT override this one
        return {"ok": False, "reason": "on battery",
                "error": "the eval suite does not run on battery — plug in "
                         "and try again"}
    ok, reason, wakeable = _ev_can_run()
    if not ok and not (force and wakeable):
        return {"ok": False, "reason": reason or "not runnable",
                "error": reason, "wakeable": wakeable}
    with _EV_LOCK:
        if _EV_STATE.get("running"):
            return {"ok": False, "reason": "a run is already in progress"}
    def _go():
        try:
            evals_run(trigger="manual", allow_wake=bool(force and wakeable))
        except Exception as e:                               # pragma: no cover
            _ev_log("manual run failed: %r" % e)
    threading.Thread(target=_go, daemon=True).start()
    return {"ok": True, "status": "running",
            "model": active_model(),                         # noqa: F821
            "note": "waking the model, then 9 cases — about a minute"
                    if wakeable else "9 cases against the loaded model — "
                                     "about a minute"}


def _ev_settings_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    patch = {}
    for k in ("enabled", "require_ac", "wake_if_ac"):
        if k in body:
            patch[k] = bool(body.get(k))
    for k in ("at_hour", "at_minute", "days"):
        if k in body:
            v = body.get(k)
            if not _ev_is_num(v):
                return ({"ok": False,
                         "error": "%s must be a number" % k}, 400)
            patch[k] = v
    if not patch:
        return ({"ok": False, "error": "nothing to set"}, 400)
    cfg = evals_set_settings(patch)
    global _ev_last_reason
    _ev_last_reason = ""          # the next tick logs its decision afresh
    return {"ok": True, "settings": cfg}


register_get("/api/evals", _ev_get)                          # noqa: F821
register_post("/api/evals/run", _ev_run_post)                # noqa: F821
register_post("/api/evals/settings", _ev_settings_post)      # noqa: F821


if not globals().get("_ev_thread_started"):
    globals()["_ev_thread_started"] = True
    try:
        _ev_init()
        threading.Thread(target=evals_loop, daemon=True).start()
    except Exception as _ev_e:                               # pragma: no cover
        _ev_log("thread failed to start: %r" % _ev_e)
