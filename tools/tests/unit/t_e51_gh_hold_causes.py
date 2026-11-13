#!/usr/bin/env python3
"""E51 — write holds follow the documented rule: a passing check clears only
what it can re-observe, and a check never stores "expired".

A: the add wall found the key can WRITE to an unlisted repository
(`unlisted_write`, "All repositories") and held writes; the next background
check, which is GET-only and cannot observe write reach, cleared the hold
with nothing fixed. Now each permission hold records its causes and a check
removes only the causes it re-observed as gone; `unlisted_write` is cleared
only by a reconnect or a passing add (the full wall).

B: one check run while the Mac's clock was ahead of the key's expiry stored
an `expired` hold, which no later passing check removed. Expiry is computed
from the credential on every read, so the check no longer stores it and
drops any stored copy.
"""
import json
import os
import sys
import time

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


def run(ns, now=None):
    ns["_GH_CHECK_LAST"]["at"] = 0.0
    return ns["_gh_run_check"](force=True, now=now)


print("== A: a write-reach hold survives a GET-only check ==")
fake = _gh_fake.FakeGitHub()
fake.user("solo", 9)
fake.repo("solo/app", private=True)
fake.repo("solo/lib", private=True)
fake.repo("solo/www", private=False)
BASE = fake.start()
tok = fake.token(_gh_fake.fg_token(), "solo", repos=["solo/app", "solo/lib"])
ns = _gh_fake.load_connector(BASE)
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "solo/app\nsolo/lib"})
assert st == 200, (st, p)
with fake.lock:
    fake.tokens[tok]["repos"] = "all"                 # widened on GitHub
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "solo/app"})
check("add finds the unlisted write and holds", st == 422 and p.get("held") is True,
      (st, p.get("lines")))
check("...unpause is refused", ns["call"]("POST", "/api/github/pause",
                                          {"paused": False})[0] == 409)
r = run(ns)
holds = ns["_gh_state"]()["holds"]
check("a passing GET-only check does NOT clear it (it cannot observe write reach)",
      "permission" in holds, (r, holds))
check("...writes stay refused", ns["_gh_writes_allowed"]("solo/app")[0] is False)
with fake.lock:
    fake.tokens[tok]["repos"] = ["solo/app", "solo/lib"]   # fixed on GitHub
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "solo/app"})
check("once fixed, a passing add (the full wall) clears it",
      st == 200 and "permission" not in ns["_gh_state"]()["holds"], (st, p.get("lines")))
check("...writes allowed again", ns["_gh_writes_allowed"]("solo/app")[0] is True,
      ns["_gh_writes_allowed"]("solo/app"))

print("== A2: a check-observable cause is cleared by the check ==")
fake.tokens[tok]["perms"]["secrets"] = "read"
r = run(ns)
check("a widened permission holds", "permission" in ns["_gh_state"]()["holds"], r)
fake.tokens[tok]["perms"]["secrets"] = None
r = run(ns)
check("...and the check that sees it gone clears it",
      "permission" not in ns["_gh_state"]()["holds"], (r, ns["_gh_state"]()["holds"]))

print("== A3: a hold stored before causes were recorded still clears as before ==")
ns["_gh_state_update"](lambda s: s["holds"].__setitem__(
    "permission", {"text": "legacy", "since": time.time()}))
r = run(ns)
check("legacy permission hold cleared by a clean check",
      "permission" not in ns["_gh_state"]()["holds"], ns["_gh_state"]()["holds"])

print("== B: a wrong clock never leaves a stored 'expired' hold ==")
c = json.load(open(os.path.join(ns["GH_DIR"], "credential.json")))
exp = c["expires_at"]
r = run(ns, now=exp + 3600)                           # the Mac's clock is ahead
check("the wrong-clock check does not STORE expired",
      "expired" not in ns["_gh_state"]()["holds"], ns["_gh_state"]()["holds"])
r = run(ns)
check("with the right clock: nothing paused, unpause works",
      ns["_gh_writes_allowed"]("solo/app")[0] is True
      and ns["call"]("POST", "/api/github/pause", {"paused": False})[0] == 200,
      (r, ns["_gh_state"]()["holds"]))
ns["_gh_state_update"](lambda s: s["holds"].__setitem__(
    "expired", {"text": "old stored copy", "since": time.time()}))
r = run(ns)
check("a stored 'expired' from an older build is dropped by the next check",
      "expired" not in ns["_gh_state"]()["holds"], ns["_gh_state"]()["holds"])
c["expires_at"] = time.time() - 5
ns["_gh_atomic_write"](os.path.join(ns["GH_DIR"], "credential.json"), c)
check("a key that really expired still pauses (computed, not stored)",
      "expired" in ns["_gh_holds"]() and ns["_gh_writes_allowed"]("solo/app")[0] is False)

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
