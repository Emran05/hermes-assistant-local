#!/usr/bin/env python3
"""Round 3, cluster a-roster: the model-server side of the new roster fields.

`model_subfolder` (weights in a repo SUBFOLDER, e.g. PocketAiHub's 4bit/) and
`sampling` (per-model request defaults) travel roster -> server-backend (or,
for the background lane, its models.json row) -> mlx-server.sh /
mlx-server-bg.sh -> MLX_VLM_MODEL_SUBFOLDER / MLX_VLM_SAMPLING_DEFAULTS ->
mlx-vlm-launch.py, which resolves the launch id to <snapshot>/<subfolder> for
the loader, --local-ready and the C130 pin, and applies the sampling defaults
only to requests that did not set the field.

Why server-side sampling (documented in the launcher): Hermes 0.18 can send
per-model sampling through a custom provider's `extra_body` (matched by
base_url + model), but only on its own turns — not on the dashboard's direct
model calls, the tool drill among them — and only by rewriting config.yaml on
every switch. A Hermes extra_body value still wins: it sets the field.

Four layers, all against the REAL code:
  1. the launcher's pure helpers, imported by path (stdlib python), plus drift
     guards against server.py's and aux_config's copies of the same rules;
  2. the REAL shell launchers end to end under a throwaway HOME with fake
     interpreters that print the exec line and the exported env (nothing is
     loaded; --local-ready runs the real stdlib launcher);
  3. the real mlx_vlm app (venv python): get_model_path resolution, the C130
     pin with a subfolder (FastAPI TestClient, loader STUBBED), and the real
     request-normalization path with the sampling defaults;
  4. the real mlx_lm APIHandler (framework python): the sampling defaults.
Layers 3/4 print a SKIP line (counted by run.sh) on a machine without those
interpreters.
"""
import ast
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
LAUNCHER = REPO / "mlx-vlm-launch.py"
VENV_PY = Path(os.path.expanduser("~/.hermes/mlx-vlm-venv/bin/python"))
FRAMEWORK_PY = Path("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3")
POCKET = "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def load_launcher():
    spec = importlib.util.spec_from_file_location("hermes_launch_r3", str(LAUNCHER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_snapshot(hub, mid, layout, sha="s1"):
    base = Path(hub) / ("models--" + mid.replace("/", "--"))
    snap = base / "snapshots" / sha
    (base / "refs").mkdir(parents=True, exist_ok=True)
    (base / "refs" / "main").write_text(sha)
    for rel, text in layout.items():
        p = snap / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    snap.mkdir(parents=True, exist_ok=True)
    return str(snap)


def full_model(prefix):
    pre = (prefix + "/") if prefix else ""
    return {pre + "config.json": "{}", pre + "tokenizer_config.json": "{}",
            pre + "tokenizer.json": "{}",
            pre + "model.safetensors.index.json": json.dumps(
                {"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors"}}),
            pre + "model-1.safetensors": "x", pre + "model-2.safetensors": "x"}


# --------------------------------------------------------------------------
# 1. pure helpers + drift guards
# --------------------------------------------------------------------------
def server_rules():
    """server.py's _roster_subfolder/_roster_sampling and aux_config's entry
    cleaner, ast-extracted (no server import)."""
    ns = {"re": re}
    src = (REPO / "dashboard" / "server.py").read_text()
    want = {"_ROSTER_SUBFOLDER_RE", "_ROSTER_SAMPLING_BOUNDS", "_roster_subfolder",
            "_roster_sampling"}
    nodes = []
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name in want:
            nodes.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want
                                               for t in n.targets):
            nodes.append(n)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "server.py", "exec"), ns)
    cfg = {"re": re}
    src = (REPO / "dashboard" / "aux_config.py").read_text()
    want_c = {"CFG_MODEL_ID_RE", "CFG_SUBFOLDER_RE", "CFG_GLOB_RE", "CFG_TA_KEY_RE",
              "_cfg_model_id", "_cfg_int", "_cfg_clean_model_entry"}
    nodes = [n for n in ast.parse(src).body
             if (isinstance(n, ast.FunctionDef) and n.name in want_c)
             or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want_c
                                                   for t in n.targets))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "aux_config.py", "exec"), cfg)
    return ns, cfg


