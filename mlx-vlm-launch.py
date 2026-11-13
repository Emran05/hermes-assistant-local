#!/usr/bin/env python3
"""mlx-vlm-launch.py — start `mlx_vlm.server` (OpenAI-compatible) for models
whose roster entry sets backend "mlx_vlm" (today: Qwen3.8-27B + its native MTP
speculative drafter, ~2x decode). Run with the ISOLATED venv interpreter
~/.hermes/mlx-vlm-venv/bin/python (never the framework Python — mlx-vlm needs
mlx>=0.32 + transformers 5, which would break the mlx-lm install).

Why a launcher and not `python -m mlx_vlm.server` directly: mlx-vlm 0.6.14 +
mlx 0.32.x crash on any sampled (temperature>0) speculative request —
`_restore_rng_state` does `mx.random.state[i] = v` but mlx 0.32 made
`mx.random.state` a read-only sentinel (no __setitem__, no public key setter).
The save/restore only keeps the drafter's RNG stream separate from the
target's; skipping it changes which random numbers get drawn, not the
correctness of speculative rejection sampling. So we make the restore a no-op
when the runtime forbids it. Drop this shim once upstream fixes it
(mlx-vlm speculative/common.py) — the patch is a pure no-op then.

Lockdown (2026-10-21 audit C130). Upstream answers EVERY request with
whatever model it names (`get_cached_model(request.model)`, a Hugging Face
download when it is not cached), installs CORS `*` with credentials, and
leaves /unload, /cache/reset, PATCH /settings (which can swap the drafter) and
/v1/realtime (its own loader) open — so any local process, container or web
page that reached :8080/:8081 could make the server pull and load another
repo, or drop the 19GB model. This launcher now:
  * pins the server to the `--model` it was started with — any other request
    model (or adapter, or a non-text model kind) is a 400 before anything is
    loaded, and the snapshot-path spelling of the same model is mapped back to
    the launch id so it cannot force a reload;
  * removes the CORS middleware and refuses any request that carries an
    Origin header (no browser is a legitimate client of the model server) or
    a Host that is not loopback (DNS rebinding);
  * refuses the mutating management routes and the realtime socket;
  * requires `Authorization: Bearer <key>` on every route except the
    read-only liveness probes (GET /health, /v1/models) when the owner has
    created ~/.hermes/dashboard/model-api-key (0600; never created here) or
    exported MLX_VLM_SERVER_API_KEY. The dashboard stamps the same header on
    its own direct calls; Hermes reads it from config.yaml's api_key.
The same file fronts the mlx-lm fallback (`--mlx-lm` as the first argument,
run by the framework python), which has the same request-named-model hole.
A guard that cannot be installed stops the server instead of starting it
unguarded.

Offline start (2026-10-21 audit C49). `--local-ready <model> [<drafter>]`
answers whether those weights are fully on disk; the shell launchers then
export HF_HUB_OFFLINE=1 + MLX_VLM_LOCAL_ONLY=1 so a start never revalidates
the repo at huggingface.co (or silently pulls an upstream change). Updates
come only from the dashboard's explicit download. An incomplete snapshot (a
download still in flight, or never downloaded) keeps the online path.

Roster fields (round 3, docs/plans/abliterated-models-2026-10.md). Two env
vars, exported by mlx-server.sh / mlx-server-bg.sh from the model's roster row
and validated HERE again (the launcher trusts no file it did not write):
  * MLX_VLM_MODEL_SUBFOLDER — the weights live in a repo SUBFOLDER (e.g.
    PocketAiHub's `4bit/`). The launch id then resolves to
    <snapshot>/<subfolder> for the loader (get_model_path / mlx-lm _download),
    for --local-ready, and for the C130 pin's aliases. That id NEVER falls
    through to a Hub download: a missing subfolder is a load error, not a
    134 GB snapshot_download of every variant. An invalid value refuses to
    start. Without the env (a launch by tools/bench/* or by hand) the model's
    own ~/.hermes/dashboard/models.json row is read for it, and a subfolder or
    `hf_offline: true` row starts offline whoever launched it.
  * MLX_VLM_SAMPLING_DEFAULTS — a JSON object of request defaults
    (temperature, top_p, top_k, min_p, repetition_penalty,
    repetition_context_size, presence_penalty, frequency_penalty). Applied to a
    request ONLY when the request did not set that field itself (an explicit
    value, null included, always wins). Why server-side: Hermes 0.18 can send
    per-model sampling (a custom provider's `extra_body`, matched by base_url
    + model), but only on Hermes's own turns — not on the dashboard's direct
    model calls, the tool drill among them — and only by rewriting Hermes's
    config.yaml on every switch. The server is pinned to one model, so the
    model's defaults live with it; a Hermes extra_body value still wins,
    because it sets the field on the request.
"""
import atexit
import hmac
import json
import os
import re
import runpy
import sys


def _patch_rng_restore():
    try:
        import mlx.core as mx
        from mlx_vlm.speculative import common
    except Exception:
        return
    try:                                   # does this mlx allow item assignment?
        st = list(mx.random.state)
        mx.random.state[0] = st[0]
        return                             # yes → upstream code is fine
    except TypeError:
        pass
    except Exception:
        return

    def _restore_noop(state):
        return None

    common._restore_rng_state = _restore_noop
    print("[mlx-vlm-launch] mlx>=0.32 read-only random.state — RNG restore "
          "patched to no-op (sampled speculative decoding works)", flush=True)


def _hub_cache_dir():
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")


