#!/usr/bin/env python3
"""E08 (server half) — the scope wall's write-reach result reaches the card.

The wall's "All repositories" probe is a SAMPLE: it tries to write a blob to
an unlisted public repository of the owner. With nothing to sample (no other
public repository, or only empty ones) an "All repositories" key was stored
as `write_reach: unchecked` silently, the status payload never carried the
value, and the headline said the key "can only touch the repositories you
picked". A selected-repositories key whose one sampled repository answered
403 was labelled `contained`, as if containment were proven.

Now: status carries top-level `write_reach` (as stored) and, whenever it is
not proven, a plain `write_reach_note`; `unchecked` also comes back as a
connect WARNING; a single refused sample is stored as `sampled`, never
`contained`; the headline no longer claims what the wall cannot prove.
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


def run(build, repos):
    fake = _gh_fake.FakeGitHub()
    tok = build(fake)
    base = fake.start()
    ns = _gh_fake.load_connector(base)
    st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": repos})
    s = ns["call"]("GET", "/api/github/status", query={"scan": "0"})[1]
    return fake, ns, st, p, s


OVERCLAIM = "the key can only touch the repositories you picked"

print("== (c) All repositories, nothing to sample ==")


def c(f):
    f.user("one", 3)
    f.repo("one/app", private=True)
    return f.profile("all-repos", "one")


fake, ns, st, p, s = run(c, "one/app")
check("stored (the wall is sample-based by design)", st == 200 and p.get("stored"), (st, p))
check("status carries write_reach exactly as stored: unchecked",
      s.get("write_reach") == "unchecked", s.get("write_reach"))
check("...with a plain note that it could not be checked",
      "could not check" in (s.get("write_reach_note") or ""), s.get("write_reach_note"))
check("connect answers it as a warning the card shows",
      any("could not check" in w for w in p.get("warnings") or []), p.get("warnings"))
check("the headline no longer asserts containment", OVERCLAIM not in s.get("headline", ""),
      s.get("headline"))
fake.stop()

print("== (b) All repositories, three empty public repos ==")


def b(f):
    f.user("solo", 2)
    f.repo("solo/app", private=True)
    for n in ("a-empty1", "a-empty2", "a-empty3", "www"):
        f.repo("solo/" + n, private=False)
    f.fail_on("POST", r"^/repos/solo/a-empty\d/git/blobs$", 409, message="Git Repository is empty.")
    return f.profile("all-repos", "solo")


fake, ns, st, p, s = run(b, "solo/app")
check("unchecked, with the note", s.get("write_reach") == "unchecked"
      and s.get("write_reach_note"), (s.get("write_reach"), s.get("write_reach_note")))
fake.stop()

print("== (a) one sampled repository refused the write ==")


def a(f):
    f.user("octo", 1)
    f.repo("octo/app", private=True)
    f.repo("octo/aaa-site", private=False)
    return f.token(_gh_fake.fg_token(), "octo", repos=["octo/app"])


fake, ns, st, p, s = run(a, "octo/app")
check("stored", st == 200 and p.get("stored"), (st, p.get("lines")))
check("not labelled 'contained' (a single 403 proves nothing about the rest)",
      s.get("write_reach") == "sampled", s.get("write_reach"))
check("...and the note says it was a sample",
      "sample" in (s.get("write_reach_note") or ""), s.get("write_reach_note"))
check("...no warning for that (the key is not All repositories)",
      not any("could not check" in w for w in p.get("warnings") or []), p.get("warnings"))
fake.stop()

print("== control: All repositories with something to sample is still refused ==")


def ctrl(f):
    f.user("ctl", 4)
    f.repo("ctl/app", private=True)
    f.repo("ctl/www", private=False)
    return f.profile("all-repos", "ctl")


fake, ns, st, p, s = run(ctrl, "ctl/app")
check("422, nothing stored", st == 422 and not p.get("stored")
      and not os.path.exists(os.path.join(ns["GH_DIR"], "credential.json")), (st, p))
check("disconnected status: write_reach is None, no note",
      s.get("write_reach") is None and s.get("write_reach_note") is None,
      (s.get("write_reach"), s.get("write_reach_note")))
fake.stop()

print("== a legacy 'contained' is reported as the sample it was, with the honest note ==")
fake, ns, st, p, s = run(a, "octo/app")
ns["_gh_state_update"](lambda st_: st_["probe"]["checks"].__setitem__("write_reach", "contained"))
s = ns["call"]("GET", "/api/github/status", query={"scan": "0"})[1]
# review: the card (aux_github.js) reads "contained" as PROVEN and hides the
# warning, so an existing install would keep the unqualified claim
check("reported as 'sampled' (never 'contained', which the card reads as proven)",
      s.get("write_reach") == "sampled", s.get("write_reach"))
check("note present (never proven)", "sample" in (s.get("write_reach_note") or ""),
      s.get("write_reach_note"))
fake.stop()

print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
