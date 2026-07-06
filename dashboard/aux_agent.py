# aux_agent.py — backend for the flagship Agent page (UI restructure B2).
#
# Ships ONE tiny route: GET /api/agent/pulse — a pre-joined "heartbeat" feed for
# the Agent page's autonomous ticker (v2) and the Pulse rail. It unions three
# existing surfaces into a single call so the client polls once instead of three
# times:
#     recorder tail  (every tool the agent ran, on every surface)
#   ∪ for-you moves  (proactive findings meta)
#   ∪ watchtower feed (recent rule fires — "what it's watching")
#
# This module exec's into server.py's globals (aux-module pattern), so it reuses
# the sibling handlers already registered by aux_recorder / aux_foryou /
# aux_watchtower — resolved at REQUEST time (they load after this file in the
# sorted aux order, but the handler only calls them when a request arrives, by
# which point every aux module is loaded).
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an
# aux_*.py — it rebinds the shared global `datetime` to the class and silently
# breaks every later `datetime.datetime(...)`/`datetime.timedelta(...)`. Import
# under a private alias.
import datetime as _agent_datetime  # noqa: F401  (aliased per the aux gotcha)


def _agent_call(_name, **_q):
    """Call a sibling aux route handler by name, at request time, defensively.

    Returns its dict payload (unwrapping a (payload, status) tuple) or None."""
    fn = globals().get(_name)
    if not callable(fn):
        return None
    try:
        ctx = RouteCtx(query={k: [str(v)] for k, v in _q.items()})  # noqa: F821
    except Exception:
        # RouteCtx should always exist in server.py globals; be defensive anyway.
        return None
    try:
        r = fn(ctx)
    except Exception:
        return None
    if isinstance(r, tuple):
        r = r[0] if r else None
    return r if isinstance(r, dict) else None


def _agent_rel(ts):
    """Relative, human, no-emoji. Mirrors the client's relTime for parity."""
    try:
        s = _agent_datetime.datetime.now().timestamp() - float(ts)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        s = 0
    if s < 90:
        return "just now"
    if s < 3600:
        return str(max(1, int(round(s / 60)))) + "m ago"
    if s < 86400:
        return str(int(round(s / 3600))) + "h ago"
    return str(int(round(s / 86400))) + "d ago"


def _agent_pulse(ctx):
    """Joined heartbeat feed: recorder ∪ foryou ∪ watchtower, newest-first.

    Shape (all fields optional/best-effort so the ticker never dies on a gap):
        {
          ok, generated_at,
          events:[{source, kind, gist, tool, target, ts, rel}...],  # unified
          last_action:{...}|None,
          facts:[str,...],          # ready-to-crossfade ticker lines
          counts:{actions, foryou, watch_fires}
        }
    """
    events = []
    facts = []

    # --- recorder tail (ground truth of what it did) ----------------------
    rec = _agent_call("recorder_api_handler", limit="14") or {}
    actions = rec.get("actions") or []
    last_action = None
    for a in actions:
        if not isinstance(a, dict):
            continue
        tool = a.get("tool") or "tool"
        target = a.get("target") or ""
        gist = ("ran " + tool + (" " + target if target and target != "—"
                                 else "")).strip()
        ev = {"source": "recorder", "kind": a.get("kind") or "other",
              "gist": gist, "tool": tool, "target": target,
              "ts": a.get("ts"), "rel": _agent_rel(a.get("ts")),
              "id": a.get("id"), "status": a.get("status")}
        events.append(ev)
        if last_action is None:
            last_action = ev
    if last_action:
        facts.append("Last action " + (last_action["rel"] or "recently") +
                     " — " + last_action["gist"])

    # --- for-you proactive moves (meta only; never the content) -----------
    fy = _agent_call("foryou_get_handler") or {}
    moves = fy.get("moves") or []
    fy_count = len(moves)
    if fy.get("building"):
        facts.append("Scanning your world — finding what's worth your time")
    elif fy_count:
        facts.append("Scanned your feeds · " + str(fy_count) +
                     (" move" if fy_count == 1 else " moves") + " for you")
    for m in moves[:6]:
        if not isinstance(m, dict):
            continue
        title = (m.get("title") or m.get("summary") or "").strip()
        if not title:
            continue
        events.append({"source": "foryou", "kind": "proactive",
                       "gist": title, "tool": "for_you",
                       "target": m.get("source") or "", "ts": m.get("ts"),
                       "rel": _agent_rel(m.get("ts"))})

    # --- watchtower feed (what it's watching / recent fires) --------------
    wt = _agent_call("watchtower_feed_handler") or {}
    fires = wt.get("fires") or []
    for f in fires[:8]:
        if not isinstance(f, dict):
            continue
        label = (f.get("label") or f.get("text") or "").strip()
        if not label:
            continue
        events.append({"source": "watchtower", "kind": "watch",
                       "gist": label, "tool": f.get("type") or "watch",
                       "target": "", "ts": f.get("ts"),
                       "rel": _agent_rel(f.get("ts"))})
    if fires:
        top = (fires[0].get("label") or fires[0].get("text") or "").strip()
        if top:
            facts.append("Watching · " + top)
    else:
        facts.append("On watch — nothing needs you right now")

    # newest-first across all sources (missing ts sinks to the bottom)
    events.sort(key=lambda e: (e.get("ts") or 0), reverse=True)

    return {
        "ok": True,
        "generated_at": _agent_datetime.datetime.now().isoformat(timespec="seconds"),
        "events": events[:40],
        "last_action": last_action,
        "facts": facts,
        "counts": {"actions": len(actions), "foryou": fy_count,
                   "watch_fires": len(fires)},
    }


register_get("/api/agent/pulse", _agent_pulse)  # noqa: F821