SUB_CASES = ["4bit", "4bit/", "/4bit", "a/b", "a/b/c/d", "a/b/c/d/e", "../x", "a/../b",
             ".", "..", "a\\b", "a b", "", "  ", None, 3, "x" * 64, "x" * 65, "a//b",
             "6bit", "mtp", "a.b_c-d"]
SMP_CASES = [{"repetition_penalty": 1.15}, {"repetition_penalty": 2.5}, {"top_k": 20.0},
             {"top_k": 20.5}, {"top_k": True}, {"temperature": float("nan")},
             {"temperature": 0, "top_p": 1, "min_p": 0.05, "presence_penalty": -2,
              "frequency_penalty": 2, "repetition_context_size": 4096},
             {"repetition_context_size": 0}, {"seed": 1}, {"top_p": "0.9"}, None, [], {}]


def test_pure(L):
    print("\n1. launcher helpers (stdlib) + drift guards")
    srvns, cfgns = server_rules()
    for v in SUB_CASES:
        check("subfolder rule agrees with server.py for %r" % (v,),
              L._clean_subfolder(v) == srvns["_roster_subfolder"](v),
              (L._clean_subfolder(v), srvns["_roster_subfolder"](v)))
    check("bounds table identical to server.py's",
          L._SAMPLING_BOUNDS == srvns["_ROSTER_SAMPLING_BOUNDS"])
    for v in SMP_CASES:
        a, b = L._clean_sampling(v), srvns["_roster_sampling"](v)
        check("sampling rule agrees with server.py for %r" % (v,), a == b, (a, b))
        # aux_config's inline fallback (no _roster_sampling in its globals)
        e = cfgns["_cfg_clean_model_entry"]({"id": "org/x", "sampling": v})
        want = a if (a or v == {}) else None
        check("sampling rule agrees with aux_config's fallback for %r" % (v,),
              e.get("sampling") == want, (e.get("sampling"), want))
    for v in SUB_CASES:
        if not isinstance(v, str):
            continue
        e = cfgns["_cfg_clean_model_entry"]({"id": "org/x", "model_subfolder": v})
        ok_srv = srvns["_roster_subfolder"](v)
        # aux_config keeps a VALID value verbatim (it never rewrites); server
        # .py strips slashes — so compare validity, not spelling
        check("aux_config accepts %r iff it is a clean subfolder as written" % v,
              ("model_subfolder" in e) == (bool(ok_srv) and ok_srv == v), e)

    env = {}
    check("_model_subfolder: unset -> ('', None)", L._model_subfolder(env) == ("", None))
    check("_model_subfolder: '4bit' -> ('4bit', None)",
          L._model_subfolder({L._SUBFOLDER_ENV: "4bit"}) == ("4bit", None))
    sub, err = L._model_subfolder({L._SUBFOLDER_ENV: "../../etc"})
    check("_model_subfolder: '../../etc' -> refused with a reason", sub == "" and err, (sub, err))
    check("_model_subfolder: whitespace only -> unset",
          L._model_subfolder({L._SUBFOLDER_ENV: "  "}) == ("", None))
    check("_sampling_defaults: unset -> {}", L._sampling_defaults({}) == {})
    check("_sampling_defaults: valid JSON kept",
          L._sampling_defaults({L._SAMPLING_ENV: '{"repetition_penalty": 1.15, "top_k": 20}'})
          == {"repetition_penalty": 1.15, "top_k": 20})
    check("_sampling_defaults: not JSON -> {} (never stops the server)",
          L._sampling_defaults({L._SAMPLING_ENV: "rp=1.15"}) == {})
    check("_sampling_defaults: a list -> {}", L._sampling_defaults({L._SAMPLING_ENV: "[1]"}) == {})
    check("_sampling_defaults: invalid entries dropped",
          L._sampling_defaults({L._SAMPLING_ENV: '{"repetition_penalty": 9, "top_p": 0.8}'})
          == {"top_p": 0.8})

    with tempfile.TemporaryDirectory(prefix="hermes-r3-l-") as d:
        hub = os.path.join(d, "hub")
        os.environ["HF_HUB_CACHE"] = hub
        try:
            snap = fake_snapshot(hub, POCKET, full_model("4bit"))
            check("_launch_dir: <snapshot>/4bit", L._launch_dir(POCKET, "4bit")
                  == os.path.join(snap, "4bit"))
            check("_launch_dir: absent variant -> None", L._launch_dir(POCKET, "6bit") is None)
            check("_launch_dir: invalid subfolder -> None", L._launch_dir(POCKET, "../x") is None)
            check("_launch_dir: unknown repo -> None", L._launch_dir("org/none", "4bit") is None)
            check("_local_ready with the subfolder: complete 4bit/ is ready",
                  L._local_ready(POCKET, True, "4bit") is True)
            check("_local_ready WITHOUT the subfolder: the empty root is not",
                  L._local_ready(POCKET, True) is False)
            os.remove(os.path.join(snap, "4bit", "model-2.safetensors"))
            check("_local_ready: 4bit/ missing a shard is not ready",
                  L._local_ready(POCKET, True, "4bit") is False)
            fake_snapshot(hub, POCKET, full_model("4bit"))
            al = L._model_aliases(POCKET, "4bit")
            check("pin aliases: the id and realpath(<snapshot>/4bit)",
                  POCKET in al and os.path.realpath(os.path.join(snap, "4bit")) in al, al)
            check("pin aliases: NOT the snapshot root of the multi-variant repo",
                  os.path.realpath(snap) not in al, al)
            al0 = L._model_aliases(POCKET)
            check("pin aliases without a subfolder: unchanged (root realpath)",
                  os.path.realpath(snap) in al0, al0)

            def ready(args, sub=None):
                env = dict(os.environ)
                env.pop(L._SUBFOLDER_ENV, None)
                if sub is not None:
                    env[L._SUBFOLDER_ENV] = sub
                return subprocess.run([sys.executable, str(LAUNCHER), "--local-ready"] + args,
                                      env=env, capture_output=True, timeout=60).returncode
            check("--local-ready + MLX_VLM_MODEL_SUBFOLDER=4bit -> 0", ready([POCKET], "4bit") == 0)
            check("--local-ready without it -> 1 (root has no weights)", ready([POCKET]) == 1)
            check("--local-ready with an invalid subfolder -> 1, never the root",
                  ready([POCKET], "../4bit") == 1)
            dsnap = fake_snapshot(hub, "org/drafter", full_model(""))
            check("--local-ready: the subfolder applies to the served model only "
                  "(a root drafter is still ready)",
                  ready([POCKET, "org/drafter"], "4bit") == 0)

            # roster fallback: a launch WITHOUT the env reads models.json
            mf = os.path.join(d, "models.json")
            os.environ["HERMES_MODELS_FILE"] = mf
            with open(mf, "w") as f:
                json.dump({"models": [{"id": POCKET, "model_subfolder": "4bit"},
                                      {"id": "org/offline", "hf_offline": True},
                                      {"id": "org/bad", "model_subfolder": "../x"}]}, f)
            check("_launch_subfolder: the env wins",
                  L._launch_subfolder(POCKET, {L._SUBFOLDER_ENV: "6bit"}) == ("6bit", None, "env"))
            check("_launch_subfolder: no env -> the model's models.json row",
                  L._launch_subfolder(POCKET, {}) == ("4bit", None, "roster"))
            sub, err, src = L._launch_subfolder("org/bad", {})
            check("_launch_subfolder: an invalid roster value is an error, not the root",
                  sub == "" and err and src == "roster", (sub, err, src))
            check("_launch_subfolder: a model with no row -> nothing",
                  L._launch_subfolder("org/none", {}) == ("", None, None))
            check("--local-ready with NO env still judges <snapshot>/4bit from the roster "
                  "(any launcher, not only mlx-server.sh)", ready([POCKET]) == 0)
            saved = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "MLX_VLM_LOCAL_ONLY")}
            try:
                for k in saved:
                    os.environ.pop(k, None)
                why = L._force_offline_for("org/plain", "")
                check("_force_offline_for: a plain model is left alone",
                      why is None and "HF_HUB_OFFLINE" not in os.environ)
                why = L._force_offline_for("org/offline", "")
                check("_force_offline_for: a roster hf_offline row starts offline "
                      "whoever launched it (tools/bench included)",
                      why == "roster hf_offline" and os.environ.get("HF_HUB_OFFLINE") == "1"
                      and os.environ.get("MLX_VLM_LOCAL_ONLY") == "1")
                for k in saved:
                    os.environ.pop(k, None)
                why = L._force_offline_for(POCKET, "4bit")
                check("_force_offline_for: a subfolder model always starts offline",
                      why == "model_subfolder" and os.environ.get("HF_HUB_OFFLINE") == "1")
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            # a launch with an invalid roster subfolder refuses to start before
            # importing anything (the stdlib python has no mlx_vlm at all)
            r = subprocess.run([sys.executable, str(LAUNCHER), "--model", "org/bad",
                                "--port", "9"], env=dict(os.environ), capture_output=True,
                               text=True, timeout=60)
            check("launcher refuses to start on an invalid roster model_subfolder (exit 78)",
                  r.returncode == 78 and "NOT STARTING" in r.stderr, (r.returncode, r.stderr[-300:]))
            r = subprocess.run([sys.executable, str(LAUNCHER), "--mlx-lm", "--model", "org/bad"],
                               env=dict(os.environ), capture_output=True, text=True, timeout=60)
            check("...the mlx-lm fallback too", r.returncode == 78 and "NOT STARTING" in r.stderr,
                  (r.returncode, r.stderr[-300:]))
        finally:
            os.environ.pop("HF_HUB_CACHE", None)
            os.environ["HERMES_MODELS_FILE"] = os.path.join(tempfile.gettempdir(),
                                                            "hermes-r3-no-such-models.json")


