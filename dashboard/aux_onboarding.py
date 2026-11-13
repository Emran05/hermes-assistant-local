# aux_onboarding.py — first-run onboarding (1.1.1).
#
# The owner's ask: "a sleek onboarding process that asks for sys info — or pulls
# it — to recommend models, and sets preferences."  Nothing here asks the user
# for a fact the Mac already knows: chip, RAM, free disk, macOS version and
# laptop-vs-desktop are all detected, and the model recommendation is derived
# from RAM by a PURE function (_onb_tier) so it is unit-testable for every
# machine size without owning one of each.
#
# Four routes, all through the aux registry (server.py register_get/register_post):
#   GET  /api/onboarding/state   sysinfo + detect + recommendation + prefs
#   POST /api/onboarding/apply   writes preferences through the EXISTING helpers
#   POST /api/onboarding/done    writes ~/.hermes/dashboard/onboarded
#   POST /api/onboarding/reset   removes it (Settings "Run setup again")
#
# DESIGN LAWS obeyed here:
#  * `import datetime as _onb_datetime` — an aux module must NEVER do
#    `from datetime import datetime` (CLAUDE.md: it rebinds the shared global
#    `datetime` to the class and breaks every other module's datetime.timedelta).
#  * NO NETWORK at request time.  The model catalog below is STATIC; every id
#    and download size in it was verified against https://huggingface.co/api/
#    models/<id> by hand before it was written down (2026-09-05).  A dashboard
#    request must never depend on huggingface.co being reachable.
#  * NO SECRET IS EVER ECHOED.  `telegram_configured` is a BOOLEAN derived from
#    whether TELEGRAM_BOT_TOKEN has a non-empty value in ~/.hermes/.env — the
#    value itself is never read into a response, never logged, and never
#    returned in any shape.  Same for the Google token (existence only).
#  * NO MODEL IS EVER WOKEN OR DOWNLOADED as a side effect of reading state.
#    /api/onboarding/state is pure I/O against the filesystem; only an explicit
#    apply with download:true calls download_model().
#
# LOAD ORDER (server.py execs aux_*.py SORTED): this file runs after
# aux_claudebridge / aux_messages / aux_google but BEFORE aux_watchtower.  So
# anything from watchtower (_wt_load, watchtower_post_handler) is resolved BY
# NAME AT REQUEST TIME via _onb_g(), never captured at module load — the same
# discipline aux_index uses for MSG_STORE/INTEL_FILE.

import datetime as _onb_datetime
import urllib.parse as _onb_urlparse
import urllib.request as _onb_urlreq

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
ONB_FILE = os.path.join(DATA, "onboarded")          # {version, ts} once done
# The version of the sheet a marker was written by (state.done_version).
# 1.4.0 (round 3): the model advisor answers the models step, other modules
# add steps through aux_onboarding.js's extra-steps hook, and apply refuses
# an uncensored pick without the acknowledgement.
ONB_VERSION = "1.4.0"
ONB_ENV_FILE = os.path.join(HOME, ".hermes", ".env")
ONB_DISK_HEADROOM_GB = 5.0      # refuse a download that would leave less free
ONB_IDLE_CHOICES = (5, 10, 20)  # the sheet's radio row; None/"never" = off
# R2-42: a background pick made while the lane is serving another model waits
# here until that lane has stopped (see _onb_bg_promote)
ONB_BG_PENDING = os.path.join(DATA, "bg-model.pending")
ONB_BG_WATCH_S = 30
# E22: a primary pick whose download STARTED from the sheet. It is not made the
# active model until its weights (and drafter) are complete on disk. Then a
# watcher (_onb_primary_promote) activates it the way apply always did — only
# while the model server is down, restarting nothing — and only while
# active-model still says what it said when the pick was made (line 2 of the
# file), so a model the owner chose since in the menu is never overridden. The
# sheet also offers "Make it the assistant" (a plain apply, download:false).
# R4 review: without the watcher a fresh Mac whose owner finished setup before
# the download ended kept NO active-model, and the first wake loaded
# mlx-server.sh's hardcoded 27B default instead of the pick.
ONB_PRIMARY_PENDING = os.path.join(DATA, "onboarding-primary.pending")


# --------------------------------------------------------------------------
# The static model catalog.
#
# Entries are FULL ROSTER SHAPES — the same fields _SEED_MODELS carries, so an
# entry copied into models.json by _onb_ensure_roster() behaves identically to a
# seeded one (backend, template_args, drafter, thinking).  `size_gb` is the
# DOWNLOAD size (sum of the repo's files), which is what the user is about to
# spend; `ram` is the RESIDENT footprint, which is what _model_fit() judges.
# The two differ a lot on a 4-bit model and conflating them is how you tell
# someone a 16 GB download needs 16 GB of RAM.
#
# Verified against the HF API on 2026-09-05 (repo exists, sum of sibling blob
# sizes): 2B 1.75 GB · 4B 3.06 GB · 9B 5.98 GB · 27B 16.08 GB · MTP 0.87 GB.
#
# `ctx` (1.1.3) is the native context window — 262144 across the whole
# Qwen3.5/3.8 family — carried here for the same reason `backend` is: an entry
# copied into models.json must be a COMPLETE roster shape, and the model menu's
# Details block reads `ctx` off the roster (asking the model server would mean
# waking it).  server.py's `_model_download_gb()` also reads `size_gb` /
# `draft_size_gb` out of this table by name, so a model that is not downloaded
# yet can still be quoted an estimate.
#
# All four are the same Qwen3.5/3.8 GatedDeltaNet family and share a chat
# template, so `backend: mlx_vlm` applies to every one of them for the reason
# the 9B seed entry documents: their tokenizer_config uses transformers-5's
# TokenizersBackend, which mlx-lm's pinned transformers<5 cannot load.
# --------------------------------------------------------------------------
_ONB_CATALOG = [
    {"id": "mlx-community/Qwen3.5-2B-4bit", "label": "Qwen3.5-2B",
     "ram": 3, "size_gb": 1.7, "ctx": 262144,
     "note": "smallest brain · chat and light tools · fastest",
     "thinking": True, "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm"},
    {"id": "mlx-community/Qwen3.5-4B-4bit", "label": "Qwen3.5-4B",
     "ram": 4, "size_gb": 3.1, "ctx": 262144,
     "note": "small brain · chat and everyday tools",
     "thinking": True, "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm"},
    {"id": "mlx-community/Qwen3.5-9B-4bit", "label": "Qwen3.5-9B",
     "ram": 7, "size_gb": 6.0, "ctx": 262144,
     "note": "background lane · news, scraping, briefings · fast",
     "role": "background", "thinking": True,
     "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm"},
    {"id": "mlx-community/Qwen3.8-27B-4bit", "label": "Qwen3.8-27B",
     "ram": 19, "size_gb": 16.1, "draft_size_gb": 0.9, "ctx": 262144,
     "note": "assistant brain · dense · MTP ~2x · Aug-2026",
     "thinking": True, "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm",
     "draft_model": "mlx-community/Qwen3.8-27B-MTP-bf16",
     "draft_kind": "mtp", "draft_block_size": 3},
]
_ONB_BY_ID = {m["id"]: m for m in _ONB_CATALOG}

