#!/usr/bin/env python3
"""doctor.py — one command, one screen of health checks for Hermes Assistant.

    python3 dashboard/doctor.py            # the one-screen text report
    python3 dashboard/doctor.py --json     # the same thing as JSON
    python3 dashboard/doctor.py --quiet    # only what is not PASS, plus the tally

Exit code 0 when nothing FAILed, 1 otherwise — so it drops into a shell `&&`
chain, a pre-flight in update.sh, or CI.

WHAT THIS IS ALLOWED TO DO.  Every check is cheap, READ-ONLY and must NEVER
start, wake or load a model server.  The owner runs on battery and the primary
lane is ~19 GB resident, so a diagnostic that costs 30 s and 19 GB of RAM is
worse than no diagnostic.  Concretely:
  * launchd is inspected with `launchctl list <label>` — never `kickstart`,
    `bootstrap` or `start`.
  * the model lanes are probed with a 2 s HTTP GET to :8080 / :8081.  That is
    safe because the mlx plists carry no `Sockets` key (they are RunAtLoad
    false / KeepAlive false, gated further by `model-autostart-off` — see
    install-services.sh), so nothing on this Mac is socket-activated and a
    refused connection stays a refused connection.
  * everything else is a file read, a statvfs, or a sub-second subprocess
    (`sw_vers`, `sysctl`, `hermes --version`).
  * nothing is written.  Not models.json, not settings.json, not a cache file.
    `_model_registry()` in server.py WRITES models.json when it migrates seeds;
    that is why this module reads the file directly instead of calling it.

STRUCTURE.  `CHECKS` is a list of (id, label, fn).  A check function returns
`{"status", "detail", "fix"}` and `run_checks()` normalises each result to the
full `{"id", "label", "status", "detail", "fix"}` shape.  Every call is wrapped
in try/except, so one broken check can never take the report down — a crash
inside a check becomes a FAIL carrying the exception text.  Adding a check is
one decorated function; nothing else in the file changes.

REUSE.  This module is standalone by design: importing server.py would exec
every aux_*.py, start their daemon threads and cost seconds, which is the wrong
shape for a CLI.  So the handful of helpers it needs (`_weights_complete`,
`_hf_snapshot_dir`, `_model_fit`, the Claude-CLI resolver, onboarding's FDA
verdict) are replicated here in their minimal form.  But when doctor runs INSIDE
the dashboard, aux_doctor.py assigns `doctor.HOST = globals()` and `_host(name)`
then prefers the live function over the local copy — so the served answer is
computed by the same code the rest of the dashboard uses, and the CLI still
works on a machine where the dashboard is down.
"""

import argparse
import glob as _glob
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# paths / constants — mirrored from server.py + install-services.sh
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, ".hermes", "dashboard")
LOGS = os.path.join(HOME, ".hermes", "logs")
DASH_LOG = os.path.join(LOGS, "dashboard.log")
ERR_LOG = os.path.join(LOGS, "errors.log")
MLX_LOG = os.path.join(LOGS, "mlx-server.log")     # the plist's Standard*Path
# Every other service log in ~/.hermes/logs, for the size line in `Logs`
# (2026-10-21 audit C14).  The first four are launchd StandardOutPaths that
# server.py now copy-truncates beside dashboard.log; gateway.log is ALSO the
# agent's own RotatingFileHandler target (5 MB x 3), so it is listed for its
# size but rotated by Hermes, not by us.
SERVICE_LOGS = ("mlx-server.log", "mlx-bg.log", "serve.log",
                "gateway.error.log", "gateway.log")
LOG_BIG_MB = 64          # 8x the rotation cap: past this, nothing is rotating it

DASH_PORT = int(os.environ.get("DASH_PORT", "7788") or "7788")
DASH_BASE = "http://127.0.0.1:%d" % DASH_PORT
MODEL_URL = os.environ.get("MODEL_URL", "http://127.0.0.1:8080/v1/models")
BG_MODEL_URL = os.environ.get("BG_MODEL_URL", "http://127.0.0.1:8081/v1/models")

VERSION_FILE = os.path.join(ROOT, "VERSION")
REPO_CONFIG = os.path.join(ROOT, "config.yaml")
HERMES_CONFIG = os.path.join(HOME, ".hermes", "config.yaml")
HERMES_PLUGINS = os.path.join(HOME, ".hermes", "plugins")
GOOG_TOKEN = os.path.join(HOME, ".hermes", "google_token.json")     # aux_google.py

SETTINGS_FILE = os.path.join(DATA, "settings.json")
MODELS_FILE = os.path.join(DATA, "models.json")
ACTIVE_MODEL_FILE = os.path.join(DATA, "active-model")
MSG_STORE = os.path.join(DATA, "messages.json")
IX_DB = os.path.join(DATA, "index.db")
NY_STORE = os.path.join(DATA, "needsyou.json")
ONB_FILE = os.path.join(DATA, "onboarded")
UPD_CACHE = os.path.join(DATA, "update-check.json")
PAUSE_FILE = os.path.join(DATA, "agent-paused")
IDLE_SUSPEND_FILE = os.path.join(DATA, "agent-idle-suspended")
AUTOSTART_OFF = os.path.join(DATA, "model-autostart-off")

DEFAULT_MODEL = "mlx-community/Qwen3.8-27B-4bit"
MLX_VLM_VENV = os.path.join(HOME, ".hermes", "mlx-vlm-venv")
MLX_VLM_VENV_PY = os.path.join(MLX_VLM_VENV, "bin", "python")
# The pin exists because 0.6.15-0.6.17 CORRUPT OUTPUT when two requests overlap
# while the native MTP drafter is loaded — exactly this configuration
# (CLAUDE.md, install-mlx-vlm-venv.sh header).  A different version is a WARN,
# never a silent pass.
MLX_VLM_PIN = "0.6.14"

# install-services.sh
LABEL_DASH = "com.hermes.dashboard"
LABEL_SERVE = "com.hermes.serve"
LABEL_MLX = "com.hermes.mlx-server"
LABEL_BG = "com.hermes.mlx-bg"
AGENTS_DIR = os.path.join(HOME, "Library", "LaunchAgents")

DISK_FAIL_GB = 5.0
DISK_WARN_GB = 20.0

PASS, WARN, FAIL = "pass", "warn", "fail"

# --------------------------------------------------------------------------
# host bridge — the live dashboard's helpers when we are running inside it
# --------------------------------------------------------------------------
HOST = {}


def _host(name):
    """The dashboard's own `name` when doctor was imported by aux_doctor.py.

    Returns None outside the server process (or when the global vanished), and
    every caller has a local fallback — so the CLI never depends on this."""
    try:
        v = HOST.get(name)
    except Exception:
        return None
    return v if callable(v) else None


# --------------------------------------------------------------------------
# tiny primitives
# --------------------------------------------------------------------------
def _run(argv, timeout=6):
    """(returncode, output) for a short command — never raises, never inherits
    stdin.  `rc` is -1 when the command could not be run at all (missing,
    timed out) and `output` is then the reason.

    THE RETURN CODE IS PART OF THE ANSWER.  This used to hand back stdout-or-
    stderr and nothing else, so a `hermes --version` that printed a traceback
    and exited 1 read as a healthy PASS with the traceback as its detail.  Every
    caller must look at `rc` before believing the text."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)
    return p.returncode, ((p.stdout or "").strip() or (p.stderr or "").strip())


def _cmd_note(argv, rc, out):
    """One clause naming a command that did not exit 0, for a check detail."""
    what = os.path.basename((argv or [""])[0]) or "command"
    if rc < 0:
        return "%s could not run (%s)" % (what, _sample(out, cap=80, hard=60))
    tail = _sample(out, cap=80, hard=60)
    return "%s exited %d%s" % (what, rc, (" — " + tail) if tail else "")


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _json_state(path):
    """("absent"|"ok"|"bad", value) for a JSON file.

    `_read_json` hands back the default for a MISSING file and for a corrupt
    one alike, and on a fresh install that difference is the whole answer — an
    absent settings.json is normal (the dashboard writes defaults on the first
    change), a corrupt one is a real failure."""
    try:
        with open(path) as f:
            return "ok", json.load(f)
    except FileNotFoundError:
        return "absent", None
    except Exception:
        return "bad", None


# The report is meant to be pasted into an issue, so an absolute path under the
# home directory would ship the owner's account name with it.  Not anchored, so
# a path quoted mid-sentence is caught too; the lookahead stops
# /Users/<name> from matching a DIFFERENT user whose name merely starts the
# same way.
_HOME_RE = re.compile(re.escape(HOME) + r"(?![A-Za-z0-9._\-])") if HOME not in ("", "/") else None


def _display_path(s):
    """Every path this module emits, with the home directory collapsed to `~`.

    Applied centrally in `run_checks()` to each detail and fix, so a check
    added later gets it for free — and applied to the JSON payload as well as
    the text report.  There is deliberately no second, raw copy: nothing
    downstream (aux_doctor.js, the MCP `doctor` tool) reads a path back out of
    a detail, the fixes are shell commands where `~` expands to the same place,
    and a payload that still carried the absolute path would defeat the point."""
    s = "" if s is None else str(s)
    if not s or _HOME_RE is None:
        return s
    return _HOME_RE.sub("~", s)


def _plain(text):
    """Control characters out (a log line can carry ANSI escapes), whitespace
    squeezed.  NOT a redactor — `_sample` is the only thing that scrubs."""
    s = "".join(" " if (ord(c) < 0x20 or ord(c) == 0x7F) else c
                for c in str("" if text is None else text))
    return " ".join(s.split())


def _sample(text, cap=60, hard=36):
    """Raw text from a log or a subprocess, made safe to print in a report.

    Inside the dashboard the scrubber is aux_convos.py's `_cv_redact`, resolved
    BY NAME at call time through the host bridge (the discipline aux_trace.py
    uses) — it collapses labelled secrets, Bearer tokens, the unlabeled vendor
    shapes and any `~/.hermes/...` path.  On the CLI there is no scrubber in
    this process, so the line is truncated harder instead: a shorter quote is a
    smaller leak, and the count and the shape still identify the failure."""
    s = _display_path(_plain(text))
    if not s:
        return ""
    fn = _host("_cv_redact")
    if fn:
        try:
            s = _display_path(_plain(fn(s)))
        except Exception:
            fn = None
    lim = cap if fn else hard
    return (s[:max(1, lim - 3)] + "...") if len(s) > lim else s


# Every probe here is loopback (the dashboard, the model lanes). urllib's
# default opener honours the macOS system web proxy, which has no loopback
# exemption, so with a manual HTTP proxy set those probes went to the proxy
# (2026-11-09 audit E20). An empty ProxyHandler connects directly, always —
# and, unlike urlopen(), never picks up an opener server.py installed.
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(url, timeout=3):
    """(status, body_text) or (0, error_string).  GET only, no headers added —
    the dashboard's same-origin guard allows a request that carries no Origin.
    Never through a proxy (E20)."""
    try:
        with _LOOPBACK_OPENER.open(url, timeout=timeout) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


def _lane_up(url, timeout=2):
    """Is a model lane answering?  A PROBE — see the module docstring on why
    this cannot start anything."""
    st, _ = _http(url, timeout)
    return st == 200


def _launchctl(label):
    """{"loaded":bool,"pid":int|None,"exit":int|None} for a launchd label.

    `launchctl list <label>` is read-only and returns 113 for a label that is
    not in the domain (booted out) — which is the NORMAL state of the model
    services here, since 2026-09-01 made them on-demand."""
    try:
        p = subprocess.run(["/bin/launchctl", "list", label],
                           capture_output=True, text=True, timeout=6,
                           stdin=subprocess.DEVNULL)
    except Exception:
        return {"loaded": False, "pid": None, "exit": None}
    if p.returncode != 0:
        return {"loaded": False, "pid": None, "exit": None}
    out = p.stdout or ""

    def _num(key):
        m = re.search(r'"%s"\s*=\s*(-?\d+)' % key, out)
        return int(m.group(1)) if m else None

    return {"loaded": True, "pid": _num("PID"), "exit": _num("LastExitStatus")}


def _plist_installed(label):
    return os.path.isfile(os.path.join(AGENTS_DIR, label + ".plist"))


def _fmt_when(ts):
    """Absolute 12-hour clock — the repo's design law, never 'x minutes ago'."""
    if not ts:
        return "never"
    try:
        t = time.localtime(float(ts))
        today = time.localtime()
        stamp = time.strftime("%-I:%M %p", t)
        if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
            return stamp
        return time.strftime("%b %-d ", t) + stamp
    except Exception:
        return "never"


def _ago(ts):
    try:
        return max(0.0, time.time() - float(ts))
    except Exception:
        return None


def _short(mid):
    """'mlx-community/Qwen3.8-27B-4bit' -> 'Qwen3.8-27B-4bit'."""
    return (mid or "").split("/")[-1] or (mid or "?")


