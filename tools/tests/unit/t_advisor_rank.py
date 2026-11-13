#!/usr/bin/env python3
"""Model advisor, cluster C2 — the ranking (docs/plans/model-advisor-2026-10.md §7, §10).

Runs the REAL dashboard/aux_advisor.py and the REAL dashboard/aux_advisor_hw.py,
exec'd in server.py's sorted order (aux_advisor.py FIRST) into one fresh
namespace that also holds the REAL server.py roster pieces the advisor
resolves by name (the seeds, _roster_uncensored, _model_fit, _mem_ceilings,
_catalog_dl_gb — ast-extracted) and the REAL onboarding catalog (the literal
out of aux_onboarding.py).  Every machine is SYNTHETIC (_adv_machine), so no
command runs: subprocess is a stub that refuses, and one check patches open()
to prove the ranking itself does no I/O when every input is given.

  1. the §7.6 golden: Recommended primary + background for 11 machines x 6
     answer profiles (Balanced, Faster, battery, code + best, docs,
     uncensored), and the design's own printed Balanced column
  2. the design's alternatives / won't-fit examples and the M5 variants
  3. properties over a sweep of chips x RAM sizes x profiles: Recommended
     never uncensored and never `no`; uncensored ids absent unless asked for
     AND acknowledged; a background lane only at >= 24 GB, only when the plan
     fits, and smaller than the primary; more RAM (same chip) never a worse
     Recommended verdict; relaxation exactly when a use case is unsatisfiable;
     every won't-fit row has a kind and a reason; JSON-serialisable;
     deterministic
  4. vision, drills (a local fail removes "verified", a pass >= 5/6 adds it),
     the opt-in alternative rule, an unsupported Mac, "what you run now"
  5. the "why" templates and the score parts

Regenerate the golden table after a DELIBERATE scoring/constant change (and
review the diff with the owner — D14):
    python3 tools/tests/unit/t_advisor_rank.py --print-golden
"""
import ast
import builtins
import copy
import io
import itertools
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
SERVER = os.path.join(DASH, "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


class RefusingSub:
    """Nothing on the ranking path may run a command."""
    TimeoutExpired = Exception

    def __init__(self):
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        raise AssertionError("the ranking ran a command: %r" % (argv,))

    def Popen(self, *a, **k):
        raise AssertionError("the ranking ran Popen")


SERVER_NAMES = ("_SEED_MODELS", "_SEED_ONLY_KEYS", "_mem_ceilings", "_model_fit", "_seed_entry",
                "_roster_uncensored", "_catalog_dl_gb", "DISK_HEADROOM_GB")


def _server_nodes():
    with io.open(SERVER, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=SERVER)
    out = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in SERVER_NAMES:
            out.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in SERVER_NAMES
                                               for t in n.targets):
            out.append(n)
    got = {getattr(n, "name", None) or n.targets[0].id for n in out}
    missing = set(SERVER_NAMES) - got
    assert not missing, "server.py lacks %s" % sorted(missing)
    return ast.Module(body=out, type_ignores=[])


def _literal(path, name):
    with io.open(path, encoding="utf-8") as f:
        for n in ast.parse(f.read()).body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                 for t in n.targets):
                return ast.literal_eval(n.value)
    raise KeyError(name)


def load(extra=None):
    g = {"__name__": "aux_advisor_rank_under_test", "os": os, "sys": sys, "re": re,
         "json": json, "HOME": "/nonexistent-home"}
    exec(compile(_server_nodes(), SERVER, "exec"), g)
    cat = _literal(os.path.join(DASH, "aux_onboarding.py"), "_ONB_CATALOG")
    g["_ONB_CATALOG"] = cat
    g["_ONB_BY_ID"] = {m["id"]: m for m in cat}
    g["subprocess"] = RefusingSub()
    if extra:
        g.update(extra)
    for fn in ("aux_advisor.py", "aux_advisor_hw.py"):          # server.py's sorted order
        p = os.path.join(DASH, fn)
        with io.open(p, encoding="utf-8") as f:
            exec(compile(f.read(), p, "exec"), g)
    return g


G = load()
M27 = "mlx-community/Qwen3.8-27B-4bit"
M27U = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
M35 = "mlx-community/Qwen3.6-35B-A3B-4bit"
M35H = "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit"
MPOCKET = "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"
MHUI = "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit"
MV9 = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
M9 = "mlx-community/Qwen3.5-9B-4bit"
M4 = "mlx-community/Qwen3.5-4B-4bit"
M2 = "mlx-community/Qwen3.5-2B-4bit"
SHORT = {M27: "27B", M35: "35B", M9: "9B", M4: "4B", M2: "2B", M27U: "27B-unc", M35H: "35B-her",
         MPOCKET: "27B-pocket", MHUI: "27B-huihui", MV9: "9B-unc", None: "-"}
UNCENSORED = {M27U, M35H, MPOCKET, MHUI, MV9}
S = 18500                                           # Balanced, the new-install profile
METAL_HERE = 55662788608

