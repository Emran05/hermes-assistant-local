# aux_advisor_hw.py — model advisor, part 1 (round 3, cluster C1): what this
# Mac is, and what a given model would cost on it.
#
# Design: docs/plans/model-advisor-2026-10.md §3 (detection), §3.3 (chip
# table), §3.4 (GPU working-set budget), §3.5 (plan limit), §5 (estimator),
# §6.1/6.2 (advisor facts).  The ranking, the routes and the UI are other
# clusters (aux_advisor.py / aux_advisor.js); they call the names listed under
# CONTRACT below and nothing else.
#
# WHAT THIS MODULE IS
#   * Detection.  Chip family/variant/generation, GPU cores, the real GPU
#     working-set budget (Metal, in a <=2 s subprocess), RAM, power source,
#     Low Power Mode, thermal state, fanless-or-not, free disk.  Every command
#     is an ABSOLUTE path (launchd's PATH has no /usr/sbin), needs no sudo, and
#     goes through _adv_run(), which calls the module-level name `subprocess`
#     so a harness can stub it.  Every parser is PURE.  Detection NEVER raises:
#     a fact that cannot be read degrades to None plus a `source` saying so.
#   * A static chip table (_ADV_CHIPS): bandwidth plus llama.cpp's measured
#     pp512/tg128 rows, from which two dimensionless factors are derived —
#     e_chip (decode bandwidth efficiency) and p_index (prefill compute), both
#     relative to the reference chip, the M5 Max 40c this repo is measured on.
#   * Advisor facts (_ADV_FACTS): one record per curated model id — exact
#     weight bytes and architecture numbers read off Hugging Face's public
#     metadata by hand (2026-10-26), plus the measured calibration constants.
#   * The estimator: resident memory per lane, decode tok/s (short and at the
#     turn's context), cold prefill of Hermes's own setup prompt, a battery
#     cost label, download size.  EVERY number it returns carries
#     `estimate: true`, a `basis` (measured-here | reference | formula), a
#     human `formula` with the numbers substituted, and a `range` [lo, hi].
#     The UI never shows a bare number.
#   * "Measured here" overrides: medians from this Mac's own model-server logs,
#     read through aux_context's parser (_cx_rows — file reads only).
#
# WHAT IT NEVER DOES
#   * No network.  No model is woken: nothing here touches :8080/:8081/:9119,
#     model_online(), bg_online() or mlx_admission().  The Metal probe creates
#     a Metal DEVICE in a throwaway child process (no model, no MLX, no venv).
#   * No writes of any kind: no settings.json, no files, no caches on disk.
#   * No routes, no threads, nothing at import time but definitions.
#
# LOAD ORDER (server.py execs aux_*.py SORTED): aux_advisor.py runs BEFORE this
# file, aux_context.py / aux_promptbudget.py / aux_onboarding.py AFTER it.  So
# every foreign name (_machine_ram_gb, _mem_ceilings, _disk_free_gb, _cx_rows,
# _CX_LOG, _CX_LOG_BG, prompt_budget_profile, _idle_min, DISK_HEADROOM_GB) is
# resolved BY NAME AT CALL TIME through _advhw_g(), never captured at module
# load — the discipline aux_index/aux_onboarding document.  This module never
# does `from datetime import datetime` (CLAUDE.md aux gotcha) and imports
# everything under a private alias.
#
# UNITS (§5.1): memory is GiB (2**30) — the number Apple prints on the box,
# _machine_ram_gb, footprint(1) and MLX_SOFT_GB.  Download sizes are DECIMAL
# GB (what HF and the disk show).  Bandwidth is decimal GB/s, so the decode
# formula divides decimal bytes.  The facts store exact bytes and every form
# is derived from them: a 27B is 16.08 GB to download and 14.95 GiB of weights.
#
# CONTRACT (the names other clusters call; the full shapes are in the
# docstrings and in docs_notes of the round-3 report):
#   _adv_hw(bust=False)                  -> hw dict (this Mac, cached)
#   _adv_machine(brand, ram_gb, ...)     -> hw dict for a SYNTHETIC Mac (pure)
#   _adv_limits(hw=None, env=None)       -> plan memory limit + which term binds
#   _adv_estimate(mid, hw, answers=None, prefix=None, lane="primary",
#                 measured=None)         -> estimate dict | None (no facts)
#   _adv_verdict(total_gb, limit_gb)     -> "fits" | "tight" | "no"
#   _adv_apc_entries(ram_gb, lane)       -> int (mirrors mlx-server*.sh)
#   _adv_measured(mid, rows=None)        -> measured-here dict | None
#   _adv_prefix(mid=None, measured=None, profile=None) -> {tokens,source,profile}
#   _adv_hw_notes(hw)                    -> [str] (battery / Air / thermal ...)
#   _adv_download_gb(mid), _adv_disk_ok(need_gb, free_gb)
#   _adv_battery_label(bytes_per_token_gb, with_bg=False)
#   _adv_formulas(), _adv_constants()    -> for the state route's disclosure
#   _ADV_FACTS, _ADV_CHIPS, _ADV_REF_KEY
#   pure parsers: _adv_parse_brand, _adv_parse_ioreg_cores,
#     _adv_parse_sp_displays, _adv_parse_sp_hardware, _adv_parse_perflevels,
#     _adv_parse_batt, _adv_parse_pmset, _adv_parse_therm, _adv_form,
#     _adv_gpu_budget, _adv_chip_row
# Private helpers are `_advhw_*`; nothing else in the shared namespace may
# define an `_adv_*` name this file defines (t_advisor_hw.py checks it).

import copy as _advhw_copy
import json as _advhw_json
import math as _advhw_math
import os as _advhw_os
import re as _advhw_re
import statistics as _advhw_stats
import sys as _advhw_sys
import threading as _advhw_threading
import time as _advhw_time

if "subprocess" not in globals():       # server.py already has it (and a
    import subprocess                   # harness may have stubbed it): keep that

_ADV_HW_VERSION = 1

# --------------------------------------------------------------------------
# constants (each one is quoted by _adv_constants() so the UI can show it)
# --------------------------------------------------------------------------
_ADV_GIB = float(2 ** 30)
_ADV_REF_KEY = ("M5", "Max", 40)         # this Mac: every constant is measured here
_ADV_REF_BW = 614.0                      # GB/s, M5 Max 40c (Apple spec)
_ADV_REF_PP = 3219.99                    # llama.cpp #4167 pp512, Llama-2-7B Q4_0
_ADV_REF_TG = 119.92                     # llama.cpp #4167 tg128

_ADV_RUN_TIMEOUT = 3.0                   # seconds per detection command
_ADV_PROBE_TIMEOUT = 2.0                 # the Metal probe (§3.4)
_ADV_DYNAMIC_TTL = 60.0                  # power / thermal / disk (§3.2)

_ADV_SHORT_CTX = 1000                    # "short" decode: a 1k-token context
_ADV_TURN_CTX = {"chat": 25000, "agent": 25000, "code": 40000, "docs": 131072}
_ADV_TURN_DEFAULT = 25000
_ADV_BG_TURN_CTX = 25000                 # the background lane's turn (§5.8)

# decode (§5.3)
_ADV_BETA = 0.48     # ms per output token per 1k ctx tokens, at 32 KiB/token, 614 GB/s
_ADV_K_SMALL = 0.65  # k for dense <=4B models: ASSUMPTION (fixed overhead dominates)
_ADV_K_MOE = 0.32    # k for MoE on active bytes: community M5 Max A3B 110-126 tok/s
_ADV_FANLESS_SUSTAINED = 0.85   # MacBook Air, runs longer than ~5 min (reports, unmeasured)

# prefill (§5.4)
_ADV_K_PREFILL = 19000.0         # B-params x tok/s on the reference, dense default
_ADV_MOE_PREFILL_EFF = 0.30      # MoE prefill efficiency, low confidence
_ADV_SLOW_PREFILL_S = 45.0       # above this the plan gets the idle-suspend note

# The cold prefix Hermes sends (§5.4).  full = measured (mlx-server.log
# cached_tokens=0 lines, 20,468-20,622); lean ("Balanced", the profile new
# installs start with) and focused are predicted from schema bytes in
# docs/plans/1.2-baseline.md.
_ADV_PREFIX_TOKENS = {"full": 20600, "lean": 18500, "focused": 14200}
_ADV_PREFIX_DEFAULT = 18500

# memory (§3.5, §5.2)
_ADV_OVERHEAD_GIB = 0.5          # runtime / activations / Metal, plus 3% of weights
_ADV_OVERHEAD_FRAC = 0.03
_ADV_FITS_FRAC = 0.85            # plan verdict: fits <= 0.85 x limit, tight <= limit
_ADV_SOFT_MARGIN_GB = 2.0        # admission refuses at >= soft; 2 GB = one ~20k turn of KV
_ADV_DISK_HEADROOM_GB = 5.0      # server.py DISK_HEADROOM_GB (resolved by name first)

# measured-here (§5.3, D6)
_ADV_MEASURED_MIN_N = 5
_ADV_MEASURED_MIN_GEN = 64
_ADV_COLD_MIN_NEW = 4000
_ADV_COLD_MAX_CACHED = 256       # "the whole prompt was read": a genuinely cold row
_ADV_COLD_MAX_PREFIX = 32000     # a cold row longer than this carried more than Hermes's setup
                                 # (a pasted document, the compression summariser): never S
_ADV_PROBE_RETRY_S = 600.0       # a FAILED Metal probe is retried at most this often
_ADV_SHORT_MAX_PROMPT = 4000
_ADV_TURN_MIN_PROMPT, _ADV_TURN_MAX_PROMPT = 16000, 32000

# ranges (§5.5): basis / confidence -> +/- fraction
_ADV_RANGE_FRAC = {"measured-here": 0.10, "reference": 0.15, "row": 0.25, "low": 0.40}
_ADV_RESIDENT_RANGE = 0.10

# battery cost (§5.6): decimal GB moved per generated token
_ADV_BATTERY_STEPS = ((2.0, "light"), (7.0, "moderate"))
_ADV_BATTERY_ORDER = ("light", "moderate", "heavy")


def _advhw_g(name, default=None):
    """A server.py / sibling-aux global BY NAME AT CALL TIME (see LOAD ORDER)."""
    return globals().get(name, default)


