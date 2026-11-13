# aux_hfhub.py — browse Hugging Face for MLX models and add one to the
# model menu (2026-11-12, owner request).
#
#   GET  /api/models/hf/search?q=&limit=   MLX text models on the Hub, most
#        downloaded first: {ok, q, results[{id, downloads, likes, updated,
#        pipeline, gated, bits, uncensored, vision, in_roster, curated}],
#        took_ms, cached}
#   GET  /api/models/hf/inspect?id=        one repo, judged for THIS Mac and
#        THIS runtime: {ok, id, sha, verdict: ok|warn|no, blocks[], warns[],
#        notes[], facts{...}, row{...roster shape...}, alternatives[],
#        in_roster, curated}
#   POST /api/models/hf/import {id, confirm:true, sha?, download?}
#        re-inspects ON THE SERVER (a client-sent shape is never trusted),
#        refuses a "no", adds (or refreshes) the roster row through
#        models_update, records the inspection in hf-imports.json and, with
#        download:true, starts download_model(). A curated id goes through
#        add_model() so it keeps its checked shape.
#
# What it never does: run a repo's own code (an auto_map refuses the model,
# *.py is never downloaded — _dl_ignore_patterns), fetch pickle/GGUF, switch
# or restart a model, read a README or model card into the page, or follow a
# redirect off huggingface.co. Every string that comes back from the Hub is
# reduced to a validated shape (an org/name id, a date, a number, a licence
# id) before it leaves this module; the page escapes what is left.
#
# Network (NETWORK_FACTS "models" row): only when the owner searches or opens
# a model here — the search words or the repo name go to huggingface.co, with
# no token and nothing else of theirs. Results are cached 10 minutes and
# requests are spaced at least _HFX_GAP_S apart.
#
# Load order: aux files exec SORTED, so this runs after aux_advisor*/
# aux_github and before aux_index/aux_onboarding — every foreign name is
# resolved BY NAME AT CALL TIME (the aux_index discipline).

import fnmatch as _hfx_fnmatch
import glob as _hfx_glob
import json as _hfx_json
import math as _hfx_math
import os as _hfx_os
import re as _hfx_re
import subprocess as _hfx_subprocess
import threading as _hfx_threading
import time as _hfx_time
import urllib.error as _hfx_urlerror
import urllib.parse as _hfx_urlparse
import urllib.request as _hfx_urlrequest

_HFX_BASE = "https://huggingface.co"
_HFX_HOSTS = ("huggingface.co",)
_HFX_TIMEOUT = 12
_HFX_MAX_JSON = 4_000_000           # a model-info answer with ~200 files is ~60 KB
_HFX_MAX_FILE = 8_000_000           # config / tokenizer_config (Gemma's is 1.2 MB) / template / index
_HFX_TTL = 600
_HFX_GAP_S = 0.35
_HFX_Q_MAX = 100
_HFX_LIMIT_MAX = 40
_HFX_ALLOW = ["*.safetensors", "*.json", "*.jinja", "*.txt", "*.model"]
_HFX_EXACT_MAX = 16                 # aux_config keeps at most 16 allow_patterns
_HFX_PIPELINES = ("text-generation", "image-text-to-text", "any-to-any")
_HFX_UNC_RE = _hfx_re.compile(r"abliterat|uncensor|heretic|decensor", _hfx_re.I)
_HFX_BITS_RE = _hfx_re.compile(r"(?<![0-9])([2-8])[-_ ]?bit", _hfx_re.I)
_HFX_DATE_RE = _hfx_re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_HFX_SHA_RE = _hfx_re.compile(r"^[0-9a-f]{40}$")
_HFX_FILE_RE = _hfx_re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_HFX_TYPE_RE = _hfx_re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")
_HFX_TOOL_RE = _hfx_re.compile(r"tool_call|\btools\b")
_HFX_RISKY = (".py", ".pyc", ".pkl", ".pickle", ".bin", ".pt", ".pth", ".ckpt")

_hfx_lock = _hfx_threading.Lock()           # the cache + the request spacing
_hfx_net_lock = _hfx_threading.Lock()       # one outbound request at a time
_hfx_store_lock = _hfx_threading.Lock()     # hf-imports.json read-modify-write
_hfx_cache = {}                              # key -> (ts, value)
_hfx_last_req = [0.0]
_hfx_types_memo = {}                         # "vlm"/"lm" -> frozenset (success only)


def _hfx_g(name, default=None):
    return globals().get(name, default)


class _HfxError(Exception):
    def __init__(self, msg, status=502):
        Exception.__init__(self, msg)
        self.status = status


