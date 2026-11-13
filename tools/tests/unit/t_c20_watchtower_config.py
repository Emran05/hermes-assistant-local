#!/usr/bin/env python3
"""C20 — an unreadable or corrupt watchtower.json must fail CLOSED.

read_json() folds "absent" and "corrupt" into the same default and every
default in _wt_load() is ON, so a hand-edit typo switched the owner's OFF
briefing/news masters back on, and the next settings write persisted that.
Exec's the real aux_watchtower.py (see _wt_harness.py); no network, no model.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def main():
    print("C20: watchtower.json absent vs corrupt vs unreadable")
    g, home, data = H.load()
    wt = os.path.join(data, "watchtower.json")

    # absent -> first-run defaults, nothing flagged
    cfg = g["_wt_load"]()
    check("absent file: defaults (masters on)",
          cfg["master"] == {"briefings": True, "news": True}, cfg["master"])
    check("absent file: no config_error", not cfg.get("config_error"), cfg.get("config_error"))

    # the owner's real state: both masters OFF
    H.wj(wt, {"master": {"briefings": False, "news": False},
              "quiet_hours": {"start": "23:00", "end": "06:00"},
              "rules": [{"type": "system_metric", "label": "RAM",
                         "params": {"metric": "ram_pct", "op": ">", "value": 1}}]})
    cfg = g["_wt_load"]()
    check("good file: masters off as written",
          cfg["master"] == {"briefings": False, "news": False}, cfg["master"])
    good_rules = cfg["rules"]
    check("good file: the rule validated", len(good_rules) == 1, good_rules)

    # a hand-edit typo
    with open(wt, "w") as f:
        f.write('{"master": {"briefings": false, "news": false},}')
    cfg = g["_wt_load"]()
    check("corrupt file: masters read as OFF (fail closed)",
          cfg["master"] == {"briefings": False, "news": False}, cfg["master"])
    check("corrupt file: config_error is set", bool(cfg.get("config_error")), cfg.get("config_error"))
    check("corrupt file: last good quiet hours kept",
          cfg["quiet_hours"] == {"start": "23:00", "end": "06:00"}, cfg["quiet_hours"])

    # a fresh process with no last-good copy still fails closed
    g2, _h, _d = H.load(home=home)
    cfg2 = g2["_wt_load"]()
    check("corrupt at boot: masters OFF",
          cfg2["master"] == {"briefings": False, "news": False}, cfg2["master"])

    # the rule pass pushes nothing on a broken config
    seen = []
    g2["_evaluate"] = lambda rule: (seen.append(rule) or (True, "sig", {}))
    g2["_fire_rule"] = lambda *a, **k: seen.append("fired")
    cfg_r = dict(cfg2, rules=good_rules)      # a live rule, read off a broken file
    g2["_rule_pass"](cfg_r, g2["_wt_state_load"](), 1e9)
    check("corrupt file: rule pass evaluates/fires nothing", seen == [], seen)

    # GET exposes it
    out = g2["watchtower_get_handler"](H.Ctx())
    check("GET /api/watchtower carries config_error",
          bool(out.get("config_error")), out.get("config_error"))

    # a save moves the corrupt file aside, then writes fail-closed + the change
    before = open(wt).read()
    res = g2["watchtower_post_handler"](H.Ctx(body={
        "op": "set_quiet_hours", "start": "21:00", "end": "08:00"}))
    check("save on corrupt file: ok", isinstance(res, dict) and res.get("ok"), res)
    asides = [n for n in os.listdir(data) if n.startswith("watchtower.json.corrupt-")]
    check("corrupt original kept aside", len(asides) == 1, os.listdir(data))
    if asides:
        check("aside copy is byte-identical",
              open(os.path.join(data, asides[0])).read() == before)
    saved = H._server_defs({"read_json"})["read_json"](wt, None)
    check("new file parses", isinstance(saved, dict), saved)
    check("new file keeps masters OFF",
          isinstance(saved, dict) and saved.get("master") == {"briefings": False, "news": False},
          saved and saved.get("master"))
    check("new file has the owner's change",
          isinstance(saved, dict) and saved.get("quiet_hours") == {"start": "21:00", "end": "08:00"},
          saved and saved.get("quiet_hours"))
    check("config_error is never persisted",
          isinstance(saved, dict) and "config_error" not in saved, saved)
    cfg3 = g2["_wt_load"]()
    check("after the save: config_error clears", not cfg3.get("config_error"), cfg3.get("config_error"))

    # review: the write after the aside copy FAILS (disk full). The corrupt
    # original must still be there — no file at all reads as a first run,
    # i.e. every default and both masters ON.
    g4, h4, d4 = H.load()
    wt4 = os.path.join(d4, "watchtower.json")
    with open(wt4, "w") as f:
        f.write('{"master": {"briefings": false, "news": false},}')
    real_write = g4["_wt_write_json"]

    def disk_full(path, obj):
        if path == wt4:
            raise OSError(28, "No space left on device")
        return real_write(path, obj)
    g4["_wt_write_json"] = disk_full
    res = g4["watchtower_post_handler"](H.Ctx(body={"op": "set_master", "news": True}))
    check("failed save on corrupt file: refused",
          isinstance(res, tuple) and res[1] >= 500, res)
    check("failed save on corrupt file: the file is still there", os.path.isfile(wt4))
    g5, _h5, _d5 = H.load(home=h4)
    check("failed save on corrupt file: a fresh load still has masters OFF",
          g5["_wt_load"]()["master"] == {"briefings": False, "news": False},
          g5["_wt_load"]()["master"])

    # unreadable (a directory in its place): refuse to write over it
    g3, h3, d3 = H.load()
    wt3 = os.path.join(d3, "watchtower.json")
    os.makedirs(wt3)
    cfg = g3["_wt_load"]()
    check("unreadable file: masters OFF",
          cfg["master"] == {"briefings": False, "news": False}, cfg["master"])
    res = g3["watchtower_post_handler"](H.Ctx(body={"op": "set_master", "news": True}))
    status = res[1] if isinstance(res, tuple) else 200
    body = res[0] if isinstance(res, tuple) else res
    check("unreadable file: save refused with 503", status == 503, res)
    check("unreadable file: error names the file",
          "watchtower.json" in str(body.get("error", "")), body)
    check("unreadable file: left in place", os.path.isdir(wt3))
    H.finish()


if __name__ == "__main__":
    main()
