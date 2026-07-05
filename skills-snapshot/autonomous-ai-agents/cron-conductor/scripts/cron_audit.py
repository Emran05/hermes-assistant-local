#!/usr/bin/env python3
"""cron_audit.py — read-only fleet audit for Hermes cron jobs.

Joins:
  1. ~/.hermes/cron/jobs.json         (source of truth behind `hermes cron list`)
  2. ~/.hermes/memories/cron-ledger.md (§-delimited ledger entries)
  3. ~/.hermes/cron/output/<id>/       (recent run outputs; [SILENT] discipline)
  4. http://127.0.0.1:7788/api/recorder + /api/metrics (best-effort context)

Emits a markdown keep/kill/reschedule health table on stdout.
Stdlib only. NEVER modifies anything.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
JOBS_FILE = os.path.join(HERMES_HOME, "cron", "jobs.json")
OUTPUT_DIR = os.path.join(HERMES_HOME, "cron", "output")
LEDGER_FILE = os.path.join(HERMES_HOME, "memories", "cron-ledger.md")
DASH = "http://127.0.0.1:7788"
QUIET_START, QUIET_END = 22, 7  # 10:00 PM – 7:00 AM

SILENCE = re.compile(r"^\s*(\[SILENT\]|SILENT|NO[_ ]REPLY)\s*$", re.I | re.M)


def load_jobs():
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("jobs", [])
    except (OSError, ValueError):
        return []


def load_ledger():
    """Return {job_id: entry_text} parsed from § -delimited ledger entries."""
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    entries = {}
    for chunk in re.split(r"\n§\n|^§$", text, flags=re.M):
        chunk = chunk.strip()
        m = re.search(r"cron:([A-Za-z0-9._-]+)", chunk)
        if m:
            entries[m.group(1)] = chunk
    return entries


def recent_outputs(job_id, n=5):
    """Newest-first list of (filename, text) for a job's last n run outputs."""
    d = os.path.join(OUTPUT_DIR, job_id)
    try:
        files = sorted(
            (f for f in os.listdir(d) if not f.startswith(".")), reverse=True
        )[:n]
    except OSError:
        return []
    out = []
    for name in files:
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8",
                      errors="replace") as f:
                out.append((name, f.read()))
        except OSError:
            continue
    return out


def http_json(path, timeout=3):
    try:
        with urllib.request.urlopen(DASH + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def fmt12(iso):
    """ISO timestamp -> local 12-hour string."""
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone()
        s = dt.strftime("%b %d %I:%M %p")
        return re.sub(r"(^\w+ )0?(\d+) 0?(\d+:)", r"\1\2 \3", s)
    except ValueError:
        return str(iso)


def sched_hour(job):
    """Best-effort firing hour for quiet-hours + overlap checks."""
    nxt = job.get("next_run_at")
    if nxt:
        try:
            dt = datetime.fromisoformat(str(nxt).replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone()
            return dt.hour, dt.strftime("%H:%M")
        except ValueError:
            pass
    return None, None


def in_quiet_hours(hour):
    return hour is not None and (hour >= QUIET_START or hour < QUIET_END)


def audit():
    jobs = load_jobs()
    ledger = load_ledger()
    recorder = http_json("/api/recorder?limit=200") or {}
    metrics = http_json("/api/metrics") or {}

    # overlap map: fire-minute -> [job ids]
    fire = {}
    for j in jobs:
        _, hm = sched_hour(j)
        if hm:
            fire.setdefault(hm, []).append(j.get("id"))

    lines = []
    lines.append("# Cron Fleet Audit — %s" %
                 re.sub(r" 0(\d:)", r" \1",
                        datetime.now().strftime("%b %d, %Y %I:%M %p")))
    lines.append("")
    lines.append("Sources: jobs.json (%d job%s), ledger (%d entr%s), "
                 "run outputs, recorder %s, metrics %s." % (
                     len(jobs), "" if len(jobs) == 1 else "s",
                     len(ledger), "y" if len(ledger) == 1 else "ies",
                     "ok" if recorder else "unreachable",
                     "ok" if metrics else "unreachable"))
    lines.append("")

    if not jobs:
        lines.append("**No cron jobs exist.** Fleet is empty.")
        orphans = set(ledger) - set()
        if orphans:
            lines.append("")
            lines.append("Ledger entries with no live job (retired or stale): "
                         + ", ".join("`%s`" % o for o in sorted(orphans)))
        return "\n".join(lines)

    lines.append("| Job | Schedule | Last run | Status | Ledger | Verdict | Why |")
    lines.append("|---|---|---|---|---|---|---|")

    for j in jobs:
        jid = str(j.get("id", "?"))
        name = j.get("name") or jid
        sched = j.get("schedule_display") or str(j.get("schedule", "?"))
        last_run = fmt12(j.get("last_run_at"))
        status = j.get("last_status") or ("paused" if not j.get("enabled", True)
                                          else "never-run")
        outs = recent_outputs(jid)
        fails = 0
        for _, txt in outs:
            if re.search(r"\b(error|failed|traceback)\b", txt[:400], re.I) \
                    and not SILENCE.search(txt):
                fails += 1
            else:
                break  # consecutive from newest only
        if j.get("last_status") == "error":
            fails = max(fails, 1)

        reasons, verdict = [], "KEEP"
        ledgered = jid in ledger or any(
            name and name in e for e in ledger.values())
        if not j.get("enabled", True):
            verdict = "KILL?"
            reasons.append("paused (%s)" % (j.get("paused_reason") or "no reason"))
        if fails >= 3:
            verdict = "KILL?"
            reasons.append("zombie: %d consecutive failing runs" % fails)
        elif j.get("last_status") == "error":
            reasons.append("last run errored: %s" %
                           (str(j.get("last_error"))[:60] or "unknown"))
        if not ledgered:
            if verdict == "KEEP":
                verdict = "KILL?"
            reasons.append("orphan: no cron-ledger.md entry")
        hour, hm = sched_hour(j)
        if in_quiet_hours(hour):
            if verdict == "KEEP":
                verdict = "RESCHEDULE?"
            reasons.append("fires in quiet hours (10 PM-7 AM)")
        if hm and len(fire.get(hm, [])) > 1:
            if verdict == "KEEP":
                verdict = "RESCHEDULE?"
            others = [o for o in fire[hm] if o != jid]
            reasons.append("overlaps %s at %s" % (", ".join(map(str, others)), hm))
        if not j.get("last_run_at") and verdict == "KEEP":
            verdict = "UNKNOWN"
            reasons.append("never run — do a manual `hermes cron run` first")
        if verdict == "KEEP" and not reasons:
            reasons.append("healthy + ledgered")

        lines.append("| %s (`%s`) | %s | %s | %s | %s | **%s** | %s |" % (
            name, jid, sched, last_run, status,
            "yes" if ledgered else "NO", verdict, "; ".join(reasons)))

    stale = sorted(set(ledger) - {str(j.get("id")) for j in jobs})
    if stale:
        lines.append("")
        lines.append("Ledger entries with no live job (retired or stale): "
                     + ", ".join("`%s`" % s for s in stale))

    ram = (metrics or {}).get("ram")
    if ram:
        lines.append("")
        lines.append("RAM context (from /api/metrics): `%s`" %
                     json.dumps(ram)[:200])
    lines.append("")
    lines.append("_Read-only report. Propose KILL?/RESCHEDULE? rows to the "
                 "user; apply only approved changes._")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        print(audit())
    except Exception as e:
        print("cron_audit failed: %r" % e, file=sys.stderr)
        sys.exit(1)
