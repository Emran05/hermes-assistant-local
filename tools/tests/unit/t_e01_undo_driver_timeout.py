#!/usr/bin/env python3
"""E01 review (2026-11-10): a checkpoint driver that times out is a timeout.

`_ckpt()` reported a timeout as str(TimeoutExpired), which quotes the whole
command line — the driver's own source. Two consequences:
  * the undo handler classifies a failed restore by words in the error text,
    and since E01 the driver's source holds "snapshot not found": every
    restore that merely TIMED OUT was read as "snapshot pruned", and the row
    was marked not undoable for good (the Undo button never came back);
  * the refusal the owner saw carried ~5 KB of Python.
Now a timeout says "timed out", nothing is marked, and the file is untouched.

Drives the REAL dashboard/aux_recorder.py with its real checkpoint driver and
the vendored checkpoint manager (fixtures/e01_checkpoint_manager.py) against
real git in a scratch HOME; only the one driver call under test is made to
time out.
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
import types

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
        print("  FAIL %s  <- %s" % (name, str(extra)[:300]))


class Ctx:
    def __init__(self, body=None):
        self.body = body or {}
        self.query = {}

    def q1(self, k, d=""):
        return self.query.get(k, d)


def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


OLD_HOME = os.environ.get("HOME")
HOME = os.path.realpath(tempfile.mkdtemp(prefix="hermes-e01t-"))
os.environ["HOME"] = HOME
try:
    tools = os.path.join(HOME, ".hermes", "hermes-agent", "tools")
    os.makedirs(tools)
    open(os.path.join(tools, "__init__.py"), "w").close()
    shutil.copy(FIXTURE, os.path.join(tools, "checkpoint_manager.py"))
    sys.path.insert(0, os.path.join(HOME, ".hermes", "hermes-agent"))
    from tools.checkpoint_manager import CheckpointManager      # the fixture
    data = os.path.join(HOME, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    G = {"__name__": "aux_recorder_e01t", "HOME": HOME,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(HOME, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), G)

    # the driver call named in SLOW times out exactly as subprocess.run does;
    # every other call is the real subprocess
    SLOW = set()

    def run(cmd, *a, **kw):
        try:
            op = json.loads(kw.get("input") or "{}").get("op")
        except (TypeError, ValueError):
            op = None
        if op in SLOW:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 60)
        return subprocess.run(cmd, *a, **kw)

    G["subprocess"] = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)

    proj = os.path.join(HOME, "proj")
    os.makedirs(proj)
    for name, body in (("package.json", b"{}\n"), ("app.py", b"owner\n"), ("lib.py", b"owner lib\n")):
        with open(os.path.join(proj, name), "wb") as f:
            f.write(body)
    time.sleep(1.1)
    check("snapshot taken", CheckpointManager(enabled=True)._take(proj, "before patch") is True)
    time.sleep(1.1)
    app = os.path.join(proj, "app.py")
    with open(app, "wb") as f:
        f.write(b"agent\n")
    new = os.path.join(proj, "new.txt")
    with open(new, "wb") as f:
        f.write(b"agent made\n")
    os.chmod(new, 0o600)

    def sql(q, args=()):
        con = sqlite3.connect(G["REC_DB"])
        try:
            out = con.execute(q, args).fetchall()
            con.commit()
            return out
        finally:
            con.close()

    def record(tc, tool, target, kind="write"):
        sql("INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
            " reversible, status, origin) VALUES(?,?,?,?,?,?,?, 'no', 'done', 'ws')",
            (tc, time.time(), "s", "hub", tool, target, kind))
        G["_finalize_ws"](tc, target, time.time(), kind)
        return sql("SELECT id FROM actions WHERE tool_call_id=?", (tc,))[0][0]

    eid = record("e1", "patch", app)
    nid = record("n1", "write_file", new)
    shid = record("s1", "terminal", "truncate -s 0 " + proj + "/lib.py", "shell")

    def state(aid):
        return sql("SELECT status, reversible, undo_note FROM actions WHERE id=?", (aid,))[0]

    check("(setup) all three rows are undoable", state(eid)[1] == "yes" and state(nid)[1] == "yes"
          and state(shid)[1] == "partial", (state(eid), state(nid), state(shid)))

    def undo(aid):
        res = G["recorder_undo_handler"](Ctx({"id": aid, "force": False}))
        return res[0] if isinstance(res, tuple) else res

    def text(res):
        return "%s %s" % (res.get("error"), res.get("detail") or "")

    print("\n== a file restore that times out ==")
    SLOW.add("restore")
    res = undo(eid)
    check("refused, the file is untouched", res.get("ok") is False and read(app) == b"agent\n", res)
    check("the answer says it timed out, in one line (not the driver's source)",
          "timed out" in text(res) and len(text(res)) < 300 and "import json" not in text(res),
          text(res))
    check("the row is NOT marked 'snapshot pruned': it is still undoable",
          state(eid) == ("done", "yes", ""), state(eid))

    print("\n== a whole-folder (shell) restore that times out ==")
    res = undo(shid)
    check("refused, says timed out", res.get("ok") is False and "timed out" in text(res)
          and len(text(res)) < 300, text(res))
    check("the shell row is still undoable too", state(shid)[:2] == ("done", "partial")
          and "pruned" not in (state(shid)[2] or ""), state(shid))
    SLOW.discard("restore")

    print("\n== a probe that times out on a created file ==")
    SLOW.add("probe")
    res = undo(nid)
    check("refused, the created file stays where it is", res.get("ok") is False
          and read(new) == b"agent made\n" and not os.listdir(G["UNDO_TRASH"]), res)
    check("...with a short reason", "timed out" in text(res) and len(text(res)) < 400
          and "import json" not in text(res), text(res))
    check("...and the row keeps its Undo for when the store answers", state(nid)[:2] == ("done", "yes"),
          state(nid))
    SLOW.discard("probe")

    print("\n== once the driver answers again, the same rows undo ==")
    res = undo(eid)
    check("the edit is restored", res.get("ok") is True and read(app) == b"owner\n", res)
    res = undo(nid)
    check("the created file goes to the undo trash", res.get("ok") is True
          and not os.path.exists(new), res)
finally:
    if OLD_HOME is not None:
        os.environ["HOME"] = OLD_HOME
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
