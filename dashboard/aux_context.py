# aux_context.py — "Context & compaction": what each turn actually costs in
# context, how much of it the prefix cache served, and the four knobs that
# decide when the agent throws context away.
#
#   GET  /api/context/turn?job=<id>            -> the measurement for one chat job
#   GET  /api/context/turn?since=&until=       -> same, for an explicit epoch window
#                                                 (debug seam; no job needed)
#   GET  /api/context/recent?n=20              -> the last N primary-lane turns
#   GET  /api/context/compression              -> the four compression.* knobs
#   POST /api/context/compression              -> write them (validated, backed up)
#
# Every route is same-origin-only for free: server.py's `_guard()` runs the
# Host/Origin check before any route dispatch, so nothing here re-implements it.
#
# ---------------------------------------------------------------------------
# WHERE THE NUMBERS COME FROM.  No model is ever started and no model server is
# ever asked: the MLX server writes everything needed to its own launchd stdout
# (~/.hermes/logs/mlx-server.log), two lines per request —
#
#   Prefill completed: request=<id> prompt_tokens=N cached_tokens=C elapsed=Ts rate=R tok/s
#   Request completed: endpoint=/chat/completions model=M stream=B ... prompt_tokens=N
#                      generated_tokens=G elapsed=Ts prefill=P tok/s decode=D tok/s
#                      finish_reason=F in_flight=I
#
# `tools/bench/prompt_size.py` already parses exactly this pair (RE_PREFILL /
# RE_DONE / parse(), lines 49-98).  Its regexes and its pairing loop are
# VENDORED below rather than imported: tools/ is a bench directory that is not on
# the dashboard's import path, importing across the repo would couple a running
# service to a script that is free to change its CLI, and the copy needs two
# fields the original drops anyway — the prefill line's own `elapsed` (the
# seconds a user actually waited for prefill) and the completion line's
# `stream=` flag.  Two changes to the vendored regexes, both additive:
#   * the timestamp keeps its millisecond field, so a turn that lasts 300 ms is
#     still correlated to the right request,
#   * `stream=(True|False)` is captured, because it is what separates a
#     CONVERSATION turn from the auxiliary traffic that shares this server.
#
# THE PRIMARY LANE.  Everything the dashboard chat sends is `stream=True`.  The
# auxiliary client — conversation titles, the compression summariser itself, the
# watchtower's small classifier calls — is `stream=False` and usually two orders
# of magnitude smaller (94 and 207 tokens, against 20-27k for a real turn).  A
# turn's measurement therefore uses the stream=True rows only, and falls back to
# every row in the window if a window somehow has none, so an unusual backend
# never produces an empty meter.
#
# ONE TURN IS OFTEN SEVERAL REQUESTS.  A tool loop sends the whole conversation
# again after every tool result: the 13:48 turn on this machine was five
# requests, 22,944 -> 26,925 tokens.  So the payload reports the LAST request's
# prompt/cached/decode figures (that is the context the turn ended at, which is
# what a context meter means) and SUMS prefill seconds and generated tokens
# across the turn (that is the waiting the user actually paid).  `requests` says
# how many were folded together, so the number is never mistaken for one call.
#
# HOW A COMPACTION IS DETECTED.  Two independent signals, OR'd:
#   1. the job's status text contained "Compacting" — the tui gateway rewrites a
#      lifecycle status carrying COMPACTION_STATUS_MARKER into
#      `status.update {kind:"compacting", text:"...Compacting context..."}` and
#      hermes_rpc.py stores its text into `job["status"]`.  That field is
#      TRANSIENT (the next tool.start overwrites it, tool.complete clears it),
#      so the caller that watched the turn poll-by-poll can hand back what it
#      saw as `&compacting=1`; whatever is still on the job at request time is
#      read too.  Believing the observer here is not a trust hole — the flag
#      only ever adds a note to a transcript.
#   2. the prompt dropped by more than 30 % against the previous request of the
#      same session, AND the two were less than ten minutes apart.  This one
#      needs no cooperation from anybody and is what makes the "recent turns"
#      table honest.  The time guard is what stops its one false positive: a
#      fresh conversation two days later also starts small, and that is a new
#      chat, not a compaction.
# `_CX_LAST` is the tiny per-session last-prompt map that signal 2 needs; it is
# in-memory, bounded, and losing it on a restart costs one turn's `compacted`.
#
# THE WINDOW.  `model.context_length` in ~/.hermes/config.yaml (65,536 here) —
# NOT the roster's native `ctx` (262,144 for the Qwen3.8 family).  What the
# compressor multiplies by `compression.threshold` is hermes's own configured
# context length, so that is the only number against which "72 % of the window"
# means anything.  The roster figure rides along as `model_ctx_native` for the
# model menu's sake and is never used for a percentage.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an aux
# module — it rebinds the shared global.  Private aliases only.
#
# LOAD ORDER.  aux files exec SORTED, so this runs after aux_config and before
# aux_metrics (whose MeteredJob is what gives a chat job its `submitted_ts`).
# Nothing here touches a foreign global at module load: `HOME` is server.py's
# own, and `CHAT_JOBS` / `active_model` are resolved by name inside a request.
import os as _cx_os
import re as _cx_re
import shutil as _cx_shutil
import threading as _cx_threading
import time as _cx_time
import datetime as _cx_datetime

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
_CX_LOG = _cx_os.path.join(HOME, ".hermes", "logs", "mlx-server.log")   # noqa: F821
_CX_CFG = _cx_os.path.join(HOME, ".hermes", "config.yaml")             # noqa: F821