def _hf_snapshot(repo_id):
    """The cached snapshot dir of an HF repo id, or None. refs/main first, the
    newest snapshot dir only as a fallback — the same order server.py's
    `_hf_snapshot_dir` uses, so the dashboard and the loader agree on which
    checkout "the" model is. A ref that is not one plain path segment is
    ignored rather than followed out of snapshots/."""
    d = os.path.join(_hub_cache_dir(), "models--" + str(repo_id).replace("/", "--"))
    snaps = os.path.join(d, "snapshots")
    try:
        sha = open(os.path.join(d, "refs", "main")).read().strip()
        if sha and "/" not in sha and ".." not in sha and \
                os.path.isdir(os.path.join(snaps, sha)):
            return os.path.join(snaps, sha)
    except OSError:
        pass
    try:                                   # no ref → newest snapshot dir
        cands = [os.path.join(snaps, x) for x in os.listdir(snaps)]
        cands = [c for c in cands if os.path.isdir(c)]
        return max(cands, key=os.path.getmtime) if cands else None
    except OSError:
        return None


# --------------------------------------------------------------------------
# Round-3 roster fields — pure stdlib, so --local-ready and the tests run them
# without the venv. The bounds mirror server.py's _ROSTER_SAMPLING_BOUNDS and
# the subfolder rule its _roster_subfolder (change both or neither).
# --------------------------------------------------------------------------
_SUBFOLDER_ENV = "MLX_VLM_MODEL_SUBFOLDER"
_SAMPLING_ENV = "MLX_VLM_SAMPLING_DEFAULTS"
_SUBFOLDER_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,3}$")
_SAMPLING_BOUNDS = {
    "temperature": (float, 0.0, 2.0),
    "top_p": (float, 0.0, 1.0),
    "top_k": (int, 0, 1000),
    "min_p": (float, 0.0, 1.0),
    "repetition_penalty": (float, 1.0, 2.0),
    "repetition_context_size": (int, 1, 4096),
    "presence_penalty": (float, -2.0, 2.0),
    "frequency_penalty": (float, -2.0, 2.0),
}


def _clean_subfolder(v):
    """A clean repo-relative subfolder or "" (never absolute, never "." or
    "..", never a backslash)."""
    if not isinstance(v, str):
        return ""
    s = v.strip().strip("/")
    if not s or len(s) > 64 or not _SUBFOLDER_RE.fullmatch(s):
        return ""
    if any(p in (".", "..") for p in s.split("/")):
        return ""
    return s


def _model_subfolder(env=None):
    """(subfolder, error) from MLX_VLM_MODEL_SUBFOLDER. ("", None) when unset;
    ("", reason) when set but invalid — the caller refuses to start rather than
    load the snapshot root of a multi-variant repo."""
    raw = (os.environ if env is None else env).get(_SUBFOLDER_ENV, "")
    if not str(raw).strip():
        return "", None
    sub = _clean_subfolder(raw)
    if not sub:
        return "", "invalid %s=%r" % (_SUBFOLDER_ENV, str(raw)[:80])
    return sub, None


def _roster_path():
    return os.environ.get("HERMES_MODELS_FILE") or os.path.join(
        os.path.expanduser("~"), ".hermes", "dashboard", "models.json")


def _roster_row(model):
    """The owner's models.json row for `model`, or {} (absent, unreadable or
    not an object — never an error)."""
    try:
        with open(_roster_path()) as f:
            ms = json.load(f).get("models") or []
    except Exception:
        return {}
    for m in ms if isinstance(ms, list) else ():
        if isinstance(m, dict) and _same_model(m.get("id"), model):
            return m
    return {}


def _launch_subfolder(model, env=None):
    """(subfolder, error, source) for the served model. The env the shell
    launchers export wins; WITHOUT it the model's own models.json row is read,
    so a launcher started by any other path (tools/bench/*, a terminal, a
    shell launcher that dropped a stale server-backend's value) still loads
    <snapshot>/<subfolder> and never sends a multi-variant repo id to the Hub.
    source is "env", "roster" or None."""
    sub, err = _model_subfolder(env)
    if sub or err:
        return sub, err, "env"
    raw = _roster_row(model).get("model_subfolder")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "", None, None
    sub = _clean_subfolder(raw)
    if not sub:
        return "", "invalid roster model_subfolder %r for %s" % (str(raw)[:80], model), "roster"
    return sub, None, "roster"


def _force_offline_for(model, subfolder):
    """A subfolder model, or a roster row marked hf_offline, starts offline
    whoever launched it — set BEFORE mlx_vlm / huggingface_hub are imported
    (huggingface_hub reads HF_HUB_OFFLINE at import). Returns the reason, or
    None when nothing was forced."""
    why = None
    if subfolder:
        why = "model_subfolder"
    elif _roster_row(model).get("hf_offline") is True:
        why = "roster hf_offline"
    if why:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["MLX_VLM_LOCAL_ONLY"] = "1"
    return why


def _clean_sampling(v):
    """The valid subset of a sampling dict. Unknown names, booleans,
    non-finite or out-of-range numbers are dropped, never clamped."""
    out = {}
    if not isinstance(v, dict):
        return out
    for k, (typ, lo, hi) in _SAMPLING_BOUNDS.items():
        x = v.get(k)
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            continue
        if typ is int:
            if isinstance(x, float) and not x.is_integer():
                continue
            x = int(x)
        else:
            try:
                x = float(x)
            except OverflowError:       # E106: an int too large for a float
                continue
            if x != x or x in (float("inf"), float("-inf")):
                continue
        if lo <= x <= hi:
            out[k] = x
    return out


