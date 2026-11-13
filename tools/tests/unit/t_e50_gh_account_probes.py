#!/usr/bin/env python3
"""E50 — the scope wall reads the ACCOUNT permissions that widen reach.

A fine-grained key with the account permission "Git SSH keys" can add an SSH
key to the owner's account and so reach every repository over SSH; "GPG keys"
and "SSH signing keys" are the same family. The wall probed five repository
permissions only, so such a key was stored under a headline that said it
could only touch the picked repositories.

Now the wall and the 6-hourly check make one read each (GET /user/keys,
/user/gpg_keys, /user/ssh_signing_keys): 200 = the permission is present =
refuse (connect/add) or hold writes (check); 403/404 = absent; anything else
fails closed (connect: retry, nothing stored). The check stays GET-only.
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

print("== connect refuses a key with an account permission ==")
for perm, path, word in (("git_ssh_keys", "/user/keys", "SSH keys"),
                         ("gpg_keys", "/user/gpg_keys", "GPG keys"),
                         ("ssh_signing_keys", "/user/ssh_signing_keys", "signing keys")):
    ns = _gh_fake.load_connector(BASE)
    tok = fake.profile("clean", "octo", ["octo/app"])
    fake.tokens[tok]["perms"][perm] = "write"
    st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app"})
    check("%s: refused 422, nothing stored" % perm, st == 422 and not p.get("stored")
          and not os.path.exists(os.path.join(ns["GH_DIR"], "credential.json")), (st, p))
    check("%s: the line names the permission" % perm,
          any(word in ln for ln in p.get("lines") or []), p.get("lines"))
    check("%s: a delete-this-token line follows" % perm, bool(p.get("final")), p.get("final"))

ns = _gh_fake.load_connector(BASE)
tok = fake.profile("clean", "octo", ["octo/app"])
n0 = len(fake.log)
st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app"})
check("a clean key still connects", st == 200 and p.get("stored"), (st, p.get("lines")))
paths = [c["path"] for c in fake.log[n0:] if c["method"] == "GET"]
check("...after reading all three account endpoints",
      all(x in paths for x in ("/user/keys", "/user/gpg_keys", "/user/ssh_signing_keys")), paths)
check("...recorded as absent in the wall's checks",
      all(p["checks_passed"].get(k) == "absent"
          for k in ("git_ssh_keys", "gpg_keys", "ssh_signing_keys")), p.get("checks_passed"))

ns5 = _gh_fake.load_connector(BASE)
tok5 = fake.profile("clean", "octo", ["octo/app"])
fake.fail_on("GET", r"^/user/keys$", 502, times=1)
st, p = ns5["call"]("POST", "/api/github/connect", {"token": tok5, "repos": "octo/app"})
check("a 5xx on an account probe fails closed (retry, nothing stored)",
      st == 503 and p.get("retry") and not os.path.exists(
          os.path.join(ns5["GH_DIR"], "credential.json")), (st, p.get("lines")))

print("== the 6-hourly check notices it, GET-only ==")
fake.tokens[tok]["perms"]["git_ssh_keys"] = "read"
ns["_GH_CHECK_LAST"]["at"] = 0.0
n0 = len(fake.log)
r = ns["_gh_run_check"](force=True)
check("the check makes no write request", all(c["method"] == "GET" for c in fake.log[n0:]))
holds = ns["_gh_state"]()["holds"]
check("an account permission added on GitHub holds writes",
      "permission" in holds and "SSH keys" in holds["permission"]["text"], (r, holds))
fake.tokens[tok]["perms"]["git_ssh_keys"] = None
ns["_GH_CHECK_LAST"]["at"] = 0.0
r = ns["_gh_run_check"](force=True)
check("removed again: the next check lifts the hold", "permission" not in
      ns["_gh_state"]()["holds"], (r, ns["_gh_state"]()["holds"]))

print("== repos add re-runs the account probes too ==")
fake.repo("octo/lib", private=True)
fake.tokens[tok]["repos"].append("octo/lib")
fake.tokens[tok]["perms"]["gpg_keys"] = "read"
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
check("add refuses and holds", st == 422 and p.get("held") is True, (st, p))
fake.tokens[tok]["perms"]["gpg_keys"] = None
st, p = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
check("fixed on GitHub: add passes and its full wall lifts the hold",
      st == 200 and "permission" not in ns["_gh_state"]()["holds"], (st, p.get("lines")))

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
