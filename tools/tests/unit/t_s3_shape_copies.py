#!/usr/bin/env python3
"""Review R4-seams: one blank-run rule on all three surfaces, and the two
approval cards' `shape` really are the same code.

The server's C-longline check (aux_ghchanges.py) counts a run of blanks as
deceptive at 40 characters OR 80 screen columns (_ghc_blank_width: a tab is 8
columns, U+3000 and U+2800 are 2). After the S2 seam only the main window's
approval card (index.html streamJob `shape`) used that rule; Quick Ask's card
(aux_quickask.js `qaShape`) and the GitHub review sheet (aux_github.js DL)
still marked only runs of 40+ characters, so ten tabs (80 columns of empty
area) pushed what followed out of sight on those two. And CLAUDE.md says
`shape` and `qaShape` are "the same code — change both or neither", which
nothing checked.

Now:
  * the text between the SHAPE-BODY markers is identical in both files (line
    by line, indentation aside) and plain ASCII — an invisible character typed
    into the regular expression instead of its escape is caught here;
  * the main card, the Quick Ask card and the review sheet mark the same runs,
    in the same words ("A, B and C" for three kinds);
  * Quick Ask's card shows the Trust policy's reason line the main card shows
    (approval._policy.reason): text, marked, one line, at most 300 characters.

Drives the REAL streamJob, the REAL qaShape / approvalBox and the REAL
aux_github.js diffHTML in node. Offline.
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
import t_b11_quickask_busy as qb  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


DASH = os.path.join(ui.REPO, "dashboard")
NBSP, IDEO, BRAILLE = chr(0xA0), chr(0x3000), chr(0x2800)
L, R = chr(0x27E6), chr(0x27E7)          # the marker brackets
RLO, ZWSP, LSEP, ELL = chr(0x202E), chr(0x200B), chr(0x2028), chr(0x2026)


def M(words):
    return L + words + R


# (label, text, what every surface must show for it)
CASES = [
    ("ten tabs (80 columns)", "ls" + "\t" * 10 + "; rm -rf ~", "ls" + M("10 tabs") + "; rm -rf ~"),
    ("nine tabs (72 columns) are left alone", "ls" + "\t" * 9 + "x", "ls" + "\t" * 9 + "x"),
    ("39 spaces are left alone", "a" + " " * 39 + "b", "a" + " " * 39 + "b"),
    ("40 spaces", "a" + " " * 40 + "b", "a" + M("40 spaces") + "b"),
    ("40 ideographic spaces", "a" + IDEO * 40 + "b", "a" + M("40 other blank characters") + "b"),
    ("39 ideographic spaces (78 columns) are left alone", "a" + IDEO * 39 + "b", "a" + IDEO * 39 + "b"),
    ("20 braille blanks (40 columns) are left alone", "a" + BRAILLE * 20 + "b", "a" + BRAILLE * 20 + "b"),
    ("6 tabs + 32 spaces: 38 characters but 80 columns", "a" + "\t" * 6 + " " * 32 + "b",
     "a" + M("32 spaces and 6 tabs") + "b"),
    ("5 tabs + 30 spaces: 70 columns are left alone", "a" + "\t" * 5 + " " * 30 + "b",
     "a" + "\t" * 5 + " " * 30 + "b"),
    ("three kinds, in the sheet's words", "a" + " " * 20 + "\t" * 5 + NBSP * 20 + "b",
     "a" + M("20 spaces, 5 tabs and 20 other blank characters") + "b"),
    ("two runs on one line, only the long one marked", "a  b" + "\t" * 12 + "c  d",
     "a  b" + M("12 tabs") + "c  d"),
    ("a single tab stays a tab", "tab\tend", "tab\tend"),
]


def body_of(path, what):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    a, b = src.find("// SHAPE-BODY-BEGIN"), src.find("// SHAPE-BODY-END")
    check("%s carries the SHAPE-BODY markers, once" % what,
          0 <= a < b and src.count("// SHAPE-BODY-BEGIN") == 1 and src.count("// SHAPE-BODY-END") == 1)
    if not 0 <= a < b:
        return None
    return [ln.strip() for ln in src[a:b].split("\n")[1:] if ln.strip()]


def main():
    print("== the two cards' shape() is one text ==")
    idx = body_of(ui.INDEX, "index.html")
    qa = body_of(os.path.join(DASH, "aux_quickask.js"), "aux_quickask.js")
    check("the body is identical in index.html and aux_quickask.js (indentation aside)",
          bool(idx) and idx == qa,
          [(i, a, b) for i, (a, b) in enumerate(zip(idx or [], qa or [])) if a != b][:2]
          or (len(idx or []), len(qa or [])))
    check("...and is more than a stub", len(idx or []) >= 15, len(idx or []))
    for what, lines in (("index.html", idx), ("aux_quickask.js", qa)):
        bad = [ln for ln in (lines or []) if any(ord(c) > 127 for c in ln)]
        check("%s: the body is plain ASCII (escapes, never an invisible character typed in)" % what,
              lines is not None and not bad, [ascii(b)[:120] for b in bad[:2]])

    print("\n== the main window's card ==")
    fn = ui.extract("streamJob")
    steps = [{"state": "approval", "done": False, "text": "", "status": "",
              "approval": {"command": c[1], "approval_id": "A%d" % i}} for i, c in enumerate(CASES)]
    steps.append({"state": "done", "done": True, "reply": "fin", "text": "fin"})
    rc, data, out = ui.run_node(e15.SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn or "", "steps": json.dumps(steps)})
    check("streamJob ran in node", rc == 0 and data and "error" not in data, out[-1200:])
    seen = (data or {}).get("seen") or []
    for i, (label, _t, want) in enumerate(CASES):
        got = ((seen[i + 1] if i + 1 < len(seen) else None) or {}).get("code")
        check("main card: " + label, got == want, (got, want))

    print("\n== Quick Ask's card ==")
    fns = [qb.qa_fn(n) for n in ("qaMarkHidden", "qaShape", "approvalBox")]
    check("aux_quickask.js has qaMarkHidden / qaShape / approvalBox", all(fns))
    evil = ('<img src=x onerror="globalThis.PWNED=1">' + RLO + "danger" + ZWSP + BRAILLE * 60
            + "end\nsecond" + LSEP + "third")
    reasons = [
        {"tier": "ask", "reason": "terminal:rm -> filesystem is set to Auto, but more than one command"},
        None,
        {"tier": "ask", "reason": evil},
        {"tier": "ask", "reason": "x" * 900},
        "not a dict",
        {"tier": "ask", "reason": "   "},
        {"tier": "ask", "reason": 7},
    ]
    box_js = r"""
