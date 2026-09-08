# aux_promptbudget.py — "Prompt budget": the fixed prefix every fresh chat pays,
# measured, plus the one config knob that shrinks it.
#
#   GET  /api/prompt/budget            -> the measured breakdown (5 min cache)
#   GET  /api/prompt/budget?fresh=1    -> bypass the cache
#   POST /api/prompt/budget            -> {"toolsets":[...]} | {"profile":"lean"|"full"}
#                                         (+ optional {"restart":true})
#
# WHY THIS EXISTS.  Ground truth from ~/.hermes/logs/mlx-server.log: every fresh
# dashboard session prefills 20,468-20,622 tokens at ~735-810 tok/s, i.e. 25-28
# seconds of wall clock before the first token, once per new conversation (every
# later turn is ~0.2s off the APC exact-prefix cache — see
# docs/plans/post-v1-baseline.md).  Prefill is FLOP-bound, so tokens ARE seconds.
# `hermes prompt-size` says where they go: ~20.7k chars of system prompt (8,988
# of that the skills index) and 55,855 bytes of TOOL SCHEMA JSON — the tool
# schemas are the single biggest item in the prefix, bigger than the whole system
# prompt.  Turning tools off is the only lever that costs nothing but a config
# line.
#
# ============================================================================
# THE KEY THIS MODULE WRITES IS `platform_toolsets.cli`, NOT `.tui`.  MEASURED.
# ============================================================================
# Dashboard chats run through `hermes serve` (the TUI gateway), and every new
# session's tool list comes from `tui_gateway/server.py::_load_enabled_toolsets`.
# Its resolution order is:
#   1. env `HERMES_TUI_TOOLSETS` (an explicit pin; com.hermes.serve.plist sets
#      no such variable, verified),
#   2. `agent.coding_context.coding_selection(platform="tui")` — the coding
#      posture.  It returns None here: the serve plist's WorkingDirectory and the
#      dashboard's own `session.create` cwd are both `~`, which is not a code
#      workspace (probed: is_coding False),
#   3. the fallback, which is literally
#         enabled = _get_platform_tools(cfg, "cli", include_default_mcp_servers=True)
#      — the platform key is HARDCODED "cli".  There is no "tui" entry in
#      `hermes_cli/platforms.py::PLATFORMS`, and a `platform_toolsets.tui:` block
#      in config.yaml is read by nothing.  Writing it would be a silent no-op.
# So the honest knob is `platform_toolsets.cli`.  A/B measured against a sandbox
# HERMES_HOME: `cli: [hermes-cli]` -> 33 tools / 55,275 B; the lean list -> 22
# tools / 47,931 B.  The same key also governs `hermes -z` one-shot runs
# (run_agent: the chat fallback, briefings, the watchtower intel pass, For-You)
# and an interactive `hermes` in a terminal — the same agent, so the diet is
# consistent, and those runs pay the same prefill.  Said plainly in the UI.
#
# NO RESTART IS NEEDED.  `_load_enabled_toolsets()` is uncached and runs at agent
# construction (per `_make_agent`, per new session), and `hermes_cli.config.
# load_config()` is memoised on the config file's (mtime_ns, size) — an edit is
# picked up by the very next new session.  Open conversations keep the agent they
# were built with, so a restart is only ever a convenience; POST does it solely
# when the caller asks for `"restart": true`.
#
# HOW THE NUMBERS ARE COMPUTED.  Two read-only sources, no model ever loaded:
#   * `hermes prompt-size --platform tui --json`, run with cwd=~ so it cannot
#     pick up a stray AGENTS.md.  NOTE its `tools` figure is NOT platform-aware:
#     `_build_inspection_agent` constructs an AIAgent without `enabled_toolsets`,
#     so `get_tool_definitions(None)` takes the "start with everything" branch.
#     Its 34 tools / 55,855 B is therefore the ceiling, not this platform's
#     answer, and it does not move when the config changes.  We use it for the
#     system-prompt/skills-index half only.
#   * `_pb_probe()` — one short-lived run of the Hermes venv interpreter that
#     imports the tool registry and calls
#     `len(json.dumps(defs, ensure_ascii=False).encode())`, byte-for-byte the
#     same expression `hermes_cli/prompt_size.py` uses, so the numbers add up
#     (verified: our all-toolsets figure is 55,855, identical to prompt-size).
#     It also resolves any HYPOTHETICAL selection by handing `_get_platform_tools`
#     a config dict built in memory — that is how "before -> after" is previewed
#     without writing the file.
#
# THE SKILLS INDEX IS NOT TOUCHED, DELIBERATELY.  `build_skills_system_prompt`
# does take `compact_categories` (names-only for a whole category), and a config
# key does reach it — `agent.coding_context: focus|strict|lean`.  But
# `coding_compact_skill_categories()` returns a non-empty set only when
# `is_coding` is true, and dashboard sessions run with cwd=~, where it is false
# (probed).  So no config path reaches THIS surface, and patching hermes-agent is
# out of scope: the finding and the measured headroom ride in the payload as
# `skills.note` instead.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an aux
# module — it rebinds the shared global.  Private aliases only.
#
# LOAD ORDER.  aux files exec SORTED, so this module runs AFTER aux_onboarding
# and BEFORE aux_quickask/aux_update/aux_watchtower.  Nothing here reads a
# foreign global at module load: `HERMES`, `HOME` and `_hermes_env` are
# server.py's own (defined before any aux exec), and everything else is resolved
# inside a request.
import binascii as _pb_binascii
import contextlib as _pb_contextlib
import fcntl as _pb_fcntl
import json as _pb_json
import os as _pb_os
import re as _pb_re
import shutil as _pb_shutil
import subprocess as _pb_subprocess
import threading as _pb_threading
import time as _pb_time
import datetime as _pb_datetime

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
_PB_CFG = _pb_os.path.join(HOME, ".hermes", "config.yaml")        # noqa: F821
_PB_AGENT_ROOT = _pb_os.path.join(HOME, ".hermes", "hermes-agent")  # noqa: F821
_PB_VENV_PY = _pb_os.path.join(_PB_AGENT_ROOT, "venv", "bin", "python")

