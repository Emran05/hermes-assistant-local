#!/usr/bin/env python3
"""Browse Hugging Face (aux_hfhub.py, 2026-11-12): search, inspect, import.

Offline: the module's one outbound function, _hfx_fetch, is replaced by a
router over in-memory fixtures, and the runtime's model-type lists are set by
hand, so nothing here reaches huggingface.co or runs a subprocess. The REAL
server.py is loaded (temp HOME), so add/import go through the real
models_update / _model_registry / _catalog_dl_gb / _switch_ack_refusal.

Proves:
  * the judgement: MLX-only, no auto_map, no gated repo, no GGUF/.bin-only
    repo, a loader for the model_type (mlx-vlm first, mlx-lm only for a
    tokenizer it can load), a fit check against this Mac, single-build scope
    (a 4bit/ subfolder is picked and pinned, a root build beside other builds
    gets an exact file list, a repo of several unnamed builds is refused);
  * the derived roster row: backend, thinking + template_args off, ctx,
    uncensored, verified:false, licence, never trust_remote_code;
  * Hub strings are reduced to validated shapes (ids, dates, licences,
    labels without control/bidi characters);
  * the routes: cross-site refused, caching, confirm needed, a "no" writes
    nothing, a changed sha is stale, a case clash is refused, re-import
    updates in place, hf-imports.json is 0600 and feeds the disk gate, an
    imported uncensored model is behind the switch acknowledgement, a curated
    id goes through add_model;
  * the redirect handler refuses anything off https://huggingface.co.
"""
import importlib.util
import json
import os
import stat
import sys
import tempfile

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


