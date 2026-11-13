#!/usr/bin/env python3
"""Visual audit V41/V42 (2026-11-12): a label on the accent fill uses the
theme's on-accent ink, never a hard-coded white.

`--iris` is a deep blue in the light theme and a LIGHT lavender (#98A2FF) in
the dark one, which is why index.html defines `--iris-ink` (white in light,
near-black in dark) and `button.primary` uses it. Thirteen aux modules,
expand.js and one index.html rule wrote `background:var(--iris);color:#fff`
instead, so in the dark theme "Download JSONL", "Run checks", the context
card's "Apply" and a dozen more read white on lavender at 2.35:1.

And the opposite mistake: aux_watchtower.js's "Add watch" is `.wt-btn.primary`
with a 24% TINT of the accent. It inherited `--iris-ink` from index.html's
`button.primary`, which is for a SOLID fill: white on pale lavender in light
(1.43:1), near-black on dark blue-grey in dark (2.11:1). A tinted fill takes
the page ink.

Checks, on the real files:
  * no rule in dashboard/*.js or index.html pairs an accent background with a
    hard-coded white label (rules split over concatenated string fragments
    included);
  * every theme block in index.html that sets `--iris` also sets `--iris-ink`,
    and the two contrast at 4.5:1 or better;
  * `.wt-btn.primary` sets `color:var(--ink)`.
Offline; reads files only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "dashboard"))

FAILS = []
PASSES = [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
        print("  ok    " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (" :: " + detail if detail else ""))


ACCENT_BG = (r"background(?:-color)?:\s*(?:var\(--iris(?:-2)?(?:,[^)]*)?\)"
             r"|linear-gradient\([^;{}]*--iris[^;{}]*\))\s*(?:!important)?")
WHITE = r"color:\s*(?:#fff(?:fff)?|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))(?![0-9a-fA-F])"
# the same declaration block: no brace between the two declarations
PAIR = re.compile("(?:" + ACCENT_BG + r";[^{}]{0,200}?(?<![-\w])" + WHITE + r")|(?:(?<![-\w])"
                  + WHITE + r"[^{}]{0,200}?;\s*" + ACCENT_BG + ")", re.I)


def flat(text):
    """Join adjacent string fragments ('a' + \\n 'b'), so a rule written over
    several concatenated literals is read as one."""
    return re.sub(r"""["']\s*\+\s*\n\s*(?:\+\s*)?["']""", "", text)


def lum(hexcol):
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    vals = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in vals]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def main():
    names = sorted(n for n in os.listdir(DASH) if n.endswith(".js")) + ["index.html"]
    check("found the dashboard's scripts", len(names) > 40, str(len(names)))
    total = 0
    for n in names:
        with open(os.path.join(DASH, n), encoding="utf-8") as f:
            text = flat(f.read())
        hits = [text[max(0, m.start() - 40):m.end()].replace("\n", " ")[-110:]
                for m in PAIR.finditer(text)]
        total += len(hits)
        if hits:
            check("%s: no white label on the accent fill" % n, False, " || ".join(hits[:3]))
    check("no rule pairs an accent background with a hard-coded white label", total == 0,
          "%d rule(s)" % total)

    # the detector itself has teeth (the three shapes the old files used)
    for sample in ("x{border:0;background:var(--iris);color:#fff;min-height:40px}",
                   "x{background:var(--iris,#6b8afd);color:#fff;font-weight:600}",
                   "x{color:#fff;background:var(--iris);border-color:var(--iris)}",
                   'x{background:var(--iris);" +\n        "color:#fff}'):
        check("detector catches: " + sample[:44].replace("\n", " "),
              PAIR.search(flat(sample)) is not None)
    for sample in ("x{background:var(--iris);color:var(--iris-ink,#fff)}",
                   "x{background:var(--iris)}y{color:#fff}",
                   "x{background:var(--glass);color:#fff}",
                   "x{background:var(--iris);border-color:#fff}"):
        check("detector leaves alone: " + sample[:44], PAIR.search(flat(sample)) is None)

    with open(os.path.join(DASH, "index.html"), encoding="utf-8") as f:
        idx = f.read()
    blocks = re.findall(r"--iris:\s*(#[0-9A-Fa-f]{3,6})\s*;[^{}]*?--iris-ink:\s*(#[0-9A-Fa-f]{3,6})", idx)
    n_iris = len(re.findall(r"--iris:\s*#", idx))
    check("every palette that sets --iris also sets --iris-ink", n_iris >= 4 and len(blocks) == n_iris,
          "%d of %d" % (len(blocks), n_iris))
    for iris, ink in blocks:
        r = contrast(iris, ink)
        check("--iris-ink %s on --iris %s is at least 4.5:1 (%.2f)" % (ink, iris, r), r >= 4.5)
    check("button.primary takes its label from --iris-ink",
          re.search(r"button\.primary\{[^}]*color:var\(--iris-ink\)", idx) is not None)

    with open(os.path.join(DASH, "aux_watchtower.js"), encoding="utf-8") as f:
        wt = flat(f.read())
    m = re.search(r"\.wt-btn\.primary\{([^}]*)\}", wt)
    check("aux_watchtower.js has a .wt-btn.primary rule", m is not None)
    body = m.group(1) if m else ""
    check(".wt-btn.primary is a tinted (color-mix) fill", "color-mix(" in body and "--iris" in body)
    check(".wt-btn.primary labels itself with the page ink",
          re.search(r"(?<![-\w])color:var\(--ink\)", body) is not None, body[-80:])

    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
