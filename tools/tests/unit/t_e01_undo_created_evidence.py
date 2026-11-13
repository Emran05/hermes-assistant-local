#!/usr/bin/env python3
"""E01 review (2026-11-10): the premises behind "the agent created this file".

t_e01_undo_created_proof.py covers the finding's own cases (an edit to a file
the store never snapshots). These are the ways a file that WAS there before
the agent's write_file could still pass for "created" — each one moved the
owner's file to the undo trash with the note "the agent had created this
file" (reproduced against Hermes' real checkpoint manager):

  1. a Telegram / gateway turn never takes a snapshot; the recorder matched
     its write with a snapshot another turn took up to 15 minutes earlier,
     which does not hold a file made in between;
  2. the same stale match on any surface, when the agent READ the file and
     then rewrote it whole — the read came after the snapshot, so the "touched
     before the snapshot" rule did not see it;
  3. a file over the store's size cap before the write and small after it;
  4. a gitignored file, once the .gitignore line that hid it from the
     snapshot is gone (git is asked about today's rules);
and one restore that reached further than its row:
  5. Undo of a file whose NAME git reads as a pattern ("*.txt") restored
     every file the pattern matched, the owner's later edits to them included.

Drives the REAL dashboard/aux_recorder.py and its REAL checkpoint driver
against real git, with the vendored stand-in for Hermes' checkpoint manager
(fixtures/e01_checkpoint_manager.py) in a scratch HOME. The agent's writes
are made the way Hermes' write_file makes them (upstream _atomic_write): a
0600 temp file renamed over the target, keeping an existing target's mode.
"""
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return os.environ.get("HERMES_REPO") or os.path.dirname(
        os.path.dirname(os.path.dirname(_HERE)))


REPO = _find_repo()
AUX = os.path.join(REPO, "dashboard", "aux_recorder.py")
FIXTURE = os.path.join(_HERE, "fixtures", "e01_checkpoint_manager.py")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


class Ctx:
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}

    def q1(self, k, d=""):
        return self.query.get(k, d)


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_e01b", "HOME": home,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(home, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def owner_write(p, b, mode=0o644):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b)
    os.chmod(p, mode)


