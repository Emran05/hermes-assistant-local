#!/usr/bin/env python3
"""E60 review (audit 2026-11-09, round-4 review of c03a): an @mention behind
an underscore or a non-ASCII letter is neutralised too.

E60 closed two "this is a URL" exemptions that left a live @mention. The
mention pattern itself still had one: it took ANY word character in front of
the @ (Python's Unicode \\w) to mean "an e-mail address, not a mention". But

  * an underscore is Markdown emphasis: GitHub renders `_@user_` and
    `__@org/team__` as <em>@user</em> / <strong>...</strong>, the mention is
    the first thing in its text and it notifies;
  * GitHub's mention filter knows ASCII letters and digits only, so a
    non-ASCII letter before the @ (or after the name) does not stop it.

A random-body run against a CommonMark renderer found about 100 live
mentions per 40,000 bodies, nine in ten of them this shape. Now only an ASCII
letter or digit directly in front of the @ (an e-mail address) leaves it
alone.

Drives the REAL _ghc_clean_text and the request -> decide -> pull request
path (t_gh_changes.py's harness).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
BARE = re.compile(r"(?<![`A-Za-z0-9])@[A-Za-z0-9][A-Za-z0-9/-]*(?!`)")


def main():
    w = H.standard_world("e60m")
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    clean = ns["_ghc_clean_text"]

    print("== emphasis and non-ASCII neighbours do not hide a mention ==")
    cases = [
        ("_@victim_ (emphasis)", "thanks _@victim_ for this", 1),
        ("__@victim__ (strong)", "thanks __@victim__ for this", 1),
        ("_@org/team_", "cc _@acme/owners_", 1),
        ("at the start of the text", "_@victim_", 1),
        ("after a bracket", "(_@victim_)", 1),
        ("a non-ASCII letter in front", "caf\u00e9@victim", 1),
        ("a CJK character in front", "\u4e2d@victim", 1),
        ("a non-ASCII letter after the name", "ping @victim\u00e9", 1),
        ("two, mixed", "_@one_ and \u00e9@two", 2),
    ]
    for name, body, want in cases:
        _t, b, notes = clean("T", body)
        check("%s -> wrapped" % name, notes["mentions"] == want and not BARE.search(b)
              and b.count("`@") == want, (b, notes["mentions"]))
        _t, b2, _n = clean("T", b)
        check("...and cleaning it again changes nothing", b2 == b, (b, b2))
    t, _b, notes = clean("_@victim_ did it", "")
    check("the same in a title", notes["mentions"] == 1 and not BARE.search(t), t)

    print("\n== what is not a mention is left alone ==")
    for name, body in (("an e-mail address", "write to someone@example.com today"),
                       ("an @ after a digit", "see commit 1234@upstream"),
                       ("a single-line code span", "run `_@victim_` here")):
        _t, b, notes = clean("T", body)
        check("%s: unchanged" % name, b == body and notes["mentions"] == 0, (b, notes))

    print("\n== end to end: the pull request GitHub receives ==")
    sb = H.prepare(ns, "octo/app")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "e60m", title="Fix the copy",
                      body="Reviewed by _@victim_ and __@acme/owners__.")
    st2, d = H.decide(ns, r.get("id"), "push_pr")
    prs = w.fake.repos["octo/app"]["pulls"]
    sent = (prs[-1].get("body") or "") if prs else "@none"
    check("the pull request is opened", st == 200 and st2 == 200 and d.get("state") == "pr_open",
          (st, st2, d))
    check("...and neither mention in its body is live", "`@victim`" in sent
          and "`@acme/owners" in sent and not BARE.search(sent), sent)

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
