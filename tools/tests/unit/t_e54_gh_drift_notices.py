#!/usr/bin/env python3
"""E54 / E77 — drift notices: one summary per repository per check, never
repeated for branches already alerted, and sent after the GitHub lock is
released. They are security notices and ignore the notification masters
on purpose (owner decision: ping_on_push is always true).

Before: N foreign hermes/* branches produced N Telegram messages in one
check, the drift list was cut to 200 rows on every save so the rest were
"new" (and re-sent) at every check, and each send (a subprocess with a 60 s
timeout) ran while `_GH_NET_LOCK` was held, so connect/add/check answered
busy and a Push could time out waiting for the lock.
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
fake.repo("octo/lib", private=True)
BASE = fake.start()
sent, lock_held = [], []
ns = None


def tg(text):
    sent.append(text)
    lock_held.append(ns["_GH_NET_LOCK"].locked())
    return True, ""


ns = _gh_fake.load_connector(BASE, extra={"_wt_send_telegram": tg})
ns["_wt_send_telegram"] = tg
tok = fake.profile("clean", "octo", ["octo/app", "octo/lib"])
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app\nocto/lib"})
assert st == 200, (st, p)
for i in range(250):
    fake.set_head("octo/app", "hermes/x%03d" % i, "%040x" % (i + 1))
fake.set_head("octo/lib", "hermes/one", "f" * 40)


def run():
    ns["_GH_CHECK_LAST"]["at"] = 0.0
    return ns["_gh_run_check"](force=True)


r = run()
check("all 251 foreign branches are reported as new", len(r.get("drift_new") or []) == 251,
      len(r.get("drift_new") or []))
check("ONE Telegram message per repository (2, not 251)", len(sent) == 2, len(sent))
app_msg = [m for m in sent if "octo/app" in m]
check("the summary says how many branches", app_msg and "250" in app_msg[0], app_msg)
lib_msg = [m for m in sent if "octo/lib" in m]
check("a single branch keeps the one-branch wording", lib_msg and "hermes/one" in lib_msg[0],
      lib_msg)
check("every notice went out AFTER the network lock was released",
      lock_held and not any(lock_held), lock_held)
stored = ns["_gh_state"]()["drift"]
check("all 251 drift rows are kept (no global cut at 200)", len(stored) == 251, len(stored))
check("...all marked notified", all(d.get("notified") for d in stored))
n = len(sent)
r = run()
check("the next check sends nothing and reports nothing new",
      len(sent) == n and not r.get("drift_new"), (len(sent) - n, len(r.get("drift_new") or [])))
r = run()
check("...nor the one after", len(sent) == n, len(sent) - n)

print("== an undelivered summary is retried once per repository ==")
fails = [True]


def tg_down(text):
    if fails[0]:
        return False, "sender down"
    sent.append(text)
    return True, ""


ns["_wt_send_telegram"] = tg_down
fake.set_head("octo/lib", "hermes/two", "e" * 40)
fake.set_head("octo/lib", "hermes/three", "d" * 40)
run()
fails[0] = False
n = len(sent)
run()
check("retried as ONE summary for that repository", len(sent) == n + 1 and "octo/lib" in sent[-1],
      sent[n:])
run()
check("...and not again once delivered", len(sent) == n + 1, sent[n:])

print("== E77: a security notice, sent whatever the notification masters say ==")
check("the exception is named in code", ns.get("GH_SECURITY_NOTICES_IGNORE_MASTERS") is True)
ns["_wt_push_allowed"] = lambda *a, **k: False
ns["_wt_news_on"] = lambda *a, **k: False
n = len(sent)
ok, _det = ns["_gh_notify"]("test notice")
check("_gh_notify still sends with both masters off", ok and len(sent) == n + 1)

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