MACHINES = [
    ("Air M1, 8 GB", "Apple M1", 8, 8, "MacBook Air", None),
    ("Air M2, 16 GB", "Apple M2", 10, 16, "MacBook Air", None),
    ("Air M4, 24 GB", "Apple M4", 10, 24, "MacBook Air", None),
    ("MBP M1 Max 32c, 32 GB", "Apple M1 Max", 32, 32, "MacBook Pro", None),
    ("MBP M3 Pro 18c, 36 GB", "Apple M3 Pro", 18, 36, "MacBook Pro", None),
    ("MBP M4 Max 32c, 36 GB", "Apple M4 Max", 32, 36, "MacBook Pro", None),
    ("mini M4 Pro 20c, 48 GB", "Apple M4 Pro", 20, 48, "Mac mini", None),
    ("MBP M5 Max 40c, 64 GB", "Apple M5 Max", 40, 64, "MacBook Pro", METAL_HERE),
    ("Studio M2 Max 38c, 96 GB", "Apple M2 Max", 38, 96, "Mac Studio", None),
    ("MBP M4 Max 40c, 128 GB", "Apple M4 Max", 40, 128, "MacBook Pro", None),
    ("Studio M2 Ultra 76c, 192 GB", "Apple M2 Ultra", 76, 192, "Mac Studio", None),
]
PROFILES = {
    "balanced": {"uses": ["chat", "agent"], "priority": "balanced", "power": None},
    "faster": {"uses": ["chat", "agent"], "priority": "faster", "power": None},
    "battery": {"uses": ["chat", "agent"], "priority": "balanced", "power": "battery"},
    "code_best": {"uses": ["chat", "agent", "code"], "priority": "best", "power": None},
    "docs": {"uses": ["chat", "agent", "docs"], "priority": "balanced", "power": None},
    "uncensored": {"uses": ["chat", "agent", "uncensored"], "priority": "balanced", "power": None,
                   "uncensored_ack": True},
}

# THE GOLDEN (§7.6 / §10): "primary+background" of the Recommended slot, per
# machine and profile, with S = 18,500 (the Balanced profile's prefix), no
# drills beyond the catalog's reference ones, briefings/news on, no env
# override.  Changing it is a deliberate act (D14): --print-golden.
GOLDEN = {
    "Air M1, 8 GB": {"balanced": "2B+-", "faster": "2B+-", "battery": "2B+-", "code_best": "2B+-",
                     "docs": "2B+-", "uncensored": "2B+-"},
    "Air M2, 16 GB": {"balanced": "4B+-", "faster": "4B+-", "battery": "4B+-", "code_best": "4B+-",
                      "docs": "4B+-", "uncensored": "4B+-"},
    "Air M4, 24 GB": {"balanced": "9B+2B", "faster": "9B+2B", "battery": "9B+2B",
                      "code_best": "9B+2B", "docs": "9B+2B", "uncensored": "9B+2B"},
    "MBP M1 Max 32c, 32 GB": {"balanced": "9B+4B", "faster": "9B+4B", "battery": "9B+4B",
                              "code_best": "9B+4B", "docs": "9B+4B", "uncensored": "9B+4B"},
    "MBP M3 Pro 18c, 36 GB": {"balanced": "9B+4B", "faster": "9B+4B", "battery": "9B+4B",
                              "code_best": "9B+4B", "docs": "9B+4B", "uncensored": "9B+4B"},
    "MBP M4 Max 32c, 36 GB": {"balanced": "9B+4B", "faster": "9B+4B", "battery": "9B+4B",
                              "code_best": "9B+4B", "docs": "4B+2B", "uncensored": "9B+4B"},
    "mini M4 Pro 20c, 48 GB": {"balanced": "9B+4B", "faster": "9B+4B", "battery": "9B+4B",
                               "code_best": "9B+4B", "docs": "9B+4B", "uncensored": "9B+4B"},
    "MBP M5 Max 40c, 64 GB": {"balanced": "27B+9B", "faster": "27B+9B", "battery": "9B+4B",
                              "code_best": "27B+9B", "docs": "27B+4B", "uncensored": "27B+9B"},
    "Studio M2 Max 38c, 96 GB": {"balanced": "27B+9B", "faster": "9B+4B", "battery": "27B+9B",
                                 "code_best": "27B+9B", "docs": "27B+9B", "uncensored": "27B+9B"},
    "MBP M4 Max 40c, 128 GB": {"balanced": "27B+9B", "faster": "27B+9B", "battery": "9B+4B",
                               "code_best": "27B+9B", "docs": "27B+4B", "uncensored": "27B+9B"},
    "Studio M2 Ultra 76c, 192 GB": {"balanced": "27B+9B", "faster": "27B+9B", "battery": "27B+9B",
                                    "code_best": "27B+9B", "docs": "27B+4B", "uncensored": "27B+9B"},
}

# docs/plans/model-advisor-2026-10.md §7.6, AS PRINTED: the Balanced Recommended column.
DESIGN_BALANCED = {
    "Air M1, 8 GB": "2B+-", "Air M2, 16 GB": "4B+-", "Air M4, 24 GB": "9B+2B",
    "MBP M1 Max 32c, 32 GB": "9B+4B", "MBP M3 Pro 18c, 36 GB": "9B+4B",
    "MBP M4 Max 32c, 36 GB": "9B+4B", "mini M4 Pro 20c, 48 GB": "9B+4B",
    "MBP M5 Max 40c, 64 GB": "27B+9B", "Studio M2 Max 38c, 96 GB": "27B+9B",
    "MBP M4 Max 40c, 128 GB": "27B+9B", "Studio M2 Ultra 76c, 192 GB": "27B+9B",
}


