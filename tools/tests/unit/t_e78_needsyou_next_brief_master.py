#!/usr/bin/env python3
"""E78 (docs/audits/2026-11-09-codebase-audit.md): Needs-you's empty state
promised "next morning brief at 8:00 AM" while the Briefings master was off
or watchtower.json was corrupt (which forces both masters off) — no brief
would be sent.  _ny_next_brief checked each slot's `enabled` only.

AFTER: no next brief while `master.briefings` is not exactly true or the
config carries `config_error`, whether the config comes from watchtower's own
_wt_load or (watchtower not loaded) from the raw file.

Drives the REAL aux_needsyou.py (_ny_env) and, for the loaded case, the REAL
aux_watchtower.py's _wt_load (_wt_harness) — offline.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()


def cfg(master=None, **extra):
    c = {"brief": {"enabled": True, "hour": 8, "minute": 0},
         "midday": {"enabled": True, "hour": 12, "minute": 30},
         "evening": {"enabled": True, "hour": 18, "minute": 0}}
    if master is not None:
        c["master"] = master
    c.update(extra)
    return c


print("=== E78: via watchtower's _wt_load (stubbed shape) ===")
g, data = E.load()
for label, c, want in [
        ("masters on", cfg({"briefings": True, "news": True}), True),
        ("no master key (default on)", cfg(), True),
        ("briefings off", cfg({"briefings": False, "news": True}), False),
        ("config_error", cfg({"briefings": True}, config_error="corrupt"), False),
        ("briefings 'false' string", cfg({"briefings": "false"}), False),
]:
    g["_wt_load"] = (lambda c=c: c)
    nb = g["_ny_next_brief"](NOW)
    E.check("%s -> %s" % (label, "a next brief" if want else "none"),
            (nb is not None) == want, nb)

print("=== E78: raw watchtower.json when watchtower is not loaded ===")
g, data = E.load()
g.pop("_wt_load", None)
path = os.path.join(data, "watchtower.json")
for label, c, want in [
        ("masters on", cfg({"briefings": True}), True),
        ("briefings off", cfg({"briefings": False}), False),
        ("briefings 'false' string", cfg({"briefings": "false"}), False),
]:
    with open(path, "w") as f:
        json.dump(c, f)
    nb = g["_ny_next_brief"](NOW)
    E.check("raw %s -> %s" % (label, "a next brief" if want else "none"),
            (nb is not None) == want, nb)
with open(path, "w") as f:
    f.write("{not json")
E.check("raw corrupt file -> none", g["_ny_next_brief"](NOW) is None)

print("=== E78: with the REAL _wt_load ===")
import _wt_harness as H                                         # noqa: E402
wg, whome, wdata = H.load()
g, data = E.load()
g["_wt_load"] = wg["_wt_load"]
wpath = wg.get("WT_FILE") or os.path.join(wdata, "watchtower.json")
with open(wpath, "w") as f:
    json.dump(cfg({"briefings": False, "news": True}), f)
E.check("real loader, briefings off -> none", g["_ny_next_brief"](NOW) is None)
with open(wpath, "w") as f:
    json.dump(cfg({"briefings": True, "news": True}), f)
E.check("real loader, briefings on -> a next brief",
        g["_ny_next_brief"](NOW) is not None)
with open(wpath, "w") as f:
    f.write("{corrupt")
E.check("real loader, corrupt file -> none", g["_ny_next_brief"](NOW) is None,
        g["_ny_next_brief"](NOW))

sys.exit(E.finish())
