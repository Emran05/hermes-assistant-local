#!/usr/bin/env python3
"""vitals_snapshot.py - current vitals + week-over-week deltas as JSON.

Reads /api/metrics (TTFT, RAM-by-footprint, approvals, targets) and
/api/mind_drill?days=14 (daily token/session buckets) from the local
dashboard, computes this-week vs last-week deltas, and flags target breaches.

RAM note: the dashboard samples via `footprint`, NOT ps RSS - ps badly
under-reports MLX/Metal unified memory. Trust these numbers over ps.

stdlib only. Read-only GETs against 127.0.0.1 only.

Usage: vitals_snapshot.py [--pretty]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DASH = os.environ.get("HERMES_DASH_URL", "http://127.0.0.1:7788").rstrip("/")


def fetch_json(path):
    req = urllib.request.Request(DASH + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pct_delta(cur, prev):
    if not prev:
        return None
    return round(100.0 * (cur - prev) / prev, 1)


def week_split(rows, key):
    """rows = [{'d': 'YYYY-MM-DD', key: n}, ...] oldest-first, 14 entries."""
    vals = [r.get(key) or 0 for r in rows]
    prev, this = vals[:-7], vals[-7:]
    return sum(prev), sum(this)


def main():
    ap = argparse.ArgumentParser(description="Vitals + week-over-week deltas")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    out = {"generated": time.time(), "ok": True, "errors": []}

    try:
        m = fetch_json("/api/metrics")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": "metrics unreachable: %s" % e}))
        sys.exit(2)

    turns = m.get("turns") or {}
    ttft = turns.get("ttft_ms") or {}
    ram = m.get("ram") or {}
    targets = m.get("targets") or {}
    out["metrics"] = {
        "ttft_p50_ms": ttft.get("p50"), "ttft_p95_ms": ttft.get("p95"),
        "turn_p50_ms": (turns.get("turn_ms") or {}).get("p50"),
        "tok_per_sec_p50": (turns.get("est_tok_per_sec") or {}).get("p50"),
        "turns_measured": ttft.get("n"),
        "ram_last_gb": (ram.get("last") or {}).get("gb"),
        "ram_state": (ram.get("last") or {}).get("state"),
        "ram_idle_p95_gb": ram.get("idle_gb_p95"),
        "ram_semantics": "footprint (unified memory) - NOT ps RSS",
        "model_active": (m.get("model") or {}).get("active"),
        "model_loads": (m.get("model") or {}).get("loads"),
        "approvals": m.get("approvals"),
        "undo": m.get("undo"),
        "targets": targets,
    }

    # breaches vs targets
    breaches = []
    p50, p95 = ttft.get("p50"), ttft.get("p95")
    if p50 is not None and targets.get("ttft_p50_ms") and p50 > targets["ttft_p50_ms"]:
        breaches.append("ttft_p50 %.0fms > target %dms" % (p50, targets["ttft_p50_ms"]))
    if p95 is not None and targets.get("ttft_p95_ms") and p95 > targets["ttft_p95_ms"]:
        breaches.append("ttft_p95 %.0fms > target %dms" % (p95, targets["ttft_p95_ms"]))
    idle = ram.get("idle_gb_p95")
    model = ((m.get("model") or {}).get("active") or "").lower()
    is_moe = ("a3b" in model) or ("moe" in model) or ("30b" in model) or ("air" in model)
    idle_target = targets.get("moe_idle_gb") if is_moe else targets.get("idle_gb")
    if idle is not None and idle_target and idle > idle_target:
        breaches.append("ram_idle_p95 %.1fGB > target %dGB" % (idle, idle_target))
    out["breaches"] = breaches

    # week-over-week from mind_drill
    try:
        d = fetch_json("/api/mind_drill?days=14")
        tok_prev, tok_this = week_split(d.get("tokens_by_day") or [], "in_tok")
        otok_prev, otok_this = week_split(d.get("tokens_by_day") or [], "out_tok")
        ses_prev, ses_this = week_split(d.get("sessions_by_day") or [], "n")
        out["week_over_week"] = {
            "in_tokens": {"this_week": tok_this, "prev_week": tok_prev,
                          "delta_pct": pct_delta(tok_this, tok_prev)},
            "out_tokens": {"this_week": otok_this, "prev_week": otok_prev,
                           "delta_pct": pct_delta(otok_this, otok_prev)},
            "sessions": {"this_week": ses_this, "prev_week": ses_prev,
                         "delta_pct": pct_delta(ses_this, ses_prev)},
        }
        out["skill_usage_top"] = (d.get("skill_usage") or [])[:5]
        out["model_mix"] = d.get("model_mix")
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["errors"].append("mind_drill unreachable: %s" % e)

    print(json.dumps(out, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
