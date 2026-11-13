#!/usr/bin/env python3
"""E116 (audit 2026-11-09): config exports and pre-restore backups are 0600
from birth.

Before: _cfg_dump (state-snapshot.json) and _cfg_backup
(snapshot-backups/pre-restore-*.json) used a plain open(tmp, "wb"), so under
the usual 022 umask both were created -rw-r--r-- — readable by every account
on the Mac — although they hold the whole permission policy, the roster and
widget config (C110: every ~/.hermes store is <= 0600).

Execs the REAL dashboard/aux_config.py against a throwaway HOME with the
umask forced to 022 and checks the modes of what Export and a restore write,
including an existing 0644 file being replaced and older backups kept by GC.

Run:  python3 t_e116_config_export_modes.py
"""
import os
import shutil
import stat
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %r" % (name, extra))


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


HOME = tempfile.mkdtemp(prefix="hermes-e116-")
os.environ["HOME"] = HOME
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA, exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "dashboard"))
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real                          # noqa: E402

old_umask = os.umask(0o022)
try:
    lock = threading.Lock()
    g = {"__name__": "aux_config_e116", "HOME": HOME, "DATA": DATA,
         "HERE": os.path.join(REPO, "dashboard"),
         "HERMES": os.path.join(HOME, "bin", "hermes"), "_hermes_env": lambda: {},
         "get_layout": lambda: {"order": []}, "save_layout": lambda o: None,
         "SETTINGS_FILE": os.path.join(DATA, "settings.json"),
         "LAYOUT_FILE": os.path.join(DATA, "layout.json"),
         "MODELS_FILE": os.path.join(DATA, "models.json"), "WIDGETS": {},
         "_model_registry": lambda: [], "active_model": lambda: "",
         "switch_model": lambda m: {"ok": False}, "_model_downloaded": lambda m: False,
         "_widget_cache": {},
         "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    g.update(server_real.settings_api(g["SETTINGS_FILE"], lock))
    exec(compile(open(os.path.join(REPO, "dashboard", "aux_config.py"),
                      encoding="utf-8").read(), "aux_config.py", "exec"), g)

    r = g["snapshot_export"]("")
    snap = g["CFG_SNAPSHOT_PATH"]
    check("export ok", isinstance(r, dict) and r.get("ok") is True, r)
    check("THE FIX: state-snapshot.json is created 0600", mode(snap) == 0o600, oct(mode(snap)))

    # an export written before the fix (0644) is tightened by the next export
    os.chmod(snap, 0o644)
    g["snapshot_export"]("again")
    check("re-export over an old 0644 file leaves it 0600", mode(snap) == 0o600, oct(mode(snap)))
    check("no tmp file is left behind", not os.path.exists(snap + ".tmp"))

    # a stale tmp left by a crash with a wide mode does not widen the result
    with open(snap + ".tmp", "w") as f:
        f.write("junk")
    os.chmod(snap + ".tmp", 0o666)
    g["snapshot_export"]("third")
    check("a stale wide tmp file does not widen the export", mode(snap) == 0o600, oct(mode(snap)))

    bk = g["CFG_BACKUPS"]
    old = os.path.join(bk, "pre-restore-20200101-000000.json")
    with open(old, "w") as f:
        f.write("{}")
    os.chmod(old, 0o644)
    name = g["_cfg_backup"]()
    p = os.path.join(DATA, name)
    check("THE FIX: a pre-restore backup is created 0600", mode(p) == 0o600, oct(mode(p)))
    check("an older 0644 backup kept by GC is tightened to 0600", mode(old) == 0o600, oct(mode(old)))
    check("the backups folder is owner-only", mode(bk) & 0o077 == 0, oct(mode(bk)))
finally:
    os.umask(old_umask)
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
