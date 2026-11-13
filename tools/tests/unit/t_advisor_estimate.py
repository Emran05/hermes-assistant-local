#!/usr/bin/env python3
"""Model advisor, cluster C1 — the estimator (docs/plans/model-advisor-2026-10.md §5, §6, §10).

Runs the REAL dashboard/aux_advisor_hw.py, exec'd into a fresh namespace the
way server.py execs it, with the REAL _mem_ceilings (ast-extracted from
server.py) and — for the measured-here checks — the REAL aux_context.py exec'd
into the same namespace against a throwaway HOME with synthetic model-server
logs.  No model, no network, no dashboard; subprocess is a stub that refuses.

  1. facts: kv/state bytes recomputed from the config numbers; coverage of
     every curated id (server.py seeds + the onboarding catalog); sizes and
     ctx agree with those tables; k_decode re-derived from its anchor; the
     27B prefill constant IS aux_promptbudget's (one number, two cards)
  2. calibration goldens on the reference Mac (M5 Max 40c, 64 GB), §10 table
  3. the §5.8 table (11 machines x 5 models + background residents + the §3.5
     limits) against tools/tests/unit/fixtures/advisor_golden.json at +/-1%,
     and that fixture against the design's printed numbers
  4. properties: monotone in bandwidth / context / cache entries, MTP > 1 only
     where measured, every estimate labelled with formula and lo <= v <= hi,
     JSON-serialisable, and NO I/O inside the pure path
  5. measured-here overrides from this Mac's logs (aux_context's parser)
  6. tools/bench/advisor_calibrate.py: refuses on battery and in CI, and on AC
     fits k / slope / prefill from synthetic logs without touching anything

Regenerate the fixture after a DELIBERATE constant change (and review the diff):
    python3 tools/tests/unit/t_advisor_estimate.py --write-golden
"""
import ast
import builtins
import contextlib
import datetime
import importlib.util
import io
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
AUX = os.path.join(DASH, "aux_advisor_hw.py")
AUX_CX = os.path.join(DASH, "aux_context.py")
GOLDEN = os.path.join(_HERE, "fixtures", "advisor_golden.json")
CALIB = os.path.join(REPO, "tools", "bench", "advisor_calibrate.py")
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