# --------------------------------------------------------------------------
# 2. shell launchers end to end
# --------------------------------------------------------------------------
ENV_LINE = ('echo "ENV sub=${MLX_VLM_MODEL_SUBFOLDER-<unset>} '
            'smp=${MLX_VLM_SAMPLING_DEFAULTS-<unset>} off=${HF_HUB_OFFLINE-<unset>} '
            'lo=${MLX_VLM_LOCAL_ONLY-<unset>}"')


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_launcher(script, files, venv=True, env_extra=None):
    with tempfile.TemporaryDirectory(prefix="hermes-r3-sh-") as d:
        home = Path(d)
        (home / ".hermes" / "dashboard").mkdir(parents=True)
        for rel, text in files.items():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(text)
        if venv:
            vb = home / ".hermes" / "mlx-vlm-venv" / "bin"
            vb.mkdir(parents=True)
            _exe(vb / "python", '#!/bin/bash\n%s\necho "EXEC vlm $*"\n' % ENV_LINE)
        fb = home / "fakebin"
        fb.mkdir()
        _exe(fb / "python3", '#!/bin/bash\nif [ "$2" = "--mlx-lm" ]; then %s; '
                             'echo "EXEC lm $*"; exit 0; fi\n'
                             'exec "%s" "$@"\n' % (ENV_LINE, sys.executable))
        env = {"HOME": str(home), "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % fb,
               "HERMES_MODEL_ALWAYS_ON": "1"}
        for k in ("PYTHONPATH", "HERMES_TESTS_OFFLINE"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        env.update(env_extra or {})
        r = subprocess.run(["/bin/bash", str(REPO / script)], capture_output=True,
                           text=True, env=env, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    ex = [ln for ln in lines if ln.startswith("EXEC ")]
    en = [ln for ln in lines if ln.startswith("ENV ")]
    return (ex[-1] if ex else ""), (en[-1] if en else ""), out


def test_shell():
    print("\n2. mlx-server.sh / mlx-server-bg.sh end to end")
    sb = ".hermes/dashboard/server-backend"
    am = ".hermes/dashboard/active-model"
    full = {"backend": "mlx_vlm", "model": POCKET, "model_subfolder": "4bit",
            "hf_offline": True, "sampling": {"repetition_penalty": 1.15},
            "enable_thinking": False}
    ex, en, out = run_launcher("mlx-server.sh", {sb: json.dumps(full), am: POCKET})
    check("primary: the launch id is the repo id (the pin's spelling)",
          ex.startswith("EXEC vlm") and ("--model %s " % POCKET) in ex + " ", ex or out[-400:])
    check("primary: MLX_VLM_MODEL_SUBFOLDER=4bit exported",
          "sub=4bit " in en, en or out[-400:])
    check("primary: the sampling defaults exported as JSON",
          'smp={"repetition_penalty": 1.15} ' in en, en)
    check("primary: offline + local-only", "off=1 " in en and en.endswith("lo=1"), en)

    nooff = dict(full)
    nooff.pop("hf_offline")
    ex, en, out = run_launcher("mlx-server.sh", {sb: json.dumps(nooff), am: POCKET})
    check("primary: a subfolder forces offline even without hf_offline (never a "
          "134 GB snapshot_download)", "off=1 " in en and "roster model_subfolder" in out, en)

    ex, en, out = run_launcher("mlx-server.sh", {sb: json.dumps(full),
                                                 am: "mlx-community/Qwen3.8-27B-4bit"})
    check("primary: a server-backend written for ANOTHER model -> its subfolder and "
          "sampling are ignored", "sub=<unset>" in en and "smp=<unset>" in en
          and "ignoring its model_subfolder/sampling" in out, en)

    plain = {"backend": "mlx_vlm", "model": "mlx-community/Qwen3.8-27B-4bit"}
    ex, en, out = run_launcher("mlx-server.sh",
                               {sb: json.dumps(plain), am: "mlx-community/Qwen3.8-27B-4bit"},
                               env_extra={"MLX_VLM_MODEL_SUBFOLDER": "evil",
                                          "MLX_VLM_SAMPLING_DEFAULTS": '{"top_k":1}'})
    check("primary: an INHERITED subfolder/sampling env is dropped (the roster is "
          "the only source)", "sub=<unset>" in en and "smp=<unset>" in en, en)

    legacy = {"backend": "mlx_vlm"}             # a pre-round-3 file: no "model" key
    ex, en, out = run_launcher("mlx-server.sh", {sb: json.dumps(legacy), am: POCKET})
    check("primary: a pre-round-3 server-backend still starts (no new env)",
          ex.startswith("EXEC vlm") and "sub=<unset>" in en, ex or out[-300:])

    ex, en, out = run_launcher("mlx-server.sh", {sb: json.dumps(full), am: POCKET}, venv=False)
    check("primary mlx-lm fallback: the same subfolder + sampling env",
          ex.startswith("EXEC lm") and "sub=4bit " in en
          and 'smp={"repetition_penalty": 1.15} ' in en and "off=1 " in en, (ex, en))

    bg = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
    roster = {"models": [{"id": bg, "label": "bg", "model_subfolder": "4bit",
                          "sampling": {"repetition_penalty": 1.1}}]}
    files = {".hermes/dashboard/models.json": json.dumps(roster),
             ".hermes/dashboard/bg-model": bg}
    ex, en, out = run_launcher("mlx-server-bg.sh", files)
    check("bg lane: its roster row's subfolder + sampling exported, offline",
          ex.startswith("EXEC vlm") and "sub=4bit " in en
          and 'smp={"repetition_penalty": 1.1} ' in en and "off=1 " in en, (ex, en, out[-300:]))
    ex, en, out = run_launcher("mlx-server-bg.sh", files, venv=False)
    check("bg lane mlx-lm fallback: the same env",
          ex.startswith("EXEC lm") and "sub=4bit " in en, (ex, en))
    ex, en, out = run_launcher("mlx-server-bg.sh", {".hermes/dashboard/bg-model": bg},
                               env_extra={"MLX_VLM_MODEL_SUBFOLDER": "evil"})
    check("bg lane: no models.json -> old behaviour, no subfolder (inherited env dropped)",
          ex.startswith("EXEC vlm") and "sub=<unset>" in en and "--trust-remote-code" not in ex,
          (ex, en))
    roster = {"models": [{"id": bg, "label": "bg", "hf_offline": True}]}
    ex, en, out = run_launcher("mlx-server-bg.sh",
                               {".hermes/dashboard/models.json": json.dumps(roster),
                                ".hermes/dashboard/bg-model": bg})
    check("bg lane: roster hf_offline is honoured (it was ignored before)",
          "off=1 " in en, en)


# --------------------------------------------------------------------------
# 3. the real mlx_vlm (venv)
# --------------------------------------------------------------------------
VLM_DRIVER = r'''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("hermes_launch_drv", sys.argv[1])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
pinned = sys.argv[2]
hub = os.environ["HF_HUB_CACHE"]
repo = os.path.join(hub, "models--" + pinned.replace("/", "--"))
snap = os.path.join(repo, "snapshots", "s1")
os.makedirs(os.path.join(snap, "4bit")); os.makedirs(os.path.join(repo, "refs"))
open(os.path.join(snap, "4bit", "config.json"), "w").write("{}")
open(os.path.join(repo, "refs", "main"), "w").write("s1")
out = {}
from mlx_vlm import utils as U
hub_calls = []
U.snapshot_download = lambda **kw: hub_calls.append(kw.get("repo_id")) or "/nonexistent"
os.environ.pop("MLX_VLM_LOCAL_ONLY", None)
gmp = L._patch_local_snapshot_resolution(pinned, "4bit")
out["resolved"] = str(U.get_model_path(pinned))
out["resolved_ok"] = out["resolved"] == os.path.join(snap, "4bit")
U.get_model_path("other-org/other")            # not the launch id -> upstream path
out["other_hub_calls"] = list(hub_calls)
del hub_calls[:]
os.remove(os.path.join(snap, "4bit", "config.json"))
try:
    U.get_model_path(pinned)
    out["missing"] = "returned"
except FileNotFoundError as e:
    out["missing"] = "FileNotFoundError"
out["missing_hub_calls"] = list(hub_calls)
open(os.path.join(snap, "4bit", "config.json"), "w").write("{}")
# the C130 pin with the subfolder
import mlx_vlm.server as pkg
appmod = sys.modules["mlx_vlm.server.app"]
from fastapi import HTTPException
seen = []
def stub(model_path, adapter_path=appmod._INHERIT_ADAPTER, *, model_kind="auto"):
    seen.append(str(model_path))
    raise HTTPException(status_code=418, detail="stub loader reached")
appmod.get_cached_model = stub
pkg.get_cached_model = stub
L._install_vlm_guards(pinned, "", "4bit")
import warnings; warnings.simplefilter("ignore")
from fastapi.testclient import TestClient
c = TestClient(appmod.app, base_url="http://127.0.0.1:8080")
msg = [{"role": "user", "content": "hi"}]
def chat(model):
    del seen[:]
    r = c.post("/v1/chat/completions", json={"model": model, "messages": msg})
    return [r.status_code, list(seen)]
out["pin_id"] = chat(pinned)
out["pin_subdir"] = chat(os.path.join(snap, "4bit"))
out["pin_root"] = chat(snap)
out["pin_other"] = chat("attacker-org/repo")
# sampling defaults through the real request normalization
from mlx_vlm.server.schemas import ChatRequest, AnthropicRequest
L._patch_sampling_defaults({"repetition_penalty": 1.15, "top_k": 40})
build = appmod._protocol_deps.build_gen_args
def ga(**kw):
    a = build(ChatRequest(model=pinned, messages=msg, **kw))
    return [a.repetition_penalty, a.top_k]
out["unset"] = ga()
out["explicit"] = ga(repetition_penalty=1.0, top_k=5)
out["explicit_null"] = ga(repetition_penalty=None)
a = build(AnthropicRequest(model=pinned, max_tokens=8, messages=msg))
out["anthropic"] = [a.repetition_penalty, a.top_k]
a = build(AnthropicRequest(model=pinned, max_tokens=8, messages=msg, repetition_penalty=1.3))
out["anthropic_explicit"] = a.repetition_penalty
print("RESULT " + json.dumps(out))
'''


def run_driver(py, driver, args, env_extra=None):
    with tempfile.TemporaryDirectory(prefix="hermes-r3-drv-") as d:
        f = Path(d) / "drv.py"
        f.write_text(driver)
        env = dict(os.environ)
        env["HF_HOME"] = str(Path(d) / "hf")
        env["HF_HUB_CACHE"] = str(Path(d) / "hf" / "hub")
        env["HF_HUB_OFFLINE"] = "1"
        env["HOME"] = d
        env.update(env_extra or {})
        r = subprocess.run([str(py), str(f)] + [str(a) for a in args], cwd=d,
                           capture_output=True, text=True, timeout=300, env=env)
    line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("RESULT ")]
    if not line:
        return None, (r.stdout or "")[-800:] + (r.stderr or "")[-1500:]
    return json.loads(line[-1][7:]), ""


def test_vlm():
    print("\n3. the real mlx_vlm app (venv)")
    if not VENV_PY.exists():
        print("  SKIP venv layer: no mlx-vlm venv at %s — the subfolder resolution, the "
              "pin and the sampling defaults on the real mlx_vlm did not run" % VENV_PY)
        return
    out, err = run_driver(VENV_PY, VLM_DRIVER, [LAUNCHER, POCKET])
    if out is None:
        check("venv driver ran", False, err)
        return
    check("get_model_path(launch id) -> <snapshot>/4bit, with no MLX_VLM_LOCAL_ONLY",
          out["resolved_ok"], out["resolved"])
    check("another id still takes the upstream path (it is not this model)",
          out["other_hub_calls"] == ["other-org/other"], out["other_hub_calls"])
    check("a missing subfolder is a load error and NEVER a Hub download",
          out["missing"] == "FileNotFoundError" and out["missing_hub_calls"] == [], out)
    check("pin: the repo id reaches the loader as the launch id",
          out["pin_id"] == [418, [POCKET]], out["pin_id"])
    check("pin: the <snapshot>/4bit path spelling maps back to the launch id",
          out["pin_subdir"] == [418, [POCKET]], out["pin_subdir"])
    check("pin: the snapshot ROOT of the multi-variant repo is refused",
          out["pin_root"][0] == 400 and out["pin_root"][1] == [], out["pin_root"])
    check("pin: another repo is refused", out["pin_other"][0] == 400, out["pin_other"])
    check("sampling: a request that sets neither gets the roster defaults",
          out["unset"] == [1.15, 40], out["unset"])
    check("sampling: explicit request values always win",
          out["explicit"] == [1.0, 5], out["explicit"])
    check("sampling: an explicit null stays null (the client switched it off)",
          out["explicit_null"][0] is None and out["explicit_null"][1] == 40,
          out["explicit_null"])
    check("sampling: the Anthropic route gets the defaults too",
          out["anthropic"] == [1.15, 40], out["anthropic"])
    check("sampling: ...and keeps its explicit value", out["anthropic_explicit"] == 1.3,
          out["anthropic_explicit"])


# --------------------------------------------------------------------------
# 4. the real mlx_lm APIHandler (framework python)
# --------------------------------------------------------------------------
LM_DRIVER = r'''
import email.message, importlib.util, io, json, sys, types
spec = importlib.util.spec_from_file_location("hermes_launch_drv", sys.argv[1])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
import mlx_lm.server as S
L._patch_mlx_lm_sampling(S, {"repetition_penalty": 1.15, "top_k": 40})
H = S.APIHandler
got = []
H.handle_chat_completions = lambda self: "REQ"
H.handle_completion = lambda self, req, stop: got.append(
    [self.repetition_penalty, self.top_k, self.temperature])
cli = types.SimpleNamespace(num_draft_tokens=3, max_tokens=64, temp=0.0, top_p=1.0,
                            top_k=0, min_p=0.0)
def post(body):
    h = H.__new__(H)
    raw = json.dumps(body).encode()
    m = email.message.Message()
    m["Content-Length"] = str(len(raw))
    h.headers, h.rfile, h.wfile = m, io.BytesIO(raw), io.BytesIO()
    h.path = "/v1/chat/completions"
    h.response_generator = types.SimpleNamespace(cli_args=cli)
    del got[:]
    h.do_POST()
    return got[0] if got else h.wfile.getvalue().decode("latin-1")[:200]
msg = [{"role": "user", "content": "hi"}]
out = {"unset": post({"messages": msg}),
       "explicit": post({"messages": msg, "repetition_penalty": 1.0, "top_k": 5})}
print("RESULT " + json.dumps(out))
'''


def test_mlx_lm():
    print("\n4. the real mlx_lm APIHandler (framework python)")
    py = FRAMEWORK_PY
    if not py.exists() or subprocess.run([str(py), "-c", "import mlx_lm.server"],
                                         capture_output=True, timeout=120).returncode != 0:
        print("  SKIP mlx-lm layer: no python with mlx_lm here — the sampling defaults on "
              "the real mlx_lm server did not run")
        return
    ub = subprocess.run([str(py), "-c", "import site; print(site.getuserbase())"],
                        capture_output=True, text=True, timeout=60).stdout.strip()
    out, err = run_driver(py, LM_DRIVER, [LAUNCHER], {"PYTHONUSERBASE": ub} if ub else None)
    if out is None:
        check("mlx-lm driver ran", False, err)
        return
    check("mlx-lm: a body without them gets the roster defaults (temperature untouched)",
          out["unset"] == [1.15, 40, 0.0], out["unset"])
    check("mlx-lm: the body's own values win", out["explicit"] == [1.0, 5, 0.0], out["explicit"])


def main():
    # hermetic: the launcher's roster fallback must never read the owner's
    # ~/.hermes/dashboard/models.json from this suite
    os.environ["HERMES_MODELS_FILE"] = os.path.join(tempfile.gettempdir(),
                                                    "hermes-r3-no-such-models.json")
    L = load_launcher()
    for t in (lambda: test_pure(L), test_shell, test_vlm, test_mlx_lm):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check("section ran", False, "%s: %s" % (type(e).__name__, e))
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


main()
