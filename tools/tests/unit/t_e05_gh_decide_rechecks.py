#!/usr/bin/env python3
"""E05 / E31 (audit 2026-11-09): Pause and holds, the Trust tier and the daily
push budget are checked again AFTER decide takes _GH_NET_LOCK, so approvals
queued behind the lock cannot exceed the budget or ignore a Pause or a Trust
change made while they waited.

Drives the REAL aux_ghchanges.py + aux_github.py through t_gh_changes.py's
harness (fake GitHub on loopback, file:// remotes, scratch HOME), with real
threads queued on the real lock.

  * the decide-time budget boundary, sequentially: one push left -> the first
    approval pushes, the next is 429 push_budget and stays pending (an
    off-by-one in the budget test fails here);
  * the same boundary with four approvals queued on the lock at once: exactly
    one pushes;
  * Pause pressed while an approval waits on the lock -> 409, nothing pushed,
    still pending;
  * Trust set to Never while an approval waits -> 403, nothing pushed.
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


class CountingLock(object):
    """_GH_NET_LOCK stand-in that says how many callers are waiting on it."""

    def __init__(self, real):
        self.real, self.waiting, self.mu = real, 0, threading.Lock()

    def acquire(self, blocking=True, timeout=-1):
        with self.mu:
            self.waiting += 1
        try:
            if not blocking:
                return self.real.acquire(False)
            return self.real.acquire(True, timeout)
        finally:
            with self.mu:
                self.waiting -= 1

    def release(self):
        self.real.release()

    def locked(self):
        return self.real.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()


def wait_for(cond, secs=20):
    t0 = time.time()
    while time.time() - t0 < secs:
        if cond():
            return True
        time.sleep(0.02)
    return False


def main():
    w = H.World("e05")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    lock = CountingLock(ns["_GH_NET_LOCK"])
    ns["_GH_NET_LOCK"] = lock

    def pending(slugs):
        set_limit(50)          # a request is budget-checked too
        ids = []
        for s in slugs:
            st, r = H.request(ns, "octo/app", s)
            assert st == 200 and r.get("approvable"), (st, r)
            ids.append(r["id"])
        return ids

    def set_limit(left):
        used = ns["_gh_push_budget"]()["used"]

        def fn(s):
            s["settings"]["max_pushes_day"] = used + left
        ns["_gh_state_update"](fn)
        return ns["_gh_push_budget"]()

    def state(cid):
        return (H.get_change(ns, cid) or {}).get("state")

    def queued(cids, before=None):
        """Decide every cid in its own thread while the test holds the lock,
        run `before` once all of them wait on it, release. -> [(st, r)]."""
        out = [None] * len(cids)
        lock.real.acquire()
        ths = []
        try:
            for i, cid in enumerate(cids):
                def run(i=i, cid=cid):
                    out[i] = H.decide(ns, cid, "push_only")
                t = threading.Thread(target=run, daemon=True)
                t.start()
                ths.append(t)
            ok = wait_for(lambda: lock.waiting == len(cids))
            check("...all %d approvals are queued on _GH_NET_LOCK" % len(cids), ok, lock.waiting)
            if before:
                before()
        finally:
            lock.real.release()
        for t in ths:
            t.join(60)
        return out

    print("== the decide-time budget boundary, one after another ==")
    a, b = pending(["e05-a", "e05-b"])
    bud = set_limit(1)
    check("one push left today", bud["left"] == 1, bud)
    st, r = H.decide(ns, a, "push_only")
    check("the first approval pushes", st == 200 and r.get("state") == "pushed", (st, r))
    st, r = H.decide(ns, b, "push_only")
    check("the second is refused: 429 push_budget", st == 429
          and r.get("error") == "push_budget", (st, r))
    check("...it is still pending", state(b) == "pending", state(b))
    check("...and its branch is not on the remote", "hermes/e05-b" not in w.heads("octo/app"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": b})

    print("\n== the same boundary with four approvals queued on the lock ==")
    ids = pending(["e05-c", "e05-d", "e05-e", "e05-f"])
    bud = set_limit(1)
    res = queued(ids)
    pushed = [x for x in res if x and x[0] == 200 and x[1].get("state") == "pushed"]
    refused = [x for x in res if x and x[0] == 429 and x[1].get("error") == "push_budget"]
    check("exactly one of four pushed", len(pushed) == 1, [(x[0], x[1].get("error")) for x in res if x])
    check("...the other three: 429 push_budget", len(refused) == 3,
          [(x[0], x[1].get("error")) for x in res if x])
    heads = w.heads("octo/app")
    check("...one new hermes/* branch on the remote",
          sum(1 for s in ("e05-c", "e05-d", "e05-e", "e05-f") if "hermes/" + s in heads) == 1,
          sorted(heads))
    check("...the refused ones are still pending",
          sorted(state(c) for c in ids) == ["pending", "pending", "pending", "pushed"],
          [state(c) for c in ids])
    check("...the budget is not exceeded", ns["_gh_push_budget"]()["left"] == 0,
          ns["_gh_push_budget"]())
    for c in ids:
        if state(c) == "pending":
            ns["call"]("POST", "/api/github/changes/withdraw", {"id": c})

    print("\n== Pause pressed while an approval waits ==")
    g = pending(["e05-g"])[0]
    set_limit(5)

    def press_pause():
        st, r = ns["call"]("POST", "/api/github/pause", {"paused": True})
        check("...Pause answers 200", st == 200, (st, r))
    res = queued([g], press_pause)
    st, r = res[0]
    check("the queued approval is refused: 409 writes_blocked", st == 409
          and r.get("error") == "writes_blocked", (st, r))
    check("...still pending, no branch", state(g) == "pending"
          and "hermes/e05-g" not in w.heads("octo/app"), state(g))

    def unpause(s):
        s["settings"]["writes_paused"] = False
    ns["_gh_state_update"](unpause)

    print("\n== Trust set to Never while an approval waits ==")
    import permissions as P

    def trust_never():
        P.permissions_set({"op": "set_pattern", "pattern": "github-write:octo/app",
                           "tier": "never"})
    res = queued([g], trust_never)
    st, r = res[0]
    check("the queued approval is refused: 403 trust_never", st == 403
          and r.get("error") == "trust_never", (st, r))
    check("...still pending", state(g) == "pending", state(g))
    P.permissions_set({"op": "clear_pattern", "pattern": "github-write:octo/app"})
    st, r = H.decide(ns, g, "push_only")
    check("with Trust cleared and writes unpaused it pushes", st == 200
          and r.get("state") == "pushed", (st, r))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