# --------------------------------------------------------------------------
# The chip table (§3.3) — DATA, one dict literal.
#
# bw = memory bandwidth, decimal GB/s (Apple / Wikipedia spec).  pp/tg are
# llama.cpp Llama-2-7B Q4_0 pp512 / tg128 from ggml-org/llama.cpp discussion
# #4167 (fetched 2026-10-26).  Derived, relative to the reference M5 Max 40c:
#     e_chip  = (tg / bw) / (119.92 / 614)     decode bandwidth efficiency
#     p_index = pp / 3219.99                   prefill compute
# Model size cancels out of both, and for a MEASURED row the decode estimate
# does not depend on bw at all (k x e_chip x bw = k x tg x 614/119.92).
# A "scaled" row has no llama.cpp measurement: it keeps its source row's
# e_chip and scales p_index per GPU core.  t_advisor_hw.py recomputes every
# derived column from the raw ones, so a hand edit cannot leave them stale.
# confidence: reference | medium (a measured row) | low (scaled, or a row the
# page reader had to infer / whose memory options disagree across sources).
# --------------------------------------------------------------------------
_ADV_CHIPS = {
    ("M1", "", 7): {"bw": 68.25, "pp": 107.81, "tg": 14.19, "e_chip": 1.0645, "p_index": 0.0335, "source": "measured", "confidence": "medium"},
    ("M1", "", 8): {"bw": 68.25, "pp": 117.96, "tg": 14.15, "e_chip": 1.0615, "p_index": 0.0366, "source": "measured", "confidence": "medium"},
    ("M1", "Pro", 14): {"bw": 200.0, "pp": 232.55, "tg": 35.52, "e_chip": 0.9093, "p_index": 0.0722, "source": "measured", "confidence": "medium"},
    ("M1", "Pro", 16): {"bw": 200.0, "pp": 266.25, "tg": 36.41, "e_chip": 0.9321, "p_index": 0.0827, "source": "measured", "confidence": "medium"},
    ("M1", "Max", 24): {"bw": 400.0, "pp": 400.26, "tg": 54.61, "e_chip": 0.6990, "p_index": 0.1243, "source": "measured", "confidence": "medium"},
    ("M1", "Max", 32): {"bw": 400.0, "pp": 530.06, "tg": 61.19, "e_chip": 0.7832, "p_index": 0.1646, "source": "measured", "confidence": "medium"},
    ("M1", "Ultra", 48): {"bw": 800.0, "pp": 772.24, "tg": 74.93, "e_chip": 0.4796, "p_index": 0.2398, "source": "measured", "confidence": "medium"},
    ("M1", "Ultra", 64): {"bw": 800.0, "pp": 1030.04, "tg": 83.73, "e_chip": 0.5359, "p_index": 0.3199, "source": "measured", "confidence": "medium"},
    ("M2", "", 8): {"bw": 100.0, "pp": 145.91, "tg": 21.70, "e_chip": 1.1111, "p_index": 0.0453, "source": "measured", "confidence": "medium"},
    ("M2", "", 10): {"bw": 100.0, "pp": 179.57, "tg": 21.91, "e_chip": 1.1218, "p_index": 0.0558, "source": "measured", "confidence": "medium"},
    ("M2", "Pro", 16): {"bw": 200.0, "pp": 294.24, "tg": 37.87, "e_chip": 0.9695, "p_index": 0.0914, "source": "measured", "confidence": "medium"},
    ("M2", "Pro", 19): {"bw": 200.0, "pp": 341.19, "tg": 38.86, "e_chip": 0.9948, "p_index": 0.1060, "source": "measured", "confidence": "medium"},
    ("M2", "Max", 30): {"bw": 400.0, "pp": 537.60, "tg": 60.99, "e_chip": 0.7807, "p_index": 0.1670, "source": "measured", "confidence": "medium"},
    ("M2", "Max", 38): {"bw": 400.0, "pp": 671.31, "tg": 65.95, "e_chip": 0.8442, "p_index": 0.2085, "source": "measured", "confidence": "medium"},
    ("M2", "Ultra", 60): {"bw": 800.0, "pp": 1013.81, "tg": 88.64, "e_chip": 0.5673, "p_index": 0.3148, "source": "measured", "confidence": "medium"},
    ("M2", "Ultra", 76): {"bw": 800.0, "pp": 1238.48, "tg": 94.27, "e_chip": 0.6033, "p_index": 0.3846, "source": "measured", "confidence": "medium"},
    ("M3", "", 8): {"bw": 102.4, "scaled_from": 10, "e_chip": 1.0670, "p_index": 0.0464, "source": "scaled", "confidence": "low"},
    ("M3", "", 10): {"bw": 102.4, "pp": 186.75, "tg": 21.34, "e_chip": 1.0670, "p_index": 0.0580, "source": "measured", "confidence": "medium", "flag": "row flagged in the source table"},
    ("M3", "Pro", 14): {"bw": 150.0, "pp": 269.49, "tg": 30.65, "e_chip": 1.0462, "p_index": 0.0837, "source": "measured", "confidence": "medium", "flag": "row flagged in the source table"},
    ("M3", "Pro", 18): {"bw": 150.0, "pp": 341.67, "tg": 30.74, "e_chip": 1.0493, "p_index": 0.1061, "source": "measured", "confidence": "medium"},
    ("M3", "Max", 30): {"bw": 300.0, "pp": 567.59, "tg": 56.58, "e_chip": 0.9656, "p_index": 0.1763, "source": "measured", "confidence": "medium"},
    ("M3", "Max", 40): {"bw": 400.0, "pp": 759.70, "tg": 66.31, "e_chip": 0.8488, "p_index": 0.2359, "source": "measured", "confidence": "medium"},
    ("M3", "Ultra", 60): {"bw": 819.2, "pp": 1073.09, "tg": 88.40, "e_chip": 0.5525, "p_index": 0.3333, "source": "measured", "confidence": "medium"},
    ("M3", "Ultra", 80): {"bw": 819.2, "pp": 1471.24, "tg": 92.14, "e_chip": 0.5759, "p_index": 0.4569, "source": "measured", "confidence": "medium"},
    ("M4", "", 8): {"bw": 120.0, "scaled_from": 10, "e_chip": 1.0287, "p_index": 0.0550, "source": "scaled", "confidence": "low"},
    ("M4", "", 10): {"bw": 120.0, "pp": 221.29, "tg": 24.11, "e_chip": 1.0287, "p_index": 0.0687, "source": "measured", "confidence": "medium"},
    ("M4", "Pro", 16): {"bw": 273.0, "pp": 364.06, "tg": 49.64, "e_chip": 0.9310, "p_index": 0.1131, "source": "measured", "confidence": "medium"},
    ("M4", "Pro", 20): {"bw": 273.0, "pp": 439.78, "tg": 50.74, "e_chip": 0.9516, "p_index": 0.1366, "source": "measured", "confidence": "medium"},
    ("M4", "Max", 32): {"bw": 410.0, "pp": 713.93, "tg": 69.95, "e_chip": 0.8735, "p_index": 0.2217, "source": "measured", "confidence": "medium"},
    ("M4", "Max", 40): {"bw": 546.0, "pp": 885.68, "tg": 83.06, "e_chip": 0.7789, "p_index": 0.2751, "source": "measured", "confidence": "medium"},
    ("M5", "", 8): {"bw": 153.6, "scaled_from": 10, "e_chip": 1.0627, "p_index": 0.1796, "source": "scaled", "confidence": "low"},
    ("M5", "", 10): {"bw": 153.6, "pp": 722.79, "tg": 31.88, "e_chip": 1.0627, "p_index": 0.2245, "source": "measured", "confidence": "low", "flag": "column mapping inferred by the page reader"},
    ("M5", "Pro", 16): {"bw": 307.0, "pp": 1340.44, "tg": 67.59, "e_chip": 1.1273, "p_index": 0.4163, "source": "measured", "confidence": "medium"},
    ("M5", "Pro", 20): {"bw": 307.0, "pp": 1620.64, "tg": 66.33, "e_chip": 1.1062, "p_index": 0.5033, "source": "measured", "confidence": "medium"},
    ("M5", "Max", 32): {"bw": 460.0, "scaled_from": 40, "e_chip": 1.0000, "p_index": 0.8000, "source": "scaled", "confidence": "low"},
    ("M5", "Max", 40): {"bw": 614.0, "pp": 3219.99, "tg": 119.92, "e_chip": 1.0000, "p_index": 1.0000, "source": "reference", "confidence": "reference"},
    ("M5", "Ultra", 64): {"bw": 1228.8, "scaled_from": 80, "e_chip": 0.7463, "p_index": 1.2285, "source": "scaled", "confidence": "low"},
    ("M5", "Ultra", 80): {"bw": 1228.8, "pp": 4944.66, "tg": 179.10, "e_chip": 0.7463, "p_index": 1.5356, "source": "measured", "confidence": "low", "flag": "memory options inconsistent across sources"},
}
_ADV_CHIP_SOURCE = ("bandwidth: Apple/Wikipedia spec; pp512/tg128: ggml-org/llama.cpp "
                    "discussion #4167 (Llama-2-7B Q4_0), fetched 2026-10-26")
_ADV_FAMILIES = tuple(sorted({k[0] for k in _ADV_CHIPS}, key=lambda f: int(f[1:])))
_ADV_NEWEST_GEN = max(int(f[1:]) for f in _ADV_FAMILIES)


def _advhw_row_label(key):
    fam, var, cores = key
    return "%s%s %dc" % (fam, (" " + var) if var else "", cores)


def _adv_chip_row(family, variant, cores, gen=None):
    """The chip-table row that describes (family, variant, GPU cores). PURE.

    Rules (§3.3):
      * exact row                       -> that row
      * cores unknown (None)            -> the variant's LOWER binned row,
                                           cores_source "assumed" (conservative)
      * cores known, no row             -> nearest row of the variant, p_index
                                           scaled per core, confidence low
      * unknown variant of a known family -> the base row
      * an Apple chip newer than the table (M6+) -> the newest family's row of
                                           the same variant as a FLOOR, low
      * any other / unknown Apple chip  -> M1 8c, the slowest known row, low
    Returns a dict (never None): family, variant, gpu_cores, cores_detected,
    cores_source, bandwidth_gbs, e_chip, p_index, row, row_source, confidence,
    notes[]."""
    notes = []
    fam, var = family, (variant or "")
    rule = "exact"
    if gen is None and isinstance(fam, str) and _advhw_re.match(r"^M\d+$", fam):
        gen = int(fam[1:])
    if fam not in _ADV_FAMILIES:
        if isinstance(gen, int) and gen > _ADV_NEWEST_GEN:
            notes.append("%s is newer than the chip table; estimates use the %s%s "
                         "row as a floor" % (fam, _ADV_FAMILIES[-1], (" " + var) if var else ""))
            fam, rule = _ADV_FAMILIES[-1], "newer-family"
        else:
            notes.append("estimates assume the slowest Apple-silicon chip")
            row = dict(_ADV_CHIPS[("M1", "", 8)])
            return _advhw_row_out(("M1", "", 8), row, cores, "assumed" if cores is None else "detected",
                                  "unknown-chip", "low", notes)
    rows = sorted(c for (f, v, c) in _ADV_CHIPS if f == fam and v == var)
    if not rows:
        if var:
            notes.append("unknown %s variant %r: the base %s row is used" % (fam, var, fam))
        var, rule = "", "unknown-variant" if rule == "exact" else rule
        rows = sorted(c for (f, v, c) in _ADV_CHIPS if f == fam and v == "")
    # a row that only STANDS IN for the real chip is never better than "low"
    force = "low" if rule in ("newer-family", "unknown-variant") else None
    if cores is None:
        key = (fam, var, rows[0])
        notes.append("GPU core count not detected; the lower %s bin is assumed" % _advhw_row_label(key))
        return _advhw_row_out(key, dict(_ADV_CHIPS[key]), None, "assumed",
                              rule if rule != "exact" else "lower-bin", force, notes)
    if (fam, var, cores) in _ADV_CHIPS:
        key = (fam, var, cores)
        return _advhw_row_out(key, dict(_ADV_CHIPS[key]), cores, "detected", rule, force, notes)
    near = min(rows, key=lambda c: (abs(c - cores), c))
    src = _ADV_CHIPS[(fam, var, near)]
    pp = src.get("pp")
    if pp is None:                       # a scaled row scales from its own source
        base = _ADV_CHIPS[(fam, var, src["scaled_from"])]
        p_core = base["p_index"] / src["scaled_from"]
    else:
        p_core = src["p_index"] / near
    row = {"bw": src["bw"], "scaled_from": near, "e_chip": src["e_chip"],
           "p_index": round(p_core * cores, 4), "source": "scaled", "confidence": "low"}
    notes.append("no %d-core row: scaled per core from the %s row" % (cores, _advhw_row_label((fam, var, near))))
    return _advhw_row_out((fam, var, cores), row, cores, "detected",
                          "scaled" if rule == "exact" else rule, "low", notes)


def _advhw_row_out(key, row, cores_detected, cores_source, rule, confidence, notes):
    fam, var, cores = key
    conf = confidence or row.get("confidence") or "medium"
    if row.get("flag"):
        notes = notes + [row["flag"]]
    return {"family": fam, "variant": var, "gpu_cores": cores,
            "cores_detected": cores_detected, "cores_source": cores_source,
            "bandwidth_gbs": row["bw"], "e_chip": row["e_chip"], "p_index": row["p_index"],
            "row": _advhw_row_label(key), "row_source": row.get("source"), "rule": rule,
            "confidence": conf, "reference": key == _ADV_REF_KEY and rule == "exact",
            "notes": notes}


# --------------------------------------------------------------------------
# Advisor facts (§6.1/6.2) — one record per curated model id.
#
# Sizes and architecture were read by hand from Hugging Face's PUBLIC metadata
# on 2026-10-26 (https://huggingface.co/api/models/<id>?blobs=true for the
# file sizes, <id>/raw/main/config.json for the layers): weights_bytes = sum
# of *.safetensors (what is loaded), download_bytes = every file the curated
# download pulls (what the disk spends).  No weights were downloaded.
# The per-token numbers are COMPUTED from the config fields and asserted by
# t_advisor_estimate.py:
#     kv_bytes_per_token = 2 x full_attention_layers x kv_heads x head_dim x 2 B
#     gdn_state_bytes    = linear_layers x linear_v_heads x linear_k_dim x linear_v_dim x 2 B
# Calibration constants (k_decode, ctx slope, prefill) are measured on the
# reference M5 Max 40c where a source says so, and labelled assumptions where
# not.  quality is an ORDINAL JUDGEMENT (0-10), never shown as a number (D14).
# Facts do NOT make a model curated: only server.py's seeds, the onboarding
# catalog and aux_advisor.py's _ADV_EXTRA do.  An id here that nobody curates
# is simply never offered.
# --------------------------------------------------------------------------
_ADVHW_HF = "https://huggingface.co/api/models/%s?blobs=true"