# --------------------------------------------------------------------------
# the one outbound path
# --------------------------------------------------------------------------
class _HfxRedirect(_hfx_urlrequest.HTTPRedirectHandler):
    """Follow a redirect only to https://huggingface.co (the /resolve/ route
    answers 307 to its own /api/resolve-cache/); anything else is refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = _hfx_urlparse.urlsplit(newurl)
        if u.scheme != "https" or (u.hostname or "").lower() not in _HFX_HOSTS:
            raise _HfxError("Hugging Face redirected off huggingface.co", 502)
        return _hfx_urlrequest.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def _hfx_opener():
    handlers = [_HfxRedirect()]
    ctx_fn = _hfx_g("_ssl_context")
    if callable(ctx_fn):
        try:
            handlers.append(_hfx_urlrequest.HTTPSHandler(context=ctx_fn()))
        except Exception:
            pass
    return _hfx_urlrequest.build_opener(*handlers)


def _hfx_fetch(path, cap):
    """GET https://huggingface.co<path> -> bytes (≤ cap). Raises _HfxError.
    Tests replace this function."""
    url = _HFX_BASE + path
    with _hfx_net_lock:
        wait = _HFX_GAP_S - (_hfx_time.time() - _hfx_last_req[0])
        if wait > 0:
            _hfx_time.sleep(wait)
        _hfx_last_req[0] = _hfx_time.time()
        req = _hfx_urlrequest.Request(url, headers={
            "Accept": "application/json, text/plain;q=0.8",
            "User-Agent": "hermes-assistant (model browser)"})
        try:
            with _hfx_opener().open(req, timeout=_HFX_TIMEOUT) as r:
                data = r.read(cap + 1)
        except _HfxError:
            raise
        except _hfx_urlerror.HTTPError as e:
            if e.code in (401, 403):
                raise _HfxError("Hugging Face refused (gated or private)", 403)
            if e.code == 404:
                raise _HfxError("not found on Hugging Face", 404)
            if e.code == 429:
                raise _HfxError("Hugging Face is rate-limiting; try again in a minute", 429)
            raise _HfxError("Hugging Face answered HTTP %d" % e.code, 502)
        except Exception as e:  # noqa: BLE001 - URLError, timeout, ssl
            raise _HfxError("could not reach Hugging Face (%s)" % type(e).__name__, 502)
    if len(data) > cap:
        raise _HfxError("Hugging Face sent more than expected", 413)
    return data


def _hfx_json_get(path, cap=_HFX_MAX_JSON):
    try:
        return _hfx_json.loads(_hfx_fetch(path, cap).decode("utf-8"))
    except _HfxError:
        raise
    except (ValueError, RecursionError):
        raise _HfxError("Hugging Face sent something that is not JSON", 502)


def _hfx_cached(key, fn):
    now = _hfx_time.time()
    with _hfx_lock:
        hit = _hfx_cache.get(key)
        if hit and now - hit[0] < _HFX_TTL:
            return hit[1], True
    val = fn()
    with _hfx_lock:
        _hfx_cache[key] = (_hfx_time.time(), val)
        if len(_hfx_cache) > 200:
            for k, _v in sorted(_hfx_cache.items(), key=lambda kv: kv[1][0])[:50]:
                _hfx_cache.pop(k, None)
    return val, False


# --------------------------------------------------------------------------
# small validators — everything from the Hub passes one before it is kept
# --------------------------------------------------------------------------
def _hfx_valid_id(mid):
    fn = _hfx_g("_valid_repo_id")
    if callable(fn):
        return bool(fn(mid))
    return (isinstance(mid, str) and len(mid) <= 120 and ".." not in mid and "--" not in mid
            and bool(_hfx_re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?"
                                       r"/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?", mid)))


def _hfx_int(v, lo=0, hi=10 ** 13):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        i = int(v)
    except (OverflowError, ValueError):
        return None
    return i if lo <= i <= hi else None


def _hfx_date(v):
    return v if isinstance(v, str) and _HFX_DATE_RE.match(v) else None


def _hfx_license(v):
    fn = _hfx_g("_roster_license")
    if isinstance(v, list):
        v = v[0] if v else None
    if callable(fn):
        return fn(v)
    s = v.strip() if isinstance(v, str) else ""
    return s if _hfx_re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,39}", s) else None


def _hfx_clean_label(s):
    s = _hfx_re.sub(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]",
                    "", s if isinstance(s, str) else "")
    return s.strip()[:64]


def _hfx_tags(meta):
    tags = meta.get("tags") if isinstance(meta, dict) else None
    return [t for t in (tags or []) if isinstance(t, str) and len(t) <= 200][:200]


def _hfx_bits(mid, tags):
    for t in tags:
        m = _hfx_re.fullmatch(r"([2-8])-bit", t)
        if m:
            return int(m.group(1))
    m = _HFX_BITS_RE.search(mid or "")
    return int(m.group(1)) if m else None


def _hfx_is_uncensored(mid, tags):
    return bool(_HFX_UNC_RE.search(mid or "")) or any(_HFX_UNC_RE.search(t) for t in tags)


def _hfx_roster_ids():
    try:
        return [str(m.get("id") or "") for m in (_hfx_g("_model_registry")() or [])]
    except Exception:
        return []


def _hfx_curated_ids():
    try:
        return set(_hfx_g("_curated_model_ids")() or ())
    except Exception:
        return set()


def _hfx_in(ids, mid):
    low = (mid or "").lower()
    return any(isinstance(i, str) and i.lower() == low for i in ids)


# --------------------------------------------------------------------------
# what this Mac can load
# --------------------------------------------------------------------------
def _hfx_vlm_types():
    """model_type names the installed mlx_vlm has a module for (frozenset),
    or None when the venv is missing. A directory listing — nothing imported."""
    if "vlm" in _hfx_types_memo:
        return _hfx_types_memo["vlm"]
    venv_py = _hfx_g("MLX_VLM_VENV_PY") or _hfx_os.path.join(
        HOME, ".hermes", "mlx-vlm-venv", "bin", "python")                  # noqa: F821
    if not _hfx_os.path.exists(venv_py):
        return None
    root = _hfx_os.path.dirname(_hfx_os.path.dirname(venv_py))
    out = set()
    for d in _hfx_glob.glob(_hfx_os.path.join(root, "lib", "python3*", "site-packages",
                                              "mlx_vlm", "models", "*")):
        n = _hfx_os.path.basename(d)
        if _hfx_os.path.isdir(d) and _HFX_TYPE_RE.match(n) and not n.startswith("_"):
            out.add(n)
    if not out:
        return None
    _hfx_types_memo["vlm"] = frozenset(out)
    return _hfx_types_memo["vlm"]


def _hfx_lm_types():
    """model_type names the mlx-lm fallback can load (frozenset), or None when
    it cannot be found. find_spec only — mlx_lm is never imported."""
    if "lm" in _hfx_types_memo:
        return _hfx_types_memo["lm"]
    import shutil as _hfx_shutil
    cands = ["/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
             _hfx_shutil.which("python3")]
    for py in cands:
        if not py or not _hfx_os.path.exists(py):
            continue
        try:
            # -E, not -I: mlx-lm is a user-site install here and -I hides the
            # user site; cwd "/" so no stray module next to the dashboard loads
            r = _hfx_subprocess.run(
                [py, "-E", "-c", "import importlib.util as u;s=u.find_spec('mlx_lm');"
                 "print(s.submodule_search_locations[0] if s else '')"],
                capture_output=True, text=True, timeout=15, cwd="/")
        except Exception:
            continue
        loc = (r.stdout or "").strip().splitlines()[-1:] or [""]
        if r.returncode != 0 or not loc[0] or not _hfx_os.path.isdir(loc[0]):
            continue
        out = {_hfx_os.path.basename(p)[:-3] for p in
               _hfx_glob.glob(_hfx_os.path.join(loc[0], "models", "*.py"))}
        out = {n for n in out if _HFX_TYPE_RE.match(n) and not n.startswith("_")}
        if out:
            _hfx_types_memo["lm"] = frozenset(out)
            return _hfx_types_memo["lm"]
    return None


def _hfx_env():
    machine = None
    try:
        machine = _hfx_g("_machine_ram_gb")()
    except Exception:
        pass
    return {"vlm_types": _hfx_vlm_types(), "lm_types": _hfx_lm_types(),
            "machine_gb": machine, "roster_ids": _hfx_roster_ids(),
            "curated_ids": _hfx_curated_ids()}


# --------------------------------------------------------------------------
# the judgement — pure, so every case is testable without the network
# --------------------------------------------------------------------------
def _hfx_scope(files):
    """Where the weights are and which files a download takes.
    -> (sub, allow_patterns, selected_paths, block_reason|None)."""
    def risky(p):
        return p.lower().endswith(_HFX_RISKY + (".gguf",))
    root_st = [p for p in files if "/" not in p and p.endswith(".safetensors")]
    nested_st = [p for p in files if "/" in p and p.endswith(".safetensors")]
    if root_st:
        sel = [p for p in files if "/" not in p and not risky(p)
               and any(_hfx_fnmatch.fnmatchcase(p, a) for a in _HFX_ALLOW)]
        if not nested_st and not any("/" in p and any(_hfx_fnmatch.fnmatchcase(p, a)
                                                      for a in _HFX_ALLOW) for p in files):
            return "", list(_HFX_ALLOW), sel, None
        # the repo also carries other builds in subfolders: the globs would
        # pull those too (`*` crosses "/"), so name the root files exactly
        if len(sel) > _HFX_EXACT_MAX or any(not _HFX_FILE_RE.match(p) or
                                            _hfx_re.search(r"[*?\[\]]", p) for p in sel):
            return "", None, sel, ("the repo holds several builds and too many files to "
                                   "pick the right ones safely")
        return "", sorted(sel), sel, None
    dirs = {}
    for p in nested_st:
        d = p.rsplit("/", 1)[0]
        if (d + "/config.json") in files:
            dirs.setdefault(d, 0)
            dirs[d] += 1
    if not dirs:
        return "", None, [], ("no MLX weights (.safetensors) — GGUF, PyTorch .bin and other "
                              "formats do not run here")
    if len(dirs) > 1:
        pick = [d for d in dirs if _hfx_re.search(r"(^|[/_-])4[-_]?bit$", d, _hfx_re.I)]
        if len(pick) != 1:
            return "", None, [], ("the repo holds several builds in subfolders (%s); "
                                  "this browser adds single-build repos only"
                                  % ", ".join(sorted(dirs)[:4]))
        sub = pick[0]
    else:
        sub = next(iter(dirs))
    if sub.count("/") > 3 or len(sub) > 64 or not _hfx_re.fullmatch(r"[A-Za-z0-9._/-]+", sub) \
            or {".", ".."} & set(sub.split("/")):
        return "", None, [], "the weights sit in a folder name this app does not accept"
    pre = sub + "/"
    allow = [pre + a for a in _HFX_ALLOW]
    sel = [p for p in files if p.startswith(pre) and "/" not in p[len(pre):] and not risky(p)
           and any(_hfx_fnmatch.fnmatchcase(p, a) for a in allow)]
    return sub, allow, sel, None


def _hfx_derive(mid, meta, cfg, tokcfg, template, index, env):
    """Judge repo `mid` from what the Hub said. Pure.

    meta     the model-info JSON (?blobs=true)          dict
    cfg      config.json of the weights folder           dict | None
    tokcfg   tokenizer_config.json                       dict | None
    template the chat template text ("" when none)       str
    index    model.safetensors.index.json                dict | None
    env      {vlm_types, lm_types, machine_gb, roster_ids, curated_ids}
    """
    blocks, warns, notes = [], [], []
    meta = meta if isinstance(meta, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else None
    tokcfg = tokcfg if isinstance(tokcfg, dict) else {}
    template = template if isinstance(template, str) else ""
    tags = _hfx_tags(meta)
    sha = meta.get("sha") if isinstance(meta.get("sha"), str) and _HFX_SHA_RE.match(meta["sha"]) \
        else None
    files = {}
    for s in meta.get("siblings") or []:
        if isinstance(s, dict) and isinstance(s.get("rfilename"), str) \
                and len(s["rfilename"]) <= 300:
            files[s["rfilename"]] = _hfx_int(s.get("size")) or 0
    card = meta.get("cardData") if isinstance(meta.get("cardData"), dict) else {}
    library = meta.get("library_name") if isinstance(meta.get("library_name"), str) else None
    pipeline = meta.get("pipeline_tag") if isinstance(meta.get("pipeline_tag"), str) else None
    is_mlx = "mlx" in tags or library == "mlx"

    if meta.get("gated") not in (None, False):
        blocks.append("gated: Hugging Face asks for a login and an agreement first, and "
                      "downloads here run without a token")
    if meta.get("private") is True or meta.get("disabled") is True:
        blocks.append("private or disabled on Hugging Face")

    sub, allow, sel, scope_block = _hfx_scope(files)
    if scope_block:
        blocks.append(scope_block)
    if not is_mlx:
        blocks.append("not an MLX build — this app runs MLX weights only (see the MLX "
                      "builds of it below, if there are any)")
    risky = sorted(p for p in files if p.lower().endswith(_HFX_RISKY))
    if risky:
        warns.append("the repo carries %d code or pickle file(s) (%s); they are never "
                     "downloaded" % (len(risky), ", ".join(risky[:3])))

    if cfg is None and not scope_block:
        blocks.append("no readable config.json next to the weights")
    cfg = cfg or {}
    tc = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
    if cfg.get("auto_map") or tokcfg.get("auto_map") or tc.get("auto_map"):
        blocks.append("needs the repo's own Python code (auto_map); Hermes never runs a "
                      "model's code")
    mtype = cfg.get("model_type") if isinstance(cfg.get("model_type"), str) else None
    if mtype and not _HFX_TYPE_RE.match(mtype):
        mtype = None
    tok_class = tokcfg.get("tokenizer_class")
    tok_class = tok_class if isinstance(tok_class, str) and len(tok_class) <= 60 else None
    needs_tf5 = tok_class in ("TokenizersBackend",) or (tok_class or "").startswith("Qwen3_5")

    vlm, lm = env.get("vlm_types"), env.get("lm_types")
    backend = None
    if cfg and not mtype:
        blocks.append("config.json names no model_type")
    elif mtype:
        if vlm is not None and mtype in vlm:
            backend = "mlx_vlm"
        elif lm is not None and mtype in lm:
            if needs_tf5:
                blocks.append("its tokenizer (%s) needs the mlx-vlm runtime, which has no "
                              "loader for model type %s" % (tok_class, mtype))
            else:
                backend = "mlx_lm"
                warns.append("runs on the mlx-lm fallback (the mlx-vlm runtime has no loader "
                             "for %s): no image input, no speculative decoding" % mtype)
        elif vlm is None and lm is None:
            blocks.append("could not find a model runtime on this Mac to check model type %s"
                          % mtype)
        else:
            blocks.append("no loader for model type %s in this Mac's mlx-vlm%s"
                          % (mtype, " or mlx-lm" if lm is not None else
                             " (mlx-lm could not be checked)"))

    q = cfg.get("quantization") if isinstance(cfg.get("quantization"), dict) else None
    bits = _hfx_int((q or {}).get("bits"), 1, 16)
    qmode = (q or {}).get("mode")
    if isinstance(qmode, str) and qmode not in ("affine",):
        warns.append("uses %s quantization, which this setup has not run yet (every curated "
                     "model is affine)" % (qmode[:16] if _HFX_TYPE_RE.match(qmode) else "another"))
    if is_mlx and not q and cfg:
        warns.append("not quantized (full precision): about twice the memory of an 8-bit build "
                     "and slower")
    ctx = _hfx_int(tc.get("max_position_embeddings"), 512, 10_000_000) \
        or _hfx_int(cfg.get("max_position_embeddings"), 512, 10_000_000)
    vision = isinstance(cfg.get("vision_config"), dict)
    if cfg and not vision:
        notes.append("text-only: no image input (computer-use screenshot turns will not work)")

    if not template:
        t = tokcfg.get("chat_template")
        if isinstance(t, str):
            template = t
        elif isinstance(t, list):
            template = " ".join(x.get("template", "") for x in t
                                if isinstance(x, dict) and isinstance(x.get("template"), str))
    if not template and cfg:
        warns.append("no chat template: the agent cannot hold a conversation with it")
    elif template and not _HFX_TOOL_RE.search(template):
        warns.append("its chat template has no tool-calling section; the agent's tools may "
                     "not work")
    thinking = "enable_thinking" in template

    # weights + download size
    shards = []
    if isinstance(index, dict) and isinstance(index.get("weight_map"), dict):
        shards = sorted({v for v in index["weight_map"].values() if isinstance(v, str)})
    pre = (sub + "/") if sub else ""
    missing = [s for s in shards if (pre + s) not in files]
    if shards and not missing:
        wbytes = sum(files.get(pre + s, 0) for s in shards)
    else:
        if missing and not scope_block:
            # usually an index copied over from the source model; mlx_vlm copes,
            # but server.py _weights_complete and the launcher's _weights_ready
            # (mirrors) read it, so the model could never count as downloaded
            blocks.append("its weight index names %d file(s) the repo does not have (usually "
                          "an index left over from the original model): this app's download "
                          "check reads that index, so it could never count as downloaded"
                          % len(missing))
        wbytes = sum(sz for p, sz in files.items() if p in sel and p.endswith(".safetensors"))
    dl_bytes = sum(files.get(p, 0) for p in sel)
    w_gib = wbytes / 2 ** 30
    ram = int(_hfx_math.ceil(w_gib * 1.08 + 1.0)) if wbytes else None
    machine = env.get("machine_gb")
    fit = None
    fit_fn = _hfx_g("_model_fit")
    if ram and callable(fit_fn):
        try:
            fit = fit_fn(ram, machine) if machine else fit_fn(ram)
        except Exception:
            fit = None
    if fit == "no":
        blocks.append("needs ~%d GB of memory; this Mac has %s GB" % (ram, machine or "?"))
    elif fit == "tight":
        warns.append("needs ~%d GB of memory: tight on this Mac, expect swapping" % ram)
    if not wbytes and not scope_block:
        blocks.append("could not size the weights")

    lic = _hfx_license(card.get("license"))
    if lic is None:
        warns.append("the repo states no licence")
    unc = _hfx_is_uncensored(mid, tags)
    if unc:
        notes.append("uncensored: refusals were removed — the approval gate is the only "
                     "brake, and switching to it asks you to acknowledge that once")
    if pipeline and pipeline not in _HFX_PIPELINES:
        warns.append("tagged %s, not a chat model" % pipeline)
    notes.append("not curated: nobody has checked this build; it shows as unverified "
                 "until drilled")

    curated = _hfx_in(env.get("curated_ids") or (), mid)
    in_roster = _hfx_in(env.get("roster_ids") or (), mid)

    label = _hfx_clean_label(mid.split("/")[-1])
    bits_s = ("%d-bit" % bits) if bits else ("full precision" if cfg and not q else "")
    note_parts = (["no refusals"] if unc else []) + \
        ["from Hugging Face"] + ([bits_s] if bits_s else []) + \
        (["text-only"] if cfg and not vision else []) + ["unverified until drilled"]
    row = {"id": mid, "label": label or mid, "ram": ram, "note": " · ".join(note_parts)[:120],
           "uncensored": unc, "verified": False, "license": lic}
    if ctx:
        row["ctx"] = ctx
    if backend == "mlx_vlm":
        row["backend"] = "mlx_vlm"
    if thinking:
        row["thinking"] = True
        row["template_args"] = {"enable_thinking": False}
    if allow:
        row["allow_patterns"] = allow
    if sub:
        row["model_subfolder"] = sub
        row["hf_offline"] = True            # a partial mirror must start offline

    verdict = "no" if blocks else ("warn" if warns else "ok")
    facts = {"sha": sha, "library": library if library and len(library) <= 40 else None,
             "pipeline": pipeline if pipeline in _HFX_PIPELINES else None,
             "model_type": mtype, "backend": backend,
             "bits": bits, "vision": vision, "thinking": thinking, "ctx": ctx,
             "weights_gb": round(wbytes / 1e9, 2) if wbytes else None,
             "download_gb": round(dl_bytes / 1e9, 2) if dl_bytes else None,
             "ram_gb": ram, "fit": fit, "machine_gb": machine, "license": lic,
             "uncensored": unc, "files": len(sel), "subfolder": sub or None,
             "tokenizer_class": tok_class,
             "downloads": _hfx_int(meta.get("downloads")), "likes": _hfx_int(meta.get("likes")),
             "updated": _hfx_date(meta.get("lastModified")),
             "created": _hfx_date(meta.get("createdAt"))}
    base = card.get("base_model")
    if isinstance(base, list):
        base = base[0] if base else None
    facts["base_model"] = base if _hfx_valid_id(base) else None
    return {"id": mid, "sha": sha, "verdict": verdict, "blocks": blocks, "warns": warns,
            "notes": notes, "facts": facts, "row": row if verdict != "no" else None,
            "curated": curated, "in_roster": in_roster}


# --------------------------------------------------------------------------
# search / inspect / import
# --------------------------------------------------------------------------
def _hfx_row_from_listing(m, roster, curated):
    mid = m.get("id") if isinstance(m, dict) else None
    if not _hfx_valid_id(mid):
        return None
    pipeline = m.get("pipeline_tag") if isinstance(m.get("pipeline_tag"), str) else None
    if pipeline and pipeline not in _HFX_PIPELINES:
        return None
    tags = _hfx_tags(m)
    if "gguf" in tags and "mlx" not in tags:
        return None
    return {"id": mid, "downloads": _hfx_int(m.get("downloads")), "likes": _hfx_int(m.get("likes")),
            "updated": _hfx_date(m.get("lastModified")),
            "pipeline": pipeline, "gated": m.get("gated") not in (None, False),
            "bits": _hfx_bits(mid, tags), "uncensored": _hfx_is_uncensored(mid, tags),
            "vision": pipeline == "image-text-to-text",
            "in_roster": _hfx_in(roster, mid), "curated": _hfx_in(curated, mid)}


def _hfx_list(params):
    qs = _hfx_urlparse.urlencode(params, doseq=True)
    data = _hfx_json_get("/api/models?" + qs)
    if not isinstance(data, list):
        raise _HfxError("Hugging Face sent an unexpected answer", 502)
    return data


_HFX_EXPAND = ["downloads", "likes", "lastModified", "tags", "pipeline_tag", "gated",
               "library_name"]


def hfx_search(q, limit=30):
    q = _hfx_re.sub(r"[\x00-\x1f\x7f]", " ", q if isinstance(q, str) else "").strip()[:_HFX_Q_MAX]
    limit = max(1, min(_HFX_LIMIT_MAX, _hfx_int(limit, 1, 1000) or 30))
    t0 = _hfx_time.time()
    params = [("filter", "mlx"), ("sort", "downloads"), ("direction", "-1"),
              ("limit", str(limit))] + [("expand[]", e) for e in _HFX_EXPAND]
    if q:
        params.insert(0, ("search", q))
    raw, cached = _hfx_cached("s:%s:%d" % (q.lower(), limit), lambda: _hfx_list(params))
    roster, curated = _hfx_roster_ids(), _hfx_curated_ids()
    out = [r for r in (_hfx_row_from_listing(m, roster, curated) for m in raw) if r]
    return {"ok": True, "q": q, "results": out, "cached": cached,
            "took_ms": int((_hfx_time.time() - t0) * 1000)}


def _hfx_file(mid, sha, path, as_json=True):
    if not _HFX_FILE_RE.match(path) or ".." in path.split("/"):
        return None
    try:
        raw = _hfx_fetch("/%s/resolve/%s/%s" % (mid, sha, _hfx_urlparse.quote(path)),
                         _HFX_MAX_FILE)
    except _HfxError as e:
        if e.status in (404, 413):      # absent, or too big to be a config: unreadable
            return None
        raise
    try:
        txt = raw.decode("utf-8")
    except ValueError:
        return None
    if not as_json:
        return txt
    try:
        v = _hfx_json.loads(txt)
    except (ValueError, RecursionError):
        return None
    return v if isinstance(v, dict) else None


def _hfx_inspect_raw(mid):
    meta = _hfx_json_get("/api/models/%s/revision/main?blobs=true" % mid)
    if not isinstance(meta, dict):
        raise _HfxError("Hugging Face sent an unexpected answer", 502)
    sha = meta.get("sha") if isinstance(meta.get("sha"), str) and _HFX_SHA_RE.match(
        meta.get("sha")) else None
    files = {s.get("rfilename") for s in meta.get("siblings") or [] if isinstance(s, dict)}
    cfg = tokcfg = index = None
    template = ""
    if sha and meta.get("gated") in (None, False):
        sub, _a, _s, block = _hfx_scope({f: 0 for f in files if isinstance(f, str)})
        pre = (sub + "/") if sub else ""
        if not block:
            cfg = _hfx_file(mid, sha, pre + "config.json")
            if (pre + "tokenizer_config.json") in files:
                tokcfg = _hfx_file(mid, sha, pre + "tokenizer_config.json")
            if (pre + "chat_template.jinja") in files:
                template = _hfx_file(mid, sha, pre + "chat_template.jinja", as_json=False) or ""
            elif (pre + "chat_template.json") in files:     # Gemma & co.
                ct = _hfx_file(mid, sha, pre + "chat_template.json") or {}
                template = ct.get("chat_template") if isinstance(ct.get("chat_template"), str) else ""
            if (pre + "model.safetensors.index.json") in files:
                index = _hfx_file(mid, sha, pre + "model.safetensors.index.json")
    alts = []
    tags = _hfx_tags(meta)
    if "mlx" not in tags and meta.get("library_name") != "mlx":
        try:
            lst = _hfx_list([("filter", "base_model:quantized:" + mid), ("filter", "mlx"),
                             ("sort", "downloads"), ("direction", "-1"), ("limit", "8")]
                            + [("expand[]", e) for e in _HFX_EXPAND])
            alts = lst
        except _HfxError:
            alts = []
    return {"meta": meta, "cfg": cfg, "tokcfg": tokcfg, "template": template,
            "index": index, "alts": alts}


def hfx_inspect(mid, fresh=False):
    if not _hfx_valid_id(mid):
        return {"ok": False, "error": "not a Hugging Face repo id (org/name)"}, 400
    if fresh:
        with _hfx_lock:
            _hfx_cache.pop("i:" + mid.lower(), None)
    raw, cached = _hfx_cached("i:" + mid.lower(), lambda: _hfx_inspect_raw(mid))
    env = _hfx_env()
    res = _hfx_derive(mid, raw["meta"], raw["cfg"], raw["tokcfg"], raw["template"],
                      raw["index"], env)
    roster, curated = env["roster_ids"], env["curated_ids"]
    res["alternatives"] = [r for r in (_hfx_row_from_listing(m, roster, curated)
                                       for m in raw.get("alts") or []) if r][:8]
    res["ok"] = True
    res["cached"] = cached
    return res, 200


# --------------------------------------------------------------------------
# hf-imports.json: what was inspected when a model was added (0600)
# --------------------------------------------------------------------------
def _hfx_store_path():
    d = _hfx_g("DATA")
    d = d if isinstance(d, str) and d else _hfx_os.path.join(HOME, ".hermes", "dashboard")  # noqa
    return _hfx_os.path.join(d, "hf-imports.json")


def _hfx_store_read():
    try:
        v = _hfx_g("read_json")(_hfx_store_path(), {})
    except Exception:
        v = {}
    return v if isinstance(v, dict) else {}


def _hfx_import_size_gb(mid):
    """The download size recorded when `mid` was added here, or None — read by
    server.py _catalog_dl_gb (by name) so the menu quotes it and the disk gate
    judges it like a curated model's."""
    rec = _hfx_store_read().get(mid) if isinstance(mid, str) else None
    if not isinstance(rec, dict):
        low = (mid or "").lower()
        rec = next((v for k, v in _hfx_store_read().items()
                    if isinstance(k, str) and k.lower() == low), None)
    try:
        gb = float((rec or {}).get("download_gb") or 0)
    except (TypeError, ValueError):
        return None
    return gb if gb > 0 and gb == gb and gb < 10_000 else None


