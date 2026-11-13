#!/usr/bin/env python3
"""Model advisor, cluster C2 — the routes, the models_payload rebind and the
onboarding seam (docs/plans/model-advisor-2026-10.md §9, §10).

Loads the REAL dashboard/server.py (every aux module, the advisor's two
included) against a THROWAWAY HOME, the t_onboarding.py pattern, with
synthetic model-server logs so "measured here" runs through aux_context's real
parser.  Then, on every advisor path:

  * urllib.request.urlopen is patched to RAISE (and record): no model, serve
    or network contact;
  * model_online / bg_online / mlx_admission / _bg_probe / agent_wake are
    recorders — the advisor path must never reach them (models_payload's own
    pre-existing bg_online call is counted separately and must not grow);
  * subprocess is a recorder that answers like this M5 Max and holds an
    ALLOWLIST: sysctl, ioreg, pmset, system_profiler, sw_vers and the `-I`
    Metal probe.  Anything else fails the suite;
  * download_model, switch_model, _activate_model and _onb_set_bg are
    recorders that must stay empty.

Covers: /api/advisor/state and /rank shapes (§9, plan + estimate objects),
the measured-here override, the POST validation matrix (uncensored without
the acknowledgement -> 400), a settings_update spy proving only the `advisor`
key changes, a validation that fails INSIDE the lock writing nothing, /rank
and /state leaving settings.json (and every other file) untouched, the
models_payload rebind (advice + advisor, _adv_hw once per build, no extra
probe, survives a broken catalog entry and a failing detection), and
_adv_recommend_compat's exact _onb_recommend key set with the fallback.
"""
import copy
import datetime
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