_ADV_FACTS = {
    "mlx-community/Qwen3.8-27B-4bit": {
        "label": "Qwen3.8-27B", "verified_at": "2026-10-26", "role": "primary",
        "arch": "dense", "params_b": 27.0, "active_params_b": 27.0,
        "weights_bytes": 16054541349, "download_bytes": 16081490933,
        "size_gb": 16.1, "draft_size_gb": 0.9,
        "drafter": "mlx-community/Qwen3.8-27B-MTP-bf16",
        "drafter_bytes": 849400335, "drafter_download_bytes": 868956332,
        "full_attention_layers": 16, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 48, "linear_v_heads": 48, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 65536, "gdn_state_bytes": 75497472,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "config has vision_config and the weight index lists 333 vision tensors; "
                       "image input through Hermes is not drilled",
        "mtp": {"kind": "mtp", "block": 3, "measured": True,
                "mult": {"chat": 1.56, "code": 1.92, "agent": 1.74},
                "source": "decode_bench 2026-09-04 (tools/bench/README.md): block 3 prose 47.8, "
                          "code 58.7 tok/s vs 30.6 without the drafter; agent = mlx-server.log "
                          "<2k-token median 53.2 / 30.6. Single-stream only: two streams are a loss"},
        "k_decode": 0.8001, "k_anchor_tps": 30.6,
        "k_source": "30.6 tok/s without the drafter (decode_bench 2026-09-04, block none) x "
                    "16.05 GB / 614 GB/s",
        "k_confidence": "measured",
        "ctx_slope_ms_per_1k": 0.44, "ctx_slope_with_mtp": True,
        "ctx_slope_source": "mlx-server.log 2026-08-18..09-10 medians 53.2 (<2k, n=13) -> "
                            "36.7 (16-24k, n=9) -> 34.1 (24-32k, n=7) tok/s, MTP on",
        "prefill_ref_tps": 750.0,
        "prefill_source": "aux_promptbudget._PB_PREFILL_TOK_S (mlx-server.log cold reads "
                          "735-810 tok/s; median 20,468 new tokens in 26.1 s, n=9)",
        "prefill_confidence": "measured",
        "measured_ref": {"chip": "M5 Max 40c", "ram_gb": 64, "mlx_vlm": "0.6.14",
                         "decode_ar": 30.6, "decode_mtp_prose": 47.8, "decode_mtp_code": 58.7,
                         "decode_short": 53.2, "decode_20k": 36.7, "decode_28k": 34.1,
                         "prefill_tps": 750.0, "cold_prefix_tokens": 20468, "cold_s": 26.1,
                         "n": 67, "dates": "2026-08-18..2026-09-10",
                         "source": "tools/bench/README.md + ~/.hermes/logs/mlx-server.log"},
        "verified": {"drill": "6/6", "where": "reference", "date": "2026-08-14",
                     "source": "CLAUDE.md: drill 6/6 on both backends"},
        "quality": {"chat": 9, "code": 10, "agent": 10, "docs": 9, "vision": 8},
        "quality_note": "largest dense brain in the catalog; drilled 6/6 on tools",
        "uncensored": False, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "mlx-community/Qwen3.8-27B-4bit",
                    _ADVHW_HF % "mlx-community/Qwen3.8-27B-MTP-bf16"],
    },
    "orcarouter/Qwen3.8-27B-Uncensored-MLX": {
        "label": "Qwen3.8-27B Uncensored", "verified_at": "2026-10-26", "role": "alternative",
        "arch": "dense", "params_b": 27.0, "active_params_b": 27.0,
        # root 4-bit mirror only; the curated download skips 2/4/6/8-bit/ subfolders
        "weights_bytes": 16054541599, "download_bytes": 16950466456,
        "size_gb": 17.0, "draft_size_gb": 0.0,
        "drafter": "mtp/ (in this repo)",
        "drafter_bytes": 849400337, "drafter_download_bytes": 868953320,
        "full_attention_layers": 16, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 48, "linear_v_heads": 48, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 65536, "gdn_state_bytes": 75497472,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "same layout as Qwen3.8-27B (333 vision tensors); not drilled",
        "mtp": {"kind": "mtp", "block": 3, "measured": False,
                "mult": {"chat": 1.56, "code": 1.92, "agent": 1.74},
                "source": "assumed equal to Qwen3.8-27B; NOT measured for this build, so the "
                          "estimate uses no speed-up"},
        "k_decode": 0.8001, "k_anchor_tps": 30.6,
        "k_source": "taken from Qwen3.8-27B (identical layout and quantisation); unmeasured here",
        "k_confidence": "medium",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": 750.0,
        "prefill_source": "taken from Qwen3.8-27B (identical layout); unmeasured here",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 9, "code": 9, "agent": 9, "docs": 9, "vision": 8},
        "quality_note": "abliterated 27B; vendor reports near-stock benchmarks; not drilled here",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "orcarouter/Qwen3.8-27B-Uncensored-MLX"],
    },
    # The two abliterated 27B seeds (a-roster, round 3). Same text layout as
    # Qwen3.8-27B, so the speed constants are BORROWED from it, labelled medium,
    # and neither ships a drafter (estimated at autoregressive speed).
    "PocketAiHub/Qwen3.8-27B-Abliterated-MLX": {
        "label": "Qwen3.8-27B Abliterated (PocketAiHub)", "verified_at": "2026-10-26",
        "role": "alternative",
        "arch": "dense", "params_b": 27.0, "active_params_b": 27.0,
        # the 4bit/ subfolder only (model_subfolder); 2/6/8-bit are never pulled
        "weights_bytes": 16054541349, "download_bytes": 16081497073,
        "size_gb": 16.1, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 16, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 48, "linear_v_heads": 48, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 65536, "gdn_state_bytes": 75497472,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "same layout as Qwen3.8-27B (vision_config present); not drilled",
        "mtp": None,
        "k_decode": 0.8001, "k_anchor_tps": 30.6,
        "k_source": "taken from Qwen3.8-27B (identical weight bytes and layout); unmeasured here",
        "k_confidence": "medium",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": 750.0,
        "prefill_source": "taken from Qwen3.8-27B (identical layout); unmeasured here",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 9, "code": 9, "agent": 9, "docs": 9, "vision": 8},
        "quality_note": "abliterated 27B; self-reported 0/100 refusals and 8/8 tool calls; "
                        "not drilled here",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"],
    },
    "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit": {
        "label": "Qwen3.8-27B Abliterated (huihui, text only)", "verified_at": "2026-10-26",
        "role": "alternative",
        "arch": "dense", "params_b": 27.0, "active_params_b": 27.0,
        "weights_bytes": 15133043507, "download_bytes": 15153237017,
        "size_gb": 15.2, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 16, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 48, "linear_v_heads": 48, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 65536, "gdn_state_bytes": 75497472,
        "ctx_native": 262144,
        "vision": False,
        "vision_note": "text-only conversion: no vision_config, no vision tensors",
        "mtp": None,
        # decode never reads the vision tower, so this build's text decode is the
        # stock 27B's 30.6 tok/s over fewer total bytes
        "k_decode": 0.7542, "k_anchor_tps": 30.6,
        "k_source": "Qwen3.8-27B's 30.6 tok/s (same language-model weights; this build only "
                    "drops the vision tower) x 15.13 GB / 614 GB/s; unmeasured here",
        "k_confidence": "medium",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": 750.0,
        "prefill_source": "taken from Qwen3.8-27B (identical text layout); unmeasured here",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 8, "code": 8, "agent": 8, "docs": 8, "vision": 0},
        "quality_note": "huihui abliteration of the 27B, text only (image and computer-use "
                        "screenshot turns fail); not drilled",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit"],
    },
    "mlx-community/Qwen3.5-9B-4bit": {
        "label": "Qwen3.5-9B", "verified_at": "2026-10-26", "role": "background",
        "arch": "dense", "params_b": 9.0, "active_params_b": 9.0,
        "weights_bytes": 5950221072, "download_bytes": 5977073303,
        "size_gb": 6.0, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 8, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 24, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 32768, "gdn_state_bytes": 25165824,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "the weight index lists 333 vision tensors; image input not drilled",
        "mtp": None,
        "k_decode": 0.8528, "k_anchor_tps": 88.0,
        "k_source": "~88 tok/s short (CLAUDE.md; mlx-bg.log <2k median 93.1, n=5) x "
                    "5.95 GB / 614 GB/s",
        "k_confidence": "measured",
        "ctx_slope_ms_per_1k": 0.48, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "mlx-bg.log 2026-08-18..09-01 medians 93.1 (<2k) -> 82.0 (2-8k) -> "
                            "42.0 (24-32k, n=171) -> 32.3 (>=32k, median prompt 41,663, n=552)",
        "prefill_ref_tps": 1970.0,
        "prefill_source": "mlx-bg.log cold reads, median 1,969 tok/s (n=152; 24,248 new tokens "
                          "in 12.3 s)",
        "prefill_confidence": "measured",
        "measured_ref": {"chip": "M5 Max 40c", "ram_gb": 64, "mlx_vlm": "0.6.14",
                         "decode_short": 93.1, "decode_5k": 82.0, "decode_28k": 42.0,
                         "decode_42k": 32.3, "prefill_tps": 1969.0,
                         "cold_prefix_tokens": 24248, "cold_s": 12.3, "n": 2722,
                         "dates": "2026-08-18..2026-09-01",
                         "source": "~/.hermes/logs/mlx-bg.log (contention with the primary "
                                   "lane included)"},
        "verified": {"drill": "6/6", "where": "reference", "date": "2026-08-18",
                     "source": "CLAUDE.md: 9B tool suite 6/6"},
        "quality": {"chat": 7, "code": 6, "agent": 7, "docs": 6, "vision": 6},
        "quality_note": "the background lane; drilled 6/6 on tools",
        "uncensored": False, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "mlx-community/Qwen3.5-9B-4bit"],
    },
    "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit": {
        "label": "Qwen3.5-9B Abliterated (huihui)", "verified_at": "2026-10-26",
        "role": "background",
        "arch": "dense", "params_b": 9.0, "active_params_b": 9.0,
        "weights_bytes": 5950221072, "download_bytes": 5977070704,
        "size_gb": 6.0, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 8, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 24, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 32768, "gdn_state_bytes": 25165824,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "vision_config present (same layout as Qwen3.5-9B); not drilled",
        "mtp": None,
        "k_decode": 0.8528, "k_anchor_tps": 88.0,
        "k_source": "taken from Qwen3.5-9B (identical weight bytes and layout); unmeasured here",
        "k_confidence": "medium",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": 1970.0,
        "prefill_source": "taken from Qwen3.5-9B (identical layout); unmeasured here",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 6, "code": 5, "agent": 6, "docs": 5, "vision": 5},
        "quality_note": "huihui abliteration of the 9B; anonymous converter, no stated licence, "
                        "no evals; not drilled",
        "uncensored": True, "license": None,
        "sources": [_ADVHW_HF % "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"],
    },
    # 2026-11-12 (owner request): same qwen3_5 9B layout as the two above,
    # bytes from the shard index at @6471ad9 (t_r3_roster_hfmeta.json)
    "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit": {
        "label": "DeepSeek-V4-Pro Qwen3.5-9B Heretic", "verified_at": "2026-11-12",
        "role": "background",
        "arch": "dense", "params_b": 9.0, "active_params_b": 9.0,
        "weights_bytes": 5950219560, "download_bytes": 5975594679,
        "size_gb": 6.0, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 8, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 24, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 32768, "gdn_state_bytes": 25165824,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "vision_config and 333 vision tensors kept (same layout as Qwen3.5-9B); "
                       "not drilled",
        "mtp": None,
        "k_decode": 0.8528, "k_anchor_tps": 88.0,
        "k_source": "taken from Qwen3.5-9B (identical weight layout); unmeasured here",
        "k_confidence": "medium",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": 1970.0,
        "prefill_source": "taken from Qwen3.5-9B (identical layout); unmeasured here",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 6, "code": 6, "agent": 6, "docs": 5, "vision": 5},
        "quality_note": "DeepSeek-V4-Pro math/STEM reasoning distill of the 9B, Heretic "
                        "abliteration; third-party converter, evals only on the source's card; "
                        "not drilled",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"],
    },
    "mlx-community/Qwen3.5-4B-4bit": {
        "label": "Qwen3.5-4B", "verified_at": "2026-10-26", "role": "small",
        "arch": "dense", "params_b": 4.0, "active_params_b": 4.0,
        "weights_bytes": 3034300695, "download_bytes": 3061130647,
        "size_gb": 3.1, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 8, "kv_heads": 4, "head_dim": 256,
        "linear_layers": 24, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 32768, "gdn_state_bytes": 25165824,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "the weight index lists 297 vision tensors; image input not drilled",
        "mtp": None,
        "k_decode": _ADV_K_SMALL, "k_anchor_tps": None,
        "k_source": "ASSUMPTION for dense <=4B models (fixed per-token overhead dominates); "
                    "calibrate on AC (tools/tests/ac/advisor-calibration.md)",
        "k_confidence": "low",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": None,
        "prefill_source": "unmeasured: 19,000 / 4B params",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 5, "code": 4, "agent": 4, "docs": 4, "vision": 4},
        "quality_note": "small brain; everyday tools only; not drilled",
        "uncensored": False, "license": "apache-2.0",
        "license_note": "the base model's licence; this repo's card carries no licence field",
        "sources": [_ADVHW_HF % "mlx-community/Qwen3.5-4B-4bit"],
    },
    "mlx-community/Qwen3.5-2B-4bit": {
        "label": "Qwen3.5-2B", "verified_at": "2026-10-26", "role": "tiny",
        "arch": "dense", "params_b": 2.0, "active_params_b": 2.0,
        "weights_bytes": 1722271785, "download_bytes": 1749081927,
        "size_gb": 1.7, "draft_size_gb": 0.0,
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 6, "kv_heads": 2, "head_dim": 256,
        "linear_layers": 18, "linear_v_heads": 16, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 12288, "gdn_state_bytes": 9437184,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "the weight index lists 297 vision tensors; image input not drilled",
        "mtp": None,
        "k_decode": _ADV_K_SMALL, "k_anchor_tps": None,
        "k_source": "ASSUMPTION for dense <=4B models (fixed per-token overhead dominates); "
                    "calibrate on AC (tools/tests/ac/advisor-calibration.md)",
        "k_confidence": "low",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": None,
        "prefill_source": "unmeasured: 19,000 / 2B params",
        "prefill_confidence": "medium",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 3, "code": 2, "agent": 2, "docs": 2, "vision": 2},
        "quality_note": "smallest brain; chat and light tools; not drilled",
        "uncensored": False, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "mlx-community/Qwen3.5-2B-4bit"],
    },
    "mlx-community/Qwen3.6-35B-A3B-4bit": {
        "label": "Qwen3.6-35B-A3B", "verified_at": "2026-10-26", "role": "alternative",
        "arch": "moe", "params_b": 35.0, "active_params_b": 3.0,
        "weights_bytes": 20402204271, "download_bytes": 20429169263,
        "size_gb": 20.4, "draft_size_gb": 0.0,
        "active_bytes_per_token": 1650000000,
        "active_formula": "experts 9 x 3 x 2048 x 512 x 40 = 1.13B + full attention 0.27B + "
                          "GatedDeltaNet projections 1.0B + lm_head 0.51B = 2.93B params x "
                          "4.5 bits = 1.65 GB per token (Qwen/Qwen3.6-35B-A3B config.json)",
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "drafter_available": {"id": "mlx-community/Qwen3.6-35B-A3B-MTP-bf16",
                              "bytes": 1689283752, "download_bytes": 1708837164,
                              "enabled": False,
                              "note": "an MTP drafter exists but stays off until it is measured "
                                      "on mlx-vlm 0.6.14"},
        "full_attention_layers": 10, "kv_heads": 2, "head_dim": 256,
        "linear_layers": 30, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 20480, "gdn_state_bytes": 31457280,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "image-text-to-text; 333 vision tensors in the weight index",
        "mtp": None,
        "k_decode": _ADV_K_MOE, "k_anchor_tps": None,
        "k_source": "community M5 Max 30-35B-A3B MLX reports, 110-126 tok/s, on active bytes",
        "k_confidence": "low",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": None,
        "prefill_source": "unmeasured: 19,000 / 3B active x 0.30 MoE efficiency",
        "prefill_confidence": "low",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 8, "code": 8, "agent": 8, "docs": 8, "vision": 7},
        "quality_note": "fast MoE, older generation (Apr 2026); a guess until drilled",
        "uncensored": False, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "mlx-community/Qwen3.6-35B-A3B-4bit",
                    _ADVHW_HF % "Qwen/Qwen3.6-35B-A3B",
                    _ADVHW_HF % "mlx-community/Qwen3.6-35B-A3B-MTP-bf16"],
    },
    "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit": {
        "label": "Qwen3.6-35B-A3B Heretic", "verified_at": "2026-10-26", "role": "alternative",
        "arch": "moe", "params_b": 35.0, "active_params_b": 3.0,
        "weights_bytes": 24604592676, "download_bytes": 24624965341,
        "size_gb": 24.6, "draft_size_gb": 0.0,
        "active_bytes_per_token": 1940000000,
        "active_formula": "same 2.93B active params as the stock build, with this build's "
                          "per-layer 4/6-bit mix (6-bit lm_head, 6-bit experts on about half "
                          "the layers): ~1.94 GB per token",
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 10, "kv_heads": 2, "head_dim": 256,
        "linear_layers": 30, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 20480, "gdn_state_bytes": 31457280,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "vision weights in a separate model-vision file (0.89 GB, included)",
        "mtp": None,
        "k_decode": _ADV_K_MOE, "k_anchor_tps": None,
        "k_source": "MoE assumption shared with the stock build",
        "k_confidence": "low",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": None,
        "prefill_source": "unmeasured: 19,000 / 3B active x 0.30 MoE efficiency",
        "prefill_confidence": "low",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 7, "code": 7, "agent": 7, "docs": 7, "vision": 7},
        "quality_note": "abliterated MoE (Heretic, KL 0.0107 self-reported); not drilled",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit"],
    },
    "froggeric/Qwen3.6-35B-A3B-Uncensored-Heretic-MLX-4bit": {
        "label": "Qwen3.6-35B-A3B Heretic (froggeric)", "verified_at": "2026-10-26",
        "role": "alternative",
        "arch": "moe", "params_b": 35.0, "active_params_b": 3.0,
        "weights_bytes": 20402204301, "download_bytes": 20422478158,
        "size_gb": 20.4, "draft_size_gb": 0.0,
        "active_bytes_per_token": 1650000000,
        "active_formula": "same layout and quantisation as the stock mlx-community build: "
                          "1.65 GB per token",
        "drafter": None, "drafter_bytes": 0, "drafter_download_bytes": 0,
        "full_attention_layers": 10, "kv_heads": 2, "head_dim": 256,
        "linear_layers": 30, "linear_v_heads": 32, "linear_k_dim": 128, "linear_v_dim": 128,
        "kv_bytes_per_token": 20480, "gdn_state_bytes": 31457280,
        "ctx_native": 262144,
        "vision": True,
        "vision_note": "image-text-to-text; 333 vision tensors in the weight index",
        "mtp": None,
        "k_decode": _ADV_K_MOE, "k_anchor_tps": None,
        "k_source": "MoE assumption shared with the stock build",
        "k_confidence": "low",
        "ctx_slope_ms_per_1k": None, "ctx_slope_with_mtp": False,
        "ctx_slope_source": "unmeasured: formula 0.48 x (kv/32 KiB)",
        "prefill_ref_tps": None,
        "prefill_source": "unmeasured: 19,000 / 3B active x 0.30 MoE efficiency",
        "prefill_confidence": "low",
        "measured_ref": None,
        "verified": None,
        "quality": {"chat": 7, "code": 7, "agent": 7, "docs": 7, "vision": 7},
        "quality_note": "abliterated MoE (Heretic); same layout as the stock build; not drilled",
        "uncensored": True, "license": "apache-2.0",
        "sources": [_ADVHW_HF % "froggeric/Qwen3.6-35B-A3B-Uncensored-Heretic-MLX-4bit"],
    },
}


