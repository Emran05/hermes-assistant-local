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

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
ONB_FILE = os.path.join(DATA, "onboarded")          # {version, ts} once done
ONB_VERSION = "1.1.1"
ONB_ENV_FILE = os.path.join(HOME, ".hermes", ".env")
ONB_DISK_HEADROOM_GB = 5.0      # refuse a download that would leave less free
ONB_IDLE_CHOICES = (5, 10, 20)  # the sheet's radio row; None/"never" = off


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
    user is quoted one number."""
    m = _ONB_BY_ID.get(mid) or {}
    return float(m.get("size_gb") or 0) + float(m.get("draft_size_gb") or 0)


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
        dl = _onb_g("_model_downloaded", lambda _m: False)
        seen = set()
        for m in list(_onb_g("_model_registry", list)()) + _ONB_CATALOG:
            mid = m.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            if dl(mid):
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
def _onb_state_handler(ctx):
    done, done_at, ver = _onb_done_state()
    sysinfo = _onb_sysinfo()
    detect = _onb_detect()
    return {"ok": True,
            "version": ONB_VERSION,
            "done": done, "done_at": done_at, "done_version": ver,
            "sysinfo": sysinfo,
            "detect": detect,
            "recommendation": _onb_recommend(sysinfo["ram_gb"],
                                             detect["models_downloaded"]),
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
    cat = _ONB_BY_ID.get(mid)
    if not cat:
        return False
    row = {k: v for k, v in cat.items() if k not in ("size_gb", "draft_size_gb")}
    reg = _onb_g("_model_registry", list)()
    reg.append(row)
    _onb_g("write_json")(_onb_g("MODELS_FILE"), {"models": reg})
    return True


def _onb_known_model(mid):
    """A model id we are willing to act on: in the catalog, or already on the
    user's roster.  Anything else is refused — this route must never hand an
    arbitrary string to download_model()."""
    if mid in _ONB_BY_ID:
        return True
    try:
        return _onb_g("_model_entry", lambda _m: None)(mid) is not None
    except Exception:
        return False


def _onb_set_bg(mid):
    """Point the background lane at `mid` — write ~/.hermes/dashboard/bg-model,
    which is the one thing mlx-server-bg.sh and server.py's bg_model() both
    read.  Nothing else in the dashboard writes this file today."""
    path = os.path.join(DATA, "bg-model")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(mid.strip() + "\n")
    os.replace(tmp, path)


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


def _onb_apply_handler(ctx):
    b = ctx.body if isinstance(ctx.body, dict) else {}
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

    # --- quiet hours --------------------------------------------------------
    if "quiet_hours" in b and b["quiet_hours"] is not None:
        qh = b["quiet_hours"]
        start = str((qh or {}).get("start", "")).strip()
        end = str((qh or {}).get("end", "")).strip()
        if not (_ONB_HHMM.match(start) and _ONB_HHMM.match(end)):
            errors.append("quiet_hours needs start/end as HH:MM (24h)")
        else:
            ok, detail = _onb_wt_op("set_quiet_hours", {"start": start, "end": end})
            if ok:
                applied["quiet_hours"] = {"start": start, "end": end}
            else:
                errors.append("quiet_hours: " + str(detail))

    # --- models -------------------------------------------------------------
    # Roster membership first (additive, full shape), THEN the optional
    # download.  A background choice writes bg-model; a primary choice does NOT
    # switch the running model — switching restarts the model server, and
    # onboarding must never do that behind the user's back.  It becomes the
    # default on the next start via the roster + the model menu.
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
        try:
            _onb_ensure_roster(mid)
        except Exception as e:
            errors.append("roster %s: %s" % (role, type(e).__name__))
            continue
        applied[role] = mid
        want.append(mid)

    if applied.get("background"):
        try:
            _onb_set_bg(applied["background"])
            applied["background_lane"] = applied["background"]
        except OSError as e:
            errors.append("bg-model: " + str(e))

    # --- the download -------------------------------------------------------
    download = b.get("download")
    if download is not None and not isinstance(download, bool):
        errors.append("download must be a boolean")
        download = False
    if download and want:
        dl_check = _onb_g("_model_downloaded", lambda _m: False)
        pending = [m for m in want if not dl_check(m)]
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


register_get("/api/onboarding/state", _onb_state_handler)
register_post("/api/onboarding/apply", _onb_apply_handler)
register_post("/api/onboarding/done", _onb_done_handler)
register_post("/api/onboarding/reset", _onb_reset_handler)
