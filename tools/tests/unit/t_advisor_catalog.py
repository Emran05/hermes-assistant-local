#!/usr/bin/env python3
"""Model advisor, cluster C2 — the catalog seam and the F01/B03 lockdown
(docs/plans/model-advisor-2026-10.md §6.1, §10; audit B03/F01).

Loads the REAL dashboard/server.py — and with it every aux module, the
advisor's two included — against a THROWAWAY HOME.  Nothing is downloaded
(the snapshot_download runner and the interpreter probe are recorders), no
model is woken, no port is contacted; the detection commands are answered by
a stub.

  1. _ADV_EXTRA / _ADV_BY_ID: every shape locked down and faceted; seeds and
     onboarding ids are skipped (the stock 35B-A3B and the Heretic MoE are
     seeds now); froggeric is NOT curated (D4(b)); the builder refuses
     trust_remote_code, code / pickle patterns, a missing size or backend
  2. the three server.py seams (_curated_model_ids, add_model,
     _catalog_dl_gb) consult _ADV_BY_ID by name at call time: a curated add
     needs no confirm, stores the FULL shape (backend, template_args) and
     never trust_remote_code / size_gb / verified; the download still ignores
     pickle and *.py; _catalog_dl_gb quotes it; a non-curated id still needs
     confirm:true
  3. the F01/B03 regression over EVERY curated entry (seeds + onboarding
     catalog + advisor extras): no trust_remote_code, no *.py or pickle in
     allow_patterns, the never-code / never-pickle list always appended
  4. facts coverage: the advisor ranks exactly the curated ids
  5. _model_fit (and every other pre-existing models_payload field) is
     unchanged by the advisor's rebind, row for row
  6. D12: Config-as-Code never exports the `advisor` answers
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


TMP = tempfile.mkdtemp(prefix="hermes-adv-catalog-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
import atexit  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_adv_catalog",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_adv_catalog"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME
print("server.py loaded (HOME=%s)" % TMP)


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


class FakeSub:
    """Answers the advisor's detection commands like a 64 GB M5 Max; records
    every argv; anything else answers rc=1."""
    TimeoutExpired = Exception
    PIPE, STDOUT, DEVNULL = -1, -2, -3

    def __init__(self):
        self.calls = []

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        a = " ".join(argv)
        if len(argv) > 1 and argv[1] == "-I":
            return _R(json.dumps({"ok": True, "recommendedMaxWorkingSetSize": 55662788608,
                                  "maxBufferLength": 1, "hasUnifiedMemory": True,
                                  "name": "Apple M5 Max"}))
        if "machdep.cpu.brand_string" in a:
            return _R("Apple M5 Max")
        if "hw.memsize" in a:
            return _R(str(64 * 2 ** 30))
        if "hw.optional.arm64" in a:
            return _R("1")
        if "AGXAccelerator" in a:
            return _R('"gpu-core-count" = 40')
        if "SPHardwareDataType" in a:
            return _R(json.dumps({"SPHardwareDataType": [{"machine_name": "MacBook Pro"}]}))
        if argv[0] == "/usr/bin/pmset" and argv[-1] == "batt":
            return _R("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged")
        if argv[0] == "/usr/bin/sw_vers":
            return _R("26.5.2")
        return _R("", 1)

    def Popen(self, *a, **k):
        raise AssertionError("no Popen in this harness")


FAKE = FakeSub()
srv.subprocess = FAKE
srv._RAM_GB_CACHE[0] = 64.0
srv._bg_probe = lambda: False                 # models_payload's own lane probe: "down"
srv.model_online = lambda: False

SEED_IDS = [m["id"] for m in srv._SEED_MODELS]
ONB_IDS = [m["id"] for m in srv._ONB_CATALOG]
FROG = "froggeric/Qwen3.6-35B-A3B-Uncensored-Heretic-MLX-4bit"
M35 = "mlx-community/Qwen3.6-35B-A3B-4bit"
M35H = "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit"
NEVER = list(srv._DL_NEVER_PICKLE) + list(srv._DL_NEVER_CODE)
PICKLE_OR_CODE = (".py", ".pyc", ".pyo", ".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")

# ==========================================================================
print("\n1. _ADV_EXTRA / _ADV_BY_ID")
check("the advisor module loaded into server.py's namespace",
      callable(getattr(srv, "_adv_rank", None)) and isinstance(getattr(srv, "_ADV_BY_ID", None), dict))
for m in srv._ADV_EXTRA:
    ok, why = srv._advr_extra_ok(m)
    check("_ADV_EXTRA %s passes the lockdown" % m.get("id"), ok, why)
    check("_ADV_EXTRA %s has advisor facts" % m.get("id"), m.get("id") in srv._ADV_FACTS)
check("_ADV_BY_ID never holds a seed or an onboarding id (the seed shape is the verified one)",
      not (set(srv._ADV_BY_ID) & (set(SEED_IDS) | set(ONB_IDS))), sorted(srv._ADV_BY_ID))
check("the stock 35B-A3B and the Youssofal Heretic MoE are seeds now, so curated through the seeds",
      M35 in SEED_IDS and M35H in SEED_IDS and M35 in srv._curated_model_ids()
      and M35H in srv._curated_model_ids())
check("froggeric's Heretic is NOT curated (D4(b)): not in _ADV_EXTRA, _ADV_BY_ID, the seeds or "
      "the curated set, even though it has facts",
      FROG in srv._ADV_FACTS and FROG not in srv._ADV_BY_ID
      and all(m.get("id") != FROG for m in srv._ADV_EXTRA)
      and FROG not in srv._curated_model_ids()
      and FROG not in [mid for mid, _r in srv._adv_catalog()])
seed_dup = {"id": M35, "label": "dup", "backend": "mlx_vlm", "template_args": {}, "size_gb": 1.0}
good = {"id": "advisor-test/Fresh-Model-4bit", "label": "Fresh Model", "ram": 9, "ctx": 262144,
        "note": "test", "thinking": True, "template_args": {"enable_thinking": False},
        "backend": "mlx_vlm", "allow_patterns": ["*.safetensors", "*.json", "*.jinja"],
        "size_gb": 4.2, "draft_size_gb": 0.3, "trust_remote_code": False, "verified": True,
        "uncensored": False, "license": "apache-2.0"}
bads = [dict(good, id="advisor-test/a", trust_remote_code=True),
        dict(good, id="advisor-test/b", allow_patterns=["*.safetensors", "*.py"]),
        dict(good, id="advisor-test/c", allow_patterns=["pytorch_model.bin"]),
        dict(good, id="advisor-test/d", allow_patterns=["model.pkl"]),
        {k: v for k, v in dict(good, id="advisor-test/e").items() if k != "size_gb"},
        dict(good, id="advisor-test/f", backend="llama.cpp"),
        dict(good, id="not a repo id"),
        dict(good, id="advisor-test/g", template_args="x"),
        "not even a dict"]
err = io.StringIO()
with contextlib.redirect_stderr(err):
    built = srv._advr_extra_by_id(extra=[seed_dup, good] + bads, seeds=srv._SEED_MODELS)
check("the builder skips an id that is a seed", M35 not in built, sorted(built))
check("...keeps a valid new shape, without trust_remote_code and without a `verified` claim",
      list(built) == [good["id"]] and "trust_remote_code" not in built[good["id"]]
      and "verified" not in built[good["id"]] and built[good["id"]]["backend"] == "mlx_vlm",
      built)
check("...refuses trust_remote_code, *.py / *.bin / *.pkl allow patterns, no size_gb, an unknown "
      "backend, a malformed id, bad template_args and a non-object — one stderr line each",
      err.getvalue().count("[advisor] _ADV_EXTRA entry") == len(bads), err.getvalue())
check("...and the built shape is a COPY (editing it does not edit the source)",
      built[good["id"]] is not good and built[good["id"]]["template_args"] is not good["template_args"])

# ==========================================================================
print("\n2. the server.py seams consult _ADV_BY_ID by name, at call time")
srv.read_json(srv.MODELS_FILE, None)
srv._model_registry()                              # write the seed roster once
saved_by_id, saved_facts = srv._ADV_BY_ID, dict(srv._ADV_FACTS)
TID = good["id"]
try:
    srv._ADV_BY_ID = {TID: built[TID]}
    check("_curated_model_ids() includes an _ADV_BY_ID id", TID in srv._curated_model_ids())
    check("_catalog_dl_gb() quotes it (size_gb + draft_size_gb = 4.5)", srv._catalog_dl_gb(TID) == 4.5,
          srv._catalog_dl_gb(TID))
    r = srv.add_model(TID)
    rows = {m["id"]: m for m in srv._model_registry()}
    row = rows.get(TID) or {}
    check("a curated add of it needs NO confirm", r.get("ok") is True and TID in rows, r)
    check("...and stores the FULL roster shape (backend mlx_vlm, template_args, ctx, label, note)",
          row.get("backend") == "mlx_vlm" and row.get("template_args") == {"enable_thinking": False}
          and row.get("ctx") == 262144 and row.get("label") == "Fresh Model", row)
    check("...never trust_remote_code, size_gb, draft_size_gb or verified in models.json",
          not ({"trust_remote_code", "size_gb", "draft_size_gb", "verified"} & set(row)), sorted(row))
    pats = srv._dl_ignore_patterns(row, row.get("ignore_patterns"))
    check("...and its download still ignores every pickle and *.py pattern",
          all(p in pats for p in NEVER), pats)
    # the download job itself, end to end, with the runner recorded
    jobs = []
    real_dl_run, real_hf_py = srv._dl_run, srv._hf_python

    def rec_dl_run(argv, repo_id, env=None, stall_s=None, poll_s=None):
        jobs.append(json.loads(argv[-1]))
        return 0, "", False
    srv._dl_run = rec_dl_run
    srv._hf_python = lambda: "/usr/bin/python3"
    try:
        out = srv.download_model(TID)
        t0 = time.time()
        while srv._model_dl.get(TID) == "downloading" and time.time() - t0 < 10:
            time.sleep(0.02)
    finally:
        srv._dl_run, srv._hf_python = real_dl_run, real_hf_py
    check("download_model of the curated extra scopes the pull to its allow_patterns and appends "
          "the never-code / never-pickle ignore list",
          out.get("ok") and len(jobs) == 1 and jobs[0]["repo_id"] == TID
          and jobs[0].get("allow_patterns") == good["allow_patterns"]
          and all(p in jobs[0].get("ignore_patterns", []) for p in NEVER), (out, jobs))
    r = srv.add_model("someone-else/not-curated-4bit")
    check("a NON-curated id still needs confirm:true", r.get("needs_confirm") is True and not r.get("ok"), r)
    r = srv.add_model("someone-else/not-curated-4bit", confirm="true")
    check("...and the string 'true' is not a confirm", not r.get("ok"), r)
    r = srv.add_model("someone-else/not-curated-4bit", confirm=True)
    row2 = {m["id"]: m for m in srv._model_registry()}.get("someone-else/not-curated-4bit") or {}
    check("...confirm:true adds a plain weights-only row (no backend, no trust_remote_code)",
          r.get("ok") and "backend" not in row2 and "trust_remote_code" not in row2, row2)
    check("an extra with no advisor facts is curated but never RANKED",
          TID not in [mid for mid, _r in srv._adv_catalog()])
    srv._ADV_FACTS[TID] = dict(srv._ADV_FACTS["mlx-community/Qwen3.5-9B-4bit"], label="Fresh Model")
    check("...with facts it joins the advisor's catalog after the seeds and the onboarding catalog",
          [mid for mid, _r in srv._adv_catalog()][-1] == TID)
    hw = srv._adv_machine("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro",
                          metal_bytes=55662788608)
    res = srv._adv_rank(hw, {"uses": ["chat", "agent"]}, prefix=18500, measured=False, drills={},
                        bg_wanted=True, env={})
    seen = {p["primary"]["id"] for p in [res["recommended"]] + res["alternatives"]} | \
        {w["id"] for w in res["wont"]}
    check("...and is ranked like any other catalog model (it is in the result somewhere or simply "
          "outscored, and the ranking does not break)", res["ok"] and res["recommended"], res.get("error"))
finally:
    srv._ADV_BY_ID = saved_by_id
    srv._ADV_FACTS.clear()
    srv._ADV_FACTS.update(saved_facts)
check("with _ADV_BY_ID empty again the seam is inert (the id is no longer curated)",
      TID not in srv._curated_model_ids() and srv._catalog_dl_gb(TID) is None)

# ==========================================================================
print("\n3. the F01/B03 regression over EVERY curated entry")
ENTRIES = ([("seed", m) for m in srv._SEED_MODELS] + [("onboarding", m) for m in srv._ONB_CATALOG]
           + [("_ADV_EXTRA", m) for m in srv._ADV_EXTRA]
           + [("_ADV_BY_ID", m) for m in srv._ADV_BY_ID.values()])
v_trc, v_pat, v_ign = [], [], []
for src, m in ENTRIES:
    if m.get("trust_remote_code") is True:
        v_trc.append((src, m["id"]))
    for p in m.get("allow_patterns") or []:
        if not isinstance(p, str) or p.lower().endswith(PICKLE_OR_CODE) or p.strip() in ("*", "**", "*.*"):
            v_pat.append((src, m["id"], p))
    pats = srv._dl_ignore_patterns(m, m.get("ignore_patterns"))
    if not all(p in pats for p in NEVER):
        v_ign.append((src, m["id"], pats))
check("%d curated entries: none carries trust_remote_code: true" % len(ENTRIES), not v_trc, v_trc)
check("...none names a *.py / pickle file (or a bare '*') in allow_patterns", not v_pat, v_pat)
check("...every download appends the never-code + never-pickle ignore list", not v_ign, v_ign[:2])
check("the never lists still name every pickle format and *.py",
      {"*.bin", "*.pt", "*.pth", "*.ckpt", "*.pkl", "*.pickle"} <= set(srv._DL_NEVER_PICKLE)
      and {"*.py", "*.pyc"} <= set(srv._DL_NEVER_CODE))
for mid in SEED_IDS + ONB_IDS:
    stored = {m["id"]: m for m in srv._model_registry()}.get(mid)
    if stored and stored.get("trust_remote_code") is True:
        check("models.json never stores trust_remote_code for %s" % mid, False)
        break
else:
    check("models.json (seeded) never stores trust_remote_code: true for a curated id", True)

# ==========================================================================
print("\n4. facts coverage: the advisor ranks exactly the curated ids")
curated = set(srv._curated_model_ids())
ranked = [mid for mid, _r in srv._adv_catalog()]
check("every curated id has advisor facts (seeds + onboarding catalog + extras)",
      curated <= set(srv._ADV_FACTS), sorted(curated - set(srv._ADV_FACTS)))
check("the advisor's catalog is exactly the curated set, each id once",
      set(ranked) == curated and len(ranked) == len(set(ranked)), (sorted(curated ^ set(ranked))))
check("...in a stable order: the seeds first, then the onboarding-only ids",
      ranked[:len(SEED_IDS)] == SEED_IDS
      and ranked[len(SEED_IDS):] == [i for i in ONB_IDS if i not in SEED_IDS], ranked)
check("every uncensored seed is uncensored to the advisor too (_roster_uncensored + facts)",
      all(srv._adv_is_uncensored(m["id"], m) for m in srv._SEED_MODELS if m.get("uncensored")))
check("...and a hand edit cannot relabel a curated uncensored seed as stock",
      srv._adv_is_uncensored("orcarouter/Qwen3.8-27B-Uncensored-MLX",
                             {"id": "orcarouter/Qwen3.8-27B-Uncensored-MLX", "uncensored": False}))
check("the stock seeds and onboarding models are not uncensored",
      not any(srv._adv_is_uncensored(m["id"], m) for m in list(srv._SEED_MODELS) + list(srv._ONB_CATALOG)
              if not m.get("uncensored") and not srv._ADV_FACTS.get(m["id"], {}).get("uncensored")))
# R3-seams uncensored-1: the model menu's badge (_roster_badges ->
# _roster_uncensored) read the seed and _ADV_BY_ID but not the facts, while
# every gate counts the facts — a facts-only abliterated id showed no badge.
drift = [mid for mid in sorted(srv._ADV_FACTS)
         if srv._roster_badges({"id": mid})["uncensored"] != srv._adv_is_uncensored(mid)]
check("the menu's Uncensored badge agrees with _adv_is_uncensored for EVERY id in the advisor facts",
      not drift, drift)
FACTS_ONLY = [mid for mid in sorted(srv._ADV_FACTS)
              if srv._ADV_FACTS[mid].get("uncensored") is True and srv._seed_entry(mid) is None
              and mid not in (srv._ADV_BY_ID or {}) and mid not in (srv._ONB_BY_ID or {})]
check("(the facts carry at least one uncensored id that is neither a seed nor curated)", FACTS_ONLY, FACTS_ONLY)
if FACTS_ONLY:
    fo = FACTS_ONLY[0]
    r = srv.add_model(fo, confirm=True)
    srv._widget_cache.pop("adv_advice", None)
    row = {m["id"]: m for m in srv.models_payload()["models"]}.get(fo) or {}
    gate = srv._switch_ack_refusal(fo)
    check("...added with confirm:true, its model-menu row carries uncensored: true (the badge)",
          r.get("ok") is True and row.get("uncensored") is True, (r, row.get("uncensored")))
    check("...matching the switch gate, which refuses it without the acknowledgement (409 uncensored_ack)",
          isinstance(gate, tuple) and gate[1] == 409 and gate[0].get("error") == "uncensored_ack", gate)
    srv.write_json(srv.MODELS_FILE, {"models": [m for m in srv._model_registry() if m.get("id") != fo]})

# ==========================================================================
print("\n5. _model_fit and every pre-existing models_payload field are unchanged by the rebind")
srv._widget_cache.pop("adv_advice", None)
base = srv._adv_orig_models_payload()
full = srv.models_payload()
brow = {m["id"]: m for m in base["models"]}
frow = {m["id"]: m for m in full["models"]}
diff = []
for mid, b in brow.items():
    f = frow.get(mid)
    if f is None:
        diff.append((mid, "missing"))
        continue
    for k, v in b.items():
        if f.get(k) != v:
            diff.append((mid, k, v, f.get(k)))
check("row for row, every field the base payload has is identical after the advisor's rebind",
      not diff and set(brow) == set(frow), diff[:5])
fit_bad = [(m["id"], m.get("fit"), srv._model_fit(m.get("ram"), srv._machine_ram_gb()))
           for m in full["models"] if m.get("fit") != srv._model_fit(m.get("ram"), srv._machine_ram_gb())]
check("_model_fit's verdict is the row's `fit` for every row (ok / tight / no / None)", not fit_bad, fit_bad)
check("the rebind ADDS `advice` to every row and a top-level `advisor`",
      all("advice" in m for m in full["models"]) and isinstance(full.get("advisor"), dict)
      and full["advisor"].get("ok") is True, full.get("advisor"))
check("...and the top-level keys the base had are untouched",
      all(full.get(k) == v for k, v in base.items() if k != "models"),
      [k for k, v in base.items() if k != "models" and full.get(k) != v])

# ==========================================================================
print("\n6. D12: Config-as-Code never exports the advisor answers")
check("CFG_SETTINGS_ALLOW does not list `advisor`", "advisor" not in srv.CFG_SETTINGS_ALLOW,
      srv.CFG_SETTINGS_ALLOW)
cleaned = srv._cfg_clean_settings({"advisor": {"uses": ["chat"], "uncensored_ack": True},
                                   "timezones": [["Paris", "Europe/Paris"]]})
check("_cfg_clean_settings drops `advisor` (the export path) and keeps an allowed key",
      "advisor" not in cleaned and "timezones" in cleaned, cleaned)

check("no command ran beyond the advisor's detection allowlist",
      all(c[0] in ("/usr/sbin/sysctl", "/usr/sbin/ioreg", "/usr/bin/pmset", "/usr/sbin/system_profiler",
                   "/usr/bin/sw_vers") or (len(c) > 1 and c[1] == "-I") for c in FAKE.calls),
      [c for c in FAKE.calls if c[0] not in ("/usr/sbin/sysctl", "/usr/sbin/ioreg", "/usr/bin/pmset",
                                             "/usr/sbin/system_profiler", "/usr/bin/sw_vers")][:3])

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    print("FAILED: %r" % FAILS)
sys.exit(1 if FAILS else 0)
