#!/usr/bin/env python3
"""Regression test for R2-44 (round 2, w9; follow-up to F01 / B03).

BEFORE: tools/bench/decode_bench.py (spawn_bench_server) and
tools/bench/concurrency_probe.py (spawn) launched their private model server
with `--trust-remote-code` unconditionally, after F01 had removed it from
mlx-server.sh / mlx-server-bg.sh: benchmarking a repo that ships an
`auto_map` ran that repo's own Python.

AFTER: both add the flag only for a roster entry with a literal
`trust_remote_code: true` (a string "true" does not count), the rule the
shell launchers use.

Imports the REAL bench modules and calls the REAL spawn functions with
subprocess.Popen stubbed to a process that "dies during load", so the argv is
captured and nothing is started; tempdirs and logs live in a throwaway HOME.
On the old code the two "no flag" checks fail.
"""
import importlib.util
import os
import sys
import tempfile

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
        print("  FAIL %s %s" % (name, extra))


class _DeadProc:
    pid = 0

    def poll(self):
        return 1


class _Sub:
    def __init__(self, real):
        self.real, self.argv = real, []

    def Popen(self, argv, **kw):
        self.argv.append(list(argv))
        return _DeadProc()

    def __getattr__(self, name):
        return getattr(self.real, name)


class _Tmp:
    def __init__(self, real, root):
        self.real, self.root = real, root

    def mkdtemp(self, prefix="tmp"):
        return self.real.mkdtemp(prefix=prefix, dir=self.root)

    def __getattr__(self, name):
        return getattr(self.real, name)


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def argv_for(mod, fn_name, entry, root):
    sub = _Sub(mod.subprocess)
    mod.subprocess = sub
    mod.tempfile = _Tmp(mod.tempfile, root)
    try:
        getattr(mod, fn_name)("org/model", entry, "none", 18999, sys.executable,
                              os.path.join(root, "bench.log"), timeout=1)
    except RuntimeError:
        pass                                   # "died during load" — expected
    finally:
        mod.subprocess = sub.real
        mod.tempfile = mod.tempfile.real
    return sub.argv[-1] if sub.argv else []


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-44-") as home:
        os.environ["HOME"] = home
        for rel, fn in (("tools/bench/decode_bench.py", "spawn_bench_server"),
                        ("tools/bench/concurrency_probe.py", "spawn")):
            tag = os.path.basename(rel)
            mod = load(rel, "hermes_bench_" + fn)
            a = argv_for(mod, fn, {}, home)
            check("%s: the server is launched (argv captured)" % tag,
                  "--model" in a and "org/model" in a, a)
            check("%s: no roster flag -> NO --trust-remote-code (was always on)" % tag,
                  a and "--trust-remote-code" not in a, a)
            a = argv_for(mod, fn, {"trust_remote_code": "true"}, home)
            check("%s: a string \"true\" does not count" % tag,
                  a and "--trust-remote-code" not in a, a)
            a = argv_for(mod, fn, {"trust_remote_code": True}, home)
            check("%s: roster trust_remote_code: true -> the flag is passed" % tag,
                  "--trust-remote-code" in a, a)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