def hw_of(label, g=None):
    g = g or G
    for lab, brand, cores, ram, name, metal in MACHINES:
        if lab == label:
            return g["_adv_machine"](brand, ram, gpu_cores=cores, machine_name=name,
                                     metal_bytes=metal)
    raise KeyError(label)


def rank(hw, answers, g=None, **kw):
    g = g or G
    opts = dict(prefix=S, measured=False, drills={}, bg_wanted=True, env={})
    opts.update(kw)
    return g["_adv_rank"](hw, answers, **opts)


def pair(plan):
    if not plan:
        return "NONE"
    return "%s+%s" % (SHORT.get(plan["primary"]["id"], plan["primary"]["id"]),
                      SHORT.get((plan.get("background") or {}).get("id")))


def all_plans(r):
    return [p for p in ([r.get("recommended")] + list(r.get("alternatives") or [])) if p]


# ==========================================================================
print("\n1. the golden: Recommended for 11 machines x 6 answer profiles")
RESULTS = {}
for lab, *_rest in MACHINES:
    hw = hw_of(lab)
    for pk, ans in PROFILES.items():
        RESULTS[(lab, pk)] = rank(hw, ans)

if "--print-golden" in sys.argv:
    print("GOLDEN = {")
    for lab, *_rest in MACHINES:
        print("    %r: {%s}," % (lab, ", ".join("%r: %r" % (pk, pair(RESULTS[(lab, pk)]["recommended"]))
                                              for pk in PROFILES)))
    print("}")
    sys.exit(0)

bad = [(lab, pk, pair(RESULTS[(lab, pk)]["recommended"]), GOLDEN[lab][pk])
       for lab, *_r in MACHINES for pk in PROFILES
       if pair(RESULTS[(lab, pk)]["recommended"]) != GOLDEN[lab][pk]]
check("all 66 (machine, profile) Recommended pairs match the golden", not bad, bad)
check("the golden covers exactly 11 machines x 6 profiles",
      len(GOLDEN) == 11 and all(set(v) == set(PROFILES) for v in GOLDEN.values()))
bad = [(lab, pair(RESULTS[(lab, "balanced")]["recommended"]), want)
       for lab, want in DESIGN_BALANCED.items()
       if pair(RESULTS[(lab, "balanced")]["recommended"]) != want]
check("the Balanced column reproduces the design's printed §7.6 Recommended column (11/11)",
      not bad, bad)
check("the D1 effect: 36-48 GB Macs without an M5 get 9B + 4B, not the 27B _onb_tier picks",
      all(pair(RESULTS[(lab, "balanced")]["recommended"]) == "9B+4B"
          for lab in ("MBP M3 Pro 18c, 36 GB", "MBP M4 Max 32c, 36 GB", "mini M4 Pro 20c, 48 GB")))

# ==========================================================================
print("\n2. the design's alternatives, won't-fit rows and the M5 variants")
r = RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]
check("M5 Max 64 GB: alternatives 35B-A3B + 9B (Faster, unverified) then 9B + 4B",
      [pair(a) for a in r["alternatives"]] == ["35B+9B", "9B+4B"]
      and r["alternatives"][0]["label"] == "Faster" and not r["alternatives"][0]["verified"],
      [(pair(a), a["label"]) for a in r["alternatives"]])
rec = r["recommended"]
check("M5 Max: today's roster, fits, verified, ~32 tok/s, ~25 s, 35 of 48 GB",
      rec["verdict"] == "fits" and rec["verified"] and 31 <= rec["summary"]["decode_turn_tps"] <= 34
      and 23 <= rec["summary"]["prefill_s"] <= 27 and rec["limit_gb"] == 48.0
      and 33 <= rec["total_gb"] <= 37, rec["summary"])
check("M5 Max: the why reads as the design's example",
      rec["why"].startswith("Fits with room to spare (35 of 48 GB): ~32 tok/s in a typical "
                            "25K-token turn, first reply after wake ~25 s; the 9B lane keeps "
                            "briefings off the assistant."), rec["why"])
r = RESULTS[("MBP M3 Pro 18c, 36 GB", "balanced")]
check("M3 Pro 36 GB: the 27B is ruled out as too slow (~8 tok/s, ~4 min)",
      any(w["id"] == M27 and w["kind"] == "slow" and "~4 min" in w["reason"] for w in r["wont"]),
      r["wont"])
check("M3 Pro: alternatives 35B-A3B solo (tight) and 4B + 2B (the design's list)",
      [pair(a) for a in r["alternatives"]] == ["35B+-", "4B+2B"]
      and r["alternatives"][0]["verdict"] == "tight", [pair(a) for a in r["alternatives"]])
check("M3 Pro: the why adds the bigger-brain contrast clause",
      "The Qwen3.8-27B would load but needs ~4 min to re-read its setup after each wake."
      in r["recommended"]["why"], r["recommended"]["why"])
r = RESULTS[("Air M1, 8 GB", "balanced")]
check("Air M1 8 GB: 2B chat-first (agent relaxed), unverified, the 4B/9B/27B/35B won't fit",
      [x["use"] for x in r["relaxed"]] == ["agent"] and not r["recommended"]["verified"]
      and {w["id"] for w in r["wont"] if w["kind"] == "memory"} == {M27, M35, M9, M4},
      (r["relaxed"], r["wont"]))
