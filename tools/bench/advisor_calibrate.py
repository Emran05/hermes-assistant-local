#!/usr/bin/env python3
"""advisor_calibrate.py — what the model advisor's constants look like on THIS Mac.

Reads the model servers' own logs (no model is loaded, woken or contacted) and
prints, per model, the numbers dashboard/aux_advisor_hw.py's _ADV_FACTS carries
for it, as measured here:

  * decode tok/s by context bucket (<2k, 2-8k, 8-16k, 16-24k, 24-32k, >=32k),
    replies with >= 64 generated tokens only;
  * a weighted least-squares fit of 1000/decode = t0 + slope x ctx/1000 over
    the buckets with >= 3 replies -> t0 (ms per token at zero context) and the
    context slope (ms per output token per 1k context tokens);
  * k_effective = bytes per token / (e_chip x bandwidth x t0) — the decode
    efficiency constant, INCLUDING any drafter speed-up that was running;
  * the slope scaled back to the reference chip (what the facts store);
  * cold prefill: the median of new_tokens / prefill_s over reads of >= 4,000
    new tokens, and that rate / p_index (the facts' prefill_ref_tps);
  * the estimator's own prediction at each bucket's median prompt, against
    the measurement (error %), and the delta of each fitted constant against
    the facts.

Nothing is written: copy what you trust into _ADV_FACTS by hand, regenerate the
golden (`python3 tools/tests/unit/t_advisor_estimate.py --write-golden`), and
review the diff.  The procedure around it — which models to bench, the drill,
the two-stream probe — is tools/tests/ac/advisor-calibration.md.

ONE TRUTH: the chip table, the facts and the estimator come from
dashboard/aux_advisor_hw.py and the log parser from dashboard/aux_context.py,
both exec'd into a private namespace exactly as server.py execs them.

GATES (owner decision, round 3): refuses to run on battery — `--i-am-on-ac`
only covers a Mac whose pmset says nothing, never one that says Battery Power
— and refuses in CI (CI, GITHUB_ACTIONS) and inside the unit tier
(HERMES_TESTS_OFFLINE).  `--dry-run` prints the plan and reads nothing.

Usage:
  tools/bench/advisor_calibrate.py
  tools/bench/advisor_calibrate.py --model mlx-community/Qwen3.5-4B-4bit
  tools/bench/advisor_calibrate.py --log ~/.hermes/bench/decode_bench-server-blocknone.log
  tools/bench/advisor_calibrate.py --json
"""
import argparse
import io
import json
import os
import statistics
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASH = os.path.join(REPO, "dashboard")
AUX_HW = os.path.join(DASH, "aux_advisor_hw.py")
AUX_CX = os.path.join(DASH, "aux_context.py")
TAIL_BYTES = 8 * 1024 * 1024
MIN_GEN = 64
MIN_BUCKET_N = 3
COLD_MIN_NEW = 4000
BUCKETS = (("<2k", 0, 2000), ("2-8k", 2000, 8000), ("8-16k", 8000, 16000),
           ("16-24k", 16000, 24000), ("24-32k", 24000, 32000), (">=32k", 32000, 10 ** 9))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- gates ---------------------------------------------------------------------
def read_pmset():
    try:
        return subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True,
                              text=True, timeout=10).stdout or ""
    except Exception:
        return ""


def power_gate(pmset_text, env=None, override=False):
    """(allowed, reason).  CI and the unit tier always refuse; Battery Power
    always refuses; an unreadable pmset refuses unless --i-am-on-ac."""
    env = os.environ if env is None else env
    for k in ("CI", "GITHUB_ACTIONS"):
        if str(env.get(k, "")).lower() in ("1", "true", "yes"):
            return False, "refusing to run in CI (%s is set): calibration is an owner-run step" % k
    if env.get("HERMES_TESTS_OFFLINE"):
        return False, "refusing to run inside the unit tier (HERMES_TESTS_OFFLINE is set)"
    t = pmset_text or ""
    if "'Battery Power'" in t:
        return False, ("refusing on battery (battery rule): plug in and run it again. "
                       "--i-am-on-ac does not override a pmset that says Battery Power.")
    if "'AC Power'" in t:
        return True, "on AC power"
    if override:
        return True, "pmset said nothing; --i-am-on-ac given"
    return False, "pmset did not report AC power; pass --i-am-on-ac only if pmset is unavailable"