M2B = "mlx-community/Qwen3.5-2B-4bit"
M4B = "mlx-community/Qwen3.5-4B-4bit"
M9B = "mlx-community/Qwen3.5-9B-4bit"
M27B = "mlx-community/Qwen3.8-27B-4bit"


def _onb_g(name, default=None):
    """Resolve a server.py / sibling-aux global BY NAME AT CALL TIME.

    aux files exec into one shared namespace in sorted order, so a name defined
    by aux_watchtower.py (which sorts after this file) does not exist while this
    module's body runs.  Every cross-module lookup therefore goes through here
    and degrades to `default` rather than raising — a missing watchtower must
    leave onboarding working with the notification rows simply unknown."""
    return globals().get(name, default)


def _onb_dl_gb(mid):
    """Total bytes-on-disk this model costs to download, in GB — the main repo
    PLUS its separate-repo drafter, because download_model() pulls both and the
    user is quoted one number.

    Round 3: the advisor can recommend a curated id that is not in this
    catalog (a server.py seed, or an _ADV_BY_ID shape). Those sizes come from
    server.py's _catalog_dl_gb (resolved by name: it reads the seeds and the
    advisor's shapes), so apply's disk check and its quoted size are not 0 for
    them. An id nobody knows the size of stays 0 here, and download_model()
    re-checks the disk itself."""
    m = _ONB_BY_ID.get(mid)
    if m:
        return float(m.get("size_gb") or 0) + float(m.get("draft_size_gb") or 0)
    cat = _onb_g("_catalog_dl_gb")
    if callable(cat):
        try:
            v = cat(mid)
        except Exception:
            v = None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
    return 0.0