def _adv_kv_bytes(f):
    """KV cache bytes per token: 2 (K and V) x full-attention layers x KV heads
    x head_dim x 2 B (bf16).  Only full-attention layers keep a per-token KV."""
    return 2 * int(f["full_attention_layers"]) * int(f["kv_heads"]) * int(f["head_dim"]) * 2


def _adv_gdn_bytes(f):
    """GatedDeltaNet recurrent state per SEQUENCE (fixed, not per token)."""
    return (int(f["linear_layers"]) * int(f["linear_v_heads"])
            * int(f["linear_k_dim"]) * int(f["linear_v_dim"]) * 2)


# --------------------------------------------------------------------------
# pure parsers (§3.1)
# --------------------------------------------------------------------------
_ADVHW_BRAND_RE = _advhw_re.compile(r"^Apple (M(\d+))(?: (Pro|Max|Ultra))?$")
_ADVHW_CTRL_RE = _advhw_re.compile(r"[\x00-\x1f\x7f]+")


def _advhw_clean(s, limit=120):
    """A short single-line string from an OS command (UI-bound; esc() is C3's)."""
    if not isinstance(s, str):
        return ""
    return _ADVHW_CTRL_RE.sub(" ", s).strip()[:limit]


def _advhw_text(x):
    """A text parser's input: the string, or "" for anything else (None, a number,
    a list from a stub).  The parsers are PURE and total — they never raise."""
    return x if isinstance(x, str) else ""


def _adv_parse_brand(s):
    """`sysctl -n machdep.cpu.brand_string` -> {brand, vendor, family, gen,
    variant, supported, reason}.  PURE.

    "Apple M5 Max" -> family "M5", gen 5, variant "Max".  Anything mentioning
    Intel -> supported False ("local models need Apple silicon").  Another
    "Apple ..." string (an A-series chip, an unparsed name) -> family None,
    supported True: the estimator falls back to the slowest known row.  "Apple
    M6 Max" parses (gen 6) and the chip table treats it as a floor.  Empty or
    garbage -> vendor None, supported None (unknown; the caller may decide from
    hw.optional.arm64)."""
    brand = _advhw_clean(s)
    out = {"brand": brand, "vendor": None, "family": None, "gen": None,
           "variant": "", "supported": None, "reason": ""}
    if not brand:
        return out
    if "intel" in brand.lower():
        out.update(vendor="intel", supported=False,
                   reason="Local models need Apple silicon; this Mac has an Intel chip.")
        return out
    m = _ADVHW_BRAND_RE.match(brand)
    if m:
        out.update(vendor="apple", family=m.group(1), gen=int(m.group(2)),
                   variant=m.group(3) or "", supported=True)
        return out
    if brand.startswith("Apple"):
        out.update(vendor="apple", supported=True)
    return out


_ADVHW_ASERIES_RE = _advhw_re.compile(r"^Apple A\d+")
_ADVHW_IOREG_RE = _advhw_re.compile(r'"gpu-core-count"\s*=\s*(\d+)')


def _adv_parse_ioreg_cores(text):
    """`ioreg -rc AGXAccelerator -d 1` -> GPU core count, or None. PURE."""
    m = _ADVHW_IOREG_RE.search(_advhw_text(text))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 1024 else None


def _advhw_json_obj(x):
    if isinstance(x, dict):
        return x
    try:
        v = _advhw_json.loads(x or "")
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def _adv_parse_sp_displays(x):
    """`system_profiler SPDisplaysDataType -json` -> GPU cores, or None. PURE.
    `sppci_cores` is a STRING ("40") there; the Apple GPU entry is preferred."""
    d = _advhw_json_obj(x)
    if not d:
        return None
    items = d.get("SPDisplaysDataType")
    if not isinstance(items, list):
        return None
    ranked = sorted((g for g in items if isinstance(g, dict)),
                    key=lambda g: 0 if "Apple" in str(g.get("sppci_model", "")) else 1)
    for g in ranked:
        raw = g.get("sppci_cores")
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 1024:
            return n
    return None


def _adv_parse_sp_hardware(x):
    """`system_profiler SPHardwareDataType -json` -> {machine_name,
    machine_model, chip_type, number_processors, physical_memory}. PURE.

    A WHITELIST: the same JSON carries serial_number, platform_UUID and
    provisioning_UDID, which must never leave this function."""
    d = _advhw_json_obj(x)
    out = {"machine_name": "", "machine_model": "", "chip_type": "",
           "number_processors": "", "physical_memory": ""}
    items = (d or {}).get("SPHardwareDataType")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return out
    r = items[0]
    for k in out:
        out[k] = _advhw_clean(r.get(k) if isinstance(r.get(k), str) else "", 80)
    return out


_ADVHW_PERF_RE = _advhw_re.compile(r"^hw\.perflevel(\d+)\.(name|logicalcpu|physicalcpu):\s*(.+)$")


def _adv_parse_perflevels(x):
    """`sysctl hw.perflevel0 hw.perflevel1 ...` text, or a {"hw.perflevelN.k": v}
    map -> [{level, name, logicalcpu}] in level order. PURE.

    Names are reported verbatim: on M5 Pro/Max perflevel1 is "Performance",
    not efficiency cores, so nothing here ever assumes perflevel1 = E-cores."""
    pairs = []
    if isinstance(x, dict):
        pairs = [(str(k), str(v)) for k, v in x.items()]
    else:
        for line in _advhw_text(x).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                pairs.append((k.strip(), v.strip()))
    levels = {}
    for k, v in pairs:
        m = _ADVHW_PERF_RE.match("%s: %s" % (k, v))
        if not m:
            continue
        lv = levels.setdefault(int(m.group(1)), {"level": int(m.group(1)), "name": "", "logicalcpu": None})
        if m.group(2) == "name":
            lv["name"] = _advhw_clean(m.group(3), 40)
        elif m.group(2) == "logicalcpu":
            try:
                lv["logicalcpu"] = int(m.group(3).strip())
            except ValueError:
                pass
    return [levels[i] for i in sorted(levels)]


def _adv_form(machine_name, has_battery):
    """laptop_fanless | laptop | desktop | unknown. PURE.
    "MacBook Air" has no fan; any other MacBook, or a Mac with an internal
    battery, is a laptop; no battery reported is a desktop."""
    name = _advhw_text(machine_name)
    if "MacBook Air" in name:
        return "laptop_fanless"
    if "MacBook" in name or has_battery is True:
        return "laptop"
    if has_battery is False or name:
        return "desktop"
    return "unknown"


_ADVHW_PCT_RE = _advhw_re.compile(r"(\d{1,3})%")


def _adv_parse_batt(text):
    """`pmset -g batt` -> {source: ac|battery|unknown, pct, has_battery, state}. PURE."""
    t = _advhw_text(text)
    if "'AC Power'" in t:
        src = "ac"
    elif "'Battery Power'" in t:
        src = "battery"
    else:
        src = "unknown"
    has = "InternalBattery" in t if t else None
    pct = state = None
    if has:
        line = next((ln for ln in t.splitlines() if "InternalBattery" in ln), "")
        m = _ADVHW_PCT_RE.search(line)
        if m:
            pct = min(100, int(m.group(1)))
        for s in ("discharging", "charging", "charged", "finishing charge", "AC attached"):
            if s in line:
                state = s
                break
    return {"source": src, "pct": pct, "has_battery": has, "state": state}


def _adv_parse_pmset(text):
    """`pmset -g` -> {low_power: True|False|None, low_power_source}. PURE.

    `lowpowermode 1` is Low Power Mode.  Newer macOS prints `powermode N`
    instead; 1 is BELIEVED to mean low power (0 automatic, 2 high) — that
    mapping is UNVERIFIED, and the source string says so.  Absent -> None."""
    t = _advhw_text(text)
    m = _advhw_re.search(r"^\s*lowpowermode\s+(\d+)", t, _advhw_re.M)
    if m:
        return {"low_power": m.group(1) == "1", "low_power_source": "lowpowermode"}
    m = _advhw_re.search(r"^\s*powermode\s+(\d+)", t, _advhw_re.M)
    if m:
        return {"low_power": m.group(1) == "1",
                "low_power_source": "powermode %s (mapping unverified)" % m.group(1)}
    return {"low_power": None, "low_power_source": "absent"}


def _adv_parse_therm(text):
    """`pmset -g therm` -> {limited: bool|None, cpu_speed_limit, text}. PURE.
    "No thermal warning level has been recorded" (and no limit) -> False; a
    CPU_Speed_Limit below 100 -> True; anything else unreadable -> None."""
    t = _advhw_text(text)
    first = _advhw_clean(" ".join(ln.strip() for ln in t.splitlines() if ln.strip()), 160)
    m = _advhw_re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", t)
    if m:
        lim = int(m.group(1))
        return {"limited": lim < 100, "cpu_speed_limit": lim, "text": first}
    if "No thermal warning level has been recorded" in t:
        return {"limited": False, "cpu_speed_limit": None, "text": first}
    if _advhw_re.search(r"thermal warning level", t, _advhw_re.I):
        return {"limited": True, "cpu_speed_limit": None, "text": first}
    return {"limited": None, "cpu_speed_limit": None, "text": first}


def _advhw_parse_boottime(text):
    m = _advhw_re.search(r"sec\s*=\s*(\d+)", _advhw_text(text))
    return int(m.group(1)) if m else None


def _advhw_int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _advhw_parse_swap(text):
    """`sysctl -n vm.swapusage` -> used GiB, or None."""
    m = _advhw_re.search(r"used\s*=\s*([\d.]+)([MG])", _advhw_text(text))
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:                      # "1.2.3"
        return None
    return round(v / 1024.0 if m.group(2) == "M" else v, 2)


# --------------------------------------------------------------------------
# GPU budget, plan limit, cache tiers, verdict (§3.4, §3.5, §5.2) — PURE
# --------------------------------------------------------------------------
def _adv_gpu_budget(ram_gb, wired_mb, metal_bytes):
    """The GPU working-set budget in GiB, in precedence order (§3.4):
      1. iogpu.wired_limit_mb > 0  (an owner's `sudo sysctl`, resets at reboot)
      2. Metal recommendedMaxWorkingSetSize > 0 (the probe; what MLX reports)
      3. assumed: 0.75 x RAM at >= 36 GB, 0.66 x RAM below — Apple does not
         document the fraction, and the one measured point is 0.81 at 64 GB,
         so the fallback is conservative.
    Returns {gb, source, fraction, note}."""
    ram = float(ram_gb or 0)
    try:
        wired = float(wired_mb or 0)
    except (TypeError, ValueError):
        wired = 0.0
    try:
        mb = float(metal_bytes or 0)
    except (TypeError, ValueError):
        mb = 0.0
    if wired > 0:
        gb, src = wired / 1024.0, "iogpu.wired_limit_mb"
        note = "raised by sudo sysctl iogpu.wired_limit_mb; resets at reboot"
    elif mb > 0 and (ram <= 0 or mb / _ADV_GIB <= ram):
        gb, src = mb / _ADV_GIB, "metal"
        note = "Metal recommendedMaxWorkingSetSize, read on this Mac"
    else:
        frac = 0.75 if ram >= 36 else 0.66
        gb, src = ram * frac, "assumed"
        note = ("assumed %.0f%% of RAM: Apple does not document the fraction "
                "(measured 81%% on a 64 GB M5 Max)" % (frac * 100))
    return {"gb": round(gb, 2), "source": src,
            "fraction": round(gb / ram, 3) if ram > 0 else None, "note": note}