check("...and the why says so (chat-first, not yet verified)",
      "chat-first" in r["recommended"]["why"] and "Not yet verified" in r["recommended"]["why"],
      r["recommended"]["why"])
r = RESULTS[("Air M2, 16 GB", "balanced")]
check("Air M2 16 GB: 4B (unverified), the 9B solo is the alternative, the 2B too small",
      pair(r["recommended"]) == "4B+-" and [pair(a) for a in r["alternatives"]] == ["9B+-"]
      and any(w["id"] == M2 and w["kind"] == "small" for w in r["wont"])
      and {w["id"] for w in r["wont"] if w["kind"] == "memory"} == {M27, M35}, r["wont"])
check("Air M2: the 9B is NOT recommended (verified, but below 8 tok/s in a 25K turn)",
      r["alternatives"][0]["verified"] and not r["alternatives"][0]["usable"]
      and r["alternatives"][0]["summary"]["decode_turn_tps"] < 8, r["alternatives"][0]["summary"])
r = RESULTS[("Air M4, 24 GB", "balanced")]
check("Air M4 24 GB: 9B + 2B with 4B + 2B the alternative; the slow-prefill note is carried",
      [pair(a) for a in r["alternatives"]] == ["4B+2B"]
      and any(n.startswith("After the model sleeps") for n in r["recommended"]["notes"]),
      r["recommended"]["notes"])
r = RESULTS[("mini M4 Pro 20c, 48 GB", "balanced")]
check("M4 Pro 48 GB: the 27B sits just past the 180 s cut at S = 18,500 (the design's note)",
      any(w["id"] == M27 and w["kind"] == "slow" for w in r["wont"]), r["wont"])
r18 = rank(hw_of("mini M4 Pro 20c, 48 GB"), PROFILES["balanced"], prefix=18000)
check("...and at S = 18,000 it stays in as a candidate (176 s): still 9B + 4B recommended",
      not any(w["id"] == M27 for w in r18["wont"]) and pair(r18["recommended"]) == "9B+4B",
      r18["wont"])
r = RESULTS[("MBP M5 Max 40c, 64 GB", "docs")]
check("M5 + long documents: 27B + 4B with the 'raise the context window' note",
      pair(r["recommended"]) == "27B+4B" and any("128K-token context window" in n for n in r["notes"])
      and r["recommended"]["summary"]["turn_ctx"] == 131072, r["notes"])
r = RESULTS[("MBP M5 Max 40c, 64 GB", "code_best")]
check("M5 + coding, best answers: 27B + 9B at a 40K-token turn",
      pair(r["recommended"]) == "27B+9B" and r["recommended"]["summary"]["turn_ctx"] == 40000)
r = RESULTS[("MBP M5 Max 40c, 64 GB", "uncensored")]
unc_alts = [a for a in r["alternatives"] if a["uncensored"]]
check("M5 + unfiltered research: Recommended unchanged (27B + 9B), plus ONE uncensored "
      "alternative: the Heretic MoE + 9B, ~58 tok/s, unverified",
      pair(r["recommended"]) == "27B+9B" and len(unc_alts) == 1 and pair(unc_alts[0]) == "35B-her+9B"
      and 52 <= unc_alts[0]["summary"]["decode_turn_tps"] <= 64 and not unc_alts[0]["verified"]
      and unc_alts[0]["label"] == "Uncensored" and "uncensored" in unc_alts[0]["badges"],
      [(pair(a), a["label"]) for a in r["alternatives"]])
check("...its why opens with the uncensored trade-off line",
      unc_alts and unc_alts[0]["why"].startswith("Uncensored: no refusals"), unc_alts[:1])
r = RESULTS[("MBP M5 Max 40c, 64 GB", "battery")]
check("M5 often on battery: 9B + 4B (the design's variant)", pair(r["recommended"]) == "9B+4B")
check("...and the battery weights apply: w_battery 0.25, the others x0.75, B < 0",
      r["recommended"]["score_parts"]["w_battery"] == 0.25
      and abs(r["recommended"]["score_parts"]["w_quality"] - 0.4125) < 1e-9
      and r["recommended"]["score_parts"]["B"] < 0, r["recommended"]["score_parts"])
r = RESULTS[("MBP M5 Max 40c, 64 GB", "faster")]
alt_pairs = [pair(a) for a in r["alternatives"]]
check("M5 + faster (a documented borderline, D14): 27B + 9B by a hair over 9B + 4B, which is "
      "the next alternative",
      pair(r["recommended"]) == "27B+9B" and "9B+4B" in alt_pairs
      and abs(r["recommended"]["score"] - [a for a in r["alternatives"] if pair(a) == "9B+4B"][0]["score"]) < 0.05,
      (r["recommended"]["score"], [(pair(a), a["score"]) for a in r["alternatives"]]))

# ==========================================================================
print("\n3. properties over a sweep (chips x RAM x profiles)")
SWEEP_CHIPS = [("Apple M1", 8, "MacBook Air"), ("Apple M2 Pro", 19, "MacBook Pro"),
               ("Apple M3 Max", 40, "MacBook Pro"), ("Apple M4 Pro", 20, "Mac mini"),
               ("Apple M5", 10, "MacBook Air"), ("Apple M5 Max", 40, "MacBook Pro"),
               ("Apple M2 Ultra", 76, "Mac Studio")]
