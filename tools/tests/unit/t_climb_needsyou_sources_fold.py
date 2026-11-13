#!/usr/bin/env python3
"""Design climb (2026-11-12): the Needs-you widget's sources row.

BEFORE: every source was a 10px chip, the ones that are off or not connected
greyed to 45% opacity and wrapping to a second line; the footer always said
"Never 0" and "now precision — not enough data yet".

AFTER (widget): live sources and BLIND ones (C18: a reason the owner must act
on) stay chips in plain sight; off / not-connected ones fold behind ONE
"N sources off" toggle whose list (each chip with its reason) is in the DOM,
hidden until opened.  The pop-out still lists every chip open.  The footer is
left out of the empty state while there is no Never count and no measured
trust figure; with either, it is there.

Real aux_needsyou.js rendered offline in node with the real esc().
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

JS = r"""
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
base = {"now": [], "today": [], "later": [], "never_count": 0,
        "metrics": {}, "generated_at": 0,
        "sources": {"message": "absent", "calendar": "present",
                    "reminder": "absent", "email": "absent", "github": "absent"},
        "source_notes": {"message": "Full Disk Access needed",
                         "reminder": "Apple Reminders is off in Settings"},
        "source_off": ["reminder"]}
cases = {
    "empty": base,
    "pop_empty": base,
    "never": dict(base, never_count=2),
    "measured": dict(base, metrics={"now_precision": 0.78, "now_snoozed": 2}),
    "all_live": dict(base, sources={"message": "present", "calendar": "present"},
                     source_notes={}, source_off=[]),
}
tmp = tempfile.mkdtemp(prefix="ny-fold-")
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
    h = html["empty"]
    src = h[h.index('class="ny-src"'):h.index("</div>", h.index('class="ny-src"'))]
    E.check("the blind source stays a chip in plain sight, with its fix",
            '<span class="ny-chip off note">Messages — Full Disk Access needed' in src, src)
    E.check("a live source is a chip on the row",
            '<span class="ny-chip live">Calendar</span>' in src, src)
    E.check("off / not-connected sources are NOT on the row",
            "Reminders" not in src and "Mail" not in src and "GitHub" not in src, src)
    E.check("...one toggle names how many: '3 sources off', collapsed",
            'data-a="toggle-src"' in src and "3 sources off" in src
            and 'aria-expanded="false"' in src, src)
    E.check("...and their chips, each with its reason, are in a hidden list",
            '<div class="ny-offl" id="ny-offl" data-flip-id="ny-offl" hidden>' in h
            and "Reminders — Apple Reminders is off in Settings" in h
            and "Mail — not connected" in h and "GitHub — not connected" in h, h)
    E.check("the pop-out lists every chip open, no toggle",
            "toggle-src" not in html["pop_empty"]
            and "Mail — not connected" in html["pop_empty"]
            and "ny-offl" not in html["pop_empty"], html["pop_empty"])
    E.check("empty state with Never 0 and nothing measured: no footer",
            "ny-foot" not in h and "not enough data" not in h, h[-400:])
    E.check("a Never count brings the footer back",
            "<span>Never 2</span>" in html["never"], html["never"][-400:])
    E.check("a measured trust figure is shown",
            "now precision 78% · 2 snoozed" in html["measured"], html["measured"][-400:])
    E.check("all sources live: no toggle at all",
            "toggle-src" not in html["all_live"] and "ny-offl" not in html["all_live"],
            html["all_live"])

sys.exit(E.finish())
