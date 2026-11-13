#!/usr/bin/env python3
"""C90 (docs/audits/2026-10-21-codebase-audit.md, partial; 2026-10-08 P3 system
sampler) — the system sampler idles while nobody is looking, and /api/sys
answers from its newest sample.

BEFORE: system_sampler_loop() in expanders_extra.py spawned ps + vm_stat +
netstat every 5 s forever, with or without a window open (about 52,000
processes a day on an idle Mac), and server.py's system_status() — behind
/api/sys, the hub's System tile and the System pop-out — spawned vm_stat and
sysctl again on every call, next to a sample that was at most 5 s old.

AFTER: expanders_extra redefines system_status() to read ram_pct from the
newest SYS_HISTORY sample while it is fresh (same keys, same values), and the
sampler takes samples only while /api/hub (_hub_client_at) or the System
pop-out (expand_system) has served a client within SYS_IDLE_S; going idle
clears the history so no reader takes an hours-old sample for "now".

/api/sys is NOT a watch signal (review): the app's menu-bar item polls it every
15 s for as long as the app runs (app/main.swift fetchMenuInfo), window or no
window, so counting it kept the sampler awake forever. With no fresh sample
system_status reads one vm_stat itself and keeps no history.

The REAL expanders_extra.py runs with subprocess replaced by a recorder that
returns canned ps / vm_stat / sysctl / netstat output, and a fake clock whose
sleep() advances time. server.py's own system_status() is loaded next to it as
the reference for the payload, and is what the old code still serves — so on
the old code every "no spawn" check below fails.
"""
import os
import shutil
import sys
import time as _time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _extra_harness as H  # noqa: E402