SWEEP_RAM = [8, 16, 18, 24, 32, 36, 48, 64, 96, 128, 192]
PROFILES_SWEEP = dict(PROFILES)
PROFILES_SWEEP["vision"] = {"uses": ["chat", "vision"], "priority": "balanced", "power": None}
PROFILES_SWEEP["unc_no_ack"] = {"uses": ["chat", "agent", "uncensored"], "priority": "best",
                                "power": None, "uncensored_ack": False}
PROFILES_SWEEP["everything"] = {"uses": ["chat", "agent", "code", "docs", "vision", "uncensored"],
                                "priority": "faster", "power": "battery", "uncensored_ack": True}
ORDER = {"fits": 2, "tight": 1, None: 0}
v_unc, v_no, v_absent, v_lane, v_mono, v_wont, v_json, v_det, n = [], [], [], [], [], [], [], [], 0
for brand, cores, name in SWEEP_CHIPS:
    prev = {}
    for ram in SWEEP_RAM:
        hw = G["_adv_machine"](brand, ram, gpu_cores=cores, machine_name=name)
        for pk, ans in PROFILES_SWEEP.items():
            n += 1
            r = rank(hw, ans)
            rec = r["recommended"]
            if rec and (rec["uncensored"] or rec["primary"]["id"] in UNCENSORED
                        or ((rec.get("background") or {}).get("id") in UNCENSORED)):
                v_unc.append((brand, ram, pk))
            if rec and rec["verdict"] not in ("fits", "tight"):
                v_no.append((brand, ram, pk, rec["verdict"]))
            acked = "uncensored" in ans["uses"] and ans.get("uncensored_ack") is True
            seen = {p["primary"]["id"] for p in all_plans(r)} | {w["id"] for w in r["wont"]}
            if not acked and seen & UNCENSORED:
                v_absent.append((brand, ram, pk, sorted(seen & UNCENSORED)))
            for p in all_plans(r):
                b = p.get("background")
                if b and (ram < 24 or p["verdict"] != "fits"
                          or G["_ADV_FACTS"][b["id"]]["weights_bytes"]
                          >= G["_ADV_FACTS"][p["primary"]["id"]]["weights_bytes"]):
                    v_lane.append((brand, ram, pk, pair(p), p["verdict"]))
            for w in r["wont"]:
                if not w.get("kind") or not (w.get("reason") or "").strip() \
                        or w["kind"] not in ("chip", "memory", "slow", "small", "vision", "unknown"):
                    v_wont.append((brand, ram, pk, w))
            cur = ORDER.get(rec["verdict"] if rec else None, 0)
            if pk in prev and cur < prev[pk]:
                v_mono.append((brand, ram, pk, prev[pk], cur))
            prev[pk] = cur
            try:
                s1 = json.dumps(r, sort_keys=True)
            except (TypeError, ValueError) as e:
                v_json.append((brand, ram, pk, str(e)))
                s1 = None
            if s1 is not None and pk in ("balanced", "everything") and ram in (16, 64):
                if json.dumps(rank(hw, ans), sort_keys=True) != s1:
                    v_det.append((brand, ram, pk))
check("%d rankings: Recommended is NEVER uncensored (primary or lane)" % n, not v_unc, v_unc[:5])
check("...and never a plan that does not fit", not v_no, v_no[:5])
check("...uncensored ids are absent everywhere (plans and won't-fit) unless asked for AND acknowledged",
      not v_absent, v_absent[:5])
check("...a background lane only at >= 24 GB, only on a plan that FITS, and smaller than the primary",
      not v_lane, v_lane[:5])
check("...more RAM on the same chip never yields a worse Recommended verdict", not v_mono, v_mono[:5])
check("...every won't-fit row has a known kind and a non-empty reason", not v_wont, v_wont[:3])
check("...every result is JSON-serialisable", not v_json, v_json[:3])
check("...and deterministic (the same inputs give byte-identical JSON)", not v_det, v_det[:3])

# relaxation exactly when a use case is unsatisfiable
v_relax = []
for brand, cores, name in SWEEP_CHIPS:
    for ram in SWEEP_RAM:
        hw = G["_adv_machine"](brand, ram, gpu_cores=cores, machine_name=name)
        for pk in ("balanced", "docs", "code_best", "everything"):
            ans = PROFILES_SWEEP[pk]
            r = rank(hw, ans)
            made = G["_advr_make_ctx"](hw, ans, S, False, {}, True, None, {})
            ctx, uses, _n = made
            relaxed = [x["use"] for x in r["relaxed"]]
            cur = list(uses)
            for u in G["_ADVR_RELAX_ORDER"]:
                plans, _w = G["_advr_plans"](ctx, cur)
                ok = G["_advr_pick"](plans) is not None
                if u in relaxed:
                    if ok:                  # relaxed although satisfiable at that step
                        v_relax.append((brand, ram, pk, "relaxed-but-ok", u))
                    cur = [x for x in cur if x != u]
                elif u in cur and not ok:
                    v_relax.append((brand, ram, pk, "unsatisfiable-not-relaxed", u))
                    break
                elif ok:
                    break
            if r["recommended"] and r["recommended"]["usable"] is False and \
                    not any("closest fit" in x for x in r["notes"]):
                v_relax.append((brand, ram, pk, "unusable without the closest-fit note"))
            if relaxed and not all(any(G["_ADVR_RELAX_TEXT"][u] == t for t in r["notes"])
                                   for u in relaxed):
                v_relax.append((brand, ram, pk, "relaxation not stated in notes"))
