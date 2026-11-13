#!/usr/bin/env python3
"""Regression test for audit finding C49 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: HF_HUB_OFFLINE / MLX_VLM_LOCAL_ONLY were exported only for roster
entries with `hf_offline` (none of the shipped ones). Every other start — each
wake after an idle-suspend — passed a repo id to the loader, which called
snapshot_download() online for the model, the MTP drafter and the bg lane's
9B: a huggingface.co round trip per wake, and any commit upstream had pushed
since was fetched and loaded silently.

AFTER: mlx-vlm-launch.py `--local-ready <model> [<drafter>]` answers (stdlib,
no mlx import) whether config.json + every shard is on disk; mlx-server.sh
and mlx-server-bg.sh then export HF_HUB_OFFLINE=1 + MLX_VLM_LOCAL_ONLY=1, and
the launcher's resolution shims (mlx_vlm get_model_path, mlx_lm _download)
map the repo id to the cached snapshot. An incomplete snapshot keeps the old
online path (a download still in flight must be able to finish).

Runs the REAL launchers under a throwaway HOME with a fake HF cache and fake
interpreters that print the environment they were exec'd with (nothing is
loaded), and the REAL launcher module for the readiness logic and the mlx-lm
shim (against a stub mlx_lm.utils module).
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
LAUNCHER = REPO / "mlx-vlm-launch.py"
PRIMARY = "mlx-community/Qwen3.8-27B-4bit"
DRAFTER = "mlx-community/Qwen3.8-27B-MTP-bf16"
BG = "mlx-community/Qwen3.5-9B-4bit"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def make_snapshot(hub, repo, sharded=True, drop=None, sha="abc", tokenizer=True):
    base = Path(hub) / ("models--" + repo.replace("/", "--"))
    snap = base / "snapshots" / sha
    snap.mkdir(parents=True, exist_ok=True)
    (base / "refs").mkdir(exist_ok=True)
    (base / "refs" / "main").write_text(sha)
    (snap / "config.json").write_text("{}")
    if tokenizer:          # a served model also needs these (C49 review, R2)
        (snap / "tokenizer_config.json").write_text("{}")
        (snap / "tokenizer.json").write_text("{}")
    if sharded:
        shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
        (snap / "model.safetensors.index.json").write_text(json.dumps(
            {"weight_map": {"a": shards[0], "b": shards[1]}}))
        for s in shards:
            if s != drop:
                (snap / s).write_text("x")
    else:
        (snap / "model.safetensors").write_text("x")
    return snap


def run_ready(home, *names):
    env = dict(os.environ, HOME=str(home))
    env.pop("HF_HOME", None)
    env.pop("HF_HUB_CACHE", None)
    return subprocess.run([sys.executable, str(LAUNCHER), "--local-ready"] + list(names),
                          capture_output=True, text=True, env=env, timeout=60).returncode


def test_ready():
    with tempfile.TemporaryDirectory(prefix="hermes-c49-") as d:
        hub = Path(d) / ".cache" / "huggingface" / "hub"
        make_snapshot(hub, PRIMARY)
        make_snapshot(hub, DRAFTER, sharded=False)
        make_snapshot(hub, "org/partial", drop="model-00002-of-00002.safetensors")
        sub = make_snapshot(hub, "org/withsub") / "mtp"
        sub.mkdir()
        (sub / "config.json").write_text("{}")
        (sub / "model.safetensors").write_text("x")
        check("--local-ready: complete sharded model -> 0", run_ready(d, PRIMARY) == 0)
        check("--local-ready: model + single-file drafter -> 0", run_ready(d, PRIMARY, DRAFTER) == 0)
        check("--local-ready: a missing shard -> 1 (download in flight stays online)",
              run_ready(d, "org/partial") == 1)
        check("--local-ready: any one name incomplete -> 1", run_ready(d, PRIMARY, "org/partial") == 1)
        check("--local-ready: never-downloaded repo -> 1", run_ready(d, "org/absent") == 1)
        check("--local-ready: an in-repo drafter's local dir -> 0",
              run_ready(d, PRIMARY, str(sub)) == 0)
        check("--local-ready: no names -> 1", run_ready(d) == 1)


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_launcher(script, home, files=None, venv=True):
    home = Path(home)
    dash = home / ".hermes" / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text)
    vb = home / ".hermes" / "mlx-vlm-venv" / "bin"
    if venv:
        vb.mkdir(parents=True, exist_ok=True)
        _exe(vb / "python", '#!/bin/bash\necho "EXEC vlm offline=${HF_HUB_OFFLINE:-}'
                            ' local=${MLX_VLM_LOCAL_ONLY:-} $*"\n')
    elif (vb / "python").exists():
        (vb / "python").unlink()
    fb = home / "fakebin"
    fb.mkdir(exist_ok=True)
    _exe(fb / "python3", '#!/bin/bash\n'
         'if [ "$2" = "--mlx-lm" ]; then echo "EXEC lm offline=${HF_HUB_OFFLINE:-}'
         ' local=${MLX_VLM_LOCAL_ONLY:-} $*"; exit 0; fi\n'
         'if [ "$1" = "-m" ]; then echo "EXEC m offline=${HF_HUB_OFFLINE:-} $*"; exit 0; fi\n'
         'exec "%s" "$@"\n' % sys.executable)
    env = {"HOME": str(home), "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % fb,
           "HERMES_MODEL_ALWAYS_ON": "1"}
    for k in ("PYTHONPATH", "HERMES_TESTS_OFFLINE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    r = subprocess.run(["/bin/bash", str(REPO / script)], capture_output=True, text=True,
                       env=env, timeout=120)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("EXEC ")]
    return (lines[-1] if lines else ""), (r.stdout or "") + (r.stderr or "")


def offline(line):
    return "offline=1" in line and "local=1" in line


def test_shell():
    sb = ".hermes/dashboard/server-backend"
    backend = json.dumps({"backend": "mlx_vlm", "draft_model": DRAFTER, "draft_kind": "mtp",
                          "draft_block_size": 3, "enable_thinking": False})
    with tempfile.TemporaryDirectory(prefix="hermes-c49-sh-") as d:
        hub = Path(d) / ".cache" / "huggingface" / "hub"
        line, log = run_launcher("mlx-server.sh", d, {sb: backend})
        check("primary, nothing on disk: starts ONLINE (old path, it must fetch)",
              line.startswith("EXEC vlm") and not offline(line), line or log[-500:])
        make_snapshot(hub, PRIMARY)
        line, log = run_launcher("mlx-server.sh", d, {sb: backend})
        check("primary complete but the drafter missing: still online",
              line.startswith("EXEC vlm") and not offline(line), line or log[-500:])
        make_snapshot(hub, DRAFTER, sharded=False)
        line, log = run_launcher("mlx-server.sh", d, {sb: backend})
        check("primary + drafter complete: starts OFFLINE (HF_HUB_OFFLINE + MLX_VLM_LOCAL_ONLY)",
              offline(line), line or log[-500:])
        check("...and says why in the log", "weights complete on disk" in log, log[-500:])
        line, log = run_launcher("mlx-server.sh", d, {sb: json.dumps({"backend": "mlx_lm"})},
                                 venv=False)
        check("mlx-lm fallback, complete model: starts OFFLINE too",
              line.startswith("EXEC lm") and offline(line), line or log[-500:])
        line, log = run_launcher("mlx-server-bg.sh", d)
        check("bg lane, 9B absent: online", line.startswith("EXEC vlm") and not offline(line),
              line or log[-500:])
        make_snapshot(hub, BG)
        line, log = run_launcher("mlx-server-bg.sh", d)
        check("bg lane, 9B complete: OFFLINE", offline(line), line or log[-500:])
        line, log = run_launcher("mlx-server-bg.sh", d, venv=False)
        check("bg lane mlx-lm fallback, 9B complete: OFFLINE",
              line.startswith("EXEC lm") and offline(line), line or log[-500:])


def test_mlx_lm_shim():
    """The launcher's mlx-lm resolution shim against a stub mlx_lm.utils."""
    spec = importlib.util.spec_from_file_location("hermes_launch_c49", str(LAUNCHER))
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    if not hasattr(L, "_patch_mlx_lm_local"):
        check("launcher has the mlx-lm local shim", False)
        return
    calls = []
    pkg = types.ModuleType("mlx_lm")
    utils = types.ModuleType("mlx_lm.utils")
    utils._download = lambda p, *a, **k: calls.append(str(p)) or Path("/online/" + str(p))
    pkg.utils = utils
    saved = {k: sys.modules.get(k) for k in ("mlx_lm", "mlx_lm.utils")}
    sys.modules["mlx_lm"], sys.modules["mlx_lm.utils"] = pkg, utils
    old_env = {k: os.environ.get(k) for k in ("HF_HUB_CACHE", "MLX_VLM_LOCAL_ONLY")}
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-c49-lm-") as d:
            snap = make_snapshot(d, PRIMARY)
            os.environ["HF_HUB_CACHE"] = d
            os.environ.pop("MLX_VLM_LOCAL_ONLY", None)
            L._patch_mlx_lm_local()
            check("shim inert without MLX_VLM_LOCAL_ONLY",
                  str(utils._download(PRIMARY)) == "/online/" + PRIMARY)
            os.environ["MLX_VLM_LOCAL_ONLY"] = "1"
            L._patch_mlx_lm_local()
            del calls[:]
            got = utils._download(PRIMARY)
            check("MLX_VLM_LOCAL_ONLY: a cached repo id resolves to its snapshot, no download",
                  str(got) == str(snap) and calls == [], (got, calls))
            got = utils._download("org/absent")
            check("...an uncached one still goes to the original loader",
                  calls == ["org/absent"], calls)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    test_ready()
    test_shell()
    test_mlx_lm_shim()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
