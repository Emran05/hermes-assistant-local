#!/usr/bin/env python3
"""E01 (2026-11-09 audit, P1): Undo never moves a file it cannot PROVE the
agent created.

The recorder's Undo restores a write from Hermes' checkpoint store. That store
never snapshots `.env`, `.env.*`, `*.log`, `build/`, `dist/`, `venv/`, anything
the project's own .gitignore names, or files over 10 MB. Undo on an agent EDIT
to such a file got git's "pathspec did not match" error back and read it as
"the agent created this file": the owner's file was moved to the undo trash,
undo-of-the-undo failed, and the trash sweep deleted it 14 days later.

Now "created" needs evidence (a write_file — never a patch — of a path that is
absent from the snapshot, that the store WOULD have snapshotted: not excluded,
not gitignored, not over the size cap, no name-case twin in the snapshot, no
earlier recorded touch, and the 0600 mode write_file gives a file it creates —
t_e01_undo_created_evidence.py). Anything else is refused and the file is left
alone; rows that can be judged ahead of time are marked not undoable in the
payload; an undo that did move a created file can itself be undone, from the
trash.

Drives the REAL dashboard/aux_recorder.py (exec'd like the other recorder
suites) and its REAL checkpoint driver subprocess, against real git, with a
stand-in for Hermes' checkpoint manager (fixtures/e01_checkpoint_manager.py:
the upstream exclusion list, size prune and restore, vendored as data) in a
scratch HOME. Nothing outside the scratch folder is read or written.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
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
    g = {"__name__": "aux_recorder_e01", "HOME": home,
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


def write(p, b):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b)


OLD_HOME = os.environ.get("HOME")
HOME = os.path.realpath(tempfile.mkdtemp(prefix="hermes-e01-"))
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

    proj = os.path.join(HOME, "proj")
    os.makedirs(proj)
    write(os.path.join(proj, "package.json"), b"{}\n")          # a project marker
    write(os.path.join(proj, ".gitignore"), b"local.settings.json\n")
    owner = {
        ".env": (b"OWNER_SECRET=1\n", "write_file"),
        ".env.example": (b"EXAMPLE=1\n", "patch"),
        "debug.log": (b"owner log\n", "write_file"),
        "build/out.js": (b"// owner\n", "patch"),
        "local.settings.json": (b'{"owner": 1}\n', "patch"),
        "app.py": (b"print('owner original')\n", "patch"),
        "Readme.md": (b"owner readme\n", None),
        "big.dat": (None, "write_file"),      # 11 MB, shrunk by the agent
    }
    for rel, (body, _t) in owner.items():
        p = os.path.join(proj, rel)
        if body is None:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.truncate(11 * 1024 * 1024)
        else:
            write(p, body)
    big_csv = os.path.join(proj, "big.csv")              # stays 11 MB: oversize now
    with open(big_csv, "wb") as f:
        f.truncate(11 * 1024 * 1024)

    t0 = time.time()
    # an earlier recorded look at big.dat, BEFORE the turn's snapshot
    con = sqlite3.connect(G["REC_DB"])
    con.execute("INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
                " reversible, status, origin) VALUES('rd1', ?, 's', 'hub', 'read_file', ?,"
                " 'read', 'n/a', 'done', 'ws')", (t0 - 120, os.path.join(proj, "big.dat")))
    con.commit()
    con.close()
    time.sleep(1.1)                       # the snapshot's ISO second is after that read
    check("the turn's 'before write_file' snapshot was taken",
          CheckpointManager(enabled=True)._take(proj, "before write_file") is True)

    # ---- the agent's turn ------------------------------------------------
    edits = {".env": b"AGENT=1\n", ".env.example": b"AGENT_EX=1\n",
             "debug.log": b"agent log\n", "build/out.js": b"// agent\n",
             "local.settings.json": b'{"agent": 1}\n', "app.py": b"print('agent')\n",
             "big.dat": b"tiny\n", "big.csv": None,
             "brand_new.txt": b"made by the agent\n",
             "patched_new.txt": b"a patch that added a file\n",
             "README.md": b"agent readme\n"}
    tools_for = {"big.csv": "patch", "brand_new.txt": "write_file",
                 "patched_new.txt": "patch", "README.md": "write_file"}
    for rel, b in edits.items():
        p = os.path.join(proj, rel)
        if b is None:
            with open(p, "r+b") as f:
                f.seek(5)
                f.write(b"agent")
        else:
            was_there = os.path.exists(p)
            write(p, b)
            if not was_there:
                # Hermes' write_file creates a file 0600 (a mktemp file renamed
                # into place) and keeps the mode of one that was already there
                os.chmod(p, 0o600)
    after_agent = {rel: read(os.path.join(proj, rel)) for rel in edits}
    case_insensitive = os.path.exists(os.path.join(proj, "readme.MD"))

    ids = {}
    con = sqlite3.connect(G["REC_DB"])
    for i, rel in enumerate(edits):
        tool = tools_for.get(rel) or owner[rel][1]
        tgt = os.path.join(proj, rel)
        con.execute("INSERT INTO actions(tool_call_id, ts, session, source, tool, target,"
                    " kind, reversible, status, origin) VALUES(?,?,?,?,?,?, 'write', 'no',"
                    " 'done', 'ws')", ("tc%d" % i, time.time(), "s", "hub", tool, tgt))
        ids[rel] = con.execute("SELECT id FROM actions WHERE tool_call_id=?",
                               ("tc%d" % i,)).fetchone()[0]
    con.commit()
    con.close()
    # the live hook's own finalizer: after_state + association (+ the E01 hint)
    for i, rel in enumerate(edits):
        G["_finalize_ws"]("tc%d" % i, os.path.join(proj, rel),
                          time.time(), "write")

    def rows():
        out = G["recorder_api_handler"](Ctx(query={"limit": "100"}))
        return {a["id"]: a for a in out.get("actions", [])}

    R = rows()
    print("\n== the payload says, BEFORE any click, which rows cannot be undone ==")
    not_snap = [".env", ".env.example", "debug.log", "build/out.js",
                "local.settings.json", "big.csv", "patched_new.txt"]
    for rel in not_snap:
        a = R.get(ids[rel]) or {}
        check("%s: reversible='no' with the reason in the payload" % rel,
              a.get("reversible") == "no" and "no earlier copy" in (a.get("undo_why") or ""),
              {k: a.get(k) for k in ("reversible", "undo_why", "has_snapshot")})
    a = R.get(ids["app.py"]) or {}
    check("app.py (snapshotted) keeps its Undo, and its Undo does not remove anything",
          a.get("reversible") == "yes" and a.get("undo_effect") == "restore", a)
    a = R.get(ids["brand_new.txt"]) or {}
    check("brand_new.txt (proven created) keeps its Undo and says it moves the file to the trash",
          a.get("reversible") == "yes" and a.get("undo_effect") == "trash", a)

    # Owner-side: rows written before the hint (or whose hint failed) still
    # reach the handler — put every row back to 'yes' to drive the handler's
    # own decision, which must not trust the hint.
    con = sqlite3.connect(G["REC_DB"])
    con.execute("UPDATE actions SET reversible='yes', undo_note='' WHERE kind='write'")
    con.commit()
    con.close()

    def undo(rel, force=False):
        return G["recorder_undo_handler"](Ctx({"id": ids[rel], "force": force}))

    def trash():
        return sorted(os.listdir(TRASH)) if os.path.isdir(TRASH) else []

    print("\n== an edit to a file the store never snapshots is refused, file untouched ==")
    for rel in not_snap + ["big.dat"] + (["README.md"] if True else []):
        res = undo(rel)
        txt = "%s %s" % (res.get("error"), res.get("detail"))
        check("%s: refused (ok False), nothing moved" % rel,
              res.get("ok") is False and read(os.path.join(proj, rel)) == after_agent[rel]
              and trash() == [], (res, trash()))
        if rel != "README.md":
            check("%s: the refusal says Hermes has no earlier copy" % rel,
                  "no earlier copy" in txt, txt)
    con = sqlite3.connect(G["REC_DB"])
    st = dict(con.execute("SELECT id, reversible FROM actions WHERE id=?",
                          (ids[".env"],)).fetchall())
    con.close()
    check("a deterministic refusal marks the row not undoable (no Undo button next poll)",
          st.get(ids[".env"]) == "no", st)
    check("README.md (a case twin of the snapshot's Readme.md) still holds the agent's bytes",
          read(os.path.join(proj, "README.md")) == after_agent["README.md"], case_insensitive)

    print("\n== the force path is refused too ==")
    con = sqlite3.connect(G["REC_DB"])
    con.execute("UPDATE actions SET reversible='yes', undo_note='' WHERE id=?", (ids[".env"],))
    con.commit()
    con.close()
    write(os.path.join(proj, ".env"), b"OWNER_LATER=2\n")      # the owner edits it again
    r1 = undo(".env")
    check("without force: a conflict (or a refusal), never a move",
          r1.get("ok") is False and read(os.path.join(proj, ".env")) == b"OWNER_LATER=2\n", r1)
    con = sqlite3.connect(G["REC_DB"])
    con.execute("UPDATE actions SET reversible='yes', undo_note='' WHERE id=?", (ids[".env"],))
    con.commit()
    con.close()
    r2 = undo(".env", force=True)
    check("with force: refused, the owner's .env is still there, untouched",
          r2.get("ok") is False and read(os.path.join(proj, ".env")) == b"OWNER_LATER=2\n"
          and trash() == [], (r2, trash()))

    print("\n== a snapshotted edit still restores ==")
    res = undo("app.py")
    check("app.py restored to the owner's original",
          res.get("ok") is True and read(os.path.join(proj, "app.py")) == b"print('owner original')\n",
          res)

    print("\n== a proven created file goes to the trash, and that undo is undoable ==")
    nf = os.path.join(proj, "brand_new.txt")
    res = undo("brand_new.txt")
    check("ok, the file moved to the undo trash (never deleted)",
          res.get("ok") is True and not os.path.exists(nf) and res.get("trash")
          and read(res["trash"]) == b"made by the agent\n", res)
    con = sqlite3.connect(G["REC_DB"])
    con.row_factory = sqlite3.Row
    u = dict(con.execute("SELECT * FROM actions WHERE tool='undo' AND summary LIKE ?",
                         ("undo of #%d %%" % ids["brand_new.txt"],)).fetchone() or {})
    con.close()
    check("the undo row is itself undoable", u.get("reversible") == "yes"
          and u.get("status") == "done", u)
    R = rows()
    check("...and the payload says that Undo puts the file back from the trash",
          (R.get(u.get("id")) or {}).get("undo_effect") == "untrash", R.get(u.get("id")))
    # the reconciler's finalizer must not later strip that Undo (no snapshot needed)
    G["_finalize_pass"]()
    con = sqlite3.connect(G["REC_DB"])
    rv = con.execute("SELECT reversible FROM actions WHERE id=?", (u.get("id"),)).fetchone()
    con.close()
    check("the finalizer leaves the trash-backed undo row undoable", rv and rv[0] == "yes", rv)

    write(nf, b"something new in its place\n")
    res2 = G["recorder_undo_handler"](Ctx({"id": u.get("id"), "force": True}))
    check("undo-of-undo never overwrites a file now at that path, even with force",
          res2.get("ok") is False and read(nf) == b"something new in its place\n"
          and len(trash()) == 1, res2)
    os.remove(nf)
    res3 = G["recorder_undo_handler"](Ctx({"id": u.get("id")}))
    check("undo-of-undo puts the file back from the trash, byte for byte",
          res3.get("ok") is True and read(nf) == b"made by the agent\n" and trash() == [],
          (res3, trash()))
    con = sqlite3.connect(G["REC_DB"])
    con.row_factory = sqlite3.Row
    u2 = dict(con.execute("SELECT * FROM actions WHERE tool='undo' AND summary LIKE ?",
                          ("undo of #%d %%" % u.get("id"),)).fetchone() or {})
    con.close()
    res4 = G["recorder_undo_handler"](Ctx({"id": u2.get("id")}))
    check("...and undoing THAT moves it to the trash again",
          res4.get("ok") is True and not os.path.exists(nf) and len(trash()) == 1, res4)

    print("\n== the trash sweep (B14) is unchanged ==")
    G["_gc_trash"]()
    check("a fresh trash entry survives the sweep", len(trash()) == 1, trash())

    print("\n== a probe that cannot answer refuses (fail closed) ==")
    real_ckpt = G["_ckpt"]
    gone = os.path.join(proj, "second_new.txt")
    write(gone, b"x\n")
    con = sqlite3.connect(G["REC_DB"])
    con.execute("INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
                " reversible, status, snapshot_ref, after_state, origin) VALUES('tcx', ?, 's',"
                " 'hub', 'write_file', ?, 'write', 'yes', 'done', ?, ?, 'ws')",
                (time.time(), gone, json.dumps(G["_rec_json"](
                    sqlite3.connect(G["REC_DB"]).execute(
                        "SELECT snapshot_ref FROM actions WHERE id=?",
                        (ids["brand_new.txt"],)).fetchone()[0])),
                 json.dumps(G["_after_state"](gone))))
    xid = con.execute("SELECT id FROM actions WHERE tool_call_id='tcx'").fetchone()[0]
    con.commit()
    con.close()
    G["_ckpt"] = lambda op, **kw: ({"error": "driver broke"} if op == "probe"
                                   else real_ckpt(op, **kw))
    res = G["recorder_undo_handler"](Ctx({"id": xid}))
    check("probe error: refused, file left in place", res.get("ok") is False
          and read(gone) == b"x\n", res)
    G["_ckpt"] = real_ckpt

    print("\n== the decision itself (pure) ==")
    P = G["_rec_created_proof"]
    good = {"ok": True, "in_snapshot": False, "case_twin": False, "ignored": False,
            "rules_changed": ""}
    base = dict(tool="write_file", after={"exists": True, "size": 5}, probe=good,
                size_now=5, cap=10 * 1024 * 1024, touched_before=False,
                mode_now=0o600, source="hub")
    check("write_file + not in snapshot + would have been snapshotted = proven",
          P(**base)[0] is True)
    for k, v, why in (("tool", "patch", "a patch"), ("tool", "undo", "an undo row"),
                      ("probe", dict(good, ignored=True), "ignored"),
                      ("probe", dict(good, ignored=None), "ignore state unknown"),
                      ("probe", dict(good, in_snapshot=True), "in the snapshot"),
                      ("probe", dict(good, case_twin=True), "a case twin"),
                      ("probe", {"ok": False}, "a failed probe"),
                      ("probe", None, "no probe"),
                      ("size_now", 10 * 1024 * 1024 + 1, "over the cap now"),
                      ("after", {"exists": True, "size": 10 * 1024 * 1024 + 1}, "over the cap after"),
                      ("after", {"exists": False}, "the agent left no file"),
                      ("size_now", None, "no file now"),
                      ("touched_before", True, "an earlier touch")):
        check("not proven: %s" % why, P(**dict(base, **{k: v}))[0] is False)
finally:
    if OLD_HOME is not None:
        os.environ["HOME"] = OLD_HOME
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
