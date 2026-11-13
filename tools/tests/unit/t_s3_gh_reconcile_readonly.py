#!/usr/bin/env python3
"""Review R4-seams (S1-1): the reconcile the 6-hourly check now runs only
READS GitHub, and a failed read changes nothing.

S1-1 made the connector's check call the pipeline's _ghc_reconcile() before
its drift scan. The check is documented as GET-only ("no write reaches
GitHub"), and the reconcile writes compensating rows to the local ledger, so
two things had to hold and no suite pinned them:

  * every request the whole check sends, reconcile included, is a GET — also
    when it has an interrupted push to settle;
  * when GitHub cannot be read for that branch (500, 502, 403 rate limit, a
    redirect), NOTHING is settled: no ledger row is appended, the change is
    not marked reconciled, and the next check tries again — and then settles
    it once GitHub answers.

Drives the REAL aux_ghchanges.py + aux_github.py through t_gh_changes.py's
harness (loopback fake GitHub, file:// git remotes, scratch HOME).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("s3rec")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    Mirror = ns["_ghc_git"].Mirror
    real_push = Mirror.push

    def run_check():
        ns["_GH_CHECK_LAST"]["at"] = 0.0
        return ns["_gh_run_check"](force=True)

    def methods_since(n):
        return [(r.get("method"), r.get("path")) for r in list(w.fake.log)[n:]]

    # one pushed change, then an appended one whose push is interrupted after
    # its ledger row was written
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "feat")
    st, r = H.decide(ns, r["id"], "push_only")
    check("(setup) change 1 pushed", st == 200, (st, r))
    gh1 = w.heads("octo/app")["hermes/feat"]
    H.write(sb, "README.md", "# app\n\nmore\n")
    st, r = H.request(ns, "octo/app", "feat", mode="append")
    c2 = r["id"]

    def push(self, url, commit, slug, auth_env=None):
        Mirror.push = real_push
        ns["_GHC_INFLIGHT"].clear()
        raise SystemExit("simulated stop")
    Mirror.push = push
    try:
        H.decide(ns, c2, "push_only")
    except SystemExit:
        pass
    finally:
        Mirror.push = real_push
    H.get_change(ns, c2)                       # the sweep marks it interrupted
    led = ns["_gh_ledger_heads"]("octo/app")["hermes/feat"]
    check("(setup) the ledger names a commit GitHub never received", led != gh1, (led, gh1))
    rows0 = len(H.ledger(ns))

    for status, label in ((500, "a 500"), (502, "a 502"), (403, "a 403 (rate limit)"), (301, "a redirect")):
        print("\n== GitHub answers %s for the branch ==" % label)
        w.fake.fail_on("GET", r"^/repos/octo/app/git/ref/heads/hermes", status, times=None)
        n0 = len(w.fake.log)
        res = run_check()
        sent = methods_since(n0)
        w.fake.fail = [f for f in w.fake.fail if "git/ref/heads/hermes" not in f[1].pattern]
        check("the check still ran", isinstance(res, dict), res)
        check("every request of the check was a GET", bool(sent) and all(m == "GET" for m, _p in sent),
              sorted({m for m, _p in sent}))
        check("no ledger row was appended", len(H.ledger(ns)) == rows0, H.ledger(ns)[rows0:])
        check("the ledger head is unchanged", ns["_gh_ledger_heads"]("octo/app")["hermes/feat"] == led)
        ch = H.get_change(ns, c2) or {}
        check("the change is NOT marked reconciled", not ch.get("reconciled"), ch.get("reconciled"))
        check("...and still says it was interrupted", ch.get("state") == "failed" and ch.get("interrupted") is True,
              (ch.get("state"), ch.get("interrupted")))
        check("GitHub's branch was not touched", w.heads("octo/app")["hermes/feat"] == gh1)

    print("\n== GitHub answers again ==")
    n0 = len(w.fake.log)
    res = run_check()
    sent = methods_since(n0)
    check("every request was a GET this time too", bool(sent) and all(m == "GET" for m, _p in sent),
          sorted({m for m, _p in sent}))
    ch = H.get_change(ns, c2) or {}
    check("the interrupted change is settled now", ch.get("reconciled") is True, ch.get("reconciled"))
    check("...by ONE compensating local row", len(H.ledger(ns)) == rows0 + 1, H.ledger(ns)[rows0:])
    last = H.ledger(ns)[-1]
    check("...a push of the previous head, marked as an undo",
          last.get("event") == "push" and last.get("sha") == gh1
          and str(last.get("change_id")).endswith(":undo"), last)
    check("the ledger head is GitHub's again", ns["_gh_ledger_heads"]("octo/app")["hermes/feat"] == gh1)
    check("no drift was raised for Hermes' own branch", res.get("drift_new") == [], res.get("drift_new"))
    check("GitHub's branch never moved", w.heads("octo/app")["hermes/feat"] == gh1)
    n1 = len(H.ledger(ns))
    run_check()
    check("a further check appends nothing (settled once)", len(H.ledger(ns)) == n1)

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
