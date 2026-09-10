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

DASH_PORT = int(os.environ.get("DASH_PORT", "7788") or "7788")
DASH_BASE = "http://127.0.0.1:%d" % DASH_PORT
MODEL_URL = os.environ.get("MODEL_URL", "http://127.0.0.1:8080/v1/models")
BG_MODEL_URL = os.environ.get("BG_MODEL_URL", "http://127.0.0.1:8081/v1/models")

VERSION_FILE = os.path.join(ROOT, "VERSION")
REPO_CONFIG = os.path.join(ROOT, "config.yaml")
HERMES_CONFIG = os.path.join(HOME, ".hermes", "config.yaml")

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


def _http(url, timeout=3):
    """(status, body_text) or (0, error_string).  GET only, no headers added —
    the dashboard's same-origin guard allows a request that carries no Origin."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
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


def _model_downloaded(mid):
    snap = _hf_snapshot_dir(mid)
    return bool(snap) and _weights_complete(snap)


def _draft_state(m):
    """"ready" | "missing" | "" (no drafter) for a roster entry."""
    sub = (m.get("draft_subfolder") or "").strip("/")
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
    """settings.json claude_escalation.enabled, default True (fails open, the
    way claude_escalation_enabled() does)."""
    fn = _host("claude_escalation_enabled")
    if fn:
        try:
            return bool(fn())
        except Exception:
            pass
    s = _read_json(SETTINGS_FILE, {}) or {}
    blk = s.get("claude_escalation")
    if isinstance(blk, dict) and "enabled" in blk:
        return bool(blk.get("enabled"))
    return True


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
        return warn(" · ".join(parts) + " — chat falls back to one-shot `hermes -z`",
                    "./install-services.sh")
    return {"status": status, "detail": " · ".join(parts), "fix": fix}


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
        complete = bool(snap) and _weights_complete(snap)
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
        fix = fix or ("repair or delete %s (the dashboard rewrites defaults)"
                      % SETTINGS_FILE)
    else:
        bits.append("settings.json ok")
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
    return ok("Reminders + Notes automation off — Google Calendar used for context")


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
    if pay.get("update_available"):
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
    d = ok("writable · dashboard.log %.1f MB, last written %s"
           % (sz / (1024 ** 2), _fmt_when(mt)))
    if sz > 200 * 1024 * 1024:
        return warn("dashboard.log is %.0f MB — launchd never rotates it"
                    % (sz / (1024 ** 2)),
                    ": > %s   # truncate in place" % DASH_LOG)
    return d


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