# 512 KB reaches back to 2026-07-17 on this machine (43 requests, 6.3k lines)
# against a 4.3 MB log — several months of chat for a tenth of the read.
_CX_TAIL_BYTES = 512 * 1024

_CX_ENDPOINT = "/chat/completions"

# A finished job whose done moment we never observed: cap how far the search
# window may stretch past `submitted_ts` so a late call cannot swallow the next
# conversation's requests. 30 min is well past the longest turn this machine
# has ever logged (54 s).
_CX_MAX_TURN_S = 1800.0

# `compacted` fires below this fraction of the previous prompt (a >30 % drop).
_CX_DROP_RATIO = 0.70

# ...but only when the two requests plausibly belong to the SAME conversation.
# A compaction happens mid-conversation; a smaller prompt after a two-day gap is
# just a new chat starting from the fixed prefix again, and calling that a
# compaction is the one false positive this heuristic is prone to. Ten minutes
# is well past the longest gap the tool loop leaves between requests in a turn.
_CX_SAME_CONVO_GAP_S = 600.0

# clamp + coerce for each writable key: (lo, hi, kind)
_CX_LIMITS = {
    "threshold":       (0.3, 0.9, "float"),
    "target_ratio":    (0.1, 0.5, "float"),
    "protect_last_n":  (2, 60, "int"),
    "protect_first_n": (0, 10, "int"),
}
# order is the order they are written into a freshly created block
_CX_KEYS = ["threshold", "target_ratio", "protect_last_n", "protect_first_n"]

# agent/agent_init.py:1400-1448 — the defaults the runtime falls back to when a
# key is absent.  Quoted so the card can say "default 0.5" without guessing.
_CX_DEFAULTS = {"threshold": 0.50, "target_ratio": 0.20,
                "protect_last_n": 20, "protect_first_n": 3}

_CX_FALLBACK_WINDOW = 65536

_cx_lock = _cx_threading.Lock()
_cx_cache = {"key": None, "rows": None}      # (mtime_ns, size) -> parsed rows
_CX_LAST = {}                                # session -> last prompt_tokens
_CX_DONE = {}                                # job id -> first observed done ts
_CX_TURN = {}                                # job id -> computed payload


def _cx_trim(d, cap=64):
    """Keep the little in-memory maps from growing without bound."""
    if len(d) > cap:
        for k in list(d.keys())[: len(d) - cap]:
            d.pop(k, None)