# --- one truth: the advisor + the parser, exec'd like server.py does -------------
def load_modules(home=None):
    g = {"__name__": "advisor_calibrate_ns", "HOME": home or os.path.expanduser("~"),
         "DATA": os.path.join(home or os.path.expanduser("~"), ".hermes", "dashboard"),
         "os": os, "sys": sys, "subprocess": subprocess,
         "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None,
         "CHAT_JOBS": {}, "active_model": lambda: ""}
    import threading
    g["_state_lock"] = threading.Lock()
    for path in (AUX_HW, AUX_CX):
        with io.open(path, encoding="utf-8") as f:
            exec(compile(f.read(), path, "exec"), g)
    return g


def read_rows(g, path, tail_bytes):
    text, _size = g["_cx_read_tail"](path, tail_bytes)
    rows = g["_cx_parse"](text)
    for r in rows:
        r["log"] = path
    return rows


# --- the fit ---------------------------------------------------------------------
def buckets_for(rows):
    out = []
    dec = [r for r in rows if (r.get("generated_tokens") or 0) >= MIN_GEN
           and (r.get("decode_tps") or 0) > 0]
    for name, lo, hi in BUCKETS:
        b = [r for r in dec if lo <= (r.get("prompt_tokens") or 0) < hi]
        if b:
            out.append({"bucket": name, "n": len(b),
                        "median_decode_tps": round(statistics.median(r["decode_tps"] for r in b), 1),
                        "median_prompt": int(statistics.median(r["prompt_tokens"] for r in b))})
    return out


def fit_line(buckets):
    """Weighted least squares of y = 1000/decode on x = ctx/1000 over buckets
    with >= MIN_BUCKET_N replies.  None with fewer than two such buckets."""
    pts = [(b["median_prompt"] / 1000.0, 1000.0 / b["median_decode_tps"], b["n"])
           for b in buckets if b["n"] >= MIN_BUCKET_N and b["median_decode_tps"] > 0]
    if len(pts) < 2:
        return None
    sw = sum(w for _, _, w in pts)
    mx = sum(x * w for x, _, w in pts) / sw
    my = sum(y * w for _, y, w in pts) / sw
    sxx = sum(w * (x - mx) ** 2 for x, _, w in pts)
    if sxx <= 0:
        return None
    slope = sum(w * (x - mx) * (y - my) for x, y, w in pts) / sxx
    t0 = my - slope * mx
    return {"t0_ms": round(t0, 3), "slope_ms_per_1k": round(slope, 4), "points": len(pts)}


def single_bucket_fit(g, f, chip, buckets):
    """t0 from ONE short-context bucket — the shape of a decode_bench log, where every
    >= 64-token reply carries a prompt of a few dozen tokens.  The context slope cannot be
    measured from one bucket, so the estimator's own formula slope (0.48 ms x KV/32 KiB,
    scaled to this chip) is subtracted; at a prompt of < 8k tokens it is a few percent of
    the per-token time.  None unless a bucket under 8k tokens has >= MIN_BUCKET_N replies."""
    if not f or not chip.get("bandwidth_gbs") or not chip.get("e_chip"):
        return None
    short = [b for b in buckets if b["median_prompt"] < 8000 and b["n"] >= MIN_BUCKET_N
             and b["median_decode_tps"] > 0]
    if not short:
        return None
    b = max(short, key=lambda x: x["n"])
    bw, e = float(chip["bandwidth_gbs"]), float(chip["e_chip"])
    slope = g["_ADV_BETA"] * (g["_adv_kv_bytes"](f) / 32768.0) * (614.0 / bw) / e
    t0 = 1000.0 / b["median_decode_tps"] - slope * b["median_prompt"] / 1000.0
    if t0 <= 0:
        return None
    return {"t0_ms": round(t0, 3), "slope_ms_per_1k": round(slope, 4), "points": 1,
            "note": "one bucket (%s, n=%d): the slope is the formula's, not measured" % (b["bucket"], b["n"])}