TMP = tempfile.mkdtemp(prefix="t-advisor-est-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)


class RefusingSub:
    """The pure path must never run a command; this proves it."""
    TimeoutExpired = Exception

    def __init__(self):
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        raise AssertionError("estimator ran a command: %r" % (argv,))


with io.open(AUX, encoding="utf-8") as _f:
    SRC = _f.read()


def load(home=None, extra=None):
    g = {"__name__": "aux_advisor_hw_est", "HOME": home or TMP, "os": os, "sys": sys}
    server_real.server_functions(["_mem_ceilings"], g)
    g["subprocess"] = RefusingSub()
    if extra:
        g.update(extra)
    exec(compile(SRC, AUX, "exec"), g)
    return g


G = load()
F = G["_ADV_FACTS"]
M27 = "mlx-community/Qwen3.8-27B-4bit"
M27U = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
M35 = "mlx-community/Qwen3.6-35B-A3B-4bit"
M9 = "mlx-community/Qwen3.5-9B-4bit"
M4 = "mlx-community/Qwen3.5-4B-4bit"
M2 = "mlx-community/Qwen3.5-2B-4bit"
GRID = [M27, M35, M9, M4, M2]
ANS = {"uses": ["chat", "agent"]}
S = 18500
METAL_HERE = 55662788608

# The §5.8 machines.  (label, brand, gpu cores, RAM GB, machine name, metal bytes)
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

# docs/plans/model-advisor-2026-10.md §5.8, AS PRINTED (resident GiB, verdict,
# decode short, decode at the 25K turn, cold prefill s) — the design-time
# reference implementation's output, rounded.  The implementation re-derives
# from exact HF byte counts, so it matches within rounding plus ~3% (the 2B /
# 4B weigh 1-2% less than the design's round figures).
DESIGN_58 = {
    "Air M1, 8 GB": [(20.6, "no", 6, 4, 673), (21.3, "no", 14, 7, 266), (8.2, "no", 10, 5, 256),
                     (5.4, "no", 14, 6, 106), (2.9, "fits", 26, 13, 53)],
    "Air M2, 16 GB": [(20.6, "no", 9, 6, 442), (21.3, "no", 21, 11, 175), (8.2, "fits", 15, 8, 168),
                      (5.4, "fits", 22, 9, 70), (2.9, "fits", 40, 21, 35)],
    "Air M4, 24 GB": [(20.6, "no", 10, 7, 359), (21.3, "no", 23, 13, 142), (8.2, "fits", 17, 9, 137),
                      (5.4, "fits", 25, 10, 57), (2.9, "fits", 44, 23, 28)],
    "MBP M1 Max 32c, 32 GB": [(20.6, "tight", 25, 17, 150), (21.3, "no", 59, 32, 59),
                              (8.2, "fits", 43, 22, 57), (5.4, "fits", 63, 26, 24),
                              (2.9, "fits", 112, 57, 12)],
    "MBP M3 Pro 18c, 36 GB": [(21.8, "tight", 13, 8, 232), (21.7, "tight", 29, 16, 92),
                              (8.8, "fits", 22, 11, 89), (6.0, "fits", 31, 13, 37),
                              (3.1, "fits", 56, 29, 18)],
    "MBP M4 Max 32c, 36 GB": [(21.8, "tight", 29, 19, 111), (21.7, "tight", 67, 37, 44),
                              (8.8, "fits", 49, 25, 42), (6.0, "fits", 72, 30, 18),
                              (3.1, "fits", 128, 66, 9)],
    "mini M4 Pro 20c, 48 GB": [(23.0, "fits", 21, 14, 181), (22.1, "fits", 49, 27, 71),
                               (9.4, "fits", 36, 18, 69), (6.5, "fits", 52, 22, 29),
                               (3.3, "fits", 93, 48, 14)],
    "MBP M5 Max 40c, 64 GB": [(25.4, "fits", 49, 32, 25), (22.9, "fits", 115, 63, 10),
                              (10.5, "fits", 84, 43, 9), (7.7, "fits", 123, 51, 4),
                              (3.8, "fits", 219, 113, 2)],
    "Studio M2 Max 38c, 96 GB": [(25.4, "fits", 27, 18, 118), (22.9, "fits", 63, 35, 47),
                                 (10.5, "fits", 46, 24, 45), (7.7, "fits", 68, 28, 19),
                                 (3.8, "fits", 120, 62, 9)],
    "MBP M4 Max 40c, 128 GB": [(25.4, "fits", 34, 22, 90), (22.9, "fits", 80, 44, 35),
                               (10.5, "fits", 58, 30, 34), (7.7, "fits", 85, 35, 14),
                               (3.8, "fits", 152, 78, 7)],
    "Studio M2 Ultra 76c, 192 GB": [(25.4, "fits", 39, 26, 64), (22.9, "fits", 90, 49, 25),
                                    (10.5, "fits", 66, 34, 24), (7.7, "fits", 96, 40, 10),
                                    (3.8, "fits", 172, 88, 5)],
}
# §3.5 worked limits: (GPU budget, soft, limit)
DESIGN_35 = {
    "Air M1, 8 GB": (5.3, 6, 4.0), "Air M2, 16 GB": (10.6, 12, 10.0), "Air M4, 24 GB": (15.8, 17, 15.0),
    "MBP M1 Max 32c, 32 GB": (21.1, 23, 21.0), "MBP M3 Pro 18c, 36 GB": (27.0, 26, 24.0),
    "MBP M4 Max 32c, 36 GB": (27.0, 26, 24.0), "mini M4 Pro 20c, 48 GB": (36.0, 35, 33.0),
    "MBP M5 Max 40c, 64 GB": (51.8, 50, 48.0), "Studio M2 Max 38c, 96 GB": (72.0, 50, 48.0),
    "MBP M4 Max 40c, 128 GB": (96.0, 50, 48.0), "Studio M2 Ultra 76c, 192 GB": (144.0, 50, 48.0),
}
# §5.8 note: background-lane residents (25K turn) by RAM tier, from the design
# reference run: {ram: (9B, 4B, 2B)}
DESIGN_BG = {24: (8.2, 5.4, 2.9), 36: (8.2, 5.4, 2.9), 48: (8.8, 6.0, 3.1), 64: (9.4, 6.5, 3.3)}


def machine(g, row):
    label, brand, cores, ram, name, metal = row
    return g["_adv_machine"](brand, ram, gpu_cores=cores, machine_name=name, metal_bytes=metal)


def compute_golden(g):
    out = {"_about": "Generated by tools/tests/unit/t_advisor_estimate.py --write-golden from "
                     "dashboard/aux_advisor_hw.py. Answers chat+agent, 25K-token turn, "
                     "prefix 18,500 tokens (Balanced), primary lane; bg residents at 25K. "
                     "Regenerate only after a deliberate constant change, and review the diff.",
           "prefix_tokens": S, "uses": ANS["uses"], "machines": []}
    for row in MACHINES:
        hw = machine(g, row)
        lim = g["_adv_limits"](hw, env={})
        m = {"label": row[0], "brand": row[1], "gpu_cores": row[2], "ram_gb": row[3],
             "machine_name": row[4], "metal_bytes": row[5], "chip_row": hw["chip"]["row"],
             "gpu_budget_gb": lim["gpu_budget_gb"], "gpu_budget_source": lim["gpu_budget_source"],
             "soft_gb": lim["soft_gb"], "limit_gb": lim["limit_gb"], "binding": lim["binding"],
             "models": {}, "bg": {}}
        for mid in GRID:
            e = g["_adv_estimate"](mid, hw, ANS, S, measured=False)
            m["models"][mid] = {"resident_gb": e["resident_gb"],
                                "verdict": g["_adv_verdict"](e["resident_gb"], lim["limit_gb"]),
                                "decode_short_tps": e["decode_short_tps"],
                                "decode_turn_tps": e["decode_turn_tps"], "prefill_s": e["prefill_s"]}
        for mid in (M9, M4, M2):
            e = g["_adv_estimate"](mid, hw, ANS, S, lane="bg", measured=False)
            m["bg"][mid] = e["resident_gb"]
        out["machines"].append(m)
    return out


if "--write-golden" in sys.argv:
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with io.open(GOLDEN, "w", encoding="utf-8") as f:
        json.dump(compute_golden(G), f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote %s" % GOLDEN)
    print("TESTS 1 passed 0 failed")
    sys.exit(0)

# ==========================================================================
print("\n1. advisor facts")
KV = {M27: 65536, M27U: 65536, M9: 32768, M4: 32768, M2: 12288, M35: 20480}
GDN = {M27: 75497472, M9: 25165824, M4: 25165824, M2: 9437184, M35: 31457280}
for mid, want in sorted(KV.items()):
    check("kv bytes/token %s = %d (config numbers, recomputed)" % (mid.split("/")[-1], want),
          G["_adv_kv_bytes"](F[mid]) == want == F[mid]["kv_bytes_per_token"])
for mid, want in sorted(GDN.items()):
    check("GatedDeltaNet state %s = %d" % (mid.split("/")[-1], want),
          G["_adv_gdn_bytes"](F[mid]) == want == F[mid]["gdn_state_bytes"])
bad = [m for m, f in F.items() if G["_adv_kv_bytes"](f) != f["kv_bytes_per_token"]
       or G["_adv_gdn_bytes"](f) != f["gdn_state_bytes"]]
check("every facts record's stored kv/state bytes match its own config numbers", not bad, bad)
REQ = ("label", "verified_at", "arch", "params_b", "active_params_b", "weights_bytes",
       "download_bytes", "size_gb", "draft_size_gb", "drafter_bytes", "full_attention_layers",
       "kv_heads", "head_dim", "linear_layers", "linear_v_heads", "linear_k_dim", "linear_v_dim",
       "kv_bytes_per_token", "gdn_state_bytes", "ctx_native", "vision", "mtp", "k_decode",
       "k_source", "k_confidence", "ctx_slope_ms_per_1k", "ctx_slope_source",
       "prefill_ref_tps", "prefill_source", "prefill_confidence", "measured_ref", "verified",
       "quality", "quality_note", "uncensored", "license", "sources")
bad = {m: [k for k in REQ if k not in f] for m, f in F.items()}
bad = {m: v for m, v in bad.items() if v}
check("every record carries every §6.1 field", not bad, bad)
check("verified_at is an ISO date on every record",
      all(re.match(r"^\d{4}-\d{2}-\d{2}$", f["verified_at"]) for f in F.values()))
check("quality: chat/code/agent/docs/vision ordinals 0-10 on every record",
      all(set(f["quality"]) == {"chat", "code", "agent", "docs", "vision"}
          and all(isinstance(v, int) and 0 <= v <= 10 for v in f["quality"].values())
          for f in F.values()))
check("MoE records carry active bytes/token below their weights; dense records carry none",
      all((f["arch"] == "moe") == bool(f.get("active_bytes_per_token")) for f in F.values())
      and all(f["active_bytes_per_token"] < f["weights_bytes"] for f in F.values() if f["arch"] == "moe"))
check("only the stock 27B has a MEASURED MTP multiplier",
      [m for m, f in F.items() if (f.get("mtp") or {}).get("measured") is True] == [M27])
check("uncensored records are exactly the abliterated builds, none in a primary role",
      sorted(m for m, f in F.items() if f["uncensored"]) ==
      sorted([M27U, "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit",
              "froggeric/Qwen3.6-35B-A3B-Uncensored-Heretic-MLX-4bit",
              "PocketAiHub/Qwen3.8-27B-Abliterated-MLX",
              "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit",
              "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit",
              "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"])
      and all(f["role"] != "primary" for f in F.values() if f["uncensored"]))
check("the 35B-A3B drafter exists but is NOT enabled (stays off until measured)",
      F[M35]["drafter_bytes"] == 0 and F[M35]["drafter_available"]["enabled"] is False)
for mid, f in sorted(F.items()):
    a = f.get("k_anchor_tps")
    if a:
        want = a * f["weights_bytes"] / 1e9 / 614.0
        ok = abs(f["k_decode"] - want) < 5e-4
    else:
        want = G["_ADV_K_MOE"] if f["arch"] == "moe" else G["_ADV_K_SMALL"]
        ok = f["k_decode"] == want and f["k_confidence"] == "low"
    if not ok:
        check("k_decode %s" % mid, False, (f["k_decode"], want))
        break
else:
    check("k_decode = anchor tok/s x GB / 614 where measured; the labelled assumption otherwise", True)
for mid, f in F.items():
    sep = f.get("drafter") and "/" in f["drafter"] and "in this repo" not in f["drafter"]
    ok = f["size_gb"] == round(f["download_bytes"] / 1e9, 1) and \
        f["draft_size_gb"] == (round(f["drafter_download_bytes"] / 1e9, 1) if sep else 0.0)
    if not ok:
        check("size_gb/draft_size_gb derive from the byte counts: %s" % mid, False,
              (f["size_gb"], f["download_bytes"], f["draft_size_gb"]))
        break
else:
    check("size_gb / draft_size_gb derive from the exact byte counts (decimal GB)", True)


def literal(path, name):
    tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("%s not found in %s" % (name, path))


SEEDS = literal(os.path.join(DASH, "server.py"), "_SEED_MODELS")
ONB = literal(os.path.join(DASH, "aux_onboarding.py"), "_ONB_CATALOG")
check("every server.py seed id has advisor facts", all(m["id"] in F for m in SEEDS),
      [m["id"] for m in SEEDS if m["id"] not in F])
check("every onboarding catalog id has advisor facts", all(m["id"] in F for m in ONB),
      [m["id"] for m in ONB if m["id"] not in F])
bad = [(m["id"], m.get("size_gb"), F[m["id"]]["size_gb"], m.get("draft_size_gb"), F[m["id"]]["draft_size_gb"])
       for m in ONB if float(m.get("size_gb") or 0) != F[m["id"]]["size_gb"]
       or float(m.get("draft_size_gb") or 0) != F[m["id"]]["draft_size_gb"]]
check("size_gb / draft_size_gb agree with the onboarding catalog (verified by hand 2026-09-05)", not bad, bad)
bad = [m["id"] for m in SEEDS + ONB if m.get("ctx") and m["ctx"] != F[m["id"]]["ctx_native"]]
check("ctx agrees with the seeds and the catalog (262,144)", not bad, bad)
pbsrc = io.open(os.path.join(DASH, "aux_promptbudget.py"), encoding="utf-8").read()
m = re.search(r"^_PB_PREFILL_TOK_S\s*=\s*([\d.]+)", pbsrc, re.M)
check("the 27B's reference prefill IS aux_promptbudget._PB_PREFILL_TOK_S (one number, two cards)",
      m and float(m.group(1)) == F[M27]["prefill_ref_tps"], m and m.group(1))
m = re.search(r"^_PB_BASELINE_TOKENS\s*=\s*(\d+)", pbsrc, re.M)
check("the Full-profile prefix (20,600) sits on aux_promptbudget's measured baseline (20,622)",
      m and abs(int(m.group(1)) - G["_ADV_PREFIX_TOKENS"]["full"]) <= 100, m and m.group(1))
check("facts are JSON-serialisable", bool(json.dumps(F)))

# ==========================================================================
print("\n2. calibration goldens on the reference Mac (M5 Max 40c, 64 GB, Metal 51.84 GiB)")
HERE = G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro",
                         metal_bytes=METAL_HERE)
CHIP = HERE["chip"]
f27_ar = dict(F[M27], mtp=None)
dec = G["_adv_decode_tps"]


def within(v, want, tol):
    return abs(v - want) <= tol * want


v = dec(f27_ar, CHIP, 0)
check("27B AR 30.6 tok/s +/-5%% (got %.1f)" % v, within(v, 30.6, 0.05))
v = dec(F[M27], CHIP, 0, ["chat"])
check("27B MTP prose 47.8 +/-5%% (got %.1f)" % v, within(v, 47.8, 0.05))
v = dec(F[M27], CHIP, 0, ["code"])
check("27B MTP code at block 3: 58.7 +/-5%% (got %.1f; tools/bench/README.md 2026-09-04)" % v,
      within(v, 58.7, 0.05))
v = dec(F[M27], CHIP, 20972, ["chat", "agent"])
check("27B @20k (16-24k bucket, median prompt 20,972): 36.7 +/-12%% (got %.1f)" % v, within(v, 36.7, 0.12))
v = dec(F[M27], CHIP, 28218, ["chat", "agent"])
check("27B @28k (24-32k bucket): 34.1 +/-12%% (got %.1f)" % v, within(v, 34.1, 0.12))
e9 = G["_adv_estimate"](M9, HERE, ANS, S, lane="bg", measured=False)
check("9B short (1k ctx): 88 +/-10%% (got %.1f)" % e9["decode_short_tps"],
      within(e9["decode_short_tps"], 88.0, 0.10))
v = dec(F[M9], CHIP, 41663, lane="bg")
check("9B >=32k (median prompt 41,663): 32.3 +/-12%% (got %.1f)" % v, within(v, 32.3, 0.12))
v = dec(F[M9], CHIP, 28700, lane="bg")
check("9B 24-32k (median prompt 28,700): 42.0 +/-12%% (got %.1f)" % v, within(v, 42.0, 0.12))
v = G["_adv_prefill_s"](F[M27], CHIP, 20500)
check("27B cold read of 20.5k tokens: 25-29 s (got %.1f)" % v, 25.0 <= v <= 29.0)
v = G["_adv_prefill_s"](F[M9], CHIP, 24248)
check("9B cold read of 24.2k tokens: 12.3 +/-1.5 s (got %.1f)" % v, abs(v - 12.3) <= 1.5)
v = G["_adv_resident_gib"](F[M27], 64, "primary", 25000, 20500)
check("27B resident with 6 APC entries at a 20.5k prefix: 26 +/-2 GiB (got %.2f)" % v, abs(v - 26.0) <= 2.0)
v = G["_adv_resident_gib"](F[M9], 64, "bg", 25000, 20500)
check("9B background lane resident (4 entries, 20.5k): ~9.6 GiB, the design's D10 figure (got %.2f)" % v,
      abs(v - 9.6) <= 0.2)
e = G["_adv_estimate"](M27, HERE, ANS, S, measured=False)
check("this Mac's 27B reads as the reference: basis 'reference', +/-15% ranges",
      e["basis"]["decode"] == "reference" and e["basis"]["prefill"] == "reference"
      and abs(e["range"]["decode"][1] / e["decode_turn_tps"] - 1.15) < 0.01, e["basis"])

# ==========================================================================
print("\n3. the §5.8 golden fixture")
try:
    with io.open(GOLDEN, encoding="utf-8") as f:
        GOLD = json.load(f)
except (OSError, ValueError) as e:
    GOLD = None
    check("the fixture exists and parses (%s)" % GOLDEN, False, e)
if GOLD:
    NOW = compute_golden(G)
    check("fixture: 11 machines x 5 models + 3 background residents each",
          len(GOLD["machines"]) == 11 and all(len(m["models"]) == 5 and len(m["bg"]) == 3
                                              for m in GOLD["machines"]))
    drift = []
    for gm, nm in zip(GOLD["machines"], NOW["machines"]):
        for k in ("chip_row", "binding", "gpu_budget_source", "soft_gb"):
            if gm[k] != nm[k]:
                drift.append((gm["label"], k, gm[k], nm[k]))
        for k in ("limit_gb", "gpu_budget_gb"):
            if abs(gm[k] - nm[k]) > 0.01 * gm[k]:
                drift.append((gm["label"], k, gm[k], nm[k]))
        for mid in GRID:
            a, b = gm["models"][mid], nm["models"][mid]
            if a["verdict"] != b["verdict"]:
                drift.append((gm["label"], mid, "verdict", a["verdict"], b["verdict"]))
            for k in ("resident_gb", "decode_short_tps", "decode_turn_tps", "prefill_s"):
                if abs(a[k] - b[k]) > 0.01 * abs(a[k]) + 1e-9:
                    drift.append((gm["label"], mid, k, a[k], b[k]))
        for mid in (M9, M4, M2):
            if abs(gm["bg"][mid] - nm["bg"][mid]) > 0.01 * gm["bg"][mid]:
                drift.append((gm["label"], mid, "bg", gm["bg"][mid], nm["bg"][mid]))
    check("the implementation reproduces the fixture within +/-1% (verdicts and bindings exact)",
          not drift, drift[:12])
    off = []
    for gm in GOLD["machines"]:
        for mid, want in zip(GRID, DESIGN_58[gm["label"]]):
            a = gm["models"][mid]
            res, verdict, short, turn, pre = want
            if abs(a["resident_gb"] - res) > 0.16:
                off.append((gm["label"], mid, "resident", a["resident_gb"], res))
            if a["verdict"] != verdict:
                off.append((gm["label"], mid, "verdict", a["verdict"], verdict))
            for k, w in (("decode_short_tps", short), ("decode_turn_tps", turn), ("prefill_s", pre)):
                if abs(a[k] - w) > max(1.0, 0.03 * w):
                    off.append((gm["label"], mid, k, a[k], w))
    check("the fixture agrees with the design's printed §5.8 table (rounding + 3%)", not off, off[:12])
    off = []
    for gm in GOLD["machines"]:
        gpu, soft, limit = DESIGN_35[gm["label"]]
        if abs(gm["gpu_budget_gb"] - gpu) > 0.06 or gm["soft_gb"] != soft or abs(gm["limit_gb"] - limit) > 0.06:
            off.append((gm["label"], gm["gpu_budget_gb"], gm["soft_gb"], gm["limit_gb"], (gpu, soft, limit)))
    check("the fixture's limits agree with the design's §3.5 worked limits", not off, off)
    off = []
    for gm in GOLD["machines"]:
        want = DESIGN_BG.get(gm["ram_gb"])
        if want and gm["label"] in ("Air M4, 24 GB", "MBP M3 Pro 18c, 36 GB", "mini M4 Pro 20c, 48 GB",
                                    "MBP M5 Max 40c, 64 GB"):
            for mid, w in zip((M9, M4, M2), want):
                if abs(gm["bg"][mid] - w) > 0.16:
                    off.append((gm["label"], mid, gm["bg"][mid], w))
    check("background-lane residents agree with the design (9B 8.2-9.4, 4B 5.4-6.5, 2B 2.9-3.3 GiB)",
          not off, off)
    here = [m for m in GOLD["machines"] if m["label"].startswith("MBP M5 Max")][0]
    check("this Mac's plan today (27B + 9B lane) fits: %.1f + %.1f of %.0f GB"
          % (here["models"][M27]["resident_gb"], here["bg"][M9], here["limit_gb"]),
          G["_adv_verdict"](here["models"][M27]["resident_gb"] + here["bg"][M9], here["limit_gb"]) == "fits")

# ==========================================================================
print("\n4. properties")
answers_set = [{"uses": ["chat", "agent"]}, {"uses": ["chat"]}, {"uses": ["chat", "agent", "code"]},
               {"uses": ["chat", "agent", "docs"]}, {"uses": ["chat", "vision"]}, {"uses": []}, None]
all_est = []
for row in MACHINES:
    hw = machine(G, row)
    for mid in F:
        for lane in ("primary", "bg"):
            for a in answers_set:
                all_est.append(G["_adv_estimate"](mid, hw, a, S, lane=lane, measured=False))
check("%d estimates computed (every facts id x 11 machines x 2 lanes x 7 answer sets)" % len(all_est),
      len(all_est) == len(F) * 11 * 2 * 7)
bad = []
for e in all_est:
    if e.get("estimate") is not True or not e.get("supported"):
        bad.append((e["id"], "flag"))
        continue
    for k in ("decode", "decode_short", "prefill", "resident"):
        if not isinstance(e["formula"].get(k), str) or len(e["formula"][k]) < 20:
            bad.append((e["id"], "formula", k))
    for k, v in (("decode", e["decode_turn_tps"]), ("decode_short", e["decode_short_tps"]),
                 ("prefill", e["prefill_s"]), ("resident", e["resident_gb"])):
        lo, hi = e["range"][k]
        if not (lo <= v <= hi) or not (isinstance(v, float) and math.isfinite(v) and v > 0):
            bad.append((e["id"], "range", k, lo, v, hi))
    if e["basis"].get("decode") not in ("measured-here", "reference", "formula"):
        bad.append((e["id"], "basis"))
check("every estimate: estimate:true, a substituted formula per number, lo <= value <= hi, a basis",
      not bad, bad[:8])
try:
    json.dumps(all_est)
    ok = True
except (TypeError, ValueError) as ex:
    ok = ex
check("every estimate is JSON-serialisable", ok is True, ok)
bad = []
for f in F.values():
    for lane in ("primary", "bg"):
        for a in answers_set:
            mul = G["_adv_mtp_mult"](f, G["_advhw_uses"](a), lane)
            if mul > 1.0 and not ((f.get("mtp") or {}).get("measured") is True and lane == "primary"):
                bad.append((f["label"], lane, mul))
check("an MTP multiplier above 1 only where mtp.measured, and only on the primary lane", not bad, bad)
check("the unmeasured-drafter 27B Uncensored decodes at AR speed, with the note",
      abs(dec(F[M27U], CHIP, 0) - 30.6) < 0.2
      and any("not measured" in n for n in G["_adv_estimate"](M27U, HERE, ANS, S, measured=False)["notes"]))
mono = []
for mid, f in F.items():
    for lane in ("primary", "bg"):
        last = None
        for bw in (68.25, 100, 120, 150, 200, 273, 400, 546, 614, 819.2, 1228.8):
            v = dec(f, {"bandwidth_gbs": bw, "e_chip": 1.0, "p_index": 1.0}, 25000, lane=lane)
            if last is not None and v < last - 1e-9:
                mono.append((mid, lane, "bw", bw))
            last = v
        last = None
        for ctx in (0, 1000, 4000, 16000, 25000, 40000, 65536, 131072):
            v = dec(f, CHIP, ctx, lane=lane)
            if last is not None and v > last + 1e-9:
                mono.append((mid, lane, "ctx", ctx))
            last = v
check("decode: non-decreasing in bandwidth, non-increasing in context (every model, both lanes)",
      not mono, mono[:6])
mono = []
for mid, f in F.items():
    for lane in ("primary", "bg"):
        last = None
        for ram in (8, 16, 24, 32, 36, 48, 64, 96, 128, 192):
            v = G["_adv_resident_gib"](f, ram, lane, 25000, S)
            if last is not None and v < last - 1e-9:
                mono.append((mid, lane, "ram", ram))
            last = v
        last = None
        for ctx in (1000, 25000, 40000, 131072):
            v = G["_adv_resident_gib"](f, 64, lane, ctx, S)
            if last is not None and v < last - 1e-9:
                mono.append((mid, lane, "ctx", ctx))
            last = v
check("resident: non-decreasing in context and in APC entries (RAM tier)", not mono, mono[:6])
sweep_bad, n_sweep = [], 0
for key in sorted(G["_ADV_CHIPS"]):
    fam, var, cores = key
    brand = "Apple %s%s" % (fam, (" " + var) if var else "")
    last_lim = None
    for ram in (8, 16, 24, 32, 36, 48, 64, 96, 128, 192):
        hw = G["_adv_machine"](brand, ram, gpu_cores=cores)
        if hw["chip"]["row"] != G["_advhw_row_label"](key):
            sweep_bad.append((key, ram, "row", hw["chip"]["row"]))
        lim = G["_adv_limits"](hw, env={})["limit_gb"]
        if not lim or lim <= 0 or (last_lim is not None and lim < last_lim):
            sweep_bad.append((key, ram, "limit", lim, last_lim))
        last_lim = lim
        for mid in F:
            e = G["_adv_estimate"](mid, hw, ANS, S, measured=False)
            n_sweep += 1
            vals = [e[k] for k in ("resident_gb", "decode_short_tps", "decode_turn_tps", "prefill_s")]
            if not (e["supported"] and all(isinstance(v, float) and math.isfinite(v) and v > 0 for v in vals)
                    and e["decode_short_tps"] >= e["decode_turn_tps"]
                    and all(e["range"][k][0] <= v <= e["range"][k][1] for k, v in
                            zip(("resident", "decode_short", "decode", "prefill"), vals))):
                sweep_bad.append((key, ram, mid))
check("%d estimates over every chip row (M1..M5, base/Pro/Max/Ultra, every bin) x 8-192 GB: "
      "well-formed, ranged, limit positive and non-decreasing in RAM" % n_sweep, not sweep_bad, sweep_bad[:5])
small = [G["_adv_verdict"](G["_adv_estimate"](M2, G["_adv_machine"]("Apple M1", r, gpu_cores=8), ANS, S,
                                              measured=False)["resident_gb"],
                           G["_adv_limits"](G["_adv_machine"]("Apple M1", r, gpu_cores=8), env={})["limit_gb"])
         for r in (8, 16, 24, 32)]
big = [G["_adv_verdict"](G["_adv_estimate"](M27, G["_adv_machine"]("Apple M4", r, gpu_cores=10), ANS, S,
                                            measured=False)["resident_gb"],
                         G["_adv_limits"](G["_adv_machine"]("Apple M4", r, gpu_cores=10), env={})["limit_gb"])
       for r in (8, 16, 24)]
check("sanity: the 2B fits every Mac from 8 GB; the 27B fits no Mac below 32 GB",
      small == ["fits"] * 4 and big == ["no"] * 3, (small, big))
e_air = G["_adv_estimate"](M9, machine(G, MACHINES[2]), ANS, S, measured=False)
check("a fanless Air carries decode_sustained = 0.85 x the turn speed, and says so",
      abs(e_air["decode_sustained_tps"] - round(e_air["decode_turn_tps"] * 0.85, 1)) < 0.11
      and any("fanless" in n for n in e_air["notes"]), e_air["notes"])
check("a desktop carries no sustained figure",
      G["_adv_estimate"](M9, machine(G, MACHINES[8]), ANS, S, measured=False)["decode_sustained_tps"] is None)
e_slow = G["_adv_estimate"](M27, machine(G, MACHINES[4]), ANS, S, measured=False)
check("a slow cold read (> 45 s) carries the idle-suspend / Focused-profile note",
      e_slow["prefill_s"] > 45 and any("Focused prompt profile" in n for n in e_slow["notes"]))
check("the turn context follows the answers: chat/agent 25K, code 40K, docs 131,072",
      [G["_adv_turn_ctx"](u) for u in (["chat"], ["agent", "code"], ["code", "docs"])] == [25000, 40000, 131072])
check("the background lane always estimates a 25K turn, whatever the answers",
      G["_adv_estimate"](M9, HERE, {"uses": ["docs"]}, S, lane="bg", measured=False)["turn_ctx"] == 25000)
check("the drafter counts only on the primary lane",
      G["_adv_estimate"](M27, HERE, ANS, S, lane="bg", measured=False)["inputs"]["drafter_gib"] == 0
      and G["_adv_estimate"](M27, HERE, ANS, S, measured=False)["inputs"]["drafter_gib"] > 0.7)
check("MoE moves 1.65 GB per token -> battery 'light'; the 27B 'heavy'; the 9B 'moderate'",
      G["_adv_estimate"](M35, HERE, ANS, S, measured=False)["battery"] == "light"
      and G["_adv_estimate"](M27, HERE, ANS, S, measured=False)["battery"] == "heavy"
      and G["_adv_estimate"](M9, HERE, ANS, S, measured=False)["battery"] == "moderate")
check("a scaled chip row widens the range to +/-40%",
      abs(G["_adv_estimate"](M27, G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=32), ANS, S,
                             measured=False)["range"]["decode"][1]
          / G["_adv_estimate"](M27, G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=32), ANS, S,
                               measured=False)["decode_turn_tps"] - 1.40) < 0.01)