# --------------------------------------------------------------------------
# system facts — pulled, never asked
# --------------------------------------------------------------------------
def _onb_run(argv, timeout=4):
    """One short command -> stripped stdout, or "" for anything that goes wrong.

    Calls the module-level name `subprocess` deliberately (not a local alias) so
    a harness can stub sysctl/sw_vers/pmset by assigning server.subprocess."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except Exception:
        pass
    return ""


def _onb_disk_free_gb(path=None):
    """Free space on the volume holding ~ in GB (GiB, matching _machine_ram_gb).

    f_bavail, not f_bfree: the blocks available to a NON-root process are what a
    huggingface download can actually use."""
    try:
        st = os.statvfs(path or HOME)
        return round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
    except Exception:
        return None


def _onb_sysinfo():
    chip = _onb_run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"])
    macos = _onb_run(["/usr/bin/sw_vers", "-productVersion"])
    batt = _onb_run(["/usr/bin/pmset", "-g", "batt"])
    ram = _onb_g("_machine_ram_gb", lambda: 0)()
    try:
        cores = os.cpu_count() or 0
    except Exception:
        cores = 0
    return {
        "chip": chip or "Apple silicon",
        # int GiB — the number Apple prints on the box, and the number every
        # tier boundary below is expressed in.
        "ram_gb": int(round(ram or 0)),
        "disk_free_gb": _onb_disk_free_gb(),
        "macos": macos or "",
        # pmset lists an InternalBattery source only on a portable Mac.
        "laptop": "InternalBattery" in (batt or ""),
        "cores": cores,
    }


# --------------------------------------------------------------------------
# what is already installed / connected
# --------------------------------------------------------------------------
def _onb_which(name, extra=()):
    try:
        p = shutil.which(name)
        if p:
            return p
    except Exception:
        pass
    for cand in extra:
        try:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        except OSError:
            pass
    return ""


def _onb_env_has(key):
    """Does ~/.hermes/.env define `key` with a NON-EMPTY value?

    Returns a BOOLEAN AND NOTHING ELSE.  The value is compared to "" inside this
    function and then goes out of scope; it is never returned, never logged and
    never stored.  That is the whole contract of this helper — the onboarding
    sheet needs to say "Telegram is configured", not what the token is."""
    try:
        with open(ONB_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return bool(v.strip().strip("'\""))
    except OSError:
        pass
    return False


def _onb_fda():
    """Full Disk Access, as the Message Center already knows it, or None.

    The dashboard's launchd python can never hold FDA itself (CLAUDE.md) — the
    signed app probes it with open(2) on chat.db and ships the verdict in its
    ingest payload, which aux_messages persists as `fda` in messages.json.  So
    the honest answers are True / False / None ("the app has not reported yet"),
    and None must render as "unknown", not as "denied"."""
    store_path = _onb_g("MSG_STORE")
    if not store_path:
        return None
    st = _onb_g("read_json", lambda p, d: d)(store_path, None)
    if not isinstance(st, dict) or "fda" not in st:
        return None
    return bool(st.get("fda"))


def _onb_detect():
    hermes = _onb_which("hermes", (os.path.join(HOME, ".local", "bin", "hermes"),))
    # Reuse the bridge's resolver first: it knows the nvm layout
    # (~/.nvm/versions/node/*/bin/claude) that launchd's PATH never sees —
    # the same binary the escalation feature actually runs.
    claude = None
    try:
        _cb = _onb_g("_cb_claude_bin")
        claude = _cb() if callable(_cb) else None
    except Exception:
        claude = None
    if not claude:
        claude = _onb_which("claude", (os.path.join(HOME, ".local", "bin", "claude"),
                                       os.path.join(HOME, ".claude", "local", "claude")))
    downloaded = []
    try:
        seen = set()
        for m in list(_onb_g("_model_registry", list)()) + _ONB_CATALOG:
            mid = m.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            if _onb_model_complete(mid):
                downloaded.append(mid)
    except Exception:
        pass
    return {
        "hermes_cli": bool(hermes), "hermes_path": hermes,
        "claude_cli": bool(claude),
        "mlx_venv": os.path.isdir(os.path.join(HOME, ".hermes", "mlx-vlm-venv", "bin")),
        "models_downloaded": downloaded,
        "fda": _onb_fda(),
        # booleans only — see _onb_env_has
        "telegram_configured": _onb_env_has("TELEGRAM_BOT_TOKEN"),
        "google_configured": os.path.isfile(
            _onb_g("GOOG_TOKEN") or os.path.join(HOME, ".hermes", "google_token.json")),
    }


# --------------------------------------------------------------------------
# the recommendation — PURE, so every tier is testable without the hardware
# --------------------------------------------------------------------------
def _onb_tier(ram_gb):
    """Which models suit a Mac with `ram_gb` GiB of RAM.

    PURE: no I/O, no globals, no roster lookup — give it a number, get a plan.
    That is what lets the harness assert 8/16/24/36/48/64/128 GB in one pass.

    THIS IS DELIBERATELY STRICTER THAN _model_fit, and the two answer different
    questions.  _model_fit asks "would this ONE model load on this Mac" (0.60 x
    RAM comfortable, 0.85 x RAM impossible), which is the right question for the
    model menu, where the user has already decided to go looking.  A first-run
    RECOMMENDATION has to budget for the whole running system instead: the
    primary PLUS an always-on background lane PLUS macOS PLUS the app PLUS a
    browser.  So the 4B reads "ok" on an 8 GB Air by _model_fit and is still not
    what we recommend there, and the 27B+9B pair is "tight" from 36-47 GB
    (26 GB resident of 36 is 72%) even though the 27B alone reads "ok" from
    32 GB up.  The alternatives list carries every model with its own fit badge,
    so a user who disagrees can see the honest per-model verdict and pick it.

    The boundaries, then, are about the PAIR:

      <16  minimal   2B only.  A 16 GB Air running a 4B primary has no headroom
                     left for the OS, the app and a browser, and no second lane
                     is even arguable — so: chat only, small context.
      16-23 small    4B primary, still no background lane (the briefing/news
                     work runs on the primary, which is what bg_lane() already
                     falls back to when :8081 is down).
      24-35 balanced 9B primary + 2B background — the first size where a second
                     resident model is affordable.
      36-47 large    27B primary but TIGHT (19 of 36-47 GB resident is real
                     pressure once the KV cache grows), 9B background.  The
                     battery alternative is a 9B primary + 2B background, which
                     is the whole roster under 10 GB resident.
      >=48  full     27B + 9B — today's shipping roster, measured on the M5 Max.
    """
    try:
        ram = float(ram_gb or 0)
    except (TypeError, ValueError):
        ram = 0.0

    if ram < 16:
        return {"tier": "minimal", "primary": M2B, "background": None,
                "tight": False, "alt": None,
                "why": "Under 16 GB there is room for one small brain and "
                       "nothing else — chat only, small context.",
                "note": "chat only, small context"}
    if ram < 24:
        return {"tier": "small", "primary": M4B, "background": None,
                "tight": False, "alt": None,
                "why": "A 4B assistant fits comfortably and leaves the Mac "
                       "usable. No background lane at this size — briefings run "
                       "on the same model.",
                "note": ""}
    if ram < 36:
        return {"tier": "balanced", "primary": M9B, "background": M2B,
                "tight": False, "alt": None,
                "why": "Enough RAM for a 9B assistant plus a small always-on "
                       "lane, so news and briefings never interrupt you.",
                "note": ""}
    if ram < 48:
        return {"tier": "large", "primary": M27B, "background": M9B,
                "tight": True,
                "alt": {"primary": M9B, "background": M2B,
                        "label": "Lighter on battery",
                        "why": "A 9B assistant with a 2B background lane keeps "
                               "the whole roster under 10 GB resident."},
                "why": "The 27B assistant will load, but 26 GB resident with "
                       "the background lane is tight here — expect swapping "
                       "under heavy use.",
                "note": "tight"}
    return {"tier": "full", "primary": M27B, "background": M9B,
            "tight": False, "alt": None,
            "why": "Room for the full roster: the 27B assistant stays warm "
                   "while a 9B lane handles news and briefings.",
            "note": ""}


def _onb_fits(ram_gb, downloaded=()):
    """Every catalog model judged against THIS Mac — reuses _model_fit (1.0.3)
    so the badges in the sheet are the same verdict the model menu shows."""
    fit = _onb_g("_model_fit", lambda r, m=None: None)
    out = []
    for m in _ONB_CATALOG:
        out.append({"id": m["id"], "label": m["label"], "ram": m["ram"],
                    "size_gb": round(_onb_dl_gb(m["id"]), 1),
                    "note": m.get("note", ""),
                    "role": m.get("role", "primary"),
                    "fit": fit(m.get("ram"), ram_gb),
                    "downloaded": m["id"] in (downloaded or ())})
    return out


_ONB_REC_KEYS = ("tier", "primary", "background", "tight", "alt", "why", "note",
                 "primary_label", "background_label", "download_gb", "total_gb",
                 "already_downloaded", "fits")


def _onb_recommendation(ram_gb, downloaded=()):
    """The models step's recommendation: the model advisor's, else the RAM tier.

    Round 3 (model-advisor D1): _adv_recommend_compat (aux_advisor.py, which
    sorts BEFORE this file but is resolved by name at call time like every
    other cross-module call here) answers in _onb_recommend's exact key set
    plus `advisor`, and falls back to _onb_recommend itself whenever it cannot
    answer. It is documented never to raise; this still guards against that
    and against a malformed answer, so an advisor bug can only ever cost the
    owner the RAM-only recommendation, never the sheet. _onb_tier is untouched
    and stays the fallback."""
    fn = _onb_g("_adv_recommend_compat")
    if callable(fn):
        try:
            rec = fn(ram_gb, list(downloaded or ()))
        except Exception as e:
            print("[onboarding] advisor recommendation failed (%s); using the RAM "
                  "tier" % type(e).__name__, file=sys.stderr, flush=True)
            rec = None
        if isinstance(rec, dict) and all(k in rec for k in _ONB_REC_KEYS) \
                and isinstance(rec.get("primary"), str) and rec["primary"] \
                and isinstance(rec.get("fits"), list):
            return rec
    return _onb_recommend(ram_gb, downloaded)


def _onb_recommend(ram_gb, downloaded=()):
    plan = _onb_tier(ram_gb)
    ids = [i for i in (plan["primary"], plan["background"]) if i]
    need = sum(_onb_dl_gb(i) for i in ids if i not in (downloaded or ()))
    return {**plan,
            "primary_label": (_ONB_BY_ID.get(plan["primary"]) or {}).get("label", ""),
            "background_label": (_ONB_BY_ID.get(plan["background"]) or {}).get("label", ""),
            "download_gb": round(need, 1),
            "total_gb": round(sum(_onb_dl_gb(i) for i in ids), 1),
            "already_downloaded": bool(ids) and not need,
            "fits": _onb_fits(ram_gb, downloaded)}


# --------------------------------------------------------------------------
# current preferences (read through the same helpers the panels use)
# --------------------------------------------------------------------------
def _onb_prefs():
    wt = {}
    try:
        loader = _onb_g("_wt_load")
        if callable(loader):
            wt = loader() or {}
    except Exception:
        wt = {}
    master = wt.get("master") if isinstance(wt.get("master"), dict) else {}
    quiet = wt.get("quiet_hours") if isinstance(wt.get("quiet_hours"), dict) else {}
    webr = wt.get("web_research") if isinstance(wt.get("web_research"), dict) else {}

    def _call(name, default):
        fn = _onb_g(name)
        try:
            return fn() if callable(fn) else default
        except Exception:
            return default

    return {
        # theme is a CLIENT-side preference (localStorage hermes_theme +
        # data-theme on <html>); the server has no opinion and stores nothing.
        "theme": None,
        "idle_min": _call("_idle_min", 10),
        "idle_enabled": _call("idle_suspend_enabled", True),
        "prewarm": _call("prewarm_enabled", True),
        # 2026-09-10 audit A02: an absent/unavailable helper must read as OFF,
        # not on — outbound inference is opt-in, so "we couldn't tell" must
        # never be presented to the sheet as "it's already enabled".
        "claude_escalation": _call("claude_escalation_enabled", False),
        # "lean" | "full" | "custom"; read straight off config.yaml, no
        # subprocess (aux_promptbudget.py, resolved by name at call time)
        "prompt_budget": _call("prompt_budget_profile", "full"),
        "briefings": bool(master.get("briefings", True)),
        "news": bool(master.get("news", True)),
        # C50 / R2-43: the unattended web research pass — explicit true only
        "web_research": webr.get("enabled") is True,
        "quiet_hours": {"start": quiet.get("start", "22:00"),
                        "end": quiet.get("end", "07:00")},
    }


# --------------------------------------------------------------------------
# the done marker
# --------------------------------------------------------------------------
def _onb_done_state():
    d = _onb_g("read_json", lambda p, x: x)(ONB_FILE, None)
    if isinstance(d, dict):
        return True, d.get("ts"), d.get("version")
    # a marker written by something other than us (a bare `touch`) still counts
    # as done — the sheet must not reopen just because the file has no JSON.
    return os.path.exists(ONB_FILE), None, None


# --------------------------------------------------------------------------
# GET /api/onboarding/state
# --------------------------------------------------------------------------
def _onb_primary_pending_rec():
    """(id, base) — base is active-model's content when the pick was made
    ("" = absent), None when the record does not carry one."""
    try:
        with open(ONB_PRIMARY_PENDING) as f:
            lines = f.read().split("\n")
    except OSError:
        return "", None
    mid = lines[0].strip()[:200]
    base = lines[1].strip()[:200] if len(lines) > 2 else None
    return mid, base


def _onb_primary_pending():
    return _onb_primary_pending_rec()[0]


def _onb_set_primary_pending(mid):
    """Record (or, with mid falsy, clear) the pending primary pick, with the
    active model at this moment as line 2. Best effort: a failed write only
    means the pick is neither offered nor promoted."""
    try:
        if not mid:
            os.remove(ONB_PRIMARY_PENDING)
            return
        tmp = ONB_PRIMARY_PENDING + ".tmp"
        with open(tmp, "w") as f:
            f.write(mid.strip() + "\n" + _onb_current_active() + "\n")
        os.replace(tmp, ONB_PRIMARY_PENDING)
    except OSError:
        pass


def _onb_primary_superseded(mid, base):
    """The owner has since made something else the active model (the menu),
    so the pending pick is void. Unknown base = not provable either way."""
    cur = _onb_current_active()
    return base is not None and cur != base and cur != mid


_onb_primary_lock = threading.Lock()
_onb_primary_watch_lock = threading.Lock()
_onb_primary_watching = [False]


def _onb_primary_promote():
    """One watcher step. True = nothing left to watch. Activates the pending
    pick only when it is COMPLETE on disk, active-model is unchanged since the
    pick and the model server is down (_onb_activate_primary: restarts
    nothing). A failed download never completes, so it never activates."""
    with _onb_primary_lock:
        mid, base = _onb_primary_pending_rec()
        if not mid:
            return True
        if _onb_current_active() == mid or _onb_primary_superseded(mid, base):
            _onb_set_primary_pending(None)
            return True
        if base is None:
            return True                  # the sheet's button only
        if not _onb_model_complete(mid):
            return False
        applied, errors = {}, []
        _onb_activate_primary(mid, applied, errors)
        if applied.get("primary_active") is True:
            _onb_set_primary_pending(None)
            print("[onboarding] %s is now the assistant model (its download "
                  "finished)" % mid, file=sys.stderr, flush=True)
            return True
        return False


def _onb_primary_watch_loop():
    try:
        while True:
            time.sleep(ONB_BG_WATCH_S)
            try:
                if _onb_primary_promote() is not False:
                    return
            except Exception:
                pass
    finally:
        with _onb_primary_watch_lock:
            _onb_primary_watching[0] = False


def _onb_primary_watch():
    """One daemon thread per process, only while a pick is pending."""
    with _onb_primary_watch_lock:
        if _onb_primary_watching[0]:
            return
        _onb_primary_watching[0] = True
    threading.Thread(target=_onb_primary_watch_loop, daemon=True,
                     name="onb-primary-pending").start()


def _onb_primary_pending_state():
    """{id, label, ready} for a pending pick, or None. A pick that is already
    the active model, or one the owner superseded in the model menu, is
    resolved and the record dropped. `ready` = weights and drafter complete."""
    mid, base = _onb_primary_pending_rec()
    if not mid:
        return None
    if _onb_current_active() == mid or _onb_primary_superseded(mid, base):
        _onb_set_primary_pending(None)
        return None
    try:
        label = _onb_label(mid)
    except Exception:
        label = mid.split("/")[-1]
    return {"id": mid, "label": label, "ready": _onb_model_complete(mid),
            "error": _onb_dl_error(mid)}


def _onb_dl_error(mid):
    """Why the last download of `mid` failed, or None (E22 leftover: a re-run
    of setup read a failed pending download as "still downloading"). The same
    text models_payload() ships as the row's `download_error`, read from
    server.py's _model_dl / _model_dl_err by name — not models_payload()
    itself, which probes the background lane's port."""
    try:
        state = (_onb_g("_model_dl") or {}).get(mid)
        if state != "error":
            return None
        why = (_onb_g("_model_dl_err") or {}).get(mid)
        why = str(why).strip() if why else ""
        return (why or "the download failed")[:600]
    except Exception:
        return None


