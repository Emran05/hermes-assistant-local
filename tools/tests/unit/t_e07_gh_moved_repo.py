#!/usr/bin/env python3
"""E07 / E53 — a repository renamed or transferred on GitHub, and removing a
repository for real.

GitHub answers the old `GET /repos/{o}/{r}` with 301 (Location
/repositories/<id>). The client never follows a redirect. Before this fix the
6-hourly check read the new name as "a private repository you did not list"
and paused EVERY GitHub write, `repos add` of the new name answered 503 for
ever, `remove` only switched the row off, and a reconnect kept the stale row,
so every later check failed and every later add was refused.

Now: a 3xx on the repository read marks THAT row `moved` (writes refused
there only, a plain reason in status, no hold), the Location target is never
contacted, `remove` drops the row (or marks it removed while something still
refers to it) so remove + add of the new name works, a reconnect starts clean,
and the 10-repository limit counts only the rows Hermes uses (E53).

The REAL aux_github.py + gh_api.py against the loopback fake GitHub.
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


def world():
    fake = _gh_fake.FakeGitHub()
    fake.user("octo", 4242)
    fake.repo("octo/app", private=True)
    fake.repo("octo/lib", private=True)
    fake.repo_ids.update({"octo/app": 501, "octo/lib": 502})
    base = fake.start()
    tok = fake.token(_gh_fake.fg_token(), "octo", repos=["octo/app", "octo/lib"])
    ns = _gh_fake.load_connector(base)
    st, p = ns["call"]("POST", "/api/github/connect",
                       {"token": tok, "repos": "octo/app\nocto/lib"})
    assert st == 200 and p.get("stored"), (st, p)
    return fake, ns, tok


def run(ns):
    ns["_GH_CHECK_LAST"]["at"] = 0.0
    return ns["_gh_run_check"](force=True)


def holds(ns):
    return sorted(ns["_gh_state"]()["holds"])


def row(ns, name):
    return ns["_gh_state"]()["repos"].get(name)


print("== a private repository renamed on GitHub (301 with Location) ==")
fake, ns, tok = world()
fake.move("octo/lib", "octo/core")
n0 = len(fake.log)
r = run(ns)
check("the check sets NO hold (the key was never widened)", holds(ns) == [], (holds(ns), r))
check("...and the check itself succeeds", r.get("ok") is True, r)
check("writes to the untouched repository stay allowed",
      ns["_gh_writes_allowed"]("octo/app")[0] is True, ns["_gh_writes_allowed"]("octo/app"))
okm, why = ns["_gh_writes_allowed"]("octo/lib")
check("writes to the moved repository are refused, with a plain reason",
      okm is False and "renamed or transferred" in why, why)
rw = row(ns, "octo/lib") or {}
check("the row is marked moved", isinstance(rw.get("moved"), dict), rw)
st, s = ns["call"]("GET", "/api/github/status", query={"scan": "0"})
pr = [x for x in s["repos"] if x["full_name"] == "octo/lib"]
check("status shows moved + the remove-and-add-again reason",
      pr and pr[0].get("moved") is True and "remove it and add it under its new name"
      in (pr[0].get("moved_reason") or ""), pr)
check("...naming the new name when GitHub's listing gives it away",
      pr and "octo/core" in (pr[0].get("moved_reason") or ""), pr)
check("the unpause switch is not blocked by a hold",
      ns["call"]("POST", "/api/github/pause", {"paused": False})[0] == 200)
check("the redirect target was never contacted",
      not any("/repositories/" in c["path"] for c in fake.log[n0:]))
check("the redirect was not followed with the key anywhere (only GETs to known paths)",
      all(c["method"] == "GET" for c in fake.log[n0:]))

st, p = ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/lib"})
check("remove answers 200", st == 200, (st, p))
check("...and drops the row (nothing refers to it)", row(ns, "octo/lib") is None,
      row(ns, "octo/lib"))
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/core"})
check("adding the new name now works (was 503 for ever)", st == 200 and
      (p.get("added") or {}).get("enabled") is True, (st, p.get("lines"), p.get("added")))
r = run(ns)
check("the next check is clean", r.get("ok") is True and holds(ns) == [], (r, holds(ns)))
fake.stop()

print("== a moved repository whose redirect carries no id (fail closed, then repairable) ==")
fake, ns, tok = world()
fake.move("octo/lib", "octo/core", location=False)
with fake.lock:
    fake.repo_ids.pop("octo/core", None)          # GitHub's listing gives no id either
r = run(ns)
check("the unknown private name still raises the reach hold (cannot tell it is the same repo)",
      "permission" in holds(ns), (holds(ns), r))
check("...but the moved row itself is not a check error that blocks clearing",
      "octo/lib" not in str(r.get("error") or ""), r)
st, p = ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/lib"})
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/core"})
check("remove + add of the new name is not a 503 'try again' loop", st == 200, (st, p.get("lines")))
r = run(ns)
check("the next check re-observes the reach and lifts the hold", holds(ns) == [], (holds(ns), r))
fake.stop()

print("== a public repository renamed: same, never a stuck error ==")
fake = _gh_fake.FakeGitHub()
fake.user("solo", 9)
fake.repo("solo/app", private=False)
fake.repo("solo/www", private=False)
base = fake.start()
tok = fake.token(_gh_fake.fg_token(), "solo", repos=["solo/app", "solo/www"])
ns = _gh_fake.load_connector(base)
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "solo/app\nsolo/www"})
assert st == 200, (st, p)
fake.move("solo/www", "solo/site", repo_id=777)
r = run(ns)
check("public rename: check ok, no hold", r.get("ok") is True and holds(ns) == [], (r, holds(ns)))
check("...moved row, writes refused there", isinstance((row(ns, "solo/www") or {}).get("moved"),
                                                         dict))
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "solo/site"})
check("adding the new name works while the old row is still there", st == 200, (st, p.get("lines")))
fake.stop()

print("== a reconnect starts clean ==")
fake, ns, tok = world()
fake.move("octo/lib", "octo/core")
ns["call"]("POST", "/api/github/disconnect", {})
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app\nocto/core"})
check("reconnect with the new name: 200", st == 200, (st, p.get("lines")))
rows = ns["_gh_state"]()["repos"]
check("the stale row is gone (or at least no longer listed or enabled)",
      "octo/lib" not in rows or not (rows["octo/lib"].get("listed") or rows["octo/lib"].get("enabled")),
      rows.get("octo/lib"))
r = run(ns)
check("the check after reconnect is clean (was 'unreachable (status 301)' for ever)",
      r.get("ok") is True, r)
fake.repo("octo/extra", private=True)
with fake.lock:
    fake.tokens[tok]["repos"].append("octo/extra")
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/extra"})
check("a later add works", st == 200, (st, p.get("lines")))
fake.stop()

print("== remove keeps a row only while something refers to it ==")
fake, ns, tok = world()
sb = ns["_gh_sandbox_path"]("octo/lib")
os.makedirs(os.path.join(sb, ".git"))
st, p = ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/lib"})
rw = row(ns, "octo/lib") or {}
check("a prepared sandbox keeps the row, marked removed and off",
      rw.get("removed") is True and not rw.get("enabled") and not rw.get("listed"), rw)
check("...the sandbox itself is untouched", os.path.isdir(os.path.join(sb, ".git")))
n0 = len(fake.log)
r = run(ns)
check("a removed row is not checked any more",
      not any("/repos/octo/lib" in c["path"] for c in fake.log[n0:]))
check("...and the key still reaching it is not 'unlisted' (the owner listed it once)",
      holds(ns) == [], holds(ns))
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
rw = row(ns, "octo/lib") or {}
check("adding it again brings it back", st == 200 and rw.get("enabled") is True
      and not rw.get("removed"), (st, rw))
fake.stop()

print("== E53: the limit counts only the repositories Hermes uses ==")
fake = _gh_fake.FakeGitHub()
fake.user("many", 5)
names = ["many/r%d" % i for i in range(11)]
for n in names:
    fake.repo(n, private=True)
base = fake.start()
tok = fake.token(_gh_fake.fg_token(), "many", repos=names[:10])
ns = _gh_fake.load_connector(base)
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "\n".join(names[:10])})
check("connect 10", st == 200 and len(p.get("enabled") or []) == 10, (st, p.get("lines")))
for n in names[:5]:
    ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": n})
with fake.lock:
    fake.tokens[tok]["repos"] = names[5:]            # narrowed on GitHub, plus the 11th
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": names[10]})
check("an 11th name with only 5 in use is accepted (was 400 too_many)", st == 200,
      (st, p.get("error"), p.get("hint"), p.get("lines")))
check("...and enabled", (p.get("added") or {}).get("enabled") is True, p.get("added"))
st, s = ns["call"]("GET", "/api/github/status", query={"scan": "0"})
check("status lists 6 enabled", len(s["enabled"]) == 6, s["enabled"])
fake.stop()

print("== review: rows stored before ids were kept (every existing install) ==")
fake, ns, tok = world()


def _strip_ids(s):
    for _r in s["repos"].values():
        _r.pop("id", None)


ns["_gh_state_update"](_strip_ids)
fake.move("octo/lib", "octo/core")                  # Location /repositories/502
r = run(ns)
check("the FIRST check after the rename sets no hold (the redirect's id is the new name's)",
      holds(ns) == [] and ns["_gh_writes_allowed"]("octo/app")[0] is True, (holds(ns), r))
check("...and records where it went", ((row(ns, "octo/lib") or {}).get("moved") or {})
      .get("to") == "octo/core", row(ns, "octo/lib"))
fake.stop()

print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