def _advhw_reserve_gb(ram_gb):
    """macOS + this app + a browser (a labelled judgement, §3.5)."""
    ram = float(ram_gb or 0)
    return 6.0 if ram >= 24 else 4.5 if ram >= 16 else 3.5


def _adv_limits(hw=None, env=None):
    """What the lanes TOGETHER may use (§3.5):

        limit = min(gpu_budget, soft - 2, ram - reserve)

    soft comes from server.py's REAL _mem_ceilings (resolved by name), so the
    MLX_SOFT_GB / MLX_HARD_GB env overrides flow through; `env` is passed to it
    verbatim (None = os.environ, the dashboard's own).  `binding` names the
    term that wins.  PURE when `hw` is given."""
    hw = hw if hw is not None else _adv_hw()
    ram = float(hw.get("ram_gb") or 0)
    gpu = (hw.get("gpu_budget") or {})
    gpu_gb = gpu.get("gb")
    soft = hard = None
    mc = _advhw_g("_mem_ceilings")
    if callable(mc) and ram > 0:
        try:
            soft, hard = mc(ram, env)
        except Exception:
            soft = hard = None
    reserve = _advhw_reserve_gb(ram)
    # order = tie-break: Hermes's own ceiling is named first when two terms
    # agree (a 64 GB Mac with an ASSUMED 48 GB GPU budget is capped by policy)
    terms = []
    if isinstance(soft, (int, float)):
        terms.append(("soft", float(soft) - _ADV_SOFT_MARGIN_GB))
    if isinstance(gpu_gb, (int, float)) and gpu_gb > 0:
        terms.append(("gpu", float(gpu_gb)))
    if ram > 0:
        terms.append(("reserve", ram - reserve))
    if not terms:
        return {"ram_gb": ram or None, "gpu_budget_gb": gpu_gb,
                "gpu_budget_source": gpu.get("source"), "soft_gb": soft, "hard_gb": hard,
                "reserve_gb": reserve, "limit_gb": None, "binding": None, "terms": {},
                "note": "memory size unknown", "formula": "", "estimate": True}
    binding, limit = min(terms, key=lambda t: t[1])
    if binding == "soft":
        if ram >= 64 and soft == 50.0:
            note = ("Hermes caps model memory at 50 GB on Macs with 64 GB or more "
                    "(MLX_SOFT_GB); plans stay 2 GB under it")
        else:
            note = ("Hermes's admission ceiling is %g GB here (MLX_SOFT_GB); plans stay "
                    "2 GB under it for one turn's cache growth" % soft)
    elif binding == "gpu":
        note = "macOS lets the GPU use about %.1f GB here (%s)" % (gpu_gb, gpu.get("source"))
    else:
        note = "%.1f GB is kept for macOS, this app and a browser" % reserve
    formula = "min(GPU %s, soft %s - 2, RAM %g - reserve %g) = %.1f GB (%s binds)" % (
        ("%.1f" % gpu_gb) if isinstance(gpu_gb, (int, float)) else "?",
        ("%g" % soft) if isinstance(soft, (int, float)) else "?",
        ram, reserve, limit, binding)
    return {"ram_gb": ram, "gpu_budget_gb": gpu_gb, "gpu_budget_source": gpu.get("source"),
            "soft_gb": soft, "hard_gb": hard, "reserve_gb": reserve,
            "limit_gb": round(limit, 2), "binding": binding,
            "terms": {k: round(v, 2) for k, v in terms}, "note": note,
            "formula": formula, "estimate": True}


def _adv_apc_entries(ram_gb, lane="primary"):
    """APC exact-prefix-cache entries the lane's launcher configures — a MIRROR
    of mlx-server.sh (primary 6/4/3/2) and mlx-server-bg.sh (bg 4/3/2/2) at
    RAM >= 64/48/36/else.  The shell divides hw.memsize by 2**30 with integer
    division, hence int().  t_advisor_hw.py parses both scripts (drift guard)."""
    r = int(float(ram_gb or 0))
    if lane == "bg":
        return 4 if r >= 64 else 3 if r >= 48 else 2
    return 6 if r >= 64 else 4 if r >= 48 else 3 if r >= 36 else 2


def _adv_verdict(total_gb, limit_gb):
    """PLAN verdict (§5.2): fits <= 0.85 x limit, tight <= limit, else no."""
    if not isinstance(total_gb, (int, float)) or not isinstance(limit_gb, (int, float)) \
            or limit_gb <= 0:
        return None
    if total_gb <= _ADV_FITS_FRAC * limit_gb:
        return "fits"
    if total_gb <= limit_gb:
        return "tight"
    return "no"


def _adv_download_gb(mid):
    """Decimal GB the curated download of `mid` pulls (main repo + a separate
    drafter repo), from the facts.  None when the id has no facts."""
    f = _ADV_FACTS.get(mid)
    if not f:
        return None
    return round(float(f.get("size_gb") or 0) + float(f.get("draft_size_gb") or 0), 1)


def _adv_disk_ok(need_gb, free_gb, headroom_gb=None):
    """free - need >= headroom (server.py's _disk_short rule).  FAILS OPEN —
    True when either number is unknown, exactly like the model menu."""
    if not isinstance(need_gb, (int, float)) or not isinstance(free_gb, (int, float)):
        return True
    h = headroom_gb
    if h is None:
        h = _advhw_g("DISK_HEADROOM_GB", _ADV_DISK_HEADROOM_GB)
    return free_gb - need_gb >= float(h)


def _adv_battery_label(bytes_per_token_gb, with_bg=False):
    """Ordinal battery cost from memory traffic per generated token (§5.6) — a
    proxy, not watts.  An always-on background lane adds one step."""
    if not isinstance(bytes_per_token_gb, (int, float)):
        return None
    idx = len(_ADV_BATTERY_STEPS)
    for i, (cap, _label) in enumerate(_ADV_BATTERY_STEPS):
        if bytes_per_token_gb <= cap:
            idx = i
            break
    if with_bg:
        idx = min(idx + 1, len(_ADV_BATTERY_ORDER) - 1)
    return _ADV_BATTERY_ORDER[idx]


# --------------------------------------------------------------------------
# detection runners (§3.1, §3.2, §3.4)
# --------------------------------------------------------------------------
# Every command this module may run.  t_advisor_hw.py fails on anything else.
_ADV_COMMANDS = ("/usr/sbin/sysctl", "/usr/sbin/ioreg", "/usr/bin/pmset",
                 "/usr/sbin/system_profiler", "/usr/bin/sw_vers")

# The Metal probe: stdlib ctypes only, one JSON line out.  It is the probe that
# measured 55,662,788,608 B (51.84 GiB) on this Mac in 0.1 s.  Run in a CHILD
# (`python -I -c`, empty env but PATH) so a ctypes misuse can never crash the
# dashboard, the dashboard never owns a Metal device, and no MLX venv is needed.
_ADV_PROBE_SRC = r'''
import ctypes, json
out = {"ok": False}
try:
    objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    metal = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Metal.framework/Metal")
    metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
    metal.MTLCreateSystemDefaultDevice.argtypes = []
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    dev = metal.MTLCreateSystemDefaultDevice()
    if dev:
        P = ctypes.c_void_p
        def send(rt):
            return ctypes.CFUNCTYPE(rt, P, P)(("objc_msgSend", objc))
        sel = objc.sel_registerName
        out["recommendedMaxWorkingSetSize"] = int(send(ctypes.c_uint64)(dev, sel(b"recommendedMaxWorkingSetSize")))
        out["maxBufferLength"] = int(send(ctypes.c_uint64)(dev, sel(b"maxBufferLength")))
        out["hasUnifiedMemory"] = bool(send(ctypes.c_bool)(dev, sel(b"hasUnifiedMemory")))
        ns = send(P)(dev, sel(b"name"))
        s = send(ctypes.c_char_p)(ns, sel(b"UTF8String")) if ns else None
        out["name"] = s.decode("utf-8", "replace")[:80] if s else ""
        out["ok"] = True
    else:
        out["error"] = "no Metal device"
except Exception as e:
    out["error"] = type(e).__name__
print(json.dumps(out))
'''


def _adv_run(argv, timeout=_ADV_RUN_TIMEOUT):
    """One short command -> stripped stdout, or "" for anything that goes
    wrong (missing binary, non-zero exit, timeout).  NEVER raises.  Calls the
    module-level name `subprocess` (a harness stubs it)."""
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        if getattr(r, "returncode", 1) == 0:
            return (getattr(r, "stdout", "") or "").strip()
    except Exception:
        pass
    return ""


def _advhw_sysctl(name):
    return _adv_run(["/usr/sbin/sysctl", "-n", name])


def _adv_metal_probe(timeout=_ADV_PROBE_TIMEOUT):
    """{ok, bytes, max_buffer, unified, name, ms} from the Metal probe child,
    or {ok: False, error}.  NEVER raises; the caller falls back to "assumed"."""
    exe = _advhw_sys.executable or "/usr/bin/python3"
    t0 = _advhw_time.time()
    try:
        r = subprocess.run([exe, "-I", "-c", _ADV_PROBE_SRC], capture_output=True,
                           text=True, timeout=timeout, env={"PATH": "/usr/bin:/bin"})
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}
    ms = int((_advhw_time.time() - t0) * 1000)
    rc = getattr(r, "returncode", 1)
    if rc != 0:
        return {"ok": False, "error": "exit %s" % rc, "ms": ms}
    lines = [ln for ln in (getattr(r, "stdout", "") or "").splitlines() if ln.strip()]
    try:
        d = _advhw_json.loads(lines[-1]) if lines else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or d.get("ok") is not True:
        err = d.get("error") if isinstance(d, dict) else "unparsable output"
        return {"ok": False, "error": _advhw_clean(err, 80) or "probe failed", "ms": ms}
    b = d.get("recommendedMaxWorkingSetSize")
    if not isinstance(b, int) or isinstance(b, bool) or b <= 0:
        return {"ok": False, "error": "no working-set size", "ms": ms}
    mbl = d.get("maxBufferLength")
    return {"ok": True, "bytes": b,
            "max_buffer": mbl if isinstance(mbl, int) and not isinstance(mbl, bool) else None,
            "unified": d.get("hasUnifiedMemory") if isinstance(d.get("hasUnifiedMemory"), bool) else None,
            "name": _advhw_clean(d.get("name"), 80), "ms": ms}


def _advhw_ram_gb():
    fn = _advhw_g("_machine_ram_gb")
    if callable(fn):
        try:
            v = float(fn())
            if v > 0:
                return v, "hw.memsize"
        except Exception:
            pass
    b = _advhw_int(_advhw_sysctl("hw.memsize"))
    if b and b > 0:
        return b / _ADV_GIB, "hw.memsize"
    return None, "unknown"


def _advhw_disk_free_gb():
    fn = _advhw_g("_disk_free_gb")
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    try:
        st = _advhw_os.statvfs(_advhw_g("HOME") or _advhw_os.path.expanduser("~"))
        return round(st.f_bavail * st.f_frsize / _ADV_GIB, 1)
    except Exception:
        return None


def _advhw_detect_cores(raw):
    n_ioreg = _adv_parse_ioreg_cores(_adv_run(["/usr/sbin/ioreg", "-rc", "AGXAccelerator", "-d", "1"]))
    raw["ioreg"] = ('"gpu-core-count" = %d' % n_ioreg) if n_ioreg else ""
    raw["sp_displays"] = ""
    if n_ioreg is None:
        n_sp = _adv_parse_sp_displays(_adv_run(
            ["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"], timeout=5))
        raw["sp_displays"] = {"SPDisplaysDataType": [{"sppci_cores": str(n_sp)}]} if n_sp else ""


def _advhw_detect_sp_hardware():
    return _adv_parse_sp_hardware(
        _adv_run(["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"], timeout=5))


def _advhw_detect_perflevels():
    n = _advhw_int(_advhw_sysctl("hw.nperflevels")) or 0
    return (_adv_run(["/usr/sbin/sysctl"] + ["hw.perflevel%d" % i for i in range(min(n, 8))])
            if n > 0 else "")


def _advhw_static_missing(raw):
    """E94: the static facts a timed-out or failed command left blank. They
    are not facts about the Mac (the dashboard starts at login, when the Mac
    is busiest), so — like a failed Metal probe — they are re-detected on bust
    and at most every _ADV_PROBE_RETRY_S, never cached as final."""
    raw = raw or {}
    out = []
    brand = raw.get("brand")
    if not brand:
        out.append("brand")
    if raw.get("arm64") is None:
        out.append("arm64")
    apple = _adv_parse_brand(brand)["supported"] is not False
    if apple and _adv_parse_ioreg_cores(raw.get("ioreg")) is None \
            and _adv_parse_sp_displays(raw.get("sp_displays")) is None:
        out.append("cores")              # an Intel Mac has none to find
    sp = raw.get("sp_hardware") if isinstance(raw.get("sp_hardware"), dict) else {}
    if not sp.get("machine_name"):
        out.append("sp_hardware")
    if not raw.get("hw_model"):
        out.append("hw_model")
    if apple and not raw.get("perflevels"):
        out.append("perflevels")
    if raw.get("ram_gb") is None:
        out.append("ram")
    if not raw.get("macos"):
        out.append("macos")
    return out


def _advhw_fill_missing(raw, missing):
    """Re-run only the commands behind `missing`, in place. A piece that
    still fails stays blank (and is retried next time)."""
    if "brand" in missing:
        raw["brand"] = _advhw_sysctl("machdep.cpu.brand_string")
    if "arm64" in missing:
        raw["arm64"] = _advhw_int(_advhw_sysctl("hw.optional.arm64"))
    # a brand that just arrived may say Intel: then there are no cores to find
    if "cores" in missing and _adv_parse_brand(raw.get("brand"))["supported"] is not False:
        _advhw_detect_cores(raw)
    if "sp_hardware" in missing:
        raw["sp_hardware"] = _advhw_detect_sp_hardware()
    if "hw_model" in missing:
        raw["hw_model"] = _advhw_sysctl("hw.model")
    if "perflevels" in missing:
        raw["perflevels"] = _advhw_detect_perflevels()
    if "ram" in missing:
        raw["ram_gb"], raw["ram_source"] = _advhw_ram_gb()
    if "macos" in missing:
        raw["macos"] = _adv_run(["/usr/bin/sw_vers", "-productVersion"])