check("a measured chip row with measured constants: +/-25% (M4 Pro 20c, 27B)",
      abs(G["_adv_estimate"](M27, machine(G, MACHINES[6]), ANS, S, measured=False)["range"]["decode"][1]
          / G["_adv_estimate"](M27, machine(G, MACHINES[6]), ANS, S, measured=False)["decode_turn_tps"]
          - 1.25) < 0.01)
intel = G["_adv_machine"]("Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz", 32)
e = G["_adv_estimate"](M9, intel, ANS, S, measured=False)
check("an Intel Mac: supported False, the reason, no numbers", e["supported"] is False
      and e["decode_turn_tps"] is None and "Apple silicon" in e["reason"], e)
check("an id without facts -> None (the caller keeps the roster's own figures)",
      G["_adv_estimate"]("someone/not-curated", HERE, ANS, S, measured=False) is None)
check("_adv_estimates covers every facts id", set(G["_adv_estimates"](HERE, ANS, S, measured=False)) == set(F))

print("   no I/O on the pure path")
real_open = builtins.open
io_calls = []


def no_open(*a, **k):
    io_calls.append(("open", a[:1]))
    raise AssertionError("pure path opened %r" % (a[:1],))


def refuse(name):
    def f(*a, **k):
        io_calls.append((name, a[:1]))
        raise AssertionError("pure path called os.%s" % name)
    return f