def _onb_state_handler(ctx):
    done, done_at, ver = _onb_done_state()
    # ?brief=1 — the done marker only. The sheet's boot check and the Settings
    # row ask on every page load and need nothing else, and the full answer
    # now runs the model advisor's ranking (cached detection, but still the
    # logs/profile/drills reads), which only an open sheet uses.
    q = getattr(ctx, "query", None) or {}
    bq = q.get("brief") if isinstance(q, dict) else None
    if isinstance(bq, list):
        bq = bq[0] if bq else ""
    if str(bq or "").strip().lower() in ("1", "true", "yes"):
        return {"ok": True, "version": ONB_VERSION, "brief": True,
                "done": done, "done_at": done_at, "done_version": ver}
    sysinfo = _onb_sysinfo()
    detect = _onb_detect()
    return {"ok": True,
            "version": ONB_VERSION,
            "done": done, "done_at": done_at, "done_version": ver,
            "sysinfo": sysinfo,
            "detect": detect,
            "recommendation": _onb_recommendation(sysinfo["ram_gb"],
                                                  detect["models_downloaded"]),
            "primary_pending": _onb_primary_pending_state(),
            "prefs": _onb_prefs()}


# --------------------------------------------------------------------------
# POST /api/onboarding/apply
# --------------------------------------------------------------------------
_ONB_THEMES = ("system", "light", "dark")
_ONB_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _onb_ensure_roster(mid):
    """Make sure `mid` is in models.json with its FULL roster shape.

    Additive only — an existing entry (user-edited or seeded) is left exactly as
    it is, and nothing is ever removed.  This is the add_model() path with the
    catalog's real fields instead of add_model's `ram: None, note: "added"`
    stub, because a stub entry would download fine and then load on the WRONG
    backend (mlx-lm instead of mlx_vlm) with thinking left on."""
    entry_of = _onb_g("_model_entry", lambda _m: None)
    if entry_of(mid) is not None:
        return False
    # the onboarding catalog first, then the model advisor's curated shapes
    # (round 3, aux_advisor.py _ADV_BY_ID — full roster shapes too), the same
    # order server.py's add_model() uses. `verified` is evidence (a drill),
    # never a curated claim, and remote code is never curated: neither is
    # stored, exactly as add_model() drops them.
    cat = _ONB_BY_ID.get(mid) or (_onb_g("_ADV_BY_ID") or {}).get(mid)
    if not isinstance(cat, dict) or not cat:
        return False
    row = {k: v for k, v in cat.items()
           if k not in ("size_gb", "draft_size_gb", "verified", "trust_remote_code")}
    # E113: append under server.py's locked read-modify-write, re-checking the
    # id inside the lock (another writer may have added it since the look above)
    upd = _onb_g("models_update")
    if callable(upd):
        added = []

        def mut(reg):
            if any(isinstance(m, dict) and m.get("id") == mid for m in reg):
                return False
            reg.append(row)
            added.append(1)
        upd(mut)
        return bool(added)
    reg = _onb_g("_model_registry", list)()
    reg.append(row)
    _onb_g("write_json")(_onb_g("MODELS_FILE"), {"models": reg})
    return True