def _advhw_detect_static():
    """Facts that cannot change while the process lives (except the wired
    limit, which is part of the cache key).  ~0.3 s cold, measured."""
    raw = {}
    raw["brand"] = _advhw_sysctl("machdep.cpu.brand_string")
    raw["arm64"] = _advhw_int(_advhw_sysctl("hw.optional.arm64"))
    raw["rosetta"] = _advhw_int(_advhw_sysctl("sysctl.proc_translated"))
    # keep only what is parsed: the raw ioreg / system_profiler dumps are tens
    # of KB and the static cache lives for the whole process
    _advhw_detect_cores(raw)
    raw["hw_model"] = _advhw_sysctl("hw.model")
    raw["sp_hardware"] = _advhw_detect_sp_hardware()
    raw["perflevels"] = _advhw_detect_perflevels()
    raw["ram_gb"], raw["ram_source"] = _advhw_ram_gb()
    raw["macos"] = _adv_run(["/usr/bin/sw_vers", "-productVersion"])
    raw["wired_mb"] = _advhw_int(_advhw_sysctl("iogpu.wired_limit_mb"))
    raw["metal"] = _adv_metal_probe()
    raw["boottime"] = _advhw_parse_boottime(_advhw_sysctl("kern.boottime"))
    return raw


def _advhw_detect_dynamic():
    raw = {}
    raw["batt"] = _adv_run(["/usr/bin/pmset", "-g", "batt"])
    raw["pmset"] = _adv_run(["/usr/bin/pmset", "-g"])
    raw["therm"] = _adv_run(["/usr/bin/pmset", "-g", "therm"])
    raw["disk_free_gb"] = _advhw_disk_free_gb()
    raw["memory_level"] = _advhw_int(_advhw_sysctl("kern.memorystatus_level"))
    raw["swap_used_gb"] = _advhw_parse_swap(_advhw_sysctl("vm.swapusage"))
    return raw


def _advhw_static_key():
    return (_advhw_parse_boottime(_advhw_sysctl("kern.boottime")),
            _advhw_int(_advhw_sysctl("iogpu.wired_limit_mb")))


_ADV_STATIC = {"key": None, "raw": None, "at": 0.0, "key_at": 0.0, "metal_at": 0.0,
               "retry_at": 0.0}
_ADV_DYNAMIC = {"raw": None, "at": 0.0}
_advhw_lock = _advhw_threading.Lock()


def _adv_hw(bust=False):
    """THIS Mac, assembled from cached detection.  Never raises.

    Static facts (chip, cores, model id, machine name, perflevels, RAM, macOS,
    GPU budget) are cached for the process and keyed on kern.boottime +
    iogpu.wired_limit_mb, the key re-read at most every 60 s — an owner's
    `sudo sysctl iogpu.wired_limit_mb=...` re-detects.  Dynamic facts (power,
    Low Power Mode, thermal, disk, memory pressure) live 60 s.  bust=True (the
    card's Re-check) refreshes the dynamic facts and the key, not the rest —
    except a Metal probe that FAILED: that is not a fact about the Mac (Metal
    may not have been ready when the dashboard started), so it is retried on
    bust and otherwise at most every _ADV_PROBE_RETRY_S, and until it answers
    the GPU budget says "assumed". E94: the same goes for any static fact a
    command left blank (brand, cores, machine name, model id, perflevels,
    RAM, macOS) — only the missing pieces are re-run.  Returns a deep copy, so a caller cannot
    corrupt the cache."""
    try:
        with _advhw_lock:
            now = _advhw_time.time()
            fresh = False                    # the probe already ran in THIS call
            if bust or _ADV_STATIC["raw"] is None or now - _ADV_STATIC["key_at"] >= _ADV_DYNAMIC_TTL:
                key = _advhw_static_key()
                _ADV_STATIC["key_at"] = now
                if _ADV_STATIC["raw"] is None or key != _ADV_STATIC["key"]:
                    _ADV_STATIC["raw"] = _advhw_detect_static()
                    _ADV_STATIC["key"] = key
                    _ADV_STATIC["at"] = now
                    _ADV_STATIC["metal_at"] = now
                    _ADV_STATIC["retry_at"] = now
                    fresh = True
            # E94: a static piece a command failed to answer is retried the
            # same way (bust, else at most every _ADV_PROBE_RETRY_S)
            if not fresh and (bust or now - _ADV_STATIC["retry_at"] >= _ADV_PROBE_RETRY_S):
                missing = _advhw_static_missing(_ADV_STATIC["raw"])
                if missing:
                    _advhw_fill_missing(_ADV_STATIC["raw"], missing)
                    _ADV_STATIC["retry_at"] = now
            metal = _ADV_STATIC["raw"].get("metal")
            if not fresh and isinstance(metal, dict) and metal.get("ok") is not True \
                    and _adv_parse_brand(_ADV_STATIC["raw"].get("brand"))["supported"] is not False \
                    and (bust or now - _ADV_STATIC["metal_at"] >= _ADV_PROBE_RETRY_S):
                _ADV_STATIC["raw"]["metal"] = _adv_metal_probe()
                _ADV_STATIC["metal_at"] = now
            if bust or _ADV_DYNAMIC["raw"] is None or now - _ADV_DYNAMIC["at"] >= _ADV_DYNAMIC_TTL:
                _ADV_DYNAMIC["raw"] = _advhw_detect_dynamic()
                _ADV_DYNAMIC["at"] = now
            raw = dict(_ADV_STATIC["raw"])
            raw.update(_ADV_DYNAMIC["raw"])
            hw = _adv_assemble(raw, local=True)
            hw["static_at"] = _ADV_STATIC["at"]
            hw["dynamic_at"] = _ADV_DYNAMIC["at"]
            return _advhw_copy.deepcopy(hw)
    except Exception as e:          # detection never raises (§3.2)
        err = "%s: %s" % (type(e).__name__, _advhw_clean(str(e), 120))
        try:
            hw = _adv_assemble({}, local=True)
        except Exception:
            hw = _advhw_minimal_hw()
        hw["error"] = err
        return hw


def _advhw_minimal_hw():
    """The last-resort hw dict (a bug in assembly itself): every fact unknown,
    estimates fall back to the slowest known chip."""
    return {"ok": True, "version": _ADV_HW_VERSION, "local": True, "supported": True,
            "unsupported_reason": "",
            "chip": _advhw_row_out(("M1", "", 8), dict(_ADV_CHIPS[("M1", "", 8)]), None,
                                   "assumed", "unknown-chip", "low",
                                   ["estimates assume the slowest Apple-silicon chip"]),
            "ram_gb": None, "gpu_budget": {"gb": None, "source": "unknown", "fraction": None,
                                           "note": "detection failed", "metal": {}},
            "cpu": {"levels": [], "count": None}, "macos": "", "model_id": "",
            "machine_name": "", "form": "unknown", "fanless": False,
            "power": {"source": "unknown", "pct": None, "has_battery": None, "state": None,
                      "low_power": None, "low_power_source": "absent"},
            "thermal": {"limited": None, "cpu_speed_limit": None, "text": ""},
            "disk_free_gb": None, "rosetta": False,
            "now": {"memory_free_pct": None, "swap_used_gb": None}, "sources": {}}


def _adv_assemble(raw, local=False):
    """Raw detection output -> the hw dict (§9 `hw`). PURE.  Every missing
    fact degrades to None with a `sources` entry saying so."""
    raw = raw or {}
    brand = _adv_parse_brand(raw.get("brand"))
    supported, reason = brand["supported"], brand["reason"]
    if supported is None:
        arm = raw.get("arm64")
        if arm == 1:
            supported = True
        elif arm == 0:
            supported, reason = False, "Local models need Apple silicon; this Mac is not one."
        else:
            supported = True         # unknown: never block, estimate conservatively
    cores = _adv_parse_ioreg_cores(raw.get("ioreg"))
    cores_via = "ioreg" if cores is not None else None
    if cores is None:
        cores = _adv_parse_sp_displays(raw.get("sp_displays"))
        cores_via = "system_profiler" if cores is not None else None
    chip = None
    if supported:
        chip = _adv_chip_row(brand["family"], brand["variant"], cores, brand["gen"])
        if cores_via and chip["cores_source"] == "detected":
            chip["cores_source"] = cores_via
        if brand["family"] is None:
            chip["notes"] = ["could not identify the chip%s" % (
                (" (%s)" % brand["brand"]) if brand["brand"] else "")] + chip["notes"]
        chip.update(brand=brand["brand"], gen=brand["gen"],
                    detected_family=brand["family"], detected_variant=brand["variant"])
    ram = raw.get("ram_gb")
    ram = float(ram) if isinstance(ram, (int, float)) and ram > 0 else None
    metal = raw.get("metal") if isinstance(raw.get("metal"), dict) else {"ok": False, "error": "not probed"}
    gpu = _adv_gpu_budget(ram or 0, raw.get("wired_mb") or 0,
                          metal.get("bytes") if metal.get("ok") else 0)
    gpu["metal"] = {k: metal.get(k) for k in ("ok", "bytes", "max_buffer", "unified", "name", "ms", "error")
                    if k in metal}
    sp = raw.get("sp_hardware") if isinstance(raw.get("sp_hardware"), dict) else {}
    batt = _adv_parse_batt(raw.get("batt"))
    pm = _adv_parse_pmset(raw.get("pmset"))
    th = _adv_parse_therm(raw.get("therm"))
    machine_name = sp.get("machine_name") or ""
    form = _adv_form(machine_name, batt["has_battery"])
    # an A-series Mac (the A18 Pro MacBook) has no fan whatever its marketing name
    if form == "laptop" and _ADVHW_ASERIES_RE.match(brand["brand"]):
        form = "laptop_fanless"
    levels = _adv_parse_perflevels(raw.get("perflevels"))
    disk = raw.get("disk_free_gb")
    return {
        "ok": True, "version": _ADV_HW_VERSION, "local": bool(local),
        "supported": bool(supported), "unsupported_reason": "" if supported else reason,
        "chip": chip,
        "ram_gb": round(ram, 2) if ram else None,
        "gpu_budget": gpu,
        "cpu": {"levels": levels,
                "count": sum(lv["logicalcpu"] or 0 for lv in levels) or None},
        "macos": _advhw_clean(raw.get("macos"), 20),
        "model_id": _advhw_clean(raw.get("hw_model") or sp.get("machine_model"), 40),
        "machine_name": machine_name,
        "form": form,
        "fanless": form == "laptop_fanless",
        "power": {"source": batt["source"], "pct": batt["pct"],
                  "has_battery": batt["has_battery"], "state": batt["state"],
                  "low_power": pm["low_power"], "low_power_source": pm["low_power_source"]},
        "thermal": th,
        "disk_free_gb": disk if isinstance(disk, (int, float)) else None,
        "rosetta": raw.get("rosetta") == 1,
        "now": {"memory_free_pct": raw.get("memory_level"),
                "swap_used_gb": raw.get("swap_used_gb")},
        "sources": {"chip": "machdep.cpu.brand_string" if brand["brand"] else "unknown",
                    "gpu_cores": cores_via or "assumed",
                    "ram": raw.get("ram_source") or ("hw.memsize" if ram else "unknown"),
                    "gpu_budget": gpu["source"],
                    "power": "pmset -g batt" if raw.get("batt") else "unknown",
                    "thermal": "pmset -g therm" if raw.get("therm") else "unknown",
                    "machine_name": "system_profiler" if machine_name else "unknown"},
    }


def _adv_machine(brand, ram_gb, gpu_cores=None, machine_name=None, has_battery=None,
                 on_battery=False, metal_bytes=None, wired_mb=0, low_power=None,
                 thermal_limit=None, disk_free_gb=None, macos=""):
    """A SYNTHETIC Mac (local False): the hw dict the estimator, the goldens,
    the ranking tests and the calibration script use for a machine nobody here
    owns.  PURE.  `brand` is the brand string ("Apple M4 Pro")."""
    if has_battery is None:
        has_battery = bool(machine_name and "MacBook" in machine_name)
    batt = ""
    if has_battery or on_battery:
        batt = "Now drawing from '%s'\n -InternalBattery-0 (id=1)\t80%%; %s; 3:00 remaining present: true" % (
            "Battery Power" if on_battery else "AC Power", "discharging" if on_battery else "charged")
    else:
        batt = "Now drawing from 'AC Power'"
    pm = "" if low_power is None else " lowpowermode         %d\n" % (1 if low_power else 0)
    th = ("CPU_Speed_Limit = %d\n" % thermal_limit) if thermal_limit is not None else \
         "Note: No thermal warning level has been recorded\n"
    raw = {"brand": brand, "arm64": 1 if (brand or "").startswith("Apple") else None,
           "ioreg": ('"gpu-core-count" = %d' % gpu_cores) if gpu_cores else "",
           "ram_gb": float(ram_gb), "ram_source": "synthetic",
           "metal": {"ok": True, "bytes": int(metal_bytes)} if metal_bytes else {"ok": False, "error": "synthetic"},
           "wired_mb": wired_mb, "sp_hardware": {"machine_name": machine_name or ""},
           "batt": batt, "pmset": pm, "therm": th, "disk_free_gb": disk_free_gb,
           "macos": macos}
    hw = _adv_assemble(raw, local=False)
    hw["sources"]["ram"] = "synthetic"
    return hw


