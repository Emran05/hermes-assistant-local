#!/usr/bin/env python3
"""task_audit.py - flight-recorder audit for a time window.

Pulls /api/recorder from the local dashboard (which has NO server-side time
filter: we paginate with before=<id> and filter by ts client-side), then
prints a readable diff-style report of every recorded action in the window,
flagging irreversible-marked rows and which rows are undo-eligible.

stdlib only. Read-only: performs GETs against 127.0.0.1 only.

Usage:
  task_audit.py --minutes 60
  task_audit.py --start 1783260000 --end 1783270000
  task_audit.py --minutes 1440 --json
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

DASH = os.environ.get("HERMES_DASH_URL", "http://127.0.0.1:7788").rstrip("/")
UNDO_KINDS = {"write", "shell"}   # mirrors aux_recorder UNDO_WHITELIST
READ_KINDS = {"read", "net"}      # no side effects on the machine
MAX_PAGES = 50                    # 50 * 200 = 10k rows hard stop


def fetch_json(path):
    req = urllib.request.Request(DASH + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get_actions(start_ts, end_ts):
    """Newest-first pages until we cross start_ts; returns rows in window."""
    out, before = [], None
    for _ in range(MAX_PAGES):
        q = "/api/recorder?limit=200" + ("&before=%d" % before if before else "")
        data = fetch_json(q)
        acts = data.get("actions") or []
        if not acts:
            break
        for a in acts:
            ts = a.get("ts") or 0
            if ts < start_ts:
                return out
            if ts <= end_ts:
                out.append(a)
        before = acts[-1].get("id")
        if before is None:
            break
    return out


def t12(ts):
    """Epoch -> local 12-hour timestamp like '9:06:12 PM'."""
    dt = datetime.datetime.fromtimestamp(ts)
    s = dt.strftime("%I:%M:%S %p")
    return s.lstrip("0")


def d12(ts):
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%a %b %d, ") + t12(ts)


def is_irreversible(a):
    """Side-effecting action the recorder marks non-reversible (no snapshot)."""
    return (a.get("kind") not in READ_KINDS and
            (a.get("reversible") or "").lower() == "no")


def classify(a):
    flags = []
    if a.get("kind") in READ_KINDS:
        flags.append("[read-only]")
    elif is_irreversible(a):
        flags.append("[IRREVERSIBLE]")
    elif a.get("kind") in UNDO_KINDS:
        flags.append("[undoable]")
    st = a.get("status") or ""
    if st == "undone":
        flags.append("[UNDONE]")
    elif st == "pending":
        flags.append("[pending-approval]")
    elif st not in ("done", ""):
        flags.append("[%s]" % st)
    return flags


def trim(s, n=100):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    p = argparse.ArgumentParser(description="Flight-recorder audit for a time window")
    p.add_argument("--minutes", type=float, default=60.0,
                   help="window ending now (default 60)")
    p.add_argument("--start", type=float, default=None, help="epoch start (overrides --minutes)")
    p.add_argument("--end", type=float, default=None, help="epoch end (default: now)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args()

    now = datetime.datetime.now().timestamp()
    end_ts = args.end if args.end is not None else now
    start_ts = args.start if args.start is not None else end_ts - args.minutes * 60.0

    try:
        actions = get_actions(start_ts, end_ts)
    except (urllib.error.URLError, OSError, ValueError) as e:
        msg = "recorder unreachable at %s: %s" % (DASH, e)
        if args.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print("AUDIT FAILED: " + msg)
        sys.exit(2)

    actions.sort(key=lambda a: a.get("ts") or 0)  # oldest first, chronological
    irreversible = [a for a in actions if is_irreversible(a)]
    undoable = [a for a in actions
                if a.get("kind") in UNDO_KINDS and (a.get("reversible") or "").lower() != "no"]

    def suspect(a):
        summ = a.get("summary") or ""
        if (a.get("status") or "") not in ("done", ""):
            return True
        if "[Tool loop warning" in summ:
            return True
        if a.get("kind") == "shell" and '"exit_code"' in summ \
                and '"exit_code": 0' not in summ:
            return True
        return False

    failures = [a for a in actions if suspect(a)]

    if args.json:
        print(json.dumps({
            "ok": True,
            "window": {"start": start_ts, "end": end_ts,
                       "start_h": d12(start_ts), "end_h": d12(end_ts)},
            "counts": {"total": len(actions), "irreversible": len(irreversible),
                       "undoable": len(undoable), "suspect": len(failures)},
            "actions": actions,
            "irreversible_ids": [a.get("id") for a in irreversible],
            "suspect_ids": [a.get("id") for a in failures],
        }, indent=2))
        return

    print("FLIGHT-RECORDER AUDIT")
    print("window : %s  ->  %s" % (d12(start_ts), d12(end_ts)))
    print("actions: %d total | %d irreversible-marked | %d undo-eligible | %d suspect"
          % (len(actions), len(irreversible), len(undoable), len(failures)))
    print("-" * 78)
    if not actions:
        print("(no recorded actions in this window)")
        return
    for a in actions:
        flags = " ".join(classify(a))
        print("#%-5s %-11s %-9s %-14s %s" % (
            a.get("id"), t12(a.get("ts") or 0), a.get("source") or "?",
            a.get("tool") or a.get("kind") or "?", flags))
        print("       target : %s" % trim(a.get("target"), 90))
        summ = trim(a.get("summary"), 90)
        if summ:
            print("       result : %s" % summ)
    print("-" * 78)
    if irreversible:
        print("IRREVERSIBLE ACTIONS TO CHECK AGAINST INTENT:")
        for a in irreversible:
            print("  #%s %s %s -> %s" % (a.get("id"), t12(a.get("ts") or 0),
                                         a.get("tool"), trim(a.get("target"), 70)))
    else:
        print("No irreversible-marked actions in this window.")


if __name__ == "__main__":
    main()
