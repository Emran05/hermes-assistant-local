# aux_metrics.py — Metrics Baseline (P1.5).
#
# Makes the DEVPLAN "fast / smart / trustworthy" success metrics provable with
# numbers, not vibes.  A lightweight in-process collector (ring buffers) +
# best-effort JSONL persistence in ~/.hermes/metrics/ + GET /api/metrics
# (snapshot + recent history) + POST /api/metrics/count (the P1.2 undo hook).
#
# Zero new deps (stdlib only), zero risk to the chat path (every metric op is
# fully try/except'd — a bug here shows up as MISSING data, never a chat error).
#
# exec'd into server.py globals by the aux loader (after expanders_extra.py and
# the other aux_*.py, before class Handler).  Because it loads LAST it may
# redefine server providers so its wrapped versions win — the same exec-include
# override rule expanders_extra.py uses.  May use these server globals:
#   HOME HERE DATA read_json write_json _cached _widget_cache _state_lock
#   CHAT_JOBS _jobs_lock agent_paused active_model model_online
#   _mlx_footprint_gb hub_data switch_model agent_power register_get
#   register_post uuid
# Imports ALL its own stdlib deps (exec'd code cannot rely on server.py's
# function-local imports) and defines only new names (_met_*, MET_*, metrics_*,
# MeteredJob) — plus the four intentional provider overrides.
#
# What is measured / where it comes from:
#   * TTFT (p50/p95), full turn latency, est tok/s, path, per-turn approvals —
#     from a MeteredJob (dict subclass) that timestamps its own lifecycle as
#     hermes_rpc.run_turn mutates it.  NO run_turn edit needed for the primary
#     numbers (setup_ms/serve_ttft_ms stay null without the optional 1-liner).
#   * Hub API latency — a fail-silent wrapper around hub_data().
#   * RAM envelope (idle/active/paused) — a 120s sampler that reuses the SAME
#     _cached("mlx_ram", 60, _mlx_footprint_gb) entry /api/models already warms,
#     so footprint(1) spawns at most once per 60s machine-wide (never ps RSS).
#   * Model load time — switch/resume arm a watch; a passive 15s model_online()
#     watcher resolves it (or emits trigger:"observed" for unattended restarts).
#   * Approval counts — parsed from the REAL ~/.hermes/dashboard/permissions-log.jsonl.
#   * Undo counts — read from the REAL recorder.db (P1.2), mode=ro.

import collections
import json
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import uuid

# --------------------------------------------------------------------------
# constants / paths
# --------------------------------------------------------------------------
MET_DIR       = os.path.join(HOME, ".hermes", "metrics")
COUNTERS_FILE = os.path.join(MET_DIR, "counters.json")
PERM_LOG      = os.path.join(DATA, "permissions-log.jsonl")   # P1.3 audit log
REC_DB        = os.path.join(DATA, "recorder.db")             # P1.2 flight recorder

MET_SIZE_CAP  = 50 * 1024 * 1024        # 50MB/day -> stop persisting for the day
MET_RETAIN_D  = 30                      # delete daily files older than this
MET_MAX_LINES = 200000                  # per-file parse guard (corrupt giant file)

MET_TARGETS = {"ttft_p50_ms": 1500, "ttft_p95_ms": 3000, "hub_p95_ms": 100,
               "idle_gb": 6, "moe_idle_gb": 20}

MET_RINGS = {
    "turn":       collections.deque(maxlen=256),
    "hub_api":    collections.deque(maxlen=512),
    "ram":        collections.deque(maxlen=720),   # 24h at 120s
    "model_load": collections.deque(maxlen=32),
    "count":      collections.deque(maxlen=128),
}
_MET_LOCK        = threading.Lock()     # guards rings + counters + file append
_MET_COUNTERS    = read_json(COUNTERS_FILE, {}) or {}
if not isinstance(_MET_COUNTERS, dict):
    _MET_COUNTERS = {}
_MET_PERSIST_ERR = None                 # last JSONL-append error message, or None
_MET_LOAD_WATCH  = None                 # {"model":..,"trigger":..,"ts":..} while armed
_MET_MODEL_STATE = {"online": None, "offline_since": None}


