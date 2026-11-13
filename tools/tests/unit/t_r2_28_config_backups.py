#!/usr/bin/env python3
"""R2-28 — config.yaml backups are unique per change and pruned to the newest N.

Every compression (aux_context) or prompt-budget (aux_promptbudget) change
wrote config.yaml.bak-<kind>-<YYYYmmdd-HHMMSS> and nothing ever pruned them,
so ~/.hermes collected one copy of the whole config per click forever; and two
changes inside the same second wrote the SAME name, so the second backup
overwrote the first one's copy of the pre-change file.  Now the name is
reserved with O_EXCL (<ts>-2, -3, ... within a second) and only the newest 10
of each kind are kept; other files next to the config are never touched.

Execs the REAL aux_context.py and aux_promptbudget.py the way server.py does,
each into its own throwaway HOME.  No model, no subprocess, no network.

Fails on the old code: N changes in one second leave ONE backup (holding the
state after change N-1, not the original), and 15 changes leave 15 backups.
"""
import glob
import io
import re
import os
import shutil
import stat
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s  — %r" % (name, detail))


CX_CFG = ("model:\n  default: x\n  context_length: 65536\n"
          "compression:\n  enabled: true\n  threshold: 0.5\n  target_ratio: 0.2\n"
          "  protect_last_n: 20\n")
PB_CFG = ("model:\n  default: x\n  context_length: 65536\n"
          "platform_toolsets:\n  cli:\n    - hermes-cli\n  telegram:\n    - web\n")


def load(mod, home, cfg):
    os.makedirs(os.path.join(home, ".hermes", "dashboard"), exist_ok=True)
    os.makedirs(os.path.join(home, ".hermes", "logs"), exist_ok=True)
    p = os.path.join(home, ".hermes", "config.yaml")
    io.open(p, "w", encoding="utf-8").write(cfg)
    os.chmod(p, 0o644)
    aux = os.path.join(REPO, "dashboard", mod)
    g = {"__name__": mod[:-3] + "_r2_28", "HOME": home,
         "DATA": os.path.join(home, ".hermes", "dashboard"),
         "HERMES": os.path.join(home, "bin", "hermes"),
         "_hermes_env": lambda: dict(os.environ),
         "_state_lock": threading.Lock(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    exec(compile(io.open(aux, encoding="utf-8").read(), aux, "exec"), g)
    return g, p


def frozen_clock(g, prefix, stamp="20261026-120000"):
    """Make the module's datetime.now() a fixed second, so 'same second' is
    deterministic instead of a race against the wall clock."""
    real = g[prefix + "_datetime"]

    class _DT(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime.strptime(stamp, "%Y%m%d-%H%M%S")

    class _Mod:
        datetime = _DT

        def __getattr__(self, k):
            return getattr(real, k)
    g[prefix + "_datetime"] = _Mod()
    return lambda: g.__setitem__(prefix + "_datetime", real)


tmp = tempfile.mkdtemp(prefix="hermes-r2-28-")
try:
    for mod, prefix, kind, cfg, change in (
            ("aux_context.py", "_cx", "context", CX_CFG,
             lambda g, i: g["_cx_write"]({"threshold": round(0.30 + i / 100.0, 2)})),
            ("aux_promptbudget.py", "_pb", "promptbudget", PB_CFG,
             lambda g, i: g["_pb_write_toolsets"]("cli", ["hermes-cli", "t%d" % i]))):
        print("\n=== %s ===" % mod)
        g, cfgp = load(mod, os.path.join(tmp, kind), cfg)
        d = os.path.dirname(cfgp)
        shape = re.compile(r"\.bak-%s-\d{8}-\d{6}(-\d+)?$" % kind)

        def backups():
            return sorted(f for f in glob.glob(cfgp + ".bak-%s-*" % kind)
                          if shape.search(f))
        original = io.open(cfgp, encoding="utf-8").read()
        # unrelated neighbours that pruning must never touch
        for fn in ("config.yaml.bak-toolbudget-20200101-000000", "config.yaml.lock",
                   "config.yaml.bak-%s-notes.txt" % kind):
            io.open(os.path.join(d, fn), "w").write("keep me")

        restore = frozen_clock(g, prefix)
        baks = []
        for i in range(3):
            changed, bak = change(g, i)
            check("change %d in the same second wrote a backup" % (i + 1),
                  changed is True and bak and os.path.exists(bak), (changed, bak))
            baks.append(bak)
        restore()
        check("three changes in one second -> three distinct backups",
              len(set(baks)) == 3 and len(backups()) == 3, (baks, backups()))
        check("the FIRST backup still holds the original config",
              io.open(baks[0], encoding="utf-8").read() == original)
        check("the names are <ts>, <ts>-2, <ts>-3",
              [b.rsplit("-%s-" % kind, 1)[1] for b in baks]
              == ["20261026-120000", "20261026-120000-2", "20261026-120000-3"], baks)
        check("backups are 0600", all(stat.S_IMODE(os.stat(b).st_mode) == 0o600 for b in baks),
              [oct(stat.S_IMODE(os.stat(b).st_mode)) for b in baks])

        # older backups from earlier days, named the way the old code did
        for day in range(1, 13):
            fn = "%s.bak-%s-202609%02d-080000" % (cfgp, kind, day)
            io.open(fn, "w").write("old %d" % day)
        for i in range(3, 6):
            change(g, i)
        left = backups()
        keep = g.get(prefix.upper() + "_BACKUP_KEEP", 10)
        check("pruned to the newest %d of this kind" % keep, len(left) == keep, len(left))
        # 12 old + 6 new = 18 -> the 8 oldest (Sept 1-8) go, Sept 9-12 stay
        gone = ["-202609%02d-" % dd for dd in range(1, 9)]
        kept = ["-202609%02d-" % dd for dd in range(9, 13)]
        check("the oldest days were the ones removed",
              not any(x in f for f in left for x in gone)
              and all(any(x in f for f in left) for x in kept),
              [os.path.basename(f) for f in left])
        check("every same-second backup of today survived",
              all(b in left for b in baks), [os.path.basename(f) for f in left])
        for fn in ("config.yaml.bak-toolbudget-20200101-000000", "config.yaml.lock",
                   "config.yaml.bak-%s-notes.txt" % kind):
            check("left alone: " + fn, os.path.exists(os.path.join(d, fn)))
        check("a no-op change writes no backup",
              change(g, 5) == (False, None) and len(backups()) == keep)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