def _sampling_defaults(env=None):
    """The validated MLX_VLM_SAMPLING_DEFAULTS object ({} when unset or
    unparsable — sampling is quality, not a guard, so a bad value never stops
    the server; it is reported instead)."""
    raw = str((os.environ if env is None else env).get(_SAMPLING_ENV, "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        print("[mlx-vlm-launch] WARNING: %s is not JSON — no sampling defaults"
              % _SAMPLING_ENV, file=sys.stderr, flush=True)
        return {}
    clean = _clean_sampling(parsed)
    dropped = sorted(set(parsed) - set(clean)) if isinstance(parsed, dict) else ["<not an object>"]
    if dropped:
        print("[mlx-vlm-launch] WARNING: ignored invalid sampling default(s): %s"
              % ", ".join(map(str, dropped))[:200], file=sys.stderr, flush=True)
    return clean


def _launch_dir(name, subfolder):
    """<snapshot>/<subfolder> for repo id `name` (or <dir>/<subfolder> for a
    local model dir), or None when it is not on disk."""
    sub = _clean_subfolder(subfolder)
    s = str(name or "").strip()
    if not (sub and s):
        return None
    p = os.path.expanduser(s)
    base = p if (os.path.isabs(p) or os.path.exists(p)) else _hf_snapshot(s)
    if not base:
        return None
    d = os.path.join(base, sub)
    return d if os.path.isdir(d) else None


def _same_model(a, b):
    return str(a or "").strip().rstrip("/") == str(b or "").strip().rstrip("/")


def _tokenizer_ready(d):
    """tokenizer_config.json plus tokenizer.json or a sentencepiece
    tokenizer.model — what both loaders read before the weights. A snapshot
    without them passed --local-ready, started offline and failed at load
    instead of fetching the missing files (C49 review)."""
    try:
        return os.path.isfile(os.path.join(d, "tokenizer_config.json")) and any(
            os.path.isfile(os.path.join(d, fn)) for fn in ("tokenizer.json", "tokenizer.model"))
    except (OSError, TypeError):
        return False


def _weights_ready(d, tokenizer=False):
    """config.json plus every weight file the shard index names (HF links a
    shard into the snapshot only when its blob has finished), or at least one
    .safetensors for a single-file model. Mirrors server.py's
    `_weights_complete` (plus config.json, which an offline load needs).
    tokenizer=True (the served model, not a drafter — a drafter's tokenizer is
    never loaded) also requires `_tokenizer_ready`."""
    try:
        if not os.path.isfile(os.path.join(d, "config.json")):
            return False
        if tokenizer and not _tokenizer_ready(d):
            return False
        idx = os.path.join(d, "model.safetensors.index.json")
        if os.path.isfile(idx):
            with open(idx) as f:
                files = set((json.load(f).get("weight_map") or {}).values())
            return bool(files) and all(os.path.isfile(os.path.join(d, fn)) for fn in files)
        return any(f.endswith(".safetensors") for f in os.listdir(d))
    except (OSError, ValueError, AttributeError):
        return False


def _local_ready(name, tokenizer=False, subfolder=""):
    """Is `name` (a repo id, or a local model dir such as an in-repo drafter's
    snapshot subfolder) fully on disk? Unknown/empty is False — the caller
    then keeps the old online behaviour, never a broken offline start.
    `subfolder` (round 3, the served model only): judge <snapshot>/<subfolder>,
    the directory the loader will actually read."""
    s = str(name or "").strip()
    if not s:
        return False
    if subfolder:
        d = _launch_dir(s, subfolder)
        return bool(d) and _weights_ready(d, tokenizer)
    if os.path.isabs(s) or os.path.exists(s):
        return _weights_ready(s, tokenizer)
    snap = _hf_snapshot(s)
    return bool(snap) and _weights_ready(snap, tokenizer)


def _patch_local_snapshot_resolution(model="", subfolder=""):
    """MLX_VLM_LOCAL_ONLY=1 (set by mlx-server.sh / mlx-server-bg.sh whenever
    the model is fully on disk — C49 — and always for roster entries with
    `hf_offline`): resolve a repo id straight to its cached snapshot
    (refs/main → snapshots/<sha>) and never call the Hub. Why: orcarouter's
    Qwen3.8-27B-Uncensored repo is a deliberately PARTIAL mirror here (we skip
    the 2/6/8-bit subfolders, 62GB), and huggingface_hub ≥1.x refuses such a
    snapshot even under HF_HUB_OFFLINE=1 (IncompleteSnapshotError from
    mlx_vlm.utils.get_model_path → snapshot_download) — online it would fetch
    the 62GB instead. utils.load(), load_drafter() and the server all look
    get_model_path up at call time, so patching the utils attribute covers
    them; real local paths pass through unchanged."""
    flag = os.environ.get("MLX_VLM_LOCAL_ONLY", "").strip().lower()
    local_only = flag in ("1", "true", "yes")
    if not (local_only or subfolder):
        return
    try:
        from pathlib import Path
        from mlx_vlm import utils as _u
    except Exception:
        return
    orig = _u.get_model_path

    def get_model_path(path_or_hf_repo, *a, **kw):
        s = str(path_or_hf_repo)
        if subfolder and _same_model(s, model):
            # round 3: the launch model lives in <snapshot>/<subfolder> and
            # NEVER reaches the Hub (the repo holds every other variant too)
            d = _launch_dir(model, subfolder)
            if d and os.path.isfile(os.path.join(d, "config.json")):
                return Path(d)
            raise FileNotFoundError(
                "%s: %s/ is not on disk (download it from the model menu)"
                % (model, subfolder))
        if not local_only:
            return orig(path_or_hf_repo, *a, **kw)
        if os.path.exists(s):
            return Path(s)
        snap = _hf_snapshot(s)
        if snap and os.path.isfile(os.path.join(snap, "config.json")):
            return Path(snap)
        return orig(path_or_hf_repo, *a, **kw)

    _u.get_model_path = get_model_path
    if local_only:
        print("[mlx-vlm-launch] MLX_VLM_LOCAL_ONLY=1 — repo ids resolve to the cached "
              "snapshot; no Hub calls", flush=True)
    if subfolder:
        print("[mlx-vlm-launch] %s resolves to <snapshot>/%s" % (model, subfolder),
              flush=True)
    return get_model_path


def _patch_mlx_lm_local(model="", subfolder=""):
    """The same MLX_VLM_LOCAL_ONLY resolution for the mlx-lm fallback: its
    loader goes through mlx_lm.utils._download (snapshot_download), looked up
    at call time, so a repo id with a complete cached snapshot never reaches
    the Hub."""
    flag = os.environ.get("MLX_VLM_LOCAL_ONLY", "").strip().lower()
    local_only = flag in ("1", "true", "yes")
    if not (local_only or subfolder):
        return
    try:
        from pathlib import Path
        from mlx_lm import utils as _u
        orig = _u._download
    except Exception:
        return

    def _download(path_or_hf_repo, *a, **kw):
        s = str(path_or_hf_repo)
        if subfolder and _same_model(s, model):
            d = _launch_dir(model, subfolder)       # round 3, never the Hub
            if d and os.path.isfile(os.path.join(d, "config.json")):
                return Path(d)
            raise FileNotFoundError(
                "%s: %s/ is not on disk (download it from the model menu)"
                % (model, subfolder))
        if local_only and not os.path.exists(s):
            snap = _hf_snapshot(s)
            if snap and os.path.isfile(os.path.join(snap, "config.json")):
                return Path(snap)
        return orig(path_or_hf_repo, *a, **kw)

    _u._download = _download
    if local_only:
        print("[mlx-vlm-launch] MLX_VLM_LOCAL_ONLY=1 — mlx-lm resolves repo ids to the "
              "cached snapshot; no Hub calls", flush=True)
    return _download


def _patch_default_reasoning_effort():
    """mlx_vlm.server has no server-level default for the Qwen3.8 chat
    template's `reasoning_effort` (its template default is xhigh — ~22k think
    tokens on trivial prompts). MLX_VLM_DEFAULT_REASONING_EFFORT=low|medium|
    xhigh applies when thinking is on and the request didn't set one."""
    effort = os.environ.get("MLX_VLM_DEFAULT_REASONING_EFFORT", "").strip().lower()
    if not effort:
        return
    try:
        from mlx_vlm.server import generation
    except Exception:
        return
    orig = generation.GenerationArguments.to_template_kwargs

    def to_template_kwargs(self):
        kw = orig(self)
        if kw.get("enable_thinking") and "reasoning_effort" not in kw:
            kw["reasoning_effort"] = effort
        return kw

    generation.GenerationArguments.to_template_kwargs = to_template_kwargs
    print(f"[mlx-vlm-launch] default reasoning_effort={effort} when thinking is on",
          flush=True)


def _eos_ids_to_add(tok_eos, config_eos, have):
    """Pure: the tokenizer's end-of-turn id(s) that are not yet stop tokens.
    tok_eos = tokenizer.eos_token_id (int | list | None); config_eos = the
    model config's eos_token_id (int | list | None); have = the processor's
    additional_eos_token_ids."""
    def ids(v):
        if isinstance(v, bool):
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, (list, tuple)):
            return [i for i in v if isinstance(i, int) and not isinstance(i, bool)]
        return []
    known = set(ids(config_eos)) | set(ids(have))
    return [i for i in dict.fromkeys(ids(tok_eos)) if i >= 0 and i not in known]


