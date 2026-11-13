#!/usr/bin/env python3
"""C59.3 — the watchtower switches take JSON booleans only.

BEFORE: set_master / set_brief / set_midday / set_evening / set_breaking /
toggle_rule / add_rule stored `bool(value)`, so {"news": "false"} — a string
from any client that stringifies form values — turned the News master (the
documented complete off-switch for breaking alerts, every watch rule and the
hourly feed fetch) ON. _wt_load normalised the stored file with bool() too,
so a hand-edited "false" read back as on.

AFTER: a posted switch that is not exactly true/false is a 400 and nothing is
stored; a stored switch that is not a real boolean reads as off (pushes
nothing); an absent one keeps its documented default.
Drives the real aux_watchtower.py (see _wt_harness.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
BAD = ["false", "true", 0, 1, None, "", [], {}]
RULE = {"type": "system_metric", "label": "RAM",
        "params": {"metric": "ram_pct", "op": ">", "value": 80}}


def status(res):
    return res[1] if isinstance(res, tuple) else 200


def body(res):
    return res[0] if isinstance(res, tuple) else res


def main():
    print("C59.3: watchtower switches are strict booleans")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    post = g["watchtower_post_handler"]

    def stored():
        return json.load(open(wt)) if os.path.exists(wt) else {}

    # ---- every posted switch: a non-boolean is refused, nothing stored -------
    cases = [("set_master", "briefings", lambda c: c["master"]["briefings"]),
             ("set_master", "news", lambda c: c["master"]["news"]),
             ("set_brief", "enabled", lambda c: c["brief"]["enabled"]),
             ("set_midday", "enabled", lambda c: c["midday"]["enabled"]),
             ("set_evening", "enabled", lambda c: c["evening"]["enabled"]),
             ("set_breaking", "enabled", lambda c: c["breaking"]["enabled"]),
             ("set_breaking", "override_quiet", lambda c: c["breaking"]["override_quiet"])]
    for op, key, read in cases:
        # start from an explicit OFF, saved through the op itself
        r0 = post(H.Ctx(body={"op": op, key: False}))
        check("%s %s=false (a real boolean) is saved" % (op, key),
              status(r0) == 200 and read(g["_wt_load"]()) is False, r0)
        before = open(wt).read()
        refused = []
        for v in BAD:
            r = post(H.Ctx(body={"op": op, key: v}))
            if status(r) != 400 or body(r).get("ok") is not False:
                refused.append((v, r))
        check("%s %s: every non-boolean (%d kinds) is a 400" % (op, key, len(BAD)),
              not refused, refused[:2])
        check("%s %s: ... and the file is untouched (still off)" % (op, key),
              open(wt).read() == before and read(g["_wt_load"]()) is False,
              read(g["_wt_load"]()))
        r1 = post(H.Ctx(body={"op": op, key: True}))
        check("%s %s=true is saved" % (op, key),
              status(r1) == 200 and read(g["_wt_load"]()) is True, r1)

    # the headline case from the audit, spelled out
    post(H.Ctx(body={"op": "set_master", "news": False}))
    r = post(H.Ctx(body={"op": "set_master", "news": "false"}))
    check('THE BUG: {"news": "false"} does not turn News on',
          g["_wt_load"]()["master"]["news"] is False and status(r) == 400,
          (r, g["_wt_load"]()["master"]))

    # a mixed body is refused as a whole: the valid half is not stored either
    post(H.Ctx(body={"op": "set_breaking", "enabled": False, "override_quiet": False}))
    r = post(H.Ctx(body={"op": "set_breaking", "enabled": True, "override_quiet": "no"}))
    bk = g["_wt_load"]()["breaking"]
    check("a body with one bad switch stores nothing",
          status(r) == 400 and bk["enabled"] is False and bk["override_quiet"] is False,
          (r, bk))
    # a non-switch field still works alongside
    r = post(H.Ctx(body={"op": "set_evening", "hour": 19}))
    check("a body with no switch at all is unaffected",
          status(r) == 200 and g["_wt_load"]()["evening"]["hour"] == 19, r)

    # ---- rules -----------------------------------------------------------
    r = post(H.Ctx(body={"op": "add_rule", "rule": dict(RULE, enabled="false")}))
    check('add_rule with enabled:"false" is a 400 and adds nothing',
          status(r) == 400 and g["_wt_load"]()["rules"] == [], (r, g["_wt_load"]()["rules"]))
    r = post(H.Ctx(body={"op": "add_rule", "rule": dict(RULE)}))
    check("add_rule with no enabled key adds an enabled rule",
          status(r) == 200 and body(r)["rule"]["enabled"] is True, r)
    rid = body(r)["rule"]["id"]
    r = post(H.Ctx(body={"op": "add_rule", "rule": dict(RULE, enabled=False)}))
    check("add_rule with enabled:false adds a disabled rule",
          status(r) == 200 and body(r)["rule"]["enabled"] is False, r)

    def rule_on():
        return next(x for x in g["_wt_load"]()["rules"] if x["id"] == rid)["enabled"]
    post(H.Ctx(body={"op": "toggle_rule", "id": rid, "enabled": False}))
    for v in ("true", 1, None):
        r = post(H.Ctx(body={"op": "toggle_rule", "id": rid, "enabled": v}))
        check("toggle_rule enabled=%r is a 400 and leaves the rule off" % (v,),
              status(r) == 400 and rule_on() is False, (r, rule_on()))
    r = post(H.Ctx(body={"op": "toggle_rule", "id": rid}))
    check("toggle_rule with no enabled is a 400 (it used to mean on)",
          status(r) == 400 and rule_on() is False, r)
    r = post(H.Ctx(body={"op": "toggle_rule", "id": rid, "enabled": True}))
    check("toggle_rule enabled=true turns it on", status(r) == 200 and rule_on() is True, r)
    r = post(H.Ctx(body={"op": "update_rule", "id": rid, "patch": {"enabled": "false"}}))
    check('update_rule patch enabled:"false" is a 400 and the rule stays on',
          status(r) == 400 and rule_on() is True, (r, rule_on()))
    r = post(H.Ctx(body={"op": "update_rule", "id": rid, "patch": {"enabled": False}}))
    check("update_rule patch enabled:false works", status(r) == 200 and rule_on() is False, r)

    # ---- stored values: a non-boolean reads as OFF, absent as the default ----
    H.wj(wt, {"master": {"briefings": "false", "news": 1},
              "brief": {"enabled": "yes"}, "midday": {"enabled": None},
              "evening": {"enabled": 0},
              "breaking": {"enabled": "true", "override_quiet": "false"},
              "rules": [dict(RULE, id="r-s", enabled="true")]})
    c = g["_wt_load"]()
    check("stored master 'false' / 1 read as off",
          c["master"] == {"briefings": False, "news": False}, c["master"])
    check("stored brief/midday/evening non-booleans read as off",
          (c["brief"]["enabled"], c["midday"]["enabled"], c["evening"]["enabled"])
          == (False, False, False),
          (c["brief"], c["midday"], c["evening"]))
    check("stored breaking non-booleans read as off (incl. the quiet override)",
          (c["breaking"]["enabled"], c["breaking"]["override_quiet"]) == (False, False),
          c["breaking"])
    check("a stored rule with a non-boolean enabled reads as off",
          [r["enabled"] for r in c["rules"]] == [False], c["rules"])
    check("... and the master gate agrees (no push)",
          not g["_master_on"](c, "news") and not g["_master_on"](c, "briefings"))
    H.wj(wt, {"rules": [dict(RULE, id="r-d")]})
    c = g["_wt_load"]()
    check("absent switches keep their documented defaults (on)",
          c["master"] == {"briefings": True, "news": True}
          and c["brief"]["enabled"] and c["midday"]["enabled"] and c["evening"]["enabled"]
          and c["breaking"]["enabled"] and c["breaking"]["override_quiet"]
          and [r["enabled"] for r in c["rules"]] == [True], c)
    H.wj(wt, {"master": {"briefings": False, "news": True}})
    c = g["_wt_load"]()
    check("real stored booleans are kept as they are",
          c["master"] == {"briefings": False, "news": True}, c["master"])
    H.finish()


if __name__ == "__main__":
    main()
