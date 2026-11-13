#!/usr/bin/env python3
"""Model advisor, cluster C1 — detection (docs/plans/model-advisor-2026-10.md §3).

Everything runs against the REAL dashboard/aux_advisor_hw.py, exec'd into a
fresh namespace the way server.py execs it, with the REAL _mem_ceilings
ast-extracted from server.py (tools/tests/lib/server_real.py).  The detection
commands are answered by a recording stub of the module-level `subprocess`
name, so no sysctl/ioreg/pmset/system_profiler runs here — except ONE check
that runs the real stdlib Metal probe in its child process (it creates a Metal
device, never a model; it is the probe the design measured in 0.1 s).

Covers: the pure parsers (brand, ioreg, system_profiler, perflevels, pmset
batt/-g/therm, form), the chip table (derived columns recomputed from the raw
ones for every row) and its lookup rules, the GPU budget precedence and the
assumed fractions at 8-192 GB, the plan limit for ten RAM sizes with and
without the MLX_SOFT_GB/MLX_HARD_GB env, the APC tiers parsed out of
mlx-server.sh / mlx-server-bg.sh (drift guard), the Metal probe's failure
modes, the cache (static once, dynamic 60 s, bust, wired-limit key), the
command allowlist, the serial-number whitelist, and the module's own hygiene
(no network imports, no writes, no routes, no threads, no name collisions
with any other dashboard module).
"""
import ast
import copy
import io
import json
import os
import re
import subprocess as _real_subprocess
import sys
import time

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
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


with io.open(AUX, encoding="utf-8") as _f:
    SRC = _f.read()

# --------------------------------------------------------------------------
# a recording stub of `subprocess` that answers like a given Mac
# --------------------------------------------------------------------------
THIS_MAC = {
    "machdep.cpu.brand_string": "Apple M5 Max",
    "hw.optional.arm64": "1",
    "sysctl.proc_translated": "0",
    "hw.model": "Mac17,6",
    "hw.memsize": str(64 * 2 ** 30),
    "hw.nperflevels": "2",
    "iogpu.wired_limit_mb": "0",
    "kern.boottime": "{ sec = 1789568983, usec = 731292 } Wed Sep 16 10:29:43 2026",
    "kern.memorystatus_level": "71",
    "vm.swapusage": "total = 3072.00M  used = 1432.38M  free = 1639.62M  (encrypted)",
    "perflevels": ("hw.perflevel0.physicalcpu: 6\nhw.perflevel0.logicalcpu: 6\n"
                   "hw.perflevel0.name: Super\nhw.perflevel1.physicalcpu: 12\n"
                   "hw.perflevel1.logicalcpu: 12\nhw.perflevel1.name: Performance\n"),
    "ioreg": '+-o AGXAcceleratorG17X  <class AGXAcceleratorG17X>\n    {\n'
             '      "gpu-core-count" = 40\n      "model" = "Apple M5 Max"\n    }\n',
    "sp_hardware": json.dumps({"SPHardwareDataType": [{
        "_name": "hardware_overview", "machine_name": "MacBook Pro", "machine_model": "Mac17,6",
        "chip_type": "Apple M5 Max", "number_processors": "proc 18:6:0:12",
        "physical_memory": "64 GB", "serial_number": "C02SERIALSECRET",
        "platform_UUID": "UUID-SECRET-1234", "provisioning_UDID": "UDID-SECRET-5678"}]}),
    "sp_displays": json.dumps({"SPDisplaysDataType": [{"sppci_cores": "40",
                                                        "sppci_model": "Apple M5 Max"}]}),
    "sw_vers": "26.5.2",
    "batt": ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=7077987)\t41%; "
             "discharging; 2:34 remaining present: true"),
    "pmset": "System-wide power settings:\nCurrently in use:\n standby              1\n"
             " powermode            2\n womp                 0\n",
    "therm": ("Note: No thermal warning level has been recorded\n"
              "Note: No performance warning level has been recorded\n"
              "Note: No CPU power status has been recorded\n"),
    "metal": json.dumps({"ok": True, "recommendedMaxWorkingSetSize": 55662788608,
                         "maxBufferLength": 41747087360, "hasUnifiedMemory": True,
                         "name": "Apple M5 Max"}),
}


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


class TimeoutExpired(Exception):
    pass