def _adv_hw_notes(hw):
    """Plain sentences about THIS machine's state that change how the numbers
    feel (§5.6) — shown, never ranked."""
    hw = hw or {}
    out = []
    if not hw.get("supported", True):
        out.append(hw.get("unsupported_reason") or "Local models need Apple silicon.")
        return out
    p = hw.get("power") or {}
    if p.get("source") == "battery":
        mins = None
        fn = _advhw_g("_idle_min")
        if callable(fn):
            try:
                mins = int(fn())
            except Exception:
                mins = None
        out.append("On battery: replies run the GPU hard. Idle-suspend frees the model's "
                   "memory after %s of no use." % ("%d min" % mins if mins else "a few minutes"))
    if p.get("low_power") is True:
        out.append("Low Power Mode is on: expect slower replies (not measured).")
    if hw.get("fanless"):
        out.append("No fan: long runs (briefings, multi-step agent tasks) slow down once it "
                   "is warm. Reports say 10-25% after 5-30 minutes (not measured here).")
    th = hw.get("thermal") or {}
    if th.get("limited") is True:
        lim = th.get("cpu_speed_limit")
        out.append("This Mac reports a CPU speed limit right now%s." % (
            " (%d%%)" % lim if isinstance(lim, int) else ""))
    if (hw.get("gpu_budget") or {}).get("source") == "assumed":
        out.append("GPU memory budget is assumed: the Metal probe did not answer.")
    chip = hw.get("chip") or {}
    for n in chip.get("notes") or []:
        out.append("Chip: %s." % n)
    if hw.get("rosetta"):
        out.append("The dashboard's Python runs under Rosetta (translated).")
    return out


# --------------------------------------------------------------------------
# the estimator (§5) — PURE given hw, facts and (optionally) measured data
# --------------------------------------------------------------------------
def _adv_turn_ctx(uses):
    """The context a typical turn carries for these answers (§4 Q1)."""
    u = set(uses or ())
    if "docs" in u:
        return _ADV_TURN_CTX["docs"]
    if "code" in u:
        return _ADV_TURN_CTX["code"]
    return _ADV_TURN_DEFAULT


def _adv_mtp_mult(f, uses=("chat", "agent"), lane="primary"):
    """Speculative-decoding speed-up for this mix of uses.  > 1 ONLY on the
    primary lane, ONLY for an entry whose drafter multiplier was MEASURED
    (an unmeasured drafter gets 1.0 and a note).  The mean of the per-use
    multipliers over the uses that have one (chat when none do)."""
    mtp = f.get("mtp") if isinstance(f, dict) else None
    if lane != "primary" or not isinstance(mtp, dict) or mtp.get("measured") is not True:
        return 1.0
    mult = mtp.get("mult") or {}
    keys = [u for u in (uses or ()) if u in mult] or ["chat"]
    vals = [float(mult[k]) for k in keys if k in mult]
    return sum(vals) / len(vals) if vals else 1.0


def _advhw_bytes_per_token(f):
    return float(f.get("active_bytes_per_token") or f["weights_bytes"])


def _advhw_decode_parts(f, chip, uses, lane):
    bw = float(chip["bandwidth_gbs"])
    e = float(chip["e_chip"])
    k = float(f["k_decode"])
    mul = _adv_mtp_mult(f, uses, lane)
    gb = _advhw_bytes_per_token(f) / 1e9
    t0 = gb / (k * e * bw) * 1000.0 / mul
    measured_slope = f.get("ctx_slope_ms_per_1k")
    if measured_slope is not None and (bool(f.get("ctx_slope_with_mtp")) == (mul > 1.0)):
        slope_ref, slope_src = float(measured_slope), "measured"
    else:
        slope_ref = _ADV_BETA * (_adv_kv_bytes(f) / 32768.0) / mul
        slope_src = "formula"
    slope = slope_ref * (_ADV_REF_BW / bw) / e
    return {"bw": bw, "e": e, "k": k, "mul": mul, "gb": gb, "t0": t0,
            "slope_ref": slope_ref, "slope_src": slope_src, "slope": slope}


def _adv_decode_tps(f, chip, ctx, uses=("chat", "agent"), lane="primary"):
    """Decode tok/s at `ctx` tokens of context (§5.3):
        t0    = bytes_per_token / (k x e_chip x BW) x 1000 / mtp_mult     [ms]
        slope = (measured, or 0.48 x kv/32KiB / mtp_mult) x (614/BW) / e_chip
        tok/s = 1000 / (t0 + slope x ctx/1000)"""
    p = _advhw_decode_parts(f, chip, uses, lane)
    return 1000.0 / (p["t0"] + p["slope"] * float(ctx) / 1000.0)


def _advhw_prefill_parts(f, chip):
    ref = f.get("prefill_ref_tps")
    if ref:
        ref, src = float(ref), "measured" if f.get("prefill_confidence") == "measured" else "shared"
    else:
        eff = _ADV_MOE_PREFILL_EFF if f.get("arch") == "moe" else 1.0
        ref, src = _ADV_K_PREFILL / float(f["active_params_b"]) * eff, "formula"
    return {"ref": ref, "src": src, "p": float(chip["p_index"]), "tps": ref * float(chip["p_index"])}


def _adv_prefill_s(f, chip, prefix_tokens):
    """Seconds to read Hermes's cold setup prompt (§5.4):
        prefill tok/s = (measured on the reference, or 19,000 / active B x 0.30 if MoE) x p_index
        cold_s        = S / prefill tok/s"""
    return float(prefix_tokens) / _advhw_prefill_parts(f, chip)["tps"]


def _adv_resident_gib(f, ram_gb, lane, ctx_turn, prefix_tokens):
    """Resident memory of one lane (§5.2), GiB:
        W + D (primary only) + ctx_turn x kvb + N_apc x (S x kvb + gdn) + 0.5 + 0.03 x W"""
    return _advhw_resident_parts(f, ram_gb, lane, ctx_turn, prefix_tokens)["total"]


def _advhw_resident_parts(f, ram_gb, lane, ctx_turn, prefix_tokens):
    W = float(f["weights_bytes"]) / _ADV_GIB
    D = float(f.get("drafter_bytes") or 0) / _ADV_GIB if lane == "primary" else 0.0
    kvb = _adv_kv_bytes(f)
    gdn = _adv_gdn_bytes(f)
    n = _adv_apc_entries(ram_gb, lane)
    kv = float(ctx_turn) * kvb / _ADV_GIB
    apc = n * (float(prefix_tokens) * kvb + gdn) / _ADV_GIB
    over = _ADV_OVERHEAD_GIB + _ADV_OVERHEAD_FRAC * W
    return {"W": W, "D": D, "kvb": kvb, "gdn": gdn, "n": n, "kv": kv, "apc": apc,
            "over": over, "total": W + D + kv + apc + over}


def _advhw_range(v, frac, nd):
    if not isinstance(v, (int, float)):
        return None
    lo, hi = round(v * (1.0 - frac), nd), round(v * (1.0 + frac), nd)
    rv = round(v, nd)
    return [min(lo, rv), max(hi, rv)]


def _advhw_frac(basis, chip_conf, const_conf):
    """§5.5: measured-here 10% · reference chip + measured constant 15% · a
    measured chip row 25% · a scaled row or a low-confidence constant 40%."""
    if basis == "measured-here":
        return _ADV_RANGE_FRAC["measured-here"]
    if chip_conf == "low" or const_conf == "low":
        return _ADV_RANGE_FRAC["low"]
    if chip_conf == "reference" and const_conf == "measured":
        return _ADV_RANGE_FRAC["reference"]
    return _ADV_RANGE_FRAC["row"]


def _advhw_uses(answers):
    uses = (answers or {}).get("uses") if isinstance(answers, dict) else None
    if isinstance(uses, str):               # "chat", not ["c", "h", "a", "t"]
        uses = [uses]
    elif not isinstance(uses, (list, tuple, set, frozenset)):
        uses = None
    uses = [u for u in (uses or []) if isinstance(u, str)]
    return uses or ["chat", "agent"]


def _advhw_mix_label(f, uses, lane):
    mtp = f.get("mtp") if isinstance(f, dict) else None
    if lane != "primary" or not isinstance(mtp, dict) or mtp.get("measured") is not True:
        return ""
    keys = [u for u in uses if u in (mtp.get("mult") or {})] or ["chat"]
    return "+".join(keys)


def _adv_estimate(mid, hw, answers=None, prefix=None, lane="primary", measured=None):
    """What `mid` costs on `hw` in `lane` (§5, shape in §9):

        {id, label, lane, supported, estimate: true,
         resident_gb, decode_short_tps, decode_turn_tps, turn_ctx, short_ctx,
         prefill_s, prefix_tokens, prefix_source, decode_sustained_tps,
         battery, bytes_per_token_gb, download_gb,
         basis{decode, decode_short, prefill, resident},
         range{decode, decode_short, prefill, resident},       # [lo, hi]
         measured{...} | None,
         formula{decode, decode_short, prefill, resident},
         confidence{chip, decode, prefill}, inputs{...}, notes[]}

    `answers` = {uses[], ...} (only `uses` matters here); `prefix` = an int,
    a {tokens, source, profile} dict or None (then _adv_prefix()).
    `measured`: None = read this Mac's logs ONLY when hw is the local machine
    (hw.local); False = never; a dict = use it.  Returns None for an id that
    has no facts (the caller shows the roster's own figures).  PURE when
    measured is False or a dict and prefix is given."""
    f = _ADV_FACTS.get(mid)
    if not f:
        return None
    hw = hw or {}
    lane = "bg" if lane == "bg" else "primary"
    uses = _advhw_uses(answers)
    base = {"id": mid, "label": f.get("label") or mid, "lane": lane, "estimate": True,
            "uncensored": bool(f.get("uncensored")), "download_gb": _adv_download_gb(mid)}
    chip = hw.get("chip")
    if not hw.get("supported", False) or not isinstance(chip, dict):
        base.update(supported=False,
                    reason=hw.get("unsupported_reason") or "This Mac cannot run local models.",
                    resident_gb=None, decode_short_tps=None, decode_turn_tps=None,
                    prefill_s=None, measured=None, basis={}, range={}, formula={}, notes=[])
        return base
    if measured is None:
        measured = _adv_measured(mid) if hw.get("local") else None
    elif measured is False:
        measured = None
    if not isinstance(measured, dict):
        measured = None
    pre = _advhw_norm_prefix(prefix, mid, measured)
    S = pre["tokens"]
    ram = float(hw.get("ram_gb") or 0)
    turn = _ADV_BG_TURN_CTX if lane == "bg" else _adv_turn_ctx(uses)
    notes = []

    # resident
    rp = _advhw_resident_parts(f, ram, lane, turn, S)
    res_formula = ("%.2f GiB weights%s + KV %s tokens x %d KiB = %.2f + %d APC x (%s x %d KiB "
                   "+ %.0f MiB state) = %.2f + overhead 0.5 + 3%% of weights = %.2f -> %.1f GiB"
                   % (rp["W"], (" + %.2f drafter" % rp["D"]) if rp["D"] else "",
                      "{:,}".format(turn), rp["kvb"] // 1024, rp["kv"], rp["n"],
                      "{:,}".format(int(S)), rp["kvb"] // 1024, rp["gdn"] / 2 ** 20, rp["apc"],
                      rp["over"], rp["total"]))

    # decode
    dp = _advhw_decode_parts(f, chip, uses, lane)
    d_short = 1000.0 / (dp["t0"] + dp["slope"] * _ADV_SHORT_CTX / 1000.0)
    d_turn = 1000.0 / (dp["t0"] + dp["slope"] * turn / 1000.0)
    mix = _advhw_mix_label(f, uses, lane)
    dec_formula_core = ("t0 = %.2f GB / (k %.3f x e_chip %.3f x %g GB/s) x 1000%s = %.2f ms; "
                        "slope = %.2f ms/1k (%s) x 614/%g / %.3f = %.3f ms/1k"
                        % (dp["gb"], dp["k"], dp["e"], dp["bw"],
                           (" / MTP %.2f (%s)" % (dp["mul"], mix)) if dp["mul"] > 1 else "",
                           dp["t0"], dp["slope_ref"], dp["slope_src"], dp["bw"], dp["e"], dp["slope"]))
    f_turn = "%s; tok/s = 1000 / (%.2f + %.3f x %.1f) = %.1f" % (
        dec_formula_core, dp["t0"], dp["slope"], turn / 1000.0, d_turn)
    f_short = "%s; tok/s = 1000 / (%.2f + %.3f x %.1f) = %.1f" % (
        dec_formula_core, dp["t0"], dp["slope"], _ADV_SHORT_CTX / 1000.0, d_short)

    # prefill
    pp = _advhw_prefill_parts(f, chip)
    pre_s = S / pp["tps"]
    f_pre = "%s tokens / (%.0f tok/s %s x p_index %.4f = %.0f tok/s) = %.1f s" % (
        "{:,}".format(int(S)), pp["ref"], "on the reference" if pp["src"] != "formula"
        else "(19,000 / %gB%s)" % (f["active_params_b"], " x 0.30 MoE" if f.get("arch") == "moe" else ""),
        pp["p"], pp["tps"], pre_s)

    chip_conf = chip.get("confidence") or "medium"
    k_conf = f.get("k_confidence") or "medium"
    slope_conf = "measured" if dp["slope_src"] == "measured" else "medium"
    dec_conf = "low" if k_conf == "low" else ("measured" if k_conf == "measured" and
                                               slope_conf == "measured" else "medium")
    dec_short_conf = "low" if k_conf == "low" else k_conf
    pre_conf = f.get("prefill_confidence") or "medium"
    if pre_conf == "measured" and pp["src"] != "measured":
        pre_conf = "medium"
    basis_ref = "reference" if chip_conf == "reference" else "formula"
    basis = {"resident": "formula",
             "decode": basis_ref if dec_conf == "measured" else "formula",
             "decode_short": basis_ref if dec_short_conf == "measured" else "formula",
             "prefill": basis_ref if pre_conf == "measured" else "formula"}
    frac = {"decode": _advhw_frac(basis["decode"], chip_conf, dec_conf),
            "decode_short": _advhw_frac(basis["decode_short"], chip_conf, dec_short_conf),
            "prefill": _advhw_frac(basis["prefill"], chip_conf, pre_conf)}

    # measured-here overrides (D6): applied only to the quantity it measured
    applied = {"decode_short": False, "decode_turn": False, "prefill": False}
    if measured:
        if measured.get("decode_short"):
            d_short = float(measured["decode_short"])
            basis["decode_short"], frac["decode_short"] = "measured-here", _ADV_RANGE_FRAC["measured-here"]
            f_short = "measured on this Mac: median of %d replies under %s tokens of context = %.1f tok/s" % (
                measured.get("n_short") or 0, "{:,}".format(_ADV_SHORT_MAX_PROMPT), d_short)
            applied["decode_short"] = True
        if measured.get("decode_turn") and _ADV_TURN_MIN_PROMPT <= turn <= _ADV_TURN_MAX_PROMPT:
            d_turn = float(measured["decode_turn"])
            basis["decode"], frac["decode"] = "measured-here", _ADV_RANGE_FRAC["measured-here"]
            f_turn = ("measured on this Mac: median of %d replies at 16-32K tokens of context = "
                      "%.1f tok/s" % (measured.get("n_turn") or 0, d_turn))
            applied["decode_turn"] = True
        if measured.get("prefill_tps"):
            pre_s = S / float(measured["prefill_tps"])
            basis["prefill"], frac["prefill"] = "measured-here", _ADV_RANGE_FRAC["measured-here"]
            f_pre = ("%s tokens / %.0f tok/s measured on this Mac (median of %d cold reads) = "
                     "%.1f s" % ("{:,}".format(int(S)), float(measured["prefill_tps"]),
                                 measured.get("n_prefill") or 0, pre_s))
            applied["prefill"] = True
        if any(applied.values()):
            notes.append("measured on this Mac's own model-server logs; those replies include "
                         "real contention between the two lanes")
        measured = dict(measured, applied=applied)

    # notes that belong to the model on this machine
    mtp = f.get("mtp")
    if isinstance(mtp, dict) and mtp.get("measured") is not True and lane == "primary":
        notes.append("has an MTP drafter, but its speed-up is not measured for this build: "
                     "speeds assume no drafter")
    if f.get("drafter_available") and lane == "primary":
        notes.append(f["drafter_available"].get("note") or "an unmeasured drafter stays off")
    if k_conf == "low":
        notes.append("the speed constant for this model is an assumption until it is measured "
                     "on AC (tools/tests/ac/advisor-calibration.md)")
    if chip_conf == "low":
        notes.append("this chip's row is scaled or low confidence: wide range")
    if pre_s > _ADV_SLOW_PREFILL_S:
        notes.append("After the model sleeps, its first reply re-reads Hermes's ~%.1fK-token "
                     "setup: ~%.0f s here. Keep it awake longer (idle-suspend) or use the "
                     "Focused prompt profile." % (S / 1000.0, pre_s))
    sustained = None
    if hw.get("fanless"):
        sustained = round(d_turn * _ADV_FANLESS_SUSTAINED, 1)
        notes.append("fanless: runs longer than ~5 min assume x%.2f (~%.0f tok/s), not measured"
                     % (_ADV_FANLESS_SUSTAINED, sustained))
    if turn > int(f.get("ctx_native") or 0):
        notes.append("the turn is longer than the model's native context")

    bpt = _advhw_bytes_per_token(f) / 1e9
    base.update(
        supported=True,
        resident_gb=round(rp["total"], 2),
        decode_short_tps=round(d_short, 1),
        decode_turn_tps=round(d_turn, 1),
        decode_sustained_tps=sustained,
        turn_ctx=turn, short_ctx=_ADV_SHORT_CTX,
        prefill_s=round(pre_s, 1),
        prefix_tokens=int(S), prefix_source=pre["source"], prefix_profile=pre.get("profile"),
        battery=_adv_battery_label(bpt),
        bytes_per_token_gb=round(bpt, 3),
        basis=basis,
        range={"decode": _advhw_range(d_turn, frac["decode"], 1),
               "decode_short": _advhw_range(d_short, frac["decode_short"], 1),
               "prefill": _advhw_range(pre_s, frac["prefill"], 1),
               "resident": _advhw_range(rp["total"], _ADV_RESIDENT_RANGE, 2)},
        measured=measured,
        formula={"decode": f_turn, "decode_short": f_short, "prefill": f_pre,
                 "resident": res_formula},
        confidence={"chip": chip_conf, "decode": dec_conf, "prefill": pre_conf},
        inputs={"bandwidth_gbs": dp["bw"], "e_chip": dp["e"], "p_index": pp["p"],
                "k": dp["k"], "mtp_mult": round(dp["mul"], 3),
                "slope_ms_per_1k": round(dp["slope"], 4), "slope_source": dp["slope_src"],
                "prefill_tps": round(pp["tps"], 1), "prefill_source": pp["src"],
                "apc_entries": rp["n"], "kv_bytes_per_token": rp["kvb"],
                "gdn_state_bytes": rp["gdn"], "weights_gib": round(rp["W"], 3),
                "drafter_gib": round(rp["D"], 3), "chip_row": chip.get("row"),
                "macos": hw.get("macos") or None},
        notes=notes,
    )
    return base