# The platform key `_load_enabled_toolsets`'s fallback actually reads. See the
# header — this is measured, not assumed.
_PB_PLATFORM_KEY = "cli"

_PB_CACHE_TTL = 300.0          # 5 minutes; every write busts it
_PB_PROMPT_SIZE_TIMEOUT = 60   # seconds, per the brief
_PB_PROBE_TIMEOUT = 90

# Measured on this Mac: prefill is compute-bound at ~735-810 tok/s on the 27B
# (mlx-server.log, cached_tokens=0 lines). 750 is the round number in the middle.
_PB_PREFILL_TOK_S = 750.0
# Tool-schema JSON is denser than prose; 3.6 bytes/token is the ratio that makes
# prompt-size's totals land on the prompt_tokens the model server actually logs.
_PB_BYTES_PER_TOKEN = 3.6

# The cold prefix the model server actually logged before any diet, for the
# "share of the measured baseline" column: mlx-server.log's cached_tokens=0
# lines run 20,468-20,622 tokens. The estimator lands ~2.5% above it, so the
# honest way to quote a saving is against the FULL profile's own estimate
# (apples to apples) with this as the sanity check beside it.
_PB_BASELINE_TOKENS = 20622

# ---- the three profiles ---------------------------------------------------
# BALANCED (config key `lean`, kept for backward compatibility — it shipped
# under that name and a config written by the previous build must keep
# resolving). Everything a local assistant on this Mac actually uses: search
# the web, run a command, read and write files, run code, use its skills, keep
# a todo, remember, search its own history, ask a clarifying question,
# delegate, look at an image, and drive the screen.
# Dropped: browser (12 schemas, the single largest toolset), tts, image_gen,
# video, video_gen, x_search, cronjob, homeassistant, spotify, discord,
# discord_admin, yuanbao, context_engine. Verified against dashboard/*.py and
# dashboard/*.js: nothing in the dashboard asks the agent for any of those tools.
_PB_LEAN = [
    "web", "terminal", "file", "code_execution", "skills", "todo", "memory",
    "session_search", "clarify", "delegation", "vision", "computer_use",
]

# FOCUSED — Balanced minus the three remaining heavyweight single-tool schemas:
# session_search (5.9 KB), delegation (5.5 KB) and computer_use (5.2 KB). This
# is the profile for someone who chats: it still reads and writes files, runs
# commands and code, searches the web, sees images and uses its skills, but it
# gives up screen control, sub-agents, and searching past conversations FROM
# INSIDE a chat. Nothing is lost from the dashboard itself — the Settings
# search, the conversation list and the local index are dashboard features that
# never go through an agent tool — and the three are one click back on.
_PB_FOCUSED = [
    "web", "terminal", "file", "code_execution", "skills", "todo", "memory",
    "clarify", "vision",
]

# key -> (UI label, the one-line honest cost of choosing it)
_PB_PROFILES = {
    "full": ("Full",
             "Every tool the agent can offer. This is the default, "
             "and it is one click away again."),
    "lean": ("Balanced",
             "No browser automation, speech, or image and video generation."),
    "focused": ("Focused",
                "Also no screen control, no sub-agents, and no "
                "past-conversation search from chat."),
}

# Fallback only — the live list comes from the probe (CONFIGURABLE_TOOLSETS in
# hermes_cli/tools_config.py). Kept so validation still works if the Hermes venv
# has gone missing.
_PB_KEYS_FALLBACK = [
    "web", "browser", "terminal", "file", "code_execution", "vision", "video",
    "image_gen", "video_gen", "x_search", "tts", "skills", "todo", "memory",
    "context_engine", "session_search", "clarify", "delegation", "cronjob",
    "homeassistant", "spotify", "discord", "discord_admin", "yuanbao",
    "computer_use",
]

_pb_lock = _pb_threading.Lock()
_pb_cache = {"at": 0.0, "payload": None}

# Single-flight for the two subprocess-backed measurements. `hermes prompt-size`
# and the venv probe each start a fresh Python that imports the whole tool
# registry (seconds of CPU, tens of MB), so N concurrent `?fresh=1` calls must
# cost ONE pair of interpreters, not N. `_pb_payload` also coalesces: a caller
# that waited for a build which finished AFTER it asked takes that answer
# instead of starting its own.
_pb_build_lock = _pb_threading.Lock()

# The advisory lock every writer of config.yaml takes. Same PATH as
# hermes-plugins/plugin_enable.py's `config_lock()` — that shared path, not
# shared code, is what makes the dashboard, install.sh and update.sh serialise
# against each other (this module must not import the plugin helper: it edits a
# different key and has to keep working when the helper is missing).
_PB_LOCK_FILE = _PB_CFG + ".lock"
_PB_LOCK_TIMEOUT = 20.0


