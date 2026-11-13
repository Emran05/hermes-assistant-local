#!/usr/bin/env python3
"""C18 (docs/audits/2026-10-21-codebase-audit.md): Needs-you reported the
Messages source as present while blind, and the empty state said "Nothing
needs you right now".

BEFORE: _ny_collect_message marked Messages "present" as soon as
messages.json parsed, then returned [] because fda was false; it never looked
at how old the sync was.  With Full Disk Access denied (the state after every
app rebuild) or the app not running for days, the hub's first widget read
"Nothing needs you right now" over a source nobody was reading.

AFTER (contract shared with k07's MCP change): `sources` values stay
"present"/"absent"; Messages is present only with Full Disk Access AND a sync
younger than NY_MSG_STALE_X x MSG_STALE_S; the payload carries
`source_notes` {source: "Full Disk Access needed" | "Messages sync is N h
old" | ...}; and the widget's empty state reads "Nothing needs you — but
Messages is not being read", with the note on the source chip.

Python half: the REAL collector and build.  JS half: the REAL
aux_needsyou.js renderer, eval'd in node with a stub element.
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

print("=== C18: Messages is present only while it is being read ===")
g, data = E.load()
srcs = {}
E.write_messages(data, [], fda=False, generated_at=NOW - 7.7 * 86400)
E.check("FDA denied -> no rows", g["_ny_collect_message"](srcs, None, NOW) == [])
E.check("FDA denied -> absent", srcs.get("message") == "absent", srcs)
E.check("FDA denied -> the reason says so",
        srcs.get("message_reason") == "Full Disk Access needed", srcs)

srcs = {}
E.write_messages(data, [E.convo("+15550000051", "hey", NOW - 3600)],
                 generated_at=NOW - 7.7 * 86400)
rows = g["_ny_collect_message"](srcs, None, NOW)
E.check("stale sync -> absent", srcs.get("message") == "absent", srcs)
E.check("stale sync -> 'Messages sync is 7 days old'",
        srcs.get("message_reason") == "Messages sync is 7 days old", srcs)
E.check("stale rows are still triaged (dropping them is a silent miss)",
        len(rows) == 1, rows)

srcs = {}
E.write_messages(data, [], generated_at=NOW - 3 * 3600)
g["_ny_collect_message"](srcs, None, NOW)
E.check("hours-old sync -> 'Messages sync is 3 h old'",
        srcs.get("message_reason") == "Messages sync is 3 h old", srcs)

srcs = {}
E.write_messages(data, [], generated_at=NOW - 60)
g["_ny_collect_message"](srcs, None, NOW)
E.check("fresh sync with FDA -> present", srcs.get("message") == "present", srcs)
E.check("...and no note", "message_reason" not in srcs, srcs)

for bad in ("NaN", "Infinity"):
    srcs = {}
    with open(os.path.join(data, "messages.json"), "w") as f:
        f.write('{"v": 1, "fda": true, "generated_at": %s, "stored_at": %s, '
                '"conversations": []}' % (bad, bad))
    g["_ny_collect_message"](srcs, None, NOW)
    E.check("generated_at=%s is not a fresh sync" % bad,
            srcs.get("message") == "absent" and srcs.get("message_reason"), srcs)

os.remove(os.path.join(data, "messages.json"))
srcs = {}
g["_ny_collect_message"](srcs, None, NOW)
E.check("never synced -> absent with a reason",
        srcs.get("message") == "absent" and srcs.get("message_reason"), srcs)

print("=== C18: the payload contract ===")
E.write_messages(data, [], fda=False, generated_at=NOW - 7.7 * 86400)
g["_ny_rem_fetch"] = lambda: {"ok": False,
                              "error": "Apple Reminders is off in Settings"}
p = g["_ny_build"](NOW)
E.check("payload.sources values are only present/absent",
        set(p["sources"].values()) <= {"present", "absent"}, p["sources"])
E.check("payload.sources keys are the six sources",
        sorted(p["sources"]) == sorted(g["NY_SOURCES"]), sorted(p["sources"]))
E.check("payload.source_notes carries the Messages reason",
        (p.get("source_notes") or {}).get("message") == "Full Disk Access needed",
        p.get("source_notes"))
E.check("...and every other reason (Reminders off)",
        (p.get("source_notes") or {}).get("reminder")
        == "Apple Reminders is off in Settings", p.get("source_notes"))
E.check("mail, absent by design, carries no note",
        "email" not in (p.get("source_notes") or {}), p.get("source_notes"))
E.check("Reminders switched off is the owner's choice (source_off)",
        p.get("source_off") == ["reminder"], p.get("source_off"))
E.check("...and Messages without FDA is not",
        "message" not in (p.get("source_off") or []), p.get("source_off"))
g["_ny_cache"]["payload"] = None
cold = g["_ny_payload"](mark=False)
E.check("the cold payload has the key too", cold.get("source_notes") == {}, cold)
E.check("...and source_off", cold.get("source_off") == [], cold)

print("=== C18: the widget's empty state (node) ===")
JS = r"""
// esc() is the REAL one, cut out of index.html's inline script (E138: a stand-in
// tests a different escaping from the one the dashboard ships)
globalThis.esc = require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
globalThis.relTime = function () { return "2h ago"; };
globalThis.RENDER = {}; globalThis.EXPAND_RENDER = {}; globalThis.WICONS = {};
globalThis.window = globalThis;
globalThis.fetch = function () { return Promise.reject(new Error("offline")); };
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
  RENDER.needsyou(b, d, m);
  out[k] = b.innerHTML;
}
process.stdout.write(JSON.stringify(out));
"""
base = {"ok": True, "now": [], "today": [], "later": [], "never_count": 0,
        "metrics": {}, "next_brief": None, "generated_at": time.time()}
cases = {
    "blind": dict(base, sources={"message": "absent", "calendar": "present",
                                 "email": "absent"},
                  source_notes={"message": "Full Disk Access needed"}),
    "two": dict(base, sources={"message": "absent", "calendar": "absent"},
                source_notes={"message": "Messages sync is 3 h old",
                              "calendar": "Calendar access needed"}),
    # Reminders switched off in Settings: a choice, not a blind spot
    "chosen": dict(base, sources={"message": "absent", "reminder": "absent"},
                   source_notes={"message": "Messages sync is 3 h old",
                                 "reminder": "Apple Reminders is off in Settings"},
                   source_off=["reminder"]),
    "only_chosen": dict(base, sources={"message": "present", "reminder": "absent"},
                        source_notes={"reminder": "Apple Reminders is off in Settings"},
                        source_off=["reminder"]),
    "markup": dict(base, sources={"message": "absent"},
                   source_notes={"message": "<img src=x onerror=alert(1)>"}),
    "fine": dict(base, sources={"message": "present", "calendar": "present",
                                "email": "absent"}, source_notes={}),
    "old": dict(base, sources={"message": "absent"},
                source_notes={"message": "Full Disk Access needed"}),
}
cases["old"].pop("source_notes")            # an older server: no key at all
tmp = tempfile.mkdtemp(prefix="ny-c18-")
E._HOMES.append(tmp)                        # removed at exit
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
    E.check("blind Messages -> 'but Messages is not being read'",
            "Nothing needs you — but Messages is not being read" in html["blind"],
            html["blind"][:300])
    E.check("...and the old headline is gone",
            "Nothing needs you right now" not in html["blind"], html["blind"][:300])
    E.check("the Messages chip carries the reason",
            "Messages — Full Disk Access needed" in html["blind"], html["blind"])
    E.check("Mail (absent by design) is not named in the headline",
            "Mail is not" not in html["blind"], html["blind"][:300])
    E.check("two blind sources -> 'Messages and Calendar are not being read'",
            "but Messages and Calendar are not being read" in html["two"],
            html["two"][:300])
    E.check("a note that starts with the source name is not doubled",
            "Messages — sync is 3 h old" in html["two"]
            and "Messages — Messages" not in html["two"], html["two"])
    E.check("a source the owner switched off is not called blind",
            "but Messages is not being read" in html["chosen"]
            and "Reminders are not" not in html["chosen"], html["chosen"][:300])
    E.check("...but its chip still says why, without the warning style",
            '<span class="ny-chip off">Reminders — Apple Reminders is off in Settings'
            in html["chosen"], html["chosen"])
    E.check("only an owner-off source -> the calm headline stays",
            "Nothing needs you right now" in html["only_chosen"],
            html["only_chosen"][:300])
    E.check("a source note is escaped, never markup",
            "<img" not in html["markup"] and "&lt;img" in html["markup"],
            html["markup"])
    E.check("healthy sources keep 'Nothing needs you right now'",
            "Nothing needs you right now" in html["fine"], html["fine"][:300])
    E.check("a payload without source_notes still renders the old way",
            "Nothing needs you right now" in html["old"]
            and "not connected" in html["old"], html["old"][:300])

sys.exit(E.finish())