check("relaxation notes appear EXACTLY when a use case is unsatisfiable (docs -> code -> agent)",
      not v_relax, v_relax[:5])

# ==========================================================================
print("\n4. vision, drills, the opt-in alternative rule, unsupported, current")
hw64 = hw_of("MBP M5 Max 40c, 64 GB")
r = rank(hw64, {"uses": ["chat", "vision", "uncensored"], "uncensored_ack": True})
check("vision required: the text-only abliterated 27B is ruled out with 'No image support.'",
      any(w["id"] == MHUI and w["kind"] == "vision" and w["reason"] == "No image support."
          for w in r["wont"]), r["wont"])
r = rank(hw64, {"uses": ["chat", "uncensored"], "uncensored_ack": True})
check("...and without vision it is not excluded for vision",
      not any(w["id"] == MHUI and w["kind"] == "vision" for w in r["wont"]))
check("vision: every other catalog id has vision, so nothing else is filtered",
      not any(w["kind"] == "vision" and w["id"] != MHUI
              for w in rank(hw64, {"uses": ["chat", "vision"]})["wont"]))

r = rank(hw64, PROFILES["balanced"], drills={M27: {"score": 3, "of": 6, "pass": False}})
check("a LOCAL drill fail removes the 27B's verification: 9B + 4B is recommended instead",
      pair(r["recommended"]) == "9B+4B" and r["recommended"]["verified"], pair(r["recommended"]))
alt27 = [a for a in r["alternatives"] if a["primary"]["id"] == M27]
check("...the 27B plan is still shown, as unverified, with the failure in its detail",
      alt27 and not alt27[0]["verified"] and alt27[0]["primary"]["verified_source"] == "failed-here"
      and "failed" in alt27[0]["primary"]["verified_detail"], alt27[:1])
r = rank(hw_of("Air M2, 16 GB"), PROFILES["balanced"], drills={M4: {"score": 5, "of": 6, "pass": True}})
check("a local drill pass at 5/6 verifies the 4B: no 'not yet verified' clause, verified here",
      r["recommended"]["verified"] and r["recommended"]["primary"]["verified_source"] == "here"
      and "Not yet verified" not in r["recommended"]["why"], r["recommended"]["why"])
r = rank(hw_of("Air M2, 16 GB"), PROFILES["balanced"], drills={M4: {"score": 6, "of": 6}})
check("...a record with a score >= 5 and no `pass` key verifies too",
      r["recommended"]["verified"] is True)
r = rank(hw_of("Air M2, 16 GB"), PROFILES["balanced"], drills={M4: {"score": 4, "of": 6}})
check("...a score of 4/6 does not", r["recommended"]["verified"] is False)

check("the stock 35B-A3B (an opt-in alternative) is NOT recommended while undrilled, even where "
      "it scores highest (M5: it is the top alternative)",
      pair(RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]["alternatives"][0]) == "35B+9B"
      and RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]["alternatives"][0]["score"]
      > RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]["recommended"]["score"])
r = rank(hw64, PROFILES["balanced"], drills={M35: {"score": 6, "of": 6, "pass": True}})
check("...once a local drill passes it (D2/D3), it takes the slot on merit: 35B-A3B + 9B",
      pair(r["recommended"]) == "35B+9B" and r["recommended"]["verified"], pair(r["recommended"]))
r = rank(hw64, PROFILES["uncensored"], drills={M35H: {"score": 6, "of": 6, "pass": True},
                                               M27U: {"score": 6, "of": 6, "pass": True}})
check("an uncensored build NEVER takes the slot, not even verified with a higher score",
      not r["recommended"]["uncensored"] and pair(r["recommended"]) != "35B-her+9B",
      pair(r["recommended"]))
r = rank(hw64, {"uses": ["chat", "agent", "uncensored"], "uncensored_ack": False})
check("uncensored asked for WITHOUT the acknowledgement: no uncensored row anywhere, and a note",
      not ({p["primary"]["id"] for p in all_plans(r)} | {w["id"] for w in r["wont"]}) & UNCENSORED
      and any("acknowledgement" in x for x in r["notes"]) and r["answers"]["uncensored"] is False,
      r["notes"])
import inspect  # noqa: E402
check("...and nothing but answers.uncensored_ack can enable them: _adv_rank takes no other "
      "acknowledgement argument (the routes pass the STORED value)",
      not ({"unc", "ack", "uncensored_ack"} & set(inspect.signature(G["_adv_rank"]).parameters)),
      list(inspect.signature(G["_adv_rank"]).parameters))
r = rank(hw64, {"uses": ["chat", "agent", "uncensored"], "uncensored_ack": "true"})
check("...and the STRING 'true' is not an acknowledgement", not any(a["uncensored"] for a in r["alternatives"])
      and r["answers"]["uncensored"] is False)

intel = G["_adv_machine"]("Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz", 32, machine_name="MacBook Pro")
r = rank(intel, PROFILES["balanced"], current=(M27, M9))
check("an Intel Mac: nothing recommended, every stock model won't run with the chip reason",
      r["recommended"] is None and r["alternatives"] == []
      and r["wont"] and all(w["kind"] == "chip" and "Apple silicon" in w["reason"] for w in r["wont"])
      and not {w["id"] for w in r["wont"]} & UNCENSORED, r["wont"][:2])