class FakeSub:
    """Answers the advisor's commands from `facts`; records every argv.
    `fail` = a set of keys to answer with rc=1; `boom` = keys that raise."""
    TimeoutExpired = TimeoutExpired

    def __init__(self, facts=None, fail=(), boom=(), metal=None, metal_rc=0, metal_exc=None):
        self.facts = dict(THIS_MAC if facts is None else facts)
        self.fail, self.boom = set(fail), set(boom)
        self.metal, self.metal_rc, self.metal_exc = metal, metal_rc, metal_exc
        self.calls = []
        self.kw = []

    def _key(self, argv):
        a0 = argv[0] if argv else ""
        if len(argv) >= 2 and argv[1] == "-I":
            return "metal"
        if a0 == "/usr/sbin/sysctl":
            if len(argv) >= 3 and argv[1] == "-n":
                return argv[2]
            return "perflevels"
        if a0 == "/usr/sbin/ioreg":
            return "ioreg"
        if a0 == "/usr/sbin/system_profiler":
            return "sp_hardware" if "SPHardwareDataType" in argv else "sp_displays"
        if a0 == "/usr/bin/sw_vers":
            return "sw_vers"
        if a0 == "/usr/bin/pmset":
            if argv[-1] == "batt":
                return "batt"
            if argv[-1] == "therm":
                return "therm"
            return "pmset"
        return "?" + a0

    def run(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        self.kw.append(kw)
        k = self._key(argv)
        if k in self.boom:
            raise OSError("stub: %s exploded" % k)
        if k == "metal":
            if self.metal_exc is not None:
                raise self.metal_exc
            out = self.facts.get("metal", "") if self.metal is None else self.metal
            return _R(out, self.metal_rc)
        if k in self.fail or k not in self.facts:
            return _R("", 1)
        return _R(self.facts[k], 0)

    def Popen(self, *a, **k):
        raise RuntimeError("advisor must never Popen")


class FakeTime:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def time(self):
        return self.t


def load(sub=None, ram=None, extra=None, with_ceilings=True):
    g = {"__name__": "aux_advisor_hw_under_test", "HOME": "/nonexistent-home",
         "os": os, "sys": sys}
    if with_ceilings:
        server_real.server_functions(["_mem_ceilings"], g)
    g["subprocess"] = sub if sub is not None else FakeSub()
    if ram is not None:
        g["_machine_ram_gb"] = lambda: float(ram)
    if extra:
        g.update(extra)
    exec(compile(SRC, AUX, "exec"), g)
    return g


# ==========================================================================
print("\n1. the module loads inert: no command at import, contract names defined")
sub0 = FakeSub()
G = load(sub0)
check("exec runs no subprocess at all", sub0.calls == [], sub0.calls)
CONTRACT = ["_adv_hw", "_adv_machine", "_adv_limits", "_adv_estimate", "_adv_verdict",
            "_adv_apc_entries", "_adv_measured", "_adv_prefix", "_adv_hw_notes",
            "_adv_download_gb", "_adv_disk_ok", "_adv_battery_label", "_adv_formulas",
            "_adv_constants", "_ADV_FACTS", "_ADV_CHIPS", "_ADV_REF_KEY", "_adv_parse_brand",
            "_adv_parse_ioreg_cores", "_adv_parse_sp_displays", "_adv_parse_sp_hardware",
            "_adv_parse_perflevels", "_adv_parse_batt", "_adv_parse_pmset", "_adv_parse_therm",
            "_adv_form", "_adv_gpu_budget", "_adv_chip_row", "_adv_estimates",
            "_adv_metal_probe", "_adv_run", "_adv_assemble"]
missing = [n for n in CONTRACT if n not in G]
check("every contract name is defined", not missing, missing)
check("subprocess was NOT re-imported over a stub already in the namespace",
      G["subprocess"] is sub0)

print("\n2. source hygiene")
TREE = ast.parse(SRC, filename=AUX)
imports = set()
for node in ast.walk(TREE):
    if isinstance(node, ast.Import):
        imports.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imports.add((node.module or "").split(".")[0])
check("never `from datetime import datetime` (CLAUDE.md aux gotcha)",
      not re.search(r"^\s*from\s+datetime\s+import", SRC, re.M))
check("no network module imported (urllib/http/socket/ssl)",
      not (imports & {"urllib", "http", "socket", "ssl", "requests"}), imports)
check("every stdlib import is aliased (_advhw_*), except the guarded subprocess",
      all(a.asname and a.asname.startswith("_advhw_") for n in ast.walk(TREE)
          if isinstance(n, ast.Import) for a in n.names if a.name != "subprocess"))
check("no file is ever opened (no writes, no caches on disk)", "open(" not in SRC)
# CODE, not comments: the header itself says what the module never touches
_doc_ids = set()
for node in ast.walk(TREE):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
            and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
        _doc_ids.add(id(node.body[0].value))
CODE_NAMES, CODE_STRS, CODE_INTS = set(), [], set()
for node in ast.walk(TREE):
    if isinstance(node, ast.Name):
        CODE_NAMES.add(node.id)
    elif isinstance(node, ast.Attribute):
        CODE_NAMES.add(node.attr)
    elif isinstance(node, ast.Constant) and id(node) not in _doc_ids:
        if isinstance(node.value, str):
            CODE_STRS.append(node.value)
        elif isinstance(node.value, int) and not isinstance(node.value, bool):
            CODE_INTS.add(node.value)
for bad in ("settings_update", "settings_set", "write_json", "register_get", "register_post",
            "Thread", "model_online", "bg_online", "mlx_admission", "urlopen", "Popen"):
    check("code never names %s" % bad, bad not in CODE_NAMES)
for port in (7788, 8080, 8081, 9119):
    check("code never carries port %d (as a number or inside a string)" % port,
          port not in CODE_INTS and not any(str(port) in s_ for s_ in CODE_STRS))
check("no string in the code mentions launchctl", not any("launchctl" in s_ for s_ in CODE_STRS))
mine = set()
for node in TREE.body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        mine.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(e, ast.Name):
                    mine.add(e.id)
check("every top-level name is namespaced (_adv_ / _ADV_ / _advhw_ / _ADVHW_)",
      all(n.startswith(("_adv_", "_ADV_", "_advhw_", "_ADVHW_")) for n in mine),
      sorted(n for n in mine if not n.startswith(("_adv_", "_ADV_", "_advhw_", "_ADVHW_"))))
clash = {}
for fn in sorted(os.listdir(DASH)):
    if not fn.endswith(".py") or fn == "aux_advisor_hw.py":
        continue
    try:
        t = ast.parse(io.open(os.path.join(DASH, fn), encoding="utf-8").read())
    except SyntaxError:
        continue
    for node in t.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [e.id for tg in node.targets
                     for e in (tg.elts if isinstance(tg, ast.Tuple) else [tg])
                     if isinstance(e, ast.Name)]
        for n in names:
            if n in mine:
                clash.setdefault(fn, []).append(n)
check("no other dashboard module defines a name this module defines "
      "(aux_advisor.py sorts BEFORE this file, so a clash would be silently overwritten)",
      not clash, clash)

# ==========================================================================
print("\n3. _adv_parse_brand")
pb = G["_adv_parse_brand"]
for (fam, var, cores) in sorted(G["_ADV_CHIPS"]):
    name = "Apple %s%s" % (fam, (" " + var) if var else "")
    r = pb(name)
    if not (r["family"] == fam and r["variant"] == var and r["gen"] == int(fam[1:])
            and r["supported"] is True and r["vendor"] == "apple"):
        check("brand %r parses" % name, False, r)
        break
else:
    check("every chip name in the table parses to its family / variant / gen", True)
r = pb("Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz")
check("Intel -> supported False with the Apple-silicon reason",
      r["supported"] is False and r["vendor"] == "intel" and "Apple silicon" in r["reason"], r)
r = pb("Apple A18 Pro")
check("Apple A18 Pro -> apple, family None, supported (conservative fallback)",
      r["vendor"] == "apple" and r["family"] is None and r["supported"] is True, r)
r = pb("Apple M6 Max")
check("Apple M6 Max -> family M6, gen 6, Max", (r["family"], r["gen"], r["variant"]) == ("M6", 6, "Max"), r)
for bad in ("", None, "garbage \x00\x07 string", 42, "Apple M5 Turbo"):
    r = pb(bad)
    check("brand %r never raises, family None" % (bad,), r["family"] is None, r)
check("brand control characters are stripped", "\x00" not in pb("Apple\x00 M5")["brand"])
check("'Apple M5 Max ' (trailing space) still parses", pb("Apple M5 Max \n")["variant"] == "Max")

print("\n4. ioreg / system_profiler")
pi = G["_adv_parse_ioreg_cores"]
check("ioreg gpu-core-count = 40", pi(THIS_MAC["ioreg"]) == 40)
check("ioreg without gpu-core-count -> None", pi('+-o AGX\n  { "model" = "x" }') is None)
check("ioreg empty / None -> None", pi("") is None and pi(None) is None)
check("ioreg absurd count -> None", pi('"gpu-core-count" = 0') is None and pi('"gpu-core-count" = 99999') is None)
ps = G["_adv_parse_sp_displays"]
check("system_profiler sppci_cores is a STRING: '40' -> 40", ps(THIS_MAC["sp_displays"]) == 40)
check("...the Apple GPU is preferred over another adapter",
      ps(json.dumps({"SPDisplaysDataType": [{"sppci_model": "Some eGPU", "sppci_cores": "7"},
                                             {"sppci_model": "Apple M2", "sppci_cores": "10"}]})) == 10)
check("system_profiler garbage / no cores -> None",
      ps("not json") is None and ps("{}") is None
      and ps(json.dumps({"SPDisplaysDataType": [{"sppci_cores": "n/a"}]})) is None)
sh = G["_adv_parse_sp_hardware"]
hwx = sh(THIS_MAC["sp_hardware"])
check("SPHardware -> machine_name / machine_model / chip_type",
      hwx["machine_name"] == "MacBook Pro" and hwx["machine_model"] == "Mac17,6"
      and hwx["chip_type"] == "Apple M5 Max", hwx)
dumped = json.dumps(hwx)
check("SPHardware is a WHITELIST: serial number, platform UUID and UDID never leave it",
      "SECRET" not in dumped and "serial" not in dumped and "UUID" not in dumped, dumped)
check("SPHardware garbage -> empty strings, no raise", sh("{{{")["machine_name"] == "")

print("\n5. perflevels and form")
pp = G["_adv_parse_perflevels"]
lv = pp(THIS_MAC["perflevels"])
check("text: Super x6, Performance x12 in level order",
      [(x["name"], x["logicalcpu"]) for x in lv] == [("Super", 6), ("Performance", 12)], lv)
lv2 = pp({"hw.perflevel1.name": "Efficiency", "hw.perflevel1.logicalcpu": "4",
          "hw.perflevel0.name": "Performance", "hw.perflevel0.logicalcpu": "8"})
check("map form, sorted by level, names verbatim (perflevel1 is never assumed to be E-cores)",
      [(x["level"], x["name"]) for x in lv2] == [(0, "Performance"), (1, "Efficiency")], lv2)
check("perflevels empty -> []", pp("") == [] and pp(None) == [])
fm = G["_adv_form"]
check("MacBook Air -> laptop_fanless", fm("MacBook Air", True) == "laptop_fanless")
check("MacBook Pro -> laptop", fm("MacBook Pro", True) == "laptop")
check("Mac mini (no battery) -> desktop", fm("Mac mini", False) == "desktop")
check("Mac Studio, battery unknown -> desktop", fm("Mac Studio", None) == "desktop")
check("no name but an internal battery -> laptop", fm("", True) == "laptop")
check("nothing known -> unknown", fm("", None) == "unknown")

print("\n6. pmset")
pbt = G["_adv_parse_batt"]
b = pbt(THIS_MAC["batt"])
check("battery: source battery, 41%, has_battery, discharging",
      b == {"source": "battery", "pct": 41, "has_battery": True, "state": "discharging"}, b)
b = pbt("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true")
check("laptop on AC: source ac, 100%, charged", (b["source"], b["pct"], b["state"]) == ("ac", 100, "charged"), b)
b = pbt("Now drawing from 'AC Power'")
check("desktop: source ac, has_battery False, pct None",
      (b["source"], b["has_battery"], b["pct"]) == ("ac", False, None), b)
b = pbt("")
check("pmset unavailable: source unknown, has_battery None", (b["source"], b["has_battery"]) == ("unknown", None), b)
pm = G["_adv_parse_pmset"]
check("lowpowermode 1 -> low_power True", pm(" lowpowermode         1\n")["low_power"] is True)
check("lowpowermode 0 -> low_power False", pm(" lowpowermode         0\n")["low_power"] is False)
r = pm(THIS_MAC["pmset"])
check("powermode 2 -> False, with the mapping flagged unverified",
      r["low_power"] is False and "unverified" in r["low_power_source"], r)
check("powermode 1 -> True (believed mapping)", pm(" powermode 1\n")["low_power"] is True)
check("neither line -> None", pm(" standby 1\n")["low_power"] is None and pm(None)["low_power"] is None)
pt = G["_adv_parse_therm"]
r = pt(THIS_MAC["therm"])
check("'No thermal warning level has been recorded' x3 -> limited False", r["limited"] is False, r)
r = pt("CPU_Scheduler_Limit \t= 100\nCPU_Available_CPUs \t= 18\nCPU_Speed_Limit \t= 80\n")
check("CPU_Speed_Limit = 80 -> limited True, 80", (r["limited"], r["cpu_speed_limit"]) == (True, 80), r)
check("CPU_Speed_Limit = 100 -> limited False", pt("CPU_Speed_Limit = 100")["limited"] is False)
check("unreadable -> None", pt("")["limited"] is None and pt("???")["limited"] is None)

# ==========================================================================
print("\n7. the chip table: derived columns recomputed from the raw ones, every row")
CH = G["_ADV_CHIPS"]
REFR = 119.92 / 614.0
bad = []
for key, row in sorted(CH.items()):
    if row["source"] == "scaled":
        srcrow = CH[(key[0], key[1], row["scaled_from"])]
        e = srcrow["e_chip"]
        p = srcrow["pp"] / row["scaled_from"] * key[2] / 3219.99
    else:
        e = (row["tg"] / row["bw"]) / REFR
        p = row["pp"] / 3219.99
    if round(e, 4) != row["e_chip"] or round(p, 4) != row["p_index"]:
        bad.append((key, row["e_chip"], round(e, 4), row["p_index"], round(p, 4)))
check("every row's e_chip / p_index == the raw columns (4 dp)", not bad, bad)
ref = CH[G["_ADV_REF_KEY"]]
check("the reference row is M5 Max 40c: 614 GB/s, e_chip = p_index = 1",
      G["_ADV_REF_KEY"] == ("M5", "Max", 40) and ref["bw"] == 614.0
      and ref["e_chip"] == 1.0 and ref["p_index"] == 1.0 and ref["confidence"] == "reference")
check("every family M1..M5 has a base row",
      all(any(k[0] == f and k[1] == "" for k in CH) for f in ("M1", "M2", "M3", "M4", "M5")))
check("every row: bw > 0, a source and a confidence",
      all(r["bw"] > 0 and r["source"] in ("measured", "scaled", "reference")
          and r["confidence"] in ("medium", "low", "reference") for r in CH.values()))
check("scaled rows are always low confidence",
      all(r["confidence"] == "low" for r in CH.values() if r["source"] == "scaled"))
check("the design's flagged rows are low: M5 10c, M5 Ultra 80c, every scaled row",
      CH[("M5", "", 10)]["confidence"] == "low" and CH[("M5", "Ultra", 80)]["confidence"] == "low")
check("design spot values: M4 Max 40c e 0.78 / p 0.275, M3 Pro 18c 1.05 / 0.106, M2 Ultra 76c 0.60 / 0.385",
      (round(CH[("M4", "Max", 40)]["e_chip"], 2), round(CH[("M4", "Max", 40)]["p_index"], 3)) == (0.78, 0.275)
      and (round(CH[("M3", "Pro", 18)]["e_chip"], 2), round(CH[("M3", "Pro", 18)]["p_index"], 3)) == (1.05, 0.106)
      and (round(CH[("M2", "Ultra", 76)]["e_chip"], 2), round(CH[("M2", "Ultra", 76)]["p_index"], 3)) == (0.60, 0.385))

print("\n8. chip lookup rules")
cr = G["_adv_chip_row"]
r = cr("M5", "Max", 40)
check("exact: M5 Max 40c is the reference row", r["row"] == "M5 Max 40c" and r["reference"] is True
      and r["confidence"] == "reference" and r["cores_source"] == "detected", r)
r = cr("M4", "Max", None)
check("unknown cores -> the LOWER bin (M4 Max 32c), cores_source assumed",
      r["row"] == "M4 Max 32c" and r["cores_source"] == "assumed" and r["bandwidth_gbs"] == 410.0, r)
r = cr("M3", "Max", None)
check("...M3 Max unknown cores -> 30c (300 GB/s), never the 40c", r["row"] == "M3 Max 30c" and r["bandwidth_gbs"] == 300.0, r)
r = cr("M4", "Pro", 14)
check("cores with no row (M4 Pro 14c) -> scaled per core from 16c, low",
      r["row"] == "M4 Pro 14c" and r["row_source"] == "scaled" and r["confidence"] == "low"
      and abs(r["p_index"] - round(CH[("M4", "Pro", 16)]["p_index"] / 16 * 14, 4)) < 1e-9, r)
r = cr("M5", "Max", 36)
check("...between two rows, nearest wins (M5 Max 36c from 32c/40c tie -> lower 32c)",
      "32c" in " ".join(r["notes"]), r)
r = cr("M4", "Ultra", 64)
check("unknown variant of a known family (M4 Ultra) -> the base M4 row, low",
      r["family"] == "M4" and r["variant"] == "" and r["confidence"] == "low" and r["rule"] == "unknown-variant", r)
r = cr("M6", "Max", 40)
check("a family newer than the table (M6 Max) -> M5 Max as a floor, low, never 'reference'",
      r["row"] == "M5 Max 40c" and r["confidence"] == "low" and r["reference"] is False
      and r["rule"] == "newer-family", r)
r = cr(None, "", None)
check("an unknown Apple chip -> M1 8c (the slowest known row), low, with the design's note",
      r["row"] == "M1 8c" and r["confidence"] == "low"
      and any("slowest" in n for n in r["notes"]), r)
r = cr("M1", "", 8)
check("M1 8c exact is medium (a measured row)", r["confidence"] == "medium" and r["rule"] == "exact", r)

# ==========================================================================
print("\n9. _adv_gpu_budget precedence and the assumed fractions")
gb = G["_adv_gpu_budget"]
r = gb(64, 57344, 55662788608)
check("iogpu.wired_limit_mb > 0 wins over Metal", r["source"] == "iogpu.wired_limit_mb" and r["gb"] == 56.0, r)
r = gb(64, 0, 55662788608)
check("Metal next: 55,662,788,608 B = 51.84 GiB (0.81 x 64)",
      r["source"] == "metal" and r["gb"] == 51.84 and r["fraction"] == 0.81, r)
for ram in (8, 16, 24, 32, 36, 48, 64, 96, 128, 192):
    r = gb(ram, 0, 0)
    want = round(ram * (0.75 if ram >= 36 else 0.66), 2)
    if not (r["source"] == "assumed" and r["gb"] == want):
        check("assumed fraction at %d GB" % ram, False, r)
        break
else:
    check("assumed: 0.66 x RAM below 36 GB, 0.75 x at >= 36 (8..192 GB)", True)
check("8 GB assumed = 5.28, 16 GB = 10.56 (the design's worked limits)",
      gb(8, 0, 0)["gb"] == 5.28 and gb(16, 0, 0)["gb"] == 10.56)
check("a Metal answer larger than RAM is not believed -> assumed",
      gb(16, 0, 64 * 2 ** 30)["source"] == "assumed")
check("garbage inputs never raise", gb(None, "x", "y")["source"] == "assumed")

print("\n10. _adv_limits for ten RAM sizes, through the REAL _mem_ceilings")
lim = G["_adv_limits"]


def L(ram, env=None, metal=None, wired=0, g=G):
    hw = {"ram_gb": float(ram), "gpu_budget": g["_adv_gpu_budget"](ram, wired, metal or 0)}
    return g["_adv_limits"](hw, env={} if env is None else env)


WANT = {8: (4.0, "soft"), 16: (10.0, "soft"), 24: (15.0, "soft"), 32: (21.0, "soft"),
        36: (24.0, "soft"), 48: (33.0, "soft"), 64: (48.0, "soft"), 96: (48.0, "soft"),
        128: (48.0, "soft"), 192: (48.0, "soft")}
for ram, (want, bind) in sorted(WANT.items()):
    r = L(ram)
    check("%3d GB -> limit %.1f (%s binds)" % (ram, want, bind),
          r["limit_gb"] == want and r["binding"] == bind, r)
r = L(64, metal=55662788608)
check("this Mac (Metal 51.84): soft binds at 48.0, terms reported",
      r["limit_gb"] == 48.0 and r["binding"] == "soft" and r["terms"]["gpu"] == 51.84
      and r["soft_gb"] == 50.0 and r["hard_gb"] == 56.0, r)
check("...and the >= 64 GB note says Hermes caps model memory at 50 GB", "50 GB" in r["note"], r["note"])
r = L(128, env={"MLX_SOFT_GB": "80"})
check("MLX_SOFT_GB=80 on 128 GB flows through: soft term 78 binds",
      r["limit_gb"] == 78.0 and r["binding"] == "soft" and r["soft_gb"] == 80.0, r)
r = L(64, env={"MLX_SOFT_GB": "80"}, metal=55662788608)
check("MLX_SOFT_GB=80 on this Mac: the GPU budget binds instead (51.84)",
      r["limit_gb"] == 51.84 and r["binding"] == "gpu", r)
r = L(32, env={"MLX_HARD_GB": "31"})
check("MLX_HARD_GB only changes hard, not the plan limit", r["limit_gb"] == 21.0 and r["hard_gb"] == 31.0, r)
r = L(8, env={"MLX_SOFT_GB": "abc"})
check("an unparseable env value is ignored (8 GB: soft 6 - 2 = 4.0 binds, D9)", r["limit_gb"] == 4.0, r)
G2 = load(with_ceilings=False)
r = L(32, g=G2)
check("_mem_ceilings missing -> no raise, the soft term is simply absent",
      r["soft_gb"] is None and r["binding"] == "gpu" and r["limit_gb"] == 21.12, r)
r = lim({"ram_gb": None, "gpu_budget": {}}, env={})
check("RAM unknown -> limit None, no raise", r["limit_gb"] is None, r)
check("every limit carries estimate:true and a formula", L(36)["estimate"] is True and "min(" in L(36)["formula"])
check("D9 is fixed: the 8 GB Mac's soft ceiling is 6 (no longer all of its RAM), so "
      "admission can engage and the advisor's limit is soft - 2 = 4.0",
      L(8)["soft_gb"] == 6.0 and L(8)["binding"] == "soft" and L(8)["limit_gb"] == 4.0, L(8))

print("\n11. _adv_apc_entries == the launch scripts (drift guard)")


def shell_tiers(path):
    txt = io.open(path, encoding="utf-8").read()
    tiers = [(int(a), int(b)) for a, b in re.findall(
        r'\[ "\$RAM_GB" -ge (\d+) \]; then APC_ENTRIES_DEFAULT=(\d+)', txt)]
    m = re.search(r"^\s*else\s+APC_ENTRIES_DEFAULT=(\d+)", txt, re.M)
    return tiers, (int(m.group(1)) if m else None)


def shell_entries(tiers, other, ram_gib):
    r = int(ram_gib)                    # $(( RAM_BYTES / 1073741824 ))
    for floor, n in tiers:
        if r >= floor:
            return n
    return other


for lane, script in (("primary", "mlx-server.sh"), ("bg", "mlx-server-bg.sh")):
    tiers, other = shell_tiers(os.path.join(REPO, script))
    check("%s: tiers parsed (%s, else %s)" % (script, tiers, other), len(tiers) == 3 and other is not None)
    diffs = []
    for ram in (8, 12, 16, 18, 24, 32, 35.9, 36, 47.9, 48, 63.9, 64, 96, 128, 192):
        a = G["_adv_apc_entries"](ram, lane)
        b = shell_entries(tiers, other, ram)
        if a != b:
            diffs.append((ram, a, b))
    check("%s lane: _adv_apc_entries matches %s at every size" % (lane, script), not diffs, diffs)
check("design values: primary 6/4/3/2, bg 4/3/2/2",
      [G["_adv_apc_entries"](r, "primary") for r in (64, 48, 36, 16)] == [6, 4, 3, 2]
      and [G["_adv_apc_entries"](r, "bg") for r in (64, 48, 36, 16)] == [4, 3, 2, 2])

# ==========================================================================
print("\n12. the Metal probe, stubbed")
for label, kw, ok, want in (
        ("success JSON after noise lines", {"metal": "warning: x\n" + THIS_MAC["metal"] + "\n"}, True, 55662788608),
        ("timeout", {"metal_exc": TimeoutExpired("2s")}, False, None),
        ("non-zero exit", {"metal_rc": 1}, False, None),
        ("garbage output", {"metal": "Segmentation fault\n"}, False, None),
        ("ok:false JSON (no device)", {"metal": '{"ok": false, "error": "no Metal device"}'}, False, None),
        ("zero working set", {"metal": '{"ok": true, "recommendedMaxWorkingSetSize": 0}'}, False, None),
        ("a boolean is not a size", {"metal": '{"ok": true, "recommendedMaxWorkingSetSize": true}'}, False, None),
        ("an OSError from spawn", {"metal_exc": OSError("no python")}, False, None)):
    s = FakeSub(**kw)
    g = load(s)
    r = g["_adv_metal_probe"]()
    check("probe %s -> ok %s" % (label, ok), r.get("ok") is ok and (want is None or r.get("bytes") == want), r)
s = FakeSub()
g = load(s)
g["_adv_metal_probe"]()
argv, kw = s.calls[-1], s.kw[-1]
check("probe argv = [sys.executable, -I, -c, <stdlib ctypes source>]",
      argv[0] == (sys.executable or "/usr/bin/python3") and argv[1:3] == ["-I", "-c"]
      and "MTLCreateSystemDefaultDevice" in argv[3] and "import ctypes, json" in argv[3], argv[:3])
check("probe runs with a 2 s timeout and an env holding only PATH",
      kw.get("timeout") == 2.0 and kw.get("env") == {"PATH": "/usr/bin:/bin"}, kw)
check("the probe source imports nothing but ctypes and json",
      set(re.findall(r"^\s*import\s+([\w, ]+)", G["_ADV_PROBE_SRC"], re.M)) == {"ctypes, json"})
s = FakeSub(metal_exc=TimeoutExpired("2s"))
g = load(s, ram=64)
hw = g["_adv_hw"]()
check("a probe timeout degrades the GPU budget to 'assumed' (0.75 x 64 = 48)",
      hw["gpu_budget"]["source"] == "assumed" and hw["gpu_budget"]["gb"] == 48.0
      and hw["gpu_budget"]["metal"]["ok"] is False, hw["gpu_budget"])

print("\n13. the REAL Metal probe, once (a Metal device in a child; never a model)")
g = load(extra=None)
g["subprocess"] = _real_subprocess
t0 = time.time()
r = g["_adv_metal_probe"]()
dt = time.time() - t0
check("the real probe never raises and answers a well-formed dict within ~2.5 s",
      isinstance(r, dict) and isinstance(r.get("ok"), bool) and dt < 2.5, (r, dt))
if r.get("ok"):
    check("...ok: a positive integer working set and a name (no size floor: a CI VM's virtual GPU is small)",
          isinstance(r["bytes"], int) and r["bytes"] > 0 and isinstance(r.get("name"), str), r)
else:
    check("...not ok: it says why (the caller falls back to 'assumed')", bool(r.get("error")), r)

# ==========================================================================
print("\n14. _adv_hw on a stub of this Mac: shape, sources, allowlist")
s = FakeSub()
g = load(s, ram=64)
hw = g["_adv_hw"]()
c = hw["chip"]
check("chip: M5 Max 40c, the reference row, cores from ioreg",
      c["row"] == "M5 Max 40c" and c["reference"] and c["cores_source"] == "ioreg"
      and c["bandwidth_gbs"] == 614.0 and c["brand"] == "Apple M5 Max", c)
check("RAM 64 (via _machine_ram_gb), GPU budget 51.84 from Metal",
      hw["ram_gb"] == 64.0 and hw["gpu_budget"]["gb"] == 51.84 and hw["gpu_budget"]["source"] == "metal", hw["gpu_budget"])
check("machine MacBook Pro, model Mac17,6, form laptop, not fanless",
      (hw["machine_name"], hw["model_id"], hw["form"], hw["fanless"]) == ("MacBook Pro", "Mac17,6", "laptop", False))
check("power: battery 41%, low_power False (powermode 2, flagged unverified)",
      hw["power"]["source"] == "battery" and hw["power"]["pct"] == 41
      and hw["power"]["low_power"] is False and "unverified" in hw["power"]["low_power_source"], hw["power"])
check("thermal not limited, macOS 26.5.2, CPU levels Super/Performance, 18 logical",
      hw["thermal"]["limited"] is False and hw["macos"] == "26.5.2"
      and [x["name"] for x in hw["cpu"]["levels"]] == ["Super", "Performance"] and hw["cpu"]["count"] == 18)
check("right-now line: memory level 71, swap 1.4 GiB (shown, never ranked)",
      hw["now"]["memory_free_pct"] == 71 and hw["now"]["swap_used_gb"] == 1.4, hw["now"])
check("local True, supported, rosetta False", hw["local"] is True and hw["supported"] and hw["rosetta"] is False)
check("system_profiler SPDisplays is NOT run when ioreg already answered",
      not any("SPDisplaysDataType" in a for a in s.calls))
dumped = json.dumps(hw)
check("the hw dict is JSON-serialisable and carries no serial / UUID", "SECRET" not in dumped)
allowed = set(g["_ADV_COMMANDS"])
strays = [a for a in s.calls if not (a[0] in allowed or (len(a) > 1 and a[1] == "-I"))]
check("every command is an absolute path on the allowlist (sysctl/ioreg/pmset/system_profiler/sw_vers + the probe)",
      not strays, strays)
check("no command is run through a shell", all(not kw.get("shell") for kw in s.kw))

print("\n15. the cache: static once, dynamic 60 s, bust, the wired-limit key")
s = FakeSub()
ft = FakeTime()
g = load(s, ram=64, extra=None)
g["_advhw_time"] = ft
g["_adv_hw"]()
n1 = len(s.calls)
probes = lambda: sum(1 for a in s.calls if len(a) > 1 and a[1] == "-I")
g["_adv_hw"]()
check("a second call inside 60 s runs nothing", len(s.calls) == n1, s.calls[n1:])
ft.t += 61
g["_adv_hw"]()
new = s.calls[n1:]
check("after 60 s: the dynamic facts and the key are re-read, the static ones are not",
      any(a[-1] == "batt" for a in new) and any("kern.boottime" in a for a in new)
      and not any("machdep.cpu.brand_string" in a for a in new) and probes() == 1, new)
n2 = len(s.calls)
g["_adv_hw"](bust=True)
new = s.calls[n2:]
check("bust=True refreshes power at once without re-probing Metal",
      any(a[-1] == "batt" for a in new) and probes() == 1, new)
s.facts["iogpu.wired_limit_mb"] = "57344"
ft.t += 61
hw = g["_adv_hw"]()
check("an owner's `sudo sysctl iogpu.wired_limit_mb=57344` re-detects the static facts",
      probes() == 2 and hw["gpu_budget"]["source"] == "iogpu.wired_limit_mb" and hw["gpu_budget"]["gb"] == 56.0,
      hw["gpu_budget"])
hw["chip"]["row"] = "TAMPERED"
hw["gpu_budget"]["gb"] = -1
again = g["_adv_hw"]()
check("the result is a deep copy: mutating it cannot corrupt the cache",
      again["chip"]["row"] == "M5 Max 40c" and again["gpu_budget"]["gb"] == 56.0)

print("\n16. detection never raises")
s = FakeSub(facts={})
g = load(s)
hw = g["_adv_hw"]()
check("every command failing -> a hw dict, supported (unknown is not blocked), M1 8c fallback, low",
      hw["ok"] and hw["supported"] and hw["chip"]["row"] == "M1 8c" and hw["chip"]["confidence"] == "low"
      and hw["ram_gb"] is None and hw["gpu_budget"]["source"] == "assumed", hw)
check("...and the plan limit is None rather than a guess", G["_adv_limits"](hw, env={})["limit_gb"] is None)
check("...and sources say unknown", hw["sources"]["chip"] == "unknown" and hw["sources"]["power"] == "unknown")
s = FakeSub(boom={"machdep.cpu.brand_string", "ioreg", "batt", "metal", "sp_hardware"})
g = load(s, ram=32)
hw = g["_adv_hw"]()
check("commands that RAISE degrade the same way", hw["ok"] and hw["ram_gb"] == 32.0, hw.get("error"))
g = load(FakeSub())
g["_adv_assemble"] = lambda raw, local=False: 1 / 0
hw = g["_adv_hw"]()
check("even an internal bug comes back as a hw dict with `error`, not an exception",
      isinstance(hw, dict) and "ZeroDivisionError" in hw.get("error", ""), hw.get("error"))
s = FakeSub(fail={"ioreg"})
g = load(s, ram=64)
hw = g["_adv_hw"]()
check("ioreg failing -> system_profiler SPDisplays supplies the cores (string '40')",
      hw["chip"]["gpu_cores"] == 40 and hw["chip"]["cores_source"] == "system_profiler"
      and any("SPDisplaysDataType" in a for a in s.calls), hw["chip"])
s = FakeSub(fail={"ioreg", "sp_displays"})
g = load(s, ram=64)
hw = g["_adv_hw"]()
check("no core count at all -> the variant's lower bin, cores_source assumed",
      hw["chip"]["row"] == "M5 Max 32c" and hw["chip"]["cores_source"] == "assumed", hw["chip"])
facts = dict(THIS_MAC, **{"machdep.cpu.brand_string": "Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz",
                          "hw.optional.arm64": "0"})
s = FakeSub(facts=facts)
g = load(s, ram=32)
hw = g["_adv_hw"]()
check("an Intel Mac: supported False, chip None, the reason sentence",
      hw["supported"] is False and hw["chip"] is None and "Apple silicon" in hw["unsupported_reason"], hw)
facts = dict(THIS_MAC, **{"machdep.cpu.brand_string": ""})
facts.pop("hw.optional.arm64")
g = load(FakeSub(facts=facts), ram=16)
hw = g["_adv_hw"]()
check("brand unreadable and arm64 unknown -> estimated conservatively, says so",
      hw["supported"] and hw["chip"]["row"] == "M1 8c"
      and any("could not identify" in n for n in hw["chip"]["notes"]), hw["chip"])
g = load(FakeSub(facts=dict(THIS_MAC, **{"sysctl.proc_translated": "1"})), ram=64)
check("Rosetta (proc_translated 1) is reported as a note", g["_adv_hw"]()["rosetta"] is True)

print("\n17. _adv_machine (synthetic), notes, small helpers")
mk = G["_adv_machine"]
hw = mk("Apple M2", 16, gpu_cores=10, machine_name="MacBook Air", on_battery=True, low_power=True)
check("synthetic Air M2 16 GB: local False, fanless, battery, Low Power Mode, GPU assumed 10.56",
      hw["local"] is False and hw["fanless"] and hw["power"]["source"] == "battery"
      and hw["power"]["low_power"] is True and hw["gpu_budget"]["gb"] == 10.56
      and hw["chip"]["row"] == "M2 10c", hw)
notes = G["_adv_hw_notes"](hw)
check("notes: battery, Low Power Mode, no fan, assumed GPU budget",
      any("On battery" in n for n in notes) and any("Low Power Mode" in n for n in notes)
      and any("No fan" in n for n in notes) and any("assumed" in n for n in notes), notes)
g = load(extra={"_idle_min": lambda: 10})
check("the battery note quotes the idle-suspend minutes when the dashboard knows them",
      any("after 10 min" in n for n in g["_adv_hw_notes"](hw)))
hw = mk("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro", metal_bytes=55662788608,
        thermal_limit=80)
notes = G["_adv_hw_notes"](hw)
check("thermal limit 80 -> 'a CPU speed limit right now (80%)'", any("(80%)" in n for n in notes), notes)
check("metal_bytes given -> GPU budget 51.84 from metal", hw["gpu_budget"]["source"] == "metal")
check("a Mac Studio is a desktop", mk("Apple M2 Ultra", 192, gpu_cores=76, machine_name="Mac Studio")["form"] == "desktop")
check("Intel synthetic -> the unsupported note only",
      G["_adv_hw_notes"](mk("Intel(R) Core(TM) i7", 16)) == ["Local models need Apple silicon; this Mac has an Intel chip."])
vd = G["_adv_verdict"]
check("verdict: 40.8 of 48 fits (0.85 edge), 40.9 tight, 48 tight, 48.1 no, None for garbage",
      vd(40.8, 48) == "fits" and vd(40.9, 48) == "tight" and vd(48.0, 48) == "tight"
      and vd(48.1, 48) == "no" and vd(None, 48) is None and vd(10, 0) is None)
dk = G["_adv_disk_ok"]
check("disk_ok: free - need >= 5, and FAILS OPEN on an unknown",
      dk(17.0, 22.0) and not dk(17.0, 21.9) and dk(None, 1.0) and dk(17.0, None))
check("disk_ok follows server.py's DISK_HEADROOM_GB by name",
      load(extra={"DISK_HEADROOM_GB": 10.0})["_adv_disk_ok"](17.0, 26.0) is False)
bl = G["_adv_battery_label"]
check("battery label: 1.65 GB light, 5.95 moderate, 16.05 heavy; a bg lane adds one step",
      bl(1.65) == "light" and bl(5.95) == "moderate" and bl(16.05) == "heavy"
      and bl(1.65, True) == "moderate" and bl(16.05, True) == "heavy" and bl(None) is None)
check("download: 27B 16.1 + drafter 0.9 = 17.0 GB; unknown id None",
      G["_adv_download_gb"]("mlx-community/Qwen3.8-27B-4bit") == 17.0
      and G["_adv_download_gb"]("nobody/nothing") is None)
check("formulas and constants are JSON-serialisable for the state route",
      bool(json.dumps(G["_adv_formulas"]()) and json.dumps(G["_adv_constants"]())))
check("the constants quote the S rule's cap and the probe retry interval for the 'How we estimate' panel",
      G["_adv_constants"]()["cold_max_prefix_tokens"] == 32000 and G["_adv_constants"]()["probe_retry_s"] == 600.0)


# ==========================================================================
print("\n18. review additions: totality, reserve terms, RAM / disk fallbacks, probe retry, A-series")
BAD_INPUTS = [None, True, 5, 1.5, float("nan"), [], [1], {}, {"a": 1}, "", "\x00\x07", b"bytes"]
raised = []
for fn_name in ("_adv_parse_ioreg_cores", "_adv_parse_sp_displays", "_adv_parse_sp_hardware",
                "_adv_parse_perflevels", "_adv_parse_batt", "_adv_parse_pmset", "_adv_parse_therm",
                "_advhw_parse_boottime", "_advhw_parse_swap"):
    for bad_in in BAD_INPUTS:
        try:
            G[fn_name](bad_in)
        except Exception as ex_:
            raised.append((fn_name, repr(bad_in), type(ex_).__name__))
for bad_in in BAD_INPUTS:
    try:
        G["_adv_form"](bad_in, None)
        G["_adv_parse_brand"](bad_in)
    except Exception as ex_:
        raised.append(("_adv_form/_adv_parse_brand", repr(bad_in), type(ex_).__name__))
check("every text parser is total: None, a bool, a number, a list, a dict or bytes never raise", not raised, raised[:5])
check("a swap figure like '1.2.3M' is None, not a ValueError", G["_advhw_parse_swap"]("used = 1.2.3M") is None)

rv = G["_advhw_reserve_gb"]
check("reserve: 6 GB from 24 GB, 4.5 from 16, 3.5 below (the §3.5 judgement, exact edges)",
      (rv(128), rv(24), rv(23.9), rv(16), rv(15.9), rv(8)) == (6.0, 6.0, 4.5, 4.5, 3.5, 3.5))
bad = []
for ram, want in ((16, 11.5), (24, 18.0), (32, 26.0), (8, 4.5)):
    r = lim({"ram_gb": float(ram), "gpu_budget": {"gb": float(ram), "source": "iogpu.wired_limit_mb"}},
            env={"MLX_SOFT_GB": "1000", "MLX_HARD_GB": "1002"})
    if not (r["limit_gb"] == want and r["binding"] == "reserve"):
        bad.append((ram, want, r["limit_gb"], r["binding"]))
check("the RESERVE term binds when the GPU budget is raised to all of RAM and soft is lifted: "
      "RAM - 3.5 / 4.5 / 6", not bad, bad)
r = L(128, env={"MLX_SOFT_GB": "80"})
check("a >= 64 GB Mac with MLX_SOFT_GB=80 names the override, not the 50 GB policy",
      "80" in r["note"] and "50 GB" not in r["note"], r["note"])
r = lim({"ram_gb": 64.0, "gpu_budget": {"gb": 40.0, "source": "iogpu.wired_limit_mb"}}, env={})
check("a raised-then-lowered wired limit (40 GB) binds as 'gpu' with its source in the note",
      r["binding"] == "gpu" and r["limit_gb"] == 40.0 and "iogpu.wired_limit_mb" in r["note"], r)
check("battery label edges: 2.0 light, 2.01 moderate, 7.0 moderate, 7.01 heavy",
      (bl(2.0), bl(2.01), bl(7.0), bl(7.01)) == ("light", "moderate", "moderate", "heavy"))
check("GPU budget: a Metal answer EQUAL to RAM is believed, one byte more is not",
      gb(16, 0, 16 * 2 ** 30)["source"] == "metal" and gb(16, 0, 16 * 2 ** 30 + 1)["source"] == "assumed")

# every detection command carries the module's timeouts: 3 s for sysctl-class, 5 s for system_profiler
s = FakeSub()
g = load(s, ram=64)
g["_adv_hw"]()
tmo = {}
for argv_, kw_ in zip(s.calls, s.kw):
    if len(argv_) > 1 and argv_[1] == "-I":
        continue
    tmo.setdefault(argv_[0], set()).add(kw_.get("timeout"))
check("sysctl / ioreg / pmset / sw_vers run with a 3 s timeout, system_profiler with 5 s",
      tmo.get("/usr/sbin/sysctl") == {3.0} and tmo.get("/usr/sbin/ioreg") == {3.0}
      and tmo.get("/usr/bin/pmset") == {3.0} and tmo.get("/usr/sbin/system_profiler") == {5}, tmo)

# RAM: _machine_ram_gb by name, then hw.memsize through sysctl
g = load(FakeSub())
check("no _machine_ram_gb in the namespace -> RAM read from hw.memsize (64 GiB)", g["_adv_hw"]()["ram_gb"] == 64.0)
g = load(FakeSub(), extra={"_machine_ram_gb": lambda: 1 / 0})
check("_machine_ram_gb raising -> RAM read from hw.memsize", g["_adv_hw"]()["ram_gb"] == 64.0)
g = load(FakeSub(), extra={"_machine_ram_gb": lambda: 0})
check("_machine_ram_gb answering 0 -> RAM read from hw.memsize", g["_adv_hw"]()["ram_gb"] == 64.0)
g = load(FakeSub(), ram=36)
check("_machine_ram_gb wins when it answers (36)", g["_adv_hw"]()["ram_gb"] == 36.0)
# disk: _disk_free_gb by name, else statvfs on HOME
g = load(FakeSub(), ram=64, extra={"_disk_free_gb": lambda: 412.5})
check("free disk comes from server.py's _disk_free_gb by name", g["_adv_hw"]()["disk_free_gb"] == 412.5)
g = load(FakeSub(), ram=64, extra={"_disk_free_gb": lambda: 1 / 0})
check("_disk_free_gb raising -> None, no raise", g["_adv_hw"]()["disk_free_gb"] is None)
g = load(FakeSub(), ram=64, extra={"HOME": os.getcwd()})
dfree = g["_adv_hw"]()["disk_free_gb"]
check("no _disk_free_gb -> statvfs(HOME): a positive number of GiB", isinstance(dfree, float) and dfree > 0, dfree)
g = load(FakeSub(), ram=64)
check("...and an unreadable HOME -> None", g["_adv_hw"]()["disk_free_gb"] is None)

# CPU levels: nperflevels 0 runs no perflevel command; a huge count is capped at 8 levels
s = FakeSub(facts=dict(THIS_MAC, **{"hw.nperflevels": "0"}))
load(s, ram=64)["_adv_hw"]()
check("hw.nperflevels 0 -> no `sysctl hw.perflevel*` (a bare `sysctl` would dump everything)",
      not any(any(a.startswith("hw.perflevel") for a in c) for c in s.calls)
      and ["/usr/sbin/sysctl"] not in s.calls, s.calls)
s = FakeSub(facts=dict(THIS_MAC, **{"hw.nperflevels": "20"}))
load(s, ram=64)["_adv_hw"]()
pl = [c for c in s.calls if c[0] == "/usr/sbin/sysctl" and any(a.startswith("hw.perflevel") for a in c)]
check("hw.nperflevels 20 -> at most 8 levels are queried", len(pl) == 1 and len(pl[0]) - 1 == 8, pl)

# the TTL edge: exactly 60 s is expired
s = FakeSub()
ft = FakeTime()
g = load(s, ram=64)
g["_advhw_time"] = ft
g["_adv_hw"]()
n1 = len(s.calls)
ft.t += 59.9
g["_adv_hw"]()
check("at 59.9 s nothing is re-read", len(s.calls) == n1)
ft.t += 0.1
g["_adv_hw"]()
check("at exactly 60 s the dynamic facts and the key are re-read", any(a[-1] == "batt" for a in s.calls[n1:]))

# A-series Macs and the arm64 fallback
mac_neo = json.dumps({"SPHardwareDataType": [{"machine_name": "MacBook Neo", "machine_model": "Mac17,1",
                                              "chip_type": "Apple A18 Pro"}]})
g = load(FakeSub(facts=dict(THIS_MAC, **{"machdep.cpu.brand_string": "Apple A18 Pro",
                                         "sp_hardware": mac_neo, "ioreg": ""})), ram=8)
hw = g["_adv_hw"]()
check("an A-series MacBook (A18 Pro) has no fan: form laptop_fanless, fanless True, slowest-chip fallback",
      hw["form"] == "laptop_fanless" and hw["fanless"] is True and hw["chip"]["row"] == "M1 8c"
      and hw["chip"]["confidence"] == "low", (hw["form"], hw["chip"]))
check("...an M5 Max MacBook Pro is still not fanless",
      load(FakeSub(), ram=64)["_adv_hw"]()["fanless"] is False)
facts = dict(THIS_MAC, **{"machdep.cpu.brand_string": "", "hw.optional.arm64": "0"})
hw = load(FakeSub(facts=facts), ram=16)["_adv_hw"]()
check("brand unreadable but arm64 == 0 -> unsupported, no chip, the reason sentence",
      hw["supported"] is False and hw["chip"] is None and "Apple silicon" in hw["unsupported_reason"], hw)
hw = mk("Apple M2 Ultra", 64, machine_name="Mac Studio", has_battery=True)
check("_adv_machine: an explicit has_battery wins over the machine name", hw["form"] == "laptop"
      and hw["power"]["has_battery"] is True)
hw = mk("Apple M2", 16, machine_name="MacBook Pro", has_battery=False)
check("...and has_battery=False reaches the power block", hw["power"]["has_battery"] is False)

# the Metal probe: a FAILED answer is retried, a good one never
print("   the Metal probe retry")
s = FakeSub(metal_exc=TimeoutExpired("2s"))
ft = FakeTime()
g = load(s, ram=64)
g["_advhw_time"] = ft
probes = lambda: sum(1 for a in s.calls if len(a) > 1 and a[1] == "-I")
hw = g["_adv_hw"]()
check("first call: one probe, it timed out, the budget is assumed", probes() == 1
      and hw["gpu_budget"]["source"] == "assumed")
s.metal_exc = None
ft.t += 61
g["_adv_hw"]()
ft.t += 500
hw = g["_adv_hw"]()
check("a failed probe is NOT retried inside 10 minutes (561 s later: still 1 probe, still assumed)",
      probes() == 1 and hw["gpu_budget"]["source"] == "assumed", probes())
ft.t += 39
hw = g["_adv_hw"]()
check("...but is retried once 600 s have passed, and Metal then answers (51.84 GiB)",
      probes() == 2 and hw["gpu_budget"]["source"] == "metal" and hw["gpu_budget"]["gb"] == 51.84,
      (probes(), hw["gpu_budget"]))
ft.t += 3000
g["_adv_hw"]()
check("a good answer is final: no further probes", probes() == 2)

s = FakeSub(metal_exc=TimeoutExpired("2s"))
g = load(s, ram=64)
g["_advhw_time"] = FakeTime()
g["_adv_hw"]()
s.metal_exc = None
hw = g["_adv_hw"](bust=True)
n_probe = sum(1 for a in s.calls if len(a) > 1 and a[1] == "-I")
check("...so a probe that failed at start can recover on the card's Re-check (2 probes, metal)",
      n_probe == 2 and hw["gpu_budget"]["source"] == "metal", (n_probe, hw["gpu_budget"]))
s = FakeSub(metal_exc=TimeoutExpired("2s"))
g = load(s, ram=64)
g["_advhw_time"] = FakeTime()
g["_adv_hw"](bust=True)
check("the FIRST call with bust=True probes once, not twice",
      sum(1 for a in s.calls if len(a) > 1 and a[1] == "-I") == 1)
s = FakeSub(facts=dict(THIS_MAC, **{"machdep.cpu.brand_string": "Intel(R) Core(TM) i9", "hw.optional.arm64": "0"}),
            metal_exc=TimeoutExpired("2s"))
g = load(s, ram=32)
g["_advhw_time"] = FakeTime()
g["_adv_hw"]()
g["_adv_hw"](bust=True)
check("an Intel Mac is never re-probed (nothing to learn from Metal there)",
      sum(1 for a in s.calls if len(a) > 1 and a[1] == "-I") == 1)


print("\n19. review additions: lookup rules, caps, edges the mutation sweep found unguarded")
r = cr(None, "", 12)
check("an unknown Apple chip WITH a core count: M1 8c row, the detected 12 is kept as cores_detected",
      r["row"] == "M1 8c" and r["cores_detected"] == 12 and r["cores_source"] == "detected"
      and r["gpu_cores"] == 8 and r["rule"] == "unknown-chip", r)
r = cr("M4", "Max", None)
check("unknown cores on a known variant: rule 'lower-bin', cores_source assumed", r["rule"] == "lower-bin"
      and r["cores_source"] == "assumed", r)
r = cr("M4", "Ultra", None)
check("unknown variant AND unknown cores: the BASE row's lower bin (M4 8c), rule unknown-variant, low",
      r["row"] == "M4 8c" and r["rule"] == "unknown-variant" and r["confidence"] == "low"
      and r["cores_source"] == "assumed", r)
r = cr("M4", "Ultra", 10)
check("unknown variant with a base-row core count: the base row exactly (M4 10c), but never better than low",
      r["row"] == "M4 10c" and r["rule"] == "unknown-variant" and r["confidence"] == "low"
      and r["row_source"] == "measured", r)
r = cr("M6", "Max", None)
check("a newer family with unknown cores: the M5 Max lower bin as a floor (32c), rule newer-family, low",
      r["row"] == "M5 Max 32c" and r["rule"] == "newer-family" and r["confidence"] == "low", r)
r = cr("M3", "Pro", 12)
check("a core count between rows scales per core from the nearest (M3 Pro 12c from 14c), note names the source",
      r["row"] == "M3 Pro 12c" and r["row_source"] == "scaled"
      and abs(r["p_index"] - round(CH[("M3", "Pro", 14)]["p_index"] / 14 * 12, 4)) < 1e-9
      and any("M3 Pro 14c" in n for n in r["notes"]), r)

check("ioreg core count edges: 1 and 1024 are accepted, 0 and 1025 are not",
      (pi('"gpu-core-count" = 1'), pi('"gpu-core-count" = 1024'), pi('"gpu-core-count" = 1025'),
       pi('"gpu-core-count" = 0')) == (1, 1024, None, None))
check("system_profiler core count edges: '1024' accepted, '1025' and '0' not",
      (ps(json.dumps({"SPDisplaysDataType": [{"sppci_cores": "1024"}]})),
       ps(json.dumps({"SPDisplaysDataType": [{"sppci_cores": "1025"}]})),
       ps(json.dumps({"SPDisplaysDataType": [{"sppci_cores": "0"}]}))) == (1024, None, None))
G_clean = G["_advhw_clean"]
check("OS strings are capped: 120 chars by default, 80 for system_profiler fields, 40 for a CPU level name, "
      "160 for the thermal text, battery % at 100",
      len(G_clean("x" * 500)) == 120
      and len(sh(json.dumps({"SPHardwareDataType": [{"machine_name": "x" * 200}]}))["machine_name"]) == 80
      and len(pp("hw.perflevel0.name: " + "x" * 100 + "\n")[0]["name"]) == 40
      and len(pt("Note: " + "x" * 300)["text"]) == 160
      and pbt("Now drawing from 'Battery Power'\n -InternalBattery-0\t150%; discharging")["pct"] == 100)
check("the assumed-budget note quotes its fraction: 75% from 36 GB, 66% below",
      "75%" in gb(36, 0, 0)["note"] and "66%" in gb(16, 0, 0)["note"])
check("RAM unknown (0) but Metal answered: Metal is believed (nothing to compare it with)",
      gb(0, 0, 55662788608)["source"] == "metal")
r = lim({"ram_gb": None, "gpu_budget": {}}, env={})
check("no RAM and no budget: ram_gb is None (not 0.0), limit None, the 'unknown' note",
      r["ram_gb"] is None and r["limit_gb"] is None and "unknown" in r["note"], r)
bad = []
for ram in (64, 96, 128, 192):
    n_ = L(ram)["note"]
    if "Macs with 64 GB or more" not in n_:
        bad.append((ram, n_))
check("every >= 64 GB Mac gets the policy sentence (exactly 64 included), not the generic ceiling one", not bad, bad)
check("a sub-64 GB Mac whose soft term binds gets the generic sentence",
      "admission ceiling" in L(32)["note"] and "Macs with 64 GB or more" not in L(32)["note"], L(32)["note"])

# hw.memsize read through sysctl: zero and garbage are not a RAM size
bad = []
for val in ("0", "", "garbage", "-5"):
    hw_ = load(FakeSub(facts=dict(THIS_MAC, **{"hw.memsize": val})))["_adv_hw"]()
    if hw_["ram_gb"] is not None:
        bad.append((val, hw_["ram_gb"]))
check("hw.memsize '0' / empty / garbage / negative -> ram_gb None (never a made-up size)", not bad, bad)
check("hw.memsize 17179869184 -> 16.0 GiB", load(FakeSub(facts=dict(THIS_MAC, **{"hw.memsize": "17179869184"})))["_adv_hw"]()["ram_gb"] == 16.0)

# the chip notes reach the plain-sentence list
hw = G["_adv_machine"]("Apple M4 Max", 36, machine_name="MacBook Pro")        # no core count
notes = G["_adv_hw_notes"](hw)
check("a chip row with a note (cores not detected) surfaces it: 'Chip: GPU core count not detected ...'",
      any(n.startswith("Chip: GPU core count not detected") for n in notes), notes)
check("...and a clean reference chip adds no 'Chip:' sentence",
      not any(n.startswith("Chip:") for n in G["_adv_hw_notes"](
          G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro", metal_bytes=55662788608))))
check("a thermal limit with no figure still says the Mac reports a limit",
      any("CPU speed limit right now." in n for n in G["_adv_hw_notes"](
          dict(G["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40), thermal={"limited": True, "cpu_speed_limit": None}))))

# the key is re-read at EXACTLY 60 s too
s = FakeSub()
ft = FakeTime()
g = load(s, ram=64)
g["_advhw_time"] = ft
g["_adv_hw"]()
n1 = len(s.calls)
ft.t = 1_800_000_000.0 + 59.0
g["_adv_hw"]()
check("at 59 s neither the key nor the dynamic facts are re-read", len(s.calls) == n1)
ft.t = 1_800_000_000.0 + 60.0
g["_adv_hw"]()
new = s.calls[n1:]
check("at exactly 60 s BOTH the boot-time key and the dynamic facts are re-read",
      any("kern.boottime" in a for a in new) and any(a[-1] == "batt" for a in new), new)

# system_profiler SPDisplays (the cores fallback) has its own 5 s timeout
s = FakeSub(fail={"ioreg"})
load(s, ram=64)["_adv_hw"]()
tm = [kw_.get("timeout") for a_, kw_ in zip(s.calls, s.kw) if "SPDisplaysDataType" in a_]
check("the SPDisplays fallback runs with a 5 s timeout", tm == [5], tm)


print("\n20. review additions: physical vs logical CPUs, probe field types and timing")
lv = pp("hw.perflevel0.physicalcpu: 4\nhw.perflevel0.logicalcpu: 8\nhw.perflevel0.name: Performance\n")
check("a perflevel reports its LOGICAL cpu count even when the physical count differs (4 physical, 8 logical)",
      lv == [{"level": 0, "name": "Performance", "logicalcpu": 8}], lv)
check("a non-numeric logicalcpu stays None, the level and name are kept",
      pp("hw.perflevel0.logicalcpu: many\nhw.perflevel0.name: X\n") == [{"level": 0, "name": "X", "logicalcpu": None}])


class Ticker:
    """time() advances 0.25 s per call: the probe's own elapsed time becomes 250 ms."""
    def __init__(self):
        self.t = 1_800_000_000.0

    def time(self):
        self.t += 0.25
        return self.t


g = load(FakeSub(metal=json.dumps({"ok": True, "recommendedMaxWorkingSetSize": 2 ** 33,
                                   "maxBufferLength": 2 ** 32, "hasUnifiedMemory": True, "name": "Apple M5 Max"})))
g["_advhw_time"] = Ticker()
r = g["_adv_metal_probe"]()
check("the probe reports its own wall time in ms (0.25 s -> 250) and the typed fields",
      r["ms"] == 250 and r["max_buffer"] == 2 ** 32 and r["unified"] is True and r["name"] == "Apple M5 Max", r)
for label, payload, want in (
        ("a boolean maxBufferLength is no size", {"ok": True, "recommendedMaxWorkingSetSize": 2 ** 33,
                                                 "maxBufferLength": True}, ("max_buffer", None)),
        ("a string maxBufferLength is no size", {"ok": True, "recommendedMaxWorkingSetSize": 2 ** 33,
                                                "maxBufferLength": "big"}, ("max_buffer", None)),
        ("a non-boolean hasUnifiedMemory is unknown", {"ok": True, "recommendedMaxWorkingSetSize": 2 ** 33,
                                                       "hasUnifiedMemory": 1}, ("unified", None)),
        ("a 200-character device name is capped at 80", {"ok": True, "recommendedMaxWorkingSetSize": 2 ** 33,
                                                         "name": "y" * 200}, ("name", "y" * 80))):
    r = load(FakeSub(metal=json.dumps(payload)))["_adv_metal_probe"]()
    check("probe: " + label, r.get("ok") is True and r.get(want[0]) == want[1], r)
r = load(FakeSub(metal=json.dumps({"ok": False, "error": "e" * 200})))["_adv_metal_probe"]()
check("a probe error string is capped at 80 characters", r["ok"] is False and len(r["error"]) == 80, r)
r = load(FakeSub(metal=json.dumps({"ok": False})))["_adv_metal_probe"]()
check("...and an ok:false answer with no error still says why ('probe failed')", r["error"] == "probe failed", r)
r = load(FakeSub(metal="[1, 2, 3]"))["_adv_metal_probe"]()
check("a probe that prints a JSON list is 'unparsable output', not an AttributeError",
      r["ok"] is False and r["error"] == "unparsable output", r)


print("\n21. the seam with the REAL server.py: every foreign name resolves, the module loads in its namespace")
# A child process (so this suite's os.environ / sys.modules stay clean) imports the real server.py against a
# throwaway HOME — which execs EVERY aux_*.py, this one included, in the real shared namespace and in sorted
# order — and reports what the advisor can see by name at call time.  A rename in server.py of any name the
# advisor resolves would otherwise degrade silently (a missing _mem_ceilings just drops the soft term).
import tempfile as _tempfile
SEAM_HOME = _tempfile.mkdtemp(prefix="t-advisor-seam-")
os.makedirs(os.path.join(SEAM_HOME, ".hermes", "logs"))
os.makedirs(os.path.join(SEAM_HOME, ".hermes", "dashboard"))
_L = []
for i_ in range(6):
    _t = "2026-09-01 09:%02d:00,000" % i_
    _L.append("%s - INFO - Request completed: endpoint=/chat/completions model=mlx-community/Qwen3.8-27B-4bit "
              "stream=True backend=continuous_batching prompt_tokens=900 generated_tokens=120 elapsed=2.500s "
              "prefill=900.0 tok/s decode=%.1f tok/s finish_reason=stop in_flight=0\n" % (_t, 50.0 + i_))
with io.open(os.path.join(SEAM_HOME, ".hermes", "logs", "mlx-server.log"), "w", encoding="utf-8") as f_:
    f_.write("".join(_L))
SEAM_CHILD = r"""
import importlib.util, json, os, sys
dash = sys.argv[1]
sys.path.insert(0, dash)
spec = importlib.util.spec_from_file_location("srv_advisor_seam", os.path.join(dash, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["srv_advisor_seam"] = srv
spec.loader.exec_module(srv)
g = vars(srv)
out = {"loaded": "_adv_hw" in g}
foreign = ["_mem_ceilings", "_machine_ram_gb", "_disk_free_gb", "_idle_min", "prompt_budget_profile",
           "_cx_rows", "_CX_LOG", "_CX_LOG_BG", "DISK_HEADROOM_GB"]
out["foreign"] = {n: n in g for n in foreign}
out["soft_hard_64"] = list(g["_mem_ceilings"](64.0, {}))
lim = g["_adv_limits"](g["_adv_machine"]("Apple M5 Max", 64, gpu_cores=40, metal_bytes=55662788608), env={})
out["limit64"] = [lim["limit_gb"], lim["binding"], lim["soft_gb"], lim["hard_gb"]]
hw = g["_adv_hw"]()
out["hw_ok"] = bool(hw.get("ok")) and hw.get("local") is True and "error" not in hw
out["ram_matches"] = hw.get("ram_gb") == float(g["_machine_ram_gb"]())
_d1, _d2 = hw.get("disk_free_gb"), g["_disk_free_gb"]()
out["disk_matches"] = (_d1 is None and _d2 is None) or (_d1 is not None and _d2 is not None and abs(_d1 - _d2) <= 0.5)
out["json"] = bool(json.dumps(hw))
m = g["_adv_measured"]("mlx-community/Qwen3.8-27B-4bit")
out["measured_short"] = m and m.get("decode_short")
out["measured_n"] = m and m.get("n_short")
e = g["_adv_estimate"]("mlx-community/Qwen3.8-27B-4bit", hw, {"uses": ["chat", "agent"]}, None)
out["est_basis_short"] = e["basis"]["decode_short"]
out["est_decode_short"] = e["decode_short_tps"]
out["est_measured_lanes"] = (e.get("measured") or {}).get("lanes")
out["profile"] = g["_adv_prefix"]()["profile"]
out["all_ids"] = sorted(g["_adv_estimates"](hw, {"uses": ["chat"]}))
sys.stdout.write("\n@@SEAM@@" + json.dumps(out))
"""
env_ = dict(os.environ, HOME=SEAM_HOME)
for k_ in ("MLX_SOFT_GB", "MLX_HARD_GB"):
    env_.pop(k_, None)
res = _real_subprocess.run([sys.executable, "-c", SEAM_CHILD, DASH], capture_output=True, text=True,
                           env=env_, timeout=120, cwd=SEAM_HOME)
try:
    SEAM = json.loads(res.stdout.split("@@SEAM@@")[-1])
except ValueError:
    SEAM = {}
check("server.py imported with the advisor module exec'd into its namespace (no 'failed to load' on stderr)",
      SEAM.get("loaded") is True and "aux_advisor_hw.py] failed to load" not in res.stderr,
      (res.stderr or "")[-600:])
check("every foreign name the advisor resolves exists in the real namespace: %s"
      % ", ".join(sorted((SEAM.get("foreign") or {}))),
      bool(SEAM.get("foreign")) and all((SEAM.get("foreign") or {}).values()), SEAM.get("foreign"))
check("the real _mem_ceilings reaches _adv_limits: 64 GB -> soft 50 / hard 56, plan limit 48 (soft binds)",
      SEAM.get("soft_hard_64") == [50.0, 56.0] and SEAM.get("limit64") == [48.0, "soft", 50.0, 56.0], SEAM)
check("_adv_hw() in the real namespace: ok, local, no error key, JSON-serialisable; RAM and free disk (within 0.5 GiB: it moves) "
      "are what server.py's own helpers say", SEAM.get("hw_ok") and SEAM.get("json")
      and SEAM.get("ram_matches") and SEAM.get("disk_matches"), SEAM)
check("measured-here through the REAL aux_context: this HOME's mlx-server.log gives the 27B a measured short "
      "decode (median of 50..55 = 52.5, n = 6) and the local estimate uses it",
      SEAM.get("measured_short") == 52.5 and SEAM.get("measured_n") == 6
      and SEAM.get("est_basis_short") == "measured-here" and SEAM.get("est_decode_short") == 52.5
      and SEAM.get("est_measured_lanes") == ["primary"], SEAM)
check("the prompt profile resolves through the real prompt_budget_profile ('full' on a HOME with no config)",
      SEAM.get("profile") == "full", SEAM.get("profile"))
check("every facts id is estimated on the real local Mac without a raise",
      SEAM.get("all_ids") == sorted(G["_ADV_FACTS"]), SEAM.get("all_ids"))
import shutil as _shutil
_shutil.rmtree(SEAM_HOME, True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    print("FAILED:", FAILS)
sys.exit(1 if FAILS else 0)