TMP = tempfile.mkdtemp(prefix="hermes-adv-routes-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
DATA = os.path.join(TMP, ".hermes", "dashboard")
LOGS = os.path.join(TMP, ".hermes", "logs")
os.makedirs(DATA, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
# E95 (2026-11-09 audit): the advisor counts the background lane only on file
# evidence that it can run. This suite checks the lane-ON contract (plans with
# a lane), so the owner's start token is present; t_e95_advisor_lane.py
# covers the lane that nothing started.
open(os.path.join(DATA, "model-start-ok-bg"), "w").close()
import atexit  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)

M27 = "mlx-community/Qwen3.8-27B-4bit"
M9 = "mlx-community/Qwen3.5-9B-4bit"
M2 = "mlx-community/Qwen3.5-2B-4bit"
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
UNCENSORED = {ORCA, "PocketAiHub/Qwen3.8-27B-Abliterated-MLX",
              "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit",
              "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit",
              "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"}

# synthetic model-server logs (aux_context's format) so measured-here is real
T0 = datetime.datetime(2026, 9, 1, 9, 0, 0)
SEQ = [0]


def logpair(model, prompt, cached, gen, decode, prefill_s=1.2):
    SEQ[0] += 1
    t = (T0 + datetime.timedelta(minutes=SEQ[0])).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return ("%s - INFO - Prefill completed: request=req-%d prompt_tokens=%d cached_tokens=%d "
            "elapsed=%.3fs rate=900.0 tok/s\n" % (t, SEQ[0], prompt, cached, prefill_s) +
            "%s - INFO - Request completed: endpoint=/chat/completions model=%s stream=True "
            "backend=continuous_batching prompt_tokens=%d generated_tokens=%d elapsed=2.500s "
            "prefill=900.0 tok/s decode=%.1f tok/s finish_reason=stop in_flight=0\n"
            % (t, model, prompt, gen, decode))


lines = [logpair(M27, 600 + 300 * i, 0, 120, d) for i, d in enumerate([51.0, 52.5, 53.2, 54.0, 55.9])]
lines += [logpair(M27, 18000 + 2500 * i, 17500 + 2500 * i, 200, d)
          for i, d in enumerate([33.0, 36.7, 35.1, 34.0, 38.2])]
lines += [logpair(M27, p, 0, 30, 40.0, prefill_s=s)
          for p, s in [(20468, 26.1), (20470, 25.8), (20480, 27.0), (20500, 26.4), (19000, 24.0)]]
with open(os.path.join(LOGS, "mlx-server.log"), "w") as f:
    f.write("".join(lines))

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
sys.path.insert(0, _HERE)
spec = importlib.util.spec_from_file_location("hermes_server_adv_routes",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_adv_routes"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME
print("server.py loaded (HOME=%s)" % TMP)

# --------------------------------------------------------------------------
# the fences
# --------------------------------------------------------------------------
ALLOWED = ("/usr/sbin/sysctl", "/usr/sbin/ioreg", "/usr/bin/pmset", "/usr/sbin/system_profiler",
           "/usr/bin/sw_vers")
THIS_MAC = {
    "machdep.cpu.brand_string": "Apple M5 Max", "hw.optional.arm64": "1",
    "sysctl.proc_translated": "0", "hw.model": "Mac17,6", "hw.memsize": str(64 * 2 ** 30),
    "hw.nperflevels": "2", "iogpu.wired_limit_mb": "0",
    "kern.boottime": "{ sec = 1789568983, usec = 731292 } Wed Sep 16 10:29:43 2026",
    "kern.memorystatus_level": "71",
    "vm.swapusage": "total = 3072.00M  used = 1432.38M  free = 1639.62M  (encrypted)",
}


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


class FenceSub:
    TimeoutExpired = Exception
    PIPE, STDOUT, DEVNULL = -1, -2, -3

    def __init__(self):
        self.calls, self.violations = [], []

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        probe = len(argv) > 2 and argv[1] == "-I" and argv[2] == "-c"
        if not probe and argv[0] not in ALLOWED:
            self.violations.append(argv)
            return _R("", 1)
        if probe:
            return _R(json.dumps({"ok": True, "recommendedMaxWorkingSetSize": 55662788608,
                                  "maxBufferLength": 41747087360, "hasUnifiedMemory": True,
                                  "name": "Apple M5 Max"}))
        if argv[0] == "/usr/sbin/sysctl":
            if len(argv) >= 3 and argv[1] == "-n":
                return _R(THIS_MAC.get(argv[2], ""), 0 if argv[2] in THIS_MAC else 1)
            return _R("hw.perflevel0.logicalcpu: 6\nhw.perflevel0.name: Super\n"
                      "hw.perflevel1.logicalcpu: 12\nhw.perflevel1.name: Performance\n")
        if argv[0] == "/usr/sbin/ioreg":
            return _R('"gpu-core-count" = 40')
        if argv[0] == "/usr/sbin/system_profiler":
            return _R(json.dumps({"SPHardwareDataType": [{
                "machine_name": "MacBook Pro", "machine_model": "Mac17,6",
                "serial_number": "C02SERIALSECRET", "platform_UUID": "UUID-SECRET-1234"}]}))
        if argv[0] == "/usr/bin/sw_vers":
            return _R("26.5.2")
        if argv[0] == "/usr/bin/pmset":
            if argv[-1] == "batt":
                return _R("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t96%; charged")
            if argv[-1] == "therm":
                return _R("Note: No thermal warning level has been recorded\n")
            return _R(" powermode            2\n")
        return _R("", 1)

    def Popen(self, *a, **k):
        self.violations.append(["Popen"] + [str(x) for x in (a[0] if a else [])])
        raise AssertionError("no Popen on the advisor path")


FENCE = FenceSub()
srv.subprocess = FENCE
srv._RAM_GB_CACHE[0] = None
URLOPEN = []


def no_urlopen(*a, **k):
    URLOPEN.append(a[:1])
    raise OSError("t_advisor_routes: urlopen is not allowed on the advisor path")


urllib.request.urlopen = no_urlopen
PROBES = []


def probe_rec(name, ret):
    def f(*a, **k):
        PROBES.append(name)
        return ret
    return f


for nm, ret in (("model_online", False), ("bg_online", False), ("_bg_probe", False),
                ("mlx_admission", (True, 0.0, 50.0)), ("agent_wake", None)):
    setattr(srv, nm, probe_rec(nm, ret))
ACTIONS = []


def act_rec(name):
    def f(*a, **k):
        ACTIONS.append((name, a))
        return {"ok": False, "error": "recorder"}
    return f


for nm in ("download_model", "switch_model", "_activate_model", "_onb_set_bg"):
    setattr(srv, nm, act_rec(nm))
Ctx = srv.RouteCtx
GET, POST = srv.GET_ROUTES, srv.POST_ROUTES
SETTINGS = srv.SETTINGS_FILE


def get(path, **q):
    r = GET[path](Ctx(query={k: [v] for k, v in q.items()}, raw_path=path))
    return (r[0], r[1]) if isinstance(r, tuple) else (r, 200)


def post(path, body):
    r = POST[path](Ctx(body=body, raw_path=path))
    return (r[0], r[1]) if isinstance(r, tuple) else (r, 200)


def snapshot_files():
    out = {}
    for root, _dirs, files in os.walk(os.path.join(TMP, ".hermes")):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
                out[p] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return out


# ==========================================================================
print("\n1. the routes exist; nothing ran at import")
check("GET /api/advisor/state, GET /api/advisor/rank, POST /api/advisor/answers are registered",
      "/api/advisor/state" in GET and "/api/advisor/rank" in GET and "/api/advisor/answers" in POST)
check("the models_payload rebind is installed once (guarded)", srv._adv_wrapped is True
      and srv.models_payload is not srv._adv_orig_models_payload)

with open(SETTINGS, "w") as f:
    json.dump({"theme": "dark", "weather_city": "Paris", "claude_escalation": {"enabled": False},
               "advisor": {"uses": ["chat", "agent"], "priority": "balanced"}}, f)
srv._model_registry()                       # the roster bootstrap, BEFORE the no-write snapshot
before = snapshot_files()
s_mtime = os.stat(SETTINGS).st_mtime_ns

# ==========================================================================
print("\n2. GET /api/advisor/state")
st, code = get("/api/advisor/state")
check("200 and ok", code == 200 and st.get("ok") is True, (code, str(st)[:300]))
for k in ("version", "hw", "limits", "prefix", "answers", "current", "result", "models",
          "formulas", "constants", "questions", "estimate"):
    check("state carries `%s`" % k, k in st)
hw = st["hw"]
for k in ("chip", "ram_gb", "cpu", "macos", "model_id", "machine_name", "form", "power", "thermal",
          "disk_free_gb", "supported", "unsupported_reason", "gpu_budget"):
    check("hw carries `%s` (§9)" % k, k in hw)
for k in ("brand", "family", "variant", "gen", "gpu_cores", "cores_source", "bandwidth_gbs",
          "e_chip", "p_index", "row", "confidence"):
    check("hw.chip carries `%s`" % k, k in (hw.get("chip") or {}))
check("hw reads this (stubbed) Mac: M5 Max 40c, 64 GB, Metal budget 51.84 GB, MacBook Pro on AC",
      hw["chip"]["row"] == "M5 Max 40c" and hw["ram_gb"] == 64.0
      and hw["gpu_budget"]["source"] == "metal" and hw["gpu_budget"]["gb"] == 51.84
      and hw["machine_name"] == "MacBook Pro" and hw["power"]["source"] == "ac", hw)
blob = json.dumps(st)
check("no serial number, UUID or UDID ever reaches the payload",
      "SERIALSECRET" not in blob and "UUID-SECRET" not in blob)
for k in ("gpu_budget_gb", "gpu_budget_source", "soft_gb", "hard_gb", "reserve_gb", "limit_gb", "binding"):
    check("limits carries `%s`" % k, k in st["limits"])
check("limits on this Mac: 48 GB, soft binds", st["limits"]["limit_gb"] == 48.0
      and st["limits"]["binding"] == "soft", st["limits"])
check("prefix {tokens, source, profile}", set(st["prefix"] or {}) >= {"tokens", "source", "profile"},
      st["prefix"])
res = st["result"]
for k in ("recommended", "alternatives", "wont", "notes", "relaxed", "current", "limits", "answers"):
    check("result carries `%s`" % k, k in res)
rec = res["recommended"]
PLAN_KEYS = ("primary", "background", "verdict", "total_gb", "limit_gb", "score", "why", "notes",
             "download_gb", "disk_ok", "downloaded", "verified", "uncensored", "estimates")
check("the plan object has every §9 key", all(k in rec for k in PLAN_KEYS),
      [k for k in PLAN_KEYS if k not in rec])
check("...primary/background are {id, label, ...}", {"id", "label"} <= set(rec["primary"])
      and {"id", "label"} <= set(rec["background"] or {}), rec["primary"])
EST_KEYS = ("resident_gb", "decode_short_tps", "decode_turn_tps", "turn_ctx", "prefill_s",
            "prefix_tokens", "battery", "estimate", "basis", "range", "measured", "formula")
e27 = st["models"].get(M27) or {}
check("the estimate object has every §9 key", all(k in e27 for k in EST_KEYS),
      [k for k in EST_KEYS if k not in e27])
check("this Mac: 27B + 9B recommended (today's roster), verified, fits",
      rec["primary"]["id"] == M27 and rec["background"]["id"] == M9 and rec["verified"]
      and rec["verdict"] == "fits", (rec["primary"]["id"], (rec["background"] or {}).get("id")))
check("measured here: the 27B's decode and prefill come from this Mac's logs (aux_context parser)",
      e27.get("basis", {}).get("decode_short") == "measured-here"
      and e27["basis"].get("decode") == "measured-here" and e27["basis"].get("prefill") == "measured-here"
      and e27["decode_short_tps"] == 53.2 and e27["prefix_source"] == "measured", e27.get("basis"))
check("...and the Recommended plan's primary estimate is that same measured one",
      rec["estimates"]["primary"]["basis"]["decode"] == "measured-here")
cur = st["current"]
check("current: the active model (DEFAULT, 27B) + the background lane (9B) is evaluated",
      cur["active"] == M27 and cur["background"] == M9 and cur["plan"]["kind"] == "current"
      and cur["plan"]["verdict"] == "fits", cur)
check("questions: the six uses with labels, Q3 asked on a laptop, the acknowledgement copy",
      [u["key"] for u in st["questions"]["uses"]] == ["chat", "agent", "code", "docs", "vision", "uncensored"]
      and st["questions"]["power_asked"] is True
      and "approval gate" in st["questions"]["uncensored_ack_text"])
check("formulas and constants merge the estimator's and the ranking's",
      "decode" in st["formulas"] and "score" in st["formulas"]
      and "reference_chip" in st["constants"] and "usable_tps" in st["constants"])
check("every estimate in `models` is labelled estimate:true", all(e.get("estimate") is True
                                                                   for e in st["models"].values()))
check("uncensored ids are never in the answer unless asked for", not (
      {p["primary"]["id"] for p in [rec] + res["alternatives"]} | {w["id"] for w in res["wont"]})
      & UNCENSORED)

# ==========================================================================
print("\n3. GET /api/advisor/rank (unsaved answers, no writes)")
r, code = get("/api/advisor/rank")
check("no query -> the stored answers' ranking (same Recommended as /state)",
      code == 200 and r["ok"] and r["saved"] is False
      and r["result"]["recommended"]["primary"]["id"] == M27, (code, str(r)[:200]))
r, code = get("/api/advisor/rank", uses="chat,agent,code", priority="best", power="battery")
check("uses/priority/power from the query are ranked (code -> a 40K turn)",
      code == 200 and r["answers"]["uses"] == ["chat", "agent", "code"]
      and r["answers"]["priority"] == "best" and r["answers"]["power"] == "battery"
      and r["result"]["recommended"]["summary"]["turn_ctx"] == 40000, r.get("answers"))
for q, why in (({"uses": "chat,bogus"}, "an unknown use"), ({"uses": ""}, "an empty uses list"),
               ({"uses": "uncensored"}, "uncensored alone"), ({"priority": "fast"}, "a bad priority"),
               ({"power": "solar"}, "a bad power")):
    r, code = get("/api/advisor/rank", **q)
    check("rank with %s -> 400 {ok:false, errors[]}" % why,
          code == 400 and r.get("ok") is False and r.get("errors"), (code, r))
r, code = get("/api/advisor/rank", uses="chat,agent,uncensored")
seen = {p["primary"]["id"] for p in [r["result"]["recommended"]] + r["result"]["alternatives"]}
check("uncensored WITHOUT the acknowledgement: ranked without the uncensored rows, and it says so",
      code == 200 and not seen & UNCENSORED
      and any("acknowledgement" in n for n in r["result"]["notes"]), (code, r["result"]["notes"]))
check("...and says needs_ack: true (the UI stores the acknowledgement, then re-ranks)",
      r.get("needs_ack") is True and r["answers"]["uncensored_ack"] is False, r.get("needs_ack"))
r, code = get("/api/advisor/rank", uses="chat,agent,uncensored", unc="1", uncensored_ack="true")
seen = {p["primary"]["id"] for p in [r["result"]["recommended"]] + r["result"]["alternatives"]}
check("...an unsaved ranking CANNOT assert the acknowledgement (unc=1, uncensored_ack=true in the "
      "query are ignored): still no uncensored row, still needs_ack",
      code == 200 and not seen & UNCENSORED and r.get("needs_ack") is True, (code, sorted(seen)))
st2, _c = get("/api/advisor/state", bust="1")
check("/state?bust=1 re-checks (still ok)", st2.get("ok") is True)
after = snapshot_files()
check("/state and /rank left settings.json's mtime unchanged", os.stat(SETTINGS).st_mtime_ns == s_mtime)
check("...and wrote NO file at all under ~/.hermes",
      before == after, sorted(set(before.items()) ^ set(after.items()))[:4])

# ==========================================================================
print("\n4. POST /api/advisor/answers — the validation matrix")
BAD = [({"uses": "chat"}, "uses not a list"), ({"uses": []}, "uses empty"),
       ({"uses": ["chat", "bogus"]}, "an unknown use"), ({"uses": [1]}, "a non-string use"),
       ({"uses": ["uncensored"]}, "uncensored alone"),
       ({"uses": ["chat", "uncensored"]}, "uncensored without the acknowledgement"),
       ({"uses": ["chat", "uncensored"], "uncensored_ack": False}, "uncensored with ack false"),
       ({"uses": ["chat", "uncensored"], "uncensored_ack": "true"}, "ack the string 'true'"),
       ({"uncensored_ack": 1}, "ack the number 1"), ({"priority": "fast"}, "an unknown priority"),
       ({"priority": None}, "priority null"), ({"power": "solar"}, "an unknown power"),
       ({"power": True}, "power a boolean")]
for body, why in BAD:
    m0 = os.stat(SETTINGS).st_mtime_ns
    r, code = post("/api/advisor/answers", body)
    check("POST %s -> 400 {ok:false, errors[]} and nothing written" % why,
          code == 400 and r.get("ok") is False and r.get("errors")
          and os.stat(SETTINGS).st_mtime_ns == m0, (code, r))
r, code = srv._adv_answers_save(["not", "an", "object"])
check("a non-object body -> 400", code == 400 and r.get("ok") is False)
CALLS = []
real_su = srv.settings_update


def spy(fn):
    CALLS.append(fn)
    return real_su(fn)


srv.settings_update = spy
try:
    with open(SETTINGS) as f:
        s_before = json.load(f)
    srv._widget_cache["adv_advice"] = (time.time(), {"rows": {}, "advisor": {}})
    r, code = post("/api/advisor/answers", {"uses": ["chat", "code", "agent", "chat"],
                                            "priority": "best", "power": None,
                                            "uncensored_ack": False, "extra": 1})
    with open(SETTINGS) as f:
        s_after = json.load(f)
    check("a valid POST -> 200 {ok, answers} (uses deduplicated, in the questionnaire's order)",
          code == 200 and r["ok"] and r["answers"]["uses"] == ["chat", "agent", "code"]
          and r["answers"]["priority"] == "best" and isinstance(r["answers"]["saved_at"], int),
          (code, r))
    check("...unknown keys are ignored and reported", r.get("ignored") == ["extra"], r)
    check("...through settings_update, exactly once", len(CALLS) == 1)
    check("...and ONLY the `advisor` key changed (theme, weather, claude_escalation untouched)",
          {k: v for k, v in s_before.items() if k != "advisor"}
          == {k: v for k, v in s_after.items() if k != "advisor"}
          and s_after["advisor"] == r["answers"], (s_before, s_after))
    check("...and the model menu's cached advice was dropped (re-ranked on the next poll)",
          "adv_advice" not in srv._widget_cache)
    r, code = post("/api/advisor/answers", {"uncensored_ack": True})
    with open(SETTINGS) as f:
        s3 = json.load(f)
    check("MERGE: posting the acknowledgement alone keeps the stored uses/priority",
          code == 200 and s3["advisor"]["uses"] == ["chat", "agent", "code"]
          and s3["advisor"]["priority"] == "best" and s3["advisor"]["uncensored_ack"] is True, s3)
    r, code = post("/api/advisor/answers", {"uses": ["chat", "agent", "uncensored"]})
    check("...after which `uncensored` may be among the uses", code == 200
          and r["answers"]["uses"] == ["chat", "agent", "uncensored"], (code, r))
    r, code = get("/api/advisor/rank")
    alts = r["result"]["alternatives"]
    check("...and with the acknowledgement STORED the ranking shows the uncensored alternative "
          "(one, never Recommended), needs_ack false",
          code == 200 and sum(1 for a in alts if a["uncensored"]) == 1
          and not r["result"]["recommended"]["uncensored"] and r.get("needs_ack") is False,
          [(a["primary"]["id"], a["uncensored"]) for a in alts])
    r, code = post("/api/advisor/answers", {"uses": ["chat", "agent"]})
    check("dropping the use keeps the acknowledgement", code == 200
          and r["answers"]["uncensored_ack"] is True, r)
    post("/api/advisor/answers", {"uses": ["chat", "agent", "uncensored"]})
    r, code = post("/api/advisor/answers", {"uses": ["chat", "agent", "uncensored"],
                                            "uncensored_ack": False})
    check("an explicit contradiction (`uncensored` in uses AND the acknowledgement false in one "
          "body) is a 400", code == 400, (code, r))
    r, code = post("/api/advisor/answers", {"uncensored_ack": False})
    with open(SETTINGS) as f:
        s4 = json.load(f)
    check("REVOKING the acknowledgement alone always works (the safe direction): 200, the "
          "stored `uncensored` use goes with it, the other answers stay",
          code == 200 and r["answers"]["uncensored_ack"] is False
          and r["answers"]["uses"] == ["chat", "agent"] and r["answers"]["priority"] == "best"
          and s4["advisor"]["uncensored_ack"] is False and "uncensored" not in s4["advisor"]["uses"],
          (code, r, s4.get("advisor")))
    r, code = get("/api/advisor/rank", uses="chat,agent,uncensored")
    check("...and the ranking is back to needs_ack with no uncensored row",
          code == 200 and r.get("needs_ack") is True
          and not any(a["uncensored"] for a in r["result"]["alternatives"]), (code, r.get("needs_ack")))
    post("/api/advisor/answers", {"uses": ["chat", "agent"], "uncensored_ack": True})
    r, code = post("/api/advisor/answers", {"uncensored_ack": False})
    check("revoking when `uncensored` was never among the uses is a plain 200",
          code == 200 and r["answers"]["uses"] == ["chat", "agent"], (code, r))
    # a body that is valid against the PRE-lock read but not against the file
    # as it is inside the lock: settings_update must write nothing.  The stubbed
    # pre-lock read claims the acknowledgement is stored; the file (revoked just
    # above) says otherwise, so `uses: [.., uncensored]` fails only INSIDE the lock.
    real_raw = srv._advr_raw_stored
    srv._advr_raw_stored = lambda: {"uses": ["chat", "agent"], "uncensored_ack": True}
    try:
        post("/api/advisor/answers", {"uncensored_ack": False})
        m0 = os.stat(SETTINGS).st_mtime_ns
        with open(SETTINGS) as f:
            c0 = f.read()
        n0 = len(CALLS)
        r, code = post("/api/advisor/answers", {"uses": ["chat", "agent", "uncensored"]})
        with open(SETTINGS) as f:
            c1 = f.read()
        check("a body that fails INSIDE the lock (the stored acknowledgement moved) -> 400 and the "
              "file is byte-identical (the mutate fn raised, settings_update wrote nothing)",
              code == 400 and len(CALLS) == n0 + 1 and c0 == c1
              and os.stat(SETTINGS).st_mtime_ns == m0, (code, r))
    finally:
        srv._advr_raw_stored = real_raw
    r, code = post("/api/advisor/answers", {"uses": ["chat", "agent"], "uncensored_ack": False,
                                            "priority": "balanced"})
    check("back to the defaults", code == 200)
finally:
    srv.settings_update = real_su
check("_adv_answers_stored() reads a corrupt advisor value as the defaults (never raises)",
      srv._advr_clean_answers({"uses": "x", "priority": 5, "power": "solar",
                               "uncensored_ack": "yes"})
      == {"uses": ["chat", "agent"], "priority": "balanced", "power": None, "uncensored_ack": False,
          "saved_at": None})
_sa_bad = []
for _v in (10 ** 400, 2 ** 70, float("inf"), float("nan"), -5, 0, True, "x", [1], {"a": 1}, 1e11):
    try:
        if srv._advr_clean_answers({"saved_at": _v})["saved_at"] is not None:
            _sa_bad.append(repr(_v)[:24])
    except Exception as _e:                      # noqa: BLE001
        _sa_bad.append("%s raised %s" % (repr(_v)[:24], type(_e).__name__))
check("...a corrupt saved_at (a 400-digit int, inf, nan, negative, a bool, text) reads as None and "
      "never raises", not _sa_bad, _sa_bad)
check("...while a real unix time survives",
      srv._advr_clean_answers({"saved_at": 1798000000})["saved_at"] == 1798000000
      and srv._advr_clean_answers({"saved_at": 1798000000.9})["saved_at"] == 1798000000)
check("...and a stored `uncensored` without the acknowledgement is dropped (fail closed)",
      srv._advr_clean_answers({"uses": ["chat", "uncensored"], "uncensored_ack": False})["uses"]
      == ["chat"])

# ==========================================================================
print("\n5. the models_payload() rebind")
HW_CALLS = []
real_hw = srv._adv_hw


def hw_counter(bust=False):
    HW_CALLS.append(bust)
    return real_hw(bust=bust)


srv._adv_hw = hw_counter
srv._widget_cache.pop("adv_advice", None)
del PROBES[:]
base = srv._adv_orig_models_payload()
base_probes = list(PROBES)
del PROBES[:]
pay = srv.models_payload()
check("_adv_hw() runs ONCE per advice build", HW_CALLS == [False], HW_CALLS)
check("the rebind adds NO probe: only the base payload's own bg_online, as before",
      sorted(PROBES) == sorted(base_probes) and set(PROBES) <= {"bg_online", "_bg_probe"},
      (PROBES, base_probes))
pay2 = srv.models_payload()
check("...and a second poll inside 30 s reuses the cached advice (no detection at all)",
      HW_CALLS == [False])
rows = {m["id"]: m for m in pay["models"]}
a27 = rows[M27].get("advice") or {}
for k in ("resident_gb", "decode_short_tps", "decode_turn_tps", "prefill_s", "verdict_solo",
          "basis", "why"):
    check("advice carries `%s` (§9)" % k, k in a27)
check("the 27B row: recommended for you with the 9B lane, measured here",
      a27.get("why") == "recommended for you with the 9B lane" and a27.get("recommended") is True
      and a27["basis"]["decode"] == "measured-here" and a27["verdict_solo"] == "fits", a27)
check("an uncensored row is labelled as never recommended",
      "never recommended" in ((rows[ORCA].get("advice") or {}).get("why") or ""), rows[ORCA].get("advice"))
check("the top-level advisor block: {limit_gb, gpu_budget_gb, current{total_gb, verdict}}",
      pay["advisor"]["limit_gb"] == 48.0 and pay["advisor"]["gpu_budget_gb"] == 51.84
      and pay["advisor"]["current"]["verdict"] == "fits"
      and isinstance(pay["advisor"]["current"]["total_gb"], float), pay["advisor"])
check("`fit` is still _model_fit's (unchanged)",
      all(m.get("fit") == srv._model_fit(m.get("ram"), srv._machine_ram_gb()) for m in pay["models"]))
# a broken catalog entry and an odd roster row
srv._widget_cache.pop("adv_advice", None)
saved = copy.deepcopy(srv._ADV_FACTS[M2])
srv._ADV_FACTS[M2]["weights_bytes"] = "garbage"
with open(srv.MODELS_FILE) as f:
    reg = json.load(f)
reg["models"].append({"id": "someone/odd-row", "label": "Odd"})
with open(srv.MODELS_FILE, "w") as f:
    json.dump(reg, f)
try:
    import io as _io
    import contextlib as _cl
    err = _io.StringIO()
    with _cl.redirect_stderr(err):
        pay = srv.models_payload()
    rows = {m["id"]: m for m in pay["models"]}
    check("a BROKEN facts entry: the payload still answers, that row's advice is None, the rest intact",
          rows.get(M2, {}).get("advice") is None and rows[M27].get("advice")
          and pay["advisor"]["ok"] is True, (rows.get(M2, {}).get("advice"), pay["advisor"]))
    check("...a roster row the advisor has no facts for gets advice None",
          "someone/odd-row" in rows and rows["someone/odd-row"]["advice"] is None)
finally:
    srv._ADV_FACTS[M2] = saved
srv._widget_cache.pop("adv_advice", None)
srv._adv_hw = lambda bust=False: (_ for _ in ()).throw(RuntimeError("detection exploded"))
try:
    pay = srv.models_payload()
    check("a FAILING detection: the menu still answers, advisor {ok:false}, every advice None",
          pay["advisor"] == {"ok": False, "error": "RuntimeError"}
          and all(m.get("advice") is None for m in pay["models"]) and pay["models"], pay.get("advisor"))
finally:
    srv._adv_hw = hw_counter
srv._widget_cache.pop("adv_advice", None)

# ==========================================================================
print("\n6. _adv_recommend_compat: _onb_recommend's exact key set (+ advisor)")
legacy = srv._onb_recommend(64, [])
compat = srv._adv_recommend_compat(64, [])
check("the key set is exactly _onb_recommend's plus `advisor`",
      set(compat) == set(legacy) | {"advisor"}, sorted(set(compat) ^ (set(legacy) | {"advisor"})))
check("tier 'advisor', 27B + 9B on this Mac, labels and sizes filled",
      compat["tier"] == "advisor" and compat["primary"] == M27 and compat["background"] == M9
      and compat["primary_label"] == "Qwen3.8-27B" and compat["background_label"] == "Qwen3.5-9B"
      and compat["download_gb"] == 23.0 and compat["total_gb"] == 23.0
      and compat["already_downloaded"] is False and compat["tight"] is False, compat)
check("fits is _onb_fits's list (the per-model badges stay _model_fit)",
      compat["fits"] == srv._onb_fits(64, []))
check("alt is {primary, background, label, why} and never uncensored",
      compat["alt"] is None or (set(compat["alt"]) == {"primary", "background", "label", "why"}
                                and compat["alt"]["primary"] not in UNCENSORED), compat["alt"])
check("advisor {ok:true, verified, result}", compat["advisor"]["ok"] is True
      and compat["advisor"]["verified"] is True and compat["advisor"]["result"]["ok"])
c2 = srv._adv_recommend_compat(64, [M27, "mlx-community/Qwen3.8-27B-MTP-bf16", M9])
check("with both downloaded: download_gb 0, already_downloaded true",
      c2["download_gb"] == 0.0 and c2["already_downloaded"] is True, c2["download_gb"])
c3 = srv._adv_recommend_compat(64, [], answers={"uses": ["chat", "agent", "uncensored"],
                                                "uncensored_ack": True, "priority": "balanced"})
check("acknowledged uncensored answers: still a stock primary, and no uncensored alt",
      c3["primary"] not in UNCENSORED and (c3["alt"] is None or c3["alt"]["primary"] not in UNCENSORED), c3)
intel = srv._adv_machine("Intel(R) Core(TM) i7-8850H CPU @ 2.60GHz", 16, machine_name="MacBook Pro")
c4 = srv._adv_recommend_compat(16, [], hw=intel)
l4 = srv._onb_recommend(16, [])
check("an Intel Mac: the RAM-only tier answers (fallback), same keys, advisor.ok false",
      {k: v for k, v in c4.items() if k != "advisor"} == l4 and c4["advisor"]["ok"] is False
      and c4["advisor"]["fallback"] == "_onb_tier", c4.get("advisor"))
real_rank = srv._adv_rank_local
srv._adv_rank_local = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    c5 = srv._adv_recommend_compat(64, [])
finally:
    srv._adv_rank_local = real_rank
check("the advisor raising: the RAM-only tier answers (never raises)",
      {k: v for k, v in c5.items() if k != "advisor"} == legacy and c5["advisor"]["ok"] is False)
c6 = srv._adv_recommend_compat(16, [])
check("ram_gb that differs from the detected RAM re-ranks for that size (16 GB -> no lane)",
      c6["tier"] == "advisor" and c6["background"] is None and set(c6) == set(legacy) | {"advisor"}, c6)

# ==========================================================================
print("\n7. HTTP end to end through the real Handler (ephemeral loopback port)")
import _http_harness as H  # noqa: E402
server = H.Server(srv.Handler)
try:
    stc, _h, b = H.req(server.port, "GET", "/api/advisor/rank?uses=chat,bogus", None)
    j = H.as_json(b) or {}
    check("GET /api/advisor/rank?uses=chat,bogus -> 400 JSON", stc == 400 and j.get("ok") is False
          and j.get("errors"), (stc, b[:200]))
    stc, _h, b = H.req(server.port, "POST", "/api/advisor/answers",
                       json.dumps({"uses": "chat"}).encode(), headers={"Content-Type": "application/json"})
    check("POST /api/advisor/answers {uses: 'chat'} -> 400 JSON",
          stc == 400 and (H.as_json(b) or {}).get("errors"), (stc, b[:200]))
    stc, _h, b = H.req(server.port, "GET", "/api/advisor/state", None)
    check("GET /api/advisor/state -> 200 JSON", stc == 200 and (H.as_json(b) or {}).get("ok") is True,
          (stc, b[:200]))
    stc, _h, b = H.req(server.port, "GET", "/api/advisor/rank", None,
                       headers={"Origin": "http://evil.example"})
    check("the same-origin guard still runs first (a cross Origin -> 403)", stc == 403, stc)
finally:
    server.close()

# ==========================================================================
print("\n8. the fences held for the whole run")
check("urllib.request.urlopen was never called on an advisor path", not URLOPEN, URLOPEN)
check("no command outside the allowlist (sysctl, ioreg, pmset, system_profiler, sw_vers, the -I "
      "probe)", not FENCE.violations, FENCE.violations[:3])
check("the detection actually ran through the fence (the allowlist is not vacuous)",
      any(c[0] == "/usr/sbin/sysctl" for c in FENCE.calls)
      and any(len(c) > 1 and c[1] == "-I" for c in FENCE.calls))
check("download_model, switch_model, _activate_model and _onb_set_bg were never called",
      not ACTIONS, ACTIONS)
check("model_online / mlx_admission / agent_wake never ran (bg_online only for the base payload)",
      not [p for p in PROBES if p not in ("bg_online", "_bg_probe")], PROBES)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    print("FAILED: %r" % FAILS)
sys.exit(1 if FAILS else 0)