def agent_write(p, b):
    """Hermes' write_file: mktemp (0600) beside the target, an existing
    target's mode copied onto it, then a rename over the target."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hermes-tmp.", dir=os.path.dirname(p))
    if os.path.exists(p):
        os.chmod(tmp, stat.S_IMODE(os.stat(p).st_mode))
    with os.fdopen(fd, "wb") as f:
        f.write(b)
    os.replace(tmp, p)


OLD_HOME = os.environ.get("HOME")
HOME = os.path.realpath(tempfile.mkdtemp(prefix="hermes-e01b-"))
os.environ["HOME"] = HOME                 # the driver subprocess inherits it
try:
    tools = os.path.join(HOME, ".hermes", "hermes-agent", "tools")
    os.makedirs(tools)
    open(os.path.join(tools, "__init__.py"), "w").close()
    shutil.copy(FIXTURE, os.path.join(tools, "checkpoint_manager.py"))
    sys.path.insert(0, os.path.join(HOME, ".hermes", "hermes-agent"))
    from tools.checkpoint_manager import CheckpointManager      # the fixture
    G = build(HOME)
    TRASH = G["UNDO_TRASH"]
    N = [0]

    def trash():
        return sorted(os.listdir(TRASH)) if os.path.isdir(TRASH) else []

    def sql(q, args=()):
        con = sqlite3.connect(G["REC_DB"])
        try:
            out = con.execute(q, args).fetchall()
            con.commit()
            return out
        finally:
            con.close()

    def record(tool, path, source="hub", origin="ws", kind="write", ts=None):
        """One recorder row, as the live hook / the state.db reader leaves it."""
        N[0] += 1
        tc = "tc%d" % N[0]
        sql("INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
            " reversible, status, origin) VALUES(?,?,?,?,?,?,?,?, 'done', ?)",
            (tc, ts or time.time(), "s", source, tool, path, kind,
             "no" if kind == "write" else "n/a", origin))
        return tc, sql("SELECT id FROM actions WHERE tool_call_id=?", (tc,))[0][0]

    def finalize(tc, path):
        G["_finalize_ws"](tc, path, time.time(), "write")

    def payload(aid):
        out = G["recorder_api_handler"](Ctx(query={"limit": "200"}))
        return next((a for a in out.get("actions", []) if a["id"] == aid), {})

    def undo(aid, force=False):
        """The handler's OWN decision: the finalize-time label is wiped first."""
        sql("UPDATE actions SET reversible='yes', undo_note='' WHERE id=? AND status='done'",
            (aid,))
        res = G["recorder_undo_handler"](Ctx({"id": aid, "force": force}))
        return res[0] if isinstance(res, tuple) else res

    def snapshot(folder, reason="before write_file"):
        time.sleep(1.1)                   # one snapshot per ISO second
        ok = CheckpointManager(enabled=True)._take(folder, reason)
        time.sleep(1.1)
        return ok

    def refused(name, aid, path, want, word):
        a = payload(aid)
        check("%s: the row says before any click that it cannot be undone" % name,
              a.get("reversible") == "no" and word in (a.get("undo_why") or ""),
              {k: a.get(k) for k in ("reversible", "undo_why", "undo_effect")})
        res = undo(aid)
        check("%s: Undo refuses and the file stays where it is, byte for byte" % name,
              res.get("ok") is False and read(path) == want and trash() == []
              and word in ("%s %s" % (res.get("error"), res.get("detail"))),
              (res, trash()))

    # ------------------------------------------------------------------
    print("\n== control: a file write_file really created is still undoable ==")
    proj = os.path.join(HOME, "proj")
    owner_write(os.path.join(proj, "package.json"), b"{}\n")
    owner_write(os.path.join(proj, "main.py"), b"x = 1\n")
    check("the hub turn's snapshot was taken", snapshot(proj) is True)
    made = os.path.join(proj, "made.txt")
    agent_write(made, b"made by the agent\n")
    tc, mid = record("write_file", made)
    finalize(tc, made)
    check("a created file has the mode write_file gives it (0600)",
          stat.S_IMODE(os.stat(made).st_mode) == 0o600)
    check("...and its row offers the trash Undo", payload(mid).get("undo_effect") == "trash",
          payload(mid))

    # ------------------------------------------------------------------
    print("\n== 1. a Telegram write matched with another turn's snapshot ==")
    notes = os.path.join(proj, "notes.md")
    owner_write(notes, b"the owner's notes, written after the snapshot\n", 0o600)
    agent_write(notes, b"agent rewrite\n")
    tc, nid = record("write_file", notes, source="telegram", origin="statedb")
    G["_finalize_pass"]()                 # the state.db leg's own finalizer
    row = sql("SELECT snapshot_ref FROM actions WHERE id=?", (nid,))[0]
    check("the stale match happens (the row did get the hub turn's snapshot)", bool(row[0]), row)
    refused("telegram", nid, notes, b"agent rewrite\n", "never snapshotted")

    # ------------------------------------------------------------------
    print("\n== 2. read, then rewritten whole — the read came after the snapshot ==")
    todo = os.path.join(proj, "todo.md")
    owner_write(todo, b"the owner's list\n", 0o600)
    record("read_file", todo, kind="read")
    time.sleep(0.05)
    agent_write(todo, b"the owner's list (retyped by the agent)\n")
    tc, tid = record("write_file", todo)
    finalize(tc, todo)
    refused("read-then-rewrite", tid, todo, b"the owner's list (retyped by the agent)\n",
            "used before this write")

    # ------------------------------------------------------------------
    print("\n== 3. over the size cap before the write, small after ==")
    big = os.path.join(proj, "data.csv")
    os.makedirs(proj, exist_ok=True)
    with open(big, "wb") as f:
        f.truncate(11 * 1024 * 1024)
    os.chmod(big, 0o644)
    check("a fresh snapshot is taken with the 11 MB file there",
          snapshot(proj, "before write_file") is True)
    agent_write(big, b"a,b\n1,2\n")
    tc, bid = record("write_file", big)
    finalize(tc, big)
    check("the snapshot does not hold it (size prune) and the write kept its mode",
          stat.S_IMODE(os.stat(big).st_mode) == 0o644)
    refused("was over 10 MB", bid, big, b"a,b\n1,2\n", "permissions (644)")

    # ------------------------------------------------------------------
    print("\n== 4. the .gitignore line that hid it from the snapshot is gone ==")
    p4 = os.path.join(HOME, "proj4")
    owner_write(os.path.join(p4, "package.json"), b"{}\n")
    owner_write(os.path.join(p4, ".gitignore"), b"secrets.json\n")
    owner_write(os.path.join(p4, "cfg", ".gitignore"), b"local.json\n")
    owner_write(os.path.join(p4, "cfg", "app.json"), b"{}\n")
    sec = os.path.join(p4, "secrets.json")
    loc = os.path.join(p4, "cfg", "local.json")
    owner_write(sec, b'{"owner": "secret"}\n', 0o600)
    owner_write(loc, b'{"owner": "local"}\n', 0o600)
    check("snapshot taken", snapshot(p4) is True)
    agent_write(os.path.join(p4, ".gitignore"), b"# nothing ignored any more\n")
    os.remove(os.path.join(p4, "cfg", ".gitignore"))
    agent_write(sec, b'{"agent": 1}\n')
    agent_write(loc, b'{"agent": 2}\n')
    tc, sid = record("write_file", sec)
    finalize(tc, sec)
    tc2, lid = record("write_file", loc)
    finalize(tc2, loc)
    refused("un-ignored since (root .gitignore edited)", sid, sec, b'{"agent": 1}\n',
            ".gitignore changed since the snapshot")
    refused("un-ignored since (nested .gitignore deleted)", lid, loc, b'{"agent": 2}\n',
            ".gitignore changed since the snapshot")

    # ------------------------------------------------------------------
    print("\n== 5. Undo of ONE file restores one file, whatever its name ==")
    p5 = os.path.join(HOME, "proj5")
    owner_write(os.path.join(p5, "package.json"), b"{}\n")
    star = os.path.join(p5, "*.txt")
    owner_write(star, b"owner star\n")
    owner_write(os.path.join(p5, "a.txt"), b"owner a\n")
    owner_write(os.path.join(p5, "sub", "b.txt"), b"owner b\n")
    check("snapshot taken", snapshot(p5, "before patch") is True)
    agent_write(star, b"agent star\n")
    agent_write(os.path.join(p5, "a.txt"), b"agent a\n")
    tc, xid = record("patch", star)
    finalize(tc, star)
    # the owner keeps working on another file afterwards
    owner_write(os.path.join(p5, "sub", "b.txt"), b"owner b, edited later\n")
    res = undo(xid)
    check("the file named *.txt is restored", res.get("ok") is True
          and read(star) == b"owner star\n", res)
    check("a.txt (another row's file) is NOT restored with it",
          read(os.path.join(p5, "a.txt")) == b"agent a\n", read(os.path.join(p5, "a.txt")))
    check("sub/b.txt keeps the owner's later edit",
          read(os.path.join(p5, "sub", "b.txt")) == b"owner b, edited later\n",
          read(os.path.join(p5, "sub", "b.txt")))

    # ------------------------------------------------------------------
    print("\n== the control still works at the end: created -> trash -> back ==")
    res = undo(mid)
    check("the proven created file goes to the undo trash", res.get("ok") is True
          and not os.path.exists(made) and len(trash()) == 1, res)

    print("\n== the decision itself (pure) ==")
    P = G["_rec_created_proof"]
    good = {"ok": True, "in_snapshot": False, "case_twin": "", "ignored": False,
            "rules_changed": ""}
    base = dict(tool="write_file", after={"exists": True, "size": 5}, probe=good,
                size_now=5, cap=10 * 1024 * 1024, touched_before=False,
                mode_now=0o600, source="hub")
    check("every piece of evidence present = proven", P(**base)[0] is True, P(**base))
    for k, v, why in (("mode_now", 0o644, "mode 0644"), ("mode_now", 0o755, "mode 0755"),
                      ("mode_now", None, "mode unknown"),
                      ("source", "telegram", "a Telegram turn"),
                      ("source", "Discord", "a Discord turn"),
                      ("source", "api_server", "a gateway API turn"),
                      ("probe", dict(good, rules_changed=".gitignore"), ".gitignore changed"),
                      ("probe", {k: v for k, v in good.items() if k != "rules_changed"},
                       "a probe that did not compare the .gitignore files")):
        check("not proven: %s" % why, P(**dict(base, **{k: v}))[0] is False)
    check("the default (no mode given) is NOT proven",
          P("write_file", {"exists": True, "size": 5}, good, 5, 10 * 1024 * 1024, False)[0]
          is False)
finally:
    if OLD_HOME is not None:
        os.environ["HOME"] = OLD_HOME
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
