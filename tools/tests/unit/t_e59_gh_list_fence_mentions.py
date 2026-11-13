#!/usr/bin/env python3
"""E59 review (audit 2026-11-09, round-4 review of c03a): keeping the
indentation of a PR description must not leave a LIVE @mention behind a code
fence that GitHub has already closed.

Before E59 every line's indentation was flattened to one space, so every
fence in a description was a top-level block and "everything up to the
closing fence is code" was certain. E59 keeps indentation, which makes a
fence INSIDE A LIST ITEM possible:

    - note
      ```
      code
    @victim and @acme/owners

GitHub ends the list item at the unindented line, which closes the fence:
the last line is ordinary text and both mentions notify. The mention
neutraliser still treated everything after the indented opener as code and
left them alone (the audit's own exp3_mentions.py shows the live mention on
a CommonMark renderer; on the code before E59 it shows none).

Now a fence is trusted only when its opener is indented by at most one space
(never inside a list item); after a fence indented 2-3 spaces nothing but
single-line code spans is trusted, so every such mention is wrapped.

Drives the REAL _ghc_clean_text and the real request -> decide -> pull
request path (t_gh_changes.py's harness).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
# an @name that is not wrapped in backticks or made full-width
BARE = re.compile(r"(?<![`\w])@[A-Za-z0-9][A-Za-z0-9/-]*(?!`)")


def main():
    w = H.standard_world("e59m")
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    clean = ns["_ghc_clean_text"]

    print("== a fence that the end of a list item closes ==")
    live_cases = [
        ("bullet item, fence closed by the item ending",
         "- note\n  ```\n  code\n@victim and @acme/owners\n", 2),
        ("ordered item (content at column 3)", "1. step\n   ```\n   x\n@victim\n", 1),
        ("tilde fence in a bullet item", "* note\n  ~~~\n  code\n@victim\n", 1),
        ("a closer indented deeper than 3 spaces (valid inside the item)",
         "- note\n  ```\n  code\n     ```\n  @victim\n", 1),
        ("an indent-2 top-level fence closed by an unindented closer",
         "para\n\n  ```\ncode\n```\n@victim\n", 1),
        ("a later top-level fence after an unsure one is not trusted either",
         "- a\n  ```\n  x\n\n```\n@victim\n```\n", 1),
        ("nested list item", "- a\n  - b\n    text\n  ```\n  code\n@victim\n", 1),
    ]
    for name, body, want in live_cases:
        _t, b, notes = clean("T", body)
        check("%s: every mention is wrapped" % name, not BARE.search(b)
              and notes["mentions"] == want and b.count("`@") >= want, (b, notes["mentions"]))

    print("\n== what is still certainly code is left alone ==")
    kept = [
        ("an unindented fenced block", "```\n@victim\n```\nafter\n"),
        ("a fence indented by one space", "text\n\n ```\n @victim\n ```\n"),
        ("an unclosed unindented fence (code to the end)", "~~~\n@victim\nmore\n"),
        ("a single-line code span", "run `@victim` here\n"),
        ("a single-line code span after an unsure fence", "- a\n  ```\n  x\n`@victim`\n"),
    ]
    for name, body in kept:
        _t, b, notes = clean("T", body)
        check("%s: unchanged, no mention counted" % name, notes["mentions"] == 0
              and "@victim" in b and "`@victim`" not in b.replace("run `@victim`", "")
              .replace("\n`@victim`", ""), (b, notes["mentions"]))
    _t, b, notes = clean("T", "```\n@inside\n```\nthen @outside\n")
    check("after a CLOSED top-level fence a mention is wrapped, the one inside is not",
          notes["mentions"] == 1 and "`@outside`" in b and "\n@inside\n" in b, b)
    _t, b1, _n = clean("T", "- note\n  ```\n  code\n@victim\n")
    _t, b2, _n = clean("T", b1)
    check("cleaning the cleaned text again changes nothing", b1 == b2, (b1, b2))

    print("\n== end to end: the pull request GitHub receives ==")
    sb = H.prepare(ns, "octo/app")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "e59m", title="Fix the copy",
                      body="Steps:\n\n- run it\n  ```\n  make test\n@victim please review, "
                           "cc @acme/owners\n")
    check("the request is accepted", st == 200 and r.get("approvable"), (st, r))
    ch = H.get_change(ns, r.get("id")) or {}
    check("the stored description has no bare mention", not BARE.search(ch.get("body") or "@x")
          and (ch.get("sanitized") or {}).get("mentions") == 2, ch.get("body"))
    st, d = H.decide(ns, r.get("id"), "push_pr")
    prs = w.fake.repos["octo/app"]["pulls"]
    sent = (prs[-1].get("body") or "") if prs else "@none"
    check("the pull request is opened", st == 200 and d.get("state") == "pr_open" and prs,
          (st, d))
    check("...and its body, as sent to GitHub, has both mentions wrapped",
          "`@victim`" in sent and "`@acme/owners`" in sent and not BARE.search(sent), sent)
    check("...with the list and the fence's indentation kept (E59)",
          "- run it\n  ```\n  make test\n" in sent, sent)

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