check("...and 'what you run now' is still present", r["current"] is not None
      and r["current"]["primary"]["id"] == M27)

r = rank(hw64, PROFILES["balanced"], current=(M27, M9))
check("'what you run now' is always present when an active model exists: 27B + 9B, fits",
      r["current"]["kind"] == "current" and pair(r["current"]) == "27B+9B"
      and r["current"]["verdict"] == "fits" and r["current"]["label"] == "What you run now")
r = rank(hw_of("Air M2, 16 GB"), PROFILES["balanced"], current=(M27, M9))
check("...shown with its own verdict even when it cannot fit (the 27B on 16 GB: no)",
      r["current"]["verdict"] == "no" and "Does not fit" in r["current"]["why"], r["current"]["why"])
r = rank(hw64, PROFILES["balanced"], current=(M27U, None))
check("...and when the active model is uncensored (never hidden, badged)",
      r["current"]["uncensored"] is True and "uncensored" in r["current"]["badges"]
      and not r["recommended"]["uncensored"])
r = rank(hw64, PROFILES["balanced"], current=("someone/hand-added-model", None))
check("...and for an id the advisor has no facts for (no estimate, still shown)",
      r["current"]["primary"]["id"] == "someone/hand-added-model" and r["current"]["verdict"] is None)
r = rank(hw64, PROFILES["balanced"])
check("no active model given: current is None", r["current"] is None)

# ==========================================================================
print("\n5. the why templates and the score parts")
r = rank(hw_of("MBP M1 Max 32c, 32 GB"), PROFILES["balanced"])
tight = [a for a in r["alternatives"] if a["verdict"] == "tight"]
check("a tight plan's why opens 'Loads, but uses N of M GB: expect swapping under heavy use.'",
      tight and tight[0]["why"].startswith("Loads, but uses ") and "expect swapping" in tight[0]["why"],
      [a["why"] for a in r["alternatives"]])
r = rank(hw_of("Air M2, 16 GB"), PROFILES["balanced"])
check("an unverified recommended plan carries the drill clause (Settings > Agent & Models > Evals)",
      "Not yet verified for tools: run the drill" in r["recommended"]["why"], r["recommended"]["why"])
check("a solo verified-or-not plan opens 'Fits (N of M GB)' (room to spare needs verified + lane)",
      r["recommended"]["why"].startswith("Fits (5.4 of 10 GB)") or
      r["recommended"]["why"].startswith("Fits ("), r["recommended"]["why"])
v_long = []
for (lab, pk), rr in RESULTS.items():
    for p in all_plans(rr):
        k = len(re.findall(r"[.!?](?:\s|$)", p["why"]))
        if not p["why"] or k > 3 or "  " in p["why"]:
            v_long.append((lab, pk, p["why"]))
check("every why is one to three sentences, deterministic text", not v_long, v_long[:3])
r = RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]
sp = r["recommended"]["score_parts"]
check("score parts: plugged in -> the prefill penalty is halved (25 s -> 1 -> 0.5), no battery term",
      sp["pen"] == 0.5 and sp["B"] == 0 and sp["w_battery"] == 0, sp)
check("score parts: a verified 9B lane earns the full 0.8 bonus, an unverified 4B half of 0.8 x 5/7",
      r["recommended"]["score_parts"]["bg"] == 0.8
      and abs(r["alternatives"][1]["score_parts"]["bg"] - round(0.8 * 5 / 7 * 0.5, 3)) < 1e-6,
      (r["recommended"]["score_parts"], r["alternatives"][1]["score_parts"]))
r = rank(hw64, PROFILES["balanced"], bg_wanted=False)
check("briefings and news both off: no lane bonus, a solo plan wins the tie (fewer models)",
      r["recommended"]["background"] is None and r["recommended"]["score_parts"]["bg"] == 0,
      pair(r["recommended"]))
check("...and the plan's notes would have said the lane is skippable",
      any("skip" in n for n in RESULTS[("MBP M5 Max 40c, 64 GB", "balanced")]["recommended"]["notes"]))
r = RESULTS[("MBP M4 Max 40c, 128 GB", "balanced")]
check("a 128 GB Mac: the advisor states the flat 50 GB ceiling (D8)",
      any("50 GB" in n for n in r["notes"]), r["notes"])
hw_dt = G["_adv_machine"]("Apple M4 Pro", 48, gpu_cores=20, machine_name="Mac mini")
r = rank(hw_dt, {"uses": ["chat", "agent"], "power": "battery"})
check("a desktop is always plugged in (Q3 is hidden there): power reads ac", r["answers"]["power"] == "ac")
hw_bat = G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro",
                           metal_bytes=METAL_HERE, on_battery=True)
r = rank(hw_bat, {"uses": ["chat", "agent"], "power": None})
check("a laptop with power null follows what it is on now (battery)", r["answers"]["power"] == "battery")

# ==========================================================================
print("\n6. purity and module hygiene")
G2 = load()
sub = G2["subprocess"]
real_open = builtins.open
opened = []


def no_open(*a, **k):
    opened.append(a[:1])
    raise AssertionError("open() on the pure ranking path: %r" % (a[:1],))