# ---------------------------------------------------------------------------
# log parsing — vendored from tools/bench/prompt_size.py (RE_PREFILL, RE_DONE,
# parse), plus the prefill line's own elapsed and the completion line's stream
# flag, plus milliseconds on the timestamp.
# ---------------------------------------------------------------------------
_CX_TS = r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,(?P<ms>\d{1,6}))?"

_CX_RE_PREFILL = _cx_re.compile(
    _CX_TS + r".*Prefill completed: request=(?P<req>\S+) "
    r"prompt_tokens=(?P<prompt>\d+) cached_tokens=(?P<cached>\d+) "
    r"elapsed=(?P<elapsed>[\d.]+)s rate=(?P<rate>[\d.]+) tok/s")

_CX_RE_DONE = _cx_re.compile(
    _CX_TS + r".*Request completed: endpoint=(?P<endpoint>\S+) "
    r"model=(?P<model>\S+) (?:stream=(?P<stream>\w+) )?.*?prompt_tokens=(?P<prompt>\d+) "
    r"generated_tokens=(?P<gen>\d+) elapsed=(?P<elapsed>[\d.]+)s "
    r"prefill=(?P<prefill>[\d.]+) tok/s decode=(?P<decode>[\d.]+) tok/s"
    r"(?: finish_reason=(?P<finish>\S+))?")


def _cx_epoch(ts, ms):
    """'2026-09-07 13:48:52' + '380' -> local epoch seconds. None if unparsable."""
    try:
        dt = _cx_datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        v = dt.timestamp()
        if ms:
            v += int(ms[:3].ljust(3, "0")) / 1000.0
        return v
    except (ValueError, OverflowError, OSError):
        return None


def _cx_read_tail(path, nbytes):
    """Last `nbytes` of the log, starting at a line boundary. ('' , 0) if absent."""
    try:
        size = _cx_os.path.getsize(path)
    except OSError:
        return "", 0
    try:
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
                f.readline()                   # drop the partial first line
            return f.read().decode("utf-8", "replace"), size
    except OSError:
        return "", size


def _cx_parse(text, endpoint_filter=_CX_ENDPOINT):
    """Walk the log forward, pairing each completion with the most recent
    unconsumed prefill line that had the same prompt_tokens.

    Vendored from tools/bench/prompt_size.py::parse — same pairing rule (the
    completion line carries no request id), same fields, plus `epoch`,
    `prefill_s` and `stream`."""
    pending = {}                               # prompt_tokens -> [prefill dicts]
    out = []
    for line in text.splitlines():
        m = _CX_RE_PREFILL.search(line)
        if m:
            pending.setdefault(int(m.group("prompt")), []).append(
                {"cached": int(m.group("cached")),
                 "prefill_s": float(m.group("elapsed")),
                 "prefill_rate": float(m.group("rate")),
                 "req": m.group("req")})
            continue
        m = _CX_RE_DONE.search(line)
        if not m:
            continue
        if endpoint_filter and m.group("endpoint") != endpoint_filter:
            continue
        prompt = int(m.group("prompt"))
        q = pending.get(prompt)
        pre = q.pop(0) if q else {}
        stream = m.group("stream")
        out.append({
            "ts": m.group("ts"),
            "epoch": _cx_epoch(m.group("ts"), m.group("ms")),
            "request": pre.get("req"),
            "model": m.group("model"),
            "endpoint": m.group("endpoint"),
            "stream": (None if stream is None else stream.lower() == "true"),
            "prompt_tokens": prompt,
            "cached_tokens": pre.get("cached"),
            "new_tokens": (prompt - pre["cached"]) if "cached" in pre else None,
            "prefill_s": pre.get("prefill_s"),
            "generated_tokens": int(m.group("gen")),
            "elapsed_s": float(m.group("elapsed")),
            "prefill_tps": float(m.group("prefill")),
            "decode_tps": float(m.group("decode")),
            "finish_reason": m.group("finish"),
        })
    return out


