# aux_network.py — the evidence behind the Data & Network card's "Last
# outbound" column (C52, 2026-10-21 audit).
#
#   GET /api/network/last  ->  {ok, last: {fact_id: unix_ts | null},
#                               updates_signed: bool, google_connected: bool}
#
# The card used to build that column from four status reads, and two of them
# answered a different question: "Feeds" showed the newest watchtower ALERT
# (Sep 1) while the feeds had been fetched seventeen minutes earlier, and
# "Google" showed when the status FILE was read. Every value here is when a
# request really LEFT this Mac, read from a store the dashboard already keeps:
#
#   updates        update-check.json `checked_at` (the last real GitHub check)
#   feeds          intel.json `updated` (the last feed gather)
#   briefings      newest midday/evening row delivered to Telegram in the
#                  watchtower log (the 8 AM brief is not logged there)
#   alerts         newest other watchtower row delivered to Telegram
#   claude         newest bridge-log row that spawned `claude -p`
#   foryou_claude  the same, for caller "foryou"
#   google         aux_google's _GOOG_NET.last_out (module state, C21)
#
# A fact id that is ABSENT from `last` is a destination the dashboard cannot
# observe (the Telegram gateway, the agent's web search, model downloads...),
# and the card says "not tracked"; null means "tracked, no record yet".
#
# Read-only and cheap: file reads only — no network, no subprocess, nothing
# that can wake a model. It deliberately does NOT call /api/update/check (a
# stale cache there makes a GET go to GitHub) or /api/intel (whose GET runs
# the web-search probe).
#
# Load order: aux files exec SORTED, so this module runs BEFORE aux_update
# and aux_watchtower — every foreign global is resolved BY NAME AT CALL TIME,
# with this module's own literals as the fallback (the aux_index discipline).

import os as _net_os
import json as _net_json

_NET_BRIEF_TYPES = ("midday_brief", "evening_wrap", "morning_brief", "brief")
_NET_TAIL_BYTES = 262144        # newest ~256 KB of a jsonl log is plenty


def _net_g(name, default=None):
    return globals().get(name, default)


def _net_data():
    d = _net_g("DATA")
    return d if isinstance(d, str) and d else \
        _net_os.path.join(HOME, ".hermes", "dashboard")          # noqa: F821


def _net_ts(v):
    """A positive, finite unix timestamp, or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (f > 0 and f == f and f != float("inf")) else None


def _net_read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return _net_json.load(f)
    except Exception:
        return None


def _net_jsonl_tail(path):
    rows = []
    try:
        sz = _net_os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if sz > _NET_TAIL_BYTES:
                f.seek(sz - _NET_TAIL_BYTES)
                f.readline()                     # drop the partial first line
            for ln in f.read().splitlines():
                try:
                    r = _net_json.loads(ln)
                except Exception:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except Exception:
        pass
    return rows


def _net_newest(rows, pred):
    best = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = _net_ts(r.get("ts"))
        if ts and (best is None or ts > best) and pred(r):
            best = ts
    return best


def _net_last():
    data = _net_data()
    last = {}

    # GitHub: the last REAL check (a cache hit keeps the old checked_at)
    upd = _net_read_json(_net_g("_UPD_CACHE_FILE") or
                         _net_os.path.join(data, "update-check.json"))
    upd = upd if isinstance(upd, dict) else {}
    checked = _net_ts(upd.get("checked_at"))
    payload = upd.get("payload") if isinstance(upd.get("payload"), dict) else {}
    # anything but the anonymous Releases call carried an identity: a GitHub
    # token, or git with the owner's own credentials
    signed = payload.get("source") in ("github-token", "git-tags", "main")
    # E85: a check that REACHED GitHub but got no good answer (401/403/404/5xx)
    # is recorded as attempted_at/attempt_signed; checked_at stays the last
    # good answer. The newer of the two is the last outbound
    attempted = _net_ts(upd.get("attempted_at"))
    if attempted is not None and (checked is None or attempted > checked):
        last["updates"] = attempted
        signed = upd.get("attempt_signed") is True
    else:
        last["updates"] = checked

    # News sites: when the feeds were last gathered — not the alert log
    intel = _net_read_json(_net_g("INTEL_FILE") or
                           _net_os.path.join(data, "intel.json"))
    last["feeds"] = _net_ts((intel or {}).get("updated")) if isinstance(intel, dict) else None

    # Telegram pushes the dashboard made (the gateway's own traffic is not
    # observable from here, so "telegram" stays absent = not tracked)
    rd = _net_g("_wt_log_read")
    rows = None
    if callable(rd):
        try:
            rows = rd(1000)
        except Exception:
            rows = None
    if not isinstance(rows, list):
        rows = _net_jsonl_tail(_net_g("WT_LOG") or
                               _net_os.path.join(data, "watchtower-log.jsonl"))

    def _tg(r):
        d = r.get("delivered")
        return isinstance(d, list) and "telegram" in d

    last["briefings"] = _net_newest(
        rows, lambda r: _tg(r) and r.get("type") in _NET_BRIEF_TYPES)
    last["alerts"] = _net_newest(
        rows, lambda r: _tg(r) and r.get("type") not in _NET_BRIEF_TYPES)

    # Claude: a row with a model is a call that spawned `claude -p`; refusals
    # and "escalation off" never leave the Mac and carry model None. Read the
    # log file itself, not _cb_log_tail(): that one only looks at the last
    # 4 KB (~20 rows), so a day of hourly For-You rows pushed the newest
    # escalation out of view and its Last outbound read "no record".
    crow = _net_jsonl_tail(_net_g("CB_LOG_PATH") or _net_os.path.join(
        HOME, ".hermes", "dashboard", "claude-bridge-log.jsonl"))  # noqa: F821
    last["claude"] = _net_newest(
        crow, lambda r: bool(r.get("model")) and r.get("caller") != "foryou")
    last["foryou_claude"] = _net_newest(
        crow, lambda r: bool(r.get("model")) and r.get("caller") == "foryou")

    # Google: when a request last went to Google (aux_google, C21)
    gnet = _net_g("_GOOG_NET")
    last["google"] = _net_ts((gnet or {}).get("last_out")) if isinstance(gnet, dict) else None

    # GitHub, the repositories you connect (aux_github, round 3): reads and
    # checks, and pushes you approved. Both are ledger/state file reads.
    for key, fn in (("github_repos", "_gh_last_read_ts"), ("github_push", "_gh_last_push_ts")):
        f = _net_g(fn)
        try:
            last[key] = _net_ts(f()) if callable(f) else None
        except Exception:
            last[key] = None
    tok = _net_g("GOOG_TOKEN") or _net_os.path.join(HOME, ".hermes",   # noqa: F821
                                                     "google_token.json")
    return {"ok": True, "last": last, "updates_signed": bool(signed),
            "google_connected": _net_os.path.isfile(tok)}


def _net_last_handler(ctx):
    try:
        return _net_last()
    except Exception as e:
        # degrade, never 500 the Settings panel: the card drops the column
        return {"ok": False, "error": type(e).__name__}


register_get("/api/network/last", _net_last_handler)                   # noqa: F821
