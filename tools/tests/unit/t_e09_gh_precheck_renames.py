#!/usr/bin/env python3
"""E09 review (audit 2026-11-09, round-4 review of c03a): the size pre-check
never refuses a change the C-size check itself allows.

E09 measures the sandbox BEFORE the snapshot so over-limit content is refused
unstored. It counted new-or-changed files PLUS deleted ones against
max_files. A rename is one new file and one deletion on disk, but ONE entry
in the change (the diff detects renames), so moving a folder of 101-200 files
was refused 413 although the change is within the limit and was accepted
before E09. The pre-check now counts only what the snapshot would store;
deletions (which store nothing) are still counted by the C-size check after
the snapshot.

The REAL request route on the t_gh_changes.py harness.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("e09p")
    seed = dict(H.SEED_FILES)
    for i in range(6):
        seed["pkg/mod%d.py" % i] = "VALUE_%d = %d\n# module %d of the package\n" % (i, i, i)
    w.repo("octo/app", seed)
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    lim = ns["GH_LIMITS"]
    n = [0]

    def reset():
        H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
        H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)

    def req():
        n[0] += 1
        return H.request(ns, "octo/app", "e09p-%03d" % n[0])

    def sizes(r):
        return [c for c in r.get("checks") or [] if c["id"] == "C-size"]

    print("== moving a folder: 6 renames against max_files = 8 ==")
    lim["max_files"] = 8
    os.makedirs(os.path.join(sb, "lib"))
    for i in range(6):
        os.rename(os.path.join(sb, "pkg/mod%d.py" % i), os.path.join(sb, "lib/mod%d.py" % i))
    st, r = req()
    check("the request is accepted (not 413 too_large)", st == 200, (st, r.get("error"),
                                                                   r.get("hint")))
    ch = H.get_change(ns, r.get("id") or "") or {}
    check("...it is 6 entries, all renames", len(ch.get("files") or []) == 6
          and all(f["status"].startswith("R") for f in ch.get("files") or []),
          [(f["path"], f["status"]) for f in ch.get("files") or []])
    check("...within the limit: no C-size, approvable", r.get("approvable") is True
          and not sizes(r), r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    print("\n== what must still be refused or blocked ==")
    reset()
    for i in range(9):
        H.write(sb, "new/f%d.txt" % i, "%d\n" % i)
    st, r = req()
    check("9 NEW files against max_files = 8 -> 413 too_large, nothing stored", st == 413
          and r.get("error") == "too_large" and r.get("check") == "C-size" and not r.get("id"),
          (st, r))
    reset()
    lim["max_files"] = 4
    for i in range(6):
        os.unlink(os.path.join(sb, "pkg/mod%d.py" % i))
    st, r = req()
    check("6 deletions against max_files = 4 -> still a C-size BLOCK (after the snapshot)",
          st == 200 and r.get("approvable") is False and sizes(r)
          and sizes(r)[0]["level"] == "block", (st, r.get("checks")))
    st2, r2 = H.decide(ns, r.get("id"), "push_only")
    check("...which cannot be approved", st2 == 422 and r2.get("error") == "blocked", (st2, r2))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
