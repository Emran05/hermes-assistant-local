#!/usr/bin/env python3
"""E90: search highlights land on the right characters after an emoji.

2026-11-09 audit: the server's snippet offsets (aux_index._ix_snippet,
aux_convos._cv_snippet) count Python code points, and index.html's cvHi()
sliced with String.prototype.slice, which counts UTF-16 units. Every astral
character (emoji) before the match moved the mark one unit to the left, and
could split a surrogate pair. cvHi now slices code points (Array.from).

Runs the REAL cvHi (and the page's esc) from index.html in node; offsets are
computed here the way the server computes them (Python str indices).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print(("  FAIL %s %s" % (name, extra)).encode("utf-8", "backslashreplace").decode())


CASES = [
    ("ok \U0001F602\U0001F602\U0001F602 see you at the lease signing tomorrow", "lease"),
    ("\U0001F600 lease", "lease"),
    ("plain ascii lease here", "lease"),
    ("café \U0001F468‍\U0001F469 lease \U0001F602", "lease"),
    ("lease \U0001F602 at end", "\U0001F602"),
]


def main():
    fn = ui.extract("cvHi")
    esc = ui.extract("esc") or ui.extract_line("const esc=")
    check("index.html has cvHi and esc", bool(fn and esc))
    if not (fn and esc):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    rows = []
    for s, w in CASES:
        rows.append([s, s.index(w), len(w)])
    # out of range in code points (but in range in UTF-16 units): no mark
    s = "\U0001F602\U0001F602 abc"
    rows.append([s, 3, 3])                       # code points: 2 emoji + " abc" = 6
    rows.append([s, 4, 3])                       # past the end in code points (in range in UTF-16)
    js = (esc + "\n" + fn + "\nconst ROWS=" + json.dumps(rows) +
          ";\nconsole.log(JSON.stringify(ROWS.map(r=>cvHi(r[0],r[1],r[2]))));")
    rc, data, out = ui.run_node(js)
    check("cvHi ran in node", rc == 0 and isinstance(data, list), out[-1200:])
    if not isinstance(data, list):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    for (s, w), html in zip(CASES, data):
        want = '<span class="cv-mark">%s</span>' % w
        check("the mark covers %r in %r" % (w, s), want in html, html)
        check("...and the text around it is intact (no split pair)",
              html.replace('<span class="cv-mark">', "").replace("</span>", "") == s, html)
    check("a mark ending exactly at the last code point is drawn",
          data[len(CASES)].endswith('<span class="cv-mark">abc</span>'), data[len(CASES)])
    check("a mark past the end (in code points) draws no mark at all",
          "cv-mark" not in data[len(CASES) + 1], data[len(CASES) + 1])
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