const out=[];
for(const cmd of %(cmds)s){ const b=approvalBox('J', {command:cmd, approval_id:'x'}, ()=>{});
  out.push({code:b.querySelector('code').textContent}); }
const pols=[];
for(const p of %(pols)s){ const a={command:'ls; rm -rf build', approval_id:'y'}; if(p!==null)a._policy=p;
  const b=approvalBox('J', a, ()=>{}); const el=b.querySelector('.pol');
  pols.push({pol:el?el.textContent:null, html:el?el.innerHTML:null, code:b.querySelector('code').textContent,
             order:Array.from(b.children).map(c=>c.className||c.tagName)}); }
console.log(JSON.stringify({boxes:out, pols:pols, pwned:typeof globalThis.PWNED}));
"""
    rc, data, out = ui.run_node((qb.HARNESS + box_js) % {
        "dom": ui.FAKE_DOM, "fns": "\n".join(f or "" for f in fns),
        "cmds": json.dumps([c[1] for c in CASES]), "pols": json.dumps(reasons)})
    ok = rc == 0 and isinstance(data, dict)
    check("Quick Ask approvalBox ran in node", ok, out[-1200:])
    boxes = (data or {}).get("boxes") or []
    for i, (label, _t, want) in enumerate(CASES):
        got = (boxes[i] if i < len(boxes) else {}).get("code")
        check("Quick Ask: " + label, got == want, (got, want))
    pols = (data or {}).get("pols") or [{}] * len(reasons)
    p0 = pols[0] if pols else {}
    check("Quick Ask shows the Trust reason line", p0.get("pol") == reasons[0]["reason"], p0)
    order = [str(x).lower() for x in (p0.get("order") or [])]
    check("...under the heading, above the command",
          "pol" in order and "code" in order and order.index("h") < order.index("pol") < order.index("code"),
          order)
    check("...and the command is still the command", p0.get("code") == "ls; rm -rf build", p0.get("code"))
    check("no verdict -> no line", pols[1].get("pol") is None and "pol" not in (pols[1].get("order") or []),
          pols[1])
    ev = pols[2].get("pol") or ""
    check("the reason is text, never markup", "<img" in ev and (data or {}).get("pwned") == "undefined"
          and "<img" not in (pols[2].get("html") or "").replace("&lt;img", ""), pols[2].get("html"))
    check("...hidden characters are marked", (M("U+202E") in ev) and (M("U+200B") in ev)
          and RLO not in ev and ZWSP not in ev, ascii(ev))
    check("...a 60-blank run is one marker", M("60 other blank characters") in ev, ascii(ev))
    check("...and it is ONE line", "\n" not in ev and LSEP not in ev and "end second" in ev, ascii(ev))
    lp = pols[3].get("pol") or ""
    check("a long reason is capped at 300 characters and ends with an ellipsis",
          0 < len(lp) <= 300 and lp.endswith(ELL), len(lp))
    for i, what in ((4, "a non-dict _policy"), (5, "a blank reason"), (6, "a non-string reason")):
        check("%s shows nothing" % what, pols[i].get("pol") is None, pols[i])
    with open(os.path.join(DASH, "aux_quickask.js"), encoding="utf-8") as f:
        css = f.read()
    m = re.search(r'"\.qa-appr \.pol\{([^}]*)\}"', css)
    check("`.qa-appr .pol` is quiet (muted) and wraps",
          bool(m) and "var(--muted)" in m.group(1) and "overflow-wrap:anywhere" in m.group(1),
          m.group(0) if m else None)

    print("\n== the GitHub review sheet ==")
    js = r"""
