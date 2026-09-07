# ==========================================================================
# aux_trace.py — tracing export (1.2.3)
#
# Everything needed to see a turn the way a tracing UI sees it is already on
# this Mac, in four stores that were never joined:
#
#   * ~/.hermes/metrics/metrics-YYYY-MM-DD.jsonl   one kind:"turn" record per
#     dashboard chat turn (aux_metrics._met_finish_turn) — job id, ttft, turn
#     duration, path, approvals, model.
#   * ~/.hermes/dashboard/recorder.db `actions`    one row per tool call, from
#     every surface (aux_recorder) — tool, kind, target, status, duration.
#   * ~/.hermes/logs/mlx-server.log                prompt/cached/new tokens,
#     prefill seconds, decode tok/s (aux_context._cx_rows/_cx_measure).
#   * ~/.hermes/dashboard/tool-budget.jsonl        one line per truncated tool
#     result (the tool-budget plugin).
#
# This module joins them into traces and hands them out as a file, in two
# shapes: `jsonl` (one flat object per line, dotted attribute keys, for jq)
# and `otel` (OTLP/JSON, for Jaeger / Tempo / OpenObserve).
#
# THE JOIN IS BY TIMESTAMP WINDOW, and it has to be.  There is no shared id:
# the metrics record carries the dashboard JOB id, the Recorder's `session`
# column means Hermes-agent's own session id on `statedb`/`ws` rows and
# whatever the caller passed on `dashboard` rows, and there is no job_id
# column at all.  So a turn's window is [ts - turn_ms, ts] and a Recorder row
# belongs to the turn whose window contains it — the same technique
# aux_context._cx_job_window already uses to attach model-server log lines to
# a job.  Recorder rows that fall in no turn window are still exported: they
# are clustered on their own session + a time gap and given a SYNTHETIC parent
# span, because a Telegram or CLI turn writes Recorder rows and no metrics row
# at all, and dropping them would make the export look like the dashboard is
# the only thing that runs tools.
#
# Session (and therefore the trace grouping) is resolved three ways, in order:
# the live CHAT_JOBS entry while the job is still in memory (that is also the
# only place `memory_chars` survives), else the chat store — the user message
# is saved microseconds before the job is created, so the newest user message
# at or just before the turn's start names the conversation — else the job id
# alone.  A trace is one conversation on one local day.
#
# REDACTION.  Raw tool args NEVER leave this process: the export carries
# `target` and `summary`, both scrubbed, and no `args` field in any shape.
# The scrubber is aux_convos.py's `_cv_redact` — resolved BY NAME at request
# time, never copied.  hermes_mcp.py has to copy it because it runs in another
# process; this module does not, and a second copy is a second thing to drift.
# If that function is missing the export REFUSES rather than shipping
# unscrubbed text.
#
# AUX MODULE GOTCHA: aux files exec into server.py's globals, so a bare
# `from datetime import datetime` would rebind the global `datetime` module
# name to the class.  Private aliases only.
#
# Load order: aux files exec SORTED, so aux_context/aux_convos/aux_metrics/
# aux_recorder/aux_toolbudget are all in globals before this file runs and
# aux_update is not — every foreign global is therefore resolved BY NAME at
# request time (`_tr_g`) with a local literal as the fallback, the discipline
# aux_index/aux_needsyou document.
#
# Routes:
#   GET /api/trace/export?since=&until=&format=jsonl|otel  -> file download
#   GET /api/trace/summary?since=&until=                   -> counts + totals
# ==========================================================================
import bisect as _tr_bisect
import datetime as _tr_datetime
import hashlib as _tr_hashlib
import json as _tr_json
import os as _tr_os
import sqlite3 as _tr_sqlite3
import sys as _tr_sys
import time as _tr_time
import urllib.parse as _tr_urlparse

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
_TR_MAX_DAYS = 31                 # hard cap on one export's range
_TR_MAX_TURN_S = 1800.0           # same clamp aux_context uses for a turn
_TR_MAX_SPANS = 50000             # the body is built in memory: stop, say so
_TR_MAX_ACTIONS_PER_DAY = 20000   # LIMIT on the Recorder query (it has no TTL)
_TR_TB_TAIL_BYTES = 8 * 1024 * 1024   # tail of tool-budget.jsonl to consider

# The user message is written by /api/chat immediately before _new_job(), so
# its ts sits a hair BEFORE the turn's t0. A few seconds of slack covers a slow
# save_chat; the "after" side is tight so the next turn cannot be claimed.
_TR_SESSION_SLACK_BEFORE = 8.0
_TR_SESSION_SLACK_AFTER = 2.0

_TR_ACTION_SLACK = 1.0            # a tool row may land a hair outside the window
_TR_ORPHAN_GAP_S = 120.0          # gap that splits two synthetic turns
_TR_SUMMARY_CAP = 200             # chars of `summary` kept per tool span
_TR_TARGET_CAP = 300              # chars of `target` kept per tool span

_TR_ID_SALT = "hermes-trace/v1"   # so ids are stable across runs and machines
_TR_SERVICE = "hermes-assistant"
_TR_SCOPE = "hermes.dashboard.trace"