def _onb_model_complete(mid):
    """Fully local = the weights AND, for an entry that has one, its drafter —
    the definition the model menu's `downloaded` uses (_model_downloaded AND
    _draft_ready). C70: a 27B whose MTP drafter was missing read as already
    downloaded here, so onboarding never fetched the drafter (and the first
    wake then pulled it inside the model server)."""
    try:
        if not _onb_g("_model_downloaded", lambda _m: False)(mid):
            return False
        ready = _onb_g("_draft_ready")
        if not callable(ready):
            return True
        entry = _onb_g("_model_entry", lambda _m: None)(mid) or _onb_cat(mid)
        return bool(ready(entry))
    except Exception:
        return False


def _onb_current_active():
    """The active-model file's model id, "" when absent/unreadable."""
    try:
        with open(_onb_g("ACTIVE_MODEL_FILE") or os.path.join(DATA, "active-model")) as f:
            return f.read().strip()
    except OSError:
        return ""


def _onb_activate_primary(mid, applied, errors):
    """C63: make the onboarding pick the model the next start loads.

    Only when the primary model server is NOT running: model_online() false
    AND no model-server process alive (a server still loading answers
    nothing yet, and `_mlx_proc_alive` also counts the background lane —
    that only makes this more conservative). Unknown counts as running. The
    activation goes through the same all-or-nothing helper a switch uses
    (active-model + chat-template-args/server-backend + hermes
    model.default) and restarts nothing. While the server IS running, the
    pick stays a roster entry and the result says so."""
    if _onb_current_active() == mid:
        applied["primary_active"] = True
        return
    running = True
    try:
        online = _onb_g("model_online")
        alive = _onb_g("_mlx_proc_alive")
        running = bool((online() if callable(online) else True)
                       or (alive() if callable(alive) else True))
    except Exception:
        running = True
    if running:
        applied["primary_active"] = False
        applied["primary_note"] = ("the assistant model is running now — switch to "
                                   "it from the model menu to use it")
        return
    act = _onb_g("_activate_model")
    if not callable(act):
        applied["primary_active"] = False
        errors.append("primary: model activation unavailable")
        return
    try:
        ok, why = act(mid)
    except Exception as e:
        ok, why = False, type(e).__name__
    applied["primary_active"] = bool(ok)
    if not ok:
        errors.append("primary: " + str(why))


def _onb_known_model(mid):
    """A model id we are willing to act on: in the catalog, or already on the
    user's roster.  Anything else is refused — this route must never hand an
    arbitrary string to download_model()."""
    if mid in _ONB_BY_ID:
        return True
    # round 3: the model advisor's curated shapes (aux_advisor.py, by name)
    adv = _onb_g("_ADV_BY_ID") or {}
    if isinstance(adv, dict) and mid in adv:
        return True
    try:
        return _onb_g("_model_entry", lambda _m: None)(mid) is not None
    except Exception:
        return False


def _onb_cat(mid):
    """The static catalog shape of `mid`: this file's own catalog, else the
    model advisor's curated shapes (round 3, aux_advisor.py _ADV_BY_ID, by
    name). {} when neither knows it."""
    adv = _onb_g("_ADV_BY_ID") or {}
    for src in (_ONB_BY_ID, adv if isinstance(adv, dict) else {}):
        m = src.get(mid)
        if isinstance(m, dict) and m:
            return m
    return {}


def _onb_ram_of(mid):
    """Resident GB of a model: its roster entry's `ram`, else the catalog's
    (or the advisor's curated shape, which _onb_known_model also accepts — an
    id on neither list would otherwise skip the R2-41 fit check below).
    None when none says."""
    ram = None
    try:
        ram = (_onb_g("_model_entry", lambda _m: None)(mid) or {}).get("ram")
    except Exception:
        ram = None
    if isinstance(ram, bool) or not isinstance(ram, (int, float)) or ram <= 0:
        ram = _onb_cat(mid).get("ram")
    return float(ram) if isinstance(ram, (int, float)) and ram > 0 else None


def _onb_bg_misfit(bg, primary=None):
    """R2-41: the background lane is resident NEXT TO the primary, so its fit
    is judged on the two together, by the same _model_fit rule the model menu
    uses. The primary is this request's pick, else the active model. Returns
    the refusal reason when that is "no", else None — and None too when a
    size is unknown (the fail-open rule `fit` already follows)."""
    fit = _onb_g("_model_fit")
    rb = _onb_ram_of(bg)
    if not callable(fit) or not rb:
        return None
    if not primary:
        try:
            primary = (_onb_g("active_model", lambda: "")() or "").strip()
        except Exception:
            primary = ""
    rp = _onb_ram_of(primary) if primary else None
    total = rb + (rp or 0)
    try:
        verdict = fit(total)
    except Exception:
        return None
    if verdict != "no":
        return None
    try:
        machine = int(round(_onb_g("_machine_ram_gb", lambda: 0)() or 0))
    except Exception:
        machine = 0
    label = _onb_cat(bg).get("label") or bg
    plabel = _onb_cat(primary).get("label") or primary
    return ("%s does not fit next to %s on this Mac (needs ~%s GB together%s)"
            % (label, plabel or "the assistant model", int(round(total)),
               ", this Mac has %s GB" % machine if machine else ""))


def _onb_set_bg(mid):
    """Point the background lane at `mid` — write ~/.hermes/dashboard/bg-model,
    which is the one thing mlx-server-bg.sh and server.py's bg_model() both
    read.  Nothing else in the dashboard writes this file today."""
    path = os.path.join(DATA, "bg-model")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(mid.strip() + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# R2-42: never repoint bg-model under a running background lane.
#
# mlx-server-bg.sh reads bg-model once, at start, and the launcher pins the
# server to that model (C130): a request naming any other model gets a 400.
# bg_lane() hands every background producer bg_model() as the model id, so
# rewriting the file while :8081 serves the old model made the briefing,
# watchtower, For-You, Needs-you and dictation-cleanup calls all fail until
# the owner restarted the lane. Onboarding restarts no model server, so the
# pick is DEFERRED instead: kept in bg-model.pending, reported to the owner,
# and written to bg-model only once the lane is down (no answer on its port
# AND no lane process — a lane still loading has already read the file).
# --------------------------------------------------------------------------
def _onb_bg_url(path):
    base = _onb_g("BG_MODEL_URL") or "http://127.0.0.1:8081/v1/models"
    try:
        u = _onb_urlparse.urlsplit(base)
        return "%s://%s%s" % (u.scheme or "http", u.netloc, path)
    except ValueError:
        return "http://127.0.0.1:8081" + path


def _onb_bg_port():
    try:
        return _onb_urlparse.urlsplit(_onb_bg_url("/")).port or 8081
    except ValueError:
        return 8081


def _onb_bg_proc_alive():
    """A background-lane process is present: its launcher script (which reads
    bg-model) or the server it exec'd on the lane's port. pgrep; anything
    but a clean "no match" counts as alive, so an unknown never repoints."""
    pat = r"mlx-server-bg\.sh|mlx-vlm-launch\.py.*--port %d( |$)" % _onb_bg_port()
    try:
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True,
                           timeout=5)
    except Exception:
        return True
    if r.returncode == 1 and not (r.stdout or "").strip():
        return False
    return True