# ---------------------------------------------------------------------------
# the read-only probe, run under the Hermes interpreter
# ---------------------------------------------------------------------------
# Reads its request from $PB_IN and writes its answer to $PB_OUT rather than
# stdout: importing the Hermes CLI packages installs console plumbing that
# swallows stdout (observed — the JSON came back on stderr).
_PB_PROBE_SRC = r'''
import json, os, sys
sys.path.insert(0, os.environ["PB_ROOT"])

from model_tools import get_tool_definitions
from hermes_cli.config import load_config
from hermes_cli.tools_config import _get_platform_tools, CONFIGURABLE_TOOLSETS

PLAT = os.environ.get("PB_PLATFORM", "cli")


def size(defs):
    # byte-for-byte hermes_cli/prompt_size.py's tools.json_bytes
    return len(json.dumps(defs, ensure_ascii=False).encode("utf-8"))


def names(defs):
    out = []
    for d in defs or []:
        n = (d.get("function") or {}).get("name") or d.get("name")
        if n:
            out.append(n)
    return sorted(out)


req = {}
try:
    with open(os.environ["PB_IN"], encoding="utf-8") as fh:
        req = json.load(fh) or {}
except Exception:
    req = {}

cfg = load_config() or {}

keys, labels, descs = [], {}, {}
for row in CONFIGURABLE_TOOLSETS:
    if isinstance(row, (tuple, list)):
        k = row[0]
        labels[k] = row[1] if len(row) > 1 else k
        descs[k] = row[2] if len(row) > 2 else ""
    else:
        k = row
        labels[k] = k
        descs[k] = ""
    keys.append(k)


def resolve(sel):
    """What a session would get if platform_toolsets.<PLAT> were `sel`.

    Mirrors tui_gateway/server.py::_load_enabled_toolsets's fallback branch,
    including its `| {"project"}` fold and its `if not enabled: return None`.
    Nothing is written to disk.
    """
    c = dict(cfg)
    pt = dict(c.get("platform_toolsets") or {})
    if sel is None:
        pt.pop(PLAT, None)
    else:
        pt[PLAT] = list(sel)
    c["platform_toolsets"] = pt
    enabled = _get_platform_tools(c, PLAT, include_default_mcp_servers=True)
    ts = sorted(set(enabled) | {"project"}) if enabled else None
    defs = get_tool_definitions(enabled_toolsets=ts, quiet_mode=True)
    return {"toolsets": ts, "count": len(defs), "json_bytes": size(defs),
            "names": names(defs)}


out = {"keys": keys, "labels": labels, "descs": descs,
       "per_toolset": {}, "scenarios": {}}

for k in keys:
    d = get_tool_definitions(enabled_toolsets=[k], quiet_mode=True)
    # minus the two bytes of the enclosing "[]" so an empty toolset reads 0
    out["per_toolset"][k] = {"count": len(d), "json_bytes": max(0, size(d) - 2),
                             "names": names(d)}

for nm, sel in (req.get("scenarios") or {}).items():
    try:
        out["scenarios"][nm] = resolve(sel)
    except Exception as e:
        out["scenarios"][nm] = {"error": "%s: %s" % (type(e).__name__, e)}

# The skills index, and what compacting the unused categories would save. Read
# only: no config path reaches this on the dashboard surface (see the module
# header), so it is reported, never applied.
try:
    from agent.prompt_builder import build_skills_system_prompt
    import datetime as _dt

    sk = os.path.expanduser("~/.hermes/skills")
    cats = sorted(d for d in os.listdir(sk)
                  if os.path.isdir(os.path.join(sk, d)) and not d.startswith("."))
    cat_of = {}
    for c in cats:
        for s in os.listdir(os.path.join(sk, c)):
            if os.path.isdir(os.path.join(sk, c, s)):
                cat_of[s] = c
    usage = {}
    try:
        with open(os.path.join(sk, ".usage.json"), encoding="utf-8") as fh:
            usage = json.load(fh) or {}
    except Exception:
        usage = {}
    cut = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=60)
    used_cats = set()
    for nm, rec in usage.items():
        lu = (rec or {}).get("last_used_at")
        if not lu:
            continue
        try:
            t = _dt.datetime.fromisoformat(str(lu).replace("Z", "+00:00"))
        except Exception:
            continue
        if t >= cut and cat_of.get(nm):
            used_cats.add(cat_of[nm])
    unused = [c for c in cats if c not in used_cats]
    out["skills"] = {
        "categories": cats,
        "used_categories": sorted(used_cats),
        "unused_categories": unused,
        "index_chars": len(build_skills_system_prompt()),
        "unused_compact_chars": len(
            build_skills_system_prompt(compact_categories=frozenset(unused))),
        "all_compact_chars": len(
            build_skills_system_prompt(compact_categories=frozenset(cats))),
    }
except Exception as e:
    out["skills"] = {"error": "%s: %s" % (type(e).__name__, e)}

with open(os.environ["PB_OUT"], "w", encoding="utf-8") as fh:
    json.dump(out, fh)
'''


def _pb_python():
    """The interpreter that can import the Hermes packages, or None."""
    if _pb_os.path.exists(_PB_VENV_PY):
        return _PB_VENV_PY
    # ~/.local/bin/hermes is a shell shim that execs the venv's entry point;
    # if the venv moved, read the path back out of it rather than guessing.
    try:
        with open(HERMES, encoding="utf-8") as fh:      # noqa: F821
            for line in fh:
                m = _pb_re.search(r'"([^"]+)/bin/hermes"', line)
                if m:
                    cand = m.group(1) + "/bin/python"
                    if _pb_os.path.exists(cand):
                        return cand
    except OSError:
        pass
    return None


