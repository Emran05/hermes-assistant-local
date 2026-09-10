#!/usr/bin/env python3
"""Backend harness for aux_onboarding.py (release 1.1.1).

Loads dashboard/server.py against a THROWAWAY HOME (so nothing touches the real
~/.hermes), stubs subprocess for sysctl/sw_vers/pmset, and exercises:
  * _onb_tier for 8/16/24/36/48/64/128 GB
  * /api/onboarding/state shape + done flip
  * /api/onboarding/apply validation (every field)
  * download refusal on low disk
  * done / reset
  * SECRETS: the .env token value never appears in any response

Never wakes or downloads a model: download_model is replaced by a recorder.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


# --------------------------------------------------------------------------
# throwaway HOME
# --------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="hermes-onb-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)

SECRET = "1234567890:AAH-THIS-IS-THE-SECRET-TOKEN-VALUE-xyz"
with open(os.path.join(TMP, ".hermes", ".env"), "w") as f:
    f.write("# hermes env\n")
    f.write("TELEGRAM_BOT_TOKEN=%s\n" % SECRET)
    f.write("TELEGRAM_ALLOWED_USERS=12345\n")

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

# --------------------------------------------------------------------------
# subprocess stub — sysctl / sw_vers / pmset answer from RAM_STATE
# --------------------------------------------------------------------------
RAM_STATE = {"gb": 64, "laptop": True}


class _R:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


class FakeSubprocess:
    """Only the surface aux_onboarding + _machine_ram_gb use."""
    CalledProcessError = Exception
    TimeoutExpired = Exception
    PIPE = -1
    DEVNULL = -3
    STDOUT = -2

    def run(self, argv, **kw):
        a = " ".join(str(x) for x in argv)
        if "hw.memsize" in a:
            return _R(str(int(RAM_STATE["gb"] * 1024 ** 3)))
        if "machdep.cpu.brand_string" in a:
            return _R("Apple M5 Max")
        if "sw_vers" in a:
            return _R("26.1")
        if "pmset" in a:
            return _R("Now drawing from 'AC Power'\n -InternalBattery-0 (id=123)\t100%"
                      if RAM_STATE["laptop"] else "Now drawing from 'AC Power'")
        return _R("", 1)

    def Popen(self, *a, **k):
        raise RuntimeError("harness: no Popen")


# --------------------------------------------------------------------------
# load server.py (execs every aux_*.py, registering routes)
# --------------------------------------------------------------------------
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_under_test",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_under_test"] = srv
spec.loader.exec_module(srv)
print("server.py loaded (HOME=%s)" % TMP)

FAKE = FakeSubprocess()
srv.subprocess = FAKE
srv._RAM_GB_CACHE[0] = None

# never let anything in this harness start a real download or wake a model
DOWNLOADS = []


def _fake_download(mid):
    DOWNLOADS.append(mid)
    return {"ok": True, "status": "downloading"}


srv.download_model = _fake_download
srv.agent_wake = lambda *a, **k: None
srv._mlx_start = lambda *a, **k: False

Ctx = srv.RouteCtx
GET, POST = srv.GET_ROUTES, srv.POST_ROUTES


def get(path, **q):
    return GET[path](Ctx(query={k: [v] for k, v in q.items()}))


def post(path, body):
    r = POST[path](Ctx(body=body))
    return r[0] if isinstance(r, tuple) else r


def as_ram(gb, laptop=True):
    RAM_STATE["gb"] = gb
    RAM_STATE["laptop"] = laptop
    srv._RAM_GB_CACHE[0] = None


try:
    # ======================================================================
    print("\n[1] routes registered")
    for p in ("/api/onboarding/state",):
        check("GET %s" % p, p in GET)
    for p in ("/api/onboarding/apply", "/api/onboarding/done",
              "/api/onboarding/reset"):
        check("POST %s" % p, p in POST)

    # ======================================================================
    print("\n[2] _onb_tier — every machine size (PURE)")
    M2B, M4B, M9B, M27B = srv.M2B, srv.M4B, srv.M9B, srv.M27B
    EXPECT = {
        8:   ("minimal",  M2B,  None, False),
        15:  ("minimal",  M2B,  None, False),
        16:  ("small",    M4B,  None, False),
        23:  ("small",    M4B,  None, False),
        24:  ("balanced", M9B,  M2B,  False),
        32:  ("balanced", M9B,  M2B,  False),
        35:  ("balanced", M9B,  M2B,  False),
        36:  ("large",    M27B, M9B,  True),
        47:  ("large",    M27B, M9B,  True),
        48:  ("full",     M27B, M9B,  False),
        64:  ("full",     M27B, M9B,  False),
        128: ("full",     M27B, M9B,  False),
    }
    for gb, (tier, prim, bg, tight) in sorted(EXPECT.items()):
        t = srv._onb_tier(gb)
        check("%3d GB -> %-8s %s / %s%s" % (gb, tier, prim.split("/")[-1],
                                            (bg or "none").split("/")[-1],
                                            " (tight)" if tight else ""),
              t["tier"] == tier and t["primary"] == prim and
              t["background"] == bg and t["tight"] == tight, repr(t))
    check("<16 GB carries the 'chat only' note",
          srv._onb_tier(8)["note"] == "chat only, small context")
    check("36-47 offers a battery alternative (9B + 2B)",
          (srv._onb_tier(40)["alt"] or {}).get("primary") == M9B and
          (srv._onb_tier(40)["alt"] or {}).get("background") == M2B)
    check("48+ has no alternative", srv._onb_tier(64)["alt"] is None)
    check("garbage RAM degrades to minimal, never raises",
          srv._onb_tier(None)["tier"] == "minimal" and
          srv._onb_tier("nope")["tier"] == "minimal")

    # ======================================================================
    print("\n[3] recommendation + fits (reuses _model_fit)")
    as_ram(64)
    st = get("/api/onboarding/state")
    rec = st["recommendation"]
    check("64 GB -> 27B primary + 9B background",
          rec["primary"] == M27B and rec["background"] == M9B)
    check("download_gb = 16.1 + 0.9 drafter + 6.0 = 23.0 GB",
          rec["download_gb"] == 23.0, rec["download_gb"])
    check("fits lists all four catalog models", len(rec["fits"]) == 4)
    fit = {f["id"]: f["fit"] for f in rec["fits"]}
    check("64 GB: every model fits 'ok'",
          all(v == "ok" for v in fit.values()), fit)
    as_ram(16)
    fit16 = {f["id"]: f["fit"] for f in get("/api/onboarding/state")["recommendation"]["fits"]}
    check("16 GB: 27B badge is 'no' (19 > 0.85 x 16)", fit16[M27B] == "no", fit16)
    check("16 GB: 4B/2B badges 'ok'",
          fit16[M4B] == "ok" and fit16[M2B] == "ok", fit16)
    as_ram(8)
    fit8 = {f["id"]: f["fit"] for f in get("/api/onboarding/state")["recommendation"]["fits"]}
    check("8 GB: 9B and 27B badges are 'no'",
          fit8[M9B] == "no" and fit8[M27B] == "no", fit8)
    # The tier rule is STRICTER than _model_fit on purpose (it budgets for the
    # whole running system, not one model) — assert the divergence explicitly so
    # nobody "fixes" it later.
    check("8 GB: the 4B badge reads 'ok' but the tier still recommends the 2B",
          fit8[M4B] == "ok" and srv._onb_tier(8)["primary"] == M2B, fit8)
    as_ram(40)
    fit40 = {f["id"]: f["fit"] for f in get("/api/onboarding/state")["recommendation"]["fits"]}
    check("40 GB: 27B badge is 'ok' alone, yet the PAIR is flagged tight",
          fit40[M27B] == "ok" and srv._onb_tier(40)["tight"] is True, fit40)

    # ======================================================================
    print("\n[4] state shape + detected system facts")
    as_ram(64, laptop=True)
    st = get("/api/onboarding/state")
    si, det, pr = st["sysinfo"], st["detect"], st["prefs"]
    check("sysinfo.chip from sysctl", si["chip"] == "Apple M5 Max", si["chip"])
    check("sysinfo.ram_gb == 64", si["ram_gb"] == 64)
    check("sysinfo.macos from sw_vers", si["macos"] == "26.1")
    check("sysinfo.laptop true when pmset shows InternalBattery", si["laptop"] is True)
    check("sysinfo.cores > 0", si["cores"] > 0)
    check("sysinfo.disk_free_gb is a number", isinstance(si["disk_free_gb"], (int, float)))
    as_ram(64, laptop=False)
    check("sysinfo.laptop false without InternalBattery",
          get("/api/onboarding/state")["sysinfo"]["laptop"] is False)
    as_ram(64, laptop=True)
    for k in ("hermes_cli", "claude_cli", "mlx_venv", "models_downloaded",
              "fda", "telegram_configured", "google_configured"):
        check("detect.%s present" % k, k in det)
    check("detect.telegram_configured true (token in .env)",
          det["telegram_configured"] is True)
    check("detect.google_configured false (no token file)",
          det["google_configured"] is False)
    check("detect.fda is None with no Message Center store", det["fda"] is None)
    check("detect.models_downloaded is a list (throwaway HOME: empty)",
          det["models_downloaded"] == [])
    for k in ("theme", "idle_min", "prewarm", "claude_escalation",
              "briefings", "news", "quiet_hours"):
        check("prefs.%s present" % k, k in pr)
    check("prefs.theme is null (client-side only)", pr["theme"] is None)

    # fda: once the app has reported, the probe reflects it
    srv.write_json(srv.MSG_STORE, {"v": 1, "fda": False, "convos": []})
    check("detect.fda false when the app reported no FDA",
          get("/api/onboarding/state")["detect"]["fda"] is False)
    srv.write_json(srv.MSG_STORE, {"v": 1, "fda": True, "convos": []})
    check("detect.fda true when the app reported FDA",
          get("/api/onboarding/state")["detect"]["fda"] is True)

    # ======================================================================
    print("\n[5] done / reset — state.done flips")
    check("fresh HOME starts not done", get("/api/onboarding/state")["done"] is False)
    d = post("/api/onboarding/done", {})
    check("POST done -> ok", d.get("ok") is True)
    check("marker file written", os.path.exists(srv.ONB_FILE))
    mk = json.load(open(srv.ONB_FILE))
    check("marker carries version + ts", mk.get("version") == "1.1.1" and mk.get("ts"))
    st2 = get("/api/onboarding/state")
    check("state.done flipped true", st2["done"] is True)
    check("state.done_at is the marker ts", st2["done_at"] == mk["ts"])
    r = post("/api/onboarding/reset", {})
    check("POST reset -> ok", r.get("ok") is True and r.get("done") is False)
    check("marker removed", not os.path.exists(srv.ONB_FILE))
    check("state.done back to false", get("/api/onboarding/state")["done"] is False)
    open(srv.ONB_FILE, "w").close()          # bare touch, no JSON
    check("a bare touch still counts as done",
          get("/api/onboarding/state")["done"] is True)
    post("/api/onboarding/reset", {})
    check("reset is idempotent", post("/api/onboarding/reset", {})["ok"] is True)

    # ======================================================================
    print("\n[6] apply — validation refuses every bad field")
    BAD = [
        ("theme", {"theme": "neon"}, "theme must be"),
        ("idle_min string", {"idle_min": "soon"}, "idle_min must be"),
        ("idle_min 0", {"idle_min": 0}, "idle_min must be"),
        ("idle_min 9999", {"idle_min": 9999}, "idle_min must be"),
        ("prewarm non-bool", {"prewarm": "yes"}, "prewarm must be a boolean"),
        ("escalation non-bool", {"claude_escalation": 1}, "must be a boolean"),
        ("briefings non-bool", {"briefings": "on"}, "briefings must be a boolean"),
        ("news non-bool", {"news": 3}, "news must be a boolean"),
        ("quiet_hours bad time", {"quiet_hours": {"start": "25:00", "end": "07:00"}}, "HH:MM"),
        ("quiet_hours missing end", {"quiet_hours": {"start": "22:00"}}, "HH:MM"),
        ("unknown primary", {"primary": "evil/not-a-model"}, "unknown model"),
        ("unknown background", {"background": "evil/nope"}, "unknown model"),
        ("download non-bool", {"download": "yes"}, "download must be a boolean"),
    ]
    for name, body, frag in BAD:
        r = post("/api/onboarding/apply", body)
        check("refused: %s" % name,
              r["ok"] is False and any(frag in e for e in r["errors"]),
              repr(r["errors"]))
    check("a refused apply started NO download", DOWNLOADS == [], DOWNLOADS)

    print("\n[7] apply — good values land through the existing helpers")
    r = post("/api/onboarding/apply", {
        "theme": "dark", "idle_min": 20, "prewarm": False,
        "claude_escalation": False, "briefings": False, "news": True,
        "quiet_hours": {"start": "23:30", "end": "06:15"}, "download": False})
    check("apply ok", r["ok"] is True, repr(r["errors"]))
    check("theme echoed, not stored", r["applied"]["theme"] == "dark")
    check("idle_min written", srv._idle_min() == 20.0)
    check("idle stays enabled", srv.idle_suspend_enabled() is True)
    check("prewarm off via set_prewarm_enabled", srv.prewarm_enabled() is False)
    check("escalation off via _cb_set_escalation",
          srv.claude_escalation_enabled() is False)
    wt = srv._wt_load()
    check("briefings master off", wt["master"]["briefings"] is False)
    check("news master on", wt["master"]["news"] is True)
    check("quiet hours through watchtower's own op",
          wt["quiet_hours"] == {"start": "23:30", "end": "06:15"}, wt["quiet_hours"])

    r = post("/api/onboarding/apply", {"idle_min": "never"})
    check("idle 'never' disables idle-suspend",
          r["ok"] and srv.idle_suspend_enabled() is False)
    r = post("/api/onboarding/apply", {"idle_min": None})
    check("idle null also disables", r["ok"] and srv.idle_suspend_enabled() is False)
    r = post("/api/onboarding/apply", {"idle_min": 5})
    check("idle 5 re-enables and sets minutes",
          r["ok"] and srv.idle_suspend_enabled() is True and srv._idle_min() == 5.0)
    r = post("/api/onboarding/apply", {"prewarm": True, "claude_escalation": True,
                                       "briefings": True})
    check("toggles come back on",
          srv.prewarm_enabled() and srv.claude_escalation_enabled() and
          srv._wt_load()["master"]["briefings"] is True)
    check("still no downloads", DOWNLOADS == [], DOWNLOADS)

    print("\n[8] apply — roster is additive, background lane written")
    before = [m["id"] for m in srv._model_registry()]
    r = post("/api/onboarding/apply", {"primary": M27B, "background": M9B,
                                       "download": False})
    check("apply ok", r["ok"] is True, repr(r["errors"]))
    after = [m["id"] for m in srv._model_registry()]
    check("no roster entry removed", set(before) <= set(after))
    check("27B on the roster", M27B in after)
    check("9B on the roster", M9B in after)
    ent = srv._model_entry(M27B)
    check("27B kept its FULL roster shape (backend/template_args/drafter)",
          ent.get("backend") == "mlx_vlm" and
          ent.get("template_args") == {"enable_thinking": False} and
          ent.get("draft_model") == "mlx-community/Qwen3.8-27B-MTP-bf16" and
          ent.get("draft_kind") == "mtp" and ent.get("draft_block_size") == 3, ent)
    check("size_gb is NOT written into the roster",
          "size_gb" not in ent and "draft_size_gb" not in ent)
    check("bg-model file written", srv.bg_model() == M9B, srv.bg_model())

    # a small Mac adds the 2B, which is not in _SEED_MODELS
    as_ram(24)
    r = post("/api/onboarding/apply", {"primary": M9B, "background": M2B,
                                       "download": False})
    ent2 = srv._model_entry(M2B)
    check("2B added with the full catalog shape",
          r["ok"] and ent2 and ent2.get("backend") == "mlx_vlm" and
          ent2.get("ram") == 3, ent2)
    check("active model untouched by apply (no switch, no restart)",
          srv.active_model() == srv.DEFAULT_MODEL, srv.active_model())
    as_ram(64)

    print("\n[9] download — refused when the disk is too small")
    real_free = srv._onb_disk_free_gb
    need = round(srv._onb_dl_gb(M27B) + srv._onb_dl_gb(M9B), 1)   # 23.0
    srv._onb_disk_free_gb = lambda p=None: need + 4.9             # inside headroom
    r = post("/api/onboarding/apply", {"primary": M27B, "background": M9B,
                                       "download": True})
    check("refused with %.1f GB free for a %.1f GB pull" % (need + 4.9, need),
          r["ok"] is False and any("not enough free disk" in e for e in r["errors"]),
          repr(r["errors"]))
    check("refusal started NO download", DOWNLOADS == [], DOWNLOADS)
    srv._onb_disk_free_gb = lambda p=None: 3.0
    r = post("/api/onboarding/apply", {"primary": M2B, "download": True})
    check("tiny disk refuses even the 2B",
          r["ok"] is False and any("not enough free disk" in e for e in r["errors"]))
    check("still no download", DOWNLOADS == [], DOWNLOADS)

    print("\n[10] download — allowed with headroom (download_model STUBBED)")
    srv._onb_disk_free_gb = lambda p=None: need + 5.1
    r = post("/api/onboarding/apply", {"primary": M27B, "background": M9B,
                                       "download": True})
    check("apply ok", r["ok"] is True, repr(r["errors"]))
    check("both models handed to download_model",
          sorted(DOWNLOADS) == sorted([M27B, M9B]), DOWNLOADS)
    check("applied.download_gb quoted honestly",
          r["applied"]["download_gb"] == need, r["applied"].get("download_gb"))
    DOWNLOADS[:] = []
    srv._model_downloaded = lambda mid: True          # pretend both are local
    r = post("/api/onboarding/apply", {"primary": M27B, "background": M9B,
                                       "download": True})
    check("already-downloaded models are not re-pulled",
          DOWNLOADS == [] and r["applied"].get("download_note") == "already downloaded",
          repr(r["applied"]))
    check("recommendation.already_downloaded reflects it",
          get("/api/onboarding/state")["recommendation"]["already_downloaded"] is True)
    srv._model_downloaded = lambda mid: False
    srv._onb_disk_free_gb = real_free

    # ======================================================================
    print("\n[11] SECRETS — the .env token value never appears in any response")
    bodies = []
    bodies.append(json.dumps(get("/api/onboarding/state")))
    bodies.append(json.dumps(post("/api/onboarding/apply", {
        "theme": "light", "idle_min": 10, "prewarm": True,
        "claude_escalation": True, "briefings": True, "news": True,
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "primary": M27B, "background": M9B, "download": False})))
    bodies.append(json.dumps(post("/api/onboarding/apply", {"theme": "neon"})))
    bodies.append(json.dumps(post("/api/onboarding/done", {})))
    bodies.append(json.dumps(post("/api/onboarding/reset", {})))
    blob = "\n".join(bodies)
    check("full token value absent from every response", SECRET not in blob)
    check("token body absent (no partial leak)",
          "AAH-THIS-IS-THE-SECRET" not in blob)
    check("the literal key name is not echoed with a value",
          "TELEGRAM_BOT_TOKEN" not in blob)
    check("telegram is reported as a BOOLEAN",
          get("/api/onboarding/state")["detect"]["telegram_configured"] is True)
    check("_onb_env_has returns a bool, never the value",
          srv._onb_env_has("TELEGRAM_BOT_TOKEN") is True and
          srv._onb_env_has("NOPE_NOT_SET") is False)
    with open(os.path.join(TMP, ".hermes", ".env"), "a") as f:
        f.write("EMPTY_KEY=\n")
    check("an empty value reads as NOT configured",
          srv._onb_env_has("EMPTY_KEY") is False)

    print("\n[12] no model was ever woken or really downloaded")
    check("download_model was only ever the stub",
          srv.download_model is _fake_download)
    check("no leftover queued downloads", DOWNLOADS == [], DOWNLOADS)

except Exception:
    traceback.print_exc()
    FAILS.append("EXCEPTION")

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("   - %s" % f)
sys.exit(1 if FAILS else 0)
