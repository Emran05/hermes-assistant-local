#!/usr/bin/env python3
"""E55 (connector half) — a helper that reads ONE repository's visibility
from GitHub now, for the change pipeline's decide path.

The "this repository is public" confirmation read `row["private"]`, which
only the 6-hourly check refreshes: a repository made public in between was
approved without the extra confirmation. `_gh_repo_visibility_fresh(full)`
makes a single GET /repos/{o}/{r} with the stored key, updates the row when
the answer changed, never takes `_GH_NET_LOCK` (decide already holds it),
never follows a redirect, and says plainly when it could not tell (the
caller must then fail closed).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _gh_fake  # noqa: E402

PASS, FAIL = [0], []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  ok   " + name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, repr(extra)[:400]))


fake = _gh_fake.FakeGitHub()
fake.user("octo", 4242)
fake.repo("octo/app", private=True)
BASE = fake.start()
ns = _gh_fake.load_connector(BASE)
fn = ns.get("_gh_repo_visibility_fresh")
check("the helper exists", callable(fn))
r = fn("octo/app")
check("not connected: ok False, never raises", r.get("ok") is False, r)
tok = fake.profile("clean", "octo", ["octo/app"])
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app"})
assert st == 200, (st, p)

r = fn("octo/app")
check("private, unchanged", r == {"ok": True, "private": True, "changed": False,
                                  "moved": False, "error": None}, r)
with fake.lock:
    fake.repos["octo/app"]["private"] = False
n0 = len(fake.log)
with ns["_GH_NET_LOCK"]:                      # decide holds it: must not deadlock
    r = fn("octo/app")
calls = fake.log[n0:]
check("made public on GitHub: seen at once", r.get("ok") and r.get("private") is False
      and r.get("changed") is True, r)
check("exactly one GET to the repository", [(c["method"], c["path"]) for c in calls]
      == [("GET", "/repos/octo/app")], [(c["method"], c["path"]) for c in calls])
check("the stored row is updated", ns["_gh_repo"]("octo/app")["private"] is False)
fake.fail_on("GET", r"^/repos/octo/app$", 502, times=1)
r = fn("octo/app")
check("a 5xx: ok False with no visibility (caller fails closed)",
      r.get("ok") is False and r.get("private") is None and r.get("error"), r)
fake.move("octo/app", "octo/core", repo_id=9)
r = fn("octo/app")
check("a redirect: moved, never followed", r.get("ok") is False and r.get("moved") is True
      and not any("/repositories/" in c["path"] for c in fake.log), r)
check("a malformed name: ok False", fn("../x").get("ok") is False)
check("the token is in no answer", tok not in repr([fn("octo/app"), fn("nope/nope")]))

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
