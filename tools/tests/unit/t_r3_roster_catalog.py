#!/usr/bin/env python3
"""Round 3, cluster a-roster: the curated entries AGREE with what was verified.

t_r3_roster_hfmeta.json (next to this file) is the public Hugging Face
metadata of each new roster entry, read by hand on 2026-10-26 at a pinned sha
— the full file tree with sizes, config.json model_type + quantization,
tokenizer class, auto_map, licence, chat-template sha256 — plus the result of
the header-only STRICT-load dry run against mlx_vlm 0.6.14 (each shard's
safetensors header fetched by HTTP Range, weights replaced by lazy mx.zeros of
the header shapes, utils.load_model(strict=True)) with its positive control
(the stock Qwen3.8-27B-4bit) and negative controls (a wrong shape, a missing
tensor, a doubled quantisation-scales shape — each MUST fail).

This suite is offline. It proves, against that recorded tree:
  * the roster's allow_patterns + the never-code/never-pickle ignore list
    (huggingface_hub's own filter semantics: fnmatchcase, `*` crosses `/`)
    select EVERY file the loader needs — config, tokenizer, template, index
    and every indexed shard — and nothing else (no README/LICENSE/manifest,
    no other quantisation variant, no code, no pickle);
  * the selected bytes are the seed's size_gb (the menu/disk-gate number);
  * ram covers the weights; ctx is the config's; backend is mlx_vlm where
    the tokenizer needs transformers 5; the model_type has an mlx_vlm module;
  * no auto_map / *.py / pickle anywhere in the repo; licence recorded;
  * every chat template is byte-identical to its stock sibling's;
  * strict load PASSED for every entry and the controls behaved.
When the mlx-vlm venv exists, the selection is cross-checked against the REAL
huggingface_hub.utils.filter_repo_objects, and the model_type against the
installed mlx_vlm.models package.
"""
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
SERVER = REPO / "dashboard" / "server.py"
FIXTURE = Path(_HERE) / "t_r3_roster_hfmeta.json"
VENV_PY = Path(os.path.expanduser("~/.hermes/mlx-vlm-venv/bin/python"))
STOCK27 = "mlx-community/Qwen3.8-27B-4bit"
STOCK_MOE = "mlx-community/Qwen3.6-35B-A3B-4bit"
STOCK9 = "mlx-community/Qwen3.5-9B-4bit"
SIBLING = {"PocketAiHub/Qwen3.8-27B-Abliterated-MLX": STOCK27,
           "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit": STOCK27,
           "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit": STOCK_MOE,
           STOCK_MOE: STOCK_MOE,
           "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit": STOCK9,
           # compared with the bf16 source it was converted from (2026-11-12)
           "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit":
               "xtools-at/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-heretic"}
RISKY = (".py", ".pyc", ".pkl", ".pickle", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".npz")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def server_bits():
    """_SEED_MODELS (literal) and the real _dl_ignore_patterns, ast-extracted."""
    tree = ast.parse(SERVER.read_text())
    seeds, nodes = None, []
    want = {"_DL_NEVER_PICKLE", "_DL_NEVER_CODE", "_dl_ignore_patterns"}
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SEED_MODELS"
                                             for t in n.targets):
            seeds = ast.literal_eval(n.value)
        elif isinstance(n, ast.FunctionDef) and n.name in want:
            nodes.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want
                                               for t in n.targets):
            nodes.append(n)
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    return {m["id"]: m for m in seeds}, ns["_dl_ignore_patterns"]


def hf_filter(paths, allow, ignore):
    """huggingface_hub.utils.filter_repo_objects, mirrored: fnmatchcase, a
    pattern ending in "/" matches everything below it, `*` crosses "/"."""
    def norm(ps):
        return [p + "*" if p.endswith("/") else p for p in (ps or [])]
    allow, ignore = norm(allow), norm(ignore)
    out = []
    for p in paths:
        if allow and not any(fnmatch.fnmatchcase(p, a) for a in allow):
            continue
        if ignore and any(fnmatch.fnmatchcase(p, i) for i in ignore):
            continue
        out.append(p)
    return sorted(out)


