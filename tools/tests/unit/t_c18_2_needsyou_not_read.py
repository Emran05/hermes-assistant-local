#!/usr/bin/env python3
"""C18.2 (k06 review of C18's limits): two ways the Needs-you inbox stayed
quiet about a source it could not read.

BEFORE:
  * Reminders without Automation access (osascript refused, no fallback)
    was marked absent with NO reason, so blindNames() never named it — the
    empty state still said "Nothing needs you right now";
  * the blind-source wording lived only in the EMPTY state's headline and
    the pop-out's chips, so a widget with even one row never said that
    Messages (no Full Disk Access) was not being read.

AFTER: the not-granted path carries "Reminders access needed" (a failed
read "Reminders could not be read"), never marked as the owner's own `off`;
and every non-empty render adds one line, "Not being read: Messages (Full
Disk Access needed) · Reminders (access needed)", for the same sources the
headline would name — owner-off sources excepted.

Real aux_needsyou.py / aux_needsyou.js; node renders the widget offline.
Against the pre-fix files the reason and the line are both missing.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()

print("=== C18.2: Reminders not granted carries a reason ===")
for label, fetched, want, off in (
        ("not granted", {"available": False, "grant": True},
         "Reminders access needed", False),
        ("read failed", {"available": False}, "Reminders could not be read", False),
        ("(control) off in Settings",
         {"ok": False, "error": "Apple Reminders is off in Settings"},
         "Apple Reminders is off in Settings", True)):
    g["_ny_rem_fetch"] = (lambda v: (lambda: v))(fetched)
    p = g["_ny_build"](NOW)
    notes = p.get("source_notes") or {}
    E.check("%s -> absent" % label, p["sources"].get("reminder") == "absent",
            p["sources"])
    E.check("%s -> reason %r" % (label, want), notes.get("reminder") == want, notes)
    E.check("%s -> %s" % (label, "owner's choice" if off else "NOT the owner's choice"),
            ("reminder" in (p.get("source_off") or [])) == off, p.get("source_off"))

print("=== C18.2: the widget says what it cannot read (node) ===")
JS = r"""
// esc() is the REAL one, cut out of index.html's inline script (E138: a stand-in
// tests a different escaping from the one the dashboard ships)
globalThis.esc = require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
globalThis.relTime = function () { return "2m ago"; };
globalThis.RENDER = {}; globalThis.EXPAND_RENDER = {}; globalThis.WICONS = {};
globalThis.window = globalThis;
globalThis.setTimeout = function () { return 0; };
globalThis.fetch = function () { return new Promise(function () {}); };
const fs = require("fs");
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
function el() {
  return { innerHTML: "", textContent: "",
           querySelectorAll: function () { return []; },
           getClientRects: function () { return []; } };
}
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
for (const [k, d] of Object.entries(cases)) {
  const b = el(), m = el();
  if (k.indexOf("pop_") === 0) EXPAND_RENDER.needsyou(b, d);
  else RENDER.needsyou(b, d, m);
  out[k] = b.innerHTML;
}
process.stdout.write(JSON.stringify(out));
"""
row = {"id": "calendar:1", "source": "calendar", "title": "Design review",
       "sender": "Calendar", "bucket": "now", "deadline_ts": NOW + 1800,
       "reason": "starts within the next two hours"}
base = {"ok": True, "now": [row], "today": [], "later": [], "never_count": 0,
        "metrics": {}, "next_brief": None, "generated_at": NOW}
blind = dict(base, sources={"message": "absent", "reminder": "absent",
                            "calendar": "present", "email": "absent"},
             source_notes={"message": "Full Disk Access needed",
                           "reminder": "Reminders access needed"})
cases = {
    "blind": blind,
    "pop_blind": blind,
    "only_off": dict(base, sources={"message": "present", "reminder": "absent"},
                     source_notes={"reminder": "Apple Reminders is off in Settings"},
                     source_off=["reminder"]),
    "fine": dict(base, sources={"message": "present", "email": "absent"},
                 source_notes={}),
    "markup": dict(base, sources={"message": "absent"},
                   source_notes={"message": "<img src=x onerror=alert(1)>"}),
    "empty": dict(blind, now=[]),
}
tmp = tempfile.mkdtemp(prefix="ny-c182-")
E._HOMES.append(tmp)
hj, cj = os.path.join(tmp, "h.js"), os.path.join(tmp, "c.json")
with open(hj, "w") as f:
    f.write(JS)
with open(cj, "w") as f:
    json.dump(cases, f)
r = subprocess.run(["node", hj, os.path.join(E.DASH, "aux_needsyou.js"), cj],
                   capture_output=True, text=True, timeout=60)
try:
    html = json.loads(r.stdout)
except ValueError:
    html = {}
    E.check("renderer ran", False, r.stderr[-400:])
if html:
    want = ("Not being read: Messages (Full Disk Access needed) · "
            "Reminders (access needed)")
    E.check("a non-empty widget names both blind sources", want in html["blind"],
            html["blind"][-600:])
    E.check("...and so does the pop-out", want in html["pop_blind"],
            html["pop_blind"][-900:])
    E.check("...with the row still rendered", "Design review" in html["blind"])
    E.check("an owner-off source is not called 'not being read'",
            "Not being read" not in html["only_off"], html["only_off"][-400:])
    E.check("healthy sources -> no line", "Not being read" not in html["fine"])
    E.check("the note is escaped, never markup",
            "<img" not in html["markup"] and "&lt;img" in html["markup"],
            html["markup"][-400:])
    E.check("the empty state keeps its headline and adds no second line",
            "but Messages and Reminders are not being read" in html["empty"]
            and "Not being read:" not in html["empty"], html["empty"][:400])

sys.exit(E.finish())
