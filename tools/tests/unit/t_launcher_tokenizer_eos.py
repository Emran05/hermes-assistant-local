#!/usr/bin/env python3
"""mlx-vlm-launch.py _patch_tokenizer_eos (2026-11-12).

The DeepSeek-V4-Pro Qwen3.5-9B builds name <|endoftext|> (248044) as the eos
in config.json/generation_config.json while their chat template ends a turn
with <|im_end|> (248046), so mlx_vlm's server streamed a literal "<|im_end|>"
at the end of every reply (seen live on 2026-11-12). The shim adds the
tokenizer's eos to the processor's additional_eos_token_ids after the model
loads. Offline: a stub mlx_vlm.server.generation stands in for the venv.
"""
import importlib.util
import os
import sys
import types

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
spec = importlib.util.spec_from_file_location("hermes_launch_eos", os.path.join(REPO, "mlx-vlm-launch.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

print("1. the pure picker")
f = L._eos_ids_to_add
check("tokenizer <|im_end|> added when the config names <|endoftext|>", f(248046, 248044, []) == [248046])
check("nothing when the config already names it", f(248046, [248044, 248046], []) == [])
check("nothing when it is already an additional id", f(248046, 248044, [248046]) == [])
check("a list eos keeps order, drops duplicates", f([5, 7, 5], None, []) == [5, 7])
check("junk is ignored (None, bool, str, negative)",
      f(None, 1, []) == [] and f(True, 1, []) == [] and f("x", 1, []) == [] and f(-1, 1, []) == [])


class Crit:
    def __init__(self):
        self.additional_eos_token_ids = [9]
        self.eos_token_ids = []

    def reset(self, eos):
        base = [eos] if isinstance(eos, int) else list(eos or [])
        self.eos_token_ids = base + [i for i in self.additional_eos_token_ids if i not in base]


def install(tok_eos, cfg_eos):
    tok = types.SimpleNamespace(eos_token_id=tok_eos, stopping_criteria=Crit())
    proc = types.SimpleNamespace(tokenizer=tok, additional_eos_token_ids=[9])
    cfg = types.SimpleNamespace(eos_token_id=cfg_eos)
    gen = types.ModuleType("mlx_vlm.server.generation")
    gen.load_model_resources = lambda path, adapter: ("model", proc, cfg)
    srv = types.ModuleType("mlx_vlm.server")
    srv.generation = gen
    sys.modules["mlx_vlm"] = types.ModuleType("mlx_vlm")
    sys.modules["mlx_vlm.server"] = srv
    sys.modules["mlx_vlm.server.generation"] = gen
    L._patch_tokenizer_eos()
    return gen, proc, tok


print("\n2. the patch on a stub loader")
gen, proc, tok = install(248046, 248044)
m, p, c = gen.load_model_resources("x", None)
check("the processor's additional ids gain the tokenizer eos (the batch path's stop set)",
      p.additional_eos_token_ids == [9, 248046], p.additional_eos_token_ids)
check("...and the stopping criteria stop on both after its reset",
      248046 in tok.stopping_criteria.eos_token_ids and 248044 in tok.stopping_criteria.eos_token_ids,
      tok.stopping_criteria.eos_token_ids)
L._patch_tokenizer_eos()
check("patching twice does not wrap twice", getattr(gen.load_model_resources, "_hermes_eos", False)
      and gen.load_model_resources.__closure__ is not None)
gen, proc, tok = install(248046, [248044, 248046])
gen.load_model_resources("x", None)
check("a model whose config already stops there is left exactly as it was",
      proc.additional_eos_token_ids == [9] and tok.stopping_criteria.eos_token_ids == [])
gen, proc, tok = install(248046, 248044)
proc.tokenizer = None          # a broken processor: the load still returns
out = gen.load_model_resources("x", None)
check("a failure inside the shim never breaks the load", out[0] == "model")
check("_main_vlm installs it", "_patch_tokenizer_eos()" in open(os.path.join(REPO, "mlx-vlm-launch.py")).read()
      .split("def _main_vlm", 1)[1].split("def ", 1)[0])
for k in ("mlx_vlm", "mlx_vlm.server", "mlx_vlm.server.generation"):
    sys.modules.pop(k, None)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