def _gb(n):
    return "%.1f GB" % n if n is not None else "?"


def ok(detail, fix=""):
    return {"status": PASS, "detail": detail, "fix": fix}


def warn(detail, fix=""):
    return {"status": WARN, "detail": detail, "fix": fix}


def bad(detail, fix=""):
    return {"status": FAIL, "detail": detail, "fix": fix}


# --------------------------------------------------------------------------
# minimal replicas of server.py helpers (see the REUSE note in the docstring)
# --------------------------------------------------------------------------
def _machine_ram_gb():
    fn = _host("_machine_ram_gb")
    if fn:
        try:
            return fn()
        except Exception:
            pass
    rc, out = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"], timeout=3)
    if rc != 0:
        return None
    try:
        b = int(out.strip())
        return b / (1024 ** 3) if b > 0 else None
    except Exception:
        return None


def _disk_free_gb(path=None):
    fn = _host("_disk_free_gb")
    if fn:
        try:
            return fn(path)
        except Exception:
            pass
    try:
        st = os.statvfs(path or HOME)
        # f_bavail, not f_bfree: the blocks a non-root download can really use.
        return round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
    except Exception:
        return None


def _model_fit(model_ram_gb, machine_gb=None):
    fn = _host("_model_fit")
    if fn:
        try:
            return fn(model_ram_gb, machine_gb)
        except Exception:
            pass
    if not model_ram_gb or model_ram_gb <= 0:
        return None
    m = _machine_ram_gb() if machine_gb is None else machine_gb
    if not m or m <= 0:
        return None
    if model_ram_gb > 0.85 * m:
        return "no"
    if model_ram_gb > 0.60 * m:
        return "tight"
    return "ok"


def _hf_snapshot_dir(mid):
    fn = _host("_hf_snapshot_dir")
    if fn:
        try:
            return fn(mid)
        except Exception:
            pass
    base = os.path.join(HOME, ".cache", "huggingface", "hub",
                        "models--" + (mid or "").replace("/", "--"))
    snaps = os.path.join(base, "snapshots")
    try:
        with open(os.path.join(base, "refs", "main")) as f:
            sha = f.read().strip()
        if sha and "/" not in sha and ".." not in sha and \
                os.path.isdir(os.path.join(snaps, sha)):
            return os.path.join(snaps, sha)
    except OSError:
        pass
    try:
        cands = [os.path.join(snaps, d) for d in os.listdir(snaps)]
    except OSError:
        return None
    cands = [d for d in cands if os.path.isdir(d)]
    return max(cands, key=os.path.getmtime) if cands else None


def _weights_complete(d):
    fn = _host("_weights_complete")
    if fn:
        try:
            return fn(d)
        except Exception:
            pass
    idx = os.path.join(d, "model.safetensors.index.json")
    try:
        if os.path.isfile(idx):
            with open(idx) as f:
                files = set((json.load(f).get("weight_map") or {}).values())
            return bool(files) and all(
                os.path.isfile(os.path.join(d, fn2)) for fn2 in files)
        return any(f.endswith(".safetensors") for f in os.listdir(d))
    except (OSError, ValueError):
        return False


_SUBFOLDER_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,3}$")


def _subfolder(v):
    """server.py _roster_subfolder's rule: a clean repo-relative subfolder, or
    None for a set-but-invalid value (never joined onto a snapshot path)."""
    if v is None or v == "":
        return ""
    if not isinstance(v, str):
        return None
    s = v.strip().strip("/")
    if not s or len(s) > 64 or not _SUBFOLDER_RE.fullmatch(s) \
            or any(p in (".", "..") for p in s.split("/")):
        return None
    return s


def _snapshot_complete(snap, sub):
    """True when the weights a roster row loads are all present: the snapshot
    root, or its `model_subfolder` (PocketAiHub keeps its weights in 4bit/)."""
    if not snap or sub is None:
        return False
    return _weights_complete(os.path.join(snap, sub) if sub else snap)


def _model_downloaded(mid):
    snap = _hf_snapshot_dir(mid)
    return bool(snap) and _weights_complete(snap)


def _draft_state(m):
    """"ready" | "missing" | "" (no drafter) for a roster entry."""
    sub = _subfolder(m.get("draft_subfolder"))
    if sub is None:
        return "missing"
    if sub:
        snap = _hf_snapshot_dir(m.get("draft_model") or m.get("id") or "")
        p = os.path.join(snap, sub) if snap else None
        return "ready" if (p and os.path.isdir(p) and _weights_complete(p)) else "missing"
    dm = m.get("draft_model")
    if not dm:
        return ""
    return "ready" if _model_downloaded(dm) else "missing"


def _claude_bin():
    """The Claude CLI, resolved the way aux_claudebridge._cb_claude_bin() does
    (launchd's PATH excludes the nvm bin dir, so look there explicitly)."""
    fn = _host("_cb_claude_bin")
    if fn:
        try:
            return fn()
        except Exception:
            pass
    cands = sorted(_glob.glob(os.path.join(
        HOME, ".nvm", "versions", "node", "*", "bin", "claude")), reverse=True)
    cands += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude",
              os.path.join(HOME, ".local", "bin", "claude")]
    w = shutil.which("claude")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _hermes_bin():
    """The same resolution server.py's HERMES constant uses."""
    v = HOST.get("HERMES") if isinstance(HOST, dict) else None
    if isinstance(v, str) and v:
        return v
    return shutil.which("hermes") or os.path.join(HOME, ".local", "bin", "hermes")


def _fda():
    """Full Disk Access verdict, onboarding's way: the APP holds FDA (a launchd
    python never can) and reports it in the ingest payload, which aux_messages
    persists as `fda` in messages.json.  True / False / None — None means "the
    app has not reported yet" and must render as UNKNOWN, never as denied."""
    fn = _host("_onb_fda")
    if fn:
        try:
            return fn()
        except Exception:
            pass
    st = _read_json(MSG_STORE, None)
    if not isinstance(st, dict) or "fda" not in st:
        return None
    return bool(st.get("fda"))


def _escalation_on():
    """settings.json claude_escalation.enabled — the way
    aux_claudebridge.claude_escalation_enabled() reads it since the 2026-09-10
    audit (A02): OFF unless the value is an explicit boolean `true`, and OFF
    when settings.json exists but cannot be parsed.  This copy used to default
    to True, so on the CLI a fresh install read "escalation on" while the
    dashboard had it off (2026-10-21 audit C31)."""
    fn = _host("claude_escalation_enabled")
    if fn:
        try:
            return bool(fn())
        except Exception:
            return False
    st, s = _json_state(SETTINGS_FILE)
    if st != "ok" or not isinstance(s, dict):
        return False
    blk = s.get("claude_escalation")
    return isinstance(blk, dict) and blk.get("enabled") is True


def _apple_apps_state():
    """settings.json apple_apps.{reminders,notes}, default both False — fails
    closed the way apple_apps_enabled() does (on = the app launches on refresh)."""
    fn = _host("apple_apps_enabled")
    if fn:
        try:
            return bool(fn("reminders")), bool(fn("notes"))
        except Exception:
            pass
    s = _read_json(SETTINGS_FILE, {}) or {}
    cfg = s.get("apple_apps")
    if isinstance(cfg, dict):
        return bool(cfg.get("reminders")), bool(cfg.get("notes"))
    return False, False


def _google_state():
    """aux_google's own status verdict, as a dict — or a token-file-only
    stand-in when the dashboard cannot be asked.

    Whether the last token REFRESH worked (2026-10-21 audit C21) lives only in
    the running dashboard's memory, not on disk, so a file read cannot see a
    dead grant.  Inside the dashboard this calls `_goog_status_compute()`
    itself; on the CLI it asks GET /api/google/status, which is a file read on
    the far side too (that route never calls Google).  With the dashboard down
    a token file reads as `connected: None` — present, refresh state unknown."""
    if HOST:
        fn = _host("_goog_status_compute")
        if fn:
            try:
                st = fn()
                if isinstance(st, dict):
                    return st
            except Exception:
                pass
    else:
        code, body = _http(DASH_BASE + "/api/google/status", timeout=3)
        if code == 200:
            try:
                st = json.loads(body)
            except Exception:
                st = None
            if isinstance(st, dict):
                return st
    if not os.path.isfile(GOOG_TOKEN):
        return {"connected": False, "reason": "not_connected"}
    return {"connected": None, "reason": ""}


def _yaml_sane(path):
    """(ok, note) for a YAML file WITHOUT PyYAML — which this Mac's dashboard
    python does not have.  Uses yaml when it is importable and otherwise does a
    structural lint that catches the failure that actually happens by hand: a
    tab indent, or a top-level line that is neither a comment, a document
    marker, a list item nor `key:`."""
    try:
        with open(path) as f:
            text = f.read()
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    if not text.strip():
        return False, "empty"
    try:
        import yaml  # noqa: F401  (optional; absent on the dashboard python)
        try:
            yaml.safe_load(text)
            return True, "parses (PyYAML)"
        except Exception as e:
            return False, "YAML error: %s" % (str(e).splitlines() or [""])[0]
    except ImportError:
        pass
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("\t"):
            return False, "line %d indents with a TAB (YAML forbids it)" % i
        if not line or line[0] in " #":
            continue
        if line.startswith("---") or line.startswith("..."):
            continue
        if line.lstrip().startswith("- "):
            continue
        if not re.match(r'^[\w".\-/]+\s*:', line):
            return False, "line %d is not a top-level key: %s" % (i, line[:40])
    return True, "structure ok (PyYAML not installed)"


# --------------------------------------------------------------------------
# a read-only YAML SUBSET reader for ~/.hermes/config.yaml (feature F03)
#
# The drift check below has to read values, not just lint structure, and no
# python on this Mac has PyYAML.  It lives HERE, not in aux_doctor.py or
# server.py, because doctor is a standalone CLI that must work with the
# dashboard down.  What it understands is what `hermes config set` writes:
# block maps, block sequences (including `- key: value` map items), plain /
# single- / double-quoted scalars (multi-line ones too), `|`/`>` block
# scalars, one-level flow `[a, b]` / `{k: v}`, comments, and YAML 1.1 scalars
# the way PyYAML resolves them (`off` is False, `60` is an int).  Anchors,
# aliases, tags and complex keys are NOT understood: such a line is reported
# as a problem with its line number and left out, never guessed at — the check
# then says which lines it could not read instead of passing over them.
# --------------------------------------------------------------------------
# A plain key is what YAML allows a plain scalar to be: no leading indicator
# (`-`, `?`, `:` only when a non-space follows), and a `:` inside it only when
# a non-space follows — so `- /bin/x: y` is a mapping and `- http://h/p` is not.
_HC_KEY_RE = re.compile(
    r"""^(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'"""
    r"""|(?:[^\s\-?:,\[\]{}#&*!|>'"%@`]|[-?:](?=\S))(?:[^:]|:(?=\S))*?)"""
    r"""\s*:(?:[ \t]+(?P<rest>.*))?$""")
_HC_NULL = ("", "~", "null", "Null", "NULL")
_HC_BOOL = {"true": True, "yes": True, "on": True,
            "false": False, "no": False, "off": False}
# PyYAML's own YAML 1.1 implicit resolvers (resolver.py), so `0755` is 493 and
# `1e5` stays a string exactly as Hermes will read them.
_HC_INT_RE = re.compile(r"^[-+]?(?:0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|0|[1-9][0-9_]*"
                        r"|[1-9][0-9_]*(?::[0-5]?[0-9])+)$")
_HC_FLOAT_RE = re.compile(r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?"
                          r"|\.[0-9_]+(?:[eE][-+][0-9]+)?"
                          r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*"
                          r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_HC_DQ_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\",
              "/": "/", " ": " ", "e": "\x1b", "b": "\b", "a": "\a", "v": "\v",
              "f": "\f", "\t": "\t", "N": "\x85", "_": "\xa0", "L": "\u2028",
              "P": "\u2029"}
_HC_DQ_HEX = {"x": 2, "u": 4, "U": 8}          # PyYAML writes \x01, \u2028, \U0001F600


def _hc_quote_end(s):
    """Index of the quote closing the quoted scalar that starts `s`, or None."""
    q, i = s[0], 1
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\":
            i += 2
            continue
        if c == q:
            if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2                      # '' is an escaped quote
                continue
            return i
        i += 1
    return None