def cold_prefill(rows):
    cold = [r for r in rows if (r.get("new_tokens") or 0) >= COLD_MIN_NEW and (r.get("prefill_s") or 0) > 0]
    if not cold:
        return None
    rates = [r["new_tokens"] / r["prefill_s"] for r in cold]
    # last_cold_prompt (the advisor's S) is filled in by analyse() from the advisor's OWN rule
    # (_adv_measured_from_rows: a streamed conversation turn of <= 32,000 tokens read cold), so
    # this script and the estimate can never disagree about what S is
    return {"n": len(cold), "median_tps": round(statistics.median(rates), 1),
            "median_new_tokens": int(statistics.median(r["new_tokens"] for r in cold)),
            "last_cold_prompt": None}


def pct(a, b):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not b:
        return None
    return round((a - b) / b * 100.0, 1)


def analyse(g, hw, rows, only=None):
    chip = hw.get("chip") or {}
    facts = g["_ADV_FACTS"]
    by_model = {}
    for r in rows:
        if r.get("model"):
            by_model.setdefault(r["model"], []).append(r)
    out = {}
    for mid, mrows in sorted(by_model.items()):
        if only and mid not in only:
            continue
        f = facts.get(mid)
        b = buckets_for(mrows)
        fit = fit_line(b) or single_bucket_fit(g, f, chip, b)
        rec = {"rows": len(mrows), "buckets": b, "fit": fit, "lanes": sorted({r.get("log") for r in mrows})}
        pre = cold_prefill(mrows)
        if pre:
            mm = g["_adv_measured_from_rows"](mid, mrows)
            pre["last_cold_prompt"] = (mm or {}).get("prefix_tokens")
        if pre and chip.get("p_index"):
            pre["ref_tps"] = round(pre["median_tps"] / float(chip["p_index"]), 1)
        rec["prefill"] = pre
        if f and fit and fit["t0_ms"] > 0 and chip.get("bandwidth_gbs"):
            gb = float(f.get("active_bytes_per_token") or f["weights_bytes"]) / 1e9
            e, bw = float(chip["e_chip"]), float(chip["bandwidth_gbs"])
            rec["k_effective"] = round(gb / (e * bw) * 1000.0 / fit["t0_ms"], 4)
            if fit["points"] >= 2:                       # a slope only a real fit can give
                rec["slope_ref_ms_per_1k"] = round(fit["slope_ms_per_1k"] * e * bw / 614.0, 4)
            mtp = f.get("mtp") or {}
            if mtp.get("measured") is True and (mtp.get("mult") or {}).get("agent"):
                rec["k_ar_implied"] = round(rec["k_effective"] / float(mtp["mult"]["agent"]), 4)
                rec["k_note"] = ("a drafter was running: k_effective includes its speed-up; "
                                 "k_ar_implied divides out the agent-mix multiplier")
        if f:
            rec["facts"] = {"k_decode": f["k_decode"], "ctx_slope_ms_per_1k": f.get("ctx_slope_ms_per_1k"),
                            "prefill_ref_tps": f.get("prefill_ref_tps"),
                            "delta_pct": {
                                "k": pct(rec.get("k_ar_implied") or rec.get("k_effective"), f["k_decode"]),
                                "slope": pct(rec.get("slope_ref_ms_per_1k"), f.get("ctx_slope_ms_per_1k")),
                                "prefill": pct((pre or {}).get("ref_tps"), f.get("prefill_ref_tps"))}}
            cmp_ = []
            for bk in b:
                est = g["_adv_decode_tps"](f, chip, bk["median_prompt"], ["chat", "agent"], "primary")
                cmp_.append({"bucket": bk["bucket"], "measured": bk["median_decode_tps"],
                             "estimate": round(est, 1), "error_pct": pct(est, bk["median_decode_tps"])})
            rec["estimate_vs_measured"] = cmp_
        else:
            rec["facts"] = None
            rec["note"] = "no advisor facts for this id: add a record to _ADV_FACTS first"
        out[mid] = rec
    return out


