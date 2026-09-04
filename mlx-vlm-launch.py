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
"""
import atexit
import os
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


def _patch_local_snapshot_resolution():
    """MLX_VLM_LOCAL_ONLY=1 (set by mlx-server.sh for roster entries with
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
    if flag not in ("1", "true", "yes"):
        return
    try:
        from pathlib import Path
        from mlx_vlm import utils as _u
    except Exception:
        return
    orig = _u.get_model_path

    def _cached_snapshot(repo_id):
        base = os.environ.get("HF_HUB_CACHE") or os.path.join(
            os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")
        d = os.path.join(base, "models--" + repo_id.replace("/", "--"))
        snaps = os.path.join(d, "snapshots")
        try:
            sha = open(os.path.join(d, "refs", "main")).read().strip()
            if sha and os.path.isdir(os.path.join(snaps, sha)):
                return os.path.join(snaps, sha)
        except OSError:
            pass
        try:                                   # no ref → newest snapshot dir
            cands = [os.path.join(snaps, x) for x in os.listdir(snaps)]
            cands = [c for c in cands if os.path.isdir(c)]
            return max(cands, key=os.path.getmtime) if cands else None
        except OSError:
            return None

    def get_model_path(path_or_hf_repo, *a, **kw):
        s = str(path_or_hf_repo)
        if os.path.exists(s):
            return Path(s)
        snap = _cached_snapshot(s)
        if snap and os.path.isfile(os.path.join(snap, "config.json")):
            return Path(snap)
        return orig(path_or_hf_repo, *a, **kw)

    _u.get_model_path = get_model_path
    print("[mlx-vlm-launch] MLX_VLM_LOCAL_ONLY=1 — repo ids resolve to the cached "
          "snapshot; no Hub calls", flush=True)


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


def _hard_exit():
    # mlx 0.32.x segfaults in the CompileCache destructor at interpreter
    # teardown (harmless, but launchd logs it as a crash and macOS shows a
    # "Python quit unexpectedly" dialog). atexit runs BEFORE module teardown on
    # every exit path (normal return, SystemExit, uvicorn's SIGTERM handling),
    # so ending the process here skips the crashing finalizer.
    try:
        sys.stdout.flush(); sys.stderr.flush()
    finally:
        os._exit(0)


if __name__ == "__main__":
    _patch_rng_restore()
    _patch_local_snapshot_resolution()
    _patch_default_reasoning_effort()
    atexit.register(_hard_exit)
    sys.argv[0] = "mlx_vlm.server"
    try:
        runpy.run_module("mlx_vlm.server", run_name="__main__", alter_sys=True)
    finally:
        _hard_exit()