def _adv_estimates(hw, answers=None, prefix=None, ids=None, lane="primary", measured=None):
    """{id: estimate} for every faceted id (or `ids`). Never raises per id."""
    out = {}
    for mid in (ids or list(_ADV_FACTS)):
        try:
            e = _adv_estimate(mid, hw, answers, prefix, lane, measured)
        except Exception as ex:
            e = {"id": mid, "estimate": True, "supported": False,
                 "reason": "estimate failed: %s" % type(ex).__name__}
        if e is not None:
            out[mid] = e
    return out


# --------------------------------------------------------------------------
# measured here (D6) — this Mac's own model-server logs, via aux_context
# --------------------------------------------------------------------------
def _advhw_median(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and _advhw_math.isfinite(x)]
    return float(_advhw_stats.median(xs)) if xs else None


def _adv_measured_from_rows(mid, rows):
    """Medians for `mid` from parsed log rows (aux_context._cx_parse shape).
    PURE.  None when the model has no rows at all.

      decode_short  median decode_tps of rows with prompt < 4k, generated >= 64
      decode_turn   ...                     with 16k <= prompt < 32k
      prefill_tps   median new_tokens / prefill_s over rows with >= 4,000 new tokens
      prefix_tokens prompt_tokens of the NEWEST row that read >= 4,000 tokens
                    with (almost) nothing cached — a genuinely cold setup read
                    (a streamed conversation turn of at most 32,000 tokens: not
                    a non-streamed auxiliary call, not a pasted document)
    Each quantity needs n >= 5 or it is None (its n is still reported)."""
    mine = [r for r in (rows or []) if isinstance(r, dict) and r.get("model") == mid]
    if not mine:
        return None
    dec = [r for r in mine if (r.get("generated_tokens") or 0) >= _ADV_MEASURED_MIN_GEN
           and isinstance(r.get("decode_tps"), (int, float)) and r["decode_tps"] > 0]
    short = [r["decode_tps"] for r in dec if (r.get("prompt_tokens") or 0) < _ADV_SHORT_MAX_PROMPT]
    turn = [r["decode_tps"] for r in dec
            if _ADV_TURN_MIN_PROMPT <= (r.get("prompt_tokens") or 0) < _ADV_TURN_MAX_PROMPT]
    cold = [r for r in mine if (r.get("new_tokens") or 0) >= _ADV_COLD_MIN_NEW
            and isinstance(r.get("prefill_s"), (int, float)) and r["prefill_s"] > 0]
    rates = [r["new_tokens"] / r["prefill_s"] for r in cold]
    # S = Hermes's setup prompt, so only a CONVERSATION turn that read it (nearly) cold counts:
    # a non-streamed row is the auxiliary client (titles, the compression summariser) and a
    # prompt past _ADV_COLD_MAX_PREFIX carried a pasted document on top of the setup.  Both
    # still feed the prefill RATE above.  stream None (a log that never printed it) is kept.
    full = [r for r in cold if (r.get("cached_tokens") or 0) <= _ADV_COLD_MAX_CACHED
            and r.get("stream") is not False
            and (r.get("prompt_tokens") or 0) <= _ADV_COLD_MAX_PREFIX]
    full.sort(key=lambda r: (r.get("epoch") or 0, r.get("ts") or ""))
    n = _ADV_MEASURED_MIN_N
    keep = {id(r) for r in dec} | {id(r) for r in cold}
    used = [r for r in mine if id(r) in keep]
    stamps = sorted(r.get("ts") for r in used if r.get("ts"))
    return {
        "decode_short": round(_advhw_median(short), 1) if len(short) >= n else None,
        "decode_turn": round(_advhw_median(turn), 1) if len(turn) >= n else None,
        "prefill_tps": round(_advhw_median(rates), 1) if len(rates) >= n else None,
        "prefix_tokens": int(full[-1]["prompt_tokens"]) if full else None,
        "n_short": len(short), "n_turn": len(turn), "n_prefill": len(rates),
        "n": len(used), "since": stamps[0] if stamps else None,
        "until": stamps[-1] if stamps else None,
        "lanes": sorted({r.get("lane") for r in mine if r.get("lane")}),
        "min_n": n,
    }


def _adv_measured(mid, rows=None):
    """Measured-here medians for `mid`, reading BOTH model-server logs through
    aux_context's cached parser (_cx_rows, resolved by name — file reads only,
    mtime-cached there).  None when aux_context is not loaded or the model has
    no rows.  Never raises."""
    try:
        if rows is None:
            cx = _advhw_g("_cx_rows")
            if not callable(cx):
                return None
            rows = []
            for name in ("_CX_LOG", "_CX_LOG_BG"):
                p = _advhw_g(name)
                if p:
                    rows.extend(cx(p) or [])
        return _adv_measured_from_rows(mid, rows)
    except Exception:
        return None


def _adv_prefix(mid=None, measured=None, profile=None):
    """The cold prefix S (§5.4): the newest genuinely cold read of this model
    in the logs, else the prompt profile's figure (profile given, or read off
    config.yaml through aux_promptbudget.prompt_budget_profile by name), else
    18,500 (Balanced, the profile new installs start with)."""
    if isinstance(measured, dict) and measured.get("prefix_tokens"):
        return {"tokens": int(measured["prefix_tokens"]), "source": "measured", "profile": None}
    prof = profile
    if prof is None:
        fn = _advhw_g("prompt_budget_profile")
        if callable(fn):
            try:
                prof = fn()
            except Exception:
                prof = None
    if prof in _ADV_PREFIX_TOKENS:
        return {"tokens": _ADV_PREFIX_TOKENS[prof],
                "source": "measured" if prof == "full" else "predicted", "profile": prof}
    return {"tokens": _ADV_PREFIX_DEFAULT, "source": "assumed", "profile": prof}


def _advhw_pos_tokens(x):
    """A usable token count: a real, finite number above zero, below a billion
    (never a bool)."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    try:
        return _advhw_math.isfinite(x) and 0 < x < 1e9
    except (OverflowError, ValueError):     # an int too large for a float
        return False


def _advhw_norm_prefix(prefix, mid, measured):
    if _advhw_pos_tokens(prefix):
        return {"tokens": int(prefix), "source": "given", "profile": None}
    if isinstance(prefix, dict) and _advhw_pos_tokens(prefix.get("tokens")):
        src = prefix.get("source")
        return {"tokens": int(prefix["tokens"]), "source": src if isinstance(src, str) and src else "given",
                "profile": prefix.get("profile")}
    return _adv_prefix(mid, measured)


# --------------------------------------------------------------------------
# disclosure ("How we estimate") — constants and formula templates
# --------------------------------------------------------------------------
def _adv_formulas():
    return {
        "limit": "min(GPU working set, MLX_SOFT_GB - 2, RAM - reserve); reserve 6 GB at "
                 ">=24 GB, 4.5 at >=16, else 3.5",
        "resident": "weights + drafter (primary) + turn x KV/token + APC entries x "
                    "(prefix x KV/token + recurrent state) + 0.5 GB + 3% of weights",
        "decode": "1000 / (t0 + slope x context/1000); t0 = bytes per token / (k x e_chip x "
                  "bandwidth) / MTP multiplier; slope measured or 0.48 ms x (KV/32 KiB), "
                  "scaled by 614/bandwidth/e_chip",
        "prefill": "prefix tokens / (reference prefill tok/s x p_index); reference = "
                   "measured, or 19,000 / active B params (x 0.30 for MoE)",
        "verdict": "fits <= 85% of the limit, tight <= the limit, otherwise it does not fit",
        "battery": "memory moved per generated token: <=2 GB light, <=7 GB moderate, more "
                   "heavy; an always-on background lane adds one step",
        "range": "measured here +/-10%; reference chip with a measured constant +/-15%; a "
                 "measured chip row +/-25%; a scaled row or an assumed constant +/-40%",
    }


def _adv_constants():
    return {
        "reference_chip": _advhw_row_label(_ADV_REF_KEY), "reference_bw_gbs": _ADV_REF_BW,
        "reference_pp512": _ADV_REF_PP, "reference_tg128": _ADV_REF_TG,
        "chip_source": _ADV_CHIP_SOURCE,
        "beta_ms_per_1k": _ADV_BETA, "k_small": _ADV_K_SMALL, "k_moe": _ADV_K_MOE,
        "k_prefill": _ADV_K_PREFILL, "moe_prefill_eff": _ADV_MOE_PREFILL_EFF,
        "overhead_gib": _ADV_OVERHEAD_GIB, "overhead_frac": _ADV_OVERHEAD_FRAC,
        "fits_frac": _ADV_FITS_FRAC, "soft_margin_gb": _ADV_SOFT_MARGIN_GB,
        "short_ctx": _ADV_SHORT_CTX, "turn_ctx": dict(_ADV_TURN_CTX),
        "bg_turn_ctx": _ADV_BG_TURN_CTX, "prefix_tokens": dict(_ADV_PREFIX_TOKENS),
        "prefix_default": _ADV_PREFIX_DEFAULT, "slow_prefill_s": _ADV_SLOW_PREFILL_S,
        "fanless_sustained": _ADV_FANLESS_SUSTAINED, "range_frac": dict(_ADV_RANGE_FRAC),
        "measured_min_n": _ADV_MEASURED_MIN_N, "cold_min_new_tokens": _ADV_COLD_MIN_NEW,
        "cold_max_prefix_tokens": _ADV_COLD_MAX_PREFIX, "probe_retry_s": _ADV_PROBE_RETRY_S,
        "disk_headroom_gb": _ADV_DISK_HEADROOM_GB,
    }
