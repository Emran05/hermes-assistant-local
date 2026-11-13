#!/usr/bin/env python3
"""E09 (connector half) — Disconnect, Revoke and repository removal delete
Hermes's bare mirrors (full copies of private repositories) and say what was
deleted and what was left.

Before: `~/.hermes/dashboard/github/mirrors/<owner>__<repo>.git` stayed on
disk after every one of the three and the answers never mentioned it.

Now the connector calls the change pipeline's `_ghc_delete_mirror(owner,
repo)` / `_ghc_delete_all_mirrors()` BY NAME AT CALL TIME and, when they are
absent, falls back to its own deletion: only an entry that is a direct child
of the mirrors folder, has the `<owner>__<repo>.git` shape and is a real
directory (never a symlink) is removed. The agent's sandbox clones under
~/HermesSandbox are the owner's: kept, and the answer says so.
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
fake.repo("octo/Lib", private=True)
BASE = fake.start()


def setup(extra=None):
    tok = fake.token(_gh_fake.fg_token(), "octo", repos=["octo/app", "octo/Lib"])
    ns = _gh_fake.load_connector(BASE, extra=extra)
    st, p = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app\nocto/Lib"})
    assert st == 200, (st, p)
    mdir = os.path.join(ns["GH_DIR"], "mirrors")
    os.makedirs(mdir, exist_ok=True)
    for name in ("octo__app.git", "octo__lib.git"):
        os.makedirs(os.path.join(mdir, name, "objects"), exist_ok=True)
        open(os.path.join(mdir, name, "objects", "pack"), "w").write("x")
    # hostile neighbours: a symlink with the right shape pointing outside, and
    # a file that is not a mirror
    outside = os.path.join(ns["HOME"], "precious")
    os.makedirs(outside)
    open(os.path.join(outside, "keep.txt"), "w").write("keep")
    os.symlink(outside, os.path.join(mdir, "evil__x.git"))
    open(os.path.join(mdir, "notes.txt"), "w").write("not a mirror")
    sb = ns["_gh_sandbox_path"]("octo/Lib")
    os.makedirs(os.path.join(sb, ".git"))
    return ns, mdir, outside, sb


print("== fallback (the pipeline's exports are absent) ==")
ns, mdir, outside, sb = setup()
st, p = ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/Lib"})
check("remove: 200", st == 200, (st, p))
check("remove deletes that repository's mirror (lower-case name)",
      not os.path.exists(os.path.join(mdir, "octo__lib.git")), os.listdir(mdir))
check("...and only that one", os.path.isdir(os.path.join(mdir, "octo__app.git")))
check("the answer names what was deleted", any(x.endswith("octo__lib.git")
                                               for x in p.get("mirrors_deleted") or []), p)
check("the sandbox clone is kept and the answer says it is the owner's",
      os.path.isdir(os.path.join(sb, ".git")) and sb in (p.get("sandboxes_kept") or [])
      and "HermesSandbox" in (p.get("cleanup_note") or ""), (p.get("sandboxes_kept"),
                                                               p.get("cleanup_note")))
st, p = ns["call"]("POST", "/api/github/disconnect", {})
check("disconnect deletes every remaining mirror",
      not os.path.exists(os.path.join(mdir, "octo__app.git")), os.listdir(mdir))
check("...reports it", any(x.endswith("octo__app.git") for x in p.get("mirrors_deleted") or []),
      p.get("mirrors_deleted"))
check("a symlink shaped like a mirror is never followed: its target is intact",
      os.path.isfile(os.path.join(outside, "keep.txt")))
check("...and it is reported as left, not deleted",
      any(x.endswith("evil__x.git") for x in p.get("mirrors_left") or []), p.get("mirrors_left"))
check("a file that is not a mirror is untouched", os.path.isfile(os.path.join(mdir, "notes.txt")))
check("the sandbox clone survives disconnect", os.path.isdir(os.path.join(sb, ".git")))
check("the disconnect answer says the sandboxes are the owner's to delete",
      "HermesSandbox" in (p.get("cleanup_note") or ""), p.get("cleanup_note"))

print("== a mirrors folder that is itself a symlink is never touched ==")
ns, mdir, outside, sb = setup()
real = mdir + "-real"
os.rename(mdir, real)
os.symlink(real, mdir)
st, p = ns["call"]("POST", "/api/github/disconnect", {})
check("nothing deleted through a symlinked mirrors folder",
      os.path.isdir(os.path.join(real, "octo__app.git")) and not p.get("mirrors_deleted"),
      (os.listdir(real), p.get("mirrors_deleted")))

print("== revoke deletes them too ==")
ns, mdir, outside, sb = setup()
st, p = ns["call"]("POST", "/api/github/revoke", {})
check("revoke: 200 and mirrors gone", st == 200 and not os.path.exists(
    os.path.join(mdir, "octo__app.git")) and not os.path.exists(os.path.join(mdir, "octo__lib.git")),
      (st, os.listdir(mdir)))
check("...reported", len(p.get("mirrors_deleted") or []) == 2, p.get("mirrors_deleted"))

print("== the pipeline's exports are used by name when present ==")
calls = []


def del_one(owner, repo):
    calls.append(("one", owner, repo))
    import shutil
    shutil.rmtree(os.path.join(ns2["GH_DIR"], "mirrors", ("%s__%s.git" % (owner, repo)).lower()))
    return True


def del_all():
    calls.append(("all",))
    return 0                                  # deletes nothing: the answer must say so


ns2, mdir2, outside2, sb2 = setup(extra={"_ghc_delete_mirror": del_one,
                                         "_ghc_delete_all_mirrors": del_all})
st, p = ns2["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/Lib"})
check("remove calls _ghc_delete_mirror(owner, repo)", ("one", "octo", "Lib") in calls, calls)
check("...and reports from what is on disk", any(x.endswith("octo__lib.git")
                                                 for x in p.get("mirrors_deleted") or []), p)
st, p = ns2["call"]("POST", "/api/github/disconnect", {})
check("disconnect calls _ghc_delete_all_mirrors()", ("all",) in calls, calls)
check("...and when it left a mirror, the answer says so (no silent fallback over it)",
      any(x.endswith("octo__app.git") for x in p.get("mirrors_left") or []),
      p.get("mirrors_left"))


def boom(*a):
    raise OSError("busy")


ns3, mdir3, _o, _s = setup(extra={"_ghc_delete_mirror": boom, "_ghc_delete_all_mirrors": boom})
st, p = ns3["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/Lib"})
check("a failing export never breaks remove; the mirror is reported left",
      st == 200 and any(x.endswith("octo__lib.git") for x in p.get("mirrors_left") or []),
      (st, p.get("mirrors_left")))
check("...and the raw exception text is not in the answer", "busy" not in str(p))

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
