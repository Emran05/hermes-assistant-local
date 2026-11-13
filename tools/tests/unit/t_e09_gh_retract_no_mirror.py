#!/usr/bin/env python3
"""E09 review (audit 2026-11-09, round-4 review of c03a): a pushed change can
still be retracted after its mirror was deleted.

E09 added `_ghc_delete_mirror` / `_ghc_delete_all_mirrors` (the connector
calls them when a repository is removed and on Disconnect). Retract runs its
branch deletion inside the mirror, and it closed the pull request FIRST: with
the mirror gone the git call died with FileNotFoundError after the PR was
already closed -- a 500, the branch still on GitHub, the change still shown
as an open pull request.

Now retract makes an empty mirror before it touches GitHub (deleting a branch
needs no objects), and a mirror that cannot be made refuses before anything
changes.

The REAL routes on the t_gh_changes.py harness (file:// remote, fake API).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.standard_world("e09r")
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    mpath = ns["_ghc_mirror_path"]("octo/app")

    def pushed(slug, text):
        H.write(sb, "src/login.py", text)
        st, r = H.request(ns, "octo/app", slug)
        assert st == 200 and r.get("approvable"), (st, r)
        st, d = H.decide(ns, r["id"], "push_pr")
        assert st == 200 and d.get("state") == "pr_open", (st, d)
        return r["id"], d["pr"]["number"]

    def pr_state(num):
        return [p["state"] for p in w.fake.repos["octo/app"]["pulls"] if p["number"] == num]

    print("== retract after the mirror was deleted ==")
    cid, num = pushed("gone", "COPY = 'Sign in'\n")
    check("the change is an open draft PR on hermes/gone",
          "hermes/gone" in w.heads("octo/app") and pr_state(num) == ["open"], pr_state(num))
    res = ns["_ghc_delete_mirror"]("octo", "app")
    check("the mirror is deleted (as repository removal / Disconnect do)",
          res == {"ok": True, "deleted": True} and not os.path.lexists(mpath), res)
    with H.Stderr() as err:
        st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": cid}, headers=H.OWNER)
    check("retract answers 200 retracted (not a 500)", st == 200 and r.get("state") == "retracted",
          (st, r, err.text[-300:]))
    check("...the branch is gone from GitHub", "hermes/gone" not in w.heads("octo/app"),
          sorted(w.heads("octo/app")))
    check("...the pull request is closed", pr_state(num) == ["closed"], pr_state(num))
    check("...the change is retracted", H.get_change(ns, cid)["state"] == "retracted")
    check("...and no handler crashed", "failed:" not in err.text, err.text[-300:])
    check("the ledger says Hermes left nothing on the branch",
          ns["_gh_ledger_heads"]("octo/app").get("hermes/gone") is None,
          ns["_gh_ledger_heads"]("octo/app"))

    print("\n== a mirror that cannot be made: nothing changes on GitHub ==")
    sb = H.prepare(ns, "octo/app")
    cid2, num2 = pushed("stuck", "COPY = 'Sign in!'\n")
    ns["_ghc_delete_mirror"]("octo", "app")
    with open(mpath, "w") as f:                  # a plain file where the mirror goes
        f.write("not a directory\n")
    with H.Stderr() as err:
        st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": cid2}, headers=H.OWNER)
    check("retract refuses: 503 mirror_unavailable", st == 503
          and r.get("error") == "mirror_unavailable", (st, r))
    check("...the pull request is still OPEN (nothing half-done)", pr_state(num2) == ["open"],
          pr_state(num2))
    check("...the branch is still there and the change still retractable",
          "hermes/stuck" in w.heads("octo/app")
          and H.get_change(ns, cid2)["state"] == "pr_open", H.get_change(ns, cid2)["state"])
    os.unlink(mpath)
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": cid2}, headers=H.OWNER)
    check("with the obstacle gone the same retract works", st == 200
          and r.get("state") == "retracted" and "hermes/stuck" not in w.heads("octo/app")
          and pr_state(num2) == ["closed"], (st, r))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
