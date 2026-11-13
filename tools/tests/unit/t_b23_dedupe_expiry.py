#!/usr/bin/env python3
"""B23 — a rule's dedupe signature must not suppress the same move forever.

_wt_gate compared the signature with last_signature and no age, and the
market signature was just symbol:bucket:direction, so after one NVDA -6% day
every later -6% day was "dedupe" for the life of the state file. Drives the
real _rule_pass / _fire_rule / evaluators from aux_watchtower.py (see
_wt_harness.py); Telegram is a stub.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def at(days_ago, hour):
    lt = time.localtime(time.time() - days_ago * 86400)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 0, 0, 0, 0, -1))


def main():
    print("B23: the same move on a later session alerts again")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    rule = {"id": "r-nvda", "type": "ticker_move", "label": "NVDA moves",
            "params": {"symbol": "NVDA", "threshold_pct": 5, "direction": "any"},
            "cooldown_min": 120, "channels": ["telegram", "hub"]}
    H.wj(wt, {"quiet_hours": {"start": "03:00", "end": "04:00"}, "rules": [rule]})
    quote = {"symbol": "NVDA", "pct": -6.1, "price": 100.0, "state": "REGULAR"}
    g["EXPANDERS"]["markets"] = lambda: {"indices": [], "watchlist": [dict(quote)],
                                         "state": quote["state"], "asof": quote.get("asof")}
    sends = []
    g["_wt_send_telegram"] = lambda text: (sends.append(text) or (True, ""))

    def run(now):
        g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), now)

    day1, day2, day3 = at(3, 10), at(2, 10), at(1, 10)
    quote["asof"] = day1
    run(day1)
    check("day 1: NVDA -6% alerts", len(sends) == 1, sends)
    run(day1 + 3 * 3600)
    check("day 1, later the same session: still deduped", len(sends) == 1, len(sends))

    # day 2: the market is closed, the quote is still day 1's session
    quote["state"] = "CLOSED"
    run(day2)
    check("next day, stale quote of the same session: still deduped", len(sends) == 1, len(sends))

    # day 3: a NEW session moves -6% again
    quote["asof"], quote["state"] = day3, "REGULAR"
    run(day3)
    check("a later session's -6% alerts again", len(sends) == 2, len(sends))

    g["SYS_HISTORY"].append({"ram_pct": 50.0})
    fire, sig, _ctx = g["_eval_system"]({"metric": "ram_pct", "op": ">", "value": 1})
    check("system signature carries the day", time.strftime("%Y-%m-%d") in sig, sig)
    g["w_crypto"] = lambda: {"coins": [{"id": "bitcoin", "pct": -7.0, "price": 1.0}]}
    fire, sig, _ctx = g["_eval_crypto"]({"coin": "bitcoin", "threshold_pct": 5})
    check("crypto signature carries the day", fire and time.strftime("%Y-%m-%d") in sig, sig)
    H.finish()


if __name__ == "__main__":
    main()