# OTLP SpanKind (opentelemetry/proto/trace/v1): 1 INTERNAL, 3 CLIENT.
_TR_KIND_INTERNAL = 1
_TR_KIND_CLIENT = 3
# OTLP StatusCode: 0 UNSET, 1 OK, 2 ERROR.
_TR_STATUS_OK = 1
_TR_STATUS_ERROR = 2

# Recorder kinds that talk to something outside this Mac -> CLIENT spans.
_TR_CLIENT_KINDS = ("net",)


def _tr_log(msg):
    try:
        print("[aux_trace] " + str(msg), file=_tr_sys.stderr)
    except Exception:
        pass


def _tr_g(name, default=None):
    """A foreign global, resolved at CALL time (never captured at load)."""
    return globals().get(name, default)


# --------------------------------------------------------------------------
# paths + version
# --------------------------------------------------------------------------
def _tr_met_dir():
    return _tr_g("MET_DIR") or _tr_os.path.join(HOME, ".hermes", "metrics")   # noqa: F821


def _tr_rec_db():
    return _tr_g("REC_DB") or _tr_os.path.join(DATA, "recorder.db")           # noqa: F821


def _tr_tb_log():
    return _tr_g("_TB_LOG") or _tr_os.path.join(DATA, "tool-budget.jsonl")    # noqa: F821


def _tr_chats_dir():
    return _tr_g("CHATS") or _tr_os.path.join(DATA, "chats")                  # noqa: F821


def _tr_version():
    """The repo VERSION — aux_update's reader when it has loaded, else ours."""
    fn = _tr_g("_upd_version")
    if callable(fn):
        try:
            v = fn()
            if v:
                return str(v)
        except Exception:
            pass
    try:
        root = _tr_os.path.dirname(HERE)                                      # noqa: F821
        with open(_tr_os.path.join(root, "VERSION")) as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


# --------------------------------------------------------------------------
# redaction — aux_convos.py's scrubber, by name, never copied
# --------------------------------------------------------------------------
def _tr_scrubber():
    fn = _tr_g("_cv_redact")
    return fn if callable(fn) else None


def _tr_clean(text, cap=0):
    """Control characters out, whitespace squeezed, optionally capped.
    NOT a redactor — `_tr_redact` is the only thing that removes secrets."""
    if text is None:
        return ""
    s = "".join(" " if (ord(c) < 0x20 or ord(c) == 0x7F) else c for c in str(text))
    s = " ".join(s.split())
    if cap and len(s) > cap:
        s = s[:cap - 1] + "…"
    return s


def _tr_redact(text, cap=0):
    """Scrub, THEN clean+cap. Order matters: a PEM block is multi-line and the
    cleaner would flatten it into something the multi-line rule no longer
    matches, so the scrubber has to see the original bytes."""
    fn = _tr_scrubber()
    if fn is None:                       # the route refuses before we get here
        raise RuntimeError("secret scrubber unavailable")
    return _tr_clean(fn(str(text if text is not None else "")), cap)


# --------------------------------------------------------------------------
# ids — deterministic, hex, never all-zero (OTLP forbids that)
# --------------------------------------------------------------------------
def _tr_id(nbytes, *parts):
    raw = _TR_ID_SALT + "|" + "|".join("" if p is None else str(p) for p in parts)
    h = _tr_hashlib.sha256(raw.encode("utf-8")).hexdigest()[:nbytes * 2]
    return ("1" * len(h)) if h.strip("0") == "" else h


def _tr_trace_id(*parts):
    return _tr_id(16, "trace", *parts)


def _tr_span_id(*parts):
    return _tr_id(8, "span", *parts)


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
def _tr_num(v, default=None):
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _tr_int(v, default=None):
    n = _tr_num(v, None)
    return default if n is None else int(n)


def _tr_nano(t):
    """OTLP fixed64 nanoseconds — a JSON STRING, per the proto3 JSON mapping."""
    try:
        return str(int(round(float(t) * 1e9)))
    except (TypeError, ValueError):
        return "0"


def _tr_days(since, until):
    """[(date, lo, hi)] — one entry per LOCAL day the range touches, each
    clipped to the range. Built with datetime.combine rather than +86400 so a
    DST day is still one day."""
    out = []
    d = _tr_datetime.date.fromtimestamp(since)
    last = _tr_datetime.date.fromtimestamp(until)
    one = _tr_datetime.timedelta(days=1)
    guard = 0
    while d <= last and guard <= _TR_MAX_DAYS + 1:
        guard += 1
        start = _tr_datetime.datetime.combine(d, _tr_datetime.time.min).timestamp()
        end = _tr_datetime.datetime.combine(d + one, _tr_datetime.time.min).timestamp()
        lo, hi = max(start, since), min(end, until)
        if hi > lo:
            out.append((d, lo, hi))
        d += one
    return out


