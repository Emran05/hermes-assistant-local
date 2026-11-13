#!/usr/bin/env python3
"""R3-seams ui-security-1: the hidden-character class exists twice — the
server's aux_ghchanges.py _GHC_HIDDEN_RE (the C-unicode check, the title strip,
the markers in check samples and file names) and the review sheet's
aux_github.js HIDDEN_RE (markHidden over the diff, the file list and the
agent's text). Both missed the Hangul fillers U+115F/U+1160 (zero width,
identifier characters, the siblings of the U+3164/U+FFA0 the comment names),
the Khmer inherent vowels, the reserved default-ignorables and most of the
plane-14 block, so a diff line could read as `exec()` with no marker and no
warning.

Checks, over EVERY code point 0..0x10FFFF, against the REAL files (the Python
pattern ast-read from aux_ghchanges.py, the JS one by evaluating the real
aux_github.js in a bare node vm and calling its exported markHidden):

  * the two classes accept exactly the same code points, alone between two
    letters and doubled (the run-of-two alternative);
  * the JS fallback pattern (no u flag, for an engine without it) accepts the
    same BMP code points as the main one;
  * every Default_Ignorable_Code_Point is hidden on its own, EXCEPT a lone
    variation selector (U+FE00-FE0F, the Mongolian FVS U+180B-180D/U+180F):
    one VS16 after an emoji or one FVS after a Mongolian letter is ordinary
    text, and only a run of two or more is hidden;
  * U+115F is pinned on both sides, with its marker.

No dashboard, no network, no model.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("HERMES_REPO") or HERE.parent.parent.parent)
DASH = REPO / "dashboard"

FAILS, PASSES = [], [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:900]))


# DerivedCoreProperties.txt, Default_Ignorable_Code_Point (Unicode 15.1/16.0)
DEFAULT_IGNORABLE = [
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
]
# a LONE one of these is ordinary text (VS16 after an emoji, an FVS after a
# Mongolian letter); a run of two or more is hidden
LONE_OK = [(0xFE00, 0xFE0F), (0x180B, 0x180D), (0x180F, 0x180F)]


def expand(ranges):
    out = set()
    for a, b in ranges:
        out.update(range(a, b + 1))
    return out


def to_ranges(cps):
    out = []
    for c in sorted(cps):
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def hexr(ranges, n=12):
    return ["%04X-%04X" % (a, b) if a != b else "%04X" % a for a, b in ranges[:n]]


def py_pattern():
    tree = ast.parse((DASH / "aux_ghchanges.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_GHC_HIDDEN_RE" for t in node.targets):
            call = node.value
            ok = (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                  and call.func.attr == "compile" and isinstance(call.func.value, ast.Name)
                  and call.func.value.id == "re" and len(call.args) == 1 and not call.keywords)
            if not ok:
                raise AssertionError("_GHC_HIDDEN_RE is no longer a plain re.compile(<literal>)")
            return ast.literal_eval(call.args[0])
    raise AssertionError("_GHC_HIDDEN_RE not found in aux_ghchanges.py")


NODE = r"""
"use strict";
const vm = require("vm"), fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const win = {};
const ctx = vm.createContext({ window: win, console: console, setTimeout: setTimeout,
  clearTimeout: clearTimeout, Promise: Promise });