def _hfx_store_record(mid, res):
    f = res.get("facts") or {}
    rec = {"sha": res.get("sha"), "download_gb": f.get("download_gb"),
           "weights_gb": f.get("weights_gb"), "verdict": res.get("verdict"),
           "model_type": f.get("model_type"), "backend": f.get("backend"),
           "added_at": int(_hfx_time.time())}
    with _hfx_store_lock:
        rfu = _hfx_g("read_json_for_update")
        cur = rfu(_hfx_store_path(), {}) if callable(rfu) else _hfx_store_read()
        if not isinstance(cur, dict):
            cur = {}
        cur[mid] = rec
        _hfx_g("write_json")(_hfx_store_path(), cur)


def hfx_import(body):
    body = body if isinstance(body, dict) else {}
    mid = body.get("id")
    if not _hfx_valid_id(mid):
        return {"ok": False, "error": "not a Hugging Face repo id (org/name)"}, 400
    for k in ("confirm", "download"):
        if k in body and not isinstance(body[k], bool):
            return {"ok": False, "error": "%s must be true or false" % k}, 400
    want_dl = body.get("download") is True
    sha_in = body.get("sha")
    if sha_in is not None and not (isinstance(sha_in, str) and _HFX_SHA_RE.match(sha_in)):
        return {"ok": False, "error": "sha must be a 40-character commit id"}, 400

    curated_ids = _hfx_curated_ids()
    if _hfx_in(curated_ids, mid):
        # a curated id keeps its checked shape — add_model knows it
        r = _hfx_g("add_model")(mid)
        if not r.get("ok"):
            return r, 400
        out = {"ok": True, "id": mid, "curated": True, "added": True}
        if want_dl:
            out["download"] = _hfx_g("download_model")(next(
                (i for i in curated_ids if i.lower() == mid.lower()), mid))
        return out, 200

    if body.get("confirm") is not True:
        return {"ok": False, "needs_confirm": True,
                "error": "not on the curated list — send confirm:true to add it "
                         "(weights only: no remote code, no pickle files)"}, 400
    # E104: never a second row for the same repo in another letter case
    reg_ids = _hfx_roster_ids()
    clash = next((i for i in reg_ids if i.lower() == mid.lower() and i != mid), None)
    if clash:
        return {"ok": False, "error": "already listed as %s (model ids are not "
                                      "case-sensitive) — use that id" % clash}, 409
    try:
        res, st = hfx_inspect(mid, fresh=True)
    except _HfxError as e:
        return {"ok": False, "error": str(e)}, e.status if e.status in (403, 404, 429) else 502
    if st != 200:
        return res, st
    if sha_in and res.get("sha") and sha_in != res["sha"]:
        return {"ok": False, "stale": True, "sha": res["sha"],
                "error": "the repo changed since you looked at it — review it again"}, 409
    if res["verdict"] == "no" or not res.get("row"):
        return {"ok": False, "verdict": "no", "blocks": res.get("blocks") or [],
                "error": "this model cannot run here: " + "; ".join(res.get("blocks") or [])}, 422
    row = dict(res["row"])
    derived = ("label", "ram", "note", "ctx", "backend", "thinking", "template_args",
               "allow_patterns", "model_subfolder", "hf_offline", "uncensored",
               "verified", "license")
    state = {"added": False, "updated": False}

    def _put(reg):                          # E113: re-checked under the lock
        for m in reg:
            if str(m.get("id") or "") == mid:
                for k in derived:
                    if k in row:
                        m[k] = row[k]
                    elif k in ("backend", "thinking", "template_args", "model_subfolder",
                               "hf_offline"):
                        m.pop(k, None)
                state["updated"] = True
                return None
        reg.append(row)
        state["added"] = True
        return None
    try:
        _hfx_g("models_update")(_put)
    except Exception as e:  # noqa: BLE001 - an unreadable models.json writes nothing
        return {"ok": False, "error": "could not update the model list (%s)"
                                      % type(e).__name__}, 500
    try:
        _hfx_store_record(mid, res)
    except Exception as e:  # noqa: BLE001
        print("[hfhub] could not record %s: %s" % (mid, type(e).__name__),
              file=sys.stderr, flush=True)                                  # noqa: F821
    try:
        _hfx_g("_widget_cache", {}).pop("adv_advice", None)
    except Exception:
        pass
    out = {"ok": True, "id": mid, "curated": False, "verdict": res["verdict"],
           "added": state["added"], "updated": state["updated"],
           "uncensored": bool(row.get("uncensored"))}
    if want_dl:
        out["download"] = _hfx_g("download_model")(mid)
    return out, 200


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _hfx_q(ctx, key, default=""):
    v = (getattr(ctx, "query", None) or {}).get(key)
    if isinstance(v, list):
        v = v[0] if v else default
    return v if isinstance(v, str) else default