def _patch_tokenizer_eos():
    """Stop on the TOKENIZER's end-of-turn token too (2026-11-12).

    mlx_vlm's server stops on model.config.eos_token_id plus the processor's
    additional_eos_token_ids, and falls back to the tokenizer's eos only when
    the config names none. A fine-tune whose config.json/generation_config.json
    name <|endoftext|> while its chat template ends turns with <|im_end|> (the
    DeepSeek-V4-Pro Qwen3.5-9B builds) then streams a literal "<|im_end|>" at
    the end of every reply. Adding the tokenizer's eos to
    additional_eos_token_ids (read by the batch path's stop set and by the
    StoppingCriteria, whose reset() keeps additional ids) fixes it; for a model
    that already stops there it changes nothing."""
    try:
        from mlx_vlm.server import generation
    except Exception:
        return
    orig = getattr(generation, "load_model_resources", None)
    if orig is None or getattr(orig, "_hermes_eos", False):
        return

    def load_model_resources(model_path, adapter_path):
        model, processor, config = orig(model_path, adapter_path)
        try:
            tok = getattr(processor, "tokenizer", processor)
            have = list(getattr(processor, "additional_eos_token_ids", None) or ())
            add = _eos_ids_to_add(getattr(tok, "eos_token_id", None),
                                  getattr(config, "eos_token_id", None), have)
            if add:
                processor.additional_eos_token_ids = have + add
                crit = getattr(tok, "stopping_criteria", None)
                if crit is not None:
                    crit.additional_eos_token_ids = list(dict.fromkeys(
                        list(getattr(crit, "additional_eos_token_ids", ()) or ()) + add))
                    crit.reset(getattr(config, "eos_token_id", None))
                print("[mlx-vlm-launch] also stopping on the tokenizer's eos %s "
                      "(the model config names %r)" % (add, getattr(config, "eos_token_id", None)),
                      flush=True)
        except Exception as e:  # quality, not a guard: serve anyway
            print("[mlx-vlm-launch] WARNING: tokenizer eos not added (%s: %s)"
                  % (type(e).__name__, e), file=sys.stderr, flush=True)
        return model, processor, config

    load_model_resources._hermes_eos = True
    generation.load_model_resources = load_model_resources


