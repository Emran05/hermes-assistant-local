"""A stand-in for Hermes Agent's tools/checkpoint_manager.py (E01 fixture).

The Flight Recorder's checkpoint driver (dashboard/aux_recorder.py
_CKPT_DRIVER) imports Hermes' own module from ~/.hermes/hermes-agent. The unit
tier must not depend on that checkout, so t_e01_undo_created_proof.py copies
THIS file to <scratch HOME>/.hermes/hermes-agent/tools/checkpoint_manager.py
and lets the real driver import it. Everything here is the upstream behaviour
the recorder relies on, vendored as data and kept as close to the original as
a short file allows (upstream ~/.hermes/hermes-agent/tools/checkpoint_manager.py,
read 2026-11-10):

  DEFAULT_EXCLUDES            :81-142   (written to the store's info/exclude, :476-480)
  _normalize_path             :196-198
  _project_hash               :201-204
  _store_path                 :207-209
  _index_path / _ref_name     :223-228
  _git_env                    :239-273  (GIT_DIR + GIT_WORK_TREE, no global/system config)
  CheckpointManager.list_checkpoints  :690-729  (newest first, "%H|%h|%aI|%s")
  CheckpointManager.diff      :744-792
  CheckpointManager.restore   :794-848  (a pre-rollback _take, then `checkout <c> -- <file>`)
  CheckpointManager._take     :873-1005 (per-project index seeded from the ref, `add -A`,
                                          "no changes" skip, commit-tree, update-ref)
  _drop_oversize_from_index   :1007-1051 (files > max_file_size_mb leave the snapshot)

It writes only under the scratch HOME the test sets.
"""
import hashlib
import os
import subprocess
from pathlib import Path

DEFAULT_EXCLUDES = [
    "node_modules/", "dist/", "build/", "target/", "out/", ".next/", ".nuxt/",
    "__pycache__/", "*.pyc", "*.pyo", ".cache/", ".pytest_cache/", ".mypy_cache/",
    ".ruff_cache/", "coverage/", ".coverage",
    ".venv/", "venv/", "env/",
    ".git/", ".hg/", ".svn/",
    ".worktrees/",
    "*.so", "*.dylib", "*.dll", "*.o", "*.a", "*.jar", "*.class", "*.exe", "*.obj",
    "*.mp4", "*.mov", "*.mkv", "*.webm", "*.zip", "*.tar", "*.tar.gz", "*.tgz",
    "*.7z", "*.rar", "*.iso",
    ".env", ".env.*", ".env.local", ".env.*.local",
    ".DS_Store", "Thumbs.db",
    "*.log",
]

CHECKPOINT_BASE = Path(os.path.expanduser("~")) / ".hermes" / "checkpoints"


def _normalize_path(path_value):
    return Path(path_value).expanduser().resolve()


def _project_hash(working_dir):
    return hashlib.sha256(str(_normalize_path(working_dir)).encode()).hexdigest()[:16]


def _store_path(base=None):
    return (base or CHECKPOINT_BASE) / "store"


def _index_path(store, dir_hash):
    return store / "indexes" / dir_hash


def _ref_name(dir_hash):
    return "refs/hermes/" + dir_hash