TMP = tempfile.mkdtemp(prefix="hermes-hfhub-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_hfhub", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_hfhub"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME
srv._bg_probe = lambda: False

# the runtime this "Mac" has: mlx-vlm knows qwen3_5 + llama, mlx-lm knows llama + phi3
srv._hfx_types_memo["vlm"] = frozenset({"qwen3_5", "qwen3_5_moe", "llama"})
srv._hfx_types_memo["lm"] = frozenset({"llama", "phi3"})
srv._machine_ram_gb = lambda: 64
srv.download_model = lambda mid: {"ok": True, "status": "downloading", "id": mid}

SHA = "a" * 40
SHA2 = "b" * 40
TPL = "{% for m in messages %}{{ m.content }}{% endfor %}{% if tools %}<tool_call>{% endif %}" \
      "{% if enable_thinking %}<think>{% endif %}"
QCFG = {"model_type": "qwen3_5", "architectures": ["Qwen3_5ForConditionalGeneration"],
        "vision_config": {"depth": 27},
        "text_config": {"model_type": "qwen3_5_text", "max_position_embeddings": 262144},
        "quantization": {"group_size": 64, "bits": 4, "mode": "affine"}}
GB = 10 ** 9


def meta(mid, files, tags=("mlx", "4-bit", "safetensors"), lib="mlx", sha=SHA, **kw):
    m = {"id": mid, "sha": sha, "tags": list(tags), "library_name": lib,
         "pipeline_tag": kw.pop("pipeline", "text-generation"), "gated": kw.pop("gated", False),
         "private": False, "disabled": False, "downloads": 1234, "likes": 5,
         "lastModified": "2026-11-08T03:38:20.000Z", "createdAt": "2026-11-01T00:00:00.000Z",
         "cardData": {"license": kw.pop("license", "apache-2.0"), "base_model": "Org/Base"},
         "siblings": [{"rfilename": f, "size": s} for f, s in files.items()]}
    m.update(kw)
    return m


def std_files(pre="", shards=2, extra=None):
    f = {pre + "config.json": 3000, pre + "tokenizer_config.json": 1200,
         pre + "tokenizer.json": 20_000_000, pre + "chat_template.jinja": 8000,
         pre + "model.safetensors.index.json": 120_000, pre + "generation_config.json": 140}
    for i in range(shards):
        f[pre + "model-%05d-of-%05d.safetensors" % (i + 1, shards)] = 3 * GB
    f.update(extra or {})
    return f


def index(shards=2):
    return {"weight_map": {"w%d" % i: "model-%05d-of-%05d.safetensors" % (i + 1, shards)
                           for i in range(shards)}}


REPOS = {}       # id -> {"meta":..., "files": {path: bytes|dict|str}}


def add_repo(mid, m, raw=None):
    REPOS[mid] = {"meta": m, "raw": raw or {}}


def qwen_raw(pre="", cfg=None, tok=None, tpl=TPL, shards=2):
    return {pre + "config.json": cfg if cfg is not None else QCFG,
            pre + "tokenizer_config.json": tok if tok is not None else
            {"tokenizer_class": "TokenizersBackend"},
            pre + "chat_template.jinja": tpl,
            pre + "model.safetensors.index.json": index(shards)}


add_repo("org/Good-9B-MLX-4bit", meta("org/Good-9B-MLX-4bit", std_files(extra={
    "README.md": 9000, "convert.py": 400})), qwen_raw())
add_repo("org/Heretic-9B-abliterated-MLX", meta("org/Heretic-9B-abliterated-MLX", std_files()),
         qwen_raw())
add_repo("org/Big-Unquantized", meta("org/Big-Unquantized", std_files(),
                                     tags=("transformers", "safetensors"), lib="transformers"),
         qwen_raw(cfg=dict(QCFG, quantization=None)))
add_repo("org/Custom-Code-MLX", meta("org/Custom-Code-MLX", std_files()),
         qwen_raw(cfg=dict(QCFG, auto_map={"AutoModel": "modeling.Model"})))
add_repo("org/Only-GGUF", meta("org/Only-GGUF", {"model-q4.gguf": 5 * GB, "README.md": 1},
                               tags=("gguf", "mlx")), {})
add_repo("org/Gated-MLX", meta("org/Gated-MLX", std_files(), gated="manual"), qwen_raw())
add_repo("org/Variants-MLX", meta("org/Variants-MLX", dict(std_files("4bit/"), **std_files("8bit/"),
                                                         **{"README.md": 10})),
         qwen_raw("4bit/"))
add_repo("org/Root-Plus-Variants", meta("org/Root-Plus-Variants",
                                        dict(std_files(), **std_files("8bit/"))), qwen_raw())
add_repo("org/Many-Builds", meta("org/Many-Builds", dict(std_files("a/"), **std_files("b/"))),
         qwen_raw("a/"))
add_repo("org/Llama-TextOnly-NoTpl", meta("org/Llama-TextOnly-NoTpl", std_files(shards=1)),
         qwen_raw(cfg={"model_type": "llama", "max_position_embeddings": 131072,
                       "quantization": {"bits": 4, "group_size": 64}},
                  tok={"tokenizer_class": "PreTrainedTokenizerFast"}, tpl="", shards=1))
add_repo("org/Phi-Lm-Only", meta("org/Phi-Lm-Only", std_files(shards=1)),
         qwen_raw(cfg={"model_type": "phi3", "max_position_embeddings": 4096,
                       "quantization": {"bits": 4, "group_size": 64}},
                  tok={"tokenizer_class": "LlamaTokenizerFast"}, shards=1))
add_repo("org/Phi-Needs-TF5", meta("org/Phi-Needs-TF5", std_files(shards=1)),
         qwen_raw(cfg={"model_type": "phi3", "quantization": {"bits": 4, "group_size": 64}},
                  tok={"tokenizer_class": "TokenizersBackend"}, shards=1))
add_repo("org/Weird-Type", meta("org/Weird-Type", std_files(shards=1)),
         qwen_raw(cfg={"model_type": "brandnew", "quantization": {"bits": 4}}, shards=1))
add_repo("org/Huge-MLX", meta("org/Huge-MLX", std_files(shards=20)), qwen_raw(shards=20))
add_repo("org/Hostile-MLX", meta("org/Hostile-MLX‮", std_files(),
                                 license="apache 2.0 <script>", lastModified="<b>soon</b>"),
         qwen_raw())
# Gemma-style: chat_template.json, a tokenizer_config too big to read
add_repo("org/Gemma-Style-MLX", meta("org/Gemma-Style-MLX", dict(
    {k: v for k, v in std_files().items() if not k.endswith(".jinja")},
    **{"chat_template.json": 1600, "tokenizer.model": 4_000_000})),
    {"config.json": QCFG, "tokenizer_config.json": "TOO_BIG",
     "chat_template.json": {"chat_template": TPL},
     "model.safetensors.index.json": index()})
# a stale index that names shards the repo never uploaded (lmstudio Mistral)
add_repo("org/Stale-Index-MLX", meta("org/Stale-Index-MLX", std_files(shards=3)),
         qwen_raw(shards=10))
add_repo("org/NoLicence-MLX", meta("org/NoLicence-MLX", std_files(), license=None), qwen_raw())

LISTINGS = {
    "base_model:quantized:org/Big-Unquantized": [
        {"id": "org/Good-9B-MLX-4bit", "downloads": 10, "tags": ["mlx", "4-bit"],
         "pipeline_tag": "text-generation", "lastModified": "2026-11-08T03:38:20.000Z"}],
}
SEARCH = [
    {"id": "org/Good-9B-MLX-4bit", "downloads": 18933, "likes": 3, "tags": ["mlx", "4-bit"],
     "pipeline_tag": "text-generation", "lastModified": "2026-11-08T03:38:20.000Z", "gated": False},
    {"id": "<script>alert(1)</script>/x", "downloads": 1, "tags": ["mlx"]},
    {"id": "org/Whisper-MLX", "downloads": 5, "tags": ["mlx"], "pipeline_tag": "automatic-speech-recognition"},
    {"id": "org/Uncensored-8bit-MLX", "downloads": "lots", "likes": True, "tags": ["mlx", "8-bit"],
     "pipeline_tag": "image-text-to-text", "lastModified": "yesterday", "gated": "auto"},
    {"id": "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit", "downloads": 1325,
     "tags": ["mlx", "4-bit", "uncensored"], "pipeline_tag": "text-generation"},
]
CALLS = []


class _Err(Exception):
    pass


def fake_fetch(path, cap):
    CALLS.append(path)
    import urllib.parse as up
    if path.startswith("/api/models?"):
        qs = up.parse_qs(path.split("?", 1)[1])
        for f in qs.get("filter", []):
            if f in LISTINGS:
                return json.dumps(LISTINGS[f]).encode()
        if any(f.startswith("base_model:") for f in qs.get("filter", [])):
            return b"[]"
        return json.dumps(SEARCH).encode()
    if path.startswith("/api/models/"):
        mid = path[len("/api/models/"):].split("/revision/")[0]
        r = REPOS.get(mid)
        if not r:
            raise srv._HfxError("not found on Hugging Face", 404)
        return json.dumps(r["meta"]).encode()
    # /<org>/<name>/resolve/<sha>/<path>
    parts = path.lstrip("/").split("/")
    mid, sha, fp = parts[0] + "/" + parts[1], parts[3], up.unquote("/".join(parts[4:]))
    r = REPOS.get(mid)
    if not r or fp not in r["raw"]:
        raise srv._HfxError("not found on Hugging Face", 404)
    v = r["raw"][fp]
    if v == "TOO_BIG":
        raise srv._HfxError("Hugging Face sent more than expected", 413)
    return (v if isinstance(v, str) else json.dumps(v)).encode()


srv._hfx_fetch = fake_fetch


def inspect(mid):
    res, st = srv.hfx_inspect(mid, fresh=True)
    return res


class Ctx:
    def __init__(self, query=None, body=None, headers=None):
        self.query = {k: [v] for k, v in (query or {}).items()}
        self.body = body if body is not None else {}
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.raw_path = ""


def route(path, verb="GET", **kw):
    fn = (srv.GET_ROUTES if verb == "GET" else srv.POST_ROUTES)[path]
    out = fn(Ctx(**kw))
    return out if isinstance(out, tuple) else (out, 200)


def models():
    return {m["id"]: m for m in srv._model_registry()}


# --------------------------------------------------------------------------
print("1. the judgement")
r = inspect("org/Good-9B-MLX-4bit")
check("a stock MLX qwen3_5 build: ok", r["verdict"] in ("ok", "warn") and not r["blocks"], r)
check("...with one warning, the .py file it never downloads",
      len(r["warns"]) == 1 and "never downloaded" in r["warns"][0], r["warns"])
row = r["row"]
check("row: mlx_vlm backend, thinking with template_args off, ctx from text_config",
      row["backend"] == "mlx_vlm" and row["thinking"] is True
      and row["template_args"] == {"enable_thinking": False} and row["ctx"] == 262144, row)
check("row: verified false, licence kept, not uncensored, no trust_remote_code",
      row["verified"] is False and row["license"] == "apache-2.0" and row["uncensored"] is False
      and "trust_remote_code" not in row, row)
check("row: the standard allow list (no subfolders to worry about)",
      row["allow_patterns"] == srv._HFX_ALLOW, row.get("allow_patterns"))
check("facts: weights 6 GB from the index, download excludes README/.py",
      r["facts"]["weights_gb"] == 6.0 and r["facts"]["download_gb"] == round(
          (6 * GB + 3000 + 1200 + 20_000_000 + 8000 + 120_000 + 140) / 1e9, 2), r["facts"])
check("ram covers the weights (5.6 GiB -> 8 GB) and fits a 64 GB Mac",
      row["ram"] == 8 and r["facts"]["fit"] == "ok", (row["ram"], r["facts"]["fit"]))
check("label is the repo name, sha pinned", row["label"] == "Good-9B-MLX-4bit" and r["sha"] == SHA)

r = inspect("org/Heretic-9B-abliterated-MLX")
check("an abliterated build is marked uncensored (name)", r["row"]["uncensored"] is True
      and "no refusals" in r["row"]["note"] and any("uncensored" in n for n in r["notes"]), r["row"])

r = inspect("org/Big-Unquantized")
check("a transformers (non-MLX) repo: no, with its MLX builds as alternatives",
      r["verdict"] == "no" and r["row"] is None and any("not an MLX build" in b for b in r["blocks"])
      and [a["id"] for a in r["alternatives"]] == ["org/Good-9B-MLX-4bit"], r)
check("...the alternatives come from the base_model:quantized filter + mlx",
      any("base_model%3Aquantized%3Aorg%2FBig-Unquantized" in c and "filter=mlx" in c for c in CALLS))

r = inspect("org/Custom-Code-MLX")
check("auto_map (the repo's own code): no", r["verdict"] == "no"
      and any("auto_map" in b for b in r["blocks"]), r["blocks"])
r = inspect("org/Only-GGUF")
check("GGUF only: no, and no file was fetched for it",
      r["verdict"] == "no" and any("GGUF" in b for b in r["blocks"])
      and not any(c.startswith("/org/Only-GGUF/resolve") for c in CALLS), r["blocks"])
r = inspect("org/Gated-MLX")
check("gated: no (downloads run without a token), and nothing was resolved",
      r["verdict"] == "no" and any("gated" in b for b in r["blocks"])
      and not any(c.startswith("/org/Gated-MLX/resolve") for c in CALLS), r["blocks"])

r = inspect("org/Variants-MLX")
row = r["row"]
check("4bit/ + 8bit/: the 4bit build is picked, pinned and starts offline",
      r["verdict"] != "no" and row["model_subfolder"] == "4bit" and row["hf_offline"] is True
      and all(p.startswith("4bit/") for p in row["allow_patterns"]), row)
check("...and the download is the 4bit folder only", r["facts"]["weights_gb"] == 6.0
      and r["facts"]["download_gb"] < 6.2, r["facts"])

r = inspect("org/Root-Plus-Variants")
pats = r["row"]["allow_patterns"]
check("root build beside an 8bit/ folder: exact root file names, nothing under 8bit/",
      r["verdict"] != "no" and "*" not in "".join(pats) and all("/" not in p for p in pats)
      and "model-00001-of-00002.safetensors" in pats and len(pats) <= 16, pats)
r = inspect("org/Many-Builds")
check("several builds in unnamed subfolders: no", r["verdict"] == "no"
      and any("several builds" in b for b in r["blocks"]), r["blocks"])

r = inspect("org/Llama-TextOnly-NoTpl")
check("text-only, no chat template: warn, with the text-only note",
      r["verdict"] == "warn" and any("no chat template" in w for w in r["warns"])
      and any("text-only" in n for n in r["notes"]) and "thinking" not in r["row"], r)
r = inspect("org/Phi-Lm-Only")
check("a type only mlx-lm loads: allowed on the fallback (warned, no backend key)",
      r["verdict"] == "warn" and "backend" not in r["row"] and r["facts"]["backend"] == "mlx_lm"
      and any("mlx-lm fallback" in w for w in r["warns"]), r)
r = inspect("org/Phi-Needs-TF5")
check("...but not when its tokenizer needs transformers 5 (mlx-vlm only)",
      r["verdict"] == "no" and any("TokenizersBackend" in b for b in r["blocks"]), r["blocks"])
r = inspect("org/Weird-Type")
check("a model_type no runtime here knows: no", r["verdict"] == "no"
      and any("brandnew" in b for b in r["blocks"]), r["blocks"])
r = inspect("org/Huge-MLX")
check("60 GB of weights on a 64 GB Mac: no (fit)", r["verdict"] == "no"
      and r["facts"]["fit"] == "no" and any("memory" in b for b in r["blocks"]), r["facts"])
REPOS["org/Good-9B-MLX-4bit"]["raw"]["config.json"] = dict(QCFG, quantization={"bits": 8, "mode": "mxfp8"})
r = inspect("org/Good-9B-MLX-4bit")
check("a non-affine quantization mode (mxfp8) is a caveat, not a refusal",
      r["verdict"] == "warn" and any("mxfp8" in w for w in r["warns"]), r["warns"])
REPOS["org/Good-9B-MLX-4bit"]["raw"]["config.json"] = QCFG
r = inspect("org/Gemma-Style-MLX")
check("a template in chat_template.json is read; an oversized tokenizer_config is skipped, "
      "not fatal", r["verdict"] == "ok" and r["row"]["thinking"] is True and not r["warns"], r)
r = inspect("org/Stale-Index-MLX")
check("an index naming shards the repo lacks: no (it could never read as downloaded), "
      "but the weights are still sized", r["verdict"] == "no"
      and any("left over from the original model" in b for b in r["blocks"]) and r["facts"]["ram_gb"] == 11
      and not any("could not size" in b for b in r["blocks"]), r)
r = inspect("org/NoLicence-MLX")
check("no licence stated: a warning, licence None", r["row"]["license"] is None
      and any("licence" in w for w in r["warns"]))

r = inspect("org/Hostile-MLX")
check("a licence that is not an id becomes None, a bad date None",
      r["row"]["license"] is None and r["facts"]["updated"] is None, r["facts"])
lbl = srv._hfx_clean_label("Name‮evil​\x07 ")
check("labels lose control and bidi characters", lbl == "Nameevil", repr(lbl))

res, st = srv.hfx_inspect("not an id")
check("inspect refuses a non-id (400)", st == 400 and res["ok"] is False)
res, st = route("/api/models/hf/inspect", query={"id": "org/Missing"})
check("a repo the Hub does not have: 404", st == 404 and res["ok"] is False, (st, res))

# --------------------------------------------------------------------------
print("\n2. search")
n0 = len(CALLS)
res, st = route("/api/models/hf/search", query={"q": "  qwen\x00 9b  ", "limit": "999"})
ids = [x["id"] for x in res["results"]]
check("search answers ok, q cleaned", st == 200 and res["ok"] and res["q"] == "qwen  9b", res.get("q"))
check("search asks for MLX only, sorted by downloads, limit clamped to 40",
      "filter=mlx" in CALLS[-1] and "sort=downloads" in CALLS[-1] and "limit=40" in CALLS[-1], CALLS[-1])
check("an id that is not org/name and a non-chat pipeline are dropped",
      ids == ["org/Good-9B-MLX-4bit", "org/Uncensored-8bit-MLX",
              "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"], ids)
by = {x["id"]: x for x in res["results"]}
u = by["org/Uncensored-8bit-MLX"]
check("wrong-typed counts become None, a bad date None, gated any non-false",
      u["downloads"] is None and u["likes"] is None and u["updated"] is None and u["gated"] is True, u)
check("bits from the tag, uncensored from the name, vision from the pipeline",
      u["bits"] == 8 and u["uncensored"] is True and u["vision"] is True, u)
check("the curated seed is flagged curated and in the roster",
      by["digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"]["curated"] is True
      and by["digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"]["in_roster"] is True)
n1 = len(CALLS)
res2, _ = route("/api/models/hf/search", query={"q": "QWEN  9B", "limit": "999"})
check("the same search within 10 min is served from the cache (no request)",
      len(CALLS) == n1 and res2["cached"] is True, CALLS[n1:])
res, st = route("/api/models/hf/search", query={"q": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
check("a cross-site request is refused (403) before any fetch", st == 403 and len(CALLS) == n1)
res, st = route("/api/models/hf/inspect", query={"id": "org/Good-9B-MLX-4bit"},
                headers={"Sec-Fetch-Site": "same-site"})
check("...same-site too (inspect)", st == 403)

# --------------------------------------------------------------------------
print("\n3. import")
GOOD = "org/Good-9B-MLX-4bit"
res, st = route("/api/models/hf/import", "POST", body={"id": GOOD})
check("import without confirm: 400 needs_confirm, nothing written",
      st == 400 and res.get("needs_confirm") and GOOD not in models())
res, st = route("/api/models/hf/import", "POST", body={"id": GOOD, "confirm": "true"})
check("confirm must be a JSON boolean", st == 400 and GOOD not in models(), res)
res, st = route("/api/models/hf/import", "POST", body={"id": "org/Big-Unquantized", "confirm": True})
check("a 'no' is refused (422) and writes nothing", st == 422 and res.get("blocks")
      and "org/Big-Unquantized" not in models(), (st, res))
res, st = route("/api/models/hf/import", "POST", body={"id": GOOD, "confirm": True, "sha": SHA2})
check("a sha that is not the Hub's current one: 409 stale, nothing written",
      st == 409 and res.get("stale") and res.get("sha") == SHA and GOOD not in models(), res)
res, st = route("/api/models/hf/import", "POST", body={"id": GOOD, "confirm": True, "sha": SHA})
m = models().get(GOOD) or {}
check("import adds the derived row", st == 200 and res["added"] is True and m.get("backend") == "mlx_vlm"
      and m.get("template_args") == {"enable_thinking": False} and m.get("verified") is False
      and m.get("ram") == 8 and "trust_remote_code" not in m, (res, m))
p = os.path.join(srv.DATA, "hf-imports.json")
rec = json.load(open(p)).get(GOOD) or {}
check("hf-imports.json records the sha and size, mode 0600",
      rec.get("sha") == SHA and rec.get("download_gb") and stat.S_IMODE(os.stat(p).st_mode) == 0o600,
      (rec, oct(os.stat(p).st_mode)))
check("the disk gate / menu quote that size (_catalog_dl_gb)",
      srv._catalog_dl_gb(GOOD) == round(rec["download_gb"], 1), srv._catalog_dl_gb(GOOD))
mp = next(x for x in srv.models_payload()["models"] if x["id"] == GOOD)
check("models_payload shows it unverified, with a download size and a fit",
      mp["verified"] is False and mp["download_gb"] and mp["fit"] == "ok", mp)

REPOS[GOOD]["raw"]["config.json"] = dict(QCFG, vision_config=None)
res, st = route("/api/models/hf/import", "POST", body={"id": GOOD, "confirm": True, "download": True})
rows = [x for x in srv._model_registry() if x["id"] == GOOD]
check("re-import updates the one row in place (now text-only) and starts the download",
      st == 200 and res["updated"] is True and len(rows) == 1 and "text-only" in rows[0]["note"]
      and res["download"]["ok"] is True, (res, rows))
res, st = route("/api/models/hf/import", "POST", body={"id": "ORG/good-9b-mlx-4BIT", "confirm": True})
check("another letter case of a listed id is refused (409)", st == 409
      and "already listed" in res["error"], res)

HER = "org/Heretic-9B-abliterated-MLX"
res, st = route("/api/models/hf/import", "POST", body={"id": HER, "confirm": True})
check("an uncensored import says so", st == 200 and res["uncensored"] is True)
refusal = srv._switch_ack_refusal(HER, False)
check("...and switching to it needs the acknowledgement (409 uncensored_ack)",
      refusal is not None and refusal[1] == 409 and refusal[0].get("needs_ack") is True, refusal)
check("...which the strict-boolean ack lets through", srv._switch_ack_refusal(HER, True) is None)

CUR = "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"
n = len(CALLS)
res, st = route("/api/models/hf/import", "POST", body={"id": CUR, "download": True})
check("a curated id needs no confirm, goes through add_model, fetches nothing",
      st == 200 and res["curated"] is True and res["download"]["id"] == CUR and len(CALLS) == n, res)
cm = models()[CUR]
check("...and keeps its curated shape (seed label, uncensored, mlx_vlm)",
      cm["label"] == "DeepSeek-V4-Pro Qwen3.5-9B Heretic" and srv._roster_uncensored(cm)
      and cm.get("backend") == "mlx_vlm", cm)

# --------------------------------------------------------------------------
print("\n4. the one outbound path")
h = srv._HfxRedirect()
for bad in ("https://evil.example/x", "http://huggingface.co/x", "https://huggingface.co.evil.example/x",
            "https://cdn-lfs.huggingface.co.attacker/x"):
    try:
        h.redirect_request(None, None, 307, "", {}, bad)
        check("redirect to %s refused" % bad, False)
    except srv._HfxError:
        check("redirect to %s refused" % bad, True)
check("the fetcher only ever builds https://huggingface.co URLs",
      srv._HFX_BASE == "https://huggingface.co")
check("hf_offline set only with a subfolder; every row passes the config cleaner's allow-list limit",
      all(len((inspect(i)["row"] or {}).get("allow_patterns") or []) <= 16 for i in
          ("org/Good-9B-MLX-4bit", "org/Variants-MLX", "org/Root-Plus-Variants")))
cleaner = getattr(srv, "_cfg_clean_model_entry", None)
if cleaner:
    c = cleaner(inspect("org/Root-Plus-Variants")["row"])
    check("a derived row survives a config export unchanged (allow list, backend, flags)",
          c.get("allow_patterns") == inspect("org/Root-Plus-Variants")["row"]["allow_patterns"]
          and c.get("backend") == "mlx_vlm" and c.get("uncensored") is False, c)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
