#!/usr/bin/env python3
"""C18.1 — the brief's Needs-you line says when a source is not being read.

BEFORE: _wt_needsyou_line printed nothing when both counts were 0 and never
looked at the payload's source_notes, so on a Mac without Full Disk Access
the morning brief, the midday pulse and the evening wrap read exactly like
"nothing needs you" — the false calm C18 fixed in the widget.
AFTER: a source that is absent FOR A REASON (source_notes) and not switched
off by the owner (source_off) is named: "Needs you: 0 now, 0 today (Messages
is not being read)." — the same rule as the widget's empty state.

The payload is the REAL aux_needsyou build (see _ny_env.py) with Full Disk
Access denied, handed to the REAL watchtower line (see _wt_harness.py).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E      # noqa: E402
import _wt_harness as H  # noqa: E402

check = H.check
NOW = time.time()


def main():
    print("C18.1: a blind Needs-you source is named in the brief")
    ny, ny_data = E.load()
    E.write_messages(ny_data, [], fda=False, generated_at=NOW - 7.7 * 86400)
    ny["_ny_rem_fetch"] = lambda: {"ok": False, "error": "Apple Reminders is off in Settings"}
    real = ny["_ny_build"](NOW)
    check("fixture: the real payload says Messages needs Full Disk Access",
          (real.get("source_notes") or {}).get("message") == "Full Disk Access needed"
          and not real.get("now") and not real.get("today"), real.get("source_notes"))

    g, _home, _data = H.load()
    marks = []

    def use(payload):
        def f(mark=True):
            marks.append(mark)
            return payload
        g["_ny_payload"] = f

    use(real)
    line = g["_wt_needsyou_line"]()
    check("THE FIX: 0/0 with no Full Disk Access is not silence",
          line.strip() != "", repr(line))
    check("... it names Messages as not being read",
          "Messages is not being read" in line and "0 now" in line, repr(line))
    check("... and not Reminders, which the owner switched off",
          "Reminders" not in line, repr(line))
    check("... nor Mail, absent by design (no note)", "Mail" not in line, repr(line))
    check("the brief still never marks a sighting (mark=False)",
          marks and all(m is False for m in marks), marks)

    base = {"ok": True, "now": [], "today": [], "later": []}
    use(dict(base, now=[{}, {}], today=[{}],
             sources={"message": "absent", "calendar": "present"},
             source_notes={"message": "Messages sync is 3 h old"}))
    check("counts and the blind source together",
          g["_wt_needsyou_line"]() == "\nNeeds you: 2 now, 1 today (Messages is not being read).\n",
          repr(g["_wt_needsyou_line"]()))
    use(dict(base, sources={"message": "absent", "calendar": "absent", "reminder": "absent"},
             source_notes={"message": "Full Disk Access needed", "calendar": "denied",
                           "reminder": "not granted"}))
    check("several blind sources, in the widget's order",
          "(Messages, Calendar and Reminders are not being read)" in g["_wt_needsyou_line"](),
          repr(g["_wt_needsyou_line"]()))
    use(dict(base, sources={"message": "present"}, source_notes={"message": "stale note"}))
    check("a present source is never called blind", g["_wt_needsyou_line"]() == "",
          repr(g["_wt_needsyou_line"]()))
    use(dict(base, sources={"reminder": "absent"},
             source_notes={"reminder": "Apple Reminders is off in Settings"},
             source_off=["reminder"]))
    check("switched off on purpose + nothing waiting -> nothing appended (unchanged)",
          g["_wt_needsyou_line"]() == "", repr(g["_wt_needsyou_line"]()))
    use(dict(base, now=[{}]))
    check("no blind source -> the old line, unchanged",
          g["_wt_needsyou_line"]() == "\nNeeds you: 1 now, 0 today.\n",
          repr(g["_wt_needsyou_line"]()))
    use(dict(base))
    check("nothing to say and nothing blind -> nothing appended (unchanged)",
          g["_wt_needsyou_line"]() == "", repr(g["_wt_needsyou_line"]()))

    # the line rides in every rhythm text
    use(real)
    comp = g["_brief_compose"](run_synthesis=False)
    check("the morning brief carries it", "Messages is not being read" in comp["text"],
          comp["text"][-120:])
    check("the midday pulse carries it",
          "Messages is not being read" in g["_midday_compose"](g["_wt_load"]())["text"])
    check("the evening wrap carries it",
          "Messages is not being read" in g["_evening_compose"](g["_wt_load"]())["text"])
    H.finish()


if __name__ == "__main__":
    main()