# --------------------------------------------------------------------------
# C130 lockdown — see the module docstring. Everything up to the two
# _install_* functions is pure stdlib so the decision logic is testable
# without the venv.
# --------------------------------------------------------------------------
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
# Read-only liveness probes the dashboard (model_online / _bg_probe), doctor.py
# and the bench scripts poll WITHOUT a key. With the model pinned they load
# nothing; they say which models are cached and which one is loaded.
_PROBE_ROUTES = frozenset((m, p) for m in ("GET", "HEAD")
                          for p in ("/health", "/v1/models", "/models"))
# Never used by Hermes. /unload and /cache/reset drop the model or its prefix
# cache for any caller; /v1/realtime loads whatever model its first message
# names through utils.load(), not get_cached_model, so the pin cannot see it.
_BLOCKED_PATHS = frozenset(("/unload", "/cache/reset", "/v1/cache/reset",
                            "/v1/realtime"))
# PATCH /settings can set spec_draft_model — a drafter load from any repo id.
_SETTINGS_PATHS = frozenset(("/settings", "/v1/settings"))
_API_KEY_ENV = "MLX_VLM_SERVER_API_KEY"


def _host_ok(host):
    h = str(host or "").strip().lower()
    for n in _LOOPBACK_NAMES:
        if h == n or (h.startswith(n + ":") and h[len(n) + 1:].isdigit()):
            return True
    return False


def _request_refusal(method, path, headers, api_key=""):
    """None when a request may proceed, else (status, message). Pure — the
    whole decision lives here. `headers` maps LOWER-CASED names to values."""
    m = str(method or "").upper()
    p = str(path or "/").split("?", 1)[0] or "/"
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    if not _host_ok(headers.get("host")):
        return 403, "forbidden host"
    if headers.get("origin") is not None:        # even "null" or ""
        return 403, "cross-origin request refused"
    if p in _BLOCKED_PATHS or (p in _SETTINGS_PATHS and m not in ("GET", "HEAD")):
        return 403, "route disabled by the Hermes launcher"
    if api_key and (m, p) not in _PROBE_ROUTES:
        got = str(headers.get("authorization") or "").encode("utf-8", "replace")
        if not hmac.compare_digest(got, ("Bearer " + api_key).encode("utf-8")):
            return 401, "invalid API key"
    return None


# E100 (2026-11-09 audit): the pin above decides WHICH model loads; it never
# looked at what a request asks that model to read. mlx_vlm collects every
# image/audio/video reference in message content verbatim and its loaders open
# a local PATH (Image.open, load_audio, load_video) or GET an http(s) URL
# (requests) — so any client reaching the port could have the owner's server
# read ~/Desktop/x.png into the vision model, probe which files exist, or make
# a "fully local" server fetch a URL from the owner's network. Hermes and the
# dashboard only ever send inline `data:` URIs, so every other media
# reference is refused with a 400 before the request reaches a route.
_MEDIA_KEYS = frozenset((
    "image", "images", "image_url", "input_image",
    "audio", "audios", "audio_url", "input_audio", "ref_audio",
    "video", "videos", "video_url", "input_video",
    "url", "file", "file_url", "file_path", "path", "input_file"))
# where a value names the media inside a media dict ({"url": ...}, input_audio
# {"data": ...}, an OpenAI file part {"file_data": ...})
_MEDIA_REF_KEYS = frozenset(("url", "data", "path", "file", "file_data", "file_url",
                             "file_path", "image_url", "audio_url", "video_url",
                             "image", "audio", "video"))
# never media: tool schemas and the model's own tool calls (their argument
# dicts may well hold a "url" or a "path")
_MEDIA_SKIP_KEYS = frozenset(("tools", "functions", "tool_calls", "function_call",
                              "arguments", "tool_choice", "response_format", "metadata"))
_MEDIA_SKIP_TYPES = frozenset(("tool_use", "server_tool_use", "function_call"))
_MEDIA_MAX_DEPTH = 64


def _is_data_uri(v):
    return isinstance(v, str) and v.lstrip()[:5].lower() == "data:"


def _media_in_value(key, v, depth):
    """The offending reference inside media value `v` (under `key`), or None."""
    if depth > _MEDIA_MAX_DEPTH:
        return key + " (nested too deeply)"
    if v is None or isinstance(v, (bool, int, float)):
        return None
    if isinstance(v, str):
        return None if _is_data_uri(v) else key
    if isinstance(v, list):
        for x in v:
            r = _media_in_value(key, x, depth + 1)
            if r:
                return r
        return None
    if isinstance(v, dict):
        for k, x in v.items():
            if k in _MEDIA_REF_KEYS:
                r = _media_in_value(key + "." + k, x, depth + 1)
            elif isinstance(x, (dict, list)):
                r = _media_in_obj(x, depth + 1)
            else:
                r = None
            if r:
                return r
    return None


def _media_in_obj(obj, depth=0):
    """Walk a JSON request body; the first media reference that is not a
    `data:` URI (as a dotted key), or None."""
    if depth > _MEDIA_MAX_DEPTH:
        return "(request nested too deeply)"
    if isinstance(obj, list):
        for x in obj:
            r = _media_in_obj(x, depth + 1)
            if r:
                return r
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") in _MEDIA_SKIP_TYPES:
        return None
    for k, v in obj.items():
        if not isinstance(k, str) or k in _MEDIA_SKIP_KEYS:
            continue
        if k in _MEDIA_KEYS:
            r = _media_in_value(k, v, depth + 1)
        elif isinstance(v, (dict, list)):
            r = _media_in_obj(v, depth + 1)
        else:
            r = None
        if r:
            return r
    return None