# --------------------------------------------------------------------------
# source 1 — per-turn metrics JSONL
# --------------------------------------------------------------------------
def _tr_metrics_turns(day, lo, hi):
    """kind:"turn" records of one local day, inside [lo, hi], oldest first.

    `ts` on the record is the moment the turn FINISHED (_met_finish_turn runs
    on job["done"]=True), so the window is [ts - turn_ms/1000, ts] and a turn
    is in the export when it ENDED inside the range."""
    path = _tr_os.path.join(_tr_met_dir(),
                            "metrics-%04d-%02d-%02d.jsonl" % (day.year, day.month, day.day))
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"turn"' not in line:
                    continue
                try:
                    r = _tr_json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("kind") != "turn":
                    continue
                ts = _tr_num(r.get("ts"))
                if ts is None or ts < lo or ts > hi:
                    continue
                out.append(r)
    except OSError:
        return out
    out.sort(key=lambda r: r.get("ts") or 0)
    return out


# --------------------------------------------------------------------------
# source 2 — Flight Recorder actions
# --------------------------------------------------------------------------
_TR_ACTION_COLS = ("id", "ts", "session", "source", "tool", "target", "kind",
                   "reversible", "status", "duration_s", "summary", "undone_ts",
                   "origin")


def _tr_actions(lo, hi):
    """Recorder rows in [lo, hi], oldest first. `args` is deliberately NOT
    selected — raw tool arguments never leave this process."""
    db = _tr_rec_db()
    if not _tr_os.path.exists(db):
        return []
    try:
        uri = "file:" + _tr_urlparse.quote(db) + "?mode=ro"
        con = _tr_sqlite3.connect(uri, uri=True, timeout=5.0)
    except Exception:
        return []
    try:
        con.row_factory = _tr_sqlite3.Row
        rows = con.execute(
            "SELECT " + ",".join(_TR_ACTION_COLS) + " FROM actions "
            "WHERE ts>=? AND ts<=? ORDER BY ts LIMIT ?",
            (lo, hi, _TR_MAX_ACTIONS_PER_DAY)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _tr_log("recorder read: %r" % e)
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# source 3 — tool-budget truncations
# --------------------------------------------------------------------------
def _tr_tb_epoch(v):
    """The plugin writes `datetime.now().isoformat(timespec="seconds")` — a
    NAIVE local timestamp, so .timestamp() reads it back in local time."""
    try:
        return _tr_datetime.datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def _tr_truncations(since, until):
    """[(epoch, row)] for every truncation in the range, oldest first."""
    path = _tr_tb_log()
    out = []
    try:
        size = _tr_os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > _TR_TB_TAIL_BYTES:
                fh.seek(size - _TR_TB_TAIL_BYTES)
                fh.readline()                     # drop the partial first line
            blob = fh.read()
    except OSError:
        return out
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _tr_json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ep = _tr_tb_epoch(row.get("ts"))
        if ep is None or ep < since or ep > until:
            continue
        out.append((ep, row))
    out.sort(key=lambda p: p[0])
    return out


# --------------------------------------------------------------------------
# source 4 — the chat store, as a session index
# --------------------------------------------------------------------------
def _tr_session_index(since, until):
    """Sorted [(ts, session)] of every USER message in the range (plus slack).

    /api/chat appends the user message and calls save_chat BEFORE _new_job, so
    the newest user message at or just before a turn's start names the
    conversation that turn belongs to."""
    lo = since - _TR_SESSION_SLACK_BEFORE - 60.0
    hi = until + _TR_SESSION_SLACK_AFTER + 60.0
    chats = _tr_chats_dir()
    prewarm = _tr_g("PREWARM_SESSION", "__prewarm__")
    sess_re = _tr_g("SESSION_RE")
    loader = _tr_g("load_chat")
    out = []
    try:
        names = _tr_os.listdir(chats)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        if sid == prewarm:
            continue
        if sess_re is not None and not sess_re.match(sid):
            continue
        path = _tr_os.path.join(chats, fn)
        try:
            if _tr_os.path.getmtime(path) < lo:
                continue                          # nothing in range, skip the read
        except OSError:
            continue
        try:
            chat = loader(sid) if callable(loader) else _tr_json.load(open(path))
        except Exception:
            continue
        for m in (chat.get("messages") or []) if isinstance(chat, dict) else []:
            if not isinstance(m, dict) or (m.get("role") or "") != "user":
                continue
            ts = _tr_num(m.get("ts"))
            if ts is None or ts < lo or ts > hi:
                continue
            out.append((ts, sid))
    out.sort(key=lambda p: p[0])
    return out


def _tr_session_at(index, keys, start):
    """The conversation a turn starting at `start` belongs to, or ''."""
    if not index:
        return ""
    i = _tr_bisect.bisect_right(keys, start + _TR_SESSION_SLACK_AFTER)
    if i == 0:
        return ""
    ts, sid = index[i - 1]
    return sid if ts >= start - _TR_SESSION_SLACK_BEFORE else ""


# --------------------------------------------------------------------------
# source 5 — aux_context's model-server numbers for a window
# --------------------------------------------------------------------------
_TR_CX_KEYS = ("prompt_tokens", "cached_tokens", "new_tokens", "prefill_s",
               "decode_tps", "generated_tokens", "pct_of_window", "compacted",
               "requests")


def _tr_context(start, end):
    """{prompt_tokens, cached_tokens, prefill_s, pct_of_window, compacted, …}
    for a turn window, or {} when the model-server log cannot answer.

    aux_context reads only the TAIL of ~/.hermes/logs/mlx-server.log, so this
    enriches recent turns and quietly declines for older ones — which is the
    honest answer, not a gap to paper over."""
    rows_fn = _tr_g("_cx_rows")
    primary = _tr_g("_cx_primary")
    measure = _tr_g("_cx_measure")
    window_fn = _tr_g("_cx_window")
    cfg_fn = _tr_g("_cx_read_cfg")
    if not all(callable(f) for f in (rows_fn, primary, measure, window_fn, cfg_fn)):
        return {}
    try:
        lo, hi = start - 1.0, end + 1.0
        rows = [r for r in rows_fn()
                if r.get("epoch") is not None and lo <= r["epoch"] <= hi]
        lane = primary(rows)
        if not lane:
            return {}
        window, _src = window_fn(cfg_fn())
        out = measure(lane, window, None, False, "")
        return {k: out.get(k) for k in _TR_CX_KEYS if out.get(k) is not None}
    except Exception as e:
        _tr_log("context enrich: %r" % e)
        return {}


# --------------------------------------------------------------------------
# span construction
# --------------------------------------------------------------------------
def _tr_span(trace_id, span_id, parent, name, kind_label, otel_kind,
             start, end, status, attrs, events=None, status_msg=""):
    return {"trace_id": trace_id, "span_id": span_id, "parent_span_id": parent,
            "name": name, "kind": kind_label, "otel_kind": otel_kind,
            "start": float(start), "end": float(end), "status": status,
            "status_msg": status_msg, "attrs": attrs, "events": events or []}


def _tr_put(attrs, key, value):
    """Append an attribute, skipping None (an absent measurement is absent,
    not zero) and empty strings."""
    if value is None or value == "":
        return
    attrs.append((key, value))


def _tr_turn_span(trace_id, rec, session, start, end, cx, memory_chars):
    job = _tr_clean(rec.get("job"), 32)
    model = _tr_clean(rec.get("model"), 120)
    span_id = _tr_span_id("turn", job, "%.3f" % end)

    out_tokens = _tr_int(cx.get("generated_tokens"))
    estimated = False
    if out_tokens is None:
        out_tokens = _tr_int(rec.get("est_tokens_out"))
        estimated = out_tokens is not None

    attrs = []
    _tr_put(attrs, "gen_ai.operation.name", "chat")
    _tr_put(attrs, "gen_ai.request.model", model)
    _tr_put(attrs, "gen_ai.usage.input_tokens", _tr_int(cx.get("prompt_tokens")))
    _tr_put(attrs, "gen_ai.usage.output_tokens", out_tokens)
    _tr_put(attrs, "hermes.job", job)
    _tr_put(attrs, "hermes.session", _tr_clean(session, 96))
    _tr_put(attrs, "hermes.path", _tr_clean(rec.get("path"), 16))
    _tr_put(attrs, "hermes.ttft_ms", _tr_int(rec.get("ttft_ms")))
    _tr_put(attrs, "hermes.setup_ms", _tr_int(rec.get("setup_ms")))
    _tr_put(attrs, "hermes.decode_ms", _tr_int(rec.get("decode_ms")))
    _tr_put(attrs, "hermes.cached_tokens", _tr_int(cx.get("cached_tokens")))
    _tr_put(attrs, "hermes.new_tokens", _tr_int(cx.get("new_tokens")))
    _tr_put(attrs, "hermes.prefill_s", _tr_num(cx.get("prefill_s")))
    _tr_put(attrs, "hermes.window_pct", _tr_num(cx.get("pct_of_window")))
    _tr_put(attrs, "hermes.decode_tps",
            _tr_num(cx.get("decode_tps")) or _tr_num(rec.get("est_tok_per_sec")))
    _tr_put(attrs, "hermes.memory_chars", _tr_int(memory_chars))
    _tr_put(attrs, "hermes.approvals", _tr_int(rec.get("approvals"), 0))
    if _tr_int(rec.get("approvals"), 0):
        _tr_put(attrs, "hermes.approved", _tr_int(rec.get("approved"), 0))
        _tr_put(attrs, "hermes.denied", _tr_int(rec.get("denied"), 0))
    if cx.get("compacted") is not None:
        attrs.append(("hermes.compacted", bool(cx.get("compacted"))))
    if estimated:
        # est_tokens_out is len(text)//4, not a tokenizer count. Say so in the
        # trace rather than letting a dashboard add it up as if it were real.
        attrs.append(("hermes.tokens_estimated", True))
    if rec.get("ttft_clean") is False:
        attrs.append(("hermes.ttft_clean", False))
    attrs.append(("hermes.turn.synthetic", False))

    ok = bool(rec.get("ok"))
    return _tr_span(trace_id, span_id, None,
                    ("chat " + model) if model else "chat", "turn",
                    _TR_KIND_INTERNAL, start, end,
                    _TR_STATUS_OK if ok else _TR_STATUS_ERROR, attrs,
                    status_msg="" if ok else "turn did not complete cleanly")


def _tr_synthetic_span(trace_id, session, source, start, end, actions):
    span_id = _tr_span_id("synthetic", session or "", source or "",
                          "%.3f" % start, str(actions[0].get("id")))
    errs = sum(1 for a in actions if (a.get("status") or "") == "error")
    attrs = []
    _tr_put(attrs, "gen_ai.operation.name", "chat")
    _tr_put(attrs, "hermes.session", _tr_clean(session, 96))
    _tr_put(attrs, "hermes.surface", _tr_clean(source, 32))
    _tr_put(attrs, "hermes.tool_calls", len(actions))
    attrs.append(("hermes.turn.synthetic", True))
    name = "turn " + (_tr_clean(source, 32) or "unknown")
    return _tr_span(trace_id, span_id, None, name, "turn", _TR_KIND_INTERNAL,
                    start, end,
                    _TR_STATUS_ERROR if errs else _TR_STATUS_OK, attrs,
                    status_msg=("%d tool call%s failed"
                                % (errs, "" if errs == 1 else "s")) if errs else "")


def _tr_tool_span(trace_id, parent, row):
    aid = row.get("id")
    ts = _tr_num(row.get("ts"), 0.0)
    dur = _tr_num(row.get("duration_s"), 0.0) or 0.0
    tool = _tr_clean(row.get("tool"), 80) or "tool"
    kind = _tr_clean(row.get("kind"), 24)
    status = _tr_clean(row.get("status"), 24)
    undone = row.get("undone_ts") is not None or status == "undone"

    attrs = []
    _tr_put(attrs, "gen_ai.operation.name", "execute_tool")
    _tr_put(attrs, "gen_ai.tool.name", tool)
    _tr_put(attrs, "hermes.tool.kind", kind)
    _tr_put(attrs, "hermes.tool.reversible", _tr_clean(row.get("reversible"), 16))
    _tr_put(attrs, "hermes.tool.status", status)
    _tr_put(attrs, "hermes.tool.target", _tr_redact(row.get("target"), _TR_TARGET_CAP))
    _tr_put(attrs, "hermes.tool.summary", _tr_redact(row.get("summary"), _TR_SUMMARY_CAP))
    _tr_put(attrs, "hermes.tool.source", _tr_clean(row.get("source"), 32))
    _tr_put(attrs, "hermes.tool.origin", _tr_clean(row.get("origin"), 32))
    _tr_put(attrs, "hermes.tool.action_id", _tr_int(aid))
    attrs.append(("hermes.tool.undone", bool(undone)))

    ok = status in ("done", "undone", "running")
    return _tr_span(trace_id, _tr_span_id("action", aid), parent,
                    "execute_tool " + tool, "tool",
                    _TR_KIND_CLIENT if kind in _TR_CLIENT_KINDS else _TR_KIND_INTERNAL,
                    ts, ts + max(0.0, dur),
                    _TR_STATUS_OK if ok else _TR_STATUS_ERROR, attrs,
                    status_msg="" if ok else status)


def _tr_event(row):
    attrs = []
    _tr_put(attrs, "gen_ai.tool.name", _tr_clean(row.get("tool"), 80))
    _tr_put(attrs, "hermes.budget.orig_chars", _tr_int(row.get("orig_chars")))
    _tr_put(attrs, "hermes.budget.kept_chars", _tr_int(row.get("kept_chars")))
    _tr_put(attrs, "hermes.budget.omitted_chars", _tr_int(row.get("omitted_chars")))
    _tr_put(attrs, "hermes.budget.est_tokens_saved", _tr_int(row.get("est_tokens_saved")))
    # the spill PATH is a filesystem path under ~/.hermes and never exported —
    # whether one exists is the fact worth tracing.
    attrs.append(("hermes.budget.spilled", bool(row.get("spill_path"))))
    return attrs


# --------------------------------------------------------------------------
# assembly — one local day at a time
# --------------------------------------------------------------------------
def _tr_assign(actions, windows):
    """(claimed, orphans): claimed is {window index: [action, …]}.

    A row belongs to the turn whose [start, end] (plus a second of slack)
    contains it; when windows overlap the SHORTEST one wins, because a long
    turn that was still finishing while a new one started is the looser fit."""
    claimed = {}
    orphans = []
    starts = [w[0] for w in windows]
    for a in actions:
        ts = _tr_num(a.get("ts"))
        if ts is None:
            continue
        # every window whose start is <= ts + slack; walk back while it can reach
        i = _tr_bisect.bisect_right(starts, ts + _TR_ACTION_SLACK)
        best = None
        j = i - 1
        while j >= 0:
            s, e = windows[j][0], windows[j][1]
            if ts + _TR_ACTION_SLACK < s:
                break
            if s - _TR_ACTION_SLACK <= ts <= e + _TR_ACTION_SLACK:
                if best is None or (e - s) < (windows[best][1] - windows[best][0]):
                    best = j
            if s < ts - _TR_MAX_TURN_S:
                break
            j -= 1
        if best is None:
            orphans.append(a)
        else:
            claimed.setdefault(best, []).append(a)
    return claimed, orphans


def _tr_cluster(orphans):
    """Orphan Recorder rows -> synthetic turns: same (session, source), split
    whenever more than _TR_ORPHAN_GAP_S passes between consecutive rows."""
    buckets = {}
    for a in orphans:
        buckets.setdefault((a.get("session") or "", a.get("source") or ""), []).append(a)
    out = []
    for (session, source), rows in buckets.items():
        rows.sort(key=lambda r: _tr_num(r.get("ts"), 0.0))
        run = []
        prev_end = None
        for r in rows:
            ts = _tr_num(r.get("ts"), 0.0)
            if run and prev_end is not None and ts - prev_end > _TR_ORPHAN_GAP_S:
                out.append((session, source, run))
                run = []
            run.append(r)
            prev_end = ts + (_tr_num(r.get("duration_s"), 0.0) or 0.0)
        if run:
            out.append((session, source, run))
    out.sort(key=lambda c: _tr_num(c[2][0].get("ts"), 0.0))
    return out


def _tr_day_spans(day, lo, hi, index, keys, trunc, live_jobs):
    """Every span (turns first, each followed by its tool children) for one
    local day, plus the per-trace roll-up rows the JSONL format emits."""
    day_iso = day.isoformat()
    turns = _tr_metrics_turns(day, lo, hi)
    actions = _tr_actions(lo, hi)

    windows = []
    for rec in turns:
        end = _tr_num(rec.get("ts"))
        dur = (_tr_num(rec.get("turn_ms"), 0.0) or 0.0) / 1000.0
        dur = max(0.0, min(dur, _TR_MAX_TURN_S))
        windows.append((end - dur, end, rec))
    windows.sort(key=lambda w: w[0])

    claimed, orphans = _tr_assign(actions, windows)

    spans = []
    traces = {}                       # trace_id -> roll-up

    def _roll(trace_id, session, start, end, kind, model=""):
        t = traces.get(trace_id)
        if t is None:
            t = traces[trace_id] = {"trace_id": trace_id, "session": session,
                                    "day": day_iso, "start": start, "end": end,
                                    "turns": 0, "tool_spans": 0, "events": 0,
                                    "models": []}
        t["start"] = min(t["start"], start)
        t["end"] = max(t["end"], end)
        t[kind] += 1
        if model and model not in t["models"]:
            t["models"].append(model)
        return t

    # --- real turns ---------------------------------------------------------
    for wi, (start, end, rec) in enumerate(windows):
        job = _tr_clean(rec.get("job"), 32)
        live = live_jobs.get(job) if job else None
        session = ""
        memory_chars = None
        if live is not None:
            session = _tr_clean(live.get("session"), 96)
            memory_chars = live.get("memory_chars")
        if not session:
            session = _tr_session_at(index, keys, start)
        trace_id = (_tr_trace_id("session", session, day_iso) if session
                    else _tr_trace_id("job", job))
        cx = _tr_context(start, end)
        span = _tr_turn_span(trace_id, rec, session, start, end, cx, memory_chars)

        for ep, row in trunc:
            if start - _TR_ACTION_SLACK <= ep <= end + _TR_ACTION_SLACK:
                span["events"].append({"name": "hermes.tool_budget.truncation",
                                       "ts": ep, "attrs": _tr_event(row)})
        _roll(trace_id, session, start, end, "turns",
              _tr_clean(rec.get("model"), 120))["events"] += len(span["events"])
        spans.append(span)
        for a in claimed.get(wi, []):
            child = _tr_tool_span(trace_id, span["span_id"], a)
            _roll(trace_id, session, child["start"], child["end"], "tool_spans")
            spans.append(child)

    # --- synthetic turns for Recorder rows no metrics row explains ----------
    for session, source, rows in _tr_cluster(orphans):
        start = _tr_num(rows[0].get("ts"), lo)
        end = max(_tr_num(r.get("ts"), start) + (_tr_num(r.get("duration_s"), 0.0) or 0.0)
                  for r in rows)
        trace_id = (_tr_trace_id("recorder", session, day_iso) if session
                    else _tr_trace_id("cluster", str(rows[0].get("id"))))
        span = _tr_synthetic_span(trace_id, session, source, start, end, rows)
        for ep, row in trunc:
            if start - _TR_ACTION_SLACK <= ep <= end + _TR_ACTION_SLACK:
                span["events"].append({"name": "hermes.tool_budget.truncation",
                                       "ts": ep, "attrs": _tr_event(row)})
        _roll(trace_id, session, start, end, "turns")["events"] += len(span["events"])
        spans.append(span)
        for a in rows:
            child = _tr_tool_span(trace_id, span["span_id"], a)
            _roll(trace_id, session, child["start"], child["end"], "tool_spans")
            spans.append(child)

    return spans, list(traces.values())


def _tr_build(since, until, on_day):
    """Walk the range one local day at a time, handing each day's spans to
    `on_day(day, spans, traces)`. Nothing but one day is ever in memory here —
    the caller decides what to keep. Returns (days, spans, truncated)."""
    index = _tr_session_index(since, until)
    keys = [p[0] for p in index]
    trunc = _tr_truncations(since, until)
    live_jobs = dict(_tr_g("CHAT_JOBS") or {})
    n_spans = 0
    truncated = False
    days = 0
    for day, lo, hi in _tr_days(since, until):
        spans, traces = _tr_day_spans(day, lo, hi, index, keys,
                                      [t for t in trunc if lo <= t[0] <= hi],
                                      live_jobs)
        days += 1
        if n_spans + len(spans) > _TR_MAX_SPANS:
            spans = spans[:max(0, _TR_MAX_SPANS - n_spans)]
            truncated = True
        n_spans += len(spans)
        on_day(day, spans, traces)
        if truncated:
            break
    return days, n_spans, truncated


# --------------------------------------------------------------------------
# format 1 — JSONL (flat, dotted attribute keys, grep/jq friendly)
# --------------------------------------------------------------------------
def _tr_line(obj):
    return _tr_json.dumps(obj, separators=(",", ":"), sort_keys=False,
                          ensure_ascii=False) + "\n"


def _tr_jsonl_span(s):
    obj = {"type": "span", "trace_id": s["trace_id"], "span_id": s["span_id"],
           "parent_span_id": s["parent_span_id"], "name": s["name"],
           "kind": s["kind"], "start": round(s["start"], 3),
           "end": round(s["end"], 3),
           "duration_ms": round((s["end"] - s["start"]) * 1000.0, 1),
           "status": "ok" if s["status"] == _TR_STATUS_OK else "error"}
    for k, v in s["attrs"]:
        obj[k] = v
    return obj


def _tr_jsonl(since, until, meta):
    parts = [_tr_line({"type": "meta", "service": _TR_SERVICE,
                       "version": _tr_version(), "format": "jsonl",
                       "since": round(since, 3), "until": round(until, 3),
                       "generated": round(_tr_time.time(), 3)})]

    def on_day(day, spans, traces):
        for t in traces:
            parts.append(_tr_line(dict(t, type="trace",
                                       start=round(t["start"], 3),
                                       end=round(t["end"], 3))))
        for s in spans:
            parts.append(_tr_line(_tr_jsonl_span(s)))
            for ev in s["events"]:
                obj = {"type": "event", "trace_id": s["trace_id"],
                       "span_id": s["span_id"], "name": ev["name"],
                       "ts": round(ev["ts"], 3)}
                for k, v in ev["attrs"]:
                    obj[k] = v
                parts.append(_tr_line(obj))

    days, n_spans, truncated = _tr_build(since, until, on_day)
    if truncated:
        parts.append(_tr_line({"type": "note", "truncated": True,
                               "max_spans": _TR_MAX_SPANS,
                               "note": "export stopped at the span cap — "
                                       "narrow the range"}))
    meta.update({"days": days, "spans": n_spans, "truncated": truncated})
    return "".join(parts)


# --------------------------------------------------------------------------
# format 2 — OTLP/JSON
# --------------------------------------------------------------------------
def _tr_any(v):
    """OTLP AnyValue. bool is checked FIRST — in Python bool is an int, and an
    intValue:"1" where a boolValue belongs is a wrong trace, not a typo."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}          # int64 -> JSON string (proto3 JSON)
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _tr_kv(pairs):
    return [{"key": k, "value": _tr_any(v)} for k, v in pairs]


def _tr_otel_span(s):
    out = {"traceId": s["trace_id"], "spanId": s["span_id"],
           "name": s["name"], "kind": s["otel_kind"],
           "startTimeUnixNano": _tr_nano(s["start"]),
           "endTimeUnixNano": _tr_nano(s["end"]),
           "attributes": _tr_kv(s["attrs"])}
    if s["parent_span_id"]:
        out["parentSpanId"] = s["parent_span_id"]
    if s["events"]:
        out["events"] = [{"timeUnixNano": _tr_nano(e["ts"]), "name": e["name"],
                          "attributes": _tr_kv(e["attrs"])} for e in s["events"]]
    status = {"code": s["status"]}
    if s["status"] == _TR_STATUS_ERROR and s["status_msg"]:
        status["message"] = s["status_msg"]
    out["status"] = status
    return out


def _tr_otel(since, until, meta):
    ver = _tr_version()
    resource = {"attributes": _tr_kv([("service.name", _TR_SERVICE),
                                      ("service.version", ver),
                                      ("telemetry.sdk.name", "hermes-aux-trace"),
                                      ("telemetry.sdk.language", "python")])}
    scope = {"name": _TR_SCOPE, "version": ver}
    resource_spans = []

    def on_day(day, spans, _traces):
        if not spans:
            return
        # one resourceSpans per local day: the assembly stays per-day and a
        # receiver merges same-resource batches anyway.
        resource_spans.append({"resource": resource,
                               "scopeSpans": [{"scope": scope,
                                               "spans": [_tr_otel_span(s) for s in spans]}]})

    days, n_spans, truncated = _tr_build(since, until, on_day)
    meta.update({"days": days, "spans": n_spans, "truncated": truncated})
    return _tr_json.dumps({"resourceSpans": resource_spans},
                          separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# range parsing, shared by both routes
# --------------------------------------------------------------------------
def _tr_range(ctx):
    """(since, until, None) or (None, None, (payload, status))."""
    now = _tr_time.time()
    raw_until = (ctx.q1("until", "") or "").strip()
    raw_since = (ctx.q1("since", "") or "").strip()
    try:
        until = float(raw_until) if raw_until else now
        since = float(raw_since) if raw_since else until - 86400.0
    except ValueError:
        return None, None, ({"ok": False,
                             "error": "since/until must be epoch seconds"}, 400)
    if until <= since:
        return None, None, ({"ok": False, "error": "until must be after since"}, 400)
    if (until - since) > _TR_MAX_DAYS * 86400.0:
        return None, None, ({"ok": False,
                             "error": "range too wide: at most %d days per export"
                                      % _TR_MAX_DAYS,
                             "max_days": _TR_MAX_DAYS}, 400)
    return since, until, None


def _tr_stamp(t):
    return _tr_datetime.datetime.fromtimestamp(t).strftime("%Y%m%d")


# --------------------------------------------------------------------------
# GET /api/trace/export?since=&until=&format=jsonl|otel
# --------------------------------------------------------------------------
def _tr_export(ctx):
    if _tr_scrubber() is None:
        # aux_convos failed to load: refuse rather than ship unscrubbed text.
        return {"ok": False, "error": "secret scrubber unavailable — "
                                      "aux_convos.py did not load"}, 503
    since, until, err = _tr_range(ctx)
    if err:
        return err
    fmt = (ctx.q1("format", "jsonl") or "jsonl").strip().lower()
    if fmt not in ("jsonl", "otel"):
        return {"ok": False, "error": "format must be jsonl or otel"}, 400

    meta = {}
    t0 = _tr_time.time()
    if fmt == "jsonl":
        body = _tr_jsonl(since, until, meta)
        ctype, ext = "application/x-ndjson; charset=utf-8", "jsonl"
    else:
        body = _tr_otel(since, until, meta)
        ctype, ext = "application/json; charset=utf-8", "otlp.json"

    fname = "hermes-trace-%s-%s.%s" % (_tr_stamp(since), _tr_stamp(until), ext)
    headers = {"Content-Disposition": 'attachment; filename="%s"' % fname,
               "X-Hermes-Trace-Spans": str(meta.get("spans", 0)),
               "X-Hermes-Trace-Days": str(meta.get("days", 0)),
               "X-Hermes-Trace-Ms": str(int((_tr_time.time() - t0) * 1000))}
    if meta.get("truncated"):
        headers["X-Hermes-Trace-Truncated"] = "1"
    return RawResponse(body, ctype, headers)                                  # noqa: F821


# --------------------------------------------------------------------------
# GET /api/trace/summary?since=&until=
# --------------------------------------------------------------------------
def _tr_mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def _tr_summary(ctx):
    if _tr_scrubber() is None:
        return {"ok": False, "error": "secret scrubber unavailable — "
                                      "aux_convos.py did not load"}, 503
    since, until, err = _tr_range(ctx)
    if err:
        return err
    t0 = _tr_time.time()
    acc = {"traces": set(), "turns": 0, "tool_spans": 0, "truncations": 0,
           "undone": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0,
           "estimated": 0, "prefill": [], "cached_pct": [], "models": {}}

    def on_day(_day, spans, traces):
        for t in traces:
            acc["traces"].add(t["trace_id"])
        for s in spans:
            a = dict(s["attrs"])
            if s["kind"] == "turn":
                acc["turns"] += 1
                acc["tokens_in"] += a.get("gen_ai.usage.input_tokens") or 0
                acc["tokens_out"] += a.get("gen_ai.usage.output_tokens") or 0
                if a.get("hermes.tokens_estimated"):
                    acc["estimated"] += 1
                if a.get("hermes.prefill_s") is not None:
                    acc["prefill"].append(float(a["hermes.prefill_s"]))
                pin, cached = a.get("gen_ai.usage.input_tokens"), a.get("hermes.cached_tokens")
                if pin and cached is not None:
                    acc["cached_pct"].append(100.0 * float(cached) / float(pin))
                m = a.get("gen_ai.request.model")
                if m:
                    acc["models"][m] = acc["models"].get(m, 0) + 1
            else:
                acc["tool_spans"] += 1
                if a.get("hermes.tool.undone"):
                    acc["undone"] += 1
            if s["status"] == _TR_STATUS_ERROR and s["kind"] == "tool":
                acc["errors"] += 1
            acc["truncations"] += len(s["events"])

    days, n_spans, truncated = _tr_build(since, until, on_day)
    return {"ok": True, "since": round(since, 3), "until": round(until, 3),
            "days": days, "spans": n_spans, "truncated": truncated,
            "traces": len(acc["traces"]), "turns": acc["turns"],
            "tool_spans": acc["tool_spans"], "truncations": acc["truncations"],
            "undone": acc["undone"], "tool_errors": acc["errors"],
            "tokens_in": acc["tokens_in"], "tokens_out": acc["tokens_out"],
            "turns_estimated_tokens": acc["estimated"],
            "mean_prefill_s": _tr_mean(acc["prefill"]),
            "mean_cached_pct": _tr_mean(acc["cached_pct"]),
            "models": acc["models"], "max_days": _TR_MAX_DAYS,
            "took_ms": int((_tr_time.time() - t0) * 1000)}


register_get("/api/trace/export", _tr_export)                                 # noqa: F821
register_get("/api/trace/summary", _tr_summary)                               # noqa: F821
