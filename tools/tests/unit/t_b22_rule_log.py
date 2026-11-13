#!/usr/bin/env python3
"""B22 — a still-true rule must not append a 'suppressed' row every pass.

The rule pass runs every 60 s; every suppressed pass (cooldown, dedupe, quiet
hours, daily cap, a failed delivery) appended a log row, so one tripped rule
wrote ~1,440 rows a day and flushed real fires out of every bounded reader
(Recent fires, the overnight flags, Needs-you). Drives the real _rule_pass /
_fire_rule from aux_watchtower.py (see _wt_harness.py); Telegram is a stub.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def noon():
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))


def rows(g):
    return g["_wt_log_read"](10000)


def main():
    print("B22: one log row per suppression streak")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    g["SYS_HISTORY"].append({"ram_pct": 95.0, "cpu_pct": 10.0})
    rule = {"id": "r-ram", "type": "system_metric", "label": "RAM high",
            "params": {"metric": "ram_pct", "op": ">", "value": 80},
            "cooldown_min": 30, "channels": ["telegram", "hub"]}
    sends = []
    g["_wt_send_telegram"] = lambda text: (sends.append(text) or (True, ""))
    t0 = noon()

    # quiet hours cover noon: held, 10 passes -> one row
    H.wj(wt, {"quiet_hours": {"start": "11:00", "end": "13:00"}, "rules": [rule]})
    for i in range(10):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 60 * i)
    qrows = [r for r in rows(g) if r.get("suppressed") == "quiet_hours"]
    check("quiet hours: 10 passes write ONE held row", len(qrows) == 1, len(qrows))
    check("quiet hours: nothing was sent", sends == [], sends)

    # quiet off: fires once, then 9 passes in cooldown write nothing
    H.wj(wt, {"quiet_hours": {"start": "03:00", "end": "04:00"}, "rules": [rule]})
    for i in range(10):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 900 + 60 * i)
    fired = [r for r in rows(g) if not r.get("suppressed")]
    cool = [r for r in rows(g) if r.get("suppressed") == "cooldown"]
    check("fires once", len(fired) == 1 and len(sends) == 1, (len(fired), len(sends)))
    check("cooldown passes write no rows", cool == [], len(cool))

    # past the cooldown, same signature: dedupe, still no rows
    for i in range(5):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 900 + 3600 + 60 * i)
    dd = [r for r in rows(g) if r.get("suppressed") == "dedupe"]
    check("dedupe passes write no rows", dd == [], len(dd))
    check("... and send nothing", len(sends) == 1, len(sends))

    # a failing delivery: retried every pass, logged once per streak
    rule2 = dict(rule, id="r-cpu", label="CPU", params={"metric": "cpu_pct", "op": ">", "value": 5})
    H.wj(wt, {"quiet_hours": {"start": "03:00", "end": "04:00"}, "rules": [rule2]})
    g["_wt_send_telegram"] = lambda text: (False, "telegram down")
    for i in range(6):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 7200 + 60 * i)
    df = [r for r in rows(g) if r.get("suppressed") == "deliver_failed"]
    check("failed delivery: 6 passes write ONE row", len(df) == 1, len(df))

    # once delivery recovers it fires and a later failure streak is logged again
    g["_wt_send_telegram"] = lambda text: (True, "")
    g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 7600)
    check("recovered delivery fires", any(r.get("rule_id") == "r-cpu" and not r.get("suppressed")
                                          for r in rows(g)))
    H.finish()


if __name__ == "__main__":
    main()
