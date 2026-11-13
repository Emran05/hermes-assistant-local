#!/usr/bin/env python3
"""R2-18 + X-prompts-egress-3 — the 8 AM brief is logged, and a failed send
is retried before the day is given up.

BEFORE: _brief_tick sent with `ok, det = _wt_send_telegram(...)`, wrote one
stderr line on failure and flipped the date guard: that day's brief was lost,
and since no watchtower-log row was ever written, the Data & Network card's
"Last outbound" for Telegram briefings (aux_network _net_last, which already
accepts type morning_brief) never counted the brief at all.
AFTER: every outcome is a `morning_brief` row (delivered, or suppressed
"deliver_failed" with the detail); a failed send is retried at +5 min and
then +15 min with the SAME composed text (no re-compose, no model), and only
then is the day given up. A manual POST /api/brief/send logs its row too.

Drives the real ticks (see _wt_harness.py) and the real aux_network.py
_net_last exec'd into the same namespace. Telegram and the composer are stubs.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
NOQUIET = {"start": "00:00", "end": "00:00"}


def at(h, m=0):
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


def main():
    print("R2-18: the morning brief leaves a receipt and survives a flaky send")
    g, _home, data = H.load()
    with open(os.path.join(H.DASH, "aux_network.py"), encoding="utf-8") as f:
        exec(compile(f.read(), "aux_network.py", "exec"), g)
    wt = os.path.join(data, "watchtower.json")
    st = os.path.join(data, "watchtower-state.json")
    log = os.path.join(data, "watchtower-log.jsonl")
    today = time.strftime("%Y-%m-%d")
    composed, sends, outcome = [], [], []
    g["_prewarm"] = lambda: None
    g["_wt_push_allowed"] = lambda family, sub: g["_wt_load"]()   # real clock vs synthetic slots

    def compose(run_synthesis=True):
        composed.append(run_synthesis)
        return {"text": "## Your day\nbrief #%d" % len(composed), "synthesized": False,
                "sections": {}, "degraded": []}
    g["_brief_compose"] = compose

    def send(text):
        sends.append(text)
        return outcome.pop(0) if outcome else (True, "")
    g["_wt_send_telegram"] = send

    def reset(results):
        H.wj(wt, {"master": {"briefings": True, "news": True}, "quiet_hours": NOQUIET,
                  "brief": {"enabled": True, "hour": 8, "minute": 0,
                            "channels": ["telegram", "hub"]}})
        H.wj(st, {})
        open(log, "w").close()
        del composed[:], sends[:], outcome[:]
        outcome.extend(results)
        (g.get("_brief_pending") or {}).clear()

    def tick(ts):
        g["_brief_tick"](g["_wt_load"](), g["_wt_state_load"](), ts)

    def rows():
        return [r for r in g["_wt_log_read"](1000) if r.get("type") == "morning_brief"]

    def done():
        return g["_wt_state_load"]().get("last_brief_date") == today

    # ---- a good morning: one delivered row, counted as Last outbound -------
    reset([(True, "")])
    tick(at(9))
    rs = rows()
    check("THE FIX: a delivered brief writes a morning_brief row",
          len(rs) == 1 and rs[0].get("delivered") == ["telegram", "hub"]
          and rs[0].get("suppressed") == "", rs)
    check("... the row carries no brief text (that stays in briefing.json)",
          rs and "text" not in rs[0] and "brief #1" not in json.dumps(rs[0]), rs)
    check("... and the day is done", done() and len(sends) == 1)
    last = g["_net_last"]().get("last", {})
    check("X-prompts-egress-3: Data & Network 'Last outbound' for briefings is that send",
          rs and last.get("briefings") == rs[0]["ts"], (last.get("briefings"), rs))

    # ---- Telegram down all morning: 3 attempts, 3 rows, one compose ---------
    reset([(False, "TimeoutExpired"), (False, "TimeoutExpired"), (False, "chat not found")])
    tick(at(9))
    rs = rows()
    check("first failure: a deliver_failed row with the detail",
          len(rs) == 1 and rs[0].get("suppressed") == "deliver_failed"
          and "TimeoutExpired" in rs[0].get("detail", ""), rs)
    check("... a retry is scheduled +5 min out, the day stays open",
          rs and rs[0]["context"].get("retry_at") == at(9, 5) and not done(),
          (rs, g["_wt_state_load"]().get("brief_retry")))
    tick(at(9, 2))
    check("before the retry time: nothing is sent", len(sends) == 1, sends)
    tick(at(9, 5))
    check("+5 min: the second attempt re-sends the SAME brief (no re-compose)",
          len(sends) == 2 and sends[1] == sends[0] and composed == [True], (sends, composed))
    rs = rows()
    check("... logged, with the next retry +15 min out",
          len(rs) == 2 and rs[1]["context"].get("retry_at") == at(9, 20) and not done(), rs)
    tick(at(9, 10))
    check("between retries: nothing is sent", len(sends) == 2, sends)
    tick(at(9, 20))
    rs = rows()
    check("+15 min: the third attempt, then the day is given up",
          len(sends) == 3 and len(rs) == 3 and "gave up" in rs[2].get("detail", "")
          and done(), rs)
    tick(at(9, 40))
    check("... and nothing more is sent today", len(sends) == 3 and len(rows()) == 3)
    check("one compose for the whole morning", composed == [True], composed)
    check("no failed attempt counts as 'Last outbound'",
          g["_net_last"]().get("last", {}).get("briefings") is None,
          g["_net_last"]().get("last", {}))

    # ---- recovered on the retry, after a dashboard restart -------------------
    reset([(False, "network down"), (True, "")])
    tick(at(9))
    (g.get("_brief_pending") or {}).clear()   # a restart loses the in-memory copy
    tick(at(9, 6))
    rs = rows()
    check("a retry after a restart re-sends today's brief from briefing.json",
          len(sends) == 2 and sends[1] == sends[0] and composed == [True], (sends, composed))
    check("... and the delivered row closes the day",
          [r.get("suppressed") for r in rs] == ["deliver_failed", ""] and done(), rs)

    # ---- the receipt is not mistaken for a watch fire -----------------------
    reset([(True, "")])
    tick(at(9))
    mid = g["_midday_compose"](g["_wt_load"]())
    check("the midday pulse does not count the 8 AM brief as a Watchtower flag",
          "Morning brief" not in mid["text"] and mid["items"] == 0, mid)
    check("... nor is it an overnight flag", not any(
        "Morning brief" in f for f in g["_brief_overnight_flags"]()))

    # ---- a manual send logs its receipt too ---------------------------------
    reset([(True, "")])
    out = g["brief_send_handler"](H.Ctx(body={"dry_run": False}))
    rs = rows()
    check("POST /api/brief/send (manual) logs a morning_brief row",
          isinstance(out, dict) and out.get("ok") and len(rs) == 1
          and rs[0].get("label") == "Morning brief (manual)"
          and rs[0].get("delivered") == ["telegram", "hub"], (out, rs))
    reset([(False, "boom")])
    g["brief_send_handler"](H.Ctx(body={"dry_run": False}))
    rs = rows()
    check("... a failed manual send logs deliver_failed",
          len(rs) == 1 and rs[0].get("suppressed") == "deliver_failed"
          and rs[0].get("delivered") == [], rs)

    # ---- review: a manual send during a pending retry is today's brief ------
    # (the owner sees no 8 AM brief and presses Send; the scheduled retry must
    # not then deliver a second copy)
    reset([(False, "network down"), (True, ""), (True, "")])
    tick(at(9))
    g["brief_send_handler"](H.Ctx(body={"dry_run": False}))
    tick(at(9, 5))
    tick(at(9, 25))
    check("review: a manual send while a retry is pending closes the day "
          "(no duplicate from the scheduled retry)",
          len(sends) == 2 and done() and "brief_retry" not in g["_wt_state_load"](),
          (len(sends), g["_wt_state_load"]()))
    reset([(True, ""), (True, "")])
    g["brief_send_handler"](H.Ctx(body={"dry_run": False}))
    check("... but a manual send with no retry pending leaves the 8 AM "
          "schedule alone", not done(), g["_wt_state_load"]())
    H.finish()


if __name__ == "__main__":
    main()