saved = {n: getattr(os, n) for n in ("stat", "statvfs", "listdir", "scandir")}
G["subprocess"] = RefusingSub()
builtins.open = no_open
for n in saved:
    setattr(os, n, refuse(n))
try:
    hw_p = G["_adv_machine"]("Apple M3 Pro", 36, gpu_cores=18, machine_name="MacBook Pro", on_battery=True)
    lim_p = G["_adv_limits"](hw_p, env={})
    est_p = [G["_adv_estimate"](mid, hw_p, ANS, S, lane=l, measured=False) for mid in F for l in ("primary", "bg")]
    est_p += [G["_adv_estimate"](M27, hw_p, ANS, {"tokens": 20600, "source": "x"}, measured={
        "decode_short": 50.0, "decode_turn": 30.0, "prefill_tps": 700.0, "n_short": 9})]
    rows_p = G["_adv_chip_row"]("M4", "Max", None), G["_adv_verdict"](20, 24), G["_adv_gpu_budget"](36, 0, 0)
    pure_ok = True
except AssertionError as ex:
    pure_ok = ex
finally:
    builtins.open = real_open
    for n, fn in saved.items():
        setattr(os, n, fn)
check("_adv_machine / _adv_limits / _adv_estimate(measured False or given) / chip row / verdict / "
      "GPU budget run with open, stat, statvfs, listdir and subprocess all refusing",
      pure_ok is True and not io_calls and not G["subprocess"].calls, (pure_ok, io_calls[:3]))

# ==========================================================================
print("\n5. measured here: this Mac's own logs, through aux_context's parser")
HOME = tempfile.mkdtemp(prefix="home-", dir=TMP)
LOGS = os.path.join(HOME, ".hermes", "logs")
os.makedirs(LOGS)
io.open(os.path.join(HOME, ".hermes", "config.yaml"), "w", encoding="utf-8").write(
    "model:\n  default: mlx-community/Qwen3.8-27B-4bit\n  context_length: 65536\n")
T0 = datetime.datetime(2026, 9, 1, 9, 0, 0)
SEQ = [0]


def pair(model, prompt, cached, gen, decode, prefill_s=1.2):
    SEQ[0] += 1
    t = (T0 + datetime.timedelta(minutes=SEQ[0])).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return ("%s - INFO - Prefill completed: request=req-%d prompt_tokens=%d cached_tokens=%d "
            "elapsed=%.3fs rate=900.0 tok/s\n" % (t, SEQ[0], prompt, cached, prefill_s) +
            "%s - INFO - Request completed: endpoint=/chat/completions model=%s stream=True "
            "backend=continuous_batching prompt_tokens=%d generated_tokens=%d elapsed=2.500s "
            "prefill=900.0 tok/s decode=%.1f tok/s finish_reason=stop in_flight=0\n"
            % (t, model, prompt, gen, decode))


short27 = [51.0, 52.5, 53.2, 54.0, 55.9, 49.8]
turn27 = [33.0, 36.7, 35.1, 34.0, 38.2]
cold27 = [(20468, 26.1), (20470, 25.8), (20480, 27.0), (20500, 26.4), (20622, 26.9), (16489, 20.5)]
lines = []
for i, d in enumerate(short27):
    lines.append(pair(M27, 600 + 300 * i, 0 if i else 500, 120, d))
for i, d in enumerate(turn27):
    lines.append(pair(M27, 18000 + 2500 * i, 17500 + 2500 * i, 200, d))
for p_, s_ in cold27:
    lines.append(pair(M27, p_, 0, 30, 40.0, prefill_s=s_))
lines.append(pair(M27, 700, 0, 20, 999.0))                 # < 64 generated: excluded
lines.append(pair(M27U, 900, 0, 200, 31.0))                # another model: excluded
lines.append(pair(M27, 20000, 4000, 30, 35.0, prefill_s=21.0))    # 16k new tokens, cached 4000: rate yes, S no
io.open(os.path.join(LOGS, "mlx-server.log"), "w", encoding="utf-8").write("".join(lines))
short9 = [93.1, 90.0, 95.5, 92.2]                           # only 4: below the n >= 5 bar
io.open(os.path.join(LOGS, "mlx-bg.log"), "w", encoding="utf-8").write(
    "".join(pair(M9, 500 + 50 * i, 0, 100, d) for i, d in enumerate(short9)))