def _pb_probe(scenarios):
    """One read-only run of the probe. Raises on failure; never loads a model."""
    py = _pb_python()
    if not py:
        raise RuntimeError("the Hermes venv interpreter was not found at %s"
                           % _PB_VENV_PY)
    tmp = _pb_os.path.join(_pb_os.path.dirname(_PB_CFG), "dashboard")
    if not _pb_os.path.isdir(tmp):
        tmp = _pb_os.path.expanduser("~")
    stamp = "%d-%d" % (_pb_os.getpid(), int(_pb_time.time() * 1000) % 100000)
    fin = _pb_os.path.join(tmp, ".promptbudget-in-%s.json" % stamp)
    fout = _pb_os.path.join(tmp, ".promptbudget-out-%s.json" % stamp)
    env = dict(_hermes_env())                            # noqa: F821
    env["PB_ROOT"] = _PB_AGENT_ROOT
    env["PB_IN"] = fin
    env["PB_OUT"] = fout
    env["PB_PLATFORM"] = _PB_PLATFORM_KEY
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    try:
        with open(fin, "w", encoding="utf-8") as fh:
            _pb_json.dump({"scenarios": scenarios}, fh)
        r = _pb_subprocess.run([py, "-c", _PB_PROBE_SRC],
                               capture_output=True, text=True,
                               timeout=_PB_PROBE_TIMEOUT,
                               cwd=_pb_os.path.expanduser("~"), env=env)
        if not _pb_os.path.exists(fout):
            err = (r.stderr or "").strip().splitlines()
            raise RuntimeError("the toolset probe wrote nothing (exit %s)%s"
                               % (r.returncode,
                                  (" — " + err[-1]) if err else ""))
        with open(fout, encoding="utf-8") as fh:
            return _pb_json.load(fh)
    finally:
        for p in (fin, fout):
            try:
                _pb_os.unlink(p)
            except OSError:
                pass


def _pb_prompt_size():
    """`hermes prompt-size --platform tui --json`, run from ~ so a stray
    AGENTS.md in some other directory cannot join the measurement."""
    r = _pb_subprocess.run([HERMES, "prompt-size", "--platform", "tui",   # noqa: F821
                            "--json"],
                           capture_output=True, text=True,
                           timeout=_PB_PROMPT_SIZE_TIMEOUT,
                           cwd=_pb_os.path.expanduser("~"),
                           env=_hermes_env())            # noqa: F821
    if r.returncode != 0:
        raise RuntimeError("hermes prompt-size exited %s: %s"
                           % (r.returncode, (r.stderr or "").strip()[:200]))
    return _pb_json.loads(r.stdout)


# ---------------------------------------------------------------------------
# config.yaml — a block-sequence reader/writer in the aux_config.py house style
# (stdlib line scanning, no yaml dependency)
# ---------------------------------------------------------------------------
# `hermes config set platform_toolsets.cli '["web","terminal"]'` was tried first
# and is unusable: it writes the value as a QUOTED STRING
# (`cli: '["web","terminal"]'`), which `_get_platform_tools` rejects with its
# `not isinstance(toolset_names, list)` guard and silently falls back to the full
# composite. Hence this editor. It is deliberately surgical — it rewrites only
# the lines of one platform's block and leaves every other byte of the file
# alone, comments included.

# BLOCK SCOPING.  A key inside `platform_toolsets:` is matched at the block's
# OWN child indent, captured from its first child line, and blank lines and
# comments are NEVER a block boundary.  Both matter: a `# note` in column 0 used
# to read as the end of the block, after which `cli:` was "not found" and a
# SECOND `cli:` key was written at the top while the real one still sat below
# the comment — a config with a duplicate key, silently resolved by whichever
# one PyYAML kept.  Same discipline as hermes-plugins/plugin_enable.py.
def _pb_skippable(line):
    s = line.strip()
    return (not s) or s.startswith("#")


def _pb_indent_of(line):
    return len(line) - len(line.lstrip())


def _pb_block_end(lines, start):
    """Index one past the last line of the block opened at `start`."""
    for i in range(start + 1, len(lines)):
        if _pb_skippable(lines[i]):
            continue
        if _pb_indent_of(lines[i]) == 0:
            return i
    return len(lines)


def _pb_child_indent(lines, start, end):
    """Indent of the block's own children, from its first child line."""
    for i in range(start + 1, end):
        if _pb_skippable(lines[i]):
            continue
        return _pb_indent_of(lines[i])
    return None


def _pb_find_block(lines):
    for i, line in enumerate(lines):
        if _pb_re.match(r"^platform_toolsets:\s*$", line):
            return i
    return None


