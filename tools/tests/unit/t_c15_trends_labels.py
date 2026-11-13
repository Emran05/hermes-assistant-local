#!/usr/bin/env python3
"""C15 — trends.json 'labels' is pruned with 'days'.

_trends_update capped 'days' at TRENDS_KEEP_DAYS but only ever added to
'labels' (entity -> display form), so the file grew without bound with labels
for entities no kept day still counts. Drives the real _trends_update from
aux_watchtower.py (see _wt_harness.py) in a throwaway HOME.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def main():
    print("C15: trend labels follow the kept days")
    g, _home, data = H.load()
    path = os.path.join(data, "trends.json")
    keep = g["TRENDS_KEEP_DAYS"]
    days = {"2020-01-%02d" % (i + 1): {"stale%d" % i: 3} for i in range(keep + 5)}
    days["2020-01-%02d" % (keep + 5)]["kept"] = 2           # newest old day
    labels = {k: k.title() for c in days.values() for k in c}
    H.wj(path, {"days": days, "labels": labels})

    now = time.time()
    items = [{"title": "Anthropic and OpenAI news", "ts": now - 60}]
    g["_trends_update"](items)
    d = json.load(open(path))
    live = {k for c in d["days"].values() for k in c}
    check("days are capped", len(d["days"]) == keep, len(d["days"]))
    check("today's entities are labelled",
          d["labels"].get("anthropic") == "Anthropic" and d["labels"].get("openai") == "OpenAI",
          d["labels"])
    check("a label for a kept day survives", d["labels"].get("kept") == "Kept", d["labels"])
    check("labels of pruned days are gone",
          all(k in live for k in d["labels"]), sorted(set(d["labels"]) - live)[:5])
    check("rising still resolves display names",
          all(isinstance(e.get("entity"), str) for e in g["_trends_rising"](16)))
    H.finish()


if __name__ == "__main__":
    main()
