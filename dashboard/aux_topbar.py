# aux_topbar.py — one tiny route feeding the header chips (aux_topbar.js):
# current weather + Claude plan usage, in a single call the header polls. Wraps
# the existing weather() and w_claude_usage() globals (both already _cached), so
# it adds no new fetching. The date chip is client-side (MM/DD/YYYY), no backend.
#
# AUX MODULE GOTCHA (CLAUDE.md): no `from datetime import datetime` here (unused).


def _topbar(ctx):
    out = {"ok": True, "weather": None, "claude": None}
    wfn = globals().get("weather")
    if callable(wfn):
        try:
            w = wfn() or {}
            out["weather"] = {
                "city": w.get("city"), "temp": w.get("temp"),
                "desc": w.get("desc"), "hi": w.get("hi"), "lo": w.get("lo"),
                "configured": bool(w.get("configured")), "error": w.get("error")}
        except Exception:
            pass
    cfn = globals().get("w_claude_usage")
    if callable(cfn):
        try:
            cu = cfn() or {}
            win = cu.get("window") if isinstance(cu.get("window"), dict) else {}
            today = cu.get("today") if isinstance(cu.get("today"), dict) else {}
            src = win or today                 # the active rolling window is the plan-usage signal
            cap, total = cu.get("cap"), src.get("total")
            pct = None                          # only meaningful once the user sets a cap
            if isinstance(cap, (int, float)) and cap and isinstance(total, (int, float)):
                pct = int(round(100.0 * total / cap))
            out["claude"] = {"msgs": src.get("msgs"), "cost": src.get("cost"),
                             "reset_in": win.get("reset_in"), "pct": pct, "cap": cap,
                             "available": bool(cu.get("available"))}
        except Exception:
            pass
    return out


register_get("/api/topbar", _topbar)  # noqa: F821