def _media_refusal(body, content_type=""):
    """None, or (400, message) for a JSON body naming any image/audio/video/
    file by local path or URL instead of an inline data: URI. Pure. A body
    that is not JSON passes (the route rejects it, and a multipart upload
    carries bytes, not references)."""
    if not body:
        return None
    # Whatever the content type says: the routes parse with json.loads(bytes)
    # (Starlette's request.json(); FastAPI also does it for a body with no
    # content type), which reads a UTF-8 BOM and UTF-16/32 too, so a sniff of
    # the first byte let such a body through unchecked. A non-JSON body fails
    # the parse here as it does there.
    try:
        obj = json.loads(body)
    except Exception:
        return None
    bad = _media_in_obj(obj)
    if bad:
        return 400, ("media must be sent inline as a data: URI; this server does not "
                     "open local paths or fetch URLs (%s)" % bad[:80])
    return None


def _api_key_path():
    return os.environ.get("HERMES_MODEL_API_KEY_FILE") or os.path.join(
        os.path.expanduser("~"), ".hermes", "dashboard", "model-api-key")


def _load_api_key():
    """The optional bearer key: MLX_VLM_SERVER_API_KEY when exported, else the
    owner's ~/.hermes/dashboard/model-api-key when it exists. Absent = no key
    (the old, key-less behaviour; the other guards still apply). The env var
    is POPPED so upstream's own check (which also covers the probes the
    dashboard polls without a key) stays out of the way — this launcher
    enforces the key itself."""
    env = os.environ.pop(_API_KEY_ENV, "").strip()
    if env:
        return env
    path = _api_key_path()
    try:
        st = os.stat(path)
        with open(path) as f:
            key = f.read().strip()
    except OSError:
        return ""
    if key and st.st_mode & 0o077:
        print("[mlx-vlm-launch] WARNING: %s is readable by other users — "
              "chmod 600 it" % path, file=sys.stderr, flush=True)
    return key


def _model_key(name):
    """Comparable spelling of a model reference: the realpath for anything
    that names a directory on disk, the trimmed id otherwise."""
    s = str(name or "").strip()
    if not s:
        return ""
    p = os.path.expanduser(s)
    if os.path.isabs(p) or os.path.exists(p):
        return os.path.realpath(p)
    return s.rstrip("/")


def _model_aliases(name, subfolder=""):
    """Every spelling of the launch model a request may use: the id as given,
    plus the realpath of its cached snapshot (or of the directory itself).
    With a roster `subfolder` (round 3) the directory the loader really reads
    is <snapshot>/<subfolder>, so THAT is the path alias — the snapshot root of
    a multi-variant repo is not this model and is never added."""
    out = set()
    k = _model_key(name)
    if not k:
        return out
    out.add(k)
    s = str(name).strip()
    if subfolder:
        d = _launch_dir(s, subfolder)
        if d:
            out.add(os.path.realpath(d))
    if not (os.path.isabs(os.path.expanduser(s)) or os.path.exists(os.path.expanduser(s))):
        out.add(s.rstrip("/"))
        snap = _hf_snapshot(s)
        if snap and not subfolder:
            out.add(os.path.realpath(snap))
    return out


def _argv_value(argv, flag):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return ""


def _log_refusal(method, path, headers, reason):
    print("[mlx-vlm-launch] refused %s %s host=%r origin=%r (%s)"
          % (method, str(path)[:120], headers.get("host"), headers.get("origin"),
             reason), file=sys.stderr, flush=True)


class _GuardASGI:
    """Outermost ASGI layer: _request_refusal() for every HTTP request and
    websocket handshake; lifespan passes straight through."""

    def __init__(self, app, api_key=""):
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {}
        for k, v in scope.get("headers") or ():
            headers.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        method = scope.get("method") or "GET"
        path = scope.get("path") or "/"
        ref = _request_refusal(method, path, headers, self.api_key)
        if ref is None and kind == "http" and str(method).upper() in ("POST", "PUT", "PATCH"):
            # E100: read the body once, check its media references, replay it
            chunks = []
            while True:
                msg = await receive()
                if msg.get("type") != "http.request":
                    return None             # the client went away
                chunks.append(msg.get("body") or b"")
                if not msg.get("more_body"):
                    break
            body = b"".join(chunks)
            ref = _media_refusal(body, headers.get("content-type"))
            if ref is None:
                replayed = [False]

                async def replay():
                    if not replayed[0]:
                        replayed[0] = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()  # http.disconnect, for streaming routes
                return await self.app(scope, replay, send)
        if ref is None:
            return await self.app(scope, receive, send)
        status, msg = ref
        _log_refusal(method, path, headers, msg)
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return None
        body = json.dumps({"error": {"message": msg, "type": "invalid_request_error",
                                     "code": status}}).encode()
        hdrs = [(b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode())]
        if status == 401:
            hdrs.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})
        return None


def _patch_pin_model(model, subfolder=""):
    """Wrap mlx_vlm's get_cached_model (the package attribute every protocol
    route resolves at call time, and the app-module global the startup preload
    uses) so the only thing it will ever load is the launch model."""
    from fastapi import HTTPException
    import mlx_vlm.server as pkg
    appmod = sys.modules["mlx_vlm.server.app"]
    orig = appmod.get_cached_model
    inherit = appmod._INHERIT_ADAPTER
    allowed = _model_aliases(model, subfolder)

    def get_cached_model(model_path, adapter_path=inherit, *, model_kind="auto"):
        if model_kind not in ("auto", "text_generation") or \
                _model_key(model_path) not in allowed:
            raise HTTPException(status_code=400, detail=(
                "this server only serves %s; %r is not loadable here"
                % (model or "(no model)", str(model_path)[:200])))
        if adapter_path is not inherit and adapter_path is not None:
            raise HTTPException(status_code=400,
                                detail="adapters are not served here")
        return orig(model, adapter_path, model_kind=model_kind)

    get_cached_model.__wrapped__ = orig
    appmod.get_cached_model = get_cached_model
    pkg.get_cached_model = get_cached_model
    return get_cached_model


