#!/usr/bin/env python3
"""Regression test for B03 (docs/audits/2026-10-08-codebase-audit.md) and its
feature card F01 (docs/plans/feature-research-2026-10-21.md).

BEFORE: `add_model` accepted any non-empty string, `download_model` pulled
every file of the repo (including *.py and pickle-format weights), and BOTH
launchers passed --trust-remote-code on all four invocations, so add ->
download -> switch with a repo whose config/tokenizer_config carries an
`auto_map` ran that repo's Python in the model-server process.

AFTER:
  * the roster gains a per-entry `trust_remote_code` (default off); the
    dashboard copies a literal `true` into server-backend, mlx-server.sh adds
    --trust-remote-code only then, and mlx-server-bg.sh reads its own model's
    roster entry (it has no server-backend);
  * add_model validates the HF org/name shape, accepts the curated set (seeds
    + onboarding catalog, with the catalog's full roster shape) and needs an
    explicit confirm:true for anything else; no stored row carries
    trust_remote_code;
  * downloads never fetch *.bin/*.pt/*.pth/*.ckpt/*.pkl/*.pickle, nor *.py
    unless the owner marked the entry trust_remote_code:true by hand.

Runs the REAL shell launchers end to end under a throwaway HOME with fake
interpreters that print the exec line (nothing is loaded), and the REAL
server.py functions via ast extraction with stubbed globals.
"""
import ast
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
SERVER = REPO / "dashboard" / "server.py"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def extract(path, names, ns):
    """exec the named top-level defs/assignments of `path` into ns. Returns
    the missing names (empty when all were found)."""
    tree = ast.parse(Path(path).read_text())
    found, nodes = set(), []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names:
            nodes.append(n); found.add(n.name)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n); found.update(t.id for t in n.targets if isinstance(t, ast.Name))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    return sorted(set(names) - found)


def literal(path, name):
    for n in ast.parse(Path(path).read_text()).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                             for t in n.targets):
            return ast.literal_eval(n.value)
    raise KeyError(name)


# --------------------------------------------------------------------------
# shell launchers, end to end, with fake interpreters
# --------------------------------------------------------------------------
def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_launcher(script, files, venv=True):
    """Run REPO/<script> with HOME=a temp dir holding `files` (relative path ->
    text). Returns the EXEC line the fake interpreter printed."""
    with tempfile.TemporaryDirectory(prefix="hermes-b03-") as d:
        home = Path(d)
        dash = home / ".hermes" / "dashboard"
        dash.mkdir(parents=True)
        for rel, text in files.items():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(text)
        if venv:
            vb = home / ".hermes" / "mlx-vlm-venv" / "bin"
            vb.mkdir(parents=True)
            _exe(vb / "python", '#!/bin/bash\necho "EXEC vlm $*"\n')
        fb = home / "fakebin"
        fb.mkdir()
        # never lets a server start: `-m mlx_lm server` (the old exec) and the
        # launcher's --mlx-lm mode only print; JSON parsing passes through
        _exe(fb / "python3", '#!/bin/bash\nif [ "$2" = "--mlx-lm" ]; then echo "EXEC lm $*"; exit 0; fi\n'
                             'if [ "$1" = "-m" ]; then echo "EXEC m $*"; exit 0; fi\n'
                             'exec "%s" "$@"\n' % sys.executable)
        env = {"HOME": str(home), "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % fb,
               "HERMES_MODEL_ALWAYS_ON": "1"}
        if os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        if os.environ.get("HERMES_TESTS_OFFLINE"):
            env["HERMES_TESTS_OFFLINE"] = os.environ["HERMES_TESTS_OFFLINE"]
        # the bg gate needs a fresh token when the marker exists; no marker here
        r = subprocess.run(["/bin/bash", str(REPO / script)], capture_output=True,
                           text=True, env=env, timeout=120)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("EXEC ")]
    return (lines[-1] if lines else ""), (r.stdout or "") + (r.stderr or "")