def _hc_unquote(s):
    if s[0] == "'":
        return s[1:-1].replace("''", "'")
    out, i, body = [], 0, s[1:-1]
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            e, n = body[i + 1], _HC_DQ_HEX.get(body[i + 1], 0)
            h = body[i + 2:i + 2 + n]
            if n and len(h) == n and all(x in "0123456789abcdefABCDEF" for x in h) \
                    and int(h, 16) <= 0x10FFFF:
                out.append(chr(int(h, 16)))
                i += 2 + n
                continue
            out.append(_HC_DQ_ESC.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _hc_key(k):
    """A mapping key the way PyYAML builds it: a quoted key verbatim, a plain
    one resolved like any plain scalar (`8080:` is an int, `on:` is True)."""
    if k[0] in "\"'":
        return _hc_unquote(k)
    return _hc_plain(k.strip())


def _hc_split_flow(inner):
    """Split the inside of a one-level flow collection on top-level commas."""
    parts, cur, q, i = [], [], None, 0
    while i < len(inner):
        c = inner[i]
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in "\"'":
            q = c
            cur.append(c)
        elif c == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _hc_plain(s):
    """A plain scalar resolved the way PyYAML (YAML 1.1) resolves it."""
    if s in _HC_NULL:
        return None
    low = s.lower()
    if low in _HC_BOOL and s in (low, low.capitalize(), low.upper()):
        return _HC_BOOL[low]
    if _HC_INT_RE.match(s) or _HC_FLOAT_RE.match(s):
        try:
            return _hc_number(s)
        except ValueError:
            return s
    return s


def _hc_number(s):
    """PyYAML's construct_yaml_int / construct_yaml_float for a matched scalar."""
    v = s.replace("_", "")
    sign = -1 if v[:1] == "-" else 1
    v = v.lstrip("+-")
    low = v.lower()
    if low in (".inf", ".nan"):
        return sign * float("inf") if low == ".inf" else float("nan")
    if ":" in v:                                   # base 60
        whole, _, frac = v.rpartition(":")
        n = 0
        for part in whole.split(":") + [frac.split(".")[0]]:
            n = n * 60 + int(part)
        if "." in frac:
            return sign * (n + float("0." + frac.split(".", 1)[1] or "0"))
        return sign * n
    if "." in v or "e" in low:
        return sign * float(v)
    if low.startswith("0b"):
        return sign * int(v[2:], 2)
    if low.startswith("0x"):
        return sign * int(v[2:], 16)
    if len(v) > 1 and v[0] == "0":
        return sign * int(v, 8)
    return sign * int(v)


class _HcParser:
    def __init__(self, raw):
        self.raw = raw
        self.toks = []                       # [lineno, indent, body]
        self.problems = []                   # [(lineno, why)]
        self.i = 0
        self.skip_to = 0                     # lines <= this were eaten by a block scalar
        for n, line in enumerate(raw, 1):
            s = line.rstrip()
            body = s.lstrip(" ")
            if not body or body.startswith("#"):
                continue
            if body.startswith("\t"):
                self.problems.append((n, "indented with a TAB"))
                continue
            ind = len(s) - len(body)
            if ind == 0 and (body in ("---", "...") or body.startswith("%")):
                continue
            self.toks.append([n, ind, body])

    # -- token stream ---------------------------------------------------------
    def peek(self):
        while self.i < len(self.toks) and self.toks[self.i][0] <= self.skip_to:
            self.i += 1
        return self.toks[self.i] if self.i < len(self.toks) else None

    def bad(self, lineno, why):
        self.problems.append((lineno, why))

    def drop_deeper(self, ind):
        """Skip every token nested under a line we could not read."""
        while True:
            t = self.peek()
            if t is None or t[1] <= ind:
                return
            self.i += 1

    @staticmethod
    def is_item(body):
        return body == "-" or body.startswith("- ")

    # -- grammar --------------------------------------------------------------
    def document(self):
        tree = {}
        while True:
            t = self.peek()
            if t is None:
                return tree
            if self.is_item(t[2]):
                self.bad(t[0], "a top-level list, not a mapping")
                self.i += 1
                self.drop_deeper(t[1])
                continue
            got = self.mapping(t[1])
            tree.update(got)
            t2 = self.peek()
            if t2 is not None and t2 is t:   # nothing consumed: never spin
                self.bad(t[0], "could not be read")
                self.i += 1

    def block(self, ind):
        t = self.peek()
        if t is not None and self.is_item(t[2]):
            return self.sequence(ind)
        return self.mapping(ind)

    def mapping(self, ind, first=None):
        d = {}
        if first is not None:
            self.pair(d, first[0], ind, first[1])
        while True:
            t = self.peek()
            if t is None or t[1] < ind:
                return d
            if t[1] > ind:
                self.bad(t[0], "unexpected indentation")
                self.i += 1
                self.drop_deeper(t[1] - 1)
                continue
            if self.is_item(t[2]):
                return d                    # a list item here belongs to the caller
            self.i += 1
            self.pair(d, t[0], ind, t[2])

    def pair(self, d, lineno, ind, body):
        m = _HC_KEY_RE.match(body)
        if not m:
            self.bad(lineno, "not a 'key: value' line")
            self.drop_deeper(ind)
            return
        d[_hc_key(m.group("key"))] = self.value(lineno, ind, m.group("rest") or "", seq_ok=True)

    def sequence(self, ind):
        out = []
        while True:
            t = self.peek()
            if t is None or t[1] < ind:
                return out
            if t[1] > ind:
                self.bad(t[0], "unexpected indentation")
                self.i += 1
                self.drop_deeper(t[1] - 1)
                continue
            if not self.is_item(t[2]):
                return out
            self.i += 1
            rest = t[2][1:]
            item = rest.lstrip(" ")
            cind = ind + 1 + (len(rest) - len(item))
            if not item or item.startswith("#"):
                nxt = self.peek()
                out.append(self.block(nxt[1]) if (nxt is not None and nxt[1] > ind) else None)
            elif self.is_item(item):
                self.bad(t[0], "a nested '- -' list is not supported")
                self.drop_deeper(ind)
                out.append(None)
            elif _HC_KEY_RE.match(item):
                out.append(self.mapping(cind, first=(t[0], item)))
            else:
                out.append(self.value(t[0], ind, item, seq_ok=False))

    def value(self, lineno, ind, rest, seq_ok):
        r = rest.strip()
        if r[:1] in ("&", "*", "!"):
            self.bad(lineno, "an anchor, alias or tag (not supported)")
            self.drop_deeper(ind)
            return None
        bs = re.match(r"^([|>])([-+0-9]*)\s*(#.*)?$", r)
        if bs:
            return self.block_scalar(lineno, ind, bs.group(1), bs.group(2))
        if not r or r.startswith("#"):
            t = self.peek()
            if t is not None and t[1] > ind:
                return self.block(t[1])
            if seq_ok and t is not None and t[1] == ind and self.is_item(t[2]):
                return self.sequence(ind)
            return None
        if r[:1] in "\"'" and _hc_quote_end(r) is None:
            return self.quoted_lines(lineno, ind, r)
        parts, last = [r], lineno
        while True:                          # a multi-line scalar's continuation
            t = self.peek()
            if t is None or t[1] <= ind:
                break
            gap = self.raw[last:t[0] - 1]    # the raw lines between the two
            if any(x.strip() for x in gap):  # a comment ends a plain scalar
                break
            parts.append(("\n" * len(gap) if gap else " ") + t[2])
            last = t[0]
            self.i += 1
        return self.scalar("".join(parts), lineno)

    def quoted_lines(self, lineno, ind, text):
        """A quoted scalar that does not close on its own line.  Its later
        lines are CONTENT whatever they start with — a `# heading` inside
        quotes is not a comment — so they are read raw, folded the way YAML
        folds a flow scalar (a break is a space, each blank line a newline, a
        trailing backslash in "..." joins with nothing), until it closes."""
        dq, blank, j = text[0] == '"', 0, lineno     # raw[lineno] is the NEXT line
        while j < len(self.raw):
            ln = self.raw[j]
            body = ln.strip(" \t")
            if not body:
                blank, j = blank + 1, j + 1
                continue
            if len(ln) - len(ln.lstrip(" ")) <= ind:
                break                                # dedented: it never closed
            if dq and (len(text) - len(text.rstrip("\\"))) % 2:
                text = text[:-1] + "\n" * blank + body
            else:
                text += ("\n" * blank if blank else " ") + body
            blank, j = 0, j + 1
            if _hc_quote_end(text) is not None:
                break
        self.skip_to = max(self.skip_to, j)
        self.problems = [p for p in self.problems if not lineno < p[0] <= j]
        return self.scalar(text, lineno)

    def block_scalar(self, lineno, ind, style, flags):
        lines, j = [], lineno               # raw[lineno] is the NEXT line
        while j < len(self.raw):
            ln = self.raw[j]
            if ln.strip() and (len(ln) - len(ln.lstrip(" "))) <= ind:
                break
            lines.append(ln.rstrip())
            j += 1
        self.skip_to = max(self.skip_to, j)
        # a TAB inside a block scalar is content, not bad indentation
        self.problems = [p for p in self.problems if not lineno < p[0] <= j]
        trail = 0
        while lines and not lines[-1].strip():
            lines.pop()
            trail += 1
        body = [x for x in lines if x.strip()]
        cut = min((len(x) - len(x.lstrip(" ")) for x in body), default=0)
        lines = [x[cut:] for x in lines]
        if style == "|":
            text = "\n".join(lines)
        else:                               # folded: one break -> a space,
            text, blank = "", 0             # each blank line -> a newline
            for x in lines:
                if not x.strip():
                    blank += 1
                    continue
                text += ("\n" * blank) if (blank or not text) else " "
                text += x
                blank = 0
        if not text or "-" in flags:        # chomping: strip / clip / keep
            return text
        return text + "\n" * (1 + (trail if "+" in flags else 0))

    def scalar(self, s, lineno):
        s = s.strip()
        if s[0] in "\"'":
            end = _hc_quote_end(s)
            if end is None:
                self.bad(lineno, "an unterminated quoted string")
                return s
            tail = s[end + 1:].strip()
            if tail and not tail.startswith("#"):
                self.bad(lineno, "text after a quoted value")
            return _hc_unquote(s[:end + 1])
        if s[0] in "[{":
            close = "]" if s[0] == "[" else "}"
            depth, q, end = 0, None, None
            for k, c in enumerate(s):
                if q:
                    if c == q:
                        q = None
                elif c in "\"'":
                    q = c
                elif c in "[{":
                    depth += 1
                elif c in "]}":
                    depth -= 1
                    if depth == 0:
                        end = k
                        break
            inner = s[1:end] if end is not None else None
            if inner is None or s[end] != close or any(c in inner for c in "[]{}"):
                self.bad(lineno, "a nested or unterminated flow collection")
                return None
            items = _hc_split_flow(inner)
            if close == "]":
                return [self.scalar(x, lineno) for x in items]
            d = {}
            for x in items:
                m = _HC_KEY_RE.match(x)
                if not m:
                    self.bad(lineno, "a flow mapping entry that is not 'key: value'")
                    continue
                d[_hc_key(m.group("key"))] = self.scalar(m.group("rest") or "~", lineno)
            return d
        if s[0] in "@`%":
            self.bad(lineno, "a reserved YAML indicator")
            return None
        if s[:2] in ("? ", ": ") or s in ("?", ":"):
            self.bad(lineno, "a complex '? key' (not supported)")
            return None
        m = re.search(r"[ \t]#", s)
        if m:
            s = s[:m.start()].rstrip()
        return _hc_plain(s)


def _hcfg_parse(text):
    """(tree, problems) for YAML text.  Pure, never raises: an unreadable line
    becomes a (lineno, why) problem and the rest of the file is still read."""
    try:
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if raw and raw[0].startswith("﻿"):
            raw[0] = raw[0][1:]
        p = _HcParser(raw)
        tree = p.document()
        return (tree if isinstance(tree, dict) else {}), sorted(p.problems)
    except Exception as e:                      # pragma: no cover — belt and braces
        return {}, [(0, "reader crashed — %s" % type(e).__name__)]


# --------------------------------------------------------------------------
# the drift rules (feature F03) — pure: a parsed config in, findings out
#
# Each rule pins a key whose upstream default has moved, or whose value would
# quietly break "inference and data stay on this Mac".  Facts are from the
# installed Hermes 0.18.0 (~/.hermes/hermes-agent); the 0.19-0.22 defaults are
# from the release notes quoted in docs/plans/feature-research-2026-10-21.md
# (F03).  Doctor never edits the file: every finding carries the command the
# OWNER can run.
# --------------------------------------------------------------------------
# tools/tts_tool.py BUILTIN_TTS_PROVIDERS, split by where the text goes.  An
# unset provider is DEFAULT_PROVIDER = "edge", and any name that is neither
# built in, a command provider nor a plugin falls through to Edge too.
_TTS_LOCAL = ("neutts", "kittentts", "piper")
_TTS_CLOUD = {"edge": "Microsoft Edge's cloud voices", "elevenlabs": "ElevenLabs",
              "openai": "OpenAI", "minimax": "MiniMax", "xai": "xAI",
              "mistral": "Mistral", "gemini": "Google Gemini"}
# toolsets.py: every `hermes-<platform>` composite is _HERMES_CORE_TOOLS (which
# carries computer_use and text_to_speech) except these three narrow ones.
_NARROW_COMPOSITES = ("hermes-acp", "hermes-api-server", "hermes-webhook")
# The surfaces with NO approval callback for computer_use (audit C111): only
# the interactive terminal CLI registers one, and it shares the `cli` key with
# `hermes serve` (the dashboard chat) and every `hermes -z` run.
_UNGATED_SURFACES = (("cli", "cli (hermes serve and -z)"), ("telegram", "telegram"))
APPROVAL_GATE_PLUGIN = "approval-gate"
_PLACEHOLDER_KEY = "local-anything"
_MAX_TURNS_018 = 90                      # 0.18's agent.max_turns default
_LOCAL_PROVIDERS = ("", "auto", "main", "custom")
# Local model servers by name: hermes_cli/auth.py resolves `lmstudio` (and its
# lm-studio / lm_studio aliases) to http://127.0.0.1:1234/v1 and routes
# `ollama` / `vllm` / `llamacpp` / `llama.cpp` / `llama-cpp` through `custom`.
# They stay on this Mac while the base_url next to them is unset or loopback;
# `ollama-cloud` is NOT one of them.
_LOCAL_SERVER_PROVIDERS = ("lmstudio", "lm-studio", "lm_studio", "ollama", "vllm",
                           "llamacpp", "llama.cpp", "llama-cpp")


def _cfg_at(tree, *path):
    cur = tree
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _cfg_truthy(v, default):
    """Hermes' `_parse_boolish` (tools/mcp_tool.py)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return default


def _toolset_on(cfg, platform, toolset):
    """The toolset entry that enables `toolset` on `platform`, or "".

    Mirrors hermes_cli/tools_config.py `_get_platform_tools`: a platform whose
    `platform_toolsets` entry is missing or not a list gets `hermes-<platform>`;
    a listed toolset name enables itself; `all` / `*` and a core composite
    enable everything in `_HERMES_CORE_TOOLS`."""
    pt = cfg.get("platform_toolsets") if isinstance(cfg, dict) else None
    names = pt.get(platform) if isinstance(pt, dict) else None
    if not isinstance(names, list):
        names = ["hermes-%s" % platform]
    names = [str(n) for n in names]
    if toolset in names:
        return toolset
    for n in names:
        if n in ("all", "*"):                # resolve_toolset's every-tool alias
            return n
    for n in names:
        if n.startswith("hermes-") and n not in _NARROW_COMPOSITES:
            return n
    return ""


def _host_of(url):
    try:
        from urllib.parse import urlsplit
        return (urlsplit(str(url).strip()).hostname or "").lower()
    except Exception:
        return ""


def _is_loopback(url):
    h = _host_of(url)
    return h in ("localhost", "::1") or h.startswith("127.") or h.endswith(".localhost")


def _walk_base_urls(node, path=""):
    """Every non-empty `base_url` in the tree, as (dotted.path, value)."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = ("%s.%s" % (path, k)) if path else str(k)
            if k == "base_url" and isinstance(v, str) and v.strip():
                yield p, v.strip()
            else:
                yield from _walk_base_urls(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_base_urls(v, "%s[%d]" % (path, i))


def _drift_findings(cfg, plugins_dir=None):
    """[(status, rule, detail, fix)] for a parsed ~/.hermes/config.yaml.

    Pure apart from one stat (is the approval-gate plugin on disk?).  Values
    that could be secrets are never echoed: an api_key is only ever named when
    it IS the public placeholder, a URL is reduced to its host, a command to
    its basename."""
    cfg = cfg if isinstance(cfg, dict) else {}
    plugins_dir = HERMES_PLUGINS if plugins_dir is None else plugins_dir
    out = []

    # 1. approvals.mode — the human gate.  0.18 defaults to manual; 0.19 made
    #    LLM-reviewed "smart" the default, so an UNPINNED mode changes meaning
    #    on upgrade.  tools/approval.py normalises a bool (YAML `off`) to "off".
    mode = _cfg_at(cfg, "approvals", "mode")
    fix_mode = "hermes config set approvals.mode manual"
    if mode is None or (isinstance(mode, str) and not mode.strip()):
        out.append((WARN, "approvals", "approvals.mode is not pinned — 0.18 reads that as "
                    "manual, 0.19+ as smart (an LLM approves commands for you)", fix_mode))
    else:
        norm = ("off" if mode is False else "manual") if isinstance(mode, bool) \
            else str(mode).strip().lower()
        if norm == "manual":
            out.append((PASS, "approvals", "approvals manual", ""))
        elif norm == "off":
            out.append((FAIL, "approvals", "approvals.mode is off — every command runs "
                        "with no approval", fix_mode))
        elif norm == "smart":
            out.append((WARN, "approvals", "approvals.mode is smart — an LLM approves "
                        "commands instead of you", fix_mode))
        else:
            out.append((WARN, "approvals", "approvals.mode %r is not a mode Hermes knows "
                        "(it falls back to manual with a warning)" % _sample(mode, 30, 30),
                        fix_mode))
    # _get_cron_approval_mode(): these strings mean approve; anything else,
    # including an unquoted YAML `off` (which is False), means deny.
    cron = _cfg_at(cfg, "approvals", "cron_mode")
    if isinstance(cron, str) and cron.strip().lower() in ("approve", "off", "allow", "yes"):
        out.append((WARN, "approvals", "approvals.cron_mode is %s — scheduled jobs "
                    "auto-approve dangerous commands" % _sample(cron, 20, 20),
                    "hermes config set approvals.cron_mode deny"))

    # 2. tts.provider — the core text_to_speech tool's destination.
    tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    prov = str(tts.get("provider") or "").strip().lower()
    where = [label for plat, label in _UNGATED_SURFACES if _toolset_on(cfg, plat, "tts")]
    reach = (" — the tool is on for " + " and ".join(where)) if where else \
        " — the tool is not on for cli or telegram today"
    fix_tts = ("hermes config set tts.provider piper  (local synthesis; until piper-tts "
               "is in the agent venv the tool errors instead of reaching the cloud, and its "
               "first use downloads one voice)" +
               "".join(" · or hermes tools disable tts --platform %s" % plat
                       for plat, _ in _UNGATED_SURFACES if _toolset_on(cfg, plat, "tts")))
    if not prov:
        out.append((WARN, "tts", "tts.provider is unset, so text_to_speech goes to "
                    "Microsoft Edge's cloud voices (the 0.18 default)" + reach, fix_tts))
    elif prov in _TTS_LOCAL:
        out.append((PASS, "tts", "tts %s (local)" % prov, ""))
    elif prov in _TTS_CLOUD:
        out.append((WARN, "tts", "tts.provider %s sends spoken text to %s" %
                    (prov, _TTS_CLOUD[prov]) + reach, fix_tts))
    else:
        named = _cfg_at(tts, "providers", prov)
        if not isinstance(named, dict):
            legacy = tts.get(prov)
            named = legacy if isinstance(legacy, dict) else None
        cmd = named.get("command") if isinstance(named, dict) else None
        ptype = str((named or {}).get("type") or "").strip().lower()
        if isinstance(cmd, str) and cmd.strip() and ptype in ("", "command"):
            out.append((PASS, "tts", "tts %s (a local command provider)" % _sample(prov, 30, 30), ""))
        else:
            out.append((WARN, "tts", "tts.provider %s is not built in and not a command "
                        "provider — Hermes falls back to Edge's cloud voices unless a plugin "
                        "registers it" % _sample(prov, 30, 30) + reach, fix_tts))

    # 3. computer_use on a surface with no approval callback (audit C111).
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    disabled = plugins.get("disabled") if isinstance(plugins.get("disabled"), list) else []
    gate = APPROVAL_GATE_PLUGIN in [str(x) for x in enabled] and \
        APPROVAL_GATE_PLUGIN not in [str(x) for x in disabled]
    cu = [(plat, label, _toolset_on(cfg, plat, "computer_use"))
          for plat, label in _UNGATED_SURFACES]
    cu = [(plat, label, via) for plat, label, via in cu if via]
    if not cu:
        out.append((PASS, "computer_use", "computer_use off for cli and telegram", ""))
    elif gate and os.path.isdir(os.path.join(plugins_dir, APPROVAL_GATE_PLUGIN)):
        out.append((PASS, "computer_use", "computer_use gated by the %s plugin" %
                    APPROVAL_GATE_PLUGIN, ""))
    elif gate:
        out.append((WARN, "computer_use", "plugins.enabled lists %s but %s is not on disk, "
                    "so computer_use runs unapproved on %s" % (
                        APPROVAL_GATE_PLUGIN,
                        os.path.join(plugins_dir, APPROVAL_GATE_PLUGIN),
                        " and ".join(label for _, label, _ in cu)),
                    "put the plugin back in %s, or take %s out of plugins.enabled" % (
                        os.path.join(plugins_dir, APPROVAL_GATE_PLUGIN), APPROVAL_GATE_PLUGIN)))
    else:
        via = ", ".join("%s via %s" % (label, v) for _, label, v in cu)
        out.append((WARN, "computer_use", "computer_use is on for %s, which register no "
                    "approval callback — it can type into any app and press Return with "
                    "no approval card" % via,
                    "enable the %s plugin, or " % APPROVAL_GATE_PLUGIN +
                    " and ".join("hermes tools disable computer_use --platform %s" % plat
                                 for plat, _, _ in cu) +
                    ("  (for cli, Settings › Agent & Models › Prompt budget › Focused "
                     "does the same)" if any(plat == "cli" for plat, _, _ in cu) else "")))

    # 4. the placeholder model key (audit C130: the model servers check no key).
    holders = []
    if _cfg_at(cfg, "model", "api_key") == _PLACEHOLDER_KEY:
        holders.append("model.api_key")
    for i, cp in enumerate(cfg.get("custom_providers") if isinstance(cfg.get("custom_providers"), list) else []):
        if isinstance(cp, dict) and cp.get("api_key") == _PLACEHOLDER_KEY:
            holders.append("custom_providers %s" % _sample(cp.get("name") or ("#%d" % i), 20, 20))
    if holders:
        out.append((WARN, "api_key", "%s use the placeholder key %r — the model servers "
                    "require no key, so any local process or page can drive them" %
                    (" and ".join(holders), _PLACEHOLDER_KEY),
                    "give the model servers a key of their own (audit C130), then set the "
                    "same value here"))

    # 5. MCP servers: enabled defaults to true, and so does sampling (a server
    #    can then ask OUR model for completions — and wake it).
    mcp = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    on, off = [], 0
    for name, ent in mcp.items():
        if not isinstance(ent, dict):
            continue
        if not _cfg_truthy(ent.get("enabled"), True):
            off += 1
            continue
        url = ent.get("url")
        if isinstance(url, str) and url.strip():
            where_ = "remote %s" % (_host_of(url) or "?") if not _is_loopback(url) \
                else "loopback"
        else:
            where_ = "stdio %s" % os.path.basename(str(ent.get("command") or "?"))
        # MCPServerTask reads sampling with a plain truthiness test (not
        # _parse_boolish), so a quoted "false" leaves it ON.
        samp = ent.get("sampling") if isinstance(ent.get("sampling"), dict) else {}
        s_on = bool(samp.get("enabled", True))
        on.append("%s (%s%s)" % (_sample(name, 30, 30), where_,
                                 ", sampling on" if s_on else ""))
    if on:
        out.append((WARN, "mcp", "MCP servers enabled: %s" % ", ".join(on),
                    "set mcp_servers.<name>.enabled: false, or sampling.enabled: false "
                    "for one you keep"))
    else:
        out.append((PASS, "mcp", "no MCP servers enabled" +
                    (" (%d disabled)" % off if off else ""), ""))

    # 6. the 0.22 upgrade keys — only a value that is PRESENT and risky warns.
    #    cli.py / gateway/run.py test it with plain truthiness: a quoted
    #    "false" prunes.
    if _cfg_at(cfg, "sessions", "auto_prune"):
        try:
            days = int(_cfg_at(cfg, "sessions", "retention_days") or 90)
        except (TypeError, ValueError):
            days = 90
        out.append((WARN, "sessions", "sessions.auto_prune is on — ended sessions older "
                    "than %d days are deleted at startup (search and the Mind view lose them)"
                    % days, "hermes config set sessions.auto_prune false"))
    turns = _cfg_at(cfg, "agent", "max_turns")
    if turns is None:
        turns = cfg.get("max_turns")             # the legacy root key Hermes migrates
    if isinstance(turns, int) and not isinstance(turns, bool):
        if turns > _MAX_TURNS_018:
            out.append((WARN, "max_turns", "agent.max_turns is %d — above 0.18's default "
                        "of %d, so a looping agent runs that many tool rounds" %
                        (turns, _MAX_TURNS_018), "hermes config set agent.max_turns 60"))
        else:
            out.append((PASS, "max_turns", "max_turns %d" % turns, ""))
    else:
        out.append((PASS, "max_turns", "max_turns not pinned (0.18 default %d; 0.20+ "
                    "raised it to 500)" % _MAX_TURNS_018, ""))

    # 7. where inference goes: every base_url on loopback, no cloud provider.
    remote = [(p, _host_of(u) or "?") for p, u in _walk_base_urls(cfg) if not _is_loopback(u)]
    for p, h in remote:
        out.append((WARN, "base_url", "%s points at %s — off this Mac" % (p, h),
                    "point it at 127.0.0.1, or remove it"))
    provs = [("model.provider", _cfg_at(cfg, "model", "provider"),
              _cfg_at(cfg, "model", "base_url")),
             ("delegation.provider", _cfg_at(cfg, "delegation", "provider"),
              _cfg_at(cfg, "delegation", "base_url")),
             ("fallback_model.provider", _cfg_at(cfg, "fallback_model", "provider"),
              _cfg_at(cfg, "fallback_model", "base_url"))]
    aux = cfg.get("auxiliary") if isinstance(cfg.get("auxiliary"), dict) else {}
    provs += [("auxiliary.%s.provider" % k, v.get("provider"), v.get("base_url"))
              for k, v in aux.items() if isinstance(v, dict)]
    for p, v, burl in provs:
        if isinstance(v, str) and v.strip().lower() in _LOCAL_SERVER_PROVIDERS \
                and (not isinstance(burl, str) or not burl.strip() or _is_loopback(burl)):
            continue
        if isinstance(v, str) and v.strip().lower() not in _LOCAL_PROVIDERS \
                and not v.strip().lower().startswith("custom:"):
            out.append((WARN, "provider", "%s is %s — a hosted provider, not this Mac" %
                        (p, _sample(v, 30, 30)), "hermes config set %s custom" % p))
    fb = cfg.get("fallback_providers")
    if isinstance(fb, list) and fb:
        out.append((WARN, "provider", "fallback_providers lists %d provider(s) — a failed "
                    "local turn is retried there" % len(fb),
                    "remove fallback_providers from ~/.hermes/config.yaml"))
    if not remote:
        out.append((PASS, "base_url", "every base_url on loopback", ""))
    return out


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------
CHECKS = []


def check(cid, label):
    def deco(fn):
        CHECKS.append((cid, label, fn))
        return fn
    return deco


@check("dashboard", "Dashboard")
def _chk_dashboard():
    st, body = _http(DASH_BASE + "/api/health", timeout=4)
    if st != 200:
        return bad("no answer on 127.0.0.1:%d — %s" % (DASH_PORT, body or ("HTTP %s" % st)),
                   "launchctl kickstart -k gui/$(id -u)/%s" % LABEL_DASH)
    try:
        h = json.loads(body)
    except Exception:
        return warn("answered on 127.0.0.1:%d but /api/health was not JSON" % DASH_PORT,
                    "tail -n 50 %s" % DASH_LOG)
    return ok("reachable on 127.0.0.1:%d · hermes binary %s" %
              (DASH_PORT, "found" if h.get("hermes_found") else "MISSING"))


@check("services", "Services")
def _chk_services():
    st = {lb: _launchctl(lb) for lb in (LABEL_DASH, LABEL_SERVE, LABEL_MLX, LABEL_BG)}
    parts, status, fix = [], PASS, ""

    def word(lb, ondemand=False):
        s = st[lb]
        if not s["loaded"]:
            return "not loaded" + (" (on-demand)" if ondemand else "")
        if s["pid"]:
            return "pid %d" % s["pid"]
        return "loaded, idle"

    parts.append("dashboard " + word(LABEL_DASH))
    parts.append("serve " + word(LABEL_SERVE))
    parts.append("mlx-server " + word(LABEL_MLX, ondemand=True))
    parts.append("mlx-bg " + word(LABEL_BG, ondemand=True))

    missing = [lb for lb in (LABEL_DASH, LABEL_SERVE, LABEL_MLX, LABEL_BG)
               if not _plist_installed(lb)]
    if missing:
        parts.append("no plist for " + ", ".join(missing))
        status, fix = WARN, "./install-services.sh"
    if not st[LABEL_DASH]["loaded"]:
        # the hub is always-on (RunAtLoad + KeepAlive) — down is a real failure
        return bad(" · ".join(parts), "./install-services.sh")
    if not st[LABEL_SERVE]["loaded"]:
        # no one-shot fallback since 2026-10-21 (audit C36): -z auto-approves,
        # so with serve down the dashboard chat and Quick Ask refuse EVERY
        # turn — broken now, not degraded (it used to read as a WARN)
        return bad(" · ".join(parts) + " — dashboard chat runs nothing until serve is back",
                   "./install-services.sh")
    if _serve_web_ui_exposed():
        parts.append("serve still publishes its web UI, which hands its session token "
                     "to any local page that asks")
        status, fix = WARN, "./install-services.sh   (re-renders com.hermes.serve without it)"
    return {"status": status, "detail": " · ".join(parts), "fix": fix}


def _serve_web_ui_exposed():
    """True when com.hermes.serve's plist still lets `hermes serve` mount its
    web UI (2026-10-21 audit C117).  Hermes' mount_spa() answers an
    unauthenticated GET / with index.html carrying window.__HERMES_SESSION_TOKEN__,
    and its CORS / WebSocket origin rules admit every loopback port, so any
    page a local dev server hands the browser can read the token and drive the
    agent.  install-services.sh now points HERMES_WEB_DIST at a path that cannot
    exist, which makes mount_spa() serve a 404 instead.  Reads two keys of the
    plist and never prints any value from it (the plist embeds the token).
    None when there is no plist to read."""
    p = os.path.join(AGENTS_DIR, LABEL_SERVE + ".plist")
    try:
        import plistlib
        with open(p, "rb") as f:
            pl = plistlib.load(f)
    except Exception:
        return None
    env = pl.get("EnvironmentVariables") if isinstance(pl.get("EnvironmentVariables"), dict) else {}
    dist = env.get("HERMES_WEB_DIST")
    if not isinstance(dist, str) or not dist:
        return True
    # mount_spa() keys off the DIRECTORY existing, so that is the test.
    return os.path.exists(dist)


@check("model_server", "Model lanes")
def _chk_model_server():
    """A sleeping model is the design, never a failure — but a sleeping model
    with NOTHING that explains why is a crash, and saying PASS to that was the
    one thing this check could get wrong.  It probes; it does not wake.
    (`agent_wake()` is the only thing that may.)"""
    primary = _lane_up(MODEL_URL)
    bgup = _lane_up(BG_MODEL_URL)
    paused = os.path.exists(PAUSE_FILE)
    idle = os.path.exists(IDLE_SUSPEND_FILE)
    autostart_off = os.path.exists(AUTOSTART_OFF)
    bits = ["primary " + ("up on :8080" if primary else "asleep")]
    if not primary:
        if paused:
            bits[-1] += " (paused)"
        elif idle:
            try:
                bits[-1] += " (idle-suspended %s)" % _fmt_when(
                    float(open(IDLE_SUSPEND_FILE).read().strip() or 0))
            except Exception:
                bits[-1] += " (idle-suspended)"
    bits.append("background " + ("up on :8081" if bgup else "down"))
    if autostart_off:
        bits.append("autostart off (on-demand)")
    detail = " · ".join(bits) + " — probe only, nothing was started"
    if not primary and not paused and not idle and not autostart_off:
        # No pause marker, no idle-suspend marker and no on-demand gate: this
        # lane is meant to be running and is not.  The three explained shapes
        # all leave one of those files behind, so this combination is a crash,
        # an external bootout or a start that never came up.
        return warn(detail + " — nothing explains the down primary "
                    "(no pause, no idle-suspend marker, autostart on)",
                    "tail -n 50 %s" % MLX_LOG)
    return ok(detail)


@check("hermes_agent", "Hermes Agent")
def _chk_hermes():
    b = _hermes_bin()
    if not b or not os.path.exists(b):
        return bad("not found at %s" % b,
                   "install it, then re-run ./install-services.sh")
    if not os.access(b, os.X_OK):
        return bad("%s is not executable" % b, "chmod +x %s" % b)
    argv = [b, "--version"]
    rc, out = _run(argv, timeout=15)
    line = (out.strip().splitlines() or [""])[0].strip()
    if rc != 0:
        # A broken install prints a traceback and exits non-zero; the old check
        # read only stdout-or-stderr and PASSed with the traceback as detail.
        return bad("%s  (%s)" % (_cmd_note(argv, rc, line), b),
                   "%s --version   # then reinstall the agent" % b)
    if not line:
        return warn("%s ran but printed no version" % b, "%s --version" % b)
    return ok("%s  (%s)" % (line, b))


@check("mlx_vlm_venv", "mlx-vlm venv")
def _chk_mlx_vlm():
    if not os.path.exists(MLX_VLM_VENV_PY):
        return warn("absent — mlx_vlm roster entries silently fall back to mlx-lm "
                    "(no MTP speculative decoding, no APC prefix cache)",
                    "./install-mlx-vlm-venv.sh")
    # read the version off the dist-info rather than spawning the interpreter:
    # same answer, no import of mlx/metal.
    ver = ""
    for d in _glob.glob(os.path.join(MLX_VLM_VENV, "lib", "python*",
                                     "site-packages", "mlx_vlm-*.dist-info")):
        m = re.search(r"mlx_vlm-([0-9][^/]*?)\.dist-info$", d)
        if m:
            ver = m.group(1)
            break
    if not ver:
        return warn("venv exists at %s but mlx-vlm is not installed in it" % MLX_VLM_VENV,
                    "./install-mlx-vlm-venv.sh")
    if ver != MLX_VLM_PIN:
        return warn("mlx-vlm %s — the pin is %s.  0.6.15-0.6.17 CORRUPT OUTPUT when two "
                    "requests overlap with the MTP drafter loaded (CLAUDE.md)" %
                    (ver, MLX_VLM_PIN),
                    "MLX_VLM_VERSION=%s ./install-mlx-vlm-venv.sh" % MLX_VLM_PIN)
    return ok("mlx-vlm %s, pinned (%s)" % (ver, MLX_VLM_VENV))


@check("hf_python", "Downloads")
def _chk_hf_python():
    """The dashboard's Homebrew python has no huggingface_hub, so model
    downloads from the menu used to fail silently — server.py's `_hf_python()`
    is the fix and this is the check that it still resolves."""
    fn = _host("_hf_python")
    if fn:
        try:
            py = fn()
        except Exception:
            py = None
    else:
        py = None
        for c in (MLX_VLM_VENV_PY,
                  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
                  shutil.which("python3"), sys.executable):
            if not c or not os.path.exists(c):
                continue
            try:
                if subprocess.run([c, "-c", "import huggingface_hub"],
                                  capture_output=True, timeout=25,
                                  stdin=subprocess.DEVNULL).returncode == 0:
                    py = c
                    break
            except Exception:
                continue
    if not py:
        return warn("no interpreter on this Mac can `import huggingface_hub` — "
                    "model downloads from the menu will fail",
                    "./install-mlx-vlm-venv.sh  (or: pip3 install huggingface_hub)")
    return ok("huggingface_hub available via %s" % py)


@check("models", "Model roster")
def _chk_models():
    reg = _read_json(MODELS_FILE, None)
    if not isinstance(reg, dict) or not isinstance(reg.get("models"), list):
        return warn("models.json absent or unreadable — the dashboard seeds it on start",
                    "launchctl kickstart -k gui/$(id -u)/%s" % LABEL_DASH)
    models = [m for m in reg["models"] if isinstance(m, dict)]
    try:
        active = (open(ACTIVE_MODEL_FILE).read().strip() or DEFAULT_MODEL)
    except OSError:
        active = DEFAULT_MODEL
    ram = _machine_ram_gb()
    status, fix, parts = PASS, "", []
    for m in models:
        mid = m.get("id") or "?"
        bits = []
        snap = _hf_snapshot_dir(mid)
        complete = _snapshot_complete(snap, _subfolder(m.get("model_subfolder")))
        if complete:
            bits.append("complete")
        elif snap:
            bits.append("PARTIAL")
        else:
            bits.append("not downloaded")
        ds = _draft_state(m)
        if ds:
            bits.append("drafter " + ("ready" if ds == "ready" else "MISSING"))
        fit = _model_fit(m.get("ram"), ram)
        if fit:
            bits.append("fit " + fit)
        mark = "* " if mid == active else ""
        parts.append("%s%s: %s" % (mark, _short(mid), ", ".join(bits)))
        if mid == active:
            if not complete:
                status = FAIL
                fix = "pick another model in the model menu, or re-download this one"
            elif fit == "no":
                status = WARN if status != FAIL else status
                fix = fix or ("needs ~%s of RAM, this Mac has %s — switch to a smaller model"
                              % (_gb(m.get("ram")), _gb(ram)))
            elif ds == "missing" and status == PASS:
                status = WARN
                fix = "re-download it from the model menu to restore speculative decoding"
        elif snap and not complete and status == PASS:
            status = WARN
            fix = "a partial download is on disk — re-run it from the model menu"
    head = "%d in roster, active marked * · this Mac has %s" % (len(models), _gb(ram))
    return {"status": status, "detail": head + " · " + " · ".join(parts), "fix": fix}


@check("disk", "Disk")
def _chk_disk():
    free = _disk_free_gb()
    if free is None:
        return warn("could not read free space on the volume holding ~",
                    "df -h ~")
    if free < DISK_FAIL_GB:
        return bad("%s free — below the %s floor; downloads are refused and "
                   "sqlite writes are at risk" % (_gb(free), _gb(DISK_FAIL_GB)),
                   "free space, or move the HF cache off this volume")
    if free < DISK_WARN_GB:
        return warn("%s free — a model download needs 17-19 GB plus 5 GB headroom"
                    % _gb(free), "free space before the next model download")
    return ok("%s free" % _gb(free))


@check("hardware", "Hardware")
def _chk_hardware():
    ram = _machine_ram_gb()
    notes = []
    argv = ["/usr/bin/sw_vers", "-productVersion"]
    rc, out = _run(argv, timeout=4)
    macos = (out.strip().splitlines() or [""])[0] if rc == 0 else ""
    if rc != 0:
        notes.append(_cmd_note(argv, rc, out))
    argv = ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]
    rc, out = _run(argv, timeout=4)
    chip = (out.strip().splitlines() or [""])[0] if rc == 0 else ""
    if rc != 0:
        notes.append(_cmd_note(argv, rc, out))
    chip = chip or platform.processor() or "?"
    arm = platform.machine() == "arm64"
    detail = "%s · %s RAM · macOS %s" % (chip, _gb(ram) if ram else "?", macos or "?")
    if not arm:
        return bad(detail + " · NOT Apple Silicon — MLX needs arm64",
                   "Hermes Assistant requires an Apple Silicon Mac")
    if ram and ram < 16:
        return warn(detail + " · under 16 GB, only the smallest model is usable",
                    "Settings > Agent & Models — pick the 2B model")
    if notes:
        # An unreadable machine is not a healthy machine: the numbers this
        # report is built on came from somewhere else, or nowhere.
        return warn(detail + " · Apple Silicon · " + " · ".join(notes),
                    "run the command by hand to see why it failed")
    return ok(detail + " · Apple Silicon")


@check("fda", "Full Disk Access")
def _chk_fda():
    v = _fda()
    if v is True:
        return ok("granted — the app can read the Messages database")
    if v is False:
        return warn("denied — the Message Center and the message index stay empty",
                    "System Settings > Privacy & Security > Full Disk Access "
                    "> add Hermes Assistant.app")
    return warn("unknown — the app has not reported yet (it is the only thing that "
                "can hold FDA; a launchd python never can)",
                "open Hermes Assistant.app and let one message sync run")


# --------------------------------------------------------------------------
# dictation — the push-to-talk helper.  Same shape as the FDA check above and
# for the same reason: the grants belong to a process this one is not.  The
# helper (app/dictation) POSTs its own verdict to /api/dictation/status and the
# dashboard stores it; doctor reads that store, never a microphone.
# --------------------------------------------------------------------------

DICTATION_STORE = os.path.join(DATA, "dictation.json")
DICTATION_BUNDLE = os.path.join(ROOT, "app", "build", "Hermes Dictation.app")
DICTATION_STALE_S = 90


def _dictation():
    """(built, running, status) — status is the helper's last heartbeat, {} when
    it has never sent one.  `running` is its claim ANDed with a fresh heartbeat:
    a killed process never gets to retract its own."""
    built = os.path.isdir(DICTATION_BUNDLE)
    st = _read_json(DICTATION_STORE, None)
    status = st.get("status") if isinstance(st, dict) else None
    status = status if isinstance(status, dict) else {}
    age = _ago(status.get("ts")) if status.get("ts") else None
    running = bool(status.get("running")) and age is not None and age <= DICTATION_STALE_S
    return built, running, status, age


@check("dictation", "Dictation")
def _chk_dictation():
    built, running, status, age = _dictation()
    if not built and status.get("ts"):
        # It ran here before (it sent a heartbeat), so the bundle was set up and
        # is now gone — a main-app rebuild used to delete it (2026-11-09 audit
        # E126). That is not the same as "never installed".
        return warn("the helper is missing although dictation was set up here "
                    "(last heartbeat %s) — the bundle in app/build is gone"
                    % _fmt_when(status.get("ts")),
                    "bash app/build-dictation.sh, then launch it once (and re-grant "
                    "Microphone and Accessibility)")
    if not built:
        # Informational, not a warning: dictation is opt-in and a Mac without it
        # is not an unhealthy Mac.
        return ok("not installed (optional) — build the helper with "
                  "app/build-dictation.sh, then launch it once")
    if not running:
        # Absolute 12-hour clock, per the repo's design law — never "x min ago".
        when = (" · last heartbeat %s" % _fmt_when(status.get("ts"))) if age is not None \
            else " · it has never checked in"
        return warn("helper built but not running" + when,
                    'open "app/build/Hermes Dictation.app" — it must be launched '
                    "by hand or by Start at login, never by launchd")
    bits = []
    if status.get("engine"):
        bits.append(str(status["engine"]))
    if status.get("hotkey"):
        bits.append("hold " + str(status["hotkey"]))
    grants = {k: str(status.get(k) or "unknown")
              for k in ("mic", "accessibility", "speech")}
    label = {"mic": "microphone", "accessibility": "accessibility", "speech": "speech model"}
    missing = [label[k] for k, v in grants.items() if v != "granted"]
    detail = " · ".join(bits + ["%s %s" % (label[k], grants[k]) for k in
                                ("mic", "accessibility", "speech")])
    if missing:
        return warn("running, but " + ", ".join(missing) + " not granted · " + detail,
                    "System Settings > Privacy & Security — add Hermes "
                    "Dictation.app under Microphone and Accessibility "
                    "(a rebuild resets both: it is ad-hoc signed)")
    return ok("running · " + detail)


@check("config", "Config")
def _chk_config():
    bits, worst, fix = [], PASS, ""
    r_ok, r_note = _yaml_sane(REPO_CONFIG) if os.path.exists(REPO_CONFIG) else (False, "missing")
    bits.append("config.yaml " + ("ok" if r_ok else "BAD (%s)" % r_note))
    if not r_ok:
        worst, fix = FAIL, "fix %s — %s" % (REPO_CONFIG, r_note)
    # Absent and corrupt had the same answer here, because `_read_json` returns
    # the default for BOTH — so a fresh install, where the file legitimately
    # does not exist yet, FAILed the whole run and exited 1.
    st, _s = _json_state(SETTINGS_FILE)
    if st == "absent":
        bits.append("settings.json absent (defaults)")
    elif st == "bad":
        bits.append("settings.json UNREADABLE")
        worst = FAIL
        fix = fix or ("repair %s by hand (if it does not parse, the "
                      "dashboard's next settings write moves it aside as "
                      "settings.json.corrupt-<ts> and starts from defaults; if "
                      "it cannot be read, settings writes fail until it can)"
                      % SETTINGS_FILE)
    else:
        bits.append("settings.json ok")
    # server.py moves a store it could not parse aside as <name>.corrupt-<ts>
    # instead of writing defaults over it (2026-10-21 audit C27). The dashboard
    # then runs on defaults for that store, so the owner has to be told.
    moved = sorted(os.path.relpath(p, DATA) for pat in ("*.corrupt-*",
                   os.path.join("chats", "*.corrupt-*"))
                   for p in _glob.glob(os.path.join(DATA, pat)))
    if moved:
        bits.append("%d store file(s) moved aside as unparsable: %s"
                    % (len(moved), ", ".join(moved[:3])
                       + (" …" if len(moved) > 3 else "")))
        if worst == PASS:
            worst = WARN
        fix = fix or ("merge what you need from %s back into the live file "
                      "by hand, then delete the .corrupt copy"
                      % os.path.join(DATA, moved[0]))
    # ~/.hermes being 0700 is what keeps the legacy 0644 stores under it
    # (chats, settings, tasks, briefing) from other accounts; write_json now
    # narrows a store to 0600 on its next write, but nothing else checks the
    # parent (2026-10-21 audit C110).
    try:
        hmode = os.stat(os.path.dirname(DATA)).st_mode & 0o777
    except OSError:
        hmode = None
    if hmode is not None and hmode & 0o077:
        bits.append("~/.hermes is %03o (other accounts can enter it)" % hmode)
        if worst == PASS:
            worst = WARN
        fix = fix or "chmod 700 ~/.hermes"
    if not os.path.exists(HERMES_CONFIG):
        bits.append("~/.hermes/config.yaml MISSING")
        if worst == PASS:
            worst = WARN
        fix = fix or "cp config.yaml ~/.hermes/config.yaml  (then `hermes config set ...`)"
    else:
        h_ok, h_note = _yaml_sane(HERMES_CONFIG)
        bits.append("~/.hermes/config.yaml " + ("ok" if h_ok else "BAD (%s)" % h_note))
        if not h_ok:
            worst = FAIL
            fix = fix or "fix ~/.hermes/config.yaml — %s" % h_note
    return {"status": worst, "detail": " · ".join(bits), "fix": fix}


@check("hermes_drift", "Hermes config drift")
def _chk_hermes_drift():
    """The agent's own config, held against the keys that keep it local and
    gated (feature F03).  Read-only: one file read and an in-process parse — no
    `hermes` subprocess, no network, no model.  It never edits the file; each
    finding names the command the owner can run."""
    missing = False
    try:
        with open(HERMES_CONFIG, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text, missing = "", True
    except (OSError, UnicodeDecodeError) as e:
        return warn("could not read ~/.hermes/config.yaml (%s) — nothing was checked"
                    % type(e).__name__, "ls -l %s" % HERMES_CONFIG)
    cfg, problems = _hcfg_parse(text)
    found = _drift_findings(cfg)
    rank = {PASS: 0, WARN: 1, FAIL: 2}
    issues = [f for f in found if f[0] != PASS]
    issues.sort(key=lambda f: -rank[f[0]])
    fine = [f[2] for f in found if f[0] == PASS]
    status = issues[0][0] if issues else PASS
    bits = []
    if missing:
        bits.append("no ~/.hermes/config.yaml, so Hermes' defaults apply")
    if problems:
        status = status if status == FAIL else WARN
        ln, why = problems[0]
        bits.append("could not read %d line(s) — line %d is %s; keys there were not checked"
                    % (len(problems), ln, why))
    bits += [f[2] for f in issues]
    if fine:
        bits.append(("fine: " if (issues or problems) else "") + ", ".join(fine))
    fixes = []
    for f in issues:
        if f[3] and f[3] not in fixes:
            fixes.append(f[3])
    if problems and not fixes:
        fixes.append("check those lines of %s by hand" % HERMES_CONFIG)
    return {"status": status, "detail": " · ".join(bits), "fix": " · ".join(fixes)}


# --------------------------------------------------------------------------
# dangerous-command list drift (permissions._DANGER_PATTERNS is a COPY of
# upstream tools/approval.py DANGEROUS_PATTERNS). Both files are read as
# SOURCE and walked as an AST: nothing is imported or executed, so a changed
# or hostile upstream file can only change the verdict.
# --------------------------------------------------------------------------
HERMES_APPROVAL_SRC = os.path.join(HOME, ".hermes", "hermes-agent", "tools", "approval.py")
PERMISSIONS_SRC = os.path.join(HERE, "permissions.py")
DANGER_MSG = ("Hermes' dangerous-command list changed; Trust 'Auto' may judge commands "
              "differently until Hermes Assistant is updated")


class _DpUnreadable(Exception):
    pass


def _dp_str(node, env):
    """A module-level string expression: literals, f-strings over names that
    hold strings (no conversion, no format spec), `+`. Anything else raises."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and isinstance(env.get(node.id), str):
        return env[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _dp_str(node.left, env) + _dp_str(node.right, env)
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                if v.conversion != -1 or v.format_spec is not None:
                    raise _DpUnreadable("formatted value")
                out.append(_dp_str(v.value, env))
            else:
                out.append(_dp_str(v, env))
        return "".join(out)
    raise _DpUnreadable(type(node).__name__)


def _dp_flags(node, env):
    """`re.X | re.Y` (or a name bound to it) -> a frozenset of flag names."""
    import ast
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "re":
        return frozenset([{"I": "IGNORECASE", "S": "DOTALL", "M": "MULTILINE",
                           "X": "VERBOSE", "A": "ASCII"}.get(node.attr, node.attr)])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _dp_flags(node.left, env) | _dp_flags(node.right, env)
    if isinstance(node, ast.Name) and isinstance(env.get(node.id), frozenset):
        return env[node.id]
    raise _DpUnreadable("flags")


def _dp_table(path, list_name, flags_name):
    """-> ([(regex, description), ...], flags frozenset) read from `path`'s
    source. Raises OSError / SyntaxError / _DpUnreadable."""
    import ast
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    strs, flags, table, compiled_flags = {}, {}, None, None
    for node in tree.body:
        # `NAME = value` and the annotated `NAME: type = value` alike: a type
        # hint added upstream changes no pattern and must not read as drift
        if isinstance(node, ast.AnnAssign) and node.value is not None \
                and isinstance(node.target, ast.Name):
            name = node.target.id
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name = node.targets[0].id
        else:
            continue
        if name == list_name:
            if not isinstance(node.value, (ast.List, ast.Tuple)):
                raise _DpUnreadable("%s is not a list" % list_name)
            table = []
            for el in node.value.elts:
                if not (isinstance(el, ast.Tuple) and len(el.elts) == 2):
                    raise _DpUnreadable("an entry is not a pair")
                table.append((_dp_str(el.elts[0], strs), _dp_str(el.elts[1], strs)))
            continue
        if name == list_name + "_COMPILED":
            # the flags the list is really compiled with
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "compile" \
                        and len(sub.args) >= 2:
                    compiled_flags = _dp_flags(sub.args[1], flags)
            continue
        try:
            strs[name] = _dp_str(node.value, strs)
            continue
        except _DpUnreadable:
            pass
        try:
            flags[name] = _dp_flags(node.value, flags)
        except _DpUnreadable:
            pass
    if table is None:
        raise _DpUnreadable("no %s" % list_name)
    fl = compiled_flags if compiled_flags is not None else flags.get(flags_name)
    if fl is None:
        raise _DpUnreadable("no flags")
    return table, fl


@check("danger_patterns", "Trust patterns")
def _chk_danger_patterns():
    """Hermes' installed DANGEROUS_PATTERNS vs the copy in permissions.py.
    Read-only: two file reads and an AST walk, no import, no network."""
    if not os.path.isfile(HERMES_APPROVAL_SRC):
        return ok("Hermes' source not found (%s) — nothing to compare"
                  % HERMES_APPROVAL_SRC.replace(HOME, "~"))
    try:
        mine, mine_fl = _dp_table(PERMISSIONS_SRC, "_DANGER_PATTERNS", "_DP_RE_FLAGS")
    except Exception as e:
        return warn("could not read the copy in permissions.py (%s)" % type(e).__name__,
                    "reinstall Hermes Assistant")
    try:
        up, up_fl = _dp_table(HERMES_APPROVAL_SRC, "DANGEROUS_PATTERNS", "_RE_FLAGS")
    except Exception as e:
        return warn("%s — could not read Hermes' list to compare (%s)"
                    % (DANGER_MSG, type(e).__name__),
                    "update Hermes Assistant (./update.sh), or set the Trust classes "
                    "you rely on to Ask")
    diffs = []
    if len(up) != len(mine):
        diffs.append("%d patterns in Hermes, %d in the copy" % (len(up), len(mine)))
    keys_up, keys_mine = [k for _r, k in up], [k for _r, k in mine]
    added = [k for k in keys_up if k not in keys_mine]
    gone = [k for k in keys_mine if k not in keys_up]
    if added:
        diffs.append("new in Hermes: %s" % ", ".join(k[:40] for k in added[:3]))
    if gone:
        diffs.append("no longer in Hermes: %s" % ", ".join(k[:40] for k in gone[:3]))
    mine_by = {}
    for rx, k in mine:
        mine_by.setdefault(k, []).append(rx)
    changed = [k for rx, k in up if k in mine_by and rx not in mine_by[k]]
    if changed:
        diffs.append("pattern changed: %s" % ", ".join(k[:40] for k in changed[:3]))
    if not diffs and up != mine:
        diffs.append("same patterns in a different order")
    if up_fl != mine_fl:
        diffs.append("flags %s vs %s" % ("|".join(sorted(up_fl)) or "none",
                                         "|".join(sorted(mine_fl)) or "none"))
    if diffs:
        return warn("%s (%s)" % (DANGER_MSG, "; ".join(diffs)),
                    "update Hermes Assistant (./update.sh), or set the Trust classes "
                    "you rely on to Ask")
    return ok("Hermes' %d dangerous-command patterns match the copy Trust uses" % len(up))


@check("claude_bridge", "Claude bridge")
def _chk_claude():
    b = _claude_bin()
    esc = _escalation_on()
    sw = "escalation %s" % ("on" if esc else "OFF (nothing reaches Claude)")
    if not b:
        return warn("Claude CLI not found — the deep brain is unavailable · " + sw,
                    "install the Claude CLI, or leave escalation off")
    return ok("%s · %s" % (b, sw))


@check("apple_apps", "Apple apps")
def _chk_apple_apps():
    rem, notes = _apple_apps_state()
    on = [n for n, v in (("Reminders", rem), ("Notes", notes)) if v]
    if on:
        return warn("%s automation ON — launches the app on refresh" % " + ".join(on),
                    "Settings › Connections › Apple apps")
    # Says only what this switch governs.  It used to add "Google Calendar used
    # for context", which was printed whether or not Google was connected
    # (2026-10-21 audit C31) — and calendar context is not this switch's to
    # describe: it comes from icalBuddy on every surface but the chat preamble.
    return ok("Reminders + Notes automation off — neither app is launched on refresh")


@check("google", "Google")
def _chk_google():
    """Google is optional, so not connected is a PASS — but a token file is not
    a working grant (C21): once Google refuses the refresh, calendar context
    stops reaching Hermes while every file on disk still looks connected."""
    st = _google_state()
    reason = str(st.get("reason") or "")
    fix = "Settings › Connections › Google: Disconnect, then connect again"
    if st.get("connected") is None:
        return ok("token on file · whether its last refresh worked is known only to "
                  "the running dashboard")
    if not st.get("connected"):
        if reason in ("", "not_connected"):
            return ok("not connected (optional)")
        return warn("a token is on file but cannot be used (%s) — Google context is off"
                    % _sample(reason, 60, 60), fix)
    if reason.startswith("refresh_failed:"):
        code = _sample(reason.split(":", 1)[1], 40, 40)
        when = (" at " + _fmt_when(st.get("refresh_failed_at"))) if st.get("refresh_failed_at") else ""
        if st.get("needs_reauth") is True:
            return warn("Google no longer accepts this sign-in (%s%s): it was revoked or it "
                        "expired, so your calendar is not reaching Hermes" % (code, when), fix)
        return warn("the last token refresh failed (%s%s) — retried every 5 minutes" % (code, when),
                    "check the network; if it persists, " + fix)
    if st.get("scope_excess"):
        return warn("connected, but the grant is wider than read-only — it can also "
                    "change your calendar", fix + " to narrow it")
    return ok("connected · read-only")


@check("index", "Search index")
def _chk_index():
    if not os.path.exists(IX_DB):
        return warn("index.db absent — 'Search everything' has nothing to search",
                    "it builds itself ~60s after the dashboard starts")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % IX_DB, uri=True, timeout=5.0)
    except Exception as e:
        return bad("index.db unreadable — %s: %s" % (type(e).__name__, e),
                   "rm %s and restart the dashboard to rebuild" % IX_DB)
    try:
        n = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        per = con.execute("SELECT source, COUNT(*) FROM items GROUP BY source").fetchall()
        row = con.execute("SELECT v FROM ix_meta WHERE k='last_sweep'").fetchone()
    except Exception as e:
        return bad("index.db is present but its schema did not answer — %s: %s"
                   % (type(e).__name__, e),
                   "rm %s and restart the dashboard to rebuild" % IX_DB)
    finally:
        try:
            con.close()
        except Exception:
            pass
    last = ""
    try:
        meta = json.loads(row[0]) if row else {}
        last = _fmt_when(meta.get("ts"))
    except Exception:
        last = "unknown"
    srcs = ", ".join("%s %d" % (s, c) for s, c in sorted(per)) or "no sources"
    if n == 0:
        return warn("0 documents (%s) — last build %s" % (srcs, last),
                    "open the dashboard; the sweep runs at start+60s and every 30 min")
    return ok("%d documents (%s) · last build %s" % (n, srcs, last))


@check("needsyou", "Needs-you")
def _chk_needsyou():
    if not os.path.exists(NY_STORE):
        return ok("store not created yet — it is written the first time the inbox is built")
    d = _read_json(NY_STORE, "__err__")
    if d == "__err__" or not isinstance(d, dict):
        return warn("needsyou.json is unreadable — the inbox restarts from empty",
                    "rm %s (nothing upstream is lost: the store only TAGS)" % NY_STORE)
    items = d.get("items") if isinstance(d.get("items"), dict) else {}
    hist = d.get("history") if isinstance(d.get("history"), dict) else {}
    shown = d.get("shown") if isinstance(d.get("shown"), (dict, list)) else []
    return ok("%d tagged items · %d known correspondents · %d sightings recorded"
              % (len(items), len(hist), len(shown)))


@check("onboarding", "First-run setup")
def _chk_onboarding():
    if not os.path.exists(ONB_FILE):
        return warn("not completed — the setup sheet opens itself on the next load",
                    "open the dashboard, or POST /api/onboarding/done to skip it")
    d = _read_json(ONB_FILE, {}) or {}
    return ok("completed %s (v%s)" % (_fmt_when(d.get("ts")), d.get("version") or "?"))



# The cached answer is re-judged against what is installed NOW, as the
# Settings card (aux_update._upd_refresh_cached) does: a cache written before
# an update kept update_available=true, so doctor said "vX is available" with
# vX installed (2026-11-09 audit E127). The version order is SemVer, the same
# as aux_update._upd_cmp (t_e127_doctor_update_verdict.py keeps them equal).
_UPD_VER_RE = re.compile(
    r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$")


def _upd_ver(s):
    m = _UPD_VER_RE.match(str(s or "").strip())
    if not m:
        return None
    nums = tuple(int(m.group(i) or 0) for i in (1, 2, 3))
    pre = tuple(int(x) if x.isdigit() else x for x in (m.group(4) or "").split(".") if x)
    return nums, pre


def _upd_newer(latest, current):
    a, b = _upd_ver(latest), _upd_ver(current)
    if not latest or not current or a is None:
        return False
    if b is None:
        return True
    if a[0] != b[0]:
        return a[0] > b[0]
    pa, pb = a[1], b[1]
    if not pa or not pb:
        return (not pa) and bool(pb)          # a release beats its pre-release
    for x, y in zip(pa, pb):
        if x == y:
            continue
        xn, yn = isinstance(x, int), isinstance(y, int)
        if xn != yn:
            return not xn                     # numeric < alphanumeric
        return x > y
    return len(pa) > len(pb)


def _upd_still_available(pay, cur):
    """Is the cached answer's update still an update for what runs now?"""
    if pay.get("source") == "main":
        # main channel: only while origin/main's commit is not in HEAD (E130)
        remote = str(pay.get("remote_head") or "")
        if remote:
            try:
                rc = subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor",
                                     remote, "HEAD"], capture_output=True,
                                    timeout=5).returncode
                if rc in (0, 1):
                    return rc == 1
            except Exception:
                pass
        return bool(pay.get("update_available"))
    return _upd_newer(pay.get("latest"), cur)