def _cx_rows():
    """Parsed rows for the current log tail, cached on (mtime_ns, size) so a
    burst of requests re-reads nothing."""
    try:
        st = _cx_os.stat(_CX_LOG)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _cx_lock:
        if key is not None and _cx_cache["key"] == key and _cx_cache["rows"] is not None:
            return _cx_cache["rows"]
    text, _size = _cx_read_tail(_CX_LOG, _CX_TAIL_BYTES)
    rows = _cx_parse(text)
    with _cx_lock:
        _cx_cache["key"] = key
        _cx_cache["rows"] = rows
    return rows


def _cx_primary(rows):
    """Conversation turns only. stream=True is the dashboard/gateway lane; the
    auxiliary client (titles, the compression summariser) is stream=False. A log
    whose backend never printed `stream=` yields None everywhere — then every
    row is kept rather than showing an empty meter."""
    lane = [r for r in rows if r.get("stream") is True]
    return lane if lane else rows


# ---------------------------------------------------------------------------
# config.yaml — read
# ---------------------------------------------------------------------------
def _cx_num(raw, kind):
    raw = (raw or "").split("#", 1)[0].strip().strip('"\'')
    if not raw:
        return None
    try:
        return int(raw) if kind == "int" else float(raw)
    except ValueError:
        return None


def _cx_read_cfg():
    """Stdlib scanner (no yaml dep — same approach as _config_model_default and
    aux_config's _cfg_read_yaml_keys).

    Returns the four compression knobs (None when the key is absent, so the
    caller can show the runtime default and say so), plus `enabled`,
    `model.context_length`, `model.default` and `auxiliary.compression.*`."""
    out = {"threshold": None, "target_ratio": None, "protect_last_n": None,
           "protect_first_n": None, "enabled": None,
           "context_length": None, "model_default": None,
           "aux_model": None, "aux_provider": None, "aux_context_length": None,
           "has_block": False}
    section = None
    sub = None
    try:
        with open(_CX_CFG, encoding="utf-8") as f:
            for line in f:
                if _cx_re.match(r"^\S", line):
                    m = _cx_re.match(r"^([A-Za-z0-9_.-]+):\s*(.*)$", line)
                    section = m.group(1) if m else None
                    sub = None
                    if section == "compression":
                        out["has_block"] = True
                    continue
                if section == "compression":
                    m = _cx_re.match(r"^\s{1,4}([A-Za-z0-9_]+):\s*(.*)$", line)
                    if not m:
                        continue
                    k, v = m.group(1), m.group(2)
                    if k in _CX_LIMITS:
                        out[k] = _cx_num(v, _CX_LIMITS[k][2])
                    elif k == "enabled":
                        out["enabled"] = v.split("#", 1)[0].strip().lower() in (
                            "true", "1", "yes")
                elif section == "model":
                    m = _cx_re.match(r"^\s{1,4}(context_length|default):\s*(.*)$", line)
                    if not m:
                        continue
                    if m.group(1) == "context_length":
                        out["context_length"] = _cx_num(m.group(2), "int")
                    else:
                        out["model_default"] = m.group(2).split("#", 1)[0].strip().strip('"\'')
                elif section == "auxiliary":
                    m = _cx_re.match(r"^\s{2}([A-Za-z0-9_]+):\s*$", line)
                    if m:
                        sub = m.group(1)
                        continue
                    if sub != "compression":
                        continue
                    m = _cx_re.match(r"^\s{3,6}([A-Za-z0-9_]+):\s*(.*)$", line)
                    if not m:
                        continue
                    k, v = m.group(1), m.group(2).split("#", 1)[0].strip().strip('"\'')
                    if k == "model" and v.lower() != "auto":
                        out["aux_model"] = v
                    elif k == "provider":
                        out["aux_provider"] = v
                    elif k == "context_length":
                        out["aux_context_length"] = _cx_num(v, "int")
    except OSError:
        pass
    return out


def _cx_effective(cfg):
    """The four values the runtime will actually use, defaults filled in."""
    return {k: (cfg.get(k) if cfg.get(k) is not None else _CX_DEFAULTS[k])
            for k in _CX_KEYS}


