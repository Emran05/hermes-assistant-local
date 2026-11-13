#!/usr/bin/env python3
"""E09, pipeline half (audit 2026-11-09): the dashboard's bare mirror must not
keep every object ever snapshotted.

Drives the REAL aux_ghchanges.py / gh_git.py through t_gh_changes.py's harness
(fake GitHub on loopback, file:// remotes, scratch HOME).

  (a) over-limit content is refused BEFORE `add -A` writes it into the mirror
      (413 too_large, C-size, no change stored, no blob in the mirror); an
      ignored file never counts;
  (b) after a withdraw, a reject, an expiry or a blocked request, that change's
      objects are pruned from the mirror, while a PENDING change's objects
      survive every prune and it still pushes;
  (c) _ghc_delete_mirror(owner, repo) / _ghc_delete_all_mirrors() delete
      mirrors (pending changes cancelled first, refused while one is pushing,
      a planted symlink is removed without following it).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def main():
    w = H.World("e09")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.repo("octo/two", {"a.txt": "a\n"})
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app", "octo/two"])
    sb = H.prepare(ns, "octo/app")
    H.prepare(ns, "octo/two")
    mirror = ns["_ghc_mirror_path"]("octo/app")
    n = [0]

    def has(sha):
        return H.tgit(["cat-file", "-t", sha], git_dir=mirror, check_ok=False) == "blob"

    def put(rel, data):
        p = H.write(sb, rel, data)
        return H.tgit(["hash-object", p])

    def reset():
        H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
        H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)

    def req(**kw):
        n[0] += 1
        return H.request(ns, "octo/app", "e09-%03d" % n[0], **kw)

    def changes():
        return len(os.listdir(os.path.join(ns["_ghc_root"](), "changes")))

    print("== (a) over-limit content is refused before it is stored ==")
    lim = ns["GH_LIMITS"]
    saved = dict(lim)
    lim.update(max_file=64 * 1024)
    big = put("data/dump.bin", os.urandom(200 * 1024))
    before = changes()
    st, r = req()
    check("a file over max_file -> 413 too_large (C-size)", st == 413
          and r.get("error") == "too_large" and r.get("check") == "C-size"
          and "data/dump.bin" in r.get("hint", ""), (st, r))
    check("...no change was stored", changes() == before, changes())
    check("...and the file's blob never entered the mirror", not has(big), big)
    lim.update(saved)
    lim.update(max_bytes=100 * 1024)
    reset()
    b1 = put("data/a.bin", os.urandom(60 * 1024))
    b2 = put("data/b.bin", os.urandom(60 * 1024))
    st, r = req()
    check("new files adding up over max_bytes -> 413, neither blob stored", st == 413
          and not has(b1) and not has(b2), (st, r))
    lim.update(saved)
    lim.update(max_files=3)
    reset()
    for i in range(4):
        put("many/f%d.txt" % i, "%d\n" % i)
    st, r = req()
    check("more files than max_files -> 413", st == 413 and r.get("error") == "too_large", (st, r))
    lim.update(saved)
    lim.update(max_file=64 * 1024)
    reset()
    H.write(sb, "src/login.py", ("COPY = 'x'\n" * 10000))
    st, r = req()
    check("a TRACKED file grown over max_file -> 413", st == 413, (st, r))
    reset()
    ign = put("build/out.bin", os.urandom(200 * 1024))
    H.write(sb, ".gitignore", "build/\n")
    H.write(sb, "README.md", "# app\n\nIgnored build output.\n")
    st, r = req()
    check("an ignored big file does not count (200) and is not stored", st == 200
          and r.get("approvable") and not has(ign), (st, r.get("error")))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    lim.update(saved)

    print("\n== (b) refused, withdrawn, rejected, expired changes are pruned ==")
    reset()
    keep = put("keep/pending.txt", "pending content %s\n" % os.urandom(8).hex())
    st, r = req()
    p1 = r.get("id")
    check("a pending change (P1) is stored and its blob is in the mirror",
          st == 200 and r.get("approvable") and has(keep), (st, r.get("error")))
    reset()
    wd = put("w/withdrawn.txt", "withdrawn %s\n" % os.urandom(8).hex())
    st, r = req()
    check("a second change's blob is in the mirror", st == 200 and has(wd))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    check("withdrawn -> its blob is pruned", not has(wd), wd)
    check("...P1's blob survives the prune", has(keep), keep)
    reset()
    rj = put("r/rejected.txt", "rejected %s\n" % os.urandom(8).hex())
    st, r = req()
    st2, r2 = H.decide(ns, r.get("id"), "reject", headers=None)
    check("rejected -> its blob is pruned", st2 == 200 and not has(rj), (st2, r2))
    reset()
    secret = ns["ENV_SECRETS"]["OPENAI_API_KEY"]
    bl = put("conf/leak.py", "KEY = '%s'  # %s\n" % (secret, os.urandom(4).hex()))
    st, r = req()
    check("a blocked request -> stored as blocked, its blob pruned at once",
          st == 200 and r.get("blocked") and not has(bl), (st, r.get("blocked")))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    reset()
    ex = put("e/expired.txt", "expired %s\n" % os.urandom(8).hex())
    st, r = req()
    eid = r.get("id")
    path = os.path.join(ns["_ghc_root"](), "changes", eid + ".json")
    with open(path) as f:
        rec = json.load(f)
    rec["expires_at"] = time.time() - 5
    with open(path, "w") as f:
        json.dump(rec, f)
    check("before anything looks, the soon-expired blob is still there", has(ex))
    reset()
    H.write(sb, "README.md", "# app\n\nnext\n")
    st, r = req()
    check("expired -> pruned on the next request to that repository",
          (H.get_change(ns, eid) or {}).get("state") == "expired" and not has(ex), eid)
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    check("P1's blob survives all of it", has(keep), keep)
    st, r = H.decide(ns, p1, "push_only")
    check("...and P1 still pushes", st == 200 and r.get("state") == "pushed", (st, r))
    rem = w.bare_path("octo/app")
    check("...with its content on the remote",
          H.tgit(["cat-file", "-t", keep], git_dir=rem, check_ok=False) == "blob")

    print("\n== (c) deleting mirrors ==")
    reset()
    H.write(sb, "README.md", "# app\n\nlast\n")
    st, r = req()
    p3 = r.get("id")
    res = ns["_ghc_delete_mirror"]("octo", "app")
    check("_ghc_delete_mirror deletes it", res.get("ok") and res.get("deleted")
          and not os.path.lexists(mirror), res)
    check("...its pending change was cancelled first",
          (H.get_change(ns, p3) or {}).get("state") == "cancelled", H.get_change(ns, p3))
    check("...a bad name is refused", ns["_ghc_delete_mirror"]("octo", "../x").get("ok") is False)
    check("...deleting an absent mirror is fine", ns["_ghc_delete_mirror"]("octo", "app")
          == {"ok": True, "deleted": False})
    two = ns["_ghc_mirror_path"]("octo/two")
    sb2 = ns["_ghc_sandbox"]("octo/two")
    H.write(sb2, "a.txt", "b\n")
    st, r = H.request(ns, "octo/two", "e09-two")
    tid = r.get("id")
    ns["_ghc_set"](tid, state="pushing")
    ns["_GHC_INFLIGHT"].add(tid)
    res = ns["_ghc_delete_mirror"]("octo", "two")
    check("a change being pushed -> refused, mirror kept", res.get("ok") is False
          and res.get("error") == "pushing" and os.path.isdir(two), res)
    ns["_GHC_INFLIGHT"].discard(tid)
    ns["_ghc_set"](tid, state="withdrawn")
    outside = os.path.join(w.tmp, "outside")
    os.makedirs(outside)
    H.write(outside, "precious.txt", "keep me\n")
    link = os.path.join(os.path.dirname(two), "evil__link.git")
    os.symlink(outside, link)
    res = ns["_ghc_delete_all_mirrors"]()
    check("_ghc_delete_all_mirrors deletes every real mirror", res.get("deleted") == 1
          and not os.path.lexists(two), res)
    check("...a planted symlink is removed without following it",
          not os.path.lexists(link) and os.path.isfile(os.path.join(outside, "precious.txt")))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