def test_shell():
    vlm_backend = json.dumps({"backend": "mlx_vlm", "draft_model": "org/drafter",
                              "draft_kind": "mtp", "draft_block_size": 3,
                              "enable_thinking": False})
    trusted = json.dumps({"backend": "mlx_vlm", "enable_thinking": False,
                          "trust_remote_code": True})
    stringy = json.dumps({"backend": "mlx_vlm", "trust_remote_code": "true"})
    sb = ".hermes/dashboard/server-backend"

    line, log = run_launcher("mlx-server.sh", {sb: vlm_backend})
    check("primary mlx_vlm: launched", line.startswith("EXEC vlm "), log[-600:])
    check("primary mlx_vlm: no --trust-remote-code by default",
          line and "--trust-remote-code" not in line, line)
    line, log = run_launcher("mlx-server.sh", {sb: trusted})
    check("primary mlx_vlm: roster trust_remote_code:true adds the flag",
          "--trust-remote-code" in line, line or log[-600:])
    line, _ = run_launcher("mlx-server.sh", {sb: stringy})
    check("primary: a non-boolean 'true' does not count", line and "--trust-remote-code" not in line, line)

    line, log = run_launcher("mlx-server.sh", {}, venv=False)
    check("primary mlx-lm fallback: launched through the guarded launcher",
          line.startswith("EXEC lm ") and "--mlx-lm" in line, log[-600:])
    check("primary mlx-lm fallback: no --trust-remote-code by default",
          line and "--trust-remote-code" not in line, line)
    line, log = run_launcher("mlx-server.sh", {sb: json.dumps({"backend": "mlx_lm",
                                                              "trust_remote_code": True})},
                             venv=False)
    check("primary mlx-lm: roster trust_remote_code:true adds the flag",
          "--trust-remote-code" in line, line or log[-600:])

    roster = lambda trc, mid="mlx-community/Qwen3.5-9B-4bit": json.dumps(
        {"models": [{"id": mid, "label": "x", "trust_remote_code": trc}]})
    mj = ".hermes/dashboard/models.json"
    line, log = run_launcher("mlx-server-bg.sh", {})
    check("bg lane: launched", line.startswith("EXEC vlm "), log[-600:])
    check("bg lane: no --trust-remote-code by default", line and "--trust-remote-code" not in line, line)
    line, log = run_launcher("mlx-server-bg.sh", {mj: roster(True)})
    check("bg lane: its roster entry's trust_remote_code:true adds the flag",
          "--trust-remote-code" in line, line or log[-600:])
    line, _ = run_launcher("mlx-server-bg.sh", {mj: roster(True, "other/model")})
    check("bg lane: another entry's flag does not leak", line and "--trust-remote-code" not in line, line)
    line, _ = run_launcher("mlx-server-bg.sh", {mj: "{not json"})
    check("bg lane: unreadable models.json -> off", line and "--trust-remote-code" not in line, line)
    line, log = run_launcher("mlx-server-bg.sh", {mj: roster(True)}, venv=False)
    check("bg lane mlx-lm fallback honours the roster flag too",
          line.startswith("EXEC lm ") and "--trust-remote-code" in line, line or log[-600:])
    line, _ = run_launcher("mlx-server-bg.sh", {}, venv=False)
    check("bg lane mlx-lm fallback: off by default", line and "--trust-remote-code" not in line, line)


# --------------------------------------------------------------------------
# server-backend carries the roster flag
# --------------------------------------------------------------------------
def test_write_backend():
    with tempfile.TemporaryDirectory(prefix="hermes-b03-sb-") as d:
        venv_py = os.path.join(d, "python")
        open(venv_py, "w").close()
        entries = {}
        ns = {"os": os, "json": json, "TEMPLATE_ARGS_FILE": os.path.join(d, "cta"),
              "SERVER_BACKEND_FILE": os.path.join(d, "sb"), "MLX_VLM_VENV_PY": venv_py,
              "_model_entry": lambda mid: entries.get(mid),
              "_draft_model_path": lambda m: m.get("draft_model")}
        missing = extract(SERVER, ["_write_template_args"], ns)
        if missing:
            check("_write_template_args found", False, missing)
            return
        entries["a/plain"] = {"id": "a/plain", "backend": "mlx_vlm"}
        entries["a/trusted"] = {"id": "a/trusted", "backend": "mlx_vlm", "trust_remote_code": True}
        entries["a/stringy"] = {"id": "a/stringy", "backend": "mlx_lm", "trust_remote_code": "yes"}
        out = {}
        for mid in entries:
            ns["_write_template_args"](mid)
            out[mid] = json.load(open(ns["SERVER_BACKEND_FILE"]))
        check("server-backend: no flag for a plain entry", "trust_remote_code" not in out["a/plain"],
              out["a/plain"])
        check("server-backend: literal true is carried", out["a/trusted"].get("trust_remote_code") is True,
              out["a/trusted"])
        check("server-backend: a truthy non-boolean is not",
              "trust_remote_code" not in out["a/stringy"], out["a/stringy"])