@check("updater", "Updates")
def _chk_updater():
    """Reads the 6h cache aux_update.py already keeps.  Deliberately does NOT
    hit GitHub: doctor must be runnable on a plane."""
    try:
        cur = open(VERSION_FILE).read().strip()
    except Exception:
        cur = "?"
    cache = _read_json(UPD_CACHE, {}) or {}
    pay = cache.get("payload") if isinstance(cache.get("payload"), dict) else {}
    checked = cache.get("checked_at") or pay.get("checked_at")
    latest = pay.get("latest") or ""
    err = pay.get("error") or ""
    chan = cache.get("channel") or pay.get("channel") or "stable"
    when = _fmt_when(checked)
    age = _ago(checked)
    base = "running %s · channel %s · last checked %s" % (cur, chan, when)
    if not checked:
        return warn(base + " — no release check has ever completed",
                    "Settings > System & Data > Check for updates")
    if err:
        return warn(base + " · last check failed: %s" % err,
                    "Settings > System & Data > Check for updates")
    if _upd_still_available(pay, cur):
        return warn(base + " · %s is available" % latest,
                    "Settings > System & Data > Update, or ./update.sh")
    if age is not None and age > 48 * 3600:
        return warn(base + " — the cached answer is over 48h old",
                    "Settings > System & Data > Check for updates")
    return ok(base + " · latest known %s" % (latest or cur))