const vm=require('vm'),fs=require('fs');const win={};
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),vm.createContext({window:win,console,setTimeout,clearTimeout,Promise}),{filename:'aux_github.js'});
const G=win.hermesGitHub;const lines=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const un=s=>s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&');
const out=lines.map(l=>{const h=G.diffHTML('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+'+l+'\n');
  const m=h.match(/<span class="ghd-l add">([\s\S]*?)<\/span><\/div>/);
  return m?un(m[1].replace(/<[^>]*>/g,'')).slice(1):null;});
console.log(JSON.stringify(out));
"""
    with tempfile.TemporaryDirectory(prefix="hermes-s3-shape-") as td:
        p, q = os.path.join(td, "t.js"), os.path.join(td, "lines.json")
        with open(p, "w") as f:
            f.write(js)
        with open(q, "w", encoding="utf-8") as f:
            json.dump([c[1] for c in CASES], f)
        r = subprocess.run(["node", p, os.path.join(DASH, "aux_github.js"), q],
                           capture_output=True, text=True, timeout=60)
    sheet = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else None
    check("aux_github.js diffHTML ran", isinstance(sheet, list) and len(sheet) == len(CASES),
          (r.stderr or r.stdout)[-800:])
    for i, (label, _t, want) in enumerate(CASES):
        got = sheet[i] if isinstance(sheet, list) and i < len(sheet) else None
        check("review sheet: " + label, got == want, (got, want))

    print("\n== the server's numbers are the ones all three use ==")
    with open(os.path.join(DASH, "aux_ghchanges.py"), encoding="utf-8") as f:
        gc = f.read()
    check("server: C-longline is 40 blanks or 80 columns",
          re.search(r"^GHC_WS_RUN = 40$", gc, re.M) and re.search(r"^GHC_WS_COLS = 80$", gc, re.M))
    with open(os.path.join(DASH, "aux_github.js"), encoding="utf-8") as f:
        gj = f.read()
    check("aux_github.js: BLANK_RUN = 40, BLANK_COLS = 80",
          re.search(r"var BLANK_RUN = 40, BLANK_COLS = 80;", gj) is not None)
    check("the cards' body tests 40 characters and 80 columns",
          any("m.length < 40 && cols < 80" in ln for ln in (idx or [])))
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
