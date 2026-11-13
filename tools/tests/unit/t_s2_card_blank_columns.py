#!/usr/bin/env python3
"""S2 seam (E02/E15 copies): the main approval card marks blank runs by the
server's rule, in the GitHub sheet's words.

aux_ghchanges.py's C-longline counts a run of blanks as deceptive at 40
characters OR 80 screen columns (_ghc_blank_width: a tab is 8 columns, an
ideographic space or a braille blank 2).  The main card (index.html streamJob
shape()) marked only runs of 40+ characters, so 10-39 tabs (80-312 columns)
were drawn as empty area that pushes what follows out of sight.  It now marks
either, the stricter rule.  And its marker words now match the GitHub sheet's
(aux_github.js blankWord: "1 space, 1 tab and 1 other blank character"), where
the card used to say "… and … and …".

Drives the REAL streamJob (index.html) and the REAL aux_github.js diffHTML in
node.  Offline.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402
import t_e15_approval_multiline as e15  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


CASES = [
    ("ten tabs (80 columns)", "ls" + "\t" * 10 + "; rm -rf ~", "ls⟦10 tabs⟧; rm -rf ~"),
    ("nine tabs (72 columns) are left alone", "ls" + "\t" * 9 + "x", "ls" + "\t" * 9 + "x"),
    ("39 spaces are left alone", "a" + " " * 39 + "b", "a" + " " * 39 + "b"),
    ("40 spaces", "a" + " " * 40 + "b", "a⟦40 spaces⟧b"),
    ("40 ideographic spaces (n >= 40)", "a" + "　" * 40 + "b", "a⟦40 other blank characters⟧b"),
    ("39 ideographic spaces + 2 spaces = 41 chars", "a" + "　" * 39 + "  b",
     "a⟦2 spaces and 39 other blank characters⟧b"),
    ("20 braille blanks (40 columns) are left alone", "a" + "⠀" * 20 + "b", "a" + "⠀" * 20 + "b"),
    ("6 tabs + 32 spaces: 38 characters but 80 columns", "a" + "\t" * 6 + " " * 32 + "b",
     "a⟦32 spaces and 6 tabs⟧b"),
    ("5 tabs + 30 spaces: 70 columns are left alone", "a" + "\t" * 5 + " " * 30 + "b",
     "a" + "\t" * 5 + " " * 30 + "b"),
    ("all three kinds, the sheet's wording", "a" + " " * 20 + "\t" * 5 + " " * 20 + "b",
     "a⟦20 spaces, 5 tabs and 20 other blank characters⟧b"),
    ("a single tab stays a tab", "tab\tend", "tab\tend"),
]


def main():
    fn = ui.extract("streamJob")
    check("index.html has a top-level streamJob", bool(fn))
    steps = [{"state": "approval", "done": False, "text": "", "status": "",
              "approval": {"command": c[1], "approval_id": "A%d" % i}} for i, c in enumerate(CASES)]
    steps.append({"state": "done", "done": True, "reply": "fin", "text": "fin"})
    rc, data, out = ui.run_node(e15.SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn or "", "steps": json.dumps(steps)})
    check("streamJob ran in node", rc == 0 and data and "error" not in data, out[-1500:])
    seen = (data or {}).get("seen") or []
    for i, (label, _cmd, want) in enumerate(CASES):
        got = ((seen[i + 1] if i + 1 < len(seen) else None) or {}).get("code")
        check("main card: " + label, got == want, (got, want))

    # the sheet's own words for the same runs (aux_github.js DL via diffHTML)
    js = r"""
const vm=require('vm'),fs=require('fs');const win={};
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),vm.createContext({window:win,console,setTimeout,clearTimeout,Promise}),{filename:'aux_github.js'});
const G=win.hermesGitHub;const lines=JSON.parse(process.argv[3]);
const out=lines.map(l=>{const h=G.diffHTML('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+'+l+'\n');
  const m=h.match(/<span class="ghd-ws">([^<]*)<\/span>/);return m?m[1]:null;});
console.log(JSON.stringify(out));
"""
    with tempfile.TemporaryDirectory(prefix="hermes-s2-blank-") as td:
        p = os.path.join(td, "t.js")
        with open(p, "w") as f:
            f.write(js)
        runs = ["a" + " " * 40 + "b", "a" + " " * 20 + "\t" * 20 + " " * 20 + "b"]
        r = subprocess.run(["node", p, os.path.join(ui.REPO, "dashboard", "aux_github.js"), json.dumps(runs)],
                           capture_output=True, text=True, timeout=60)
    sheet = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else None
    check("aux_github.js diffHTML ran", isinstance(sheet, list), (r.stderr or "")[-800:])
    if isinstance(sheet, list):
        check("the sheet and the card say a 40-space run the same way",
              sheet[0] == "⟦40 spaces⟧", sheet[0])
        check("...and a three-kind run the same way (\"A, B and C\")",
              sheet[1] == "⟦20 spaces, 20 tabs and 20 other blank characters⟧", sheet[1])
    # the server's numbers are the ones the card uses
    gc = open(os.path.join(ui.REPO, "dashboard", "aux_ghchanges.py"), encoding="utf-8").read()
    check("server: C-longline is 40 blanks or 80 columns",
          re.search(r"^GHC_WS_RUN = 40$", gc, re.M) and re.search(r"^GHC_WS_COLS = 80$", gc, re.M))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
