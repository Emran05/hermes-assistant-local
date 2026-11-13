#!/usr/bin/env python3
"""C09 — a switch flipped while a watchtower tick is running counts for the
next send.

watchtower_loop read the config once per pass, so a brief that spent a minute
in the model rewrite, or a breaking/rule pass blocked on a 60 s Telegram send,
went on sending after the owner had switched it off. Drives the real ticks
from aux_watchtower.py (see _wt_harness.py) with Telegram and the composers
stubbed; the stubs flip the switch in watchtower.json mid-tick.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
NOQUIET = {"start": "00:00", "end": "00:00"}      # start == end: no quiet hours


def today_at(h):
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, 0, 0, 0, 0, -1))


def main():
    print("C09: each push reads the config as it is now")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    base = {"quiet_hours": NOQUIET,
            "brief": {"enabled": True, "hour": 8, "minute": 0, "channels": ["telegram", "hub"]},
            "midday": {"enabled": True, "hour": 11, "minute": 0, "channels": ["telegram", "hub"]},
            "master": {"briefings": True, "news": True}}

    def set_master(**kw):
        d = json.load(open(wt))
        d.setdefault("master", {}).update(kw)
        H.wj(wt, d)

    sends = []
    g["_wt_send_telegram"] = lambda text: (sends.append(text) or (True, ""))
    comp = {"text": "## Your day\nhello", "synthesized": False, "sections": {}, "degraded": []}

    # control: nothing flipped -> the brief sends
    H.wj(wt, base)
    g["_brief_compose"] = lambda run_synthesis=True: dict(comp)
    g["_brief_tick"](g["_wt_load"](), g["_wt_state_load"](), today_at(9))
    check("control: the brief sends", len(sends) == 1, sends)

    # the owner switches briefings off while the brief is composing
    H.wj(wt, base)
    H.wj(os.path.join(data, "watchtower-state.json"), {})
    sends.clear()

    def compose_and_flip(run_synthesis=True):
        set_master(briefings=False)
        return dict(comp)
    g["_brief_compose"] = compose_and_flip
    g["_brief_tick"](g["_wt_load"](), g["_wt_state_load"](), today_at(9))
    check("briefings off mid-compose: nothing sent", sends == [], sends)
    check("... the date guard is left for later",
          g["_wt_state_load"]().get("last_brief_date") != time.strftime("%Y-%m-%d"),
          g["_wt_state_load"]().get("last_brief_date"))

    # midday pulse: same
    H.wj(wt, base)
    sends.clear()

    def midday_and_flip(cfg):
        set_master(briefings=False)
        return {"noteworthy": True, "items": 3, "text": "Midday pulse", "since": 0}
    g["_midday_compose"] = midday_and_flip
    g["_midday_tick"](g["_wt_load"](), g["_wt_state_load"](), today_at(12))
    check("briefings off mid-pulse: nothing sent", sends == [], sends)

    # breaking: News switched off after the first push of a multi-candidate pass
    H.wj(wt, base)
    sends.clear()
    g["_breaking_scan"] = lambda cfg: [
        {"class": "news", "sig": "news:a", "text": "BREAKING a", "context": {}},
        {"class": "ai", "sig": "ai:b", "text": "BREAKING b", "context": {}}]

    def send_then_news_off(text):
        sends.append(text)
        set_master(news=False)
        return True, ""
    g["_wt_send_telegram"] = send_then_news_off
    g["_breaking_pass"](g["_wt_load"](), g["_wt_state_load"](), time.time())
    check("News off after the first breaking push: the second is not sent",
          sends == ["BREAKING a"], sends)

    # rules: rule 2 is switched off while rule 1's push is in flight
    g["SYS_HISTORY"].append({"ram_pct": 95.0, "cpu_pct": 95.0})
    r1 = {"id": "r1", "type": "system_metric", "label": "RAM",
          "params": {"metric": "ram_pct", "op": ">", "value": 50}, "channels": ["telegram", "hub"]}
    r2 = {"id": "r2", "type": "system_metric", "label": "CPU",
          "params": {"metric": "cpu_pct", "op": ">", "value": 50}, "channels": ["telegram", "hub"]}
    H.wj(wt, dict(base, rules=[r1, r2]))
    sends.clear()

    def send_then_disable_r2(text):
        sends.append(text)
        d = json.load(open(wt))
        for r in d["rules"]:
            if r["id"] == "r2":
                r["enabled"] = False
        H.wj(wt, d)
        return True, ""
    g["_wt_send_telegram"] = send_then_disable_r2
    g["_rule_pass"](g["_wt_load"](), g["_wt_state_load"](), time.time())
    check("a rule switched off mid-pass is not sent", len(sends) == 1 and "RAM" in sends[0], sends)
    H.finish()


if __name__ == "__main__":
    main()
