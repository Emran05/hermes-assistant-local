#!/usr/bin/env python3
"""E55, pipeline side (seam with the connector, audit 2026-11-09): decide asks
the connector's _gh_repo_visibility_fresh(repo) — resolved by name at call
time — before trusting the stored private/public flag.

Drives the REAL aux_ghchanges.py through t_gh_changes.py's harness, with the
connector function stubbed (its own behaviour is the connector's suite):
  * unreadable or renamed -> 409 stale, nothing pushed, still pending;
  * visibility changed since the request -> 409 stale;
  * public now (and at request) -> confirm_public: true is required;
  * an older connector without the function keeps today's behaviour.
Also: diff headers keep a non-ASCII file name as it is (core.quotePath=false),
so the review sheet can match a file to its text preview.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("e55")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.repo("octo/pub", {"README.md": "# pub\n"}, private=False)
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app", "octo/pub"])
    sb = H.prepare(ns, "octo/app")
    sbp = H.prepare(ns, "octo/pub")
    answer = {}
    ns["_gh_repo_visibility_fresh"] = lambda full: dict(answer)

    def state(cid):
        return (H.get_change(ns, cid) or {}).get("state")

    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "e55-a")
    cid = r["id"]
    for name, ans in [("unreadable (ok false)", {"ok": False, "private": None}),
                      ("renamed on GitHub", {"ok": True, "private": True, "moved": True}),
                      ("made public since the request", {"ok": True, "private": False,
                                                          "changed": True})]:
        answer.clear()
        answer.update(ans)
        st, r = H.decide(ns, cid, "push_only")
        check("%s -> 409 stale, still pending" % name, st == 409 and r.get("stale") is True
              and state(cid) == "pending", (st, r))
    check("...nothing reached the remote", "hermes/e55-a" not in w.heads("octo/app"))
    answer.clear()
    answer.update({"ok": True, "private": True, "changed": False})
    st, r = H.decide(ns, cid, "push_only")
    check("still private -> pushes", st == 200 and r.get("state") == "pushed", (st, r))

    H.write(sbp, "README.md", "# pub\n\nmore\n")
    st, r = H.request(ns, "octo/pub", "e55-p")
    pid = r["id"]
    answer.clear()
    answer.update({"ok": True, "private": False, "changed": False})
    st, r = H.decide(ns, pid, "push_only", confirm_public=False)
    check("public now: without confirm_public -> 422", st == 422
          and r.get("error") == "confirm_public", (st, r))
    answer.update({"ok": True, "private": True, "changed": True})
    st, r = H.decide(ns, pid, "push_only")
    check("public at request, private now -> 409 stale", st == 409 and r.get("stale"), (st, r))
    answer.update({"ok": True, "private": False, "changed": False})
    st, r = H.decide(ns, pid, "push_only")
    check("public, confirmed -> pushes", st == 200, (st, r))

    del ns["_gh_repo_visibility_fresh"]
    H.write(sb, "src/login.py", "COPY = 'Log on'\n")
    st, r = H.request(ns, "octo/app", "e55-old")
    st, r = H.decide(ns, r["id"], "push_only")
    check("an older connector without the function: today's behaviour", st == 200, (st, r))

    print("\n== non-ASCII file names are not C-quoted in the diff ==")
    H.write(sb, "docs/café.sh", b"#!/bin/sh\n# \x00\necho hi\n")
    st, r = H.request(ns, "octo/app", "e55-name")
    ch = H.get_change(ns, r.get("id")) or {}
    fe = [f for f in ch.get("files") or [] if f["path"] == "docs/café.sh"]
    prev = (fe[0].get("text_preview") if fe else "") or ""
    check("the text preview header names the file as it is", "b/docs/café.sh" in prev
          and "\\303" not in prev, prev[:200])
    check("...and so does the review diff", "docs/café.sh" in (ch.get("diff") or ""),
          (ch.get("diff") or "")[:300])

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