vm.runInContext(src, ctx, { filename: "aux_github.js" });
const mh = win.hermesGitHub && win.hermesGitHub.markHidden;
if (typeof mh !== "function") throw new Error("hermesGitHub.markHidden is not exported");
// the two RegExp literals of the HIDDEN_RE block, evaluated as the file writes them
const blk = src.slice(src.indexOf("var HIDDEN_RE = null;"), src.indexOf("function hex4"));
const re = /new RegExp\(((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)\s*,\s*"(gu|g)"\)/g;
const lits = []; let m;
while ((m = re.exec(blk))) lits.push({ src: vm.runInNewContext(m[1]), flags: m[2] });
function ranges(test, max) {
  const out = [];
  for (let cp = 0; cp <= max; cp++) {
    if (!test(cp)) continue;
    const last = out[out.length - 1];
    if (last && last[1] === cp - 1) last[1] = cp; else out.push([cp, cp]);
  }
  return out;
}
const ch = String.fromCodePoint;
const marked = function (s) { return mh(s) !== s; };
const res = {
  single: ranges(function (cp) { const s = "a" + ch(cp) + "b"; return marked(s); }, 0x10FFFF),
  double: ranges(function (cp) { const s = ch(cp) + ch(cp); return marked(s); }, 0x10FFFF),
  flags: lits.map(function (l) { return l.flags; }),
  pins: {
    filler: mh("exec(" + ch(0x115F) + ")"),
    astral: mh("a" + ch(0x1D173) + "b"),
    fvsRun: mh(ch(0x1820) + ch(0x180B) + ch(0xFE0F)),
    fvsOne: mh(ch(0x1820) + ch(0x180B))
  }
};
const fb = lits.filter(function (l) { return l.flags === "g"; })[0];
const mainLit = lits.filter(function (l) { return l.flags === "gu"; })[0];
if (fb) {
  const r1 = new RegExp(fb.src);
  res.fbSingle = ranges(function (cp) { return r1.test("a" + ch(cp) + "b"); }, 0xFFFF);
  res.fbDouble = ranges(function (cp) { return r1.test(ch(cp) + ch(cp)); }, 0xFFFF);
}
if (mainLit) {
  const r2 = new RegExp(mainLit.src, "u");
  res.litSingle = ranges(function (cp) { return r2.test("a" + ch(cp) + "b"); }, 0x10FFFF);
}
process.stdout.write(JSON.stringify(res));
"""


def js_sets():
    with tempfile.TemporaryDirectory(prefix="hermes-hidden-") as td:
        script = Path(td) / "hidden.js"
        script.write_text(NODE, encoding="utf-8")
        r = subprocess.run(["node", str(script), str(DASH / "aux_github.js")],
                           capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise AssertionError("node failed: " + (r.stderr or "")[-1500:])
    return json.loads(r.stdout)


def main():
    print("== the two hidden-character classes, code point by code point ==")
    rx = re.compile(py_pattern())
    py_single = {cp for cp in range(0x110000) if rx.search("a" + chr(cp) + "b")}
    py_double = {cp for cp in range(0x110000) if rx.search(chr(cp) * 2)}
    js = js_sets()
    js_single = expand(js["single"])
    js_double = expand(js["double"])

    check("the JS file builds two patterns: the u-flag one and the BMP fallback",
          sorted(js["flags"]) == ["g", "gu"], js["flags"])
    check("...and markHidden is the u-flag one (a fallback-only engine is not what runs here)",
          expand(js.get("litSingle") or []) == js_single,
          hexr(to_ranges(expand(js.get("litSingle") or []) ^ js_single)))

    only_py, only_js = py_single - js_single, js_single - py_single
    check("alone between two letters: the server and the review sheet hide exactly the same "
          "code points over 0..0x10FFFF (%d each)" % len(py_single),
          not only_py and not only_js,
          {"server only": hexr(to_ranges(only_py)), "sheet only": hexr(to_ranges(only_js))})
    only_py, only_js = py_double - js_double, js_double - py_double
    check("...and doubled (the run-of-two alternative): the same code points too",
          not only_py and not only_js,
          {"server only": hexr(to_ranges(only_py)), "sheet only": hexr(to_ranges(only_js))})
    bmp_single = {c for c in js_single if c <= 0xFFFF}
    bmp_double = {c for c in js_double if c <= 0xFFFF}
    fb_s, fb_d = expand(js.get("fbSingle") or []), expand(js.get("fbDouble") or [])
    check("the JS fallback (no u flag) hides the same BMP code points as the main pattern",
          fb_s == bmp_single and fb_d == bmp_double,
          {"single diff": hexr(to_ranges(fb_s ^ bmp_single)), "double diff": hexr(to_ranges(fb_d ^ bmp_double))})

    print("\n== every Default_Ignorable_Code_Point, the lone-selector exemption aside ==")
    di, lone = expand(DEFAULT_IGNORABLE), expand(LONE_OK)
    for label, single, double in (("server", py_single, py_double), ("review sheet", js_single, js_double)):
        missing = (di - lone) - single
        check("%s: every default-ignorable code point is hidden on its own (%d)" % (label, len(di - lone)),
              not missing, hexr(to_ranges(missing)))
        check("%s: a LONE variation selector / Mongolian FVS is ordinary text" % label,
              not (lone & single), hexr(to_ranges(lone & single)))
        check("%s: ...and a run of two of any of them is hidden" % label,
              lone <= double, hexr(to_ranges(lone - double)))
        check("%s: U+115F (Hangul choseong filler) and U+1160 are hidden" % label,
              0x115F in single and 0x1160 in single)

    pins = js["pins"]
    check("the sheet marks a Hangul filler where it sits",
          pins["filler"] == "exec(" + chr(0x27E6) + "U+115F" + chr(0x27E7) + ")", pins["filler"])
    check("...an astral one with its five hex digits",
          pins["astral"] == "a" + chr(0x27E6) + "U+1D173" + chr(0x27E7) + "b", pins["astral"])
    check("...a run of an FVS and a VS, char by char",
          pins["fvsRun"] == chr(0x1820) + chr(0x27E6) + "U+180B" + chr(0x27E7)
          + chr(0x27E6) + "U+FE0F" + chr(0x27E7), pins["fvsRun"])
    check("...and leaves one FVS after a Mongolian letter alone",
          pins["fvsOne"] == chr(0x1820) + chr(0x180B), [hex(ord(c)) for c in pins["fvsOne"]])

    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:                       # a crash is a failure, with a summary line
        FAILS.append("suite ran")
        print("  FAIL suite ran %r" % (e,))
        print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        rc = 1
    sys.exit(rc)