def _cx_roster_ctx(model_id):
    """The roster's native context length for a model id, or None. Resolved by
    name at request time — _SEED_MODELS is server.py's."""
    try:
        for m in globals().get("_SEED_MODELS") or []:
            if m.get("id") == model_id and m.get("ctx"):
                return int(m["ctx"])
    except Exception:
        pass
    return None


def _cx_window(cfg):
    """(window_tokens, source). config `model.context_length` is what the
    compressor multiplies by `threshold`, so it wins over the roster."""
    if cfg.get("context_length"):
        return int(cfg["context_length"]), "model.context_length"
    model = cfg.get("model_default")
    try:
        model = globals()["active_model"]() or model
    except Exception:
        pass
    ctx = _cx_roster_ctx(model)
    if ctx:
        return ctx, "model roster"
    return _CX_FALLBACK_WINDOW, "fallback"


# ---------------------------------------------------------------------------
# config.yaml — write
# ---------------------------------------------------------------------------
def _cx_fmt(key, value):
    if _CX_LIMITS[key][2] == "int":
        return str(int(value))
    return "%g" % float(value)


def _cx_render_block(values):
    return "compression:\n" + "".join(
        "  %s: %s\n" % (k, _cx_fmt(k, values[k])) for k in _CX_KEYS if k in values)


def _cx_apply_text(src, values):
    """Pure: return `src` with the given compression.<key> values set.

    Only the named keys are touched; every other line of the block (enabled,
    codex_gpt55_autoraise, whatever else lives there) is preserved byte for
    byte, including a trailing comment on a line being rewritten. Returns `src`
    unchanged when nothing moves, which is what makes a no-op POST leave the
    file — and its SHA256 — untouched."""
    if not values:
        return src
    lines = src.splitlines(True)
    start = None
    for i, line in enumerate(lines):
        if _cx_re.match(r"^compression:\s*$", line):
            start = i
            break
    if start is None:
        full = dict(_CX_DEFAULTS)
        full.update(values)
        tail = "" if (not src or src.endswith("\n")) else "\n"
        return src + tail + _cx_render_block(full)

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and _cx_re.match(r"^\S", lines[i]):
            end = i
            break

    todo = dict(values)
    out = list(lines)
    for i in range(start + 1, end):
        m = _cx_re.match(
            r"^(\s+)([A-Za-z0-9_]+):[ \t]*([^#\n]*?)([ \t]*)(#[^\n]*)?(\r?\n?)$",
            lines[i])
        if not m:
            continue
        key = m.group(2)
        if key not in todo:
            continue
        # keep whatever ran between the value and a trailing comment, so a
        # rewrite that lands on the same number is still byte-identical
        gap = m.group(4) if m.group(5) else ""
        out[i] = "%s%s: %s%s%s%s" % (m.group(1), key, _cx_fmt(key, todo.pop(key)),
                                     gap, m.group(5) or "", m.group(6) or "\n")
    if todo:
        # keys the block does not carry yet — append them at its end, in the
        # canonical order, with the indent the block already uses
        indent = "  "
        for i in range(start + 1, end):
            m = _cx_re.match(r"^(\s+)\S", lines[i])
            if m:
                indent = m.group(1)
                break
        add = ["%s%s: %s\n" % (indent, k, _cx_fmt(k, todo[k]))
               for k in _CX_KEYS if k in todo]
        out = out[:end] + add + out[end:]
    return "".join(out)


def _cx_backup():
    """Timestamped copy next to the config, 0600. Returns its path."""
    stamp = _cx_datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-context-%s" % (_CX_CFG, stamp)
    _cx_shutil.copy2(_CX_CFG, dst)
    try:
        _cx_os.chmod(dst, 0o600)
    except OSError:
        pass
    return dst