def _install_vlm_guards(model, api_key="", subfolder=""):
    """Pin + CORS removal + request guard on the real mlx_vlm app. Raises when
    any piece cannot be put in place; the caller then refuses to start."""
    import mlx_vlm.server as pkg
    from starlette.middleware.cors import CORSMiddleware
    appmod = sys.modules["mlx_vlm.server.app"]
    app = appmod.app
    pinned = _patch_pin_model(model, subfolder)
    app.user_middleware = [m for m in app.user_middleware
                           if getattr(m, "cls", None) is not CORSMiddleware]
    app.add_middleware(_GuardASGI, api_key=api_key)
    if not (getattr(app.user_middleware[0], "cls", None) is _GuardASGI
            and pkg.get_cached_model is pinned and appmod.get_cached_model is pinned
            and pkg.app is app
            and not any(getattr(m, "cls", None) is CORSMiddleware
                        for m in app.user_middleware)):
        raise RuntimeError("mlx_vlm server layout changed — guard not in place")
    return app


def _install_mlx_lm_guards(S, model, draft="", api_key="", subfolder=""):
    """The same pin + guard for `mlx_lm.server` (framework python). Its
    ModelProvider maps "default_model" to --model and otherwise loads the
    model / draft_model / adapters a request names."""
    models = _model_aliases(model, subfolder) | {"default_model"}
    drafts = (_model_aliases(draft) if draft else set()) | {"default_model"}
    prov = S.ModelProvider
    orig_load = prov.load

    def load(self, model_path, adapter_path=None, draft_model_path=None):
        mk = model_path if model_path == "default_model" else _model_key(model_path)
        dk = draft_model_path if draft_model_path in (None, "default_model") \
            else _model_key(draft_model_path)
        if mk not in models or (dk is not None and dk not in drafts):
            raise ValueError("this server only serves %s; %r is not loadable here"
                             % (model or "(no model)", str(model_path)[:200]))
        if adapter_path is not None:
            raise ValueError("adapters are not served here")
        return orig_load(self, "default_model", None, "default_model")

    load.__wrapped__ = orig_load
    prov.load = load
    H = S.APIHandler

    def _guarded(orig):
        def handler(self):
            headers = {}
            for k, v in self.headers.items():
                headers.setdefault(str(k).lower(), v)
            ref = _request_refusal(self.command, self.path, headers, api_key)
            if ref is None:
                return orig(self)
            status, msg = ref
            _log_refusal(self.command, self.path, headers, msg)
            body = json.dumps({"error": msg}).encode()
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)
            return None
        handler.__wrapped__ = orig
        return handler

    for name in ("do_GET", "do_POST", "do_OPTIONS"):
        setattr(H, name, _guarded(getattr(H, name)))
    H._set_cors_headers = lambda self: None     # no Access-Control-* at all
    return prov


def _patch_sampling_defaults(defaults):
    """Round 3: the roster's per-model sampling defaults on the mlx_vlm
    server. Every protocol route (OpenAI chat/responses, Anthropic messages)
    builds its GenerationArguments through request_normalization.
    _build_gen_args, looked up at call time by app._build_gen_args, so
    wrapping that one module attribute covers them all — and it is the
    GenerationArguments object itself that changes, which the continuous-
    batching path reads directly (args.repetition_penalty, not the kwargs).
    A field the request SET (model_fields_set: an explicit null included) is
    never touched. Returns the wrapper, or None when there was nothing to do."""
    if not defaults:
        return None
    from mlx_vlm.server import request_normalization as rn
    orig = rn._build_gen_args
    is_set = getattr(rn, "_request_field_is_set", None)

    def _was_set(request, name):
        if is_set is not None:
            return is_set(request, name)
        fs = getattr(request, "model_fields_set", None)
        return (name in fs) if fs is not None else getattr(request, name, None) is not None

    def _build_gen_args(request, *a, **kw):
        args = orig(request, *a, **kw)
        for k, v in defaults.items():
            if hasattr(args, k) and not _was_set(request, k):
                setattr(args, k, v)
        return args

    _build_gen_args.__wrapped__ = orig
    rn._build_gen_args = _build_gen_args
    print("[mlx-vlm-launch] roster sampling defaults (when a request does not set "
          "them): %s" % json.dumps(defaults, sort_keys=True), flush=True)
    return _build_gen_args


def _patch_mlx_lm_sampling(S, defaults):
    """The same defaults for the mlx-lm fallback: APIHandler.do_POST reads
    each field into an attribute of the same name (self.repetition_penalty =
    body.get("repetition_penalty", 0.0), ...) and then calls
    validate_model_parameters(), so the wrapper fills the attributes for the
    keys the BODY does not carry and lets upstream validate them as usual."""
    if not defaults:
        return None
    H = S.APIHandler
    orig = H.validate_model_parameters

    def validate_model_parameters(self):
        body = getattr(self, "body", None)
        if isinstance(body, dict):
            for k, v in defaults.items():
                if k not in body and hasattr(self, k):
                    setattr(self, k, v)
        return orig(self)

    validate_model_parameters.__wrapped__ = orig
    H.validate_model_parameters = validate_model_parameters
    print("[mlx-vlm-launch] mlx-lm roster sampling defaults: %s"
          % json.dumps(defaults, sort_keys=True), flush=True)
    return validate_model_parameters


