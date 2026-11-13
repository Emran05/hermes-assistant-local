#!/usr/bin/env python3
"""Regression test for E95 (docs/audits/2026-11-09-codebase-audit.md).

BEFORE: the advisor treated the background lane (com.hermes.mlx-bg, :8081) as
always running: "What you run now" added the lane bg_model() names (the 9B by
default, whether or not anything ever started it) to the memory total, and
every Recommended plan at 24-48 GB carried a lane — but install-services.sh
leaves that lane OFF and nothing in the dashboard starts it. A 36 GB M4 Max
running the 27B was told "Does not fit: needs ~30 GB" (alone it is tight),
and onboarding downloaded and pointed bg-model at a lane model that never ran.

AFTER: whether the lane can run is decided from FILES only — the owner's start
token (model-start-ok-bg) or a background-lane log written in the last 48 h
(it runs only when the owner starts it by hand). When it cannot run, it is
not counted in "what you run now" and not part of any plan; the result
carries `lane {running:false, optional}` and a note with its real cost (the
lane model that would fit next to the Recommended primary, and the total).
`_adv_rank` without `lane_on` keeps the old behaviour, so the goldens
(t_advisor_rank.py) are unchanged — they rank a Mac whose lane runs.

Runs the REAL aux_advisor.py + aux_advisor_hw.py through t_advisor_rank.py's
own loader (its head, exec'd up to the first check), with a scratch HOME.
"""
import io
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
RANK = os.path.join(_HERE, "t_advisor_rank.py")
with io.open(RANK, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index('print("\\n1. the golden')
NS = {"__name__": "t_e95_head", "__file__": RANK}
exec(compile(_src[:_cut], RANK, "exec"), NS)
check, FAILS, PASSES = NS["check"], NS["FAILS"], NS["PASSES"]
load, hw_of = NS["load"], NS["hw_of"]
M27, M9, M4, M2 = NS["M27"], NS["M9"], NS["M4"], NS["M2"]
S = NS["S"]

TMP = tempfile.mkdtemp(prefix="hermes-e95-")
DATA = os.path.join(TMP, ".hermes", "dashboard")
LOGS = os.path.join(TMP, ".hermes", "logs")
os.makedirs(DATA)
os.makedirs(LOGS)
BGLOG = os.path.join(LOGS, "mlx-bg.log")
TOKEN = os.path.join(DATA, "model-start-ok-bg")


def local_g():
    g = load({"HOME": TMP, "DATA": DATA, "_CX_LOG_BG": BGLOG,
              "active_model": lambda: M27, "bg_model": lambda: M9})
    return g


BAL = {"uses": ["chat", "agent"], "priority": "balanced", "power": None}

try:
    print("== the decision comes from files ==")
    g = local_g()
    lr = g.get("_advr_lane_runs")
    check("aux_advisor.py has a file-only lane check", callable(lr))
    check("no start token, no lane log: the lane cannot run", lr() is False)
    with open(BGLOG, "w") as f:
        f.write("old\n")
    old = time.time() - 9 * 86400
    os.utime(BGLOG, (old, old))
    check("a lane log last written 9 days ago: still not running", lr() is False)
    os.utime(BGLOG, None)
    check("a lane log written just now (the owner started it): the lane runs", lr() is True)
    os.utime(BGLOG, (old, old))
    open(TOKEN, "w").close()
    check("the owner's start token alone counts too", lr() is True)
    os.remove(TOKEN)

    print("== 'what you run now' no longer counts a lane nothing started ==")
    hw36 = hw_of("MBP M4 Max 32c, 36 GB", g)
    res, _hw, inputs = g["_adv_rank_local"](BAL, hw=hw36)
    cur = res.get("current") or {}
    check("inputs say the lane is off", inputs.get("lane_on") is False, inputs.get("lane_on"))
    check("36 GB M4 Max on the 27B: not 'Does not fit' (was: 27B + a 9B nothing runs = 30 GB)",
          cur.get("verdict") in ("fits", "tight") and not cur.get("background"),
          (cur.get("verdict"), cur.get("total_gb"), cur.get("why")))
    rec = res.get("recommended") or {}
    check("...and the Recommended plan carries no lane",
          rec and not rec.get("background"), (rec.get("primary"), rec.get("background")))
    check("...no plan offered carries one either",
          all(not (p or {}).get("background") for p in [rec] + list(res.get("alternatives") or [])))
    lane = res.get("lane") or {}
    check("the result says the lane is not running and what it would cost",
          lane.get("running") is False and isinstance(lane.get("optional"), dict)
          and lane["optional"].get("id") in (M9, M4, M2)
          and isinstance(lane["optional"].get("resident_gb"), (int, float)), lane)
    check("...and a note says so in words (optional, its memory, how to start it)",
          any("background lane" in n and "GB" in n and "by hand" in n for n in res.get("notes") or []),
          res.get("notes"))
    hw48 = hw_of("mini M4 Pro 20c, 48 GB", g)
    res48, _h, _i = g["_adv_rank_local"](BAL, hw=hw48)
    check("48 GB M4 Pro on the 27B: 'fits' (was: 'Loads, but uses 32 of 33 GB')",
          (res48.get("current") or {}).get("verdict") == "fits",
          (res48.get("current") or {}).get("why"))

    print("== a lane the owner did start is still counted and offered ==")
    os.utime(BGLOG, None)
    res2, _hw, inputs2 = g["_adv_rank_local"](BAL, hw=hw36)
    check("inputs say the lane runs", inputs2.get("lane_on") is True)
    check("'what you run now' counts the running 9B lane",
          ((res2.get("current") or {}).get("background") or {}).get("id") == M9, res2.get("current"))
    check("the Recommended plan may carry a lane again (9B + 4B on this Mac, as the golden)",
          ((res2.get("recommended") or {}).get("background") or {}).get("id") == M4,
          (res2.get("recommended") or {}).get("background"))
    check("...and no 'not running' block", (res2.get("lane") or {}).get("running") is not False)

    print("== the pure ranking keeps its contract (the goldens) ==")
    r_default = g["_adv_rank"](hw36, BAL, prefix=S, measured=False, drills={}, bg_wanted=True, env={})
    r_on = g["_adv_rank"](hw36, BAL, prefix=S, measured=False, drills={}, bg_wanted=True, env={},
                          lane_on=True)
    check("_adv_rank without lane_on == lane_on=True (goldens unchanged)",
          NS["json"].dumps(r_default, sort_keys=True) == NS["json"].dumps(r_on, sort_keys=True))
    r_off = g["_adv_rank"](hw36, BAL, prefix=S, measured=False, drills={}, bg_wanted=True, env={},
                           lane_on=False, current=(M27, M9))
    check("lane_on=False: no lane anywhere, the current plan drops the configured lane",
          not (r_off["recommended"] or {}).get("background")
          and not (r_off["current"] or {}).get("background"), r_off.get("current"))
    hw16 = hw_of("Air M2, 16 GB", g)
    r16 = g["_adv_rank"](hw16, BAL, prefix=S, measured=False, drills={}, bg_wanted=True, env={},
                         lane_on=False)
    check("below 24 GB nothing is offered as optional either (no lane there at all)",
          not (r16.get("lane") or {}).get("optional"), r16.get("lane"))
    r_nb = g["_adv_rank"](hw36, BAL, prefix=S, measured=False, drills={}, bg_wanted=False, env={},
                          lane_on=False)
    check("briefings and news both off: no lane note at all",
          not any("background lane" in n for n in r_nb.get("notes") or []), r_nb.get("notes"))
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