@check("logs", "Logs")
def _chk_logs():
    if not os.path.isdir(LOGS):
        return bad("%s does not exist — launchd cannot write service logs" % LOGS,
                   "mkdir -p %s && ./install-services.sh" % LOGS)
    if not os.access(LOGS, os.W_OK):
        return bad("%s is not writable — every service log is being dropped" % LOGS,
                   "chmod u+w %s" % LOGS)
    if not os.path.exists(DASH_LOG):
        return warn("%s does not exist yet" % DASH_LOG,
                    "launchctl kickstart -k gui/$(id -u)/%s" % LABEL_DASH)
    try:
        sz = os.path.getsize(DASH_LOG)
        mt = os.path.getmtime(DASH_LOG)
    except OSError as e:
        return warn("%s: %s" % (type(e).__name__, e), "ls -l %s" % DASH_LOG)
    # Sizes of every service log, not only the hub's (2026-10-21 audit C14):
    # the model, serve and gateway logs used to grow unwatched.
    sizes, big = [], []
    for name in SERVICE_LOGS:
        p = os.path.join(LOGS, name)
        try:
            n = os.path.getsize(p)
        except OSError:
            continue
        sizes.append("%s %.1f MB" % (name, n / (1024 ** 2)))
        if n > LOG_BIG_MB * 1024 * 1024:
            big.append(name)
    detail = ("writable · dashboard.log %.1f MB, last written %s"
              % (sz / (1024 ** 2), _fmt_when(mt)))
    if sizes:
        detail += " · " + ", ".join(sizes)
    if sz > LOG_BIG_MB * 1024 * 1024:
        big.insert(0, "dashboard.log")
    if big:
        # The dashboard copy-truncates its own log and the launchd service logs
        # at 8 MB (startup + hourly) and the agent rotates gateway.log at 5 MB,
        # so a log this size means nothing is rotating it.
        return warn(detail + " — %s over %d MB, so nothing is rotating it (dashboard "
                    "down, or HERMES_DASH_LOG_MAX set to 0?)" % (", ".join(big), LOG_BIG_MB),
                    ": > %s   # truncate in place" % os.path.join(LOGS, big[0]))
    return ok(detail)