def _hfx_cross_site(ctx):
    """A page on another site can NAVIGATE to a GET here (the same-origin
    guard lets a navigation through); these GETs make outbound requests, so
    a cross-site or same-site fetch/navigation is refused. curl and the app's
    own fetches (same-origin, or no header) pass."""
    h = getattr(ctx, "headers", None) or {}
    return (h.get("sec-fetch-site") or "").lower() in ("cross-site", "same-site")


def _hfx_route_search(ctx):
    if _hfx_cross_site(ctx):
        return {"ok": False, "error": "forbidden"}, 403
    lim = _hfx_q(ctx, "limit", "30")
    try:
        return hfx_search(_hfx_q(ctx, "q"), int(lim) if lim.isdigit() and len(lim) < 6 else 30)
    except _HfxError as e:
        return {"ok": False, "error": str(e)}, e.status if e.status in (403, 404, 429) else 502


def _hfx_route_inspect(ctx):
    if _hfx_cross_site(ctx):
        return {"ok": False, "error": "forbidden"}, 403
    try:
        return hfx_inspect(_hfx_q(ctx, "id").strip(), fresh=_hfx_q(ctx, "fresh") == "1")
    except _HfxError as e:
        return {"ok": False, "error": str(e)}, e.status if e.status in (403, 404, 429) else 502


def _hfx_route_import(ctx):
    if _hfx_cross_site(ctx):
        return {"ok": False, "error": "forbidden"}, 403
    return hfx_import(getattr(ctx, "body", None))


register_get("/api/models/hf/search", _hfx_route_search)                   # noqa: F821
register_get("/api/models/hf/inspect", _hfx_route_inspect)                 # noqa: F821
register_post("/api/models/hf/import", _hfx_route_import)                  # noqa: F821