def main():
    fx = json.loads(FIXTURE.read_text())
    seeds, ign_fn = server_bits()
    src = SERVER.read_text()
    repos = fx["repos"]
    print("fixture verified_at %s on mlx_vlm %s / mlx %s"
          % (fx["verified_at"], fx["mlx_vlm"], fx["mlx"]))

    print("\n1. controls")
    check("positive control (stock Qwen3.8-27B-4bit) passed the strict load",
          repos[STOCK27]["strict_load"]["strict_load"] == "PASS")
    negs = fx["controls"]["negative"]
    check("at least three negative controls, incl. a quantisation-scales mutation",
          len(negs) >= 3 and any(k.startswith("NEGQ:") for k in negs), sorted(negs))
    for k, v in negs.items():
        check("negative control %s FAILED strict load (%s)" % (k, v.get("mutation")),
              v.get("strict_load") == "FAIL" and "ValueError" in (v.get("error") or ""), v)

    selections = {}
    print("\n2. each curated entry against its verified tree")
    for mid, meta in sorted(repos.items()):
        if mid == STOCK27:
            continue
        seed = seeds.get(mid)
        check("%s: on the roster" % mid, seed is not None)
        if not seed:
            continue
        sub = meta["subfolder"] or ""
        pre = (sub + "/") if sub else ""
        files = meta["files"]
        check("%s: the server.py comment pins the verified sha (%s)" % (mid, meta["sha"][:7]),
              ("@" + meta["sha"][:7]) in src)
        check("%s: model_subfolder matches the verified layout" % mid,
              (seed.get("model_subfolder") or "") == sub, seed.get("model_subfolder"))
        risky = [f for f in files if f.lower().endswith(RISKY)]
        check("%s: no code, pickle, .bin or gguf anywhere in the repo" % mid, not risky, risky)
        check("%s: no auto_map in config/tokenizer_config" % mid, meta["auto_map"] is False)
        check("%s: licence recorded as published (%r)" % (mid, meta["license"]),
              seed.get("license") == meta["license"], (seed.get("license"), meta["license"]))
        q = meta["quantization"]
        check("%s: 4-bit affine g64 MLX quantisation" % mid,
              q["bits"] == 4 and q["group_size"] == 64 and q["mode"] == "affine", q)
        check("%s: ctx is the config's max_position_embeddings" % mid,
              seed.get("ctx") == meta["max_position_embeddings"], seed.get("ctx"))
        if meta["tokenizer_class"] == "TokenizersBackend":
            check("%s: TokenizersBackend tokenizer -> backend mlx_vlm (transformers 5)" % mid,
                  seed.get("backend") == "mlx_vlm")
        check("%s: model_type %s is one mlx_vlm 0.6.14 ships" % (mid, meta["model_type"]),
              meta["model_type"] in ("qwen3_5", "qwen3_5_moe"))
        check("%s: strict load PASSED, tokenizer + processor loaded, qwen3_coder tool parser"
              % mid, meta["strict_load"]["strict_load"] == "PASS"
              and meta["strict_load"]["tokenizer_class"] == meta["tokenizer_class"]
              and meta["strict_load"]["tool_parser"] == "qwen3_coder", meta["strict_load"])
        check("%s: every tensor in the headers became a model parameter" % mid,
              meta["strict_load"]["tensors"] == meta["strict_load"]["model_params"])
        sib = SIBLING[mid]
        want_tpl = (repos.get(sib) or {}).get("chat_template_sha256") \
            or fx["stock_templates"].get(sib)
        check("%s: chat template byte-identical to %s's" % (mid, sib),
              meta["chat_template_sha256"] and meta["chat_template_sha256"] == want_tpl)
        if meta["has_vision_config"] is False:
            check("%s: text-only build says so in its note" % mid,
                  "text-only" in seed.get("note", ""), seed.get("note"))
            check("%s: ...and the strict load dropped the vision tower" % mid,
                  meta["strict_load"]["vision_tower"] is False)

        allow = seed.get("allow_patterns")
        if not allow and sub:
            allow = [sub + "/*"]
        ignore = ign_fn(seed, seed.get("ignore_patterns"))
        sel = hf_filter(list(files), allow, ignore)
        selections[mid] = (list(files), allow, ignore, sel)
        need = [pre + n for n in ("config.json", "tokenizer_config.json",
                                  "model.safetensors.index.json")]
        need += [pre + s for s in meta["index_shards"]]
        need += [f for f in files if f.startswith(pre) and "/" not in f[len(pre):]
                 and f[len(pre):] in ("chat_template.jinja", "generation_config.json",
                                      "preprocessor_config.json", "processor_config.json",
                                      "video_preprocessor_config.json", "vocab.json")]
        tok = [pre + t for t in ("tokenizer.json", "tokenizer.model") if (pre + t) in files]
        check("%s: the repo has a tokenizer file the loader can use" % mid, bool(tok))
        need += tok
        missing = sorted(set(need) - set(sel))
        check("%s: the pull includes everything the loader needs (%d files)" % (mid, len(need)),
              not missing, missing)
        junk = [f for f in sel if f.rsplit("/", 1)[-1] in ("README.md", "LICENSE", ".gitattributes")]
        check("%s: ...and no README/LICENSE/.gitattributes" % mid, not junk, junk)
        extra = sum(files[f] or 0 for f in set(sel) - set(need)
                    if not f.endswith(".safetensors"))
        check("%s: anything else selected is small JSON metadata (%d bytes)" % (mid, extra),
              extra < 200_000 and all(f.endswith(".json") for f in set(sel) - set(need)),
              sorted(set(sel) - set(need)))
        if sub:
            stray = [f for f in sel if not f.startswith(pre)]
            check("%s: nothing outside %s/ (the other variants stay on the Hub)" % (mid, sub),
                  not stray, stray[:5])
            others = sum(sz for f, sz in files.items() if not f.startswith(pre))
            check("%s: ...which skips %.0f GB of other variants" % (mid, others / 1e9),
                  others > 50e9)
        check("%s: nothing risky selected" % mid,
              not [f for f in sel if f.lower().endswith(RISKY)])
        sel_bytes = sum(files[f] or 0 for f in sel)
        check("%s: size_gb %.2f == the selected download (%.2f GB)"
              % (mid, seed.get("size_gb", 0), sel_bytes / 1e9),
              abs(seed.get("size_gb", 0) - round(sel_bytes / 1e9, 2)) < 0.006,
              sel_bytes)
        weights = sum(files[pre + s] or 0 for s in meta["index_shards"])
        check("%s: ram %s GB covers the %.1f GiB of weights" % (mid, seed.get("ram"),
                                                               weights / 2 ** 30),
              seed.get("ram", 0) >= weights / 2 ** 30 + 1)

    print("\n3. against the REAL huggingface_hub / mlx_vlm (venv)")
    if not VENV_PY.exists():
        print("  SKIP venv cross-check: no mlx-vlm venv at %s" % VENV_PY)
    else:
        driver = (
            "import json,sys,os\n"
            "from huggingface_hub.utils import filter_repo_objects\n"
            "import mlx_vlm\n"
            "d=json.load(sys.stdin)\n"
            "out={k:sorted(filter_repo_objects(v[0],allow_patterns=v[1],ignore_patterns=v[2]))"
            " for k,v in d['sel'].items()}\n"
            "mods=os.path.join(os.path.dirname(mlx_vlm.__file__),'models')\n"
            "print(json.dumps({'sel':out,'types':{t:os.path.isdir(os.path.join(mods,t))"
            " for t in d['types']}}))\n")
        payload = json.dumps({"sel": {k: v[:3] for k, v in selections.items()},
                              "types": sorted({m["model_type"] for m in repos.values()})})
        r = subprocess.run([str(VENV_PY), "-I", "-c", driver], input=payload,
                           capture_output=True, text=True, timeout=120)
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            check("venv cross-check ran", False, (r.stdout or "")[-300:] + (r.stderr or "")[-600:])
            got = None
        if got:
            for mid, (_f, _a, _i, sel) in sorted(selections.items()):
                check("%s: huggingface_hub's own filter selects exactly the same files" % mid,
                      got["sel"].get(mid) == sel,
                      sorted(set(got["sel"].get(mid) or []) ^ set(sel))[:6])
            for t, ok in sorted(got["types"].items()):
                check("installed mlx_vlm has a models/%s module" % t, ok)

    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


main()