def _hard_exit(code=0):
    # mlx 0.32.x segfaults in the CompileCache destructor at interpreter
    # teardown (harmless, but launchd logs it as a crash and macOS shows a
    # "Python quit unexpectedly" dialog). atexit runs BEFORE module teardown on
    # every exit path (normal return, SystemExit, uvicorn's SIGTERM handling),
    # so ending the process here skips the crashing finalizer.
    try:
        sys.stdout.flush(); sys.stderr.flush()
    finally:
        os._exit(code)


def _run_server_module(modname):
    """Run `modname` as __main__ and end the process with ITS status through
    _hard_exit. 2026-10-08 audit (P3, model lifecycle): the old
    `finally: _hard_exit()` turned every failure — an import error, uvicorn's
    sys.exit(1) on a busy port — into exit 0, and os._exit dropped the
    traceback before Python could print it."""
    code = 0
    try:
        runpy.run_module(modname, run_name="__main__", alter_sys=True)
    except SystemExit as e:
        if isinstance(e.code, int) or e.code is None:
            code = e.code or 0
        else:
            print(e.code, file=sys.stderr)
            code = 1
    except KeyboardInterrupt:
        code = 0
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    _hard_exit(code)


def _main_vlm():
    model = _argv_value(sys.argv[1:], "--model")
    api_key = _load_api_key()
    subfolder, sub_err, sub_src = _launch_subfolder(model)
    if sub_err:
        print("[mlx-vlm-launch] NOT STARTING: %s — refusing to load the snapshot "
              "root of a multi-variant repo instead" % sub_err,
              file=sys.stderr, flush=True)
        sys.exit(78)        # nothing imported yet: a plain exit is clean
    why = _force_offline_for(model, subfolder)
    if why:
        print("[mlx-vlm-launch] offline (%s%s) — HF_HUB_OFFLINE=1 + MLX_VLM_LOCAL_ONLY=1"
              % (why, ", from models.json" if sub_src == "roster" else ""), flush=True)
    _patch_rng_restore()
    _patch_local_snapshot_resolution(model, subfolder)
    _patch_default_reasoning_effort()
    _patch_tokenizer_eos()
    try:
        _install_vlm_guards(model, api_key, subfolder)
    except Exception as e:
        print("[mlx-vlm-launch] NOT STARTING: the request guard could not be "
              "installed (%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
        _hard_exit(78)      # mlx is imported by now: skip its teardown segfault
    try:
        _patch_sampling_defaults(_sampling_defaults())
    except Exception as e:  # quality, not a guard: say so and serve anyway
        print("[mlx-vlm-launch] WARNING: roster sampling defaults NOT applied "
              "(%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
    print("[mlx-vlm-launch] pinned to %s; CORS off, loopback Host only, no Origin, "
          "management routes off, media inline (data:) only, API key %s"
          % (model, "required" if api_key else "not set"), flush=True)
    atexit.register(_hard_exit)
    sys.argv[0] = "mlx_vlm.server"
    _run_server_module("mlx_vlm.server")


def _main_mlx_lm(argv):
    """`python3 mlx-vlm-launch.py --mlx-lm <mlx_lm server args>` — what
    `python3 -m mlx_lm server` did, with the same pin and guard."""
    api_key = _load_api_key()
    model = _argv_value(argv, "--model")
    subfolder, sub_err, _src = _launch_subfolder(model)
    if sub_err:
        print("[mlx-vlm-launch] NOT STARTING (mlx-lm): %s" % sub_err,
              file=sys.stderr, flush=True)
        sys.exit(78)
    if _force_offline_for(model, subfolder):
        print("[mlx-vlm-launch] mlx-lm offline — HF_HUB_OFFLINE=1 + MLX_VLM_LOCAL_ONLY=1",
              flush=True)
    _patch_mlx_lm_local(model, subfolder)
    try:
        import mlx_lm.server as S
        _install_mlx_lm_guards(S, model, _argv_value(argv, "--draft-model"), api_key,
                               subfolder)
    except Exception as e:
        print("[mlx-vlm-launch] NOT STARTING (mlx-lm): the request guard could not "
              "be installed (%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
        _hard_exit(78)
    try:
        _patch_mlx_lm_sampling(S, _sampling_defaults())
    except Exception as e:
        print("[mlx-vlm-launch] WARNING: roster sampling defaults NOT applied "
              "(%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
    print("[mlx-vlm-launch] mlx-lm pinned to %s; API key %s"
          % (_argv_value(argv, "--model"), "required" if api_key else "not set"), flush=True)
    sys.argv = ["mlx_lm.server"] + list(argv)
    S.main()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local-ready"]:
        # C49: exit 0 when every named model (repo id or local dir) is fully
        # on disk — the shell launchers then start offline. Stdlib only, so
        # the framework python answers it without importing mlx. The FIRST
        # name is the served model and must carry its tokenizer too; any
        # further name is a drafter (weights only).
        # Round 3: MLX_VLM_MODEL_SUBFOLDER applies to the FIRST name only (the
        # served model); an invalid one is "not ready", never the repo root.
        names = sys.argv[2:]
        sub, sub_err, _src = _launch_subfolder(names[0] if names else "")
        sys.exit(0 if names and not sub_err
                 and all(_local_ready(n, i == 0, sub if i == 0 else "")
                         for i, n in enumerate(names))
                 else 1)
    if sys.argv[1:2] == ["--mlx-lm"]:
        _main_mlx_lm(sys.argv[2:])
    else:
        _main_vlm()