def _cx_write(values):
    """Set compression.<key> for each key in `values`.

    Returns (changed, backup_path). A no-op write touches nothing at all — not
    the file, not a backup — so the config stays byte-identical."""
    with open(_CX_CFG, encoding="utf-8") as fh:
        src = fh.read()
    new = _cx_apply_text(src, values)
    if new == src:
        return False, None
    bak = _cx_backup()
    try:
        mode = _cx_os.stat(_CX_CFG).st_mode & 0o777
    except OSError:
        mode = 0o600
    tmp = _CX_CFG + ".context.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new)
        fh.flush()
        _cx_os.fsync(fh.fileno())
    try:
        _cx_os.chmod(tmp, mode)
    except OSError:
        pass
    _cx_os.replace(tmp, _CX_CFG)
    return True, bak


# ---------------------------------------------------------------------------
# shaping
# ---------------------------------------------------------------------------
def _cx_pct(part, whole, nd=1):
    try:
        if not whole:
            return None
        return round(100.0 * float(part) / float(whole), nd)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _cx_strip_emoji(text):
    """This UI ships no emoji, and the gateway's compaction status starts with
    one. Drop anything outside the BMP plus the variation selector, then squeeze
    the whitespace that leaves behind."""
    if not text:
        return ""
    keep = []
    for ch in str(text):
        o = ord(ch)
        if o > 0xFFFF or 0x2190 <= o <= 0x2BFF or o in (0xFE0F, 0xFE0E, 0x200D):
            continue
        keep.append(ch)
    return " ".join("".join(keep).split())


def _cx_row_out(r, window):
    """One log row in the shape the UI reads."""
    return {
        "ts": r.get("ts"),
        "epoch": r.get("epoch"),
        "model": r.get("model"),
        "prompt_tokens": r.get("prompt_tokens"),
        "cached_tokens": r.get("cached_tokens"),
        "new_tokens": r.get("new_tokens"),
        "prefill_s": (round(r["prefill_s"], 2) if r.get("prefill_s") is not None else None),
        "decode_tps": r.get("decode_tps"),
        "generated_tokens": r.get("generated_tokens"),
        "elapsed_s": round(r.get("elapsed_s") or 0.0, 2),
        "finish_reason": r.get("finish_reason"),
        "pct_of_window": _cx_pct(r.get("prompt_tokens"), window),
        "cache_pct": _cx_pct(r.get("cached_tokens"), r.get("prompt_tokens")),
    }


def _cx_dropped(prompt, prev_prompt, gap_s=None):
    """True when this prompt is >30 % smaller than the previous request of the
    same conversation. `gap_s` is the seconds between them; when it is known and
    exceeds _CX_SAME_CONVO_GAP_S the two are different conversations and the
    drop means nothing."""
    if prev_prompt is None or prompt is None or prev_prompt <= 0:
        return False
    if gap_s is not None and gap_s > _CX_SAME_CONVO_GAP_S:
        return False
    return prompt < prev_prompt * _CX_DROP_RATIO


def _cx_measure(rows, window, prev_prompt, compacting, status_text, prev_gap=None):
    """Fold one turn's requests (already filtered to the window + lane) into the
    single measurement the meter shows."""
    last = rows[-1]
    prefill_s = None
    for r in rows:
        if r.get("prefill_s") is not None:
            prefill_s = (prefill_s or 0.0) + r["prefill_s"]
    generated = sum(int(r.get("generated_tokens") or 0) for r in rows)

    prompt = last.get("prompt_tokens")
    dropped = _cx_dropped(prompt, prev_prompt, prev_gap)
    out = _cx_row_out(last, window)
    out.update({
        "prefill_s": (round(prefill_s, 2) if prefill_s is not None else None),
        "generated_tokens": generated,
        "requests": len(rows),
        "window": window,
        "prev_prompt_tokens": prev_prompt,
        "compacted": bool(compacting or dropped),
        "compacted_by": ("status" if compacting else ("prompt drop" if dropped else None)),
        "status_text": _cx_strip_emoji(status_text),
    })
    return out


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def _cx_job_window(jid):
    """(start, end, session, status, ok) for a chat job, or (None, ...) if the
    job is unknown. Times come from aux_metrics' MeteredJob, which stamps `t0`
    at creation and `submitted_ts` when hermes_rpc actually puts the prompt on
    the wire; the done moment is latched the first time this module sees it."""
    jobs = globals().get("CHAT_JOBS") or {}
    job = jobs.get(jid)
    if job is None:
        return None, None, None, "", False
    start = getattr(job, "submitted_ts", None)
    if start is None:
        start = job.get("_submitted_ts") or getattr(job, "t0", None) or job.get("ts")
    done = bool(job.get("done"))
    now = _cx_time.time()
    if done:
        end = _CX_DONE.get(jid)
        if end is None:
            end = now
            _CX_DONE[jid] = end
            _cx_trim(_CX_DONE)
    else:
        end = now
    if start is None:
        start = end - _CX_MAX_TURN_S
    end = min(end, start + _CX_MAX_TURN_S)
    return float(start), float(end), job.get("session"), (job.get("status") or ""), done


