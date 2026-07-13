#!/usr/bin/env python3
"""obsidian_daily.py — file the day's World Brief into a dated Obsidian note
(<vault>/Daily/YYYY-MM-DD.md), so the vault becomes a searchable life-log
alongside the code graph.

Idempotent + non-destructive: rewrites only the delimited Hermes block, so
anything YOU add to the daily note survives. Fired by a launchd WatchPaths
agent whenever the brief regenerates.

Usage: obsidian_daily.py <vault_dir> [briefing_json]
"""
import json
import os
import re
import sys
import time

BRIEF_DEFAULT = os.path.expanduser("~/.hermes/dashboard/briefing.json")
START = "%% hermes:brief:start %%"
END = "%% hermes:brief:end %%"


def _t12(ts):
    """12-hour clock, no leading zero (e.g. '8:15 AM')."""
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")


def _block(brief):
    gen = brief.get("generated_at")
    when = _t12(gen) if gen else "today"
    body = (brief.get("reply") or "").strip()
    return START + "\n> Filed by Hermes at " + when + "\n\n" + body + "\n" + END


def build(vault, briefing_json):
    try:
        brief = json.load(open(briefing_json))
    except Exception as e:
        print("no briefing (%s); skipping" % e, file=sys.stderr)
        return None
    if not (brief.get("reply") or "").strip():
        print("briefing empty; skipping", file=sys.stderr)
        return None
    ts = brief.get("generated_at")
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    ddir = os.path.join(vault, "Daily")
    os.makedirs(ddir, exist_ok=True)
    path = os.path.join(ddir, day + ".md")
    block = _block(brief)
    if os.path.exists(path):
        txt = open(path).read()
        if START in txt and END in txt:      # replace just our block, keep the rest
            txt = re.sub(re.escape(START) + r".*?" + re.escape(END),
                         lambda _m: block, txt, flags=re.S)
        else:
            txt = txt.rstrip() + "\n\n" + block + "\n"
    else:
        txt = "---\ndate: %s\ntags: [hermes/daily]\n---\n\n# %s\n\n" % (day, day) + block + "\n"
    with open(path, "w") as f:
        f.write(txt)
    return path


def main():
    if len(sys.argv) < 2:
        print("usage: obsidian_daily.py <vault_dir> [briefing_json]", file=sys.stderr)
        return 2
    vault = os.path.expanduser(sys.argv[1])
    briefing = sys.argv[2] if len(sys.argv) > 2 else BRIEF_DEFAULT
    if not os.path.isdir(vault):
        print("vault dir not found: %s" % vault, file=sys.stderr)
        return 1
    path = build(vault, briefing)
    if path:
        print("Filed brief -> %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