def _onb_bg_health_model():
    """The model the lane's server says it has loaded (GET /health,
    loaded_model — a read-only probe the launcher lets through without a
    key), or None when it does not say."""
    try:
        with _onb_urlreq.urlopen(_onb_bg_url("/health"), timeout=2) as r:
            d = json.loads(r.read(65536).decode("utf-8", "replace"))
    except Exception:
        return None
    m = d.get("loaded_model") if isinstance(d, dict) else None
    return m.strip() if isinstance(m, str) and m.strip() else None


def _onb_same_model(a, b):
    return (str(a or "").strip().rstrip("/") ==
            str(b or "").strip().rstrip("/"))


def _onb_bg_served():
    """(running, model): is the background lane up or starting, and which
    model it serves — /health's loaded_model, else the bg-model file it was
    started from. A FRESH probe, not bg_online()'s 15 s cache."""
    probe = _onb_g("_bg_probe")
    try:
        up = bool(probe()) if callable(probe) else False
    except Exception:
        up = False
    if not up and not _onb_bg_proc_alive():
        return False, None
    served = _onb_bg_health_model() if up else None
    if not served:
        try:
            served = (_onb_g("bg_model", lambda: "")() or "").strip()
        except Exception:
            served = ""
    return True, served or None


def _onb_bg_pending():
    try:
        with open(ONB_BG_PENDING) as f:
            return f.read().strip()
    except OSError:
        return ""


def _onb_bg_clear_pending():
    try:
        os.remove(ONB_BG_PENDING)
    except OSError:
        pass


# Serialises the pending file, bg-model and the watcher's decision to exit
# against an apply: without it the watcher could write a pick it read just
# before an apply wrote a newer one, or exit just after an apply deferred a
# pick (and saw a watcher still running), leaving that pick with none.
# Re-entrant: the watcher holds it around _onb_bg_promote().
_onb_bg_lock = threading.RLock()


def _onb_bg_promote():
    """Write a deferred pick to bg-model once the lane is down. None when
    nothing is pending, False while the lane still runs, True when done."""
    with _onb_bg_lock:
        mid = _onb_bg_pending()
        if not mid:
            _onb_bg_clear_pending()
            return None
        running, _served = _onb_bg_served()
        if running:
            return False
        _onb_set_bg(mid)
        _onb_bg_clear_pending()
    print("[onboarding] background lane is down: bg-model -> %s (deferred pick)"
          % mid, file=sys.stderr, flush=True)
    return True


_onb_bg_watch_lock = threading.Lock()
_onb_bg_watching = [False]


def _onb_bg_watch_loop():
    cleared = False
    try:
        while True:
            time.sleep(ONB_BG_WATCH_S)
            try:
                with _onb_bg_lock:
                    if _onb_bg_promote() is not False:
                        # cleared UNDER the lock: an apply that defers a pick
                        # from here on sees no watcher and starts one
                        with _onb_bg_watch_lock:
                            _onb_bg_watching[0] = False
                        cleared = True
                        return
            except Exception:
                pass
    finally:
        if not cleared:                  # never clobber a successor's flag
            with _onb_bg_watch_lock:
                _onb_bg_watching[0] = False


def _onb_bg_watch():
    """One daemon thread per process, only while a pick is pending."""
    with _onb_bg_watch_lock:
        if _onb_bg_watching[0]:
            return
        _onb_bg_watching[0] = True
    threading.Thread(target=_onb_bg_watch_loop, daemon=True,
                     name="onb-bg-pending").start()


def _onb_apply_bg(mid, applied, errors):
    """Point the background lane at `mid` now, or defer it (R2-42)."""
    with _onb_bg_lock:
        _onb_apply_bg_locked(mid, applied, errors)


def _onb_apply_bg_locked(mid, applied, errors):
    try:
        running, served = _onb_bg_served()
    except Exception:
        running, served = True, None
    if running and not _onb_same_model(served, mid):
        try:
            tmp = ONB_BG_PENDING + ".tmp"
            with open(tmp, "w") as f:
                f.write(mid.strip() + "\n")
            os.replace(tmp, ONB_BG_PENDING)
        except OSError as e:
            errors.append("bg-model: " + str(e))
            return
        _onb_bg_watch()
        cur = _onb_cat(served).get("label") or served or "another model"
        new = _onb_cat(mid).get("label") or mid
        applied["background_lane"] = served
        applied["background_pending"] = mid
        applied["background_note"] = (
            "the background lane is running %s right now — %s takes over once "
            "that lane has stopped, and background work keeps using %s until then"
            % (cur, new, cur))
        return
    try:
        _onb_set_bg(mid)
        _onb_bg_clear_pending()          # this pick supersedes a deferred one
        applied["background_lane"] = mid
    except OSError as e:
        errors.append("bg-model: " + str(e))


def _onb_wt_op(op, payload):
    """Run one watchtower op through ITS OWN handler, so master toggles and
    quiet hours go through the same validation, clamping and file lock the
    Watchtower card uses.  Returns (ok, detail)."""
    fn = _onb_g("watchtower_post_handler")
    ctxcls = _onb_g("RouteCtx")
    if not callable(fn) or ctxcls is None:
        return False, "watchtower not loaded"
    try:
        r = fn(ctxcls(body={"op": op, **payload}))
        if isinstance(r, tuple):
            r = r[0]
        if isinstance(r, dict) and r.get("ok"):
            return True, r
        return False, (r or {}).get("error", "refused")
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# --------------------------------------------------------------------------
# Round 3: the uncensored acknowledgement gate (model-advisor owner decision:
# uncensored builds are alternatives only, behind an acknowledgement).
#
# POST /api/models/switch already refuses an uncensored target without one
# (server.py's switch-ack refusal helper). apply writes the same two things
# a switch leads to — the roster + active model for the primary, bg-model for the
# background lane (the advisor card's "Use as background lane" posts
# {background: id} here) — so it refuses the same way, BEFORE anything is
# applied: one 409 for the whole request, no field written, no download.
# Open when the request carries ack_uncensored: true (a strict JSON boolean;
# anything else present is a 400, null included, as on the switch route) or
# when settings.json advisor.uncensored_ack is exactly true (the stored
# acknowledgement, written only by POST /api/advisor/answers). Fails CLOSED:
# a predicate that raises, or no predicate at all, counts as uncensored.
# The test is the switch gate's own sources (the curated seeds through
# _roster_uncensored, the advisor's facts and shapes through
# _adv_is_uncensored), read here directly rather than through server.py's
# switch-ack refusal helper: its answer is the switch route's 409 body, and
# t_advisor_switch_gate pins server.py as that helper's only user.
# --------------------------------------------------------------------------
def _onb_uncensored(mid):
    """True / False — or None when no uncensored predicate resolves.

    The union of server.py's _roster_uncensored (sticky to the curated seed,
    so a hand edit cannot relabel an abliterated build) and the advisor's
    _adv_is_uncensored (facts + its curated shapes); both by name, at call
    time. Any exception reads as True."""
    try:
        row = _onb_g("_model_entry", lambda _m: None)(mid)
    except Exception:
        row = None
    if not isinstance(row, dict):
        row = dict(_ONB_BY_ID.get(mid) or {"id": mid})
    if row.get("id") is None:
        row = dict(row, id=mid)
    seen = False
    for name in ("_roster_uncensored", "_adv_is_uncensored"):
        fn = _onb_g(name)
        if not callable(fn):
            continue
        seen = True
        try:
            v = fn(row) if name == "_roster_uncensored" else fn(mid, row)
        except Exception:
            return True
        if v:
            return True
    return False if seen else None