def _cx_turn(ctx):
    jid = (ctx.q1("job", "") or "").strip()
    since = ctx.q1("since", "")
    until = ctx.q1("until", "")
    compacting = (ctx.q1("compacting", "") or "").strip().lower() in ("1", "true", "yes")

    cfg = _cx_read_cfg()
    window, window_src = _cx_window(cfg)
    session = None
    status = ""
    done = True

    if since or until:
        # debug seam: an explicit epoch window, so the per-turn path is testable
        # without a live chat job (and without waking a model). Same-origin only,
        # like every other route — server.py's _guard() sees to that.
        try:
            start = float(since) if since else 0.0
            end = float(until) if until else _cx_time.time()
        except ValueError:
            return {"ok": False, "error": "since/until must be epoch seconds"}, 400
        session = "window:%s-%s" % (since, until)
    elif jid:
        cached = _CX_TURN.get(jid)
        if cached is not None and not compacting:
            return cached
        start, end, session, status, done = _cx_job_window(jid)
        if start is None:
            return {"ok": False, "gone": True,
                    "error": "no such chat job (the dashboard drops finished "
                             "jobs after an hour)"}, 404
    else:
        return {"ok": False, "error": "pass job=<id>, or since=&until= epochs"}, 400

    lo, hi = start - 1.0, end + 1.0
    rows = [r for r in _cx_rows()
            if r.get("epoch") is not None and lo <= r["epoch"] <= hi]
    lane = _cx_primary(rows)

    prev = _CX_LAST.get(session) if session else None
    prev_gap = None
    if prev is None:
        before = [r for r in _cx_primary(_cx_rows())
                  if r.get("epoch") is not None and r["epoch"] < lo]
        if before:
            prev = before[-1].get("prompt_tokens")
            head = lane[0].get("epoch") if lane else None
            if head is not None and before[-1].get("epoch") is not None:
                prev_gap = head - before[-1]["epoch"]

    if not lane:
        return {"ok": True, "found": False, "job": jid or None,
                "window": window, "window_source": window_src,
                "log": _CX_LOG, "requests": 0,
                "note": "no model request is logged for this turn — the model "
                        "server may not be the MLX backend that writes these lines",
                "compacted": bool(compacting or "Compacting" in (status or "")),
                "status_text": _cx_strip_emoji(status)}

    saw = bool(compacting) or ("Compacting" in (status or ""))
    out = _cx_measure(lane, window, prev, saw, status, prev_gap)
    out.update({"ok": True, "found": True, "job": jid or None,
                "window_source": window_src, "log": _CX_LOG, "done": done})

    if session:
        _CX_LAST[session] = out.get("prompt_tokens")
        _cx_trim(_CX_LAST)
    if jid and done:
        _CX_TURN[jid] = out
        _cx_trim(_CX_TURN)
    return out


def _cx_recent(ctx):
    try:
        n = int(ctx.q1("n", "20") or 20)
    except ValueError:
        n = 20
    n = max(1, min(n, 100))

    cfg = _cx_read_cfg()
    window, window_src = _cx_window(cfg)
    lane = _cx_primary(_cx_rows())

    out = []
    for i, r in enumerate(lane):
        row = _cx_row_out(r, window)
        back = lane[i - 1] if i else None
        prev = back.get("prompt_tokens") if back else None
        gap = None
        if back and back.get("epoch") is not None and r.get("epoch") is not None:
            gap = r["epoch"] - back["epoch"]
        row["prev_prompt_tokens"] = prev
        row["compacted"] = _cx_dropped(row.get("prompt_tokens"), prev, gap)
        out.append(row)

    return {"ok": True, "window": window, "window_source": window_src,
            "log": _CX_LOG, "count": len(out[-n:]), "total": len(lane),
            "turns": out[-n:]}