gm = {"__name__": "aux_cx_adv", "HOME": HOME, "DATA": os.path.join(HOME, ".hermes", "dashboard"),
      "os": os, "sys": sys, "_state_lock": threading.Lock(), "CHAT_JOBS": {},
      "active_model": lambda: M27, "register_get": lambda p, f: None,
      "register_post": lambda p, f: None}
server_real.server_functions(["_mem_ceilings"], gm)
gm["subprocess"] = RefusingSub()
exec(compile(SRC, AUX, "exec"), gm)                         # the sorted order: advisor_hw ...
exec(compile(io.open(AUX_CX, encoding="utf-8").read(), AUX_CX, "exec"), gm)   # ... then context
m = gm["_adv_measured"](M27)
cold_rates = [p_ / s_ for p_, s_ in cold27] + [16000 / 21.0]
check("decode_short = the median of the >= 64-token replies under 4k (n=6)",
      m and m["decode_short"] == round(statistics.median(short27), 1) and m["n_short"] == 6, m)
check("decode_turn = the median at 16-32k (n=5)",
      m["decode_turn"] == round(statistics.median([d for i, d in enumerate(turn27)
                                                   if 16000 <= 18000 + 2500 * i < 32000]), 1)
      and m["n_turn"] == 5, m)
check("prefill_tps = median of new_tokens / prefill_s over reads of >= 4,000 new tokens (n=7)",
      m["prefill_tps"] == round(statistics.median(cold_rates), 1) and m["n_prefill"] == 7, m)
check("prefix_tokens = the NEWEST genuinely cold read (16,489 — not the 4k-cached 20,000)",
      m["prefix_tokens"] == 16489, m)
check("since/until span the rows used; lanes tagged by aux_context",
      m["since"] and m["until"] and m["since"] <= m["until"] and m["lanes"] == ["primary"], m)
m9 = gm["_adv_measured"](M9)
check("the 9B (bg log) has 4 short replies: reported, but below the n >= 5 bar -> None",
      m9["n_short"] == 4 and m9["decode_short"] is None and m9["lanes"] == ["bg"], m9)
check("a model with no rows -> None", gm["_adv_measured"](M2) is None)
local = gm["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro", metal_bytes=METAL_HERE)
local["local"] = True
e = gm["_adv_estimate"](M27, local, ANS, None)
check("on THIS Mac the 27B estimate is overridden: decode short / turn / prefill basis measured-here",
      e["basis"]["decode_short"] == e["basis"]["decode"] == e["basis"]["prefill"] == "measured-here"
      and e["decode_short_tps"] == m["decode_short"] and e["decode_turn_tps"] == m["decode_turn"], e["basis"])
check("...the measured prefix (16,489) is S, and cold = S / measured rate",
      e["prefix_tokens"] == 16489 and e["prefix_source"] == "measured"
      and abs(e["prefill_s"] - round(16489 / m["prefill_tps"], 1)) <= 0.1, (e["prefix_tokens"], e["prefill_s"]))
check("...ranges are +/-10% and the label says 'measured on this Mac ... median of N replies'",
      abs(e["range"]["decode"][1] / e["decode_turn_tps"] - 1.10) < 0.01
      and "median of 5 replies" in e["formula"]["decode"]
      and any("contention" in n for n in e["notes"]), e["formula"]["decode"])
check("...and `measured.applied` records what was overridden",
      e["measured"]["applied"] == {"decode_short": True, "decode_turn": True, "prefill": True}, e["measured"])
e = gm["_adv_estimate"](M27, local, {"uses": ["chat", "agent", "code"]}, None)
check("a 40K coding turn is NOT overridden by the 16-32k bucket (decode stays a formula)",
      e["basis"]["decode"] != "measured-here" and e["basis"]["decode_short"] == "measured-here")
e = gm["_adv_estimate"](M9, local, ANS, None, lane="bg")
check("too few rows (n=4) -> the estimate keeps its formula/reference basis",
      e["basis"]["decode_short"] != "measured-here")
calls = []
real_cx = gm["_cx_rows"]
gm["_cx_rows"] = lambda p=None: (calls.append(p), real_cx(p))[1]
synthetic = gm["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40)
gm["_adv_estimate"](M27, synthetic, ANS, None)
check("a SYNTHETIC Mac never reads this Mac's logs", calls == [], calls)
gm["_adv_estimate"](M27, local, ANS, S, measured=False)
check("measured=False never reads the logs either", calls == [], calls)
gm["_adv_estimate"](M27, local, ANS, S)
check("the local Mac reads both logs, by name", len(calls) == 2, calls)
gm["_cx_rows"] = real_cx
check("_adv_measured without aux_context loaded -> None, no raise", G["_adv_measured"](M27) is None)
check("_adv_measured survives a parser that raises",
      load(extra={"_cx_rows": lambda p=None: 1 / 0, "_CX_LOG": "x"})["_adv_measured"](M27) is None)
pr = G["_adv_prefix"]
check("prefix: measured beats the profile", pr(M27, {"prefix_tokens": 19999})["tokens"] == 19999)
check("prefix: profile full 20,600 (measured) / lean 18,500 / focused 14,200 (predicted)",
      (pr(profile="full")["tokens"], pr(profile="lean")["tokens"], pr(profile="focused")["tokens"]) ==
      (20600, 18500, 14200) and pr(profile="lean")["source"] == "predicted")
check("prefix: the profile is read through prompt_budget_profile by name when not given",
      load(extra={"prompt_budget_profile": lambda: "focused"})["_adv_prefix"]()["tokens"] == 14200)
check("prefix: custom / unknown / a raising reader -> 18,500 assumed",
      pr(profile="custom")["tokens"] == 18500 and pr(profile="custom")["source"] == "assumed"
      and load(extra={"prompt_budget_profile": lambda: 1 / 0})["_adv_prefix"]()["tokens"] == 18500)
check("a bool or a non-positive prefix is ignored in favour of the default",
      G["_adv_estimate"](M27, HERE, ANS, True, measured=False)["prefix_tokens"] in (18500, 20600, 14200)
      and G["_adv_estimate"](M27, HERE, ANS, -5, measured=False)["prefix_tokens"] > 0)

# ==========================================================================
print("\n6. tools/bench/advisor_calibrate.py (never in CI, refuses on battery)")
spec = importlib.util.spec_from_file_location("advisor_calibrate_t", CALIB)
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)
AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true"
BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t41%; discharging; 2:00 remaining"
check("gate: battery -> refused, even with --i-am-on-ac",
      cal.power_gate(BATT, env={}, override=False)[0] is False
      and cal.power_gate(BATT, env={}, override=True)[0] is False)
check("gate: CI=true / GITHUB_ACTIONS / the unit tier's HERMES_TESTS_OFFLINE -> refused",
      cal.power_gate(AC, env={"CI": "true"})[0] is False
      and cal.power_gate(AC, env={"GITHUB_ACTIONS": "true"})[0] is False
      and cal.power_gate(AC, env={"HERMES_TESTS_OFFLINE": "1"})[0] is False)
check("gate: pmset unreadable -> refused unless --i-am-on-ac",
      cal.power_gate("", env={})[0] is False and cal.power_gate("", env={}, override=True)[0] is True)
check("gate: AC -> allowed", cal.power_gate(AC, env={})[0] is True)
out = io.StringIO()
reads = []
real_open2 = builtins.open


def spy_open(path, *a, **k):
    reads.append(str(path))
    return real_open2(path, *a, **k)


builtins.open = spy_open
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = cal.main(["--log", os.path.join(LOGS, "mlx-server.log")], env={}, pmset_text=BATT,
                      hw=local)
finally:
    builtins.open = real_open2
check("main() on battery exits 2 and reads no log", rc == 2 and not any("mlx-server.log" in r for r in reads),
      (rc, reads))
out = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
    rc = cal.main(["--log", os.path.join(LOGS, "mlx-server.log"), "--log", os.path.join(LOGS, "mlx-bg.log"),
                   "--json"], env={}, pmset_text=AC, hw=local)
try:
    doc = json.loads(out.getvalue())
except ValueError as ex:
    doc = {"error": str(ex), "raw": out.getvalue()[:400]}
check("main() on AC exits 0 and prints JSON", rc == 0 and "models" in doc, (rc, str(doc)[:300]))
r27 = (doc.get("models") or {}).get(M27) or {}
check("the 27B gets a fit: t0 / slope from the bucket medians, k_effective, prefill_ref_tps",
      isinstance(r27.get("fit", {}).get("t0_ms"), float) and isinstance(r27["fit"].get("slope_ms_per_1k"), float)
      and r27.get("k_effective") and r27.get("prefill", {}).get("ref_tps"), r27)
check("...prefill_ref_tps = the measured median rate / p_index (1.0 here)",
      abs(r27["prefill"]["ref_tps"] - statistics.median(cold_rates)) < 1.0, r27.get("prefill"))
check("...and its `last cold prompt` IS the advisor's S (one rule, not two copies): 16,489",
      r27["prefill"]["last_cold_prompt"] == 16489 == m["prefix_tokens"], r27.get("prefill"))
check("the script's sample thresholds are the advisor's (generated tokens, cold read size): no drift",
      cal.MIN_GEN == G["_ADV_MEASURED_MIN_GEN"] and cal.COLD_MIN_NEW == G["_ADV_COLD_MIN_NEW"])
check("...and it compares against the facts (delta % vs k_decode / prefill_ref_tps)",
      "facts" in r27 and "prefill_ref_tps" in r27["facts"], r27.get("facts"))
