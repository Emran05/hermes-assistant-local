#!/usr/bin/env python3
"""E52 — a failed check is retried on a short back-off, and a throttled
workflow read keeps the repository's last CI verdict.

Before: every check outcome set `last_check = now`, so a check that failed
on a network blip (wake before Wi-Fi, a 502) was not tried again for six
hours. And a rate-limited workflow listing (GitHub answers 403 +
X-RateLimit-Remaining: 0) read as "status 403", which the check treats as a
fact about the repository: the CI verdict was wiped and writes there were
refused until the next good check.

Now: after a failed check the next one is due after 10, 20, 40… minutes
(capped at the 6-hour interval), a good check resets it, and a rate-limited
workflow read leaves the last verdict standing.
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
tok = fake.profile("clean", "octo", ["octo/app"])
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app"})
assert st == 200, (st, p)
T0 = 2_000_000_000.0


def run(now):
    ns["_GH_CHECK_LAST"]["at"] = 0.0
    return ns["_gh_run_check"](now=now)


print("== a failed check is retried soon, with back-off ==")
fake.fail_on("GET", r"^/user$", 502, times=3)
r = run(T0)
check("the check failed on a 502", r.get("ok") is False and "502" in (r.get("error") or ""), r)
due = ns["_gh_check_due"]
check("not due 5 minutes later", due(T0 + 300) is False)
check("due again 10 minutes later (was: 6 hours)", due(T0 + 600) is True)
r = run(T0 + 600)
check("second failure: due after 20 minutes, not 10",
      due(T0 + 600 + 900) is False and due(T0 + 600 + 1200) is True, r)
r = run(T0 + 1800)
check("third failure: 40 minutes", due(T0 + 1800 + 2399) is False and due(T0 + 1800 + 2400) is True)
st, s = ns["call"]("GET", "/api/github/status", query={"scan": "0"})
check("status 'next' shows the back-off, not +6 h",
      abs((s["check"]["next"] or 0) - (T0 + 1800 + 2400)) < 1, s["check"])
r = run(T0 + 4200)
check("a good check", r.get("ok") is True, r)
check("...resets the interval to 6 hours", due(T0 + 4200 + 3600) is False
      and due(T0 + 4200 + 6 * 3600) is True)
ns["_gh_state_update"](lambda s_: s_.update(last_check_ok=False, check_fails=50,
                                            last_check=T0))
check("the back-off never exceeds the 6-hour interval", due(T0 + 6 * 3600) is True)

print("== a throttled workflow read keeps the last CI verdict ==")
r = run(T0 + 9 * 3600)
row0 = ns["_gh_repo"]("octo/app")
check("baseline: a CI verdict, writes allowed", isinstance(row0.get("ci"), dict)
      and ns["_gh_writes_allowed"]("octo/app")[0] is True, ns["_gh_writes_allowed"]("octo/app"))
fake.fail_on("GET", r"^/repos/octo/app/contents/\.github/workflows$", 403, times=1,
             message="API rate limit exceeded for user", headers={"X-RateLimit-Remaining": "0"})
r = run(T0 + 16 * 3600)
row = ns["_gh_repo"]("octo/app")
check("the throttled check is reported as failed (and so retried soon)", r.get("ok") is False, r)
check("the last CI verdict stays", isinstance(row.get("ci"), dict) and not row.get(
    "enable_blocked"), (row.get("ci"), row.get("enable_blocked")))
check("writes there stay allowed", ns["_gh_writes_allowed"]("octo/app")[0] is True,
      ns["_gh_writes_allowed"]("octo/app"))
fake.fail_on("GET", r"^/repos/octo/app/contents/\.github/workflows$", 403, times=1,
             message="Resource not accessible by personal access token")
r = run(T0 + 17 * 3600)
check("a real 403 (not a rate limit) still fails CLOSED",
      ns["_gh_writes_allowed"]("octo/app")[0] is False, ns["_gh_writes_allowed"]("octo/app"))

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
