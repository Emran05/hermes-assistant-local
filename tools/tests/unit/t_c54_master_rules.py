#!/usr/bin/env python3
"""C54 — with both master switches off, no watch rule pushes to Telegram.

The masters are documented as the complete off-switch for every watchtower
push, but 'news' only gated breaking alerts and rss_keyword rules: ticker,
index, crypto and system watches still went to Telegram with both masters
off. Drives the real _rule_pass from aux_watchtower.py (see _wt_harness.py);
Telegram is a stub, the system-metric evaluator reads a stubbed sample.
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
    print("C54: the masters gate every watch push")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    g["SYS_HISTORY"].append({"ram_pct": 95.0, "cpu_pct": 10.0})
    rule = {"id": "r-ram", "type": "system_metric", "label": "RAM high",
            "params": {"metric": "ram_pct", "op": ">", "value": 80},
            "cooldown_min": 30, "channels": ["telegram", "hub"]}
    sends = []
    g["_wt_send_telegram"] = lambda text: (sends.append(text) or (True, ""))
    t0 = noon()

    H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": False, "news": False},
              "rules": [rule]})
    cfg = g["_wt_load"]()
    check("the system rule is valid and would fire",
          len(cfg["rules"]) == 1 and g["_evaluate"](cfg["rules"][0])[0] is True, cfg["rules"])
    g["_rule_pass"](cfg, g["_wt_state_load"](), t0)
    check("both masters off: a system watch sends nothing", sends == [], sends)

    H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": True, "news": False},
              "rules": [rule]})
    g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 60)
    check("News off (briefings on): a watch sends nothing", sends == [], sends)

    H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": False, "news": True},
              "rules": [rule]})
    g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 120)
    check("News on: the watch fires", len(sends) == 1, sends)

    # News switched off while the pass is running (an earlier send took a minute)
    rule2 = dict(rule, id="r-ram2", label="RAM high 2")
    H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": False, "news": True},
              "rules": [dict(rule, id="r-a", label="A"), rule2]})

    def send_then_off(text):
        sends.append(text)
        H.wj(wt, {"quiet_hours": NOQUIET, "master": {"briefings": False, "news": False},
                  "rules": [dict(rule, id="r-a", label="A"), rule2]})
        return True, ""
    g["_wt_send_telegram"] = send_then_off
    before = len(sends)
    g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), t0 + 7200)
    check("News off mid-pass: the next watch is not sent", len(sends) == before + 1,
          len(sends) - before)
    H.finish()


if __name__ == "__main__":
    main()
