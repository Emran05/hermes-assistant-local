#!/usr/bin/env python3
"""Round-2 Config-as-Code follow-ups (docs/audits/2026-10-21-codebase-audit.md).

  C25  a restore whose pre-restore backup could not be written is REFUSED
       (500, "could not write the pre-restore backup: ...") unless the body
       says force:true.  _cfg_backup used to swallow the failure and return
       None, and the apply answered ok:true right after the UI had promised
       "a pre-restore backup will be kept".  The UI says why and asks before
       retrying with force.
  C59.5 apply_active_model must be a JSON boolean: bool("false") is True, so
       a string "false" used to SWITCH the running model (a model-server
       restart) on import.

Execs the REAL dashboard/aux_config.py into a namespace holding stubs for the
server.py globals it uses (HOME/DATA under a temp dir, layout/settings in
memory, no hermes binary, no permissions engine).  Drives the real
snapshot_post() route function.  Offline; nothing under the real ~/.hermes.
"""
import io, json, os, re, shutil, subprocess, sys, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
CFG = os.path.join(REPO, "dashboard", "aux_config.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-r2-config-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
WIDGETS = {"weather": {}, "markets": {}, "notes": {}}
STATE = {"layout": {"order": ["weather", "markets"]}, "settings": {"tickers": ["AAPL"]},
         "saved_layouts": [], "switched": []}


def load():
    ns = {"__name__": "aux_config_t", "HOME": HOME, "HERE": os.path.join(REPO, "dashboard"),
          "DATA": DATA, "WIDGETS": WIDGETS, "_state_lock": threading.RLock(), "_widget_cache": {},
          "SETTINGS_FILE": os.path.join(DATA, "settings.json"),
          "LAYOUT_FILE": os.path.join(DATA, "layout.json"),
          "MODELS_FILE": os.path.join(DATA, "models.json"),
          "HERMES": os.path.join(TMP, "no-hermes"), "_hermes_env": lambda: dict(os.environ),
          "read_json": lambda p, d=None: (json.load(open(p)) if os.path.exists(p) else d),
          "write_json": lambda p, o: None,
          "get_layout": lambda: dict(STATE["layout"]),
          "save_layout": lambda lay: (STATE["saved_layouts"].append(lay), STATE["layout"].update(lay)),
          "get_settings": lambda: dict(STATE["settings"]),
          "settings_update": lambda fn: fn(STATE["settings"]),
          "_model_registry": lambda: [{"id": "org/m-4bit", "label": "M", "ram": 5}],
          "active_model": lambda: "org/m-4bit",
          "switch_model": lambda m: (STATE["switched"].append(m), {"ok": True})[1],
          "_model_downloaded": lambda m: True,
          "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    sys.modules["permissions"] = None             # the engine is not under test
    src = io.open(CFG, encoding="utf-8").read()
    exec(compile(src, CFG, "exec"), ns)
    return ns


def restore_body(ns, **kw):
    snap = ns["snapshot_build"]()
    snap["exported_at"] = "2026-10-26T12:00:00-04:00"
    snap["dashboard"]["layout"] = {"order": ["notes", "weather"]}
    body = {"dry_run": False, "sections": ["layout"], "snapshot": snap}
    body.update(kw)
    return body


def status_of(res):
    return res[1] if isinstance(res, tuple) else 200


def body_of(res):
    return res[0] if isinstance(res, tuple) else res


def backups():
    d = os.path.join(DATA, "snapshot-backups")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def t_backup_failure(ns):
    print("\nC25. no pre-restore backup, no restore (unless forced)")
    post = ns["snapshot_post"]
    res = post("/api/config/import", restore_body(ns))
    check("premise: a restore with a writable backup dir answers ok", body_of(res).get("ok") is True, res)
    check("premise: and reports the backup it wrote",
          str(body_of(res).get("backup") or "").startswith("snapshot-backups/pre-restore-"), res)
    check("premise: the backup file exists", len(backups()) == 1, backups())

    # make the backup directory unwritable in a deterministic way: a FILE
    # where the directory should be (os.makedirs then raises)
    STATE["layout"] = {"order": ["weather", "markets"]}
    STATE["saved_layouts"][:] = []
    bdir = os.path.join(DATA, "snapshot-backups")
    shutil.rmtree(bdir)
    open(bdir, "w").close()
    err = io.StringIO()
    old_err, sys.stderr = sys.stderr, err
    try:
        res = post("/api/config/import", restore_body(ns))
    finally:
        sys.stderr = old_err
    b = body_of(res)
    check("backup cannot be written -> refused with 500", status_of(res) == 500 and b.get("ok") is False, res)
    check("the error says the pre-restore backup could not be written",
          str(b.get("error", "")).startswith("could not write the pre-restore backup: "), b)
    check("and is flagged backup_failed for the UI", b.get("backup_failed") is True, b)
    check("the reason carries no absolute path", HOME not in json.dumps(b) and "/Users/" not in json.dumps(b), b)
    check("nothing was restored", STATE["saved_layouts"] == [] and
          STATE["layout"]["order"] == ["weather", "markets"], STATE)
    check("the failure is logged to stderr", "pre-restore backup failed" in err.getvalue(), err.getvalue())

    res = post("/api/config/import", restore_body(ns, force=True))
    b = body_of(res)
    check("force:true restores anyway", status_of(res) == 200 and b.get("ok") is True
          and STATE["layout"]["order"] == ["notes", "weather"], (res, STATE))
    check("and says there is no backup", b.get("backup") is None and
          any("WITHOUT a pre-restore backup" in w for w in b.get("warnings", [])), b)
    for v in ("true", 1, None, "yes"):
        res = post("/api/config/import", restore_body(ns, force=v))
        check("force=%r (not a JSON boolean) -> 400" % (v,), status_of(res) == 400, res)
    res = post("/api/config/import", {"dry_run": True, "snapshot": restore_body(ns)["snapshot"]})
    check("a dry run never needs the backup", body_of(res).get("ok") is True
          and body_of(res).get("dry_run") is True, res)
    os.remove(bdir)


def t_apply_active_strict(ns):
    print("\nC59.5. apply_active_model is a JSON boolean, never a truthy string")
    post = ns["snapshot_post"]

    def body(v):
        snap = ns["snapshot_build"]()
        snap["exported_at"] = "2026-10-26T12:00:00-04:00"
        snap["dashboard"]["models"]["active"] = "org/other-4bit"
        snap["dashboard"]["models"]["roster"].append({"id": "org/other-4bit", "label": "O", "ram": 5})
        b = {"dry_run": False, "sections": ["models"], "snapshot": snap}
        if v is not None:
            b["apply_active_model"] = v
        return b
    for v in ("false", "true", 0, 1, []):
        STATE["switched"][:] = []
        res = post("/api/config/import", body(v))
        check("apply_active_model=%r -> 400" % (v,), status_of(res) == 400
              and "apply_active_model" in str(body_of(res).get("error")), res)
        check("  and the running model is not switched", STATE["switched"] == [], STATE["switched"])
    STATE["switched"][:] = []
    res = post("/api/config/import", body(None))
    check("absent -> the roster is restored, the active model left alone",
          body_of(res).get("ok") is True and STATE["switched"] == []
          and body_of(res)["applied"].get("active_model") == "skipped", res)
    res = post("/api/config/import", body(False))
    check("false -> not switched", body_of(res).get("ok") is True and STATE["switched"] == [], res)
    res = post("/api/config/import", body(True))
    check("true -> switched", body_of(res).get("ok") is True
          and STATE["switched"] == ["org/other-4bit"], (res, STATE["switched"]))


def t_ui():
    print("\n   the UI says why and asks before forcing")
    js = io.open(os.path.join(REPO, "dashboard", "aux_config.js"), encoding="utf-8").read()
    check("aux_config.js reacts to backup_failed", "backup_failed === true" in js)
    check("and retries with force only after a confirm()",
          re.search(r"confirm\([^;]*Restore anyway[^;]*\)\)\s*\{\s*body\.force = true", js, re.S) is not None)
    r = subprocess.run(["node", "--check", os.path.join(REPO, "dashboard", "aux_config.js")],
                       capture_output=True, text=True)
    check("aux_config.js parses", r.returncode == 0, r.stderr[-300:])


def main():
    ns = load()
    t_backup_failure(ns)
    t_apply_active_strict(ns)
    t_ui()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
