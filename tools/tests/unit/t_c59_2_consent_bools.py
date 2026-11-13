#!/usr/bin/env python3
"""C59.2 (round 2 of the 2026-10-21 audit): consent fields are JSON booleans.

Three routes read a consent decision with bool(), and bool("false") is True:
  * POST /api/shortcuts/run {ticket, approved: "false"} APPROVED the run;
  * POST /api/shortcuts/config {id, exposed: "false"} EXPOSED the shortcut
    (and a body with no `exposed` at all unexposed it);
  * POST /api/undo {id, force: "false"} overrode the recorder's conflict
    check and restored over the owner's newer edit.
Now only true/false counts; anything else is a 400 that changes nothing (a
redemption that is refused does not consume the ticket).  `force` may be
omitted (it means no); `approved` and `exposed` may not.

Shortcuts half: the REAL handlers lifted out of aux_shortcuts.py by AST over
stubs (no `shortcuts` binary, nothing spawned).  Recorder half: the REAL
aux_recorder.py exec'd the way server.py does, on a throwaway HOME, with the
checkpoint driver swapped for a recorder of restore calls.
Fails on the old code: "false" approves, exposes and forces.

Run:  python3 t_c59_2_consent_bools.py
"""
import ast
import io
import json
import os
import secrets
import shutil
import sqlite3
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
DASH = os.path.join(REPO, "dashboard")
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
    def __init__(self, body=None):
        self.query = {}
        self.body = body if body is not None else {}

    def q1(self, k, d=""):
        return d


def status(r):
    return r[1] if isinstance(r, tuple) else 200


def payload(r):
    return r[0] if isinstance(r, tuple) else r


BAD = [("the string \"false\"", "false"), ("the string \"true\"", "true"),
       ("1", 1), ("0", 0), ("null", None), ("an empty string", "")]

# ---------------------------------------------------------------------------
print("\n== Shortcuts: approved / exposed ==")
SRC = os.path.join(DASH, "aux_shortcuts.py")
WANT = {"shortcuts_run_handler", "_sb_redeem", "_sb_finish_run", "shortcuts_config_handler"}
tree = ast.parse(open(SRC, encoding="utf-8").read(), filename=SRC)
fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANT]
check("aux_shortcuts.py defines the four handlers", {n.name for n in fns} == WANT,
      sorted(n.name for n in fns))

ran, records, cfg = [], [], {"exposed": {"U-1": {"name": "Morning", "ts": 1.0}}}
NS = {
    "time": time, "secrets": secrets, "SB_CLASS": "shortcuts-run",
    "SB_TICKET_TTL": 300, "SB_IN_CAP": 16384, "SB_REC_CAP": 2000,
    "_SB_LOCK": threading.Lock(), "_SB_TICKETS": {},
    "_sb_sweep_tickets": lambda now: None,
    "_sb_installed": lambda force=False: ([{"id": "U-1", "name": "Morning"},
                                           {"id": "U-2", "name": "Evening"}], ""),
    "_sb_resolve": lambda b: ({"id": "U-1", "name": "Morning"}, None),
    "_sb_load_config": lambda: json.loads(json.dumps(cfg)),
    "_sb_save_config": lambda c: cfg.update(json.loads(json.dumps(c))),
    "_sb_perm": types.SimpleNamespace(
        decide=lambda p: {"tier": "ask", "class": "shortcuts-run", "reason": "ask"}),
    "_sb_audit": lambda *a, **k: None,
    "_sb_record": lambda *a, **k: records.append(a),
    "_sb_metric": lambda **k: None,
    "recorder_record_local": lambda *a, **k: {"ok": True},
    "_sb_execute": lambda sid, inp: (ran.append(sid) or
                                     {"outcome": "done", "name": "Morning", "rc": 0,
                                      "output": "", "error": "", "duration_s": 0.1}),
}
exec(compile(ast.Module(body=fns, type_ignores=[]), SRC, "exec"), NS)
RUN, CONF = NS["shortcuts_run_handler"], NS["shortcuts_config_handler"]


def ticket():
    r = payload(RUN(Ctx({"name": "Morning", "source": "agent"})))
    return r.get("ticket")


for label, v in BAD:
    t = ticket()
    r = RUN(Ctx({"ticket": t, "approved": v, "source": "agent"}))
    check("redeem with approved=%s is a 400 and runs nothing" % label,
          status(r) == 400 and payload(r).get("ok") is False and ran == [], (r, ran))
    check("…and leaves the ticket pending",
          NS["_SB_TICKETS"].get(t, {}).get("state") == "pending",
          NS["_SB_TICKETS"].get(t))
