#!/usr/bin/env python3
"""Seam S1-1 / S1-2 (audit 2026-11-09, E57 remainder + E56 tamper hold).

S1-1: a push interrupted between its ledger row and the push left the ledger
naming a commit GitHub never received. The change pipeline reconciles that
on its next request or decide, but the 6-hourly check ran its drift scan
first, so it raised a false drift alert and a Telegram notice about Hermes'
OWN branch. The check now calls `_ghc_reconcile(net_held=True)` (it already
holds _GH_NET_LOCK) before the drift scan.

S1-2: `aux_ghchanges._ghc_on_tamper` calls `_gh_hold("mirror_tamper", text)`
by name, but the connector had no such function, so tampering never paused
GitHub writes. The connector now stores that hold (with its cause); the
GET-only check cannot test it and must not clear it; a reconnect does.

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
    w = H.World("s1chk")
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

    print("== S1-1: the 6-hourly check reconciles an interrupted push first ==")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    st, r = H.request(ns, "octo/app", "feat")
    c1 = r["id"]
    st, r = H.decide(ns, c1, "push_pr")
    check("change 1 pushed to hermes/feat", st == 200, (st, r))
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
    led = ns["_gh_ledger_heads"]("octo/app")["hermes/feat"]
    check("(the stop left the ledger naming an unpushed commit)", led != gh1
          and w.heads("octo/app")["hermes/feat"] == gh1, (led, gh1))
    # the sweep that marks it interrupted runs on the next read of the store
    H.get_change(ns, c2)
    n_tg = len(ns["TELEGRAM"])
    res = run_check()
    check("the check reports no new drift", res.get("drift_new") == [], res)
    check("...stores no drift row", ns["_gh_state"]()["drift"] == [],
          ns["_gh_state"]()["drift"])
    check("...sends no Telegram notice", len(ns["TELEGRAM"]) == n_tg,
          ns["TELEGRAM"][n_tg:])
    check("...because the ledger head is GitHub's again",
          ns["_gh_ledger_heads"]("octo/app")["hermes/feat"] == gh1,
          ns["_gh_ledger_heads"]("octo/app"))
    ch = H.get_change(ns, c2) or {}
    check("...and the interrupted change is marked reconciled", ch.get("reconciled") is True, ch)
    check("the check still finished ok", res.get("ok") is True, res)

    # a reconcile that raises must never stop the check
    real_rec = ns["_ghc_reconcile"]

    def boom(*a, **kw):
        raise RuntimeError("reconcile broke")
    ns["_ghc_reconcile"] = boom
    res = run_check()
    check("a raising reconcile does not abort the check", res.get("ok") is True, res)
    ns["_ghc_reconcile"] = real_rec

    # the reconcile is called with the lock already held, not re-acquired
    seen = []

    def spy(full=None, net_held=False):
        seen.append((net_held, ns["_GH_NET_LOCK"].locked()))
        return real_rec(full, net_held=net_held)
    ns["_ghc_reconcile"] = spy
    run_check()
    ns["_ghc_reconcile"] = real_rec
    check("the check passes net_held=True while it holds _GH_NET_LOCK",
          seen == [(True, True)], seen)

    print("\n== S1-2: mirror tampering holds GitHub writes ==")
    check("aux_github defines _gh_hold", callable(ns.get("_gh_hold")))
    ns["_ghc_on_tamper"]("/x/mirrors/octo__app.git/config", "config changed")
    holds = ns["_gh_holds"]()
    check("the tamper hook stored a mirror_tamper hold", "mirror_tamper" in holds, holds)
    h = holds.get("mirror_tamper") or {}
    check("...with its text, since and a recorded cause", bool(h.get("text"))
          and isinstance(h.get("since"), (int, float))
          and isinstance(h.get("causes"), list) and h["causes"]
          and h["causes"][0].get("kind") == "mirror_tamper", h)
    check("...which pauses writes", ns["_gh_paused"]() is True)
    # review R4-seams: the hold is lifted only by a reconnect, so its text
    # (card, status, Needs-you, the pause route's 409) must say so
    check("the hold text says how writes come back (connect the key again)",
          "connect your GitHub key again" in str(h.get("text"))
          and "pause GitHub writes" not in str(h.get("text")), h.get("text"))
    check("...and fits the stored bound", len(str(h.get("text"))) < 600, len(str(h.get("text"))))
    check("...and the Telegram notice says the same", any(
        "connect your GitHub key again" in str(t) for t in ns["TELEGRAM"]), ns["TELEGRAM"][-2:])
    att = [a for a in ns["_gh_attention"]() if "mirror_tamper" in str(a.get("id"))]
    check("...and so does the attention row Needs-you reads", att
          and "connect your GitHub key again" in str(att[0].get("text")), att)
    ok, why = ns["_gh_writes_allowed"]("octo/app")
    check("...and refuses the change pipeline's write check", ok is False, (ok, why))
    st, p = ns["call"]("GET", "/api/github/status")
    check("status lists the hold", any(x.get("id") == "mirror_tamper"
                                       for x in ((p.get("paused") or {}).get("holds") or [])),
          p.get("paused"))
    check("...and its attention list names it", any("mirror_tamper" in str(a.get("id"))
                                                    for a in (p.get("attention") or [])),
          p.get("attention"))
    since = h.get("since")
    ns["_ghc_on_tamper"]("/x/mirrors/octo__app.git/config", "again")
    check("a second tamper keeps the first `since`",
          ns["_gh_holds"]()["mirror_tamper"].get("since") == since)
    st, p = ns["call"]("POST", "/api/github/pause", {"paused": False}, nonce_ok=True)
    check("the owner cannot switch the hold off by hand (409)", st == 409, (st, p))
    run_check()
    check("the GET-only check does not clear it", "mirror_tamper" in ns["_gh_holds"](),
          ns["_gh_holds"]())
    check("_gh_hold refuses a hold id it does not own",
          ns["_gh_hold"]("permission", "x") is False
          and ns["_gh_hold"]("expired", "x") is False
          and "permission" not in ns["_gh_state"]()["holds"])
    check("_gh_hold refuses a non-string text", ns["_gh_hold"]("mirror_tamper", 3) is False)
    H.connect(w, ns, ["octo/app"])
    check("a reconnect clears it", "mirror_tamper" not in ns["_gh_holds"](),
          ns["_gh_holds"]())

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