check("the calibration script reuses the advisor's facts and parser (one truth)",
      "aux_advisor_hw.py" in io.open(CALIB, encoding="utf-8").read()
      and "aux_context.py" in io.open(CALIB, encoding="utf-8").read())
csrc = io.open(CALIB, encoding="utf-8").read()
check("the script contains no network call, no launchctl, no model start",
      "urlopen" not in csrc and "launchctl" not in csrc and "Popen" not in csrc
      and "8080" not in csrc and "8081" not in csrc)
with contextlib.redirect_stdout(io.StringIO()) as o2, contextlib.redirect_stderr(io.StringIO()):
    rc = cal.main(["--dry-run"], env={}, pmset_text=AC, hw=local)
check("--dry-run exits 0 and only prints the plan", rc == 0 and "dry-run" in o2.getvalue())


# ==========================================================================
print("\n7. review additions: the formulas say what they compute, a given `measured`, bucket edges, S")
# (a) every formula string re-derives its own number — "formula with the numbers substituted"
F_DEC = re.compile(r"tok/s = 1000 / \(([\d.]+) \+ ([\d.]+) x ([\d.]+)\) = ([\d.]+)")
F_PRE = re.compile(r"([\d,]+) tokens / \(([\d.]+) tok/s .*? x p_index ([\d.]+) = ([\d.]+) tok/s\) = ([\d.]+) s")
F_RES = re.compile(r"^([\d.]+) GiB weights(?: \+ ([\d.]+) drafter)? \+ KV ([\d,]+) tokens x (\d+) KiB = ([\d.]+) "
                   r"\+ (\d+) APC x \(([\d,]+) x (\d+) KiB \+ (\d+) MiB state\) = ([\d.]+) \+ overhead 0\.5 "
                   r"\+ 3% of weights = ([\d.]+) -> ([\d.]+) GiB$")
bad = []
n_f = 0
for e in all_est:
    if not e.get("supported"):
        continue
    n_f += 1
    for key, ctx_, val in (("decode", e["turn_ctx"], e["decode_turn_tps"]),
                           ("decode_short", e["short_ctx"], e["decode_short_tps"])):
        m_ = F_DEC.search(e["formula"][key])
        if not m_:
            bad.append((e["id"], key, "unparsable", e["formula"][key][-80:]))
            continue
        a_, b_, c_, d_ = (float(x) for x in m_.groups())
        if abs(c_ - ctx_ / 1000.0) > 0.06 or abs(1000.0 / (a_ + b_ * c_) - d_) > 0.03 * d_ + 0.1 \
                or abs(d_ - val) > 0.11:
            bad.append((e["id"], key, (a_, b_, c_, d_), val))
    m_ = F_PRE.search(e["formula"]["prefill"])
    if not m_:
        bad.append((e["id"], "prefill", "unparsable", e["formula"]["prefill"][-80:]))
    else:
        S_, ref_, p_, tps_, sec_ = (float(x.replace(",", "")) for x in m_.groups())
        if S_ != e["prefix_tokens"] or abs(p_ - e["inputs"]["p_index"]) > 6e-5 \
                or abs(sec_ - e["prefill_s"]) > 0.11 or abs(S_ / tps_ - sec_) > 0.03 * sec_ + 0.1:
            bad.append((e["id"], "prefill", (S_, ref_, p_, tps_, sec_), e["prefill_s"]))
    m_ = F_RES.search(e["formula"]["resident"])
    if not m_:
        bad.append((e["id"], "resident", "unparsable", e["formula"]["resident"][-90:]))
    else:
        W_, D_, T_, kvk_, kv_, n_, S2_, kvk2_, mib_, apc_, ov_, tot_ = m_.groups()
        parts = float(W_) + float(D_ or 0) + float(kv_) + float(apc_) + float(ov_)
        # the pieces print at 2 dp and the total at 1 dp: the sum matches the estimate, the total its rounding
        if abs(parts - e["resident_gb"]) > 0.03 or abs(float(tot_) - e["resident_gb"]) > 0.06 \
                or int(n_) != e["inputs"]["apc_entries"] or int(T_.replace(",", "")) != e["turn_ctx"] \
                or int(S2_.replace(",", "")) != e["prefix_tokens"] or int(kvk_) * 1024 != e["inputs"]["kv_bytes_per_token"]:
            bad.append((e["id"], "resident", m_.groups(), e["resident_gb"]))
check("%d estimates: the decode, prefill and resident formulas re-derive the stated numbers "
      "(context, tokens, p_index, APC entries, KV size, the sum)" % n_f, not bad, bad[:4])

# (b) a `measured` dict passed in directly is applied (the documented "a dict = use it")
hw_syn = G["_adv_machine"]("Apple M3 Pro", 36, gpu_cores=18, machine_name="MacBook Pro")
md = {"decode_short": 50.0, "decode_turn": 30.0, "prefill_tps": 700.0, "n_short": 9, "n_turn": 6, "n_prefill": 7}
e = G["_adv_estimate"](M27, hw_syn, ANS, 18500, measured=md)
check("measured=<dict> on a SYNTHETIC Mac overrides decode short / turn and the cold read",
      e["decode_short_tps"] == 50.0 and e["decode_turn_tps"] == 30.0 and e["prefill_s"] == round(18500 / 700.0, 1)
      and e["basis"]["decode_short"] == e["basis"]["decode"] == e["basis"]["prefill"] == "measured-here"
      and e["measured"]["applied"] == {"decode_short": True, "decode_turn": True, "prefill": True}, e["measured"])
check("...with +/-10% ranges and the replies counted in the formula text",
      abs(e["range"]["decode_short"][1] / 50.0 - 1.10) < 0.01 and "median of 9 replies" in e["formula"]["decode_short"]
      and "median of 6 replies" in e["formula"]["decode"] and "median of 7 cold reads" in e["formula"]["prefill"], e["formula"])
e = G["_adv_estimate"](M27, hw_syn, ANS, 18500, measured={"decode_short": 50.0, "n_short": 9})
e0 = G["_adv_estimate"](M27, hw_syn, ANS, 18500, measured=False)
check("a partial dict overrides only what it carries (turn and prefill stay formulas)",
      e["measured"]["applied"] == {"decode_short": True, "decode_turn": False, "prefill": False}
      and e["decode_turn_tps"] == e0["decode_turn_tps"] and e["prefill_s"] == e0["prefill_s"], e["measured"])
check("measured=False and measured={} carry no override and no measured block",
      e0["measured"] is None and not G["_adv_estimate"](M27, hw_syn, ANS, 18500, measured={})["measured"])

# (c) the bucket edges of the medians, row by row
_EP = [1.0]


def row(prompt, gen=100, dec=50.0, new=None, cached=0, pre_s=1.0, stream=True, model=M27, lane="primary"):
    _EP[0] += 1.0
    return {"model": model, "prompt_tokens": prompt, "generated_tokens": gen, "decode_tps": dec,
            "new_tokens": (prompt - cached) if new is None else new, "cached_tokens": cached,
            "prefill_s": pre_s, "stream": stream, "lane": lane, "epoch": _EP[0],
            "ts": "2026-09-01 09:%02d:00" % (int(_EP[0]) % 60)}


mf = G["_adv_measured_from_rows"]
five = [row(1000, gen=64) for _ in range(5)]
m_ = mf(M27, five + [row(1000, gen=63, dec=999.0)])
check("a 63-token reply is not a decode sample, a 64-token one is (short n = 5, median 50)",
      m_["n_short"] == 5 and m_["decode_short"] == 50.0, m_)
m_ = mf(M27, five[:4] + [row(1000, gen=63)])
check("...and four 64-token replies plus a 63-token one are n = 4: below the gate -> None",
      m_["n_short"] == 4 and m_["decode_short"] is None, m_)
m_ = mf(M27, five + [row(1000, dec=0.0), row(1000, dec=-3.0), row(1000, dec=None)])
check("a reply with a zero / negative / missing decode rate is not a sample (n stays 5)",
      m_["n_short"] == 5 and m_["decode_short"] == 50.0, m_)
try:
    m_ = mf(M27, [row(6000, new=4000, pre_s=0.0), row(6000, new=4000, pre_s=None)])
    ok_ = m_["n_prefill"] == 0 and m_["prefill_tps"] is None
except Exception as ex_:                         # ZeroDivisionError would land here
    ok_, m_ = False, ex_
check("a cold read with a zero or missing prefill time is skipped, not divided by", ok_, m_)
a_ = row(19000)
b_ = row(18000)
a_["epoch"], b_["epoch"] = 10.0, 20.0
a_["ts"], b_["ts"] = "2026-09-01 09:05:00", "2026-09-01 09:01:00"
check("the NEWEST cold read is chosen by epoch, not by the ts string (18,000, not 19,000)",
      mf(M27, [a_, b_])["prefix_tokens"] == 18000 and mf(M27, [b_, a_])["prefix_tokens"] == 18000)
a_["epoch"] = None
check("...a row with no epoch sorts first (as 0): the epoched row wins", mf(M27, [a_, b_])["prefix_tokens"] == 18000)
m_ = mf(M27, [row(3999) for _ in range(5)] + [row(4000, dec=999.0)])
check("prompt 3999 is 'short', 4000 is not (and 4000 is not a turn either)",
      m_["n_short"] == 5 and m_["decode_short"] == 50.0 and m_["n_turn"] == 0, m_)
m_ = mf(M27, [row(16000, dec=30.0), row(31999, dec=30.0), row(20000, dec=30.0), row(24000, dec=30.0),
              row(28000, dec=30.0), row(15999, dec=999.0), row(32000, dec=999.0)])
check("turn bucket: 16000 and 31999 are in, 15999 and 32000 are out (n = 5, median 30)",
      m_["n_turn"] == 5 and m_["decode_turn"] == 30.0, m_)