_ERR_RE = re.compile(
    r"traceback|\bexception\b|\berror\b|\bfailed\b|\bcritical\b", re.I)
# `[guard] refused ...` is the same-origin guard DOING ITS JOB — one line per
# denial, by design (CLAUDE.md).  Counting policy denials as errors would make
# a hardened dashboard look broken, so they are skipped here explicitly.
_ERR_SKIP_RE = re.compile(r"^\[guard\] refused ")
_LOG_TAIL = 200 * 1024        # bytes of dashboard.log to scan

# server.py's main() prints this once the socket is bound, so the LAST one in
# the tail is where the running process's output begins.  launchd never rotates
# dashboard.log, so without this scope a failure that was fixed weeks ago keeps
# a healthy Mac at WARN forever — the count has to describe the process that is
# running now.
_DASH_BOOT_RE = re.compile(r"^Hermes Assistant dashboard: https?://")
# A process that died before main() never prints the banner.  aux_recorder's
# line comes from an aux module at IMPORT, so it survives that — but a launchd
# restart storm writes several with nothing between them, and each one is a
# separate short-lived process.  The EARLIEST of such a run is taken, which
# over-counts rather than hiding lines the reader needs.
_DASH_BOOT_FALLBACK_RE = re.compile(r"^\[aux_recorder\] reconciler started\s*$")