t = ticket()
r = RUN(Ctx({"ticket": t, "source": "agent"}))
check("redeem with no `approved` is a 400 (not a silent deny)",
      status(r) == 400 and NS["_SB_TICKETS"][t]["state"] == "pending", r)
r = payload(RUN(Ctx({"ticket": t, "approved": False, "source": "ui"})))
check("approved=false denies and consumes the ticket",
      r.get("status") == "denied" and NS["_SB_TICKETS"][t]["state"] == "denied"
      and ran == [], r)
t = ticket()
r = payload(RUN(Ctx({"ticket": t, "approved": True, "source": "ui"})))
check("approved=true runs it", r.get("ok") is True and ran == ["U-1"], (r, ran))

for label, v in BAD:
    snap = json.dumps(cfg, sort_keys=True)
    r = CONF(Ctx({"id": "U-2", "exposed": v, "source": "ui"}))
    check("config with exposed=%s is a 400 and changes nothing" % label,
          status(r) == 400 and json.dumps(cfg, sort_keys=True) == snap,
          (r, cfg))
snap = json.dumps(cfg, sort_keys=True)
r = CONF(Ctx({"id": "U-1", "source": "ui"}))
check("config with no `exposed` is a 400 (it used to UNexpose)",
      status(r) == 400 and json.dumps(cfg, sort_keys=True) == snap and "U-1" in cfg["exposed"],
      (r, cfg))
r = payload(CONF(Ctx({"id": "U-2", "exposed": True, "source": "ui"})))
check("exposed=true exposes", r.get("ok") is True and "U-2" in cfg["exposed"], (r, cfg))
r = payload(CONF(Ctx({"id": "U-2", "exposed": False, "source": "ui"})))
check("exposed=false unexposes", r.get("ok") is True and "U-2" not in cfg["exposed"], (r, cfg))

# ---------------------------------------------------------------------------
print("\n== Recorder: force ==")
AUX = os.path.join(DASH, "aux_recorder.py")
HOME = tempfile.mkdtemp(prefix="hermes-c592-")
try:
    data = os.path.join(HOME, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    G = {"__name__": "aux_recorder_c592", "HOME": HOME, "HERE": DASH, "DATA": data,
         "STATE_DB": os.path.join(HOME, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), G)

    work = os.path.join(HOME, "proj")
    os.makedirs(work)
    target = os.path.join(work, "notes.txt")
    with open(target, "w") as f:
        f.write("what the agent wrote")
    after = G["_after_state"](target)
    with open(target, "w") as f:
        f.write("what the OWNER wrote afterwards")      # the conflict
    con = sqlite3.connect(G["REC_DB"])
    con.execute(
        "INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
        " reversible, status, snapshot_ref, after_state, origin)"
        " VALUES('tc1', ?, 's', 'hub', 'write_file', ?, 'write', 'yes', 'done', ?, ?,"
        " 'ws')",
        (time.time() - 30, target,
         json.dumps({"workdir": work, "commit": "c0ffee", "short": "c0ffee"}),
         json.dumps(after)))
    con.commit()
    aid = con.execute("SELECT id FROM actions WHERE tool_call_id='tc1'").fetchone()[0]
    con.close()

    restores = []

    def fake_ckpt(op, **kw):
        if op == "restore":
            restores.append(kw)
            return {"success": True, "restored_to": "c0ffee"}
        if op == "list":
            return []
        return {"error": "bad op"}

    G["_ckpt"] = fake_ckpt
    UNDO = G["recorder_undo_handler"]

    def row_status():
        c = sqlite3.connect(G["REC_DB"])
        try:
            return c.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()[0]
        finally:
            c.close()

    for label, v in BAD:
        r = UNDO(Ctx({"id": aid, "force": v}))
        check("undo with force=%s is a 400 and restores nothing" % label,
              status(r) == 400 and payload(r).get("ok") is False and restores == []
              and row_status() == "done", (r, restores))
    r = payload(UNDO(Ctx({"id": aid})))
    check("undo with no `force` reports the conflict (absent means no)",
          r.get("conflict") is True and restores == [], r)
    r = payload(UNDO(Ctx({"id": aid, "force": False})))
    check("force=false reports the conflict", r.get("conflict") is True and restores == [], r)
    r = payload(UNDO(Ctx({"id": aid, "force": True})))
    check("force=true restores over it", r.get("ok") is True and len(restores) == 1
          and row_status() == "undone", (r, restores))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