def _git_env(store, working_dir, index_file=None):
    env = os.environ.copy()
    env["GIT_DIR"] = str(store)
    env["GIT_WORK_TREE"] = str(_normalize_path(working_dir))
    env.pop("GIT_NAMESPACE", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    else:
        env.pop("GIT_INDEX_FILE", None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _run_git(args, store, working_dir, index_file=None):
    wd = _normalize_path(working_dir)
    p = subprocess.run(["git"] + list(args), capture_output=True, text=True,
                       env=_git_env(store, str(wd), index_file=index_file),
                       cwd=str(wd), stdin=subprocess.DEVNULL, timeout=60)
    return p.returncode == 0, p.stdout.strip(), p.stderr.strip()


def _init_store(store):
    if (store / "HEAD").exists():
        return
    store.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    subprocess.run(["git", "init", "-q", "--bare", str(store)], env=env, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)
    base = str(store.parent)
    for k, v in (("user.email", "hermes@local"), ("user.name", "Hermes Checkpoint"),
                 ("commit.gpgsign", "false"), ("gc.auto", "0")):
        _run_git(["config", k, v], store, base)
    (store / "info").mkdir(exist_ok=True)
    (store / "info" / "exclude").write_text("\n".join(DEFAULT_EXCLUDES) + "\n",
                                            encoding="utf-8")


class CheckpointManager:
    def __init__(self, enabled=False, max_snapshots=20, max_total_size_mb=500,
                 max_file_size_mb=10):
        self.enabled = enabled
        self.max_snapshots = max_snapshots
        self.max_file_size_mb = max_file_size_mb

    def list_checkpoints(self, working_dir):
        abs_dir = str(_normalize_path(working_dir))
        store = _store_path(CHECKPOINT_BASE)
        if not (store / "HEAD").exists():
            return []
        ok, out, _ = _run_git(["log", _ref_name(_project_hash(abs_dir)),
                               "--format=%H|%h|%aI|%s", "-n", str(self.max_snapshots)],
                              store, abs_dir)
        res = []
        for line in (out.splitlines() if ok else []):
            p = line.split("|", 3)
            if len(p) == 4:
                res.append({"hash": p[0], "short_hash": p[1], "timestamp": p[2],
                            "reason": p[3]})
        return res

    def diff(self, working_dir, commit_hash):
        abs_dir = str(_normalize_path(working_dir))
        store = _store_path(CHECKPOINT_BASE)
        idx = _index_path(store, _project_hash(abs_dir))
        _run_git(["add", "-A"], store, abs_dir, index_file=idx)
        ok_s, st, _ = _run_git(["diff", "--stat", commit_hash, "--cached"], store, abs_dir,
                               index_file=idx)
        ok_d, df, _ = _run_git(["diff", commit_hash, "--cached", "--no-color"], store,
                               abs_dir, index_file=idx)
        _run_git(["read-tree", _ref_name(_project_hash(abs_dir))], store, abs_dir,
                 index_file=idx)
        return {"success": True, "stat": st if ok_s else "", "diff": df if ok_d else ""}

    def restore(self, working_dir, commit_hash, file_path=None):
        abs_dir = str(_normalize_path(working_dir))
        store = _store_path(CHECKPOINT_BASE)
        if not (store / "HEAD").exists():
            return {"success": False, "error": "No checkpoints exist for this directory"}
        ok, _, err = _run_git(["cat-file", "-t", commit_hash], store, abs_dir)
        if not ok:
            return {"success": False, "error": "Checkpoint '%s' not found" % commit_hash,
                    "debug": err or None}
        self._take(abs_dir, "pre-rollback snapshot (restoring to %s)" % commit_hash[:8])
        idx = _index_path(store, _project_hash(abs_dir))
        ok, _, err = _run_git(["checkout", commit_hash, "--", file_path or "."], store,
                              abs_dir, index_file=idx)
        if not ok:
            return {"success": False, "error": "Restore failed: " + err, "debug": err or None}
        out = {"success": True, "restored_to": commit_hash[:8], "directory": abs_dir}
        if file_path:
            out["file"] = file_path
        return out

    def _take(self, working_dir, reason):
        working_dir = str(_normalize_path(working_dir))
        store = _store_path(CHECKPOINT_BASE)
        _init_store(store)
        dh = _project_hash(working_dir)
        idx, ref = _index_path(store, dh), _ref_name(dh)
        idx.parent.mkdir(parents=True, exist_ok=True)
        ok_ref, tip, _ = _run_git(["rev-parse", "--verify", ref + "^{commit}"], store,
                                  working_dir)
        if idx.exists():
            if ok_ref and tip:
                _run_git(["read-tree", tip], store, working_dir, index_file=idx)
            else:
                idx.unlink()
        ok, _, _ = _run_git(["add", "-A"], store, working_dir, index_file=idx)
        if not ok:
            return False
        cap = self.max_file_size_mb * 1024 * 1024
        if cap > 0:
            ok, out, _ = _run_git(["ls-files", "--cached", "-z"], store, working_dir,
                                  index_file=idx)
            big = [p for p in out.split("\x00") if p and
                   os.path.getsize(os.path.join(working_dir, p)) > cap] if ok else []
            if big:
                _run_git(["rm", "--cached", "--quiet", "--"] + big, store, working_dir,
                         index_file=idx)
        if ok_ref and tip:
            same, _, _ = _run_git(["diff-index", "--cached", "--quiet", tip], store,
                                  working_dir, index_file=idx)
            if same:
                return False
        ok, tree, _ = _run_git(["write-tree"], store, working_dir, index_file=idx)
        args = ["commit-tree", tree, "-m", reason, "--no-gpg-sign"]
        if ok_ref and tip:
            args[2:2] = ["-p", tip]
        ok, sha, _ = _run_git(args, store, working_dir, index_file=idx)
        if not ok:
            return False
        _run_git(["update-ref", ref, sha], store, working_dir)
        return True
