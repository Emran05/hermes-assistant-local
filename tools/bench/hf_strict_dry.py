"""Header-only strict-load dry run of a Hugging Face MLX repo against the
installed mlx_vlm — how a curated roster entry is verified before it ships
(t_r3_roster_hfmeta.json records the result; t_r3_roster_catalog.py checks it).

No weights are downloaded: the small JSON/template/tokenizer files are, plus
each shard's safetensors HEADER by HTTP Range; the weights become lazy
mx.zeros of the header shapes and mlx_vlm.utils.load_model(strict=True) runs.
Then the processor is loaded the way the server does (model module imported
first, so mlx_vlm's own processors are registered) and the tool parser is
inferred from the chat template.

Usage (network; never part of a test tier):
  SSL_CERT_FILE=$(~/.hermes/mlx-vlm-venv/bin/python -I -c 'import certifi;print(certifi.where())') \
  ~/.hermes/mlx-vlm-venv/bin/python -I tools/bench/hf_strict_dry.py <repo> <sha> <empty workdir> [mutate]
Fetches the small files + every shard's safetensors HEADER (HTTP Range),
replaces weights with lazy mx.zeros of the header shapes and runs
mlx_vlm.utils.load_model(strict=True). Prints one JSON line.
`mutate` = drop the first input_layernorm tensor (a negative control).
"""
import json
import os
import struct
import sys
import urllib.request
from pathlib import Path

repo, sha, work = sys.argv[1], sys.argv[2], Path(sys.argv[3])
mutate = len(sys.argv) > 4
work.mkdir(parents=True, exist_ok=True)
base = "https://huggingface.co/%s/resolve/%s/" % (repo, sha)
info = json.load(urllib.request.urlopen(
    "https://huggingface.co/api/models/%s/revision/%s?blobs=true" % (repo, sha), timeout=30))
files = {s["rfilename"]: s.get("size") for s in info["siblings"]}
small = [f for f in files if not f.endswith(".safetensors") and "/" not in f
         and f.endswith((".json", ".jinja", ".txt", ".model"))]
for f in small:
    (work / f).write_bytes(urllib.request.urlopen(base + f, timeout=60).read())

idx = json.loads((work / "model.safetensors.index.json").read_text()) \
    if (work / "model.safetensors.index.json").exists() else None
shards = sorted(set(idx["weight_map"].values())) if idx else \
    [f for f in files if f.endswith(".safetensors") and "/" not in f]
headers = {}
for s in shards:
    req = urllib.request.Request(base + s, headers={"Range": "bytes=0-7"})
    n = struct.unpack("<Q", urllib.request.urlopen(req, timeout=60).read())[0]
    req = urllib.request.Request(base + s, headers={"Range": "bytes=8-%d" % (7 + n)})
    headers[s] = json.loads(urllib.request.urlopen(req, timeout=60).read())
    (work / s).write_bytes(b"")          # load_model only checks .exists()

import mlx.core as mx                     # noqa: E402
import mlx_vlm.utils as U                 # noqa: E402
from mlx_vlm.tool_parsers import _infer_tool_parser  # noqa: E402

DT = {"BF16": mx.bfloat16, "F16": mx.float16, "F32": mx.float32, "U32": mx.uint32,
      "U8": mx.uint8, "I32": mx.int32, "I64": mx.int64, "BOOL": mx.bool_}
count = [0]
dropped = [None]


def fake(path):
    h = headers[os.path.basename(path)]
    out = {}
    for k, v in h.items():
        if k == "__metadata__":
            continue
        if mutate and dropped[0] is None and k.endswith("layers.0.input_layernorm.weight"):
            dropped[0] = k
            continue
        out[k] = mx.zeros(v["shape"], dtype=DT[v["dtype"]])
    count[0] += len(out)
    return out


U._load_safetensors = fake
res = {"repo": repo, "sha": sha, "shards": shards}
try:
    model = U.load_model(work, lazy=True, strict=True)
    from mlx.utils import tree_flatten
    res.update(strict_load="PASS", tensors=count[0],
               model_params=len(tree_flatten(model.parameters())),
               vision_tower=hasattr(model, "vision_tower"))
except Exception as e:  # noqa: BLE001
    res.update(strict_load="FAIL", error="%s: %s" % (type(e).__name__, str(e)[:300]),
               mutation=dropped[0])
try:
    tpl = (work / "chat_template.jinja").read_text()
    res["tool_parser"] = _infer_tool_parser(tpl)
    U.get_model_and_args(config=U.load_config(work))     # registers mlx_vlm's processors
    proc = U.load_processor(work, add_detokenizer=True)
    res["processor_class"] = type(proc).__name__
    res["tokenizer_class"] = type(getattr(proc, "tokenizer", proc)).__name__
except Exception as e:  # noqa: BLE001
    res["tok_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
print(json.dumps(res))