m_ = mf(M27, [row(6000, new=4000, pre_s=2.0) for _ in range(5)] + [row(6000, new=3999, pre_s=0.01)])
check("cold prefill: 4000 new tokens count, 3999 do not (5 reads of 2000 tok/s)",
      m_["n_prefill"] == 5 and m_["prefill_tps"] == 2000.0, m_)
m_ = mf(M27, [row(6000, new=4000, pre_s=2.0) for _ in range(4)])
check("...four cold reads are below the gate -> None", m_["n_prefill"] == 4 and m_["prefill_tps"] is None, m_)
m_ = mf(M27, [row(20000, cached=256), row(19000, cached=257)])
check("S needs (almost) nothing cached: cached 256 qualifies, 257 does not -> the 256 row's prompt (20,000)",
      m_["prefix_tokens"] == 20000, m_)
m_ = mf(M27, [row(20000), row(18000, stream=False)])
check("S is a CONVERSATION turn: a newer non-streamed (auxiliary) cold row never becomes S",
      m_["prefix_tokens"] == 20000, m_)
m_ = mf(M27, [row(20000), row(18000, stream=None)])
check("...a log that never printed stream= (None) is kept: the newest row wins -> 18,000",
      m_["prefix_tokens"] == 18000, m_)
m_ = mf(M27, [row(20000), row(60000)])
check("S is Hermes's setup, not a pasted document: a newer 60,000-token cold row is not S",
      m_["prefix_tokens"] == 20000, m_)
m_ = mf(M27, [row(20000), row(32000), row(32001)])
check("...32,000 is still allowed, 32,001 is not -> 32,000",
      m_["prefix_tokens"] == 32000, m_)
m_ = mf(M27, [row(60000, stream=False, pre_s=60.0), row(20000, stream=False, pre_s=20.0)]
            + [row(6000, new=4000, pre_s=2.0, stream=False) for _ in range(3)])
check("the filtered rows STILL feed the prefill rate (5 reads: 1000, 1000, 2000 x 3 -> median 2000) "
      "but give no S", m_["n_prefill"] == 5 and m_["prefill_tps"] == 2000.0 and m_["prefix_tokens"] is None, m_)
m_ = mf(M27, [row(20000, model=M9), row(18000)])
check("S and every median are per model id", m_["prefix_tokens"] == 18000 and m_["n"] >= 1, m_)
# and through the REAL parser: stream=False must survive _cx_parse as False, not None
txt = ("2026-09-01 09:00:00,000 - INFO - Prefill completed: request=a1 prompt_tokens=50000 cached_tokens=0 "
       "elapsed=60.0s rate=833.3 tok/s\n"
       "2026-09-01 09:00:01,000 - INFO - Request completed: endpoint=/chat/completions model=%s stream=False "
       "backend=continuous_batching prompt_tokens=50000 generated_tokens=900 elapsed=80.0s prefill=833.3 tok/s "
       "decode=11.0 tok/s finish_reason=stop in_flight=0\n"
       "2026-09-01 09:01:00,000 - INFO - Prefill completed: request=b1 prompt_tokens=20468 cached_tokens=0 "
       "elapsed=26.0s rate=787.2 tok/s\n"
       "2026-09-01 09:01:30,000 - INFO - Request completed: endpoint=/chat/completions model=%s stream=True "
       "backend=continuous_batching prompt_tokens=20468 generated_tokens=200 elapsed=35.0s prefill=787.2 tok/s "
       "decode=35.0 tok/s finish_reason=stop in_flight=0\n") % (M27, M27)
rows_ = gm["_cx_parse"](txt)
check("through aux_context's real parser: the non-streamed 50,000-token cold row is skipped, S = 20,468",
      [r["stream"] for r in rows_] == [False, True] and gm["_adv_measured_from_rows"](M27, rows_)["prefix_tokens"] == 20468,
      (rows_[:1], gm["_adv_measured_from_rows"](M27, rows_)))

# (d) the slow-cold-read note fires exactly above 45 s
bad = []
for e in all_est:
    if not e.get("supported") or abs(e["prefill_s"] - 45.0) <= 0.1:
        continue
    has = any("re-reads Hermes" in n for n in e["notes"])
    if has != (e["prefill_s"] > 45.0):
        bad.append((e["id"], e["prefill_s"], has))
check("the idle-suspend / Focused-profile note is present iff the cold read is above 45 s (every estimate)",
      not bad, bad[:4])
check("...and the sweep really does straddle 45 s (some above, some below)",
      any(e.get("supported") and e["prefill_s"] > 46 for e in all_est)
      and any(e.get("supported") and e["prefill_s"] < 44 for e in all_est))

# (e) smaller things the mutation sweep found unguarded
e = G["_adv_estimate"](M27, HERE, ANS, S, measured=False)
check("the resident range is +/-10%", abs(e["range"]["resident"][1] / e["resident_gb"] - 1.10) < 0.01
      and abs(e["range"]["resident"][0] / e["resident_gb"] - 0.90) < 0.01, e["range"]["resident"])
check("the 27B's MEASURED context slope is used on the primary lane, the formula slope on the bg lane "
      "(the measurement was taken with the drafter running)",
      e["inputs"]["slope_source"] == "measured"
      and G["_adv_estimate"](M27, HERE, ANS, S, lane="bg", measured=False)["inputs"]["slope_source"] == "formula")
check("the decode formula names the MTP mix it used: 'MTP 1.65 (chat+agent)'",
      "MTP 1.65 (chat+agent)" in e["formula"]["decode"], e["formula"]["decode"])
check("a lone-string `uses` is one use, not its letters: 'code' -> the 40K turn",
      G["_adv_estimate"](M27, HERE, {"uses": "code"}, S, measured=False)["turn_ctx"] == 40000
      and G["_adv_estimate"](M27, HERE, {"uses": 5}, S, measured=False)["turn_ctx"] == 25000
      and G["_adv_estimate"](M27, HERE, {"uses": {"docs"}}, S, measured=False)["turn_ctx"] == 131072)
bad = []
for junk in (float("inf"), float("nan"), 10 ** 400, 2e9, 0, -1, "20000", [20000], {"tokens": float("inf")},
             {"tokens": True}, {"tokens": "x"}, {}):
    try:
        t_ = G["_adv_estimate"](M27, HERE, ANS, junk, measured=False)["prefix_tokens"]
        if t_ not in (18500, 20600, 14200):
            bad.append((repr(junk)[:30], t_))
    except Exception as ex_:
        bad.append((repr(junk)[:30], type(ex_).__name__))
check("a junk `prefix` (inf, nan, a 400-digit int, over a billion, 0, negative, a string, a list, a "
      "malformed dict) falls back to the default instead of raising", not bad, bad)
check("a good prefix dict keeps its source and profile",
      G["_adv_estimate"](M27, HERE, ANS, {"tokens": 15000.9, "source": "measured", "profile": "lean"},
                         measured=False)["prefix_source"] == "measured"
      and G["_adv_estimate"](M27, HERE, ANS, {"tokens": 15000.9, "source": "measured", "profile": "lean"},
                             measured=False)["prefix_tokens"] == 15000)
saved_ctx = F[M2]["ctx_native"]
F[M2]["ctx_native"] = 100000
e_long = G["_adv_estimate"](M2, HERE, {"uses": ["docs"]}, S, measured=False)
F[M2]["ctx_native"] = saved_ctx
check("a turn longer than the model's native context says so (docs on a 100K-context model)",
      any("native context" in n for n in e_long["notes"]), e_long["notes"])
check("...and no model in the catalog trips that note today (every ctx_native is 262,144)",
      not any("native context" in n for e_ in all_est if e_.get("supported") for n in e_["notes"]))


print("   labels, confidence, notes")
e27 = G["_adv_estimate"](M27, HERE, ANS, S, measured=False)
check("the label is the facts' label, and the lane and uncensored flag ride along",
      e27["label"] == "Qwen3.8-27B" and e27["lane"] == "primary" and e27["uncensored"] is False
      and G["_adv_estimate"](M27U, HERE, ANS, S, measured=False)["uncensored"] is True
      and G["_adv_estimate"](M9, HERE, ANS, S, lane="bg", measured=False)["lane"] == "bg")
r_ = G["_adv_estimate"](M27, {"supported": True, "chip": None, "ram_gb": 16.0}, ANS, S, measured=False)
check("a hw dict that is 'supported' but has no chip row is answered as unsupported, not a TypeError",
      r_["supported"] is False and r_["decode_turn_tps"] is None, r_)
check("'MTP' is named in the decode formula only for a model whose drafter multiplier was measured",
      "MTP" in e27["formula"]["decode"]
      and not any("MTP" in G["_adv_estimate"](m_id, HERE, ANS, S, measured=False)["formula"]["decode"]
                  for m_id in (M9, M4, M2, M35, M27U)))
check("the prefill formula says where its rate came from: 'on the reference' (27B), '19,000 / 4B' (4B), "
      "'19,000 / 3B x 0.30 MoE' (35B-A3B)",
      "on the reference" in e27["formula"]["prefill"]
      and "(19,000 / 4B)" in G["_adv_estimate"](M4, HERE, ANS, S, measured=False)["formula"]["prefill"]
      and "(19,000 / 3B x 0.30 MoE)" in G["_adv_estimate"](M35, HERE, ANS, S, measured=False)["formula"]["prefill"])
CONF = {}
for lbl, mid_, hw_ in (("27B ref", M27, HERE), ("27B M4 Pro", M27, machine(G, MACHINES[6])),
                       ("27B-U ref", M27U, HERE), ("9B ref", M9, HERE), ("4B ref", M4, HERE),
                       ("35B ref", M35, HERE), ("27B scaled M5 Max 32c", M27,
                                                G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=32))):
    e_ = G["_adv_estimate"](mid_, hw_, ANS, S, measured=False)
    CONF[lbl] = (e_["confidence"], e_["basis"]["decode"], e_["basis"]["decode_short"], e_["basis"]["prefill"],
                 e_["range"]["decode"][1] / e_["decode_turn_tps"] - 1.0)