def _onb_stored_ack():
    """settings.json advisor.uncensored_ack is exactly true. A missing or
    corrupt file (get_settings reads either as {}) means no acknowledgement."""
    gs = _onb_g("get_settings")
    try:
        s = gs() if callable(gs) else {}
    except Exception:
        s = {}
    adv = s.get("advisor") if isinstance(s, dict) else None
    return isinstance(adv, dict) and adv.get("uncensored_ack") is True


def _onb_label(mid):
    try:
        ent = _onb_g("_model_entry", lambda _m: None)(mid) or {}
    except Exception:
        ent = {}
    lbl = ent.get("label") if isinstance(ent, dict) else None
    if not (isinstance(lbl, str) and lbl.strip()):
        lbl = (_ONB_BY_ID.get(mid) or (_onb_g("_ADV_BY_ID") or {}).get(mid) or {}).get("label")
    if not (isinstance(lbl, str) and lbl.strip()):
        lbl = str(mid).split("/")[-1]
    return re.sub(r"[\x00-\x1f\x7f]", "", lbl).strip()[:80]


def _onb_ack_gate(b):
    """None when the request may proceed, else (body, status) with NOTHING
    applied. `b` is the request body (a dict)."""
    if "ack_uncensored" in b and not isinstance(b["ack_uncensored"], bool):
        return ({"ok": False, "error": "bad_ack",
                 "errors": ["ack_uncensored must be a boolean"], "applied": {}}, 400)
    if b.get("ack_uncensored") is True:
        return None
    picks = []
    for role in ("primary", "background"):
        v = b.get(role)
        if isinstance(v, str) and v.strip():
            picks.append((role, v.strip()))
    if not picks:
        return None
    refused, stored = [], None
    for role, mid in picks:
        unc = _onb_uncensored(mid)
        if unc is None:
            unc = True                    # nothing could tell: fail closed
        if not unc:
            continue
        if stored is None:
            stored = _onb_stored_ack()
        if stored:
            continue
        refused.append((role, mid))
    if not refused:
        return None
    role, mid = refused[0]
    body = {"ok": False, "needs_ack": True, "error": "uncensored_ack", "id": mid,
            "role": role, "label": _onb_label(mid),
            "errors": ["%s is an uncensored model. Acknowledge it first." % _onb_label(m)
                       for _r, m in refused],
            "applied": {}}
    text = _onb_g("_ADVR_UNCENSORED_ACK_TEXT")
    if isinstance(text, str) and text:
        body["ack_text"] = text
    return body, 409


