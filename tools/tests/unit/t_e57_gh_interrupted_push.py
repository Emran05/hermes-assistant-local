#!/usr/bin/env python3
"""E57 (audit 2026-11-09): a dashboard stop between the push ledger row and the
push must not leave the ledger naming a commit GitHub never received.

Drives the REAL aux_ghchanges.py + aux_github.py through t_gh_changes.py's
harness; the "crash" is git push replaced by SystemExit.

  * crash with the change still 'pushing' (the process died: no handler ran),
    append mode: the record is swept to failed + interrupted; the next request
    reconciles — GitHub's head is read once, the compensating ledger row is
    written, the drift scan is quiet, appending again works and the earlier
    change can still be retracted;
  * crash inside the handler (BaseException path), new mode: same reconcile,
    a ref_delete compensation, the slug can be requested again;
  * a push that DID land before the crash is recognised: the change becomes
    'pushed' with its sha and no compensation is written.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("e57")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    Mirror = ns["_ghc_git"].Mirror
    real_push = Mirror.push

    def drift():
        heads = {b: s for b, s in w.heads("octo/app").items() if b.startswith("hermes/")}
        return ns["_gh_drift_scan"]("octo/app", heads, ns["_gh_ledger_heads"]("octo/app"))

    def crash(mode):
        """mode 'dead': the process is gone (no handler runs); 'handler': the
        BaseException path runs; 'landed': the push lands, then the crash."""
        def push(self, url, commit, slug, auth_env=None):
            Mirror.push = real_push
            if mode == "landed":
                real_push(self, url, commit, slug, auth_env=auth_env)
            if mode in ("dead", "landed"):
                ns["_GHC_INFLIGHT"].clear()
            raise SystemExit("simulated stop")
        Mirror.push = push

    def decide_crashing(cid, mode):
        crash(mode)
        try:
            H.decide(ns, cid, "push_only")
        except SystemExit:
            pass
        finally:
            Mirror.push = real_push

    print("== append mode, the process died mid-push ==")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "feat")
    c1 = r["id"]
    st, r = H.decide(ns, c1, "push_pr")
    check("change 1 pushed to hermes/feat", st == 200, (st, r))
    gh1 = w.heads("octo/app")["hermes/feat"]
    H.write(sb, "README.md", "# app\n\nmore\n")
    st, r = H.request(ns, "octo/app", "feat", mode="append")
    c2 = r["id"]
    decide_crashing(c2, "dead")
    led = ns["_gh_ledger_heads"]("octo/app")["hermes/feat"]
    check("(the crash left the ledger naming an unpushed commit)", led != gh1
          and w.heads("octo/app")["hermes/feat"] == gh1, (led, gh1))
    ch = H.get_change(ns, c2) or {}
    check("the stuck change is swept to failed and marked interrupted",
          ch.get("state") == "failed" and ch.get("interrupted") is True, ch)
    H.write(sb, "README.md", "# app\n\nmore, again\n")
    st, r = H.request(ns, "octo/app", "feat")
    check("the next request reconciles: the ledger head is GitHub's again",
          ns["_gh_ledger_heads"]("octo/app")["hermes/feat"] == gh1,
          ns["_gh_ledger_heads"]("octo/app"))
    check("...the drift scan is quiet", drift() == [], drift())
    check("...appending again works (mode append, 200)", st == 200
          and r.get("mode") == "append", (st, r))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    ch = H.get_change(ns, c2) or {}
    check("...the interrupted change says it was not pushed", ch.get("state") == "failed"
          and not ch.get("pushed_sha") and ch.get("interrupted") is True
          and ch.get("reconciled") is True, ch)
    rows = [x for x in ns["_gh_ledger_rows"]() if x.get("change_id") == c2 + ":undo"]
    check("...through one compensating :undo push row", len(rows) == 1
          and rows[0].get("sha") == gh1, rows)
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": c1}, headers=H.OWNER)
    check("...and change 1 can still be retracted", st == 200, (st, r))

    print("\n== new mode, the handler ran (BaseException path) ==")
    H.write(sb, "src/login.py", "COPY = 'Sign on'\n")
    st, r = H.request(ns, "octo/app", "fresh")
    c3 = r["id"]
    decide_crashing(c3, "handler")
    ch = H.get_change(ns, c3) or {}
    check("the change is failed + interrupted", ch.get("state") == "failed"
          and ch.get("interrupted") is True, ch)
    check("(the ledger named the unpushed commit)", ns["_gh_ledger_heads"]("octo/app")
          .get("hermes/fresh") not in (None,), ns["_gh_ledger_heads"]("octo/app"))
    st, r = H.request(ns, "octo/app", "fresh")
    check("requesting the same slug again works after the reconcile", st == 200
          and r.get("mode") == "new", (st, r))
    check("...the ledger says Hermes left nothing on hermes/fresh",
          ns["_gh_ledger_heads"]("octo/app").get("hermes/fresh") is None)
    check("...drift quiet", drift() == [], drift())
    c4 = r.get("id")

    print("\n== the push landed, then the stop ==")
    decide_crashing(c4, "landed")
    sha = w.heads("octo/app").get("hermes/fresh")
    check("(the branch is on GitHub)", bool(sha))
    ns["_ghc_reconcile"]()
    ch = H.get_change(ns, c4) or {}
    check("reconcile recognises it: state pushed with the sha GitHub has",
          ch.get("state") == "pushed" and ch.get("pushed_sha") == sha, ch)
    check("...no compensation row for it", not [x for x in ns["_gh_ledger_rows"]()
                                               if x.get("change_id") == c4 + ":undo"])
    check("...the drift scan is quiet", drift() == [], drift())
    st, r = ns["call"]("POST", "/api/github/changes/retract", {"id": c4}, headers=H.OWNER)
    check("...and it can be retracted", st == 200, (st, r))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