near = lambda x, want: abs(x - want) <= 0.011      # the range ends are rounded to 1 dp
check("confidence and basis per case: reference+measured -> 'reference' 15%; a measured chip row -> 'formula' 25%; "
      "an assumed constant or a scaled row -> 'formula' 40%; a transferred constant -> 25%",
      CONF["27B ref"][:4] == ({"chip": "reference", "decode": "measured", "prefill": "measured"}, "reference",
                              "reference", "reference") and near(CONF["27B ref"][4], 0.15)
      and CONF["27B M4 Pro"][0] == {"chip": "medium", "decode": "measured", "prefill": "measured"}
      and CONF["27B M4 Pro"][1:4] == ("formula", "formula", "formula") and near(CONF["27B M4 Pro"][4], 0.25)
      and CONF["27B-U ref"][0]["decode"] == "medium" and CONF["27B-U ref"][1] == "formula"
      and near(CONF["27B-U ref"][4], 0.25)
      and CONF["9B ref"][0]["decode"] == "measured" and near(CONF["9B ref"][4], 0.15)
      and CONF["4B ref"][0]["decode"] == "low" and near(CONF["4B ref"][4], 0.40)
      and CONF["35B ref"][0] == {"chip": "reference", "decode": "low", "prefill": "low"}
      and near(CONF["35B ref"][4], 0.40)
      and CONF["27B scaled M5 Max 32c"][0]["chip"] == "low" and near(CONF["27B scaled M5 Max 32c"][4], 0.40), CONF)
n35 = G["_adv_estimate"](M35, HERE, ANS, S, measured=False)["notes"]
n35bg = G["_adv_estimate"](M35, HERE, ANS, S, lane="bg", measured=False)["notes"]
check("the 35B-A3B's unmeasured drafter is noted on the primary lane only, and stays off (no drafter GiB)",
      any("stays off until it is measured" in n for n in n35)
      and not any("stays off until it is measured" in n for n in n35bg)
      and G["_adv_estimate"](M35, HERE, ANS, S, measured=False)["inputs"]["drafter_gib"] == 0)
check("an assumed speed constant is noted (4B) and a measured one is not (27B)",
      any("assumption" in n for n in G["_adv_estimate"](M4, HERE, ANS, S, measured=False)["notes"])
      and not any("assumption" in n for n in e27["notes"]))
check("a scaled / low-confidence chip row is noted (M5 Max 32c) and a reference one is not",
      any("scaled or low confidence" in n for n in G["_adv_estimate"](
          M27, G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=32), ANS, S, measured=False)["notes"])
      and not any("scaled or low confidence" in n for n in e27["notes"]))
check("the macOS version rides with the estimate: '26.5.2', or None when unknown",
      G["_adv_estimate"](M27, G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, macos="26.5.2"), ANS, S,
                         measured=False)["inputs"]["macos"] == "26.5.2" and e27["inputs"]["macos"] is None)
check("a prefix dict with an empty or missing source is labelled 'given'",
      G["_adv_estimate"](M27, HERE, ANS, {"tokens": 15000, "source": ""}, measured=False)["prefix_source"] == "given"
      and G["_adv_estimate"](M27, HERE, ANS, {"tokens": 15000}, measured=False)["prefix_source"] == "given"
      and G["_adv_estimate"](M27, HERE, ANS, 15000, measured=False)["prefix_source"] == "given")
F[M2]["ctx_native"] = 131072
n_eq = G["_adv_estimate"](M2, HERE, {"uses": ["docs"]}, S, measured=False)["notes"]
F[M2]["ctx_native"] = 131071
n_over = G["_adv_estimate"](M2, HERE, {"uses": ["docs"]}, S, measured=False)["notes"]
F[M2]["ctx_native"] = saved_ctx
check("the native-context note fires strictly above the model's context (131,072 on a 131,072 model: no; "
      "131,071: yes)", not any("native context" in n for n in n_eq) and any("native context" in n for n in n_over))


print("   decimals, notes text, the bg 27B")
bad = []
for e in all_est:
    if not e.get("supported"):
        continue
    for key, nd in (("resident_gb", 2), ("decode_short_tps", 1), ("decode_turn_tps", 1), ("prefill_s", 1),
                    ("bytes_per_token_gb", 3)):
        if abs(e[key] * 10 ** nd - round(e[key] * 10 ** nd)) > 1e-6:
            bad.append((e["id"], key, e[key]))
    for key, nd in (("weights_gib", 3), ("drafter_gib", 3), ("slope_ms_per_1k", 4), ("mtp_mult", 3), ("prefill_tps", 1)):
        v = e["inputs"][key]
        if abs(v * 10 ** nd - round(v * 10 ** nd)) > 1e-6:
            bad.append((e["id"], key, v))
check("every number the UI will print is rounded where it is built (resident 2 dp, speeds 1 dp, bytes/token 3 dp, "
      "slope 4 dp, weights 3 dp, prefill rate 1 dp)", not bad, bad[:4])
check("bytes per token: the 27B moves its 16.05 GB, the 9B 5.95 GB, the 35B-A3B 1.65 GB, the Heretic 1.94 GB",
      (e27["bytes_per_token_gb"], G["_adv_estimate"](M9, HERE, ANS, S, measured=False)["bytes_per_token_gb"],
       G["_adv_estimate"](M35, HERE, ANS, S, measured=False)["bytes_per_token_gb"],
       G["_adv_estimate"]("Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit", HERE, ANS, S,
                          measured=False)["bytes_per_token_gb"]) == (16.055, 5.95, 1.65, 1.94))
e_s = G["_adv_estimate"](M27, machine(G, MACHINES[4]), ANS, 18500, measured=False)
check("the slow-read note quotes the setup size and the seconds it computed ('~18.5K-token setup: ~%d s')"
      % round(e_s["prefill_s"]),
      any(("~18.5K-token setup: ~%.0f s here" % e_s["prefill_s"]) in n for n in e_s["notes"]), e_s["notes"])
check("the 27B on the BACKGROUND lane keeps a measured k but a formula slope: decode confidence 'medium'",
      G["_adv_estimate"](M27, HERE, ANS, S, lane="bg", measured=False)["confidence"]["decode"] == "medium"
      and e27["confidence"]["decode"] == "measured")


print("   a decode_bench-shaped log: one short bucket still yields t0 and k")
bench = [pair(M4, 30, 0, 4, 100.0, prefill_s=0.05),                       # warmup: < 64 generated
         pair(M4, 5443, 0, 24, 110.0, prefill_s=6.3),                      # cold TTFT on the 6.5k prefix
         pair(M4, 5450, 5443, 24, 115.0, prefill_s=0.2)]                   # warm TTFT
bench += [pair(M4, 60, 0, 260, 118.0, prefill_s=0.05) for _ in range(3)]    # prose x 3
bench += [pair(M4, 70, 0, 260, 122.0, prefill_s=0.05) for _ in range(3)]    # code x 3
BENCH_LOG = os.path.join(LOGS, "decode_bench-server-blocknone.log")
io.open(BENCH_LOG, "w", encoding="utf-8").write("".join(bench))
out = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
    rc = cal.main(["--log", BENCH_LOG, "--json", "--model", M4], env={}, pmset_text=AC, hw=local)
doc_b = json.loads(out.getvalue())
r4 = (doc_b.get("models") or {}).get(M4) or {}
gb4 = F[M4]["weights_bytes"] / 1e9
t0_want = 1000.0 / 120.0 - G["_ADV_BETA"] * 1.0 * 65 / 1000.0
check("one short bucket (6 replies, median 120 tok/s at a 65-token prompt): a one-point fit, not None",
      rc == 0 and (r4.get("fit") or {}).get("points") == 1 and abs(r4["fit"]["t0_ms"] - t0_want) < 0.002
      and "not measured" in r4["fit"]["note"], r4.get("fit"))
check("...so k_effective = GB / (e_chip x BW) x 1000 / t0 = %.4f (the 4B's assumed k is 0.65)"
      % (gb4 / 614.0 * 1000.0 / t0_want),
      abs(r4.get("k_effective", 0) - gb4 / 614.0 * 1000.0 / t0_want) < 0.002, r4.get("k_effective"))
check("...but NO slope is claimed from one bucket (the facts' slope delta is None)",
      "slope_ref_ms_per_1k" not in r4 and r4["facts"]["delta_pct"]["slope"] is None, r4.get("facts"))
check("...and the one cold read of the 6.5k prefix gives n = 1 prefill sample: 5,443 tokens / 6.3 s = 864 tok/s",
      r4["prefill"]["n"] == 1 and abs(r4["prefill"]["median_tps"] - 864.0) < 1.0, r4.get("prefill"))
buck = lambda n, tps, prompt: [{"bucket": "x", "n": n, "median_decode_tps": tps, "median_prompt": prompt}]
sbf = cal.single_bucket_fit
check("a one-bucket fit is refused when the bucket is thin (2 replies), long (28k prompt), "
      "absurd (t0 <= 0) or the model has no facts",
      sbf(gm, F[M4], CHIP, buck(2, 120.0, 65)) is None and sbf(gm, F[M4], CHIP, buck(50, 40.0, 28000)) is None
      and sbf(gm, F[M4], CHIP, buck(9, 100000.0, 7000)) is None and sbf(gm, None, CHIP, buck(9, 120.0, 65)) is None
      and sbf(gm, F[M4], {}, buck(9, 120.0, 65)) is None and sbf(gm, F[M4], CHIP, []) is None)
tbl = io.StringIO()
with contextlib.redirect_stdout(tbl):
    cal.table({"hw": {}, "models": {M4: r4}})
check("the table output for a one-bucket model names the missing slope instead of printing one",
      "no slope: it needs two context buckets" in tbl.getvalue()
      and "one bucket (<2k, n=6)" in tbl.getvalue(), tbl.getvalue()[-400:])

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    print("FAILED:", FAILS)
sys.exit(1 if FAILS else 0)