def _last_boot_index(lines):
    """Index of the last dashboard-start marker in `lines`, or None."""
    idx = [i for i, ln in enumerate(lines) if _DASH_BOOT_RE.match(ln)]
    if idx:
        return idx[-1]
    idx = [i for i, ln in enumerate(lines) if _DASH_BOOT_FALLBACK_RE.match(ln)]
    if not idx:
        return None
    i = len(idx) - 1
    while i > 0 and (idx[i] - idx[i - 1]) <= 3:     # one restart storm
        i -= 1
    return idx[i]


def _dash_started_when():
    """"4:57 PM" for the RUNNING dashboard, or "" when it is not up.

    dashboard.log carries no timestamps (it is raw stdout), so the clock cannot
    come from the file — it comes from the process launchd is running.  Read-
    only: `launchctl list` plus a `ps`, no interpretation of either beyond the
    pid and its start time."""
    pid = _launchctl(LABEL_DASH).get("pid")
    if not pid:
        return ""
    rc, out = _run(["/bin/ps", "-p", str(pid), "-o", "lstart="], timeout=4)
    if rc != 0 or not out.strip():
        return ""
    try:
        return _fmt_when(time.mktime(time.strptime(out.strip(),
                                                   "%a %b %d %H:%M:%S %Y")))
    except Exception:
        return ""


