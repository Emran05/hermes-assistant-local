#!/usr/bin/env python3
"""R2-19 — a failed Telegram delivery backs off, and the hub is not repeated.

BEFORE: when a fired rule's Telegram send failed, _fire_rule returned without
stamping anything, so the still-true rule fired again on every 60 s pass:
another `hermes send` (60 s timeout each) and another hub delivery attempt,
for as long as Telegram stayed down.
AFTER: a per-rule `tg_retry` (attempt count + last_attempt) waits 1, 2, 4 …
minutes between Telegram attempts, never longer than the rule's cooldown, and
stops once a whole cooldown has passed. The hub gets the alert ONCE, on the
first attempt; the Telegram-only retry that finally succeeds completes that
same row (delivered hub -> hub + telegram) instead of adding a second one.

Drives the real _rule_pass / _fire_rule (see _wt_harness.py); Telegram is a
stub, the system-metric evaluator reads a stubbed sample.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
NOQUIET = {"start": "03:00", "end": "04:00"}


def noon():
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))


def main():
    print("R2-19: Telegram retries back off; the hub gets each alert once")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    st = os.path.join(data, "watchtower-state.json")
    log = os.path.join(data, "watchtower-log.jsonl")
    g["SYS_HISTORY"].append({"ram_pct": 95.0, "cpu_pct": 10.0})
    sends, up = [], [False]

    def send(text):
        sends.append(text)
        return (True, "") if up[0] else (False, "TimeoutExpired: hermes send exceeded 60s")
    g["_wt_send_telegram"] = send
    t0 = noon()

    def setup(rule):
        H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": True, "news": True},
                  "rules": [rule]})
        H.wj(st, {})
        open(log, "w").close()
        del sends[:]
        g["_wt_supp_last"].clear()

    def run(ts):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), ts)

    def rows(rid):
        return [r for r in g["_wt_log_read"](1000) if r.get("rule_id") == rid]

    both = {"id": "r-ram", "type": "system_metric", "label": "RAM high",
            "params": {"metric": "ram_pct", "op": ">", "value": 80},
            "cooldown_min": 120, "channels": ["telegram", "hub"]}

    # ---- Telegram down for ten minutes ------------------------------------
    setup(both)
    up[0] = False
    for i in range(11):
        run(t0 + 60 * i)
    check("THE FIX: 11 passes make 4 Telegram attempts (t+0, +1, +3, +7 min), not 11",
          len(sends) == 4, len(sends))
    fs = g["_wt_state_load"]()["fires"].get("r-ram", {})
    rt = fs.get("tg_retry") or {}
    check("the rule keeps its attempt count and last_attempt",
          rt.get("n") == 4 and rt.get("last_attempt") == t0 + 420, rt)
    hub = [r for r in rows("r-ram") if not r.get("suppressed")]
    check("the hub got the alert exactly once, on the first attempt",
          len(hub) == 1 and hub[0].get("delivered") == ["hub"]
          and hub[0].get("pending") == ["telegram"] and hub[0].get("ts") == t0, hub)
    check("... and says why Telegram is missing",
          hub and "telegram" in hub[0].get("detail", "") and "Timeout" in hub[0]["detail"], hub)
    failed = [r for r in rows("r-ram") if r.get("suppressed") == "deliver_failed"]
    check("the failing retry streak is logged once (B22), never per pass",
          len(failed) == 1 and failed[0].get("channels") == ["telegram"], failed)
    check("the hub delivery counted once against the daily cap",
          g["_wt_state_load"]()["day"].get("sent") == 1, g["_wt_state_load"]()["day"])

    # ---- Telegram comes back: the retry completes THE SAME row --------------
    up[0] = True
    run(t0 + 600)
    check("+10 min (inside the 8 min backoff): still waiting", len(sends) == 4, len(sends))
    run(t0 + 900)
    check("+15 min: the Telegram-only retry goes out", len(sends) == 5, len(sends))
    hub = [r for r in rows("r-ram") if not r.get("suppressed")]
    check("no second hub row: the first row now reads delivered hub + telegram",
          len(hub) == 1 and hub[0].get("delivered") == ["hub", "telegram"]
          and "pending" not in hub[0] and hub[0].get("telegram_at") == t0 + 900, hub)
    st_now = g["_wt_state_load"]()
    check("the retry is cleared, the day still counts one push",
          "tg_retry" not in st_now["fires"]["r-ram"] and st_now["day"]["sent"] == 1, st_now)
    for i in range(1, 6):
        run(t0 + 900 + 60 * i)
    check("afterwards the normal cooldown holds: nothing more is sent",
          len(sends) == 5, len(sends))

    # ---- the backoff never exceeds the cooldown, and retries end with it ----
    short = dict(both, id="r-short", cooldown_min=5)
    setup(short)
    up[0] = False
    for i in range(60):                    # an hour of 60 s passes
        run(t0 + 60 * i)
    check("cooldown 5 min: attempts at +0, +1, +3 min, then the retry window "
          "closes and the same alert dedupes", len(sends) == 3, len(sends))
    check("... still exactly one hub row for the alert",
          len([r for r in rows("r-short") if not r.get("suppressed")]) == 1, rows("r-short"))
    check("the backoff helper caps at the cooldown",
          [g.get("_wt_backoff_s", lambda n, c: None)(n, 5) for n in (1, 2, 3, 4, 9)] == [60, 120, 240, 300, 300])

    # ---- a Telegram-only rule: no hub row, one delivered row on recovery ----
    tg = dict(both, id="r-tg", channels=["telegram"])
    setup(tg)
    up[0] = False
    for i in range(4):
        run(t0 + 60 * i)
    check("telegram-only: 4 passes, 3 attempts (t+0, +1, +3 min)", len(sends) == 3, len(sends))
    check("... one deliver_failed row, nothing delivered, nothing counted",
          [r.get("suppressed") for r in rows("r-tg")] == ["deliver_failed"]
          and g["_wt_state_load"]()["day"].get("sent", 0) == 0, rows("r-tg"))
    up[0] = True
    run(t0 + 420)
    ok_rows = [r for r in rows("r-tg") if not r.get("suppressed")]
    st_now = g["_wt_state_load"]()
    check("recovered: one delivered telegram row, counted, cooldown started",
          len(sends) == 4 and len(ok_rows) == 1 and ok_rows[0].get("delivered") == ["telegram"]
          and st_now["day"]["sent"] == 1 and st_now["fires"]["r-tg"].get("last_fired") == t0 + 420
          and "tg_retry" not in st_now["fires"]["r-tg"], (ok_rows, st_now))

    # ---- quiet hours hold a pending retry ----------------------------------
    setup(both)
    up[0] = False
    run(t0)
    H.wj(wt, {"quiet_hours": {"start": "11:00", "end": "13:00"},
              "master": {"briefings": True, "news": True}, "rules": [both]})
    up[0] = True
    run(t0 + 300)
    check("a pending Telegram retry waits out quiet hours", len(sends) == 1, len(sends))
    H.finish()


if __name__ == "__main__":
    main()