builtins.open = no_open
try:
    err = None
    try:
        rr = rank(hw_of("MBP M5 Max 40c, 64 GB", G2), PROFILES_SWEEP["everything"], g=G2,
                  current=(M27, M9), downloaded={M9})
    except Exception as e:
        err = e
finally:
    builtins.open = real_open
check("with every input given, _adv_rank opens no file and runs no command",
      err is None and not opened and sub.calls == [], (err, opened, sub.calls))
with io.open(os.path.join(DASH, "aux_advisor.py"), encoding="utf-8") as f:
    SRC = f.read()
TREE = ast.parse(SRC)
mine = set()
for node in TREE.body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        mine.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(e, ast.Name):
                    mine.add(e.id)
check("every top-level name is namespaced (_adv_ / _ADV_ / _advr_ / _ADVR_ / _Advr)",
      all(n.startswith(("_adv_", "_ADV_", "_advr_", "_ADVR_", "_Advr")) for n in mine),
      sorted(n for n in mine if not n.startswith(("_adv_", "_ADV_", "_advr_", "_ADVR_", "_Advr"))))
clash = {}
for fn in sorted(os.listdir(DASH)):
    if not fn.endswith(".py") or fn == "aux_advisor.py":
        continue
    try:
        t = ast.parse(io.open(os.path.join(DASH, fn), encoding="utf-8").read())
    except SyntaxError:
        continue
    for node in t.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [e.id for tg in node.targets
                     for e in (tg.elts if isinstance(tg, ast.Tuple) else [tg]) if isinstance(e, ast.Name)]
        for nm in names:
            if nm in mine:
                clash.setdefault(fn, []).append(nm)
check("no other dashboard module defines a name aux_advisor.py defines (it loads FIRST)",
      not clash, clash)
bad_imp = [n for n in ast.walk(TREE) if isinstance(n, ast.ImportFrom) and n.module == "datetime"]
check("no `from datetime import ...` (the aux gotcha)", not bad_imp)
net = [a.name for n in ast.walk(TREE) if isinstance(n, (ast.Import, ast.ImportFrom))
       for a in n.names if a.name.split(".")[0] in ("urllib", "http", "socket", "requests", "subprocess")]
check("no network or subprocess import in aux_advisor.py", not net, net)
PROBES = {"model_online", "bg_online", "mlx_admission", "_bg_probe", "agent_wake", "urlopen"}
named = sorted({n.id for n in ast.walk(TREE) if isinstance(n, ast.Name) and n.id in PROBES}
               | {n.attr for n in ast.walk(TREE) if isinstance(n, ast.Attribute) and n.attr in PROBES}
               | {n.value for n in ast.walk(TREE) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str) and n.value in PROBES}
               | {str(n.value) for n in ast.walk(TREE) if isinstance(n, ast.Constant)
                  and n.value in (8080, 8081, 9119, 7788)})
check("its code never names a model port or a probe that touches one (model_online, bg_online, "
      "mlx_admission, urlopen, agent_wake)", not named, named)
check("aux_advisor.py sorts BEFORE aux_advisor_hw.py (so every hw name is resolved at call time)",
      sorted(["aux_advisor_hw.py", "aux_advisor.py"])[0] == "aux_advisor.py")

# ==========================================================================
print("\n7. without aux_advisor_hw.py (a load failure there) everything degrades, nothing raises")
routes = {}
lone = {"__name__": "aux_advisor_alone",
        "models_payload": lambda: {"models": [{"id": M27}, {"id": M9}]},
        "register_get": lambda p, f: routes.__setitem__(("GET", p), f),
        "register_post": lambda p, f: routes.__setitem__(("POST", p), f)}
with io.open(os.path.join(DASH, "aux_advisor.py"), encoding="utf-8") as f:
    exec(compile(f.read(), "aux_advisor.py", "exec"), lone)


class _Q:
    query = {}
    body = {"uses": ["chat"]}


pay = lone["models_payload"]()
check("models_payload still answers: every row's advice None, advisor ok false",
      all(m.get("advice") is None for m in pay["models"]) and pay["advisor"]["ok"] is False, pay)
st = routes[("GET", "/api/advisor/state")](_Q())
check("/api/advisor/state answers ok:false with the reason (no raise)",
      st["ok"] is False and "not loaded" in st.get("error", ""), st.get("error"))
rr = routes[("GET", "/api/advisor/rank")](_Q())
check("/api/advisor/rank answers ok:false (no raise)", rr["ok"] is False and rr["result"]["ok"] is False)
cp = lone["_adv_recommend_compat"](64, [])
check("_adv_recommend_compat falls back (advisor.ok false, the legacy key set)",
      cp["advisor"]["ok"] is False and {"tier", "primary", "background", "fits"} <= set(cp))
ans = routes[("POST", "/api/advisor/answers")](_Q())
check("POST /api/advisor/answers without settings_update -> 500, never a raise",
      isinstance(ans, tuple) and ans[1] == 500 and ans[0]["ok"] is False, ans)
check("_adv_rank itself says the estimator is not loaded",
      lone["_adv_rank"]({"supported": True}) == {"ok": False, "error": "the advisor's estimator is not loaded"})

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    print("FAILED: %r" % FAILS)
sys.exit(1 if FAILS else 0)
