#!/usr/bin/env python3
"""Regression test for E94 (docs/audits/2026-11-09-codebase-audit.md).

BEFORE: the advisor's static facts (chip brand, GPU cores, machine name, ...)
were detected once and cached for the whole process, keyed only on
kern.boottime + iogpu.wired_limit_mb. If ONE command timed out on the first
`_adv_hw()` (the dashboard starts at login, when the Mac is busiest), the
blank answer stuck until a dashboard restart: chip "unknown" -> the slowest
M1 8c row, so a 64 GB M5 Max was advised to run only the 2B. "Re-check"
(`?bust=1`) re-read only the key, which had not changed. Only a FAILED Metal
probe was retried.

AFTER: a missing static piece is treated like the failed Metal probe — it is
re-detected on bust and otherwise at most every _ADV_PROBE_RETRY_S (10 min),
only the missing pieces, and a good answer is final.

Runs the REAL dashboard/aux_advisor_hw.py exec'd into a fresh namespace with a
stub `subprocess` whose first brand-string sysctl, ioreg and SPHardware call
time out, and a fake clock.
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
AUX = os.path.join(DASH, "aux_advisor_hw.py")
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-600:]))


with io.open(AUX, encoding="utf-8") as _f:
    SRC = _f.read()

MAC = {
    "machdep.cpu.brand_string": "Apple M5 Max", "hw.optional.arm64": "1",
    "sysctl.proc_translated": "0", "hw.model": "Mac17,6", "hw.memsize": str(64 * 2 ** 30),
    "hw.nperflevels": "2", "iogpu.wired_limit_mb": "0",
    "kern.boottime": "{ sec = 1789568983, usec = 731292 } Wed Sep 16 10:29:43 2026",
    "kern.memorystatus_level": "71", "vm.swapusage": "total = 0.00M  used = 0.00M  free = 0.00M",
    "perflevels": "hw.perflevel0.physicalcpu: 6\nhw.perflevel0.logicalcpu: 6\nhw.perflevel0.name: Super\n",
    "ioreg": '    {\n      "gpu-core-count" = 40\n    }\n',
    "sp_hardware": json.dumps({"SPHardwareDataType": [{"machine_name": "MacBook Air",
                                                       "machine_model": "Mac17,6"}]}),
    "sp_displays": json.dumps({"SPDisplaysDataType": [{"sppci_cores": "40"}]}),
    "sw_vers": "26.5.2", "batt": "Now drawing from 'AC Power'", "pmset": "", "therm": "",
    "metal": json.dumps({"ok": True, "recommendedMaxWorkingSetSize": 55662788608}),
}


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


class TimeoutExpired(Exception):
    pass


class Sub:
    TimeoutExpired = TimeoutExpired

    def __init__(self, flaky=(), always=()):
        self.flaky = set(flaky)        # time out on the FIRST call only
        self.always = set(always)      # time out every time
        self.calls = []

    @staticmethod
    def key(argv):
        a0 = argv[0]
        if len(argv) >= 2 and argv[1] == "-I":
            return "metal"
        if a0 == "/usr/sbin/sysctl":
            return argv[2] if len(argv) >= 3 and argv[1] == "-n" else "perflevels"
        if a0 == "/usr/sbin/ioreg":
            return "ioreg"
        if a0 == "/usr/sbin/system_profiler":
            return "sp_hardware" if "SPHardwareDataType" in argv else "sp_displays"
        if a0 == "/usr/bin/sw_vers":
            return "sw_vers"
        if a0 == "/usr/bin/pmset":
            return {"batt": "batt", "therm": "therm"}.get(argv[-1], "pmset")
        return "?"

    def run(self, argv, **kw):
        argv = list(argv)
        k = self.key(argv)
        self.calls.append(k)
        if k in self.always:
            raise TimeoutExpired(k)
        if k in self.flaky:
            self.flaky.discard(k)
            raise TimeoutExpired(k)
        if k not in MAC:
            return _R("", 1)
        return _R(MAC[k], 0)


class FakeTime:
    def __init__(self):
        self.t = 1_800_000_000.0

    def time(self):
        return self.t


def load(sub):
    g = {"__name__": "aux_advisor_hw_e94", "HOME": "/nonexistent-home", "os": os, "sys": sys}
    server_real.server_functions(["_mem_ceilings"], g)
    g["subprocess"] = sub
    g["_machine_ram_gb"] = lambda: 64.0
    exec(compile(SRC, AUX, "exec"), g)
    ft = FakeTime()
    g["_advhw_time"] = ft
    return g, ft


STATIC = {"machdep.cpu.brand_string", "ioreg", "sp_displays", "sp_hardware", "hw.model",
          "sw_vers", "perflevels", "hw.nperflevels"}

print("== a first-call timeout on the brand, ioreg and SPHardware ==")
sub = Sub(flaky={"machdep.cpu.brand_string", "ioreg", "sp_displays", "sp_hardware"})
g, ft = load(sub)
hw = g["_adv_hw"]()
check("the blip: chip unknown -> the slowest row, machine name blank (reproduces the finding)",
      hw["chip"]["row"] == "M1 8c" and hw["machine_name"] == "", (hw["chip"]["row"], hw["machine_name"]))
n = len(sub.calls)
hw = g["_adv_hw"]()
check("a second call inside the window runs nothing static", not (set(sub.calls[n:]) & STATIC),
      sub.calls[n:])
n = len(sub.calls)
hw = g["_adv_hw"](bust=True)
check("Re-check (bust) re-detects the missing pieces: M5 Max 40c (was: still M1 8c)",
      hw["chip"]["row"] == "M5 Max 40c" and hw["chip"]["cores_source"] == "ioreg", hw["chip"])
check("...and the machine name (MacBook Air, so fanless again)",
      hw["machine_name"] == "MacBook Air" and hw["fanless"] is True, (hw["machine_name"], hw["fanless"]))
new = sub.calls[n:]
check("...re-running only what was missing (not RAM, not sw_vers, not the Metal probe)",
      "machdep.cpu.brand_string" in new and "ioreg" in new and "sp_hardware" in new
      and "sw_vers" not in new and "metal" not in new and "hw.model" not in new, new)
n = len(sub.calls)
ft.t += 3600
g["_adv_hw"](bust=True)
check("a good answer is final: an hour later + bust re-reads no static fact",
      not (set(sub.calls[n:]) & STATIC), sub.calls[n:])

print("== without bust: retried at most every 10 minutes ==")
sub = Sub(flaky={"machdep.cpu.brand_string"})
g, ft = load(sub)
hw = g["_adv_hw"]()
check("brand lost on the first call", hw["chip"]["row"] == "M1 8c", hw["chip"]["row"])
n = len(sub.calls)
ft.t += 120
hw = g["_adv_hw"]()
check("2 minutes later (key re-read): not retried yet", "machdep.cpu.brand_string" not in sub.calls[n:]
      and hw["chip"]["row"] == "M1 8c", sub.calls[n:])
ft.t += g["_ADV_PROBE_RETRY_S"]
hw = g["_adv_hw"]()
check("after the retry interval: retried and fixed with no restart",
      hw["chip"]["row"] == "M5 Max 40c", hw["chip"]["row"])

print("== a fact that keeps failing is retried, never cached as final ==")
sub = Sub(always={"sp_hardware"})
g, ft = load(sub)
g["_adv_hw"]()
n = len(sub.calls)
g["_adv_hw"](bust=True)
check("bust retries the still-missing SPHardware", sub.calls[n:].count("sp_hardware") == 1, sub.calls[n:])
n = len(sub.calls)
g["_adv_hw"]()
check("...but a plain call inside 10 min does not hammer it", "sp_hardware" not in sub.calls[n:],
      sub.calls[n:])
hw = g["_adv_hw"]()
check("...and the rest of the Mac is still right", hw["chip"]["row"] == "M5 Max 40c", hw["chip"]["row"])

print("== an Intel Mac has no GPU core count to retry ==")
MAC_INTEL = dict(MAC)
MAC["machdep.cpu.brand_string"] = "Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz"
MAC["hw.optional.arm64"] = "0"
del MAC["ioreg"], MAC["sp_displays"]
sub = Sub()
g, ft = load(sub)
g["_adv_hw"]()
n = len(sub.calls)
g["_adv_hw"](bust=True)
check("bust on an Intel Mac does not re-run ioreg / SPDisplays every time",
      "ioreg" not in sub.calls[n:] and "sp_displays" not in sub.calls[n:], sub.calls[n:])
MAC.clear()
MAC.update(MAC_INTEL)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