def _onb_apply_handler(ctx):
    b = ctx.body if isinstance(ctx.body, dict) else {}
    # round 3: an uncensored pick without the acknowledgement refuses the
    # WHOLE request before a single field is written (see _onb_ack_gate)
    gate = _onb_ack_gate(b)
    if gate is not None:
        return gate
    applied, errors = {}, []

    # --- theme: validated, echoed, NOT persisted server-side ----------------
    if "theme" in b and b["theme"] is not None:
        t = str(b["theme"]).strip().lower()
        if t in _ONB_THEMES:
            applied["theme"] = t          # the client owns localStorage
        else:
            errors.append("theme must be one of " + "/".join(_ONB_THEMES))

    # --- idle suspend: null / "never" = off, else 1..240 minutes ------------
    if "idle_min" in b:
        v = b["idle_min"]
        off = v is None or (isinstance(v, str) and v.strip().lower() == "never")
        if off:
            try:
                open(_onb_g("IDLE_SUSPEND_OFF"), "w").close()
                applied["idle_min"] = None
            except OSError as e:
                errors.append("idle: " + str(e))
        else:
            mins = None
            # R2-41: a JSON boolean is not a number of minutes — float(True)
            # is 1.0, which used to become a one-minute idle-suspend
            if not isinstance(v, bool):
                try:
                    mins = float(v)
                except (TypeError, ValueError):
                    mins = None
            if mins is None or not (1 <= mins <= 240):
                errors.append("idle_min must be null/'never' or 1-240 minutes")
            else:
                try:
                    offf = _onb_g("IDLE_SUSPEND_OFF")
                    if os.path.exists(offf):
                        os.remove(offf)
                    with open(_onb_g("IDLE_MIN_FILE"), "w") as f:
                        f.write(str(mins))
                    applied["idle_min"] = mins
                except OSError as e:
                    errors.append("idle: " + str(e))

    # --- prewarm ------------------------------------------------------------
    if "prewarm" in b:
        if not isinstance(b["prewarm"], bool):
            errors.append("prewarm must be a boolean")
        else:
            try:
                _onb_g("set_prewarm_enabled")(b["prewarm"])
                applied["prewarm"] = b["prewarm"]
            except Exception as e:
                errors.append("prewarm: %s: %s" % (type(e).__name__, e))

    # --- prompt budget (toolset diet) ---------------------------------------
    # A NEW install should start lean: every fresh conversation prefills the
    # tool schemas, and on the recommended models that is ~2k tokens and ~3
    # seconds of first-token wait for tools a personal assistant does not use
    # (browser automation, speech, image/video generation, the chat-platform
    # tools).  Full is one click away in Settings > Agent & Models.  The setter
    # lives in aux_promptbudget.py, which sorts AFTER this file, so it is
    # resolved by name at request time like every other cross-module call here;
    # a no-op value writes nothing at all.
    if "prompt_budget" in b:
        fn = _onb_g("set_prompt_budget_profile")
        if not callable(fn):
            errors.append("prompt_budget: aux_promptbudget.py is not loaded")
        elif b["prompt_budget"] not in ("lean", "focused", "full"):
            errors.append("prompt_budget must be \"lean\" (Balanced), "
                          "\"focused\" or \"full\"")
        else:
            try:
                applied["prompt_budget"] = b["prompt_budget"]
                applied["prompt_budget_changed"] = bool(fn(b["prompt_budget"]))
            except Exception as e:
                errors.append("prompt_budget: %s: %s" % (type(e).__name__, e))

    # --- Claude escalation master switch ------------------------------------
    if "claude_escalation" in b:
        if not isinstance(b["claude_escalation"], bool):
            errors.append("claude_escalation must be a boolean")
        else:
            setter = _onb_g("_cb_set_escalation")
            if not callable(setter):
                errors.append("claude bridge not loaded")
            else:
                try:
                    applied["claude_escalation"] = setter(b["claude_escalation"])
                except Exception as e:
                    errors.append("claude_escalation: %s" % type(e).__name__)

    # --- notification masters (one op sets both) ----------------------------
    masters = {}
    for key in ("briefings", "news"):
        if key in b:
            if not isinstance(b[key], bool):
                errors.append(key + " must be a boolean")
            else:
                masters[key] = b[key]
    if masters:
        ok, detail = _onb_wt_op("set_master", masters)
        if ok:
            applied.update(masters)
        else:
            errors.append("masters: " + str(detail))

    # --- agent web research (R2-43): watchtower's own op, explicit boolean ---
    if "web_research" in b:
        if not isinstance(b["web_research"], bool):
            errors.append("web_research must be a boolean")
        else:
            ok, detail = _onb_wt_op("set_web_research", {"enabled": b["web_research"]})
            if ok:
                applied["web_research"] = ((detail or {}).get("web_research") or {}) \
                    .get("enabled") is True
            else:
                errors.append("web_research: " + str(detail))

    # --- quiet hours --------------------------------------------------------
    if "quiet_hours" in b and b["quiet_hours"] is not None:
        qh = b["quiet_hours"]
        # R2-41: a list or a string has no .get — that raised AttributeError,
        # a 500 after every field above had already been applied
        start = str(qh.get("start", "")).strip() if isinstance(qh, dict) else ""
        end = str(qh.get("end", "")).strip() if isinstance(qh, dict) else ""
        if not isinstance(qh, dict):
            errors.append("quiet_hours must be an object with start/end as HH:MM (24h)")
        elif not (_ONB_HHMM.match(start) and _ONB_HHMM.match(end)):
            errors.append("quiet_hours needs start/end as HH:MM (24h)")
        else:
            ok, detail = _onb_wt_op("set_quiet_hours", {"start": start, "end": end})
            if ok:
                applied["quiet_hours"] = {"start": start, "end": end}
            else:
                errors.append("quiet_hours: " + str(detail))

    # --- models -------------------------------------------------------------
    # Roster membership first (additive, full shape), THEN the optional
    # download.  A background choice writes bg-model.  A primary choice never
    # RESTARTS the model server (onboarding must not do that behind the
    # user's back), but when the server is not running — asleep, paused or
    # never started, the normal first-run state — making the pick the active
    # model restarts nothing, so it is done (C63; before, the pick reached
    # only models.json and the first wake loaded the hardcoded 27B default).
    primary = (b.get("primary") or "").strip() if isinstance(b.get("primary"), str) else ""
    background = b.get("background")
    if background is not None and not isinstance(background, str):
        errors.append("background must be a model id or null")
        background = None
    background = (background or "").strip()

    want = []
    for mid, role in ((primary, "primary"), (background, "background")):
        if not mid:
            continue
        if not _onb_known_model(mid):
            errors.append("unknown model: " + mid[:120])
            continue
        if role == "background":
            why = _onb_bg_misfit(mid, applied.get("primary"))
            if why:
                errors.append("background: " + why)
                continue
        try:
            _onb_ensure_roster(mid)
        except Exception as e:
            errors.append("roster %s: %s" % (role, type(e).__name__))
            continue
        applied[role] = mid
        want.append(mid)

    # --- the download -------------------------------------------------------
    download = b.get("download")
    if download is not None and not isinstance(download, bool):
        errors.append("download must be a boolean")
        download = False
    if download and want:
        pending = [m for m in want if not _onb_model_complete(m)]
        need = round(sum(_onb_dl_gb(m) for m in pending), 1)
        free = _onb_disk_free_gb()
        if not pending:
            applied["download"] = []
            applied["download_note"] = "already downloaded"
        elif free is not None and free < need + ONB_DISK_HEADROOM_GB:
            # REFUSE rather than start a multi-GB pull that fills the disk.
            errors.append(
                "not enough free disk: %s GB needed (+%s GB headroom) but only "
                "%s GB free" % (need, int(ONB_DISK_HEADROOM_GB), free))
            applied["download"] = []
        else:
            started = []
            dl = _onb_g("download_model")
            for mid in pending:
                try:
                    r = dl(mid) or {}
                    if r.get("ok"):
                        started.append(mid)
                    else:
                        errors.append("download %s: %s" % (mid, r.get("error", "refused")))
                except Exception as e:
                    errors.append("download %s: %s" % (mid, type(e).__name__))
            applied["download"] = started
            applied["download_gb"] = need

    # E111: on a download request, the background lane is repointed only at a
    # model that is complete on disk or whose download really started. A
    # refused (disk) or failed start would otherwise leave bg-model naming a
    # model the lane then pulls itself, past the disk check just refused. An
    # explicit lane choice with no download asked keeps its old behaviour (the
    # advisor card warns before it posts one).
    if applied.get("background"):
        bmid = applied["background"]
        if (download and not _onb_model_complete(bmid)
                and bmid not in (applied.get("download") or [])):
            applied.pop("background", None)
            applied["background_note"] = (
                "%s was not downloaded, so the background lane was not changed"
                % _onb_label(bmid))
        else:
            _onb_apply_bg(bmid, applied, errors)

    # Activate the primary only once it is fully local (weights AND drafter).
    # A pick whose download was refused — not enough disk — or never asked for
    # would make the next wake pull it INSIDE the model server, past that very
    # disk check (the C63 failure, one model smaller). E22: so would one whose
    # download only STARTED, if that download then fails — so a started pick
    # is recorded as pending instead, and "Make it the assistant" (the sheet,
    # once /api/models says it is downloaded) is a second apply.
    if applied.get("primary"):
        pmid = applied["primary"]
        if _onb_model_complete(pmid):
            _onb_activate_primary(pmid, applied, errors)
            _onb_set_primary_pending(None)
        elif _onb_current_active() == pmid:
            applied["primary_active"] = True
            _onb_set_primary_pending(None)
        elif pmid in (applied.get("download") or []):
            applied["primary_active"] = False
            applied["primary_pending"] = True
            applied["primary_note"] = ("downloading — it becomes the assistant once "
                                       "the download finishes and the model is not "
                                       "running, or switch to it from the model menu")
            _onb_set_primary_pending(pmid)
            _onb_primary_watch()
        else:
            applied["primary_active"] = False
            applied["primary_note"] = ("not downloaded yet — download it, then "
                                       "switch to it from the model menu")
            _onb_set_primary_pending(None)

    return {"ok": not errors, "applied": applied, "errors": errors,
            "prefs": _onb_prefs()}


# --------------------------------------------------------------------------
# POST /api/onboarding/done  |  POST /api/onboarding/reset
# --------------------------------------------------------------------------
def _onb_done_handler(ctx):
    payload = {"version": ONB_VERSION, "ts": time.time(),
               "at": _onb_datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        _onb_g("write_json")(ONB_FILE, payload)
    except OSError as e:
        return ({"ok": False, "error": str(e)}, 500)
    return {"ok": True, "done": True, **payload}


def _onb_reset_handler(ctx):
    try:
        if os.path.exists(ONB_FILE):
            os.remove(ONB_FILE)
    except OSError as e:
        return ({"ok": False, "error": str(e)}, 500)
    return {"ok": True, "done": False}


# a pick deferred before a dashboard restart is still waiting for its lane
if os.path.exists(ONB_BG_PENDING):
    _onb_bg_watch()
# ...and so is a primary pick whose download finished after a restart
if os.path.exists(ONB_PRIMARY_PENDING):
    _onb_primary_watch()

register_get("/api/onboarding/state", _onb_state_handler)
register_post("/api/onboarding/apply", _onb_apply_handler)
register_post("/api/onboarding/done", _onb_done_handler)
register_post("/api/onboarding/reset", _onb_reset_handler)