check = H.check

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100000.
Pages active:                             900000.
Pages inactive:                           200000.
Pages speculative:                         10000.
Pages wired down:                         700000.
Pages purgeable:                           50000.
"""
NETSTAT = ("Name  Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll\n"
           "en0   1500  <Link#11>   aa:bb:cc:dd:ee:ff  10 0 123456 20 0 654321 0\n")


class Stop(BaseException):
    pass


class FakeTime:
    """time.time()/sleep() on a fake clock; everything else is the real module."""
    def __init__(self):
        self.now = 2000000000.0
        self.ticks = 0

    def __getattr__(self, name):
        return getattr(_time, name)

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.ticks -= 1
        if self.ticks <= 0:
            raise Stop()


class FakeOs:
    """The real os module with a pinned load average (it ticks every 5 s)."""
    def __getattr__(self, name):
        return getattr(os, name)

    def getloadavg(self):
        return (3.2, 2.0, 1.5)


class Proc:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


class FakeSubprocess:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        import subprocess
        return getattr(subprocess, name)

    def run(self, argv, **kw):
        self.calls.append(os.path.basename(argv[0]))
        name = os.path.basename(argv[0])
        if name == "ps":
            return Proc("%CPU\n 12.0\n 30.0\n")
        if name == "vm_stat":
            return Proc(VM_STAT)
        if name == "sysctl":
            return Proc("68719476736\n")
        if name == "netstat":
            return Proc(NETSTAT)
        raise AssertionError("unexpected spawn %r" % (argv,))

    def take(self):
        out, self.calls = self.calls, []
        return out


def run_loop(g, clock, ticks):
    clock.ticks = ticks
    try:
        g["system_sampler_loop"]()
    except Stop:
        pass


def main():
    print("C90: the system sampler idles unwatched; /api/sys reads its samples")
    clock = FakeTime()
    sp = FakeSubprocess()
    base = {"subprocess": sp, "time": clock, "os": FakeOs(), "shutil": shutil,
            "re": H.re, "model_online": lambda: False,
            "STARTED": clock.now - 600}
    ref = H.server_defs({"system_status"}, dict(base))["system_status"]
    hub_at = [0.0]
    widgets = {"system": {"title": "System", "provider": ref}}
    g = H.load(dict(base, system_status=ref, _hub_client_at=hub_at, WIDGETS=widgets))
    hist = g["SYS_HISTORY"]

    # 1. a dashboard nobody has opened
    run_loop(g, clock, 24)                     # two minutes of 5 s ticks
    spawned = sp.take()
    check("unwatched: two minutes of ticks spawn nothing", spawned == [], spawned[:6])
    check("unwatched: no samples are kept", len(hist) == 0, len(hist))

    # 2. the hub is open (hub_data stamped _hub_client_at)
    hub_at[0] = clock.now
    run_loop(g, clock, 3)
    spawned = sp.take()
    check("watched via /api/hub: one sample per tick", len(hist) == 3, len(hist))
    check("a sample is ps + vm_stat + netstat (sysctl once)",
          spawned.count("ps") == 3 and spawned.count("vm_stat") == 3
          and spawned.count("netstat") == 3 and spawned.count("sysctl") <= 1, spawned)

    # 3. /api/sys with a fresh sample in hand (the hub is open)
    clock.now = hist[-1]["ts"] + 2.5
    out = [g["system_status"]() for _ in range(4)]
    spawned = sp.take()
    check("system_status spawns nothing while the newest sample is fresh",
          spawned == [], spawned)
    want = ref()
    sp.take()
    check("same keys as server.py's system_status", set(out[0]) == set(want),
          (sorted(out[0]), sorted(want)))
    check("same ram_pct as server.py's vm_stat read (%s)" % want["ram_pct"],
          out[0]["ram_pct"] == want["ram_pct"] and isinstance(out[0]["ram_pct"], int),
          (out[0]["ram_pct"], want["ram_pct"]))
    check("cpu_pct is still the load average, uptime still from STARTED",
          out[0]["cpu_pct"] == want["cpu_pct"]
          and out[0]["uptime_min"] == want["uptime_min"], (out[0], want))
    check("the hub's System tile calls the new function",
          widgets["system"]["provider"] is g["system_status"], widgets["system"])

    # 4. everybody leaves: the sampler stops within SYS_IDLE_S and drops history
    idle_s = g.get("SYS_IDLE_S", 180)
    run_loop(g, clock, int(idle_s / 5) + 1)    # sampling until the window passes
    sp.take()
    run_loop(g, clock, 60)                     # five more minutes
    spawned = sp.take()
    check("idle: five minutes after the last client spawn nothing", spawned == [],
          spawned[:6])
    check("idle: the stale history is dropped", len(hist) == 0, len(hist))
    check("_sys_now-style readers find no stale sample", not hist, list(hist)[-1:])

    # 5. window closed, app still running: the menu-bar item polls /api/sys
    #    every 15 s (main.swift menuInfoTimer) — ten minutes of that
    for _ in range(40):
        st = g["system_status"]()
        run_loop(g, clock, 3)                  # 15 s of sampler ticks
    spawned = sp.take()
    check("menu-bar /api/sys polls do not wake the sampler (no ps/netstat)",
          spawned.count("ps") == 0 and spawned.count("netstat") == 0, spawned[:8])
    check("each /api/sys without a fresh sample reads vm_stat once, nothing else",
          spawned == ["vm_stat"] * 40, spawned[:8])
    check("and keeps no history for _sys_now() to mistake for 'now'", len(hist) == 0,
          len(hist))
    check("and answers with a real ram_pct", st["ram_pct"] == want["ram_pct"], st)

    # 6. the System pop-out opens: it seeds a point and the sampler resumes
    pop = g["expand_system"]()
    check("the pop-out gets a seeded history point",
          len(pop["history"]["ts"]) == 1 and pop["sys"]["ram_pct"] == want["ram_pct"],
          (len(pop["history"]["ts"]), pop["sys"]))
    sp.take()
    run_loop(g, clock, 2)
    check("the sampler resumes while the pop-out is open", len(hist) == 3, len(hist))
    H.finish()


if __name__ == "__main__":
    main()