def _met_log(msg):
    try:
        print("[aux_metrics] " + str(msg), file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------
# module-load side effects (never let them take the hub down)
# --------------------------------------------------------------------------
try:
    os.makedirs(MET_DIR, mode=0o755, exist_ok=True)
except Exception as _e:                                       # pragma: no cover
    _met_log("mkdir %s failed: %s" % (MET_DIR, _e))


# --------------------------------------------------------------------------
# core collector
# --------------------------------------------------------------------------
def _met_today_file():
    lt = time.localtime()
    return os.path.join(MET_DIR, "metrics-%04d-%02d-%02d.jsonl"
                        % (lt.tm_year, lt.tm_mon, lt.tm_mday))


def _met_persist(rec):
    """Best-effort compact JSONL append to today's file. NEVER raises."""
    global _MET_PERSIST_ERR
    try:
        path = _met_today_file()
        try:
            if os.path.getsize(path) > MET_SIZE_CAP:
                _MET_PERSIST_ERR = "size cap"
                return
        except OSError:
            pass                                   # file absent -> size 0, fine
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            os.makedirs(MET_DIR, exist_ok=True)    # one retry (dir vanished)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        _MET_PERSIST_ERR = None
    except Exception as e:
        _MET_PERSIST_ERR = ("%s: %s" % (type(e).__name__, e))[:80]


def metrics_record(kind, **fields):
    """Ring + JSONL append. NEVER raises."""
    try:
        rec = {"ts": round(time.time(), 3), "kind": kind}
        rec.update(fields)
        with _MET_LOCK:
            MET_RINGS.get(kind, MET_RINGS["count"]).append(rec)
            _met_persist(rec)
    except Exception:
        pass


def metrics_count(name, n=1):
    """Bump a persistent lifetime counter + emit a kind:'count' record.
    NEVER raises. This is the server-side hook P1.2's /undo calls."""
    try:
        try:
            n = int(n)
        except Exception:
            n = 1
        with _MET_LOCK:
            try:
                cur = int(_MET_COUNTERS.get(name, 0) or 0)
            except Exception:
                cur = 0
            cur += n
            _MET_COUNTERS[name] = cur
            try:
                write_json(COUNTERS_FILE, _MET_COUNTERS)
            except Exception:
                pass
        metrics_record("count", name=name, n=n)
        return cur
    except Exception:
        return None


def _met_pctl(vals, p):
    """Nearest-rank percentile of a list; None if empty."""
    xs = sorted(v for v in vals if isinstance(v, (int, float)))
    if not xs:
        return None
    k = max(1, int(math.ceil((p / 100.0) * len(xs))))
    return xs[min(k, len(xs)) - 1]


# --------------------------------------------------------------------------
# MeteredJob — a CHAT_JOBS entry that timestamps its own lifecycle
# --------------------------------------------------------------------------
class MeteredJob(dict):
    """dict.__init__ bypasses __setitem__, so the initial empty fields don't
    count as events; only run_turn's later mutations stamp timings."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.t0 = time.time()
        self.first_token_ts = None
        self.first_approval_ts = None
        self.submitted_ts = None
        self.n_approvals = 0
        self.n_approved = 0
        self.n_denied = 0
        self.recorded = False

    def __setitem__(self, k, v):
        try:
            now = time.time()
            if k == "text" and v and self.first_token_ts is None:
                self.first_token_ts = now
            elif k == "_submitted_ts":
                self.submitted_ts = v
            elif k == "approval" and v:
                self.n_approvals += 1
                if self.first_approval_ts is None:
                    self.first_approval_ts = now
            elif k == "pending_choice":
                if v == "approve":
                    self.n_approved += 1
                elif v == "deny":
                    self.n_denied += 1
            elif k == "done" and v and not self.recorded:
                self.recorded = True
                _met_finish_turn(self, now)
        except Exception:
            pass                                   # metrics NEVER break a turn
        super().__setitem__(k, v)

    def update(self, *a, **kw):
        # CPython dict.update bypasses a subclass __setitem__; run_turn and
        # _chat_worker finish jobs via job.update(...), so route it back.
        for k, v in dict(*a, **kw).items():
            self[k] = v


def _met_finish_turn(job, now):
    """Build + record one kind:'turn' from a completed MeteredJob. Never raises."""
    try:
        t0 = job.t0
        turn_ms = max(0.0, (now - t0) * 1000.0)

        ttft_ms = None
        if job.first_token_ts is not None:
            ttft_ms = max(0.0, (job.first_token_ts - t0) * 1000.0)

        setup_ms = None
        serve_ttft_ms = None
        if job.submitted_ts is not None:
            setup_ms = max(0.0, (job.submitted_ts - t0) * 1000.0)
            if ttft_ms is not None:
                serve_ttft_ms = max(0.0, ttft_ms - setup_ms)

        decode_ms = None
        est_tok_per_sec = None
        text = job.get("reply") or job.get("text") or ""
        est_tokens_out = len(text) // 4
        if job.first_token_ts is not None:
            decode_ms = max(0.0, (now - job.first_token_ts) * 1000.0)
            if decode_ms > 200:
                est_tok_per_sec = round(est_tokens_out / (decode_ms / 1000.0), 1)

        # path: the only route to the one-shot fallback is that exact status
        # string (_chat_worker sets it before run_agent). Everything else is the
        # serve backend. _submitted_ts (if the optional hook lands) confirms serve.
        status = job.get("status") or ""
        if job.submitted_ts is not None:
            path = "serve"
        elif status == "serve backend unavailable, using one-shot mode":
            path = "oneshot"
        else:
            path = "serve"

        ttft_clean = (job.first_token_ts is None or job.first_approval_ts is None
                      or job.first_token_ts < job.first_approval_ts)
        ok = bool(job.get("ok"))

        try:
            model = _cached("metrics_model", 60, active_model)
        except Exception:
            model = ""

        metrics_record(
            "turn", job=str(job.get("id") or "")[:12],
            ttft_ms=(round(ttft_ms) if ttft_ms is not None else None),
            setup_ms=(round(setup_ms) if setup_ms is not None else None),
            serve_ttft_ms=(round(serve_ttft_ms) if serve_ttft_ms is not None else None),
            turn_ms=round(turn_ms), decode_ms=(round(decode_ms) if decode_ms is not None else None),
            est_tokens_out=est_tokens_out, est_tok_per_sec=est_tok_per_sec,
            path=path, ok=ok, ttft_clean=ttft_clean,
            approvals=job.n_approvals, approved=job.n_approved, denied=job.n_denied,
            model=model)

        metrics_count("turns", 1)
        if not ok:
            metrics_count("turns_err", 1)
    except Exception as e:                                    # pragma: no cover
        _met_log("finish_turn: %r" % e)


def _new_job(session):
    """Redefines server.py's inline _new_job (verbatim behaviour) so every hub
    chat turn is a MeteredJob. /api/chat resolves _new_job by name at call time,
    so this override wins (exec-include order rule)."""
    jid = uuid.uuid4().hex[:12]
    job = MeteredJob({"id": jid, "session": session, "state": "running",
                      "text": "", "status": "", "approval": None, "reply": "",
                      "ok": False, "done": False, "ts": time.time()})
    with _jobs_lock:
        for k in [k for k, v in CHAT_JOBS.items()
                  if v.get("done") and time.time() - v.get("ts", 0) > 3600]:
            CHAT_JOBS.pop(k, None)
        CHAT_JOBS[jid] = job
    return job


# --------------------------------------------------------------------------
# model-load watch
# --------------------------------------------------------------------------
def _met_arm_load_watch(model, trigger):
    globals()["_MET_LOAD_WATCH"] = {"model": model, "trigger": trigger,
                                    "ts": time.time()}


def _met_emit_load(model, trigger, ms):
    try:
        metrics_record("model_load", ms=(round(ms) if isinstance(ms, (int, float)) else None),
                       model=model or "", trigger=trigger)
        metrics_count("model_loads", 1)
    except Exception:
        pass


# --------------------------------------------------------------------------
# provider overrides (guarded so a re-exec can never wrap-of-wrap)
# --------------------------------------------------------------------------
if not globals().get("_met_wrapped"):
    globals()["_met_wrapped"] = True

    _met_orig_hub_data = hub_data

    def hub_data():
        t0 = time.perf_counter()
        try:
            return _met_orig_hub_data()
        finally:
            try:
                metrics_record("hub_api",
                               ms=round((time.perf_counter() - t0) * 1000.0, 1))
            except Exception:
                pass

    _met_orig_switch_model = switch_model

    def switch_model(mid):
        out = _met_orig_switch_model(mid)
        try:
            if isinstance(out, dict) and out.get("ok") and out.get("loading"):
                _met_arm_load_watch(mid, "switch")
        except Exception:
            pass
        return out

    _met_orig_agent_power = agent_power

    def agent_power(action):
        out = _met_orig_agent_power(action)
        try:
            if action == "resume" and isinstance(out, dict) and out.get("ok") \
                    and out.get("loading"):
                _met_arm_load_watch(active_model(), "resume")
        except Exception:
            pass
        return out


# --------------------------------------------------------------------------
# sampler thread — 15s tick: model-online watch; every 8th tick (120s) a RAM
# sample; daily JSONL GC.  RAM never spikes: it reuses the shared 60s cache.
# --------------------------------------------------------------------------
def _met_chat_active():
    try:
        now = time.time()
        for v in list(CHAT_JOBS.values()):
            if not v.get("done"):
                return True
            if now - (v.get("ts") or 0) < 120:
                return True
    except Exception:
        pass
    return False


def _met_sample_ram():
    try:
        if agent_paused():
            metrics_record("ram", gb=None, state="paused")
            return
        gb = None
        try:
            gb = _cached("mlx_ram", 60, _mlx_footprint_gb)
        except Exception:
            gb = None
        state = "active" if _met_chat_active() else "idle"
        metrics_record("ram",
                       gb=(round(gb, 2) if isinstance(gb, (int, float)) else None),
                       state=state)
    except Exception:
        pass


def _met_watch_model():
    """Resolve an armed load watch; else emit a passive 'observed' load on an
    offline->online transition. Never emits while paused."""
    now = time.time()
    paused = agent_paused()
    online = False if paused else bool(model_online())
    prev = _MET_MODEL_STATE.get("online")

    watch = globals().get("_MET_LOAD_WATCH")
    if watch and now - watch.get("ts", now) > 900:        # discard stale (>15min)
        globals()["_MET_LOAD_WATCH"] = None
        watch = None

    if prev is None:                                       # first tick: seed only
        _MET_MODEL_STATE["online"] = online
        _MET_MODEL_STATE["offline_since"] = None if online else now
        return

    if (not prev) and online:                              # offline -> online
        if watch:
            _met_emit_load(watch.get("model"), watch.get("trigger", "switch"),
                           (now - watch.get("ts", now)) * 1000.0)
            globals()["_MET_LOAD_WATCH"] = None
        elif not paused:
            since = _MET_MODEL_STATE.get("offline_since")
            _met_emit_load(active_model(), "observed",
                           (now - since) * 1000.0 if since else None)
        _MET_MODEL_STATE["offline_since"] = None
    elif prev and (not online):                            # online -> offline
        _MET_MODEL_STATE["offline_since"] = now

    _MET_MODEL_STATE["online"] = online


def _met_gc_files():
    try:
        cutoff = time.time() - MET_RETAIN_D * 86400
        for e in os.scandir(MET_DIR):
            if not (e.name.startswith("metrics-") and e.name.endswith(".jsonl")):
                continue
            try:
                if e.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.remove(e.path)
            except OSError:
                pass
    except OSError:
        pass


def metrics_sampler_loop():
    try:
        _met_gc_files()
        _met_sample_ram()                                  # immediate first sample
    except Exception:
        pass
    tick = 0
    while True:
        try:
            time.sleep(15)
            tick += 1
            _met_watch_model()
            if tick % 8 == 0:                              # 120s
                _met_sample_ram()
            if tick % (8 * 720) == 0:                      # ~daily
                _met_gc_files()
        except Exception as e:                             # pragma: no cover
            _met_log("sampler: %r" % e)
            time.sleep(15)


# --------------------------------------------------------------------------
# derived reads from the real P1.2 / P1.3 files
# --------------------------------------------------------------------------
def _met_approval_counts():
    def build():
        c = {"asked": 0, "auto-approved": 0, "auto-denied": 0,
             "user-approve": 0, "user-deny": 0}
        try:
            if os.path.exists(PERM_LOG):
                with open(PERM_LOG, encoding="utf-8") as f:
                    lines = f.readlines()[-5000:]
                for ln in lines:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    a = r.get("action")
                    if a in c:
                        c[a] += 1
        except Exception:
            pass
        requested = c["asked"] + c["auto-approved"] + c["auto-denied"]
        approved = c["auto-approved"] + c["user-approve"]
        denied = c["auto-denied"] + c["user-deny"]
        pending = max(0, c["asked"] - c["user-approve"] - c["user-deny"])
        return {"requested": requested, "approved": approved, "denied": denied,
                "pending": pending, "source": "permissions-log"}
    try:
        return _cached("metrics_approvals", 20, build)
    except Exception:
        return build()


def _met_undo_counts():
    def build():
        out = {"count": 0, "undone_actions": 0}
        if not os.path.exists(REC_DB):
            return out
        try:
            uri = "file:" + urllib.parse.quote(REC_DB) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
            try:
                out["count"] = con.execute(
                    "SELECT COUNT(*) FROM actions WHERE tool='undo'").fetchone()[0]
                out["undone_actions"] = con.execute(
                    "SELECT COUNT(*) FROM actions WHERE status='undone'").fetchone()[0]
            finally:
                con.close()
        except Exception:
            pass
        return out
    try:
        return _cached("metrics_undo", 30, build)
    except Exception:
        return build()


# --------------------------------------------------------------------------
# window loading (days>1 merges parsed daily files, memoized 300s)
# --------------------------------------------------------------------------
def _met_parse_window(days):
    """Parse the last `days` daily files into per-kind record lists."""
    out = {"turn": [], "hub_api": [], "ram": [], "model_load": []}
    lt = time.localtime()
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    for i in range(days):
        day = time.localtime(base - i * 86400)
        path = os.path.join(MET_DIR, "metrics-%04d-%02d-%02d.jsonl"
                            % (day.tm_year, day.tm_mon, day.tm_mday))
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for n, ln in enumerate(f):
                    if n > MET_MAX_LINES:
                        break
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue                      # torn write costs one line
                    k = r.get("kind")
                    if k in out:
                        out[k].append(r)
        except OSError:
            continue
    return out


def _met_stats_from_turns(T):
    ttft = [r.get("ttft_ms") for r in T
            if r.get("ttft_clean") and isinstance(r.get("ttft_ms"), (int, float))]
    turn = [r.get("turn_ms") for r in T if isinstance(r.get("turn_ms"), (int, float))]
    setup = [r.get("setup_ms") for r in T if isinstance(r.get("setup_ms"), (int, float))]
    tps = [r.get("est_tok_per_sec") for r in T
           if isinstance(r.get("est_tok_per_sec"), (int, float))]
    paths = collections.Counter(r.get("path") for r in T)
    return {
        "n": len(T),
        "err": sum(1 for r in T if not r.get("ok")),
        "ttft_ms": {"p50": _met_pctl(ttft, 50), "p90": _met_pctl(ttft, 90),
                    "p95": _met_pctl(ttft, 95), "n": len(ttft)},
        "turn_ms": {"p50": _met_pctl(turn, 50), "p90": _met_pctl(turn, 90),
                    "p95": _met_pctl(turn, 95), "n": len(turn)},
        "setup_ms": {"p50": _met_pctl(setup, 50), "n": len(setup)},
        "est_tok_per_sec": {"p50": _met_pctl(tps, 50), "n": len(tps)},
        "paths": {"serve": paths.get("serve", 0), "oneshot": paths.get("oneshot", 0)},
    }


def _met_ram_stats(ram_list):
    last = ram_list[-1] if ram_list else None
    idle = [r.get("gb") for r in ram_list
            if r.get("state") == "idle" and isinstance(r.get("gb"), (int, float))]
    active = [r.get("gb") for r in ram_list
              if r.get("state") == "active" and isinstance(r.get("gb"), (int, float))]
    out = {"last": None, "idle_gb_p95": _met_pctl(idle, 95),
           "active_gb_p95": _met_pctl(active, 95), "samples": len(ram_list)}
    if last:
        out["last"] = {"gb": last.get("gb"), "state": last.get("state"),
                       "ts": last.get("ts")}
    else:                                            # ring cold: peek shared cache
        try:
            hit = _widget_cache.get("mlx_ram")
        except Exception:
            hit = None
        if hit:
            gbv = hit[1]
            out["last"] = {
                "gb": (round(gbv, 2) if isinstance(gbv, (int, float)) else None),
                "state": ("paused" if agent_paused()
                          else ("active" if _met_chat_active() else "idle")),
                "ts": hit[0], "cached": True}
    return out


# --------------------------------------------------------------------------
# GET /api/metrics
# --------------------------------------------------------------------------
def metrics_payload(ctx=None):
    try:
        days = 1
        if ctx is not None and hasattr(ctx, "q1"):
            try:
                days = int(ctx.q1("days", "1"))
            except Exception:
                days = 1
        days = max(1, min(30, days))

        if days <= 1:
            with _MET_LOCK:
                turns = list(MET_RINGS["turn"])
                hub = list(MET_RINGS["hub_api"])
                ram_list = list(MET_RINGS["ram"])
                loads = list(MET_RINGS["model_load"])
        else:
            def _win():
                w = _met_parse_window(days)
                # fold in the still-in-ring tail (records may not be persisted
                # yet, or persistence may be degraded)
                with _MET_LOCK:
                    for k in ("turn", "hub_api", "ram", "model_load"):
                        seen = {id(x) for x in w[k]}
                        for r in MET_RINGS[k]:
                            if id(r) not in seen:
                                w[k].append(r)
                return w
            try:
                w = _cached(("metrics_win", days), 300, _win)
            except Exception:
                w = _win()
            turns, hub, ram_list, loads = (w["turn"], w["hub_api"],
                                           w["ram"], w["model_load"])

        hub_ms = [r.get("ms") for r in hub if isinstance(r.get("ms"), (int, float))]
        last_load = loads[-1] if loads else None

        try:
            active = _cached("metrics_model", 60, active_model)
        except Exception:
            active = ""

        approvals = _met_approval_counts()
        undo = _met_undo_counts()

        counters = dict(_MET_COUNTERS)
        counters.setdefault("undo", 0)
        # surface the authoritative recorder count as the lifetime undo total
        counters["undo"] = max(int(counters.get("undo", 0) or 0), undo.get("count", 0))

        return {
            "ok": True,
            "window_days": days,
            "since": round(time.time() - days * 86400, 1),
            "persist_error": _MET_PERSIST_ERR,
            "turns": _met_stats_from_turns(turns),
            "hub_api": {"p50": _met_pctl(hub_ms, 50), "p95": _met_pctl(hub_ms, 95),
                        "n": len(hub_ms)},
            "ram": _met_ram_stats(ram_list),
            "model": {
                "active": active,
                "last_load": ({"ms": last_load.get("ms"),
                               "trigger": last_load.get("trigger"),
                               "ts": last_load.get("ts")} if last_load else None),
                "loads": int(_MET_COUNTERS.get("model_loads", 0) or 0),
            },
            "approvals": approvals,
            "undo": {"count": undo.get("count", 0),
                     "undone_actions": undo.get("undone_actions", 0),
                     "counter": int(_MET_COUNTERS.get("undo", 0) or 0)},
            "counters": counters,
            "targets": MET_TARGETS,
        }
    except Exception as e:
        return {"ok": False, "error": "internal: " + str(e), "targets": MET_TARGETS}


# --------------------------------------------------------------------------
# POST /api/metrics/count  (the inert write surface: bump one counter)
# --------------------------------------------------------------------------
_MET_NAME_RE = None
try:
    import re as _met_re
    _MET_NAME_RE = _met_re.compile(r"^[a-z0-9_.-]{1,40}$")
except Exception:
    _MET_NAME_RE = None


def metrics_count_api(ctx):
    try:
        body = (ctx.body if ctx is not None and hasattr(ctx, "body") else ctx) or {}
        if not isinstance(body, dict):
            body = {}
        name = str(body.get("name") or "")
        if not name or (_MET_NAME_RE and not _MET_NAME_RE.match(name)):
            return ({"ok": False, "error": "bad name"}, 400)
        n = body.get("n", 1)
        try:
            n = int(n)
        except Exception:
            return ({"ok": False, "error": "bad n"}, 400)
        if n < 1 or n > 1000:
            return ({"ok": False, "error": "bad n"}, 400)
        total = metrics_count(name, n)
        return {"ok": True, "name": name, "total": total}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 400)


# --------------------------------------------------------------------------
# wiring — routes + sampler thread (guarded, like aux_recorder.py)
# --------------------------------------------------------------------------
register_get("/api/metrics", metrics_payload)
register_post("/api/metrics/count", metrics_count_api)

if not globals().get("_metrics_thread_started"):
    globals()["_metrics_thread_started"] = True
    try:
        threading.Thread(target=metrics_sampler_loop, daemon=True).start()
    except Exception as _e:                                    # pragma: no cover
        _met_log("sampler thread failed to start: %r" % _e)