# --------------------------------------------------------------------------
# add_model
# --------------------------------------------------------------------------
def test_add_model():
    reg = {"models": [dict(m) for m in literal(SERVER, "_SEED_MODELS")]}
    written = []
    catalog = literal(REPO / "dashboard" / "aux_onboarding.py", "_ONB_CATALOG")
    ns = {"re": re, "MODELS_FILE": "/dev/null/models.json",
          "_model_registry": lambda: reg["models"],
          "write_json": lambda p, obj: (written.append(obj), reg.update(obj)),
          "_ONB_BY_ID": {m["id"]: m for m in catalog},
          # E113: add_model writes through the real locked models_update
          "_state_lock": __import__("threading").Lock(),
          "read_json_for_update": lambda p, d: reg}
    missing = extract(SERVER, ["_SEED_MODELS", "_HF_REPO_RE", "_valid_repo_id",
                               "_curated_model_ids", "add_model", "models_update"], ns)
    if missing:
        check("add_model lockdown helpers present", False, missing)
        return
    add = ns["add_model"]
    for bad in ("../x", "a b/c", "noslash", "a/b/c", "org/..", "org/x--y", "-org/x",
                "org/x.", "o" * 60 + "/" + "n" * 61, "", "org/na me", "org/x\n"):
        r = add(bad, confirm=True)
        check("malformed id refused even with confirm: %r" % bad[:30],
              not r.get("ok"), r)
    n0 = len(reg["models"])
    r = add("attacker-org/repo")
    check("a non-curated id without confirm is refused, nothing stored",
          not r.get("ok") and r.get("needs_confirm") and len(reg["models"]) == n0, r)
    r = add("attacker-org/repo", confirm="true")
    check("confirm must be the boolean true", not r.get("ok") and len(reg["models"]) == n0, r)
    r = add("attacker-org/repo", label="Evil\x07 label", confirm=True)
    row = reg["models"][-1]
    check("confirm:true adds it", r.get("ok") and row["id"] == "attacker-org/repo", r)
    check("...as a plain weights-only row (no backend, no trust_remote_code)",
          "trust_remote_code" not in row and "backend" not in row and row["label"] == "Evil label",
          row)
    r = add("mlx-community/Qwen3.5-2B-4bit")
    row = reg["models"][-1]
    check("a curated catalog id needs no confirm and gets the FULL roster shape",
          r.get("ok") and row["id"] == "mlx-community/Qwen3.5-2B-4bit"
          and row.get("backend") == "mlx_vlm" and "size_gb" not in row
          and "trust_remote_code" not in row, row)
    r = add("mlx-community/Qwen3.8-27B-4bit")
    check("an id already on the roster is a no-op ok", r.get("ok") and
          sum(1 for m in reg["models"] if m["id"] == "mlx-community/Qwen3.8-27B-4bit") == 1, r)
    src = SERVER.read_text()
    check("the route passes confirm only when it is the boolean true",
          'd.get("confirm") is True' in src)


# --------------------------------------------------------------------------
# download_model never fetches code or pickles
# --------------------------------------------------------------------------
class _R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def test_download_patterns():
    calls = []

    class FakeSub:
        def run(self, argv, **kw):
            calls.append(json.loads(argv[-1]))
            return _R(0)

    class FakeThreading:
        class Thread:
            def __init__(self, target=None, daemon=None, **kw):
                self.t = target

            def start(self):
                self.t()

    entries = {
        "org/plain": {"id": "org/plain"},
        "org/multi": {"id": "org/multi", "ignore_patterns": ["2-bit/*"],
                      "draft_subfolder": "mtp"},
        "org/withdraft": {"id": "org/withdraft", "draft_model": "org/drafter"},
        "org/trusted": {"id": "org/trusted", "trust_remote_code": True},
    }
    ns = {"json": json, "sys": sys, "os": os, "subprocess": FakeSub(),
          "threading": FakeThreading, "_model_entry": lambda m: entries.get(m),
          "_model_dl": {}, "_model_dl_err": {}, "_model_dl_lock": __import__("threading").Lock(),
          "_model_download_gb": lambda e: None,
          "_disk_free_gb": lambda: None, "_disk_short": lambda a, b: False,
          "DISK_HEADROOM_GB": 5.0, "_hf_python": lambda: "/fake/python",
          "_hermes_env": lambda: {}, "_model_downloaded": lambda m: True,
          "_draft_ready": lambda e: True,
          # round-2 R2-8: each job runs through _dl_run (no-progress watchdog)
          "_dl_run": lambda argv, repo_id, env=None: (FakeSub().run(argv).returncode, "", False)}
    missing = extract(SERVER, ["_DL_NEVER_PICKLE", "_DL_NEVER_CODE", "_dl_ignore_patterns",
                               "download_model"], ns)
    if missing:
        check("download lockdown helpers present", False, missing)
        return
    never = ["*.bin", "*.pt", "*.pth", "*.ckpt", "*.pkl", "*.pickle"]
    for mid in entries:
        del calls[:]
        r = ns["download_model"](mid)
        check("download %s started" % mid, r.get("ok"), r)
        for c in calls:
            ig = c.get("ignore_patterns") or []
            check("%s job %s: pickle formats ignored" % (mid, c["repo_id"]),
                  all(p in ig for p in never), ig)
            if mid == "org/trusted":
                check("%s: *.py let through (owner opted in by hand)" % mid, "*.py" not in ig, ig)
            else:
                check("%s job %s: *.py ignored" % (mid, c["repo_id"]), "*.py" in ig, ig)
        if mid == "org/multi":
            check("org/multi: the entry's own ignore_patterns are kept",
                  "2-bit/*" in (calls[0].get("ignore_patterns") or []), calls)
        if mid == "org/withdraft":
            check("org/withdraft: the drafter repo is a second, equally filtered job",
                  [c["repo_id"] for c in calls] == ["org/withdraft", "org/drafter"], calls)


def main():
    test_shell()
    test_write_backend()
    test_add_model()
    test_download_patterns()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