def _cx_compression_payload(cfg=None):
    cfg = cfg if cfg is not None else _cx_read_cfg()
    eff = _cx_effective(cfg)
    window, window_src = _cx_window(cfg)
    main = cfg.get("model_default")
    try:
        main = globals()["active_model"]() or main
    except Exception:
        pass
    return {
        "ok": True,
        "enabled": (True if cfg.get("enabled") is None else bool(cfg["enabled"])),
        "values": eff,
        "from_config": {k: cfg.get(k) for k in _CX_KEYS},
        "defaults": dict(_CX_DEFAULTS),
        "limits": {k: {"min": _CX_LIMITS[k][0], "max": _CX_LIMITS[k][1],
                       "kind": _CX_LIMITS[k][2]} for k in _CX_KEYS},
        "window": window,
        "window_source": window_src,
        "model": main,
        "model_ctx_native": _cx_roster_ctx(main),
        # the compressor floors and clamps this further (context_compressor.py
        # ::_compute_threshold_tokens), so it is the headline figure, not a
        # promise to the token
        "compact_at_tokens": int(window * eff["threshold"]),
        "target_tokens": int(window * eff["threshold"] * eff["target_ratio"]),
        "auxiliary": {
            "model": cfg.get("aux_model"),
            "provider": cfg.get("aux_provider"),
            "context_length": cfg.get("aux_context_length"),
            # auxiliary.compression.model unset (or "auto") means the summariser
            # runs on the main runtime model — _resolve_task_provider_model
            # drops the sentinel and _resolve_auto falls back to it.
            "inherits_main": not cfg.get("aux_model"),
            "effective": cfg.get("aux_model") or main,
        },
        "config_path": _CX_CFG,
        "config_key": "compression",
        "applies_to": "new conversations",
        "applies_note": "A conversation keeps the compressor it was built with; "
                        "the next new one picks these up. No restart needed.",
    }


def _cx_compression_get(ctx):
    try:
        return _cx_compression_payload()
    except Exception as e:
        return {"ok": False,
                "error": "could not read the compression settings — %s: %s"
                         % (type(e).__name__, e)}, 500


def _cx_compression_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    values = {}
    for k in _CX_KEYS:
        if k not in body:
            continue
        lo, hi, kind = _CX_LIMITS[k]
        raw = body[k]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return {"ok": False, "error": "%s must be a number" % k}, 400
        try:
            v = int(raw) if kind == "int" else float(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "%s must be a number" % k}, 400
        if v < lo or v > hi:
            return {"ok": False,
                    "error": "%s must be between %s and %s (got %s)" % (k, lo, hi, v)}, 400
        values[k] = v
    if not values:
        return {"ok": False,
                "error": "send at least one of %s" % ", ".join(_CX_KEYS)}, 400

    before = _cx_compression_payload()
    try:
        changed, backup = _cx_write(values)
    except OSError as e:
        return {"ok": False,
                "error": "could not write %s — %s" % (_CX_CFG, e)}, 500
    after = _cx_compression_payload()

    return {"ok": True, "changed": changed, "backup": backup,
            "config_path": _CX_CFG, "config_key": "compression",
            "applies_to": "new conversations",
            "applies_note": after.get("applies_note"),
            "before": before.get("values"), "after": after.get("values"),
            "compression": after}


register_get("/api/context/turn", _cx_turn)                     # noqa: F821
register_get("/api/context/recent", _cx_recent)                 # noqa: F821
register_get("/api/context/compression", _cx_compression_get)   # noqa: F821
register_post("/api/context/compression", _cx_compression_post)  # noqa: F821
