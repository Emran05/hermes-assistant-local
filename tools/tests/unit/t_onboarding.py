#!/usr/bin/env python3
"""Backend harness for aux_onboarding.py (release 1.1.1; round 3: 1.4.0).

Loads dashboard/server.py against a THROWAWAY HOME (so nothing touches the real
~/.hermes), stubs subprocess for sysctl/sw_vers/pmset, and exercises:
  * _onb_tier for 8/16/24/36/48/64/128 GB
  * /api/onboarding/state shape + done flip
  * /api/onboarding/apply validation (every field)
  * download refusal on low disk
  * done / reset
  * SECRETS: the .env token value never appears in any response
  * round 3: the recommendation delegates to the model advisor's
    _adv_recommend_compat (and falls back to the RAM tier, which is
    unchanged), ?brief=1, _onb_known_model / roster / sizes for the
    advisor's curated ids, and the uncensored acknowledgement gate on apply
    (primary AND background-only; 409 with nothing applied; strict boolean)

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
# E95 (2026-11-09 audit): the advisor offers a background lane only when it can
# run (file evidence). These checks are written for a Mac whose lane runs, so
# the owner's start token is present; t_e95_advisor_lane.py covers the other.
open(os.path.join(TMP, ".hermes", "dashboard", "model-start-ok-bg"), "w").close()

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
# pgrep for a background-lane process (R2-42): True -> one is running
BG_STATE = {"proc": False, "pgrep": []}


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
        if "config set model.default" in a:      # C63 activation (no real hermes)
            return _R("", 0)
        if argv and argv[0] == "pgrep":
            BG_STATE["pgrep"].append(list(argv))
            return _R("4242\n", 0) if BG_STATE["proc"] else _R("", 1)
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
# ...nor probe the real model port: apply asks model_online() before it
# activates the pick (C63), and the real one GETs 127.0.0.1:8080. "Nothing on
# :8080" is this harness's premise, so it is stated, not looked up.
srv.model_online = lambda: False
# ...and the same for the background lane on :8081 (R2-42 asks whether it is
# running before it repoints bg-model): nothing is there in this harness.
_REAL_BG_PROBE = srv._bg_probe
srv._bg_probe = lambda: False

Ctx = srv.RouteCtx
GET, POST = srv.GET_ROUTES, srv.POST_ROUTES


def get(path, **q):
    return GET[path](Ctx(query={k: [v] for k, v in q.items()}))


def post(path, body):
    r = POST[path](Ctx(body=body))
    return r[0] if isinstance(r, tuple) else r


def post_raw(path, body):
    """(body, status) — the uncensored gate answers 409 / 400."""
    r = POST[path](Ctx(body=body))
    return (r[0], r[1]) if isinstance(r, tuple) else (r, 200)


def wait_for(cond, timeout=10.0, step=0.02):
    """Poll `cond` for up to `timeout` s. The R2-42 watcher checks used fixed
    budgets of 2-3 s and a 0.4 s window, which a loaded Mac (several full
    tiers at once) can overrun: that was the bg-lane race flake."""
    import time as _tw
    end = _tw.time() + timeout
    while _tw.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        _tw.sleep(step)
    try:
        return bool(cond())
    except Exception:
        return False


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
    print("\n[3b] round 3: the recommendation is the model advisor's")
    as_ram(64)
    LEGACY_KEYS = set(srv._onb_recommend(64, []).keys())
    rec = get("/api/onboarding/state")["recommendation"]
    check("the state's recommendation comes from _adv_recommend_compat (tier 'advisor')",
          rec.get("tier") == "advisor" and (rec.get("advisor") or {}).get("ok") is True,
          (rec.get("tier"), rec.get("advisor", {}).get("error")))
    check("...in _onb_recommend's exact key set, plus 'advisor'",
          set(rec.keys()) == LEGACY_KEYS | {"advisor"}, sorted(set(rec.keys()) ^ LEGACY_KEYS))
    check("...on this M5 Max at 64 GB: the 27B assistant with the 9B lane",
          rec["primary"] == M27B and rec["background"] == M9B, (rec["primary"], rec["background"]))
    check("...and an uncensored build is never the recommendation or its alternative",
          not srv._onb_uncensored(rec["primary"]) and
          not (rec.get("alt") and srv._onb_uncensored(rec["alt"].get("primary") or "")))
    CALLS3 = []
    real_compat = srv._adv_recommend_compat

    def _boom(*a, **k):
        CALLS3.append(a)
        raise RuntimeError("advisor bug")
    try:
        srv._adv_recommend_compat = _boom
        r3 = get("/api/onboarding/state")["recommendation"]
        check("an advisor that RAISES falls back to the RAM tier (never a 500)",
              CALLS3 and r3 == srv._onb_recommend(64, []), r3.get("tier"))
        for bad in ({}, "nope", None, {"tier": "advisor"},
                    dict(srv._onb_recommend(64, []), primary="")):
            srv._adv_recommend_compat = lambda *a, _b=bad, **k: _b
            r3 = get("/api/onboarding/state")["recommendation"]
            check("a malformed advisor answer (%s) falls back to the RAM tier"
                  % (type(bad).__name__ if not isinstance(bad, dict) else sorted(bad)[:2]),
                  r3 == srv._onb_recommend(64, []), r3.get("tier"))
        del srv._adv_recommend_compat
        r3 = get("/api/onboarding/state")["recommendation"]
        check("no advisor loaded at all: the RAM tier, unchanged",
              r3 == srv._onb_recommend(64, []) and r3["tier"] == "full", r3.get("tier"))
    finally:
        srv._adv_recommend_compat = real_compat
    check("_onb_tier is still the pure RAM table (64 GB -> full 27B + 9B)",
          srv._onb_tier(64)["tier"] == "full" and srv._onb_tier(16)["primary"] == M4B)

    print("\n[3c] ?brief=1 is the done marker alone (the per-page-load boot check)")
    CALLS3[:] = []
    try:
        srv._adv_recommend_compat = _boom
        b = get("/api/onboarding/state", brief="1")
    finally:
        srv._adv_recommend_compat = real_compat
    check("brief: only ok/version/brief/done/done_at/done_version",
          set(b.keys()) == {"ok", "version", "brief", "done", "done_at", "done_version"}, sorted(b))
    check("...and it never runs the advisor (nor detection)", CALLS3 == [], len(CALLS3))
    check("...done matches the full state", b["done"] == get("/api/onboarding/state")["done"])
    check("any other brief value is the full state",
          "recommendation" in get("/api/onboarding/state", brief="0"))

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
    check("marker carries version + ts", mk.get("version") == srv.ONB_VERSION and mk.get("ts"))
    check("ONB_VERSION was bumped for the round-3 sheet (was 1.1.1)",
          srv.ONB_VERSION not in ("1.1.1", "") and srv.ONB_VERSION.count(".") == 2,
          srv.ONB_VERSION)
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

    print("\n[6b] apply — R2-41: no boolean minutes, no 500, background must fit")
    real_min = srv._idle_min()
    for v in (True, False):
        r = post("/api/onboarding/apply", {"idle_min": v})
        check("idle_min %r is refused (was: float(%r) minutes)" % (v, v),
              r["ok"] is False and any("idle_min must be" in e for e in r["errors"])
              and "idle_min" not in r["applied"], repr(r))
    check("...and no one-minute idle-suspend was written",
          srv._idle_min() == real_min, srv._idle_min())
    for name, qh in (("a list", ["22:00", "07:00"]), ("a string", "22:00-07:00"),
                     ("a number", 2200)):
        try:
            r = post("/api/onboarding/apply", {"prewarm": False, "quiet_hours": qh})
            raised = None
        except Exception as e:                       # the old code: a 500
            r, raised = {}, "%s: %s" % (type(e).__name__, e)
        check("quiet_hours as %s: an error, not a 500 (was: AttributeError)" % name,
              raised is None and r.get("ok") is False
              and any("quiet_hours must be an object" in e for e in r.get("errors", [])),
              raised or repr(r.get("errors")))
        check("...and the fields before it are still reported as applied",
              raised is None and r.get("applied", {}).get("prewarm") is False,
              raised or repr(r.get("applied")))
    post("/api/onboarding/apply", {"prewarm": True})

    bg_before = srv.bg_model()
    as_ram(16)
    r = post("/api/onboarding/apply", {"primary": M4B, "background": M27B,
                                       "download": False})
    check("16 GB: a 27B background lane next to a 4B is refused",
          r["ok"] is False and any(e.startswith("background:") and
                                   "does not fit next to" in e for e in r["errors"]),
          repr(r["errors"]))
    check("...bg-model is left alone", srv.bg_model() == bg_before, srv.bg_model())
    check("...and nothing is reported as the background lane",
          "background" not in r["applied"] and "background_lane" not in r["applied"],
          repr(r["applied"]))
    check("...while the primary pick in the same request still lands",
          r["applied"].get("primary") == M4B, repr(r["applied"]))
    as_ram(8)
    r = post("/api/onboarding/apply", {"primary": M2B, "background": M9B,
                                       "download": False})
    check("8 GB: a 9B lane next to the 2B (~10 GB together) is refused",
          r["ok"] is False and any("does not fit next to" in e for e in r["errors"]),
          repr(r["errors"]))
    r = post("/api/onboarding/apply", {"background": M2B, "download": False})
    check("8 GB, no primary in the request: judged next to the ACTIVE model (27B)",
          r["ok"] is False and any("does not fit next to Qwen3.8-27B" in e
                                   for e in r["errors"]), repr(r["errors"]))
    as_ram(16)
    r = post("/api/onboarding/apply", {"primary": M4B, "background": M2B,
                                       "download": False})
    check("16 GB: a 2B lane next to a 4B fits and is written",
          r["ok"] is True and srv.bg_model() == M2B, (r["errors"], srv.bg_model()))
    refused = []
    for gb in (8, 16, 24, 32, 36, 40, 48, 64, 128):
        as_ram(gb)
        t = srv._onb_tier(gb)
        for prim, bg in ((t["primary"], t["background"]),
                         ((t["alt"] or {}).get("primary"), (t["alt"] or {}).get("background"))):
            if prim and bg and srv._onb_bg_misfit(bg, prim):
                refused.append((gb, prim, bg))
    check("no tier's recommended (or battery) pair is ever refused", refused == [], refused)
    as_ram(64)
    check("the fit check needs a size: an unknown one never refuses",
          srv._onb_bg_misfit("org/no-such-model", M27B) is None)

    print("\n[6c] round 3: the advisor's curated ids are known models")
    as_ram(64)
    XID = "harness-org/Advisor-Extra-4bit"
    srv._ADV_BY_ID[XID] = {"id": XID, "label": "Advisor Extra", "ram": 5, "size_gb": 4.2,
                           "draft_size_gb": 0.3, "backend": "mlx_vlm", "ctx": 262144,
                           "template_args": {"enable_thinking": False},
                           "verified": {"drill": "6/6"}, "trust_remote_code": True}
    try:
        check("_onb_known_model accepts an _ADV_BY_ID id", srv._onb_known_model(XID) is True)
        check("...and still refuses an arbitrary id",
              srv._onb_known_model("harness-org/not-curated") is False)
        check("_onb_dl_gb quotes its curated size (main + drafter) through _catalog_dl_gb",
              srv._onb_dl_gb(XID) == 4.5, srv._onb_dl_gb(XID))
        seed = "mlx-community/Qwen3.6-35B-A3B-4bit"
        check("...and a curated SEED's size (was: 0, which skipped apply's disk check)",
              srv._onb_dl_gb(seed) > 0 and srv._onb_dl_gb(seed) == srv._catalog_dl_gb(seed),
              (srv._onb_dl_gb(seed), srv._catalog_dl_gb(seed)))
        check("an id nobody knows the size of is still 0",
              srv._onb_dl_gb("harness-org/not-curated") == 0.0)
        r = post("/api/onboarding/apply", {"primary": XID, "download": False})
        ent = srv._model_entry(XID) or {}
        check("apply puts it on the roster with its full curated shape",
              r["applied"].get("primary") == XID and ent.get("backend") == "mlx_vlm" and
              ent.get("template_args") == {"enable_thinking": False} and ent.get("ram") == 5,
              (r["errors"], ent))
        check("...without size_gb, draft_size_gb, verified or trust_remote_code (add_model's rule)",
              not any(k in ent for k in ("size_gb", "draft_size_gb", "verified",
                                         "trust_remote_code")), sorted(ent))
        # review: _onb_known_model accepts these ids, so the R2-41 fit check must
        # SEE them too (their `ram` lives only in the advisor's shape until the
        # pick puts them on the roster); before, an unknown ram failed open
        check("_onb_ram_of reads an advisor-curated id's ram", srv._onb_ram_of(XID) == 5.0,
              srv._onb_ram_of(XID))
        XBIG = "harness-org/Advisor-Giant-8bit"
        srv._ADV_BY_ID[XBIG] = {"id": XBIG, "label": "Advisor Giant", "ram": 60, "size_gb": 58,
                                "backend": "mlx_vlm", "ctx": 262144}
        try:
            r = post("/api/onboarding/apply", {"background": XBIG, "download": False})
            check("a background pick that cannot fit next to the active model is refused "
                  "(was: skipped the check, fail-open)",
                  r["ok"] is False and any("Advisor Giant does not fit" in e for e in r["errors"])
                  and "background" not in r["applied"], (r["errors"], r["applied"]))
            check("...and nothing was written to the roster for it",
                  srv._model_entry(XBIG) is None)
        finally:
            srv._ADV_BY_ID.pop(XBIG, None)
    finally:
        srv._ADV_BY_ID.pop(XID, None)
        srv.write_json(srv.MODELS_FILE, {"models": [m for m in srv._model_registry()
                                                    if m.get("id") not in (XID,)]})

    print("\n[6d] round 3: an uncensored pick needs the acknowledgement")
    UNC = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
    UNC9 = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
    check("fixture: both are curated uncensored seeds on the roster",
          srv._onb_uncensored(UNC) is True and srv._onb_uncensored(UNC9) is True and
          srv._onb_known_model(UNC) and srv._onb_known_model(UNC9))
    check("...and the stock models are not", srv._onb_uncensored(M27B) is False and
          srv._onb_uncensored(M9B) is False)

    def snapshot():
        try:
            mtime = os.stat(srv.MODELS_FILE).st_mtime_ns
        except OSError:
            mtime = None
        return {"bg": srv.bg_model(), "pend": os.path.exists(srv.ONB_BG_PENDING),
                "active": srv.active_model(), "prewarm": srv.prewarm_enabled(),
                "idle": srv._idle_min(), "wt": json.dumps(srv._wt_load(), sort_keys=True),
                "models": mtime, "dl": list(DOWNLOADS),
                "settings": json.dumps(srv.get_settings(), sort_keys=True)}
    post("/api/onboarding/apply", {"prewarm": True, "idle_min": 10})
    before = snapshot()
    real_disk = srv._onb_disk_free_gb
    srv._onb_disk_free_gb = lambda p=None: 900.0
    try:
        body, st = post_raw("/api/onboarding/apply", {
            "primary": UNC, "background": M9B, "download": True, "prewarm": False,
            "idle_min": 20, "briefings": False, "theme": "dark"})
        check("an uncensored primary without the ack: HTTP 409", st == 409, st)
        check("...{ok:false, needs_ack:true, error:'uncensored_ack', id}",
              body.get("ok") is False and body.get("needs_ack") is True and
              body.get("error") == "uncensored_ack" and body.get("id") == UNC, body)
        lbl = (srv._model_entry(UNC) or {}).get("label")
        check("...errors name it: '<label> is an uncensored model. Acknowledge it first.'",
              body.get("errors") == ["%s is an uncensored model. Acknowledge it first." % lbl],
              body.get("errors"))
        check("...and carry the advisor's acknowledgement text", isinstance(body.get("ack_text"), str)
              and "refusal" in body["ack_text"], body.get("ack_text"))
        check("...NOTHING was applied: no pref, no roster write, no lane, no download",
              snapshot() == before and body.get("applied") == {},
              {k: (before[k], v) for k, v in snapshot().items() if before[k] != v})

        body, st = post_raw("/api/onboarding/apply", {"background": UNC9, "download": False})
        check("the advisor card's background-only request {background: id}: 409 too",
              st == 409 and body.get("needs_ack") is True and body.get("id") == UNC9 and
              body.get("role") == "background", (st, body))
        check("...bg-model untouched, nothing deferred", snapshot() == before,
              {k: (before[k], v) for k, v in snapshot().items() if before[k] != v})
        body, st = post_raw("/api/onboarding/apply", {"primary": M27B, "background": UNC9,
                                                      "download": False})
        check("a stock primary next to an uncensored background: the whole request is "
              "refused (no partial write of the primary either)",
              st == 409 and snapshot() == before, (st, body.get("errors")))
        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "background": UNC9})
        check("both uncensored: one error per model", st == 409 and len(body["errors"]) == 2,
              body.get("errors"))

        for bad in ("yes", 1, 0, None, [], {}):
            body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "ack_uncensored": bad,
                                                          "prewarm": False})
            check("ack_uncensored %r (not a JSON boolean): HTTP 400, nothing applied" % (bad,),
                  st == 400 and body.get("ok") is False and
                  "ack_uncensored must be a boolean" in body.get("errors", []) and
                  snapshot() == before, (st, body))
        body, st = post_raw("/api/onboarding/apply", {"prewarm": True, "ack_uncensored": "x"})
        check("...even with no model in the request", st == 400, st)

        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "ack_uncensored": False,
                                                      "download": False})
        check("ack_uncensored false is a refusal, not a pass", st == 409, st)
        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "ack_uncensored": True,
                                                      "download": False})
        check("ack_uncensored true opens it: the pick lands as usual",
              st == 200 and body["applied"].get("primary") == UNC and
              "needs_ack" not in body, (st, body.get("errors")))
        check("...and the per-request ack is NOT stored",
              ((srv.get_settings().get("advisor") or {}).get("uncensored_ack")) is not True)
        body, st = post_raw("/api/onboarding/apply", {"background": UNC9, "ack_uncensored": True,
                                                      "download": False})
        check("background-only with the ack: bg-model is written",
              st == 200 and srv.bg_model() == UNC9, (st, body.get("errors"), srv.bg_model()))
        post("/api/onboarding/apply", {"background": M9B, "download": False})

        srv.settings_update(lambda d: d.__setitem__("advisor", dict(d.get("advisor") or {},
                                                                    uncensored_ack=True)))
        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "download": False})
        check("a STORED advisor acknowledgement (settings.advisor.uncensored_ack) opens it",
              st == 200 and body["applied"].get("primary") == UNC, (st, body.get("errors")))
        srv.settings_update(lambda d: d.__setitem__("advisor", dict(d.get("advisor") or {},
                                                                    uncensored_ack="true")))
        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "download": False})
        check("...only an exact true: 'true' (a string) is no acknowledgement", st == 409, st)
        srv.settings_update(lambda d: d.__setitem__("advisor", dict(d.get("advisor") or {},
                                                                    uncensored_ack=False)))
        real_settings = open(srv.SETTINGS_FILE).read()
        with open(srv.SETTINGS_FILE, "w") as f:
            f.write('{"advisor": {"uncensored_ack": true')          # corrupt
        body, st = post_raw("/api/onboarding/apply", {"primary": UNC, "download": False})
        check("a corrupt settings.json fails CLOSED (no acknowledgement)", st == 409, st)
        with open(srv.SETTINGS_FILE, "w") as f:
            f.write(real_settings)

        body, st = post_raw("/api/onboarding/apply", {"primary": M27B, "download": False})
        check("a stock pick never meets the gate", st == 200 and "needs_ack" not in body, st)

        saved = {n: getattr(srv, n, None) for n in
                 ("_roster_uncensored", "_adv_is_uncensored", "_switch_ack_refusal")}

        def _raise(*a, **k):
            raise RuntimeError("predicate bug")
        try:
            srv._roster_uncensored = _raise
            srv._adv_is_uncensored = _raise
            body, st = post_raw("/api/onboarding/apply", {"primary": M27B, "download": False})
            check("a predicate that RAISES fails closed: even a stock model needs the ack",
                  st == 409 and body.get("needs_ack") is True, st)
            for n in saved:
                srv.__dict__.pop(n, None)
            check("no predicate resolves at all: _onb_uncensored says None",
                  srv._onb_uncensored(M27B) is None)
            body, st = post_raw("/api/onboarding/apply", {"primary": M27B, "download": False})
            check("...and apply fails closed (409)", st == 409, st)
            body, st = post_raw("/api/onboarding/apply", {"primary": M27B, "ack_uncensored": True,
                                                          "download": False})
            check("...which the ack still opens", st == 200, (st, body.get("errors")))
        finally:
            for n, fn in saved.items():
                if fn is not None:
                    setattr(srv, n, fn)
        check("predicates restored", srv._onb_uncensored(UNC) is True and
              srv._onb_uncensored(M27B) is False)
    finally:
        srv._onb_disk_free_gb = real_disk
    check("the gate never started a download", DOWNLOADS == [], DOWNLOADS)
    check("...nor activated an unacknowledged model",
          srv.active_model() == srv.DEFAULT_MODEL, srv.active_model())
    post("/api/onboarding/apply", {"background": M9B, "download": False})

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

    print("\n[7b] apply — R2-43: agent web research through watchtower's own op")
    check("prefs.web_research is present and OFF by default (C50)",
          get("/api/onboarding/state")["prefs"].get("web_research") is False)
    r = post("/api/onboarding/apply", {"web_research": True})
    check("web_research true lands through set_web_research",
          r["ok"] is True and r["applied"].get("web_research") is True and
          srv._wt_load()["web_research"]["enabled"] is True, repr(r))
    check("...and prefs report it", r["prefs"].get("web_research") is True, r["prefs"])
    for bad in ("yes", 1, None):
        r = post("/api/onboarding/apply", {"web_research": bad})
        check("web_research %r is refused, nothing changes" % (bad,),
              r["ok"] is False and any("web_research must be a boolean" in e
                                       for e in r["errors"]) and
              srv._wt_load()["web_research"]["enabled"] is True, repr(r["errors"]))
    r = post("/api/onboarding/apply", {"web_research": False})
    check("web_research false turns it off again",
          r["ok"] is True and srv._wt_load()["web_research"]["enabled"] is False, repr(r))

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
    # C63: a pick that is neither local nor being downloaded is NOT made the
    # active model — the next wake would pull it inside the model server,
    # past the disk check. ([10] covers the download path, which activates.)
    check("not downloaded, no download asked: the pick is not activated (C63)",
          srv.active_model() == srv.DEFAULT_MODEL
          and r["applied"].get("primary_active") is False, srv.active_model())
    as_ram(64)

    print("\n[8b] R2-42 — never repoint bg-model under a running background lane")
    import http.server
    import socket
    import threading
    import time as _t
    LANE = {"up": True, "health": {"status": "healthy", "loaded_model": M9B}}

    class _Lane(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not LANE["up"]:
                self.send_response(503); self.end_headers(); return
            body = json.dumps(LANE["health"] if self.path.startswith("/health")
                              else {"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    lane_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Lane)
    threading.Thread(target=lane_srv.serve_forever, daemon=True).start()
    real_bg_url = srv.BG_MODEL_URL
    srv.BG_MODEL_URL = "http://127.0.0.1:%d/v1/models" % lane_srv.server_address[1]
    srv._bg_probe = _REAL_BG_PROBE                  # the real probe, at our lane
    real_watch = getattr(srv, "_onb_bg_watch", None)   # absent on the old code
    WATCH = []
    srv._onb_bg_watch = lambda: WATCH.append(1)
    pend = getattr(srv, "ONB_BG_PENDING", os.path.join(srv.DATA, "bg-model.pending"))
    bgfile = os.path.join(srv.DATA, "bg-model")
    with open(bgfile, "w") as f:                    # the lane was started on the 9B
        f.write(M9B + "\n")
    try:
        served = getattr(srv, "_onb_bg_served", lambda: None)()
        check("baseline: the lane serves the 9B and bg-model says so",
              srv.bg_model() == M9B and served == (True, M9B), (srv.bg_model(), served))
        r = post("/api/onboarding/apply", {"background": M2B, "download": False})
        check("lane up serving the 9B, pick the 2B: apply is ok", r["ok"] is True,
              repr(r["errors"]))
        check("...bg-model is NOT rewritten (was: 2B, and every background call "
              "then got a 400 from the pinned 9B server)", srv.bg_model() == M9B,
              srv.bg_model())
        check("...the pick waits in bg-model.pending",
              os.path.exists(pend) and open(pend).read().strip() == M2B)
        check("...the result says what serves now and what is pending",
              r["applied"].get("background_lane") == M9B and
              r["applied"].get("background_pending") == M2B, repr(r["applied"]))
        note = r["applied"].get("background_note") or ""
        check("...and tells the owner in words",
              "Qwen3.5-9B" in note and "Qwen3.5-2B" in note and "stopped" in note, note)
        check("...the pending watcher was started", WATCH == [1], WATCH)
        check("background producers keep naming the model the lane serves",
              srv.bg_lane()["model"] == M9B and srv.bg_lane()["lane"] == "bg",
              srv.bg_lane())
        check("while the lane still runs, promote() changes nothing",
              srv._onb_bg_promote() is False and srv.bg_model() == M9B)

        r = post("/api/onboarding/apply", {"background": M9B, "download": False})
        check("picking the model the lane already serves: written, pending cleared",
              r["ok"] and srv.bg_model() == M9B and not os.path.exists(pend)
              and "background_pending" not in r["applied"], repr(r["applied"]))

        LANE["health"] = {"status": "ok"}           # mlx-lm: no loaded_model
        r = post("/api/onboarding/apply", {"background": M2B, "download": False})
        check("/health names no model: the bg-model it started from is the "
              "served model, so the 2B is still deferred",
              srv.bg_model() == M9B and open(pend).read().strip() == M2B
              and r["applied"].get("background_lane") == M9B, repr(r["applied"]))

        LANE["up"] = False
        BG_STATE["proc"] = True                     # loading: process, no answer
        _onb_clear = srv._onb_bg_clear_pending
        _onb_clear()
        r = post("/api/onboarding/apply", {"background": M2B, "download": False})
        check("a lane still LOADING (process up, no answer) also defers",
              srv.bg_model() == M9B and os.path.exists(pend), repr(r["applied"]))
        check("...the process check is pgrep on the lane's own launcher/port",
              BG_STATE["pgrep"] and "mlx-server-bg" in BG_STATE["pgrep"][-1][-1]
              and "--port %d" % lane_srv.server_address[1] in BG_STATE["pgrep"][-1][-1],
              BG_STATE["pgrep"][-1:])
        check("...and promote() waits for it", srv._onb_bg_promote() is False)

        BG_STATE["proc"] = False                    # the lane has stopped
        check("lane down: promote() writes the deferred pick",
              srv._onb_bg_promote() is True and srv.bg_model() == M2B
              and not os.path.exists(pend), srv.bg_model())
        check("...and nothing is pending any more", srv._onb_bg_promote() is None)

        r = post("/api/onboarding/apply", {"background": M9B, "download": False})
        check("lane down: a pick is written at once, nothing deferred",
              r["ok"] and srv.bg_model() == M9B and not os.path.exists(pend)
              and r["applied"].get("background_lane") == M9B
              and "background_pending" not in r["applied"], repr(r["applied"]))

        # the real watcher thread, fast
        LANE.update(up=True, health={"status": "healthy", "loaded_model": M9B})
        srv._onb_bg_watch = real_watch
        srv.ONB_BG_WATCH_S = 0.05
        r = post("/api/onboarding/apply", {"background": M2B, "download": False})
        _t.sleep(0.3)
        check("watcher: lane up -> still pending after a few ticks",
              os.path.exists(pend) and srv.bg_model() == M9B)
        LANE["up"] = False
        check("watcher: the lane stops -> the pick lands on its own",
              wait_for(lambda: not os.path.exists(pend) and srv.bg_model() == M2B),
              srv.bg_model())
        check("...and the watcher thread exits",
              wait_for(lambda: srv._onb_bg_watching[0] is False))

        # review: a watcher that is just exiting (nothing pending) and an apply
        # that defers a pick in that instant must not leave the pick with no
        # watcher. The apply runs on its own thread right after the watcher's
        # promote() said "nothing pending"; without the shared lock it saw the
        # watcher still flagged as running, started none, and the pick waited
        # forever.
        LANE.update(up=True, health={"status": "healthy", "loaded_model": M9B})
        with open(bgfile, "w") as f:
            f.write(M9B + "\n")
        srv._onb_bg_clear_pending()
        real_promote = srv._onb_bg_promote
        real_apply_bg = srv._onb_apply_bg
        RACE = {}
        AT_LOCK = threading.Event()

        # The interleaving under test, made deterministic (it used to rest on
        # a 0.4 s join and a 2 s budget, the ~1-in-15 flake on a loaded Mac):
        # the watcher's promote() has just said "nothing pending" and still
        # holds _onb_bg_lock when an apply arrives wanting to defer a pick.
        def _apply_bg_marked(mid, applied, errors):
            AT_LOCK.set()                 # the apply is about to take the lock
            return real_apply_bg(mid, applied, errors)

        def _racing_promote():
            res = real_promote()
            if res is None and "t" not in RACE:
                def _apply():
                    RACE["r"] = post("/api/onboarding/apply",
                                     {"background": M2B, "download": False})
                # started BEFORE it is published: the old test assigned
                # RACE["t"] and then started it, and a poll landing between
                # the two saw a thread that was "not alive" and checked the
                # result before the apply ever ran — the ~1-in-15 flake
                t = threading.Thread(target=_apply, daemon=True)
                t.start()
                RACE["t"] = t
                AT_LOCK.wait(15)          # the apply is at the lock we hold...
                _t.sleep(0.05)            # ...and queued on it
            return res

        srv._onb_apply_bg = _apply_bg_marked
        srv._onb_bg_promote = _racing_promote
        try:
            srv._onb_bg_watch()
            # done = the apply's own answer is in, not "its thread looks idle"
            wait_for(lambda: "r" in RACE, timeout=20)
        finally:
            srv._onb_bg_promote = real_promote
            srv._onb_apply_bg = real_apply_bg
        check("race: the apply reached the lock while the old watcher held it",
              AT_LOCK.is_set())
        check("race: the apply deferred its pick while the old watcher exited",
              (RACE.get("r") or {}).get("applied", {}).get("background_pending") == M2B,
              RACE.get("r"))
        check("...and a watcher is still running for it (was: none, an orphan pick)",
              srv._onb_bg_watching[0] is True and os.path.exists(pend))
        LANE["up"] = False
        check("...so the pick lands once the lane stops",
              wait_for(lambda: not os.path.exists(pend) and srv.bg_model() == M2B),
              srv.bg_model())
        wait_for(lambda: srv._onb_bg_watching[0] is False)
    finally:
        srv._onb_bg_watch = real_watch
        srv.ONB_BG_WATCH_S = 30
        srv.BG_MODEL_URL = real_bg_url
        srv._bg_probe = lambda: False
        BG_STATE["proc"] = False
        lane_srv.shutdown()
        lane_srv.server_close()
        if os.path.exists(pend):
            os.remove(pend)
        with open(bgfile, "w") as f:
            f.write(M9B + "\n")

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
    check("...and activated nothing (C63)",
          r["applied"].get("primary_active") is False, r["applied"])
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
    # C63 + E22 (2026-11-09 audit): a download that only STARTED no longer
    # activates the pick (a failed download would leave the next wake to pull
    # it inside the model server); it is pending until its weights are complete
    check("download started, model not running: the pick is pending, not activated (E22)",
          r["applied"].get("primary_active") is False
          and r["applied"].get("primary_pending") is True, r["applied"])
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