def table(doc):
    hw = doc["hw"]
    print("Mac: %s, %s GB, chip row %s (bw %s GB/s, e_chip %s, p_index %s)"
          % (hw.get("machine_name") or "?", hw.get("ram_gb"), hw.get("chip_row"),
             hw.get("bandwidth_gbs"), hw.get("e_chip"), hw.get("p_index")))
    for mid, r in doc["models"].items():
        print("\n== %s  (%d rows)" % (mid, r["rows"]))
        for b in r["buckets"]:
            print("   %-7s n=%-4d decode %6.1f tok/s  median prompt %d"
                  % (b["bucket"], b["n"], b["median_decode_tps"], b["median_prompt"]))
        if r.get("fit") and r["fit"]["points"] >= 2:
            print("   fit: t0 %.2f ms, slope %.3f ms/1k (%d buckets)"
                  % (r["fit"]["t0_ms"], r["fit"]["slope_ms_per_1k"], r["fit"]["points"]))
        elif r.get("fit"):
            print("   fit: t0 %.2f ms (%s)" % (r["fit"]["t0_ms"], r["fit"].get("note")))
        if r.get("k_effective"):
            print("   k_effective %.4f%s   %s"
                  % (r["k_effective"], ("  (k_ar_implied %.4f)" % r["k_ar_implied"]) if r.get("k_ar_implied") else "",
                     ("slope at the reference %.4f ms/1k" % r["slope_ref_ms_per_1k"])
                     if r.get("slope_ref_ms_per_1k") is not None else "no slope: it needs two context buckets"))
        if r.get("prefill"):
            p = r["prefill"]
            print("   cold prefill: n=%d median %.0f tok/s (ref %s), last cold prompt %s"
                  % (p["n"], p["median_tps"], p.get("ref_tps"), p.get("last_cold_prompt")))
        if r.get("facts"):
            print("   facts: k %s, slope %s, prefill %s  ->  delta %% %s"
                  % (r["facts"]["k_decode"], r["facts"]["ctx_slope_ms_per_1k"],
                     r["facts"]["prefill_ref_tps"], r["facts"]["delta_pct"]))
            for c in r.get("estimate_vs_measured") or []:
                print("   estimate %-7s %6.1f vs measured %6.1f  (%s%%)"
                      % (c["bucket"], c["estimate"], c["measured"], c["error_pct"]))
        elif r.get("note"):
            print("   " + r["note"])


def main(argv=None, env=None, pmset_text=None, hw=None):
    ap = argparse.ArgumentParser(description="Measure the model advisor's constants from this "
                                             "Mac's model-server logs (see tools/tests/ac/advisor-calibration.md).")
    ap.add_argument("--log", action="append", help="a model-server log to read (repeatable; default "
                                                   "~/.hermes/logs/mlx-server.log and mlx-bg.log)")
    ap.add_argument("--model", action="append", help="only this model id (repeatable)")
    ap.add_argument("--tail-mb", type=float, default=TAIL_BYTES / 1024 / 1024,
                    help="read at most this many MB from the end of each log (default 8)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--i-am-on-ac", action="store_true",
                    help="only for a Mac whose pmset reports nothing; never overrides Battery Power")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, read nothing")
    args = ap.parse_args(argv)
    home = os.path.expanduser("~")
    logs = args.log or [os.path.join(home, ".hermes", "logs", "mlx-server.log"),
                        os.path.join(home, ".hermes", "logs", "mlx-bg.log")]
    if args.dry_run:
        print("[dry-run] would read (tail %.0f MB each): %s" % (args.tail_mb, ", ".join(logs)))
        print("[dry-run] would detect this Mac through dashboard/aux_advisor_hw.py (sysctl, ioreg, "
              "pmset, system_profiler, the Metal probe) and fit k / slope / prefill per model.")
        print("[dry-run] nothing is loaded, woken, contacted or written.")
        return 0
    ok, why = power_gate(read_pmset() if pmset_text is None else pmset_text, env, args.i_am_on_ac)
    if not ok:
        log("advisor_calibrate: " + why)
        return 2
    log("advisor_calibrate: %s" % why)
    g = load_modules(home)
    if hw is None:
        hw = g["_adv_hw"]()
    rows = []
    for p in logs:
        p = os.path.expanduser(p)
        if not os.path.exists(p):
            log("advisor_calibrate: no log at %s (skipped)" % p)
            continue
        rows.extend(read_rows(g, p, int(args.tail_mb * 1024 * 1024)))
    chip = hw.get("chip") or {}
    doc = {"hw": {"machine_name": hw.get("machine_name"), "ram_gb": hw.get("ram_gb"),
                  "chip_row": chip.get("row"), "bandwidth_gbs": chip.get("bandwidth_gbs"),
                  "e_chip": chip.get("e_chip"), "p_index": chip.get("p_index"),
                  "confidence": chip.get("confidence"), "macos": hw.get("macos")},
           "logs": logs, "rows": len(rows), "models": analyse(g, hw, rows, set(args.model or ()))}
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    else:
        table(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
