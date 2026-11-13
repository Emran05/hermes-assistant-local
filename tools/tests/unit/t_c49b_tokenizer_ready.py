#!/usr/bin/env python3
"""Regression test for the C49 review follow-up (round 2, w9).

BEFORE: mlx-vlm-launch.py `--local-ready` (`_weights_ready`) checked only
config.json and the indexed shards. A snapshot whose tokenizer files were
missing (a download interrupted after the weights, a hand-pruned cache)
passed, so mlx-server.sh / mlx-server-bg.sh exported HF_HUB_OFFLINE=1 +
MLX_VLM_LOCAL_ONLY=1 and the server then failed at load instead of fetching
the missing files online.

AFTER: the served model (the FIRST name) must also carry tokenizer_config.json
plus tokenizer.json or a sentencepiece tokenizer.model. A drafter (any later
name) is weights only — mlx-vlm never loads a drafter's tokenizer.

Runs the REAL launcher's `--local-ready` CLI under a throwaway HOME with a
fake HF cache (stdlib only, nothing is imported from mlx, nothing loads).
On the old code the two "no tokenizer" checks return 0 and fail.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
LAUNCHER = REPO / "mlx-vlm-launch.py"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def snap(hub, repo, files):
    base = Path(hub) / ("models--" + repo.replace("/", "--"))
    s = base / "snapshots" / "abc"
    s.mkdir(parents=True, exist_ok=True)
    (base / "refs").mkdir(exist_ok=True)
    (base / "refs" / "main").write_text("abc")
    for fn in ("config.json", "model.safetensors") + tuple(files):
        (s / fn).write_text("{}")
    return s


def ready(home, *names):
    env = dict(os.environ, HOME=str(home))
    env.pop("HF_HOME", None)
    env.pop("HF_HUB_CACHE", None)
    return subprocess.run([sys.executable, str(LAUNCHER), "--local-ready"] + list(names),
                          capture_output=True, text=True, env=env, timeout=60).returncode


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c49b-") as d:
        hub = Path(d) / ".cache" / "huggingface" / "hub"
        snap(hub, "org/notok", ())
        snap(hub, "org/cfgonly", ("tokenizer_config.json",))
        snap(hub, "org/json", ("tokenizer_config.json", "tokenizer.json"))
        snap(hub, "org/spm", ("tokenizer_config.json", "tokenizer.model"))
        snap(hub, "org/drafter", ())
        local = snap(hub, "org/localdir", ())
        check("weights complete but NO tokenizer files -> 1 (stays online, can fetch them)",
              ready(d, "org/notok") == 1)
        check("tokenizer_config.json alone (no tokenizer.json / .model) -> 1",
              ready(d, "org/cfgonly") == 1)
        check("tokenizer_config.json + tokenizer.json -> 0 (offline start)",
              ready(d, "org/json") == 0)
        check("tokenizer_config.json + sentencepiece tokenizer.model -> 0",
              ready(d, "org/spm") == 0)
        check("a drafter (second name) needs no tokenizer -> 0",
              ready(d, "org/json", "org/drafter") == 0)
        check("...but a tokenizer-less SERVED model still fails with a good drafter",
              ready(d, "org/notok", "org/json") == 1)
        check("a local model dir as the served model needs its tokenizer too",
              ready(d, str(local)) == 1)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
