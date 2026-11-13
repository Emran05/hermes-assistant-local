#!/usr/bin/env python3
"""R2-17 — an evening wrap scheduled inside quiet hours is logged, not lost.

BEFORE: _evening_tick returned at the quiet-hours check on every pass, so a
wrap set at or after quiet start (default 22:00; the hour can be 16-23) never
sent, never logged and never closed its day — the date simply rolled over.
set_evening said nothing about it.
AFTER: when the slot itself is inside quiet hours that do not lift in time,
the tick logs ONE suppressed row (suppressed "quiet_hours") and marks the day
done; set_evening (and set_quiet_hours) return a `warning`. A quiet window
that lifts in time still holds the wrap and sends it afterwards, as before.
Drives the real ticks/ops (see _wt_harness.py); Telegram and the composer are
stubs.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def at(h, m=0):
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


def main():
    print("R2-17: a wrap inside quiet hours closes its day with one row")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    st = os.path.join(data, "watchtower-state.json")
    today = time.strftime("%Y-%m-%d")
    sends = []
    g["_wt_send_telegram"] = lambda text: (sends.append(text) or (True, ""))
    g["_evening_compose"] = lambda cfg: {"noteworthy": True, "items": 3, "text": "Evening wrap"}
    # the send-time re-check reads the real clock; the slots below are synthetic
    g["_wt_push_allowed"] = lambda family, sub: g["_wt_load"]()

    def setup(evening, quiet):
        H.wj(wt, {"master": {"briefings": True, "news": True},
                  "evening": dict({"enabled": True, "minute": 0}, **evening),
                  "quiet_hours": quiet})
        H.wj(st, {})
        open(os.path.join(data, "watchtower-log.jsonl"), "w").close()
        sends.clear()

    def tick(ts):
        g["_evening_tick"](g["_wt_load"](), g["_wt_state_load"](), ts)

    def rows():
        return [r for r in g["_wt_log_read"](1000) if r.get("type") == "evening_wrap"]

    # ---- the bug: 22:30 wrap under the default 22:00-07:00 quiet hours -----
    setup({"hour": 22, "minute": 30}, {"start": "22:00", "end": "07:00"})
    tick(at(22, 10))
    check("before the slot: nothing yet", rows() == [] and
          g["_wt_state_load"]().get("last_evening_date") != today, rows())
    for m in (31, 35, 40, 59):
        tick(at(22, m))
    tick(at(23, 50))
    rs = rows()
    check("THE FIX: one suppressed row for the day", len(rs) == 1, rs)
    check("... suppressed as quiet_hours, saying why",
          rs and rs[0].get("suppressed") == "quiet_hours"
          and "quiet hours" in rs[0].get("detail", "")
          and rs[0].get("signature") == "evening:" + today, rs)
    check("... the day is closed (date guard set)",
          g["_wt_state_load"]().get("last_evening_date") == today,
          g["_wt_state_load"]().get("last_evening_date"))
    check("... and nothing was sent", sends == [], sends)
    flags = g["_brief_overnight_flags"]()
    check("the held wrap is not an 'overnight flag' in the next brief",
          not any("Evening wrap" in f for f in flags), flags)

    # ---- a same-day quiet window that lifts in time still holds, then sends -
    setup({"hour": 16, "minute": 30}, {"start": "16:00", "end": "17:00"})
    tick(at(16, 40))
    check("quiet 16:00-17:00, wrap 16:30: held at 16:40 (no row, day open)",
          rows() == [] and g["_wt_state_load"]().get("last_evening_date") != today, rows())
    tick(at(17, 5))
    check("... and sent once quiet hours lift", len(sends) == 1
          and [r.get("suppressed") for r in rows()] == [""], (sends, rows()))

    # ---- a same-day window that lifts only after the catch-up closes -------
    setup({"hour": 18}, {"start": "16:00", "end": "23:30"})
    tick(at(18, 30))
    check("quiet 16:00-23:30, wrap 18:00: blocked -> one row, day closed",
          [r.get("suppressed") for r in rows()] == ["quiet_hours"]
          and g["_wt_state_load"]().get("last_evening_date") == today and sends == [],
          rows())

    # ---- the ordinary case is unchanged -----------------------------------
    setup({"hour": 18}, {"start": "22:00", "end": "07:00"})
    tick(at(18, 5))
    check("control: an 18:00 wrap outside quiet hours sends",
          len(sends) == 1 and [r.get("suppressed") for r in rows()] == [""], rows())

    # ---- the ops warn ------------------------------------------------------
    setup({"hour": 18}, {"start": "22:00", "end": "07:00"})
    post = g["watchtower_post_handler"]
    r = post(H.Ctx(body={"op": "set_evening", "hour": 18, "minute": 0}))
    check("set_evening 18:00: no warning", r.get("ok") and "warning" not in r, r)
    r = post(H.Ctx(body={"op": "set_evening", "hour": 22, "minute": 30}))
    check("set_evening 22:30 under 22:00 quiet: saved, with a warning",
          r.get("ok") and r["evening"]["hour"] == 22
          and "quiet hours" in r.get("warning", "") and "10:30 PM" in r.get("warning", ""), r)
    r = post(H.Ctx(body={"op": "set_evening", "hour": 22, "minute": 0}))
    check("set_evening AT quiet start (22:00) warns too", "warning" in r, r)
    post(H.Ctx(body={"op": "set_evening", "hour": 19}))
    r = post(H.Ctx(body={"op": "set_quiet_hours", "start": "18:30", "end": "07:00"}))
    check("set_quiet_hours that swallows a 19:00 wrap warns",
          r.get("ok") and "warning" in r, r)
    r = post(H.Ctx(body={"op": "set_quiet_hours", "start": "22:00", "end": "07:00"}))
    check("set_quiet_hours that leaves it clear does not", r.get("ok") and "warning" not in r, r)
    post(H.Ctx(body={"op": "set_evening", "hour": 23, "enabled": False}))
    r = post(H.Ctx(body={"op": "set_evening", "minute": 15}))
    check("a disabled wrap is not warned about", r.get("ok") and "warning" not in r, r)
    H.finish()


if __name__ == "__main__":
    main()
