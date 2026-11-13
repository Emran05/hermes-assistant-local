#!/usr/bin/env python3
"""E134 + E135 (GitHub pipeline part) + E58 (audit 2026-11-09): narrow
safeguards in aux_ghchanges.py that no test pinned, each with one targeted
assertion against the REAL module (t_gh_changes.py's harness).

  E134 the change digest binds EVERY field it names: alter one field of a
       stored change (re-digested) and the card's digest is stale (409); alter
       one WITHOUT re-digesting and decide refuses too (the stored digest must
       match the record);
  E135 a slug equal to the default branch's name is refused; retract refuses
       when the LEDGER head differs even though GitHub's head matches; an
       append-mode approval refuses when the ledger head moved;
  E58  retracting the later of two changes on one branch marks the earlier
       one retracted too (the branch and its PR are gone).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("e134")
    w.repo("octo/app", dict(H.SEED_FILES))
    bare = w.repo("octo/dev", dict(H.SEED_FILES), default_branch="develop")
    H.tgit(["update-ref", "refs/heads/develop", "refs/heads/main"], git_dir=bare)
    w.repo("octo/pub", {"README.md": "# pub\n"}, private=False)
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app", "octo/dev", "octo/pub"])
    sb = H.prepare(ns, "octo/app")

    def recpath(cid):
        return os.path.join(ns["_ghc_root"](), "changes", cid + ".json")

    def load(cid):
        with open(recpath(cid)) as f:
            return json.load(f)

    def store(rec):
        with open(recpath(rec["id"]), "w") as f:
            json.dump(rec, f)

    print("== E134: every digest field is bound ==")
    sbp = H.prepare(ns, "octo/pub")
    H.write(sbp, "README.md", "# pub\n\n" + "y" * 500 + "\n")      # a C-longline tick
    st, r = H.request(ns, "octo/pub", "e134-dig", body="the body")
    cid = r["id"]
    orig = load(cid)
    check("a public change with a tick to give", orig["confirm_public"] is True
          and orig["needs_ack"], orig["needs_ack"])
    old_digest = orig["digest"]
    alters = {
        "repo": lambda v: "octo/other",
        "target_ref": lambda v: "refs/heads/hermes/other",
        "parent": lambda v: "1" * 40,
        "tree": lambda v: "2" * 40,
        "title": lambda v: v + " (changed)",
        "body": lambda v: v + " (changed)",
        "needs_ack": lambda v: list(v) + ["C-extra"],
        "confirm_public": lambda v: not v,
    }
    for field, fn in alters.items():
        rec = dict(orig)
        rec[field] = fn(rec[field])
        rec["digest"] = ns["_ghc_digest"](rec)
        store(rec)
        st, r = H.decide(ns, cid, "push_only", digest=old_digest)
        check("re-digested change with %-14s altered -> the card's digest is stale (409)"
              % field, st == 409 and r.get("stale") is True, (st, r))
        rec = dict(orig)
        rec[field] = fn(rec[field])
        store(rec)                                     # stored digest left as it was
        st, r = H.decide(ns, cid, "push_only", digest=old_digest)
        check("%-14s altered on disk, digest untouched -> refused (409)" % field,
              st == 409 and r.get("stale") is True, (st, r))
    store(orig)
    st, r = H.decide(ns, cid, "push_only", digest=old_digest)
    check("the untouched record still decides", st == 200, (st, r))

    print("\n== E135: the default branch's own name is not a slug ==")
    H.prepare(ns, "octo/dev")
    sbd = ns["_ghc_sandbox"]("octo/dev")
    H.write(sbd, "README.md", "# dev\n")
    st, r = H.request(ns, "octo/dev", "develop")
    check("slug == default branch 'develop' -> 400 bad_slug (C-branch)", st == 400
          and r.get("error") == "bad_slug" and r.get("check") == "C-branch", (st, r))
    st, r = H.request(ns, "octo/dev", "developer")
    check("...a different slug is fine", st == 200, (st, r))

    print("\n== E135: append-mode approval refuses a moved LEDGER head ==")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "feat")
    c1 = r["id"]
    st, r = H.decide(ns, c1, "push_pr")
    check("change 1 pushed to hermes/feat with a PR", st == 200 and r.get("state") == "pr_open",
          (st, r))
    H.write(sb, "README.md", "# app\n\nmore\n")
    st, r = H.request(ns, "octo/app", "feat", mode="append")
    c2 = r.get("id")
    check("change 2 appends to hermes/feat", st == 200 and r.get("mode") == "append", (st, r))
    real = w.heads("octo/app")["hermes/feat"]
    ns["_gh_ledger_append"]({"event": "push", "repo": "octo/app", "branch": "hermes/feat",
                             "sha": "3" * 40, "change_id": "ghc-000000000000"})
    st, r = H.decide(ns, c2, "push_only")
    check("ledger head moved (GitHub unchanged) -> 409 moved, nothing pushed", st == 409
          and r.get("error") == "moved" and w.heads("octo/app")["hermes/feat"] == real, (st, r))

    print("\n== E135: retract refuses a LEDGER head that is not this change's ==")
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": c1}, headers=H.OWNER)
    check("GitHub head == change 1 but the ledger says otherwise -> 409 branch_changed",
          st == 409 and r.get("error") == "branch_changed"
          and "hermes/feat" in w.heads("octo/app"), (st, r))
    ns["_gh_ledger_append"]({"event": "push", "repo": "octo/app", "branch": "hermes/feat",
                             "sha": real, "change_id": c1})

    print("\n== E58: retracting the later change retracts the earlier one too ==")
    H.write(sb, "README.md", "# app\n\nmore still\n")
    st, r = H.request(ns, "octo/app", "feat", mode="append")
    c3 = r.get("id")
    st, r = H.decide(ns, c3, "push_only")
    check("change 3 appended and pushed", st == 200 and r.get("state") == "pushed", (st, r))
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": c3}, headers=H.OWNER)
    check("retract change 3 -> 200, branch gone", st == 200
          and "hermes/feat" not in w.heads("octo/app"), (st, r))
    one = H.get_change(ns, c1) or {}
    check("change 1 (earlier, same branch) is now retracted, not an open PR",
          one.get("state") == "retracted", one.get("state"))
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": c1}, headers=H.OWNER)
    check("...so it is no longer offered for retract (409 not_retractable)", st == 409
          and r.get("error") == "not_retractable", (st, r))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