def _pb_parse_toolsets(text):
    """{platform: [names]} parsed out of the `platform_toolsets:` block.

    A platform present with no list reads as []; a platform absent is absent.
    Returns {} when the block is missing. PURE, so the harness can drive every
    shape without a config file.
    """
    out = {}
    lines = text.splitlines()
    start = _pb_find_block(lines)
    if start is None:
        return out
    end = _pb_block_end(lines, start)
    child = _pb_child_indent(lines, start, end)
    if child is None:
        return out
    plat = None
    for i in range(start + 1, end):
        line = lines[i]
        if _pb_skippable(line):
            continue
        ind = _pb_indent_of(line)
        if ind == child:                             # a platform key
            m = _pb_re.match(r"^\s*([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                plat = m.group(1)
                out.setdefault(plat, [])
                continue
            # an inline scalar (`cli: something`) — record that the key exists,
            # but never as a list we would round-trip
            m = _pb_re.match(r"^\s*([A-Za-z0-9_.-]+):\s*\S", line)
            if m:
                out.setdefault(m.group(1), [])
            plat = None
            continue
        if ind > child and plat is not None:
            m = _pb_re.match(r"^\s+-\s*(.+?)\s*$", line)
            if m:
                out[plat].append(m.group(1).strip().strip('"\''))
    return out


def _pb_read_toolsets():
    try:
        with open(_PB_CFG, encoding="utf-8") as fh:
            return _pb_parse_toolsets(fh.read())
    except OSError:
        return {}


def _pb_render(platform, items, indent=2):
    pad = " " * indent
    return ("%s%s:\n" % (pad, platform)) + "".join("%s  - %s\n" % (pad, t)
                                                   for t in items)


def _pb_apply_text(src, platform, items):
    """Pure: return `src` with platform_toolsets.<platform> set to `items`
    (None removes the platform). Byte-identical when nothing changes."""
    lines = src.splitlines(True)
    start = _pb_find_block(lines)
    if start is None:
        if items is None:
            return src
        tail = "" if (not src or src.endswith("\n")) else "\n"
        return src + tail + "platform_toolsets:\n" + _pb_render(platform, items)

    end = _pb_block_end(lines, start)
    child = _pb_child_indent(lines, start, end)
    ind = 2 if child is None else child

    # locate this platform at the block's own child indent
    p_at = None
    for i in range(start + 1, end):
        if _pb_skippable(lines[i]):
            continue
        if _pb_indent_of(lines[i]) != ind:
            continue
        if _pb_re.match(r"^\s*%s:\s*($|\S)" % _pb_re.escape(platform), lines[i]):
            p_at = i
            break

    if p_at is None:
        if items is None:
            return src
        # first key in the block, so the entry a reader is looking for is the
        # one at the top rather than buried under eleven chat platforms
        out = lines[:start + 1] + [_pb_render(platform, items, ind)] + \
            lines[start + 1:]
        return "".join(out)

    # One PAST the last line that genuinely belongs to this platform. Comments
    # and blanks are not boundaries, but they are not claimed either: a trailing
    # `# note` after the last item stays outside the replaced range and
    # survives, while the platform's own items are all rewritten.
    p_end = p_at + 1
    for i in range(p_at + 1, end):
        if _pb_skippable(lines[i]):
            continue                                 # decide on the next line
        if _pb_indent_of(lines[i]) <= ind:
            break                                    # a sibling platform key
        p_end = i + 1
    repl = [] if items is None else [_pb_render(platform, items, ind)]
    return "".join(lines[:p_at] + repl + lines[p_end:])


def _pb_backup():
    """Timestamped copy next to the config, 0600. Returns its path."""
    stamp = _pb_datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-promptbudget-%s" % (_PB_CFG, stamp)
    _pb_shutil.copy2(_PB_CFG, dst)
    try:
        _pb_os.chmod(dst, 0o600)
    except OSError:
        pass
    return dst


@_pb_contextlib.contextmanager
def _pb_config_lock(timeout=_PB_LOCK_TIMEOUT):
    """`flock(LOCK_EX)` on `config.yaml.lock`, the SAME path
    hermes-plugins/plugin_enable.py locks, so the dashboard's two config
    editors and update.sh's `plugin_enable.py` run cannot interleave.

    Advisory, and on a separate file: config.yaml itself is replaced by
    `os.replace`, so a lock on its inode would stop guarding the path the
    moment the first writer finished.
    """
    fd = _pb_os.open(_PB_LOCK_FILE, _pb_os.O_CREAT | _pb_os.O_RDWR, 0o600)
    try:
        deadline = _pb_time.time() + max(0.0, float(timeout))
        while True:
            try:
                _pb_fcntl.flock(fd, _pb_fcntl.LOCK_EX | _pb_fcntl.LOCK_NB)
                break
            except OSError:
                if _pb_time.time() >= deadline:
                    raise TimeoutError(
                        "another process is still writing %s (waited %gs)"
                        % (_PB_CFG, timeout))
                _pb_time.sleep(0.05)
        try:
            yield
        finally:
            try:
                _pb_fcntl.flock(fd, _pb_fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            _pb_os.close(fd)
        except OSError:
            pass


def _pb_atomic_write(path, text, mode):
    """Replace `path` through a UNIQUELY named temp file in the same directory.

    The old fixed `.promptbudget.tmp` name was the concurrency bug: two writers
    either clobbered each other's half-written bytes or raced `os.replace` into
    a FileNotFoundError. O_EXCL + pid + a random token cannot collide, and the
    temp file is 0600 from birth.
    """
    directory = _pb_os.path.dirname(path) or "."
    token = _pb_binascii.hexlify(_pb_os.urandom(4)).decode("ascii")
    tmp = _pb_os.path.join(directory, "%s.tmp-%d-%s"
                           % (_pb_os.path.basename(path), _pb_os.getpid(), token))
    fd = _pb_os.open(tmp, _pb_os.O_CREAT | _pb_os.O_EXCL | _pb_os.O_WRONLY, 0o600)
    try:
        with _pb_os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            _pb_os.fsync(fh.fileno())
        try:
            _pb_os.chmod(tmp, mode)
        except OSError:
            pass
        _pb_os.replace(tmp, path)
    except BaseException:
        try:
            _pb_os.unlink(tmp)
        except OSError:
            pass
        raise


def _pb_fmt_sel(sel):
    """A selection as one short string for the log line."""
    if sel is None:
        return "(inherit)"
    return ",".join(sel) if sel else "(empty)"


def _pb_write_toolsets(platform, items):
    """Set (or with items=None remove) platform_toolsets.<platform>.

    Returns (changed, backup_path). A no-op write touches nothing at all — not
    the file, not a backup — so the config stays byte-identical.

    The read -> apply -> replace is one critical section under BOTH server.py's
    `_state_lock` (this process) and the config flock (every process), so a
    second writer sees this edit rather than the text we started from.
    """
    with _state_lock:                                        # noqa: F821
        with _pb_config_lock():
            with open(_PB_CFG, encoding="utf-8") as fh:
                src = fh.read()
            new = _pb_apply_text(src, platform, items)
            if new == src:
                return False, None
            before = _pb_parse_toolsets(src).get(platform)   # None when absent
            bak = _pb_backup()
            try:
                mode = _pb_os.stat(_PB_CFG).st_mode & 0o777
            except OSError:
                mode = 0o600
            _pb_atomic_write(_PB_CFG, new, mode)
    # Always leave a trace, the way _cb_set_escalation does: this rewrites the
    # tool list of every future conversation, and "why does the agent not have
    # the browser any more" must be answerable from the log.
    try:
        print("[aux_promptbudget] platform_toolsets.%s %s -> %s"
              % (platform, _pb_fmt_sel(before), _pb_fmt_sel(items)), flush=True)
    except Exception:
        pass
    return True, bak


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------
def _pb_est(json_bytes, prompt_size):
    """Estimated tokens and cold-prefill seconds for a whole fresh prefix."""
    sp = ((prompt_size or {}).get("system_prompt") or {}).get("bytes") or 0
    total = int(sp) + int(json_bytes or 0)
    tokens = int(round(total / _PB_BYTES_PER_TOKEN))
    return {"prefix_bytes": total, "est_tokens": tokens,
            "est_seconds": round(tokens / _PB_PREFILL_TOK_S, 1)}


def _pb_shape(scn, prompt_size, full=None):
    """One profile's numbers. `full` is the Full scenario, so a saving can be
    quoted against a figure computed the same way — mixing the estimate with
    the model server's measured 20,622 would make Full itself read as -2.5%."""
    if not scn or scn.get("error"):
        return {"error": (scn or {}).get("error") or "not measured"}
    out = {
        "tool_count": scn.get("count"),
        "tools_json_bytes": scn.get("json_bytes"),
        "tools_kb": round((scn.get("json_bytes") or 0) / 1024.0, 1),
        "toolsets": scn.get("toolsets"),
        "tools": scn.get("names"),
    }
    out.update(_pb_est(scn.get("json_bytes"), prompt_size))
    base = _pb_est((full or {}).get("json_bytes"), prompt_size)["est_tokens"] \
        if full else out["est_tokens"]
    if base:
        out["pct_saved"] = round(100.0 * (base - out["est_tokens"]) / base, 1)
        out["pct_of_measured_baseline"] = round(
            100.0 * out["est_tokens"] / _PB_BASELINE_TOKENS, 1)
    return out


def _pb_strip_emoji(label):
    """CONFIGURABLE_TOOLSETS labels are emoji-prefixed for the CLI. The
    dashboard has a zero-emoji design law, so strip anything that is not word
    text off the front (hermes_cli.tools_config.gui_toolset_label does the same
    for its own HTTP surfaces)."""
    s = _pb_re.sub(r"^[^\w(]+", "", str(label or "")).strip()
    return s or str(label or "")


def _pb_build(fresh=False, selection_override=None):
    cfg_ts = _pb_read_toolsets()
    current = cfg_ts.get(_PB_PLATFORM_KEY)
    if _PB_PLATFORM_KEY not in cfg_ts:
        current = None

    scenarios = {"current": current, "full": None, "lean": list(_PB_LEAN),
                 "focused": list(_PB_FOCUSED)}
    if selection_override is not None:
        scenarios["preview"] = selection_override

    # SINGLE-FLIGHT. Both measurements start a fresh interpreter that imports
    # the agent's whole tool registry, so they run one at a time whatever the
    # caller count (`_pb_payload` additionally coalesces waiters onto the build
    # that just finished, so N concurrent ?fresh=1 calls cost ONE pair).
    ps, ps_err = None, None
    probe, probe_err = None, None
    with _pb_build_lock:
        try:
            ps = _pb_prompt_size()
        except Exception as e:
            ps_err = "%s: %s" % (type(e).__name__, e)
        try:
            probe = _pb_probe(scenarios)
        except Exception as e:
            probe_err = "%s: %s" % (type(e).__name__, e)

    keys = (probe or {}).get("keys") or list(_PB_KEYS_FALLBACK)
    labels = (probe or {}).get("labels") or {}
    descs = (probe or {}).get("descs") or {}
    per = (probe or {}).get("per_toolset") or {}
    scn = (probe or {}).get("scenarios") or {}

    live = scn.get("current") or {}
    live_keys = set(live.get("toolsets") or [])
    rows = []
    for k in keys:
        p = per.get(k) or {}
        rows.append({
            "key": k,
            "label": _pb_strip_emoji(labels.get(k, k)),
            "detail": str(descs.get(k, "")),
            "tools": p.get("count", 0),
            "bytes": p.get("json_bytes", 0),
            "kb": round((p.get("json_bytes") or 0) / 1024.0, 1),
            "enabled": (k in live_keys),
            "lean": (k in _PB_LEAN),
        })

    # Which profile is live is decided on the RESOLVED tool set, never on the
    # literal config line: `cli: [hermes-cli]` (what install.sh writes) and no
    # `cli` key at all resolve to exactly the same 33 tools, and calling the
    # first one "custom" would be a lie the UI then asks you to fix.
    def _same(a, b):
        return bool(a) and bool(b) and a.get("toolsets") == b.get("toolsets")

    if _same(scn.get("current"), scn.get("full")):
        profile = "full"
    elif _same(scn.get("current"), scn.get("lean")):
        profile = "lean"
    elif _same(scn.get("current"), scn.get("focused")):
        profile = "focused"
    else:
        profile = "custom"

    # The three-profile table, in the order the card lays them out. One row per
    # profile so the UI, the README and the plan doc all quote the same
    # arithmetic instead of three hand-copied sets of numbers.
    profiles = []
    for key in ("full", "lean", "focused"):
        label, gives_up = _PB_PROFILES[key]
        shaped = _pb_shape(scn.get(key), ps, scn.get("full"))
        row = {"key": key, "label": label, "gives_up": gives_up,
               "toolsets": (list(_PB_LEAN) if key == "lean"
                            else list(_PB_FOCUSED) if key == "focused"
                            else None),
               "active": (profile == key)}
        row.update(shaped)
        profiles.append(row)

    sk = (probe or {}).get("skills") or {}
    skills = {
        "index_chars": ((ps or {}).get("skills_index") or {}).get("chars"),
        "index_kb": round((((ps or {}).get("skills_index") or {}).get("bytes") or 0)
                          / 1024.0, 1),
        "configurable": False,
        "note": ("The skills index can be demoted to names-only per category "
                 "(build_skills_system_prompt's compact_categories), and the "
                 "config key agent.coding_context: focus does reach it — but "
                 "only in the coding posture, which needs a code workspace as "
                 "the session cwd. Dashboard sessions run from your home "
                 "folder, where that check is false, so no config path reaches "
                 "this surface today."),
    }
    if not sk.get("error"):
        skills.update({
            "categories": sk.get("categories"),
            "used_categories": sk.get("used_categories"),
            "unused_categories": sk.get("unused_categories"),
            "builder_chars": sk.get("index_chars"),
            "unused_compact_chars": sk.get("unused_compact_chars"),
            "all_compact_chars": sk.get("all_compact_chars"),
        })
        if sk.get("index_chars") and sk.get("unused_compact_chars"):
            saved = int(sk["index_chars"]) - int(sk["unused_compact_chars"])
            skills["headroom_chars"] = saved
            skills["headroom_tokens"] = int(round(saved / _PB_BYTES_PER_TOKEN))

    return {
        "ok": True,
        # A failed probe is NOT a failed request — the card still renders the
        # config and the profile table — but it must never read as a clean
        # measurement either. `prompt_size_ok` false means the system-prompt
        # half was counted as ZERO, so every token/seconds figure below is
        # tool schemas only; `probe_ok` false means the toolset list and every
        # per-toolset size are the fallback constants, not this agent's answer.
        # Both errors are rendered at the top of the card body.
        "prompt_size_ok": ps is not None,
        "probe_ok": probe is not None,
        "degraded": (ps is None) or (probe is None),
        "tokens_are_tools_only": ps is None,
        "generated": int(_pb_time.time()),
        "prompt_size": ps,
        "prompt_size_error": ps_err,
        "prompt_size_note": ("hermes prompt-size reports the tool ceiling, not "
                             "this platform's set: it builds an inspection "
                             "agent with no enabled_toolsets, so its tools "
                             "figure never moves with the config. The tool "
                             "numbers below are measured the same way "
                             "(json.dumps(defs, ensure_ascii=False)) against "
                             "the toolsets a session really resolves."),
        "config_path": _PB_CFG,
        "config_key": "platform_toolsets." + _PB_PLATFORM_KEY,
        "config_note": ("hermes serve resolves a new session's tools through "
                        "_load_enabled_toolsets, whose config fallback reads "
                        "platform_toolsets.cli — the platform key is hardcoded "
                        "and there is no tui entry in PLATFORMS, so a "
                        "platform_toolsets.tui block would be read by nothing. "
                        "The same key also applies to hermes -z one-shot runs "
                        "(briefings, watchtower, the chat fallback) and to "
                        "hermes in a terminal."),
        "selection": current,
        "inherits": profile == "full",
        "profile": profile,
        # `lean` is the wire/config name of the Balanced profile — it shipped
        # under that name and is kept so an older config still resolves.
        "lean_profile": list(_PB_LEAN),
        "focused_profile": list(_PB_FOCUSED),
        "profile_order": ["full", "lean", "focused"],
        "profiles": profiles,
        "baseline_tokens": _PB_BASELINE_TOKENS,
        "toolsets": rows,
        "current": _pb_shape(scn.get("current"), ps, scn.get("full")),
        "full": _pb_shape(scn.get("full"), ps, scn.get("full")),
        "lean": _pb_shape(scn.get("lean"), ps, scn.get("full")),
        "focused": _pb_shape(scn.get("focused"), ps, scn.get("full")),
        "preview": (_pb_shape(scn.get("preview"), ps, scn.get("full"))
                    if selection_override is not None else None),
        "skills": skills,
        "prefill_tok_s": _PB_PREFILL_TOK_S,
        "bytes_per_token": _PB_BYTES_PER_TOKEN,
        # _load_enabled_toolsets is uncached and runs per new session, and
        # load_config() is memoised on the file's (mtime_ns, size) — so the next
        # NEW conversation already gets the new list. Open ones keep the agent
        # they were built with.
        "restart_required": False,
        "restart_note": ("New conversations pick this up immediately. "
                         "Conversations already open keep the tools they "
                         "started with until you restart the agent service."),
        "probe_error": probe_err,
    }


def _pb_payload(fresh=False):
    asked = _pb_time.time()
    with _pb_lock:
        cached = _pb_cache["payload"]
        if cached and (asked - _pb_cache["at"]) < _PB_CACHE_TTL and not fresh:
            return cached
        # COALESCE. We may have queued behind another caller's build; if one
        # FINISHED after we asked, its answer is at least as fresh as the one
        # we would produce, so take it rather than spawning a second pair of
        # interpreters. This is what keeps N concurrent `?fresh=1` calls to one
        # measurement instead of N.
        if cached and _pb_cache["at"] >= asked:
            return cached
        p = _pb_build()
        _pb_cache["at"] = _pb_time.time()
        _pb_cache["payload"] = p
        return p


def _pb_invalidate():
    with _pb_lock:
        _pb_cache["at"] = 0.0
        _pb_cache["payload"] = None


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def _pb_get(ctx):
    fresh = (ctx.q1("fresh", "") or "").strip().lower() in ("1", "true", "yes")
    try:
        return _pb_payload(fresh)
    except Exception as e:
        return {"ok": False,
                "error": "prompt budget could not be measured — %s: %s"
                         % (type(e).__name__, e)}, 500


def _pb_restart_serve():
    """launchctl kickstart -k on the serve gateway. Only ever from an explicit
    {"restart": true} — it interrupts any agent turn in flight."""
    try:
        uid = _pb_os.getuid()
        r = _pb_subprocess.run(
            ["launchctl", "kickstart", "-k", "gui/%d/com.hermes.serve" % uid],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout or "").strip()[:200]
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def _pb_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}

    profile = body.get("profile")
    has_ts = "toolsets" in body
    if profile is not None and has_ts:
        return {"ok": False,
                "error": "send either toolsets or profile, not both"}, 400
    if profile is None and not has_ts:
        return {"ok": False,
                "error": "send {\"toolsets\": [...]} or "
                         "{\"profile\": \"lean\"|\"full\"}"}, 400

    if profile is not None:
        p = str(profile).strip().lower()
        if p in ("lean", "balanced"):     # "lean" is the wire name of Balanced
            want = list(_PB_LEAN)
        elif p == "focused":
            want = list(_PB_FOCUSED)
        elif p in ("full", "all", "default"):
            # The composite name, not a removed key: it is exactly what
            # install.sh ships, it resolves identically to an absent key, and
            # writing it means going back to Full is a no-op on a stock config
            # instead of a diff. `{"toolsets": null}` still removes the key for
            # anyone who wants the implicit inherit.
            want = ["hermes-cli"]
        else:
            return {"ok": False,
                    "error": "unknown profile %r — use full, lean "
                             "(Balanced) or focused" % profile}, 400
    else:
        raw = body.get("toolsets")
        if raw is None:
            want = None
        elif isinstance(raw, list):
            want = []
            for item in raw:
                if not isinstance(item, str):
                    return {"ok": False,
                            "error": "toolsets must be a list of strings"}, 400
                s = item.strip()
                if s and s not in want:
                    want.append(s)
            if not want:
                return {"ok": False,
                        "error": "at least one toolset must stay on — send "
                                 "profile \"full\" to go back to the default"}, 400
        else:
            return {"ok": False, "error": "toolsets must be a list or null"}, 400

    before = _pb_payload(fresh=True)

    if want is not None:
        keys = set()
        for row in (before.get("toolsets") or []):
            keys.add(row.get("key"))
        if not keys:
            keys = set(_PB_KEYS_FALLBACK)
        # plus the composite the platform defaults to, so "go back to Full" and
        # a hand-written {"toolsets":["hermes-cli"]} are both accepted
        keys.add("hermes-cli")
        bad = [t for t in want if t not in keys]
        if bad:
            return {"ok": False,
                    "error": "not a configurable toolset: %s" % ", ".join(sorted(bad)),
                    "valid": sorted(keys)}, 400

    try:
        changed, backup = _pb_write_toolsets(_PB_PLATFORM_KEY, want)
    except OSError as e:
        return {"ok": False,
                "error": "could not write %s — %s" % (_PB_CFG, e)}, 500

    restarted, restart_error = False, ""
    if changed and body.get("restart") is True:
        restarted, restart_error = _pb_restart_serve()

    _pb_invalidate()
    after = _pb_payload(fresh=True)

    return {
        "ok": True,
        "changed": changed,
        "backup": backup,
        "config_path": _PB_CFG,
        "config_key": "platform_toolsets." + _PB_PLATFORM_KEY,
        "selection": after.get("selection"),
        "profile": after.get("profile"),
        "restarted": restarted,
        "restart_error": restart_error,
        "restart_required": False,
        "restart_note": after.get("restart_note"),
        "before": before.get("current"),
        "after": after.get("current"),
        "budget": after,
    }


# ---------------------------------------------------------------------------
# the surface aux_onboarding.py talks to
# ---------------------------------------------------------------------------
# Both are resolved BY NAME AT CALL TIME through aux_onboarding's `_onb_g()`,
# which is what makes them safe across the sorted exec order (aux_onboarding
# runs first and this module does not exist while its body runs — by request
# time it does).  They are deliberately CHEAP: no subprocess, no probe.  A
# first-run sheet must not pay two seconds of tool-registry import to render a
# radio button.

def prompt_budget_profile():
    """"full" | "lean" (Balanced) | "focused" | "custom", read straight off
    config.yaml.

    Literal, not resolved: `[hermes-cli]` and an absent key both mean full,
    which is the only distinction a preferences read needs."""
    ts = _pb_read_toolsets()
    cur = ts.get(_PB_PLATFORM_KEY)
    if _PB_PLATFORM_KEY not in ts or cur == ["hermes-cli"]:
        return "full"
    if sorted(cur or []) == sorted(_PB_LEAN):
        return "lean"
    if sorted(cur or []) == sorted(_PB_FOCUSED):
        return "focused"
    return "custom"


def set_prompt_budget_profile(profile):
    """Write the full, lean (Balanced) or focused profile. Returns True when
    the file changed.

    A no-op stays a genuine no-op — no write, no backup — so a first-run sheet
    that ships `prompt_budget: "full"` on a stock config leaves it untouched."""
    p = str(profile or "").strip().lower()
    if p in ("lean", "balanced"):
        want = list(_PB_LEAN)
    elif p == "focused":
        want = list(_PB_FOCUSED)
    elif p in ("full", "all", "default"):
        want = ["hermes-cli"]
    else:
        raise ValueError("unknown prompt budget profile %r" % profile)
    changed, _bak = _pb_write_toolsets(_PB_PLATFORM_KEY, want)
    if changed:
        _pb_invalidate()
    return changed


register_get("/api/prompt/budget", _pb_get)     # noqa: F821 (server.py)
register_post("/api/prompt/budget", _pb_post)   # noqa: F821 (server.py)