@check("log_errors", "Recent errors")
def _chk_log_errors():
    """Two logs, two windows, because they are shaped differently.

    ~/.hermes/logs/dashboard.log is raw stdout/stderr with NO timestamps, so a
    literal 24-hour window is not computable from it — we scan the last 200 KB
    instead and say so, and report the file's last-write time so the reader can
    judge how recent that tail is.  ~/.hermes/logs/errors.log (the agent's) IS
    timestamped, so that half is a real 24-hour count."""
    bits, status, fix = [], PASS, ""
    if os.path.exists(DASH_LOG):
        # A log we could not read yields NO count.  Printing "0 error lines"
        # for a file we never opened is the most dangerous shape a health
        # report can take: it is indistinguishable from a healthy machine.
        tail = None
        try:
            sz = os.path.getsize(DASH_LOG)
            with open(DASH_LOG, "rb") as f:
                if sz > _LOG_TAIL:
                    f.seek(sz - _LOG_TAIL)
                    f.readline()          # drop the half line we landed in
                tail = f.read().decode("utf-8", "replace")
        except OSError as e:
            bits.append("could not read %s (%s)" % (DASH_LOG, type(e).__name__))
            status = WARN
            fix = "ls -l %s" % DASH_LOG
        if tail is not None:
            scanned = tail.splitlines()
            boot = _last_boot_index(scanned)
            if boot is not None:
                scanned = scanned[boot + 1:]
            lines = [ln for ln in scanned
                     if ln.strip() and _ERR_RE.search(ln) and not _ERR_SKIP_RE.match(ln)]
            if boot is None:
                # no start marker in the tail: the running process has already
                # written more than _LOG_TAIL, so the window is all we can say.
                bits.append("%d error lines in the last %d KB of dashboard.log"
                            % (len(lines), _LOG_TAIL // 1024))
            else:
                when = _dash_started_when()
                bits.append("%d error lines in dashboard.log since the last start%s"
                            % (len(lines), (" (%s)" % when) if when else ""))
            if lines:
                # The most REPEATED shape, not the newest line: a log full of one
                # failure looping is the thing a reader has to see, and the last
                # line to land is usually incidental.  Normalised on its first 60
                # characters so a varying tail (a path, a count) still groups.
                # The sample is raw log text in a report someone pastes into an
                # issue, so it goes through `_sample` (scrubber, then `~`).
                tally = {}
                for ln in lines:
                    k = _plain(ln)[:60]
                    tally[k] = tally.get(k, 0) + 1
                key, n = max(tally.items(), key=lambda kv: kv[1])
                bits.append("most common (%dx): %s" % (n, _sample(key)))
            if len(lines) >= 10:
                status = WARN
                fix = "tail -n 200 %s" % DASH_LOG
    else:
        bits.append("no dashboard.log")

    if os.path.exists(ERR_LOG):
        cutoff = time.time() - 24 * 3600
        n = 0
        readable = True
        try:
            sz = os.path.getsize(ERR_LOG)
            with open(ERR_LOG, "rb") as f:
                if sz > _LOG_TAIL:
                    f.seek(sz - _LOG_TAIL)
                    f.readline()
                for ln in f.read().decode("utf-8", "replace").splitlines():
                    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+\s+"
                                 r"(ERROR|CRITICAL)\b", ln)
                    if not m:
                        continue
                    try:
                        ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        continue
                    if ts >= cutoff:
                        n += 1
        except OSError as e:
            # Same rule as the dashboard half: no count, and never a silent 0.
            readable = False
            bits.append("could not read %s (%s)" % (ERR_LOG, type(e).__name__))
            if status == PASS:
                status = WARN
            fix = fix or "ls -l %s" % ERR_LOG
        if readable:
            bits.append("%d agent ERROR lines in the last 24h" % n)
            if n >= 20 and status == PASS:
                status = WARN
                fix = "tail -n 200 %s" % ERR_LOG
    return {"status": status, "detail": " · ".join(bits), "fix": fix}


# --------------------------------------------------------------------------
# runner + report
# --------------------------------------------------------------------------
def run_checks(only=None):
    """Every check, each in its own try/except.  A crash inside a check is a
    FAIL carrying the exception text — it never takes the report down."""
    out = []
    for cid, label, fn in CHECKS:
        if only and cid not in only:
            continue
        t0 = time.time()
        try:
            r = fn() or {}
            status = r.get("status") or PASS
            detail = str(r.get("detail") or "")
            fix = str(r.get("fix") or "")
            if status not in (PASS, WARN, FAIL):
                status, detail = FAIL, "check returned an unknown status %r" % (status,)
                fix = "this is a doctor bug — see dashboard/doctor.py"
        except Exception as e:
            status = FAIL
            detail = "check crashed — %s: %s" % (type(e).__name__, e)
            fix = "this is a doctor bug — see dashboard/doctor.py"
        # ONE place collapses the home directory, so a check added later cannot
        # forget to — and the JSON payload is scrubbed exactly like the text.
        detail = _display_path(detail)
        fix = _display_path(fix)
        out.append({"id": cid, "label": label, "status": status,
                    "detail": detail, "fix": fix,
                    "ms": int((time.time() - t0) * 1000)})
    return out


def summarize(checks):
    s = {PASS: 0, WARN: 0, FAIL: 0}
    for c in checks:
        s[c.get("status", PASS)] = s.get(c.get("status", PASS), 0) + 1
    return s


def payload(checks=None):
    """The exact object /api/doctor serves."""
    checks = run_checks() if checks is None else checks
    return {"ok": True, "generated": int(time.time()),
            "summary": summarize(checks), "checks": checks}


def _version():
    try:
        return open(VERSION_FILE).read().strip()
    except Exception:
        return "?"


def report(checks=None, quiet=False, width=None):
    """The one-screen text report.  Plain ASCII status words, no colour and no
    emoji — it is meant to be copied into an issue verbatim."""
    checks = run_checks() if checks is None else checks
    if width is None:
        try:
            width = max(60, min(shutil.get_terminal_size((100, 24)).columns, 120))
        except Exception:
            width = 100
    labels = [c["label"] for c in checks] or ["x"]
    lw = min(max(len(x) for x in labels), 20)
    pad = 6 + lw + 2                       # "PASS  " + label + two spaces
    body = max(24, width - pad)

    def wrap(text, indent):
        words, line, lines = str(text).split(), "", []
        for w in words:
            if line and len(line) + 1 + len(w) > body:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w) if line else w
        if line:
            lines.append(line)
        if not lines:
            return []
        return [lines[0]] + [" " * indent + x for x in lines[1:]]

    s = summarize(checks)
    out = ["Hermes Assistant \u00b7 doctor",
           "%s \u00b7 %s" % (_version(), time.strftime("%a %b %-d, %-I:%M %p")),
           ""]
    for c in checks:
        if quiet and c["status"] == PASS:
            continue
        head = "%-5s %-*s  " % (c["status"].upper(), lw, c["label"][:lw])
        seg = wrap(c["detail"], pad)
        out.append(head + (seg[0] if seg else ""))
        out.extend(seg[1:])
        if c["status"] != PASS and c["fix"]:
            fseg = wrap("fix: " + c["fix"], pad + 2)
            out.append(" " * pad + fseg[0])
            out.extend("  " + x for x in fseg[1:])
    out.append("")
    out.append("%d pass \u00b7 %d warn \u00b7 %d fail" % (s[PASS], s[WARN], s[FAIL]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="doctor.py",
        description="One screen of read-only health checks for Hermes Assistant. "
                    "Never starts or wakes a model server.")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable payload instead of the report")
    ap.add_argument("--quiet", action="store_true",
                    help="print only WARN/FAIL lines plus the tally")
    ap.add_argument("--only", default="",
                    help="comma-separated check ids to run (default: all)")
    ap.add_argument("--list", action="store_true", help="list the check ids and exit")
    a = ap.parse_args(argv)

    if a.list:
        for cid, label, _ in CHECKS:
            print("%-14s %s" % (cid, label))
        return 0

    only = {x.strip() for x in a.only.split(",") if x.strip()} or None
    checks = run_checks(only)
    if a.json:
        print(json.dumps(payload(checks), indent=2))
    else:
        print(report(checks, quiet=a.quiet))
    return 1 if summarize(checks)[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
