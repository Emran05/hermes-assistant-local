# aux_toolbudget.py — "Tool output budget": the cap on how much of a single
# tool result reaches the model, plus the switch that installs and enables it.
#
#   GET  /api/tool/budget   -> {installed, enabled_in_config, settings, stats}
#   POST /api/tool/budget   -> {"enabled"?, "max_chars"?, "spill"?}
#                              (+ "install": true  — link the plugin + enable it)
#                              (+ "restart": true  — kickstart com.hermes.serve)
#
# WHY THIS EXISTS.  Prompt budget (aux_promptbudget.py) measures the FIXED
# prefix every fresh conversation pays.  This is the other half: the VARIABLE
# cost, paid per tool call, for the rest of the conversation.  Every tool result
# is appended to the transcript verbatim — `model_tools.py` hands the tool's
# string straight to `make_tool_result_message` — so one `read_file` of a build
# log or one chatty `terminal` command can spend a quarter of a 65,536-token
# window in a single turn, and every later turn re-prefills it.
#
# WHAT THE RUNTIME ALREADY DOES (measured in ~/.hermes/hermes-agent, not
# assumed).  Three tools cap themselves, with three different numbers and three
# different config keys, and nothing else caps at all:
#   * `terminal`    — 50,000 chars, 40 % head / 60 % tail
#                     (tools/terminal_tool.py:2669 via tools/tool_output_limits.py
#                     `tool_output.max_bytes`, DEFAULT_MAX_BYTES = 50_000)
#   * `read_file`   — 100,000 chars (tools/file_tools.py:59
#                     `_DEFAULT_MAX_READ_CHARS`, config `file_read_max_chars`),
#                     plus 2,000-line / 2,000-char-per-line pagination
#   * `web_extract` — 15,000 chars with the full text stored on disk
#                     (tools/web_tools.py:403 `DEFAULT_EXTRACT_CHAR_LIMIT`,
#                     config `web.extract_char_limit`)
# `search_files`, `web_search`, `session_search`, `process`, `execute_code`,
# `memory`, every MCP tool and every plugin tool are UNCAPPED.  And even the
# capped ones are sized per tool rather than against the window: 100,000 chars
# is ~28,000 tokens, 42 % of this machine's context, in one result.
#
# So the plugin is not a duplicate of those caps — it is the global floor
# underneath them, applied at the one seam every tool passes through
# (`transform_tool_result`), expressed as a share of the context window.
#
# THIS MODULE DOES NOT IMPLEMENT THE CAP.  The cap lives in the agent's own
# process: hermes-plugins/tool-budget/ in this repo, linked into
# ~/.hermes/plugins/tool-budget.  The dashboard only (a) installs and enables
# it, (b) writes its three knobs into settings.json, which the plugin re-reads
# on every over-budget call, and (c) reports what it saved.  Nothing is imported
# in either direction between this module and the plugin — the plugin is
# stdlib-only, with no knowledge of the dashboard, so it loads in `hermes
# serve`, `hermes -z` and a plain terminal alike; settings.json is the whole
# interface.  (The config-editing helper in hermes-plugins/plugin_enable.py IS
# shared, deliberately, with install.sh and update.sh — see below.)
#
# PLUGINS LOAD AT PROCESS START, so enabling one needs a one-time restart of
# com.hermes.serve.  Changing max_chars/enabled/spill does NOT — the plugin
# reads settings.json fresh.  The card says which is which; the restart is
# opt-in (`{"restart": true}`), exactly as aux_promptbudget.py does it.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an
# aux module — it rebinds the shared global to the class.  Private alias only.
#
# LOAD ORDER.  aux files exec SORTED, so this runs after aux_promptbudget and
# before aux_update/aux_watchtower.  Nothing here reads a foreign global at
# module load: HOME/HERE/DATA/SETTINGS_FILE/_state_lock are server.py's own
# (defined before any aux exec), everything else is resolved inside a request.
import json as _tb_json
import os as _tb_os
import re as _tb_re
import shutil as _tb_shutil
import subprocess as _tb_subprocess
import datetime as _tb_datetime

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
_TB_NAME = "tool-budget"
_TB_CFG = _tb_os.path.join(HOME, ".hermes", "config.yaml")          # noqa: F821
_TB_PLUGIN_DIR = _tb_os.path.join(HOME, ".hermes", "plugins")       # noqa: F821
_TB_PLUGIN_DST = _tb_os.path.join(_TB_PLUGIN_DIR, _TB_NAME)
_TB_PLUGIN_SRC = _tb_os.path.join(_tb_os.path.dirname(HERE),        # noqa: F821
                                  "hermes-plugins", _TB_NAME)
_TB_LOG = _tb_os.path.join(DATA, "tool-budget.jsonl")               # noqa: F821
_TB_SPILL = _tb_os.path.join(DATA, "spill")                         # noqa: F821
_TB_ENABLED_AT = _tb_os.path.join(DATA, "tool-budget-enabled-at")   # noqa: F821

# The plugin's own defaults, restated here so the card can render before the
# key has ever been written. Keep the two in step.
_TB_DEFAULTS = {"enabled": True, "max_chars": 24000, "spill": True}

# The validated range. The floor is also the plugin's fast-path threshold: a
# result at or below it can never be over budget, so it is returned without
# any I/O at all.
_TB_MIN_CHARS = 4000
_TB_MAX_CHARS = 200000

# Same ratio aux_promptbudget.py measured against the model server's own
# prompt_tokens lines. Tool output is more prose-like than tool schemas, so
# this is if anything conservative.
_TB_CHARS_PER_TOKEN = 3.6

# The window the percentages are quoted against — read from the live config so
# the card is honest on a machine configured differently, with this Mac's
# value as the fallback.
_TB_CTX_FALLBACK = 65536

# The four choices the card offers, as chars.
_TB_CHOICES = (8000, 16000, 24000, 48000)

_TB_LOG_TAIL_BYTES = 512 * 1024   # enough for weeks of truncations


# ---------------------------------------------------------------------------
# config.yaml — read + the one edit (plugins.enabled)
# ---------------------------------------------------------------------------
# The editor itself lives in hermes-plugins/plugin_enable.py — ONE
# implementation, shared with install.sh and update.sh, because a YAML edit
# written twice is a YAML edit that drifts. It is loaded by path (the file
# ships in this repo, next to the plugin the dashboard installs) and every
# name below is just a local alias so this module reads the same as before.
def _tb_load_helper():
    import importlib.util as _il
    path = _tb_os.path.join(_tb_os.path.dirname(HERE),                 # noqa: F821
                            "hermes-plugins", "plugin_enable.py")
    spec = _il.spec_from_file_location("hermes_plugin_enable", path)
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A missing or broken helper must cost this ONE card, not the module's routes:
# server.py's aux loader catches a raising file, but it drops every route in it.
try:
    _TB_HELPER = _tb_load_helper()
    _TB_HELPER_ERR = ""
except Exception as _tb_e:                                             # noqa: F841
    _TB_HELPER = None
    _TB_HELPER_ERR = ("hermes-plugins/plugin_enable.py could not be loaded "
                      "(%s) — this install looks incomplete; re-run "
                      "./update.sh" % type(_tb_e).__name__)


def _tb_read_enabled(src):
    if not _TB_HELPER:
        raise RuntimeError(_TB_HELPER_ERR)
    return _TB_HELPER.read_enabled(src)


def _tb_apply_enable(src, name):
    if not _TB_HELPER:
        raise RuntimeError(_TB_HELPER_ERR)
    return _TB_HELPER.apply_enable(src, name)


def _tb_enable_in_config():
    """Add tool-budget to plugins.enabled exactly once, through the shared
    helper (timestamped 0600 backup, atomic replace, original mode kept).
    Returns (changed, backup_path); a no-op writes nothing at all.

    Under server.py's `_state_lock` as well as the helper's own cross-process
    flock on `config.yaml.lock`: the flock is what stops update.sh racing us,
    the process lock is what stops two dashboard requests racing each other
    before either reaches the flock."""
    if not _TB_HELPER:
        raise RuntimeError(_TB_HELPER_ERR)
    with _state_lock:                                                  # noqa: F821
        return _TB_HELPER.enable(_TB_CFG, _TB_NAME, tag="toolbudget")


def _tb_enabled_in_config():
    """True / False / **None**.

    None means UNKNOWN, and it is not a formality: without the helper this
    returned [] from `_tb_read_enabled`, which read as "definitely not
    enabled", so a broken install rendered the card's setup phase — an Install
    button offered on a state nobody had actually read — and `helper_error`
    was never shown. Unknown must stay unknown all the way to the UI."""
    if not _TB_HELPER:
        return None
    try:
        with open(_TB_CFG, encoding="utf-8") as fh:
            return _TB_NAME in _tb_read_enabled(fh.read())
    except FileNotFoundError:
        return False            # no config at all: it is certainly not on
    except Exception:
        return None             # unreadable / unparseable: we do not know


def _tb_context_length():
    """`model.context_length` off the live config, for the %-of-window label."""
    try:
        with open(_TB_CFG, encoding="utf-8") as fh:
            section = None
            for line in fh:
                if _tb_re.match(r"^\S", line):
                    m = _tb_re.match(r"^(\w+):\s*$", line)
                    section = m.group(1) if m else None
                    continue
                if section == "model":
                    m = _tb_re.match(r"^\s+context_length:\s*(\d+)", line)
                    if m:
                        return int(m.group(1))
    except (OSError, ValueError):
        pass
    return _TB_CTX_FALLBACK


# ---------------------------------------------------------------------------
# install (link the plugin into ~/.hermes/plugins)
# ---------------------------------------------------------------------------
def _tb_install_state():
    """What is on disk. `linked` distinguishes a symlink back into the repo
    (so `git pull` updates the plugin) from a copy (which does not)."""
    st = {"present": False, "linked": False, "target": "", "stale": False,
          "src_present": False, "src": _TB_PLUGIN_SRC, "dst": _TB_PLUGIN_DST}
    try:
        st["src_present"] = _tb_os.path.isfile(
            _tb_os.path.join(_TB_PLUGIN_SRC, "__init__.py"))
        if _tb_os.path.islink(_TB_PLUGIN_DST):
            st["linked"] = True
            st["target"] = _tb_os.path.realpath(_TB_PLUGIN_DST)
        st["present"] = _tb_os.path.isfile(
            _tb_os.path.join(_TB_PLUGIN_DST, "__init__.py"))
        if st["present"] and not st["linked"] and st["src_present"]:
            a = _tb_os.path.getmtime(_tb_os.path.join(_TB_PLUGIN_SRC, "__init__.py"))
            b = _tb_os.path.getmtime(_tb_os.path.join(_TB_PLUGIN_DST, "__init__.py"))
            st["stale"] = a > b + 1
    except OSError:
        pass
    return st


def _tb_install_plugin():
    """Symlink ~/.hermes/plugins/tool-budget -> <repo>/hermes-plugins/tool-budget,
    falling back to a copy when a link cannot be made. Idempotent: an existing
    link that already points at the repo is left alone."""
    if not _tb_os.path.isfile(_tb_os.path.join(_TB_PLUGIN_SRC, "__init__.py")):
        return False, "the plugin is missing from this install (%s)" % _TB_PLUGIN_SRC
    try:
        _tb_os.makedirs(_TB_PLUGIN_DIR, exist_ok=True)
    except OSError as e:
        return False, "could not create %s — %s" % (_TB_PLUGIN_DIR, e)

    try:
        if _tb_os.path.islink(_TB_PLUGIN_DST):
            if _tb_os.path.realpath(_TB_PLUGIN_DST) == _tb_os.path.realpath(_TB_PLUGIN_SRC):
                return True, ""                     # already linked correctly
            _tb_os.unlink(_TB_PLUGIN_DST)
        elif _tb_os.path.isdir(_TB_PLUGIN_DST):
            # A previous copy. Replace it, keeping a dated aside rather than
            # deleting anything the owner might have edited by hand.
            aside = _TB_PLUGIN_DST + ".old-" + _tb_datetime.datetime.now().strftime(
                "%Y%m%d-%H%M%S")
            _tb_os.rename(_TB_PLUGIN_DST, aside)
        _tb_os.symlink(_TB_PLUGIN_SRC, _TB_PLUGIN_DST)
        return True, ""
    except OSError:
        pass
    try:                                            # fallback: a real copy
        if _tb_os.path.isdir(_TB_PLUGIN_DST):
            _tb_shutil.rmtree(_TB_PLUGIN_DST)
        _tb_shutil.copytree(_TB_PLUGIN_SRC, _TB_PLUGIN_DST,
                            ignore=_tb_shutil.ignore_patterns("__pycache__"))
        return True, ""
    except OSError as e:
        return False, "could not install the plugin — %s" % e


def _tb_busy_jobs():
    """Is a chat turn running right now?

    The same predicate aux_evals.py's `_ev_busy_jobs()` uses, reimplemented
    rather than imported: aux modules exec into one shared globals dict in
    sorted order, so importing across them would couple this card's routes to
    another module's load order for three lines of code. `CHAT_JOBS` is
    server.py's own and is resolved by NAME at call time."""
    try:
        return any(not v.get("done")
                   for v in list(CHAT_JOBS.values()))                  # noqa: F821
    except Exception:
        return False


def _tb_restart_serve():
    """launchctl kickstart -k on the serve gateway. Only ever from an explicit
    {"restart": true}, and never while a turn is in flight (`_tb_post` refuses
    first) — it interrupts any agent turn it lands on."""
    try:
        uid = _tb_os.getuid()
        r = _tb_subprocess.run(
            ["launchctl", "kickstart", "-k", "gui/%d/com.hermes.serve" % uid],
            capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        err = "" if ok else (r.stderr or r.stdout or "").strip()[:200]
    except Exception as e:
        ok, err = False, "%s: %s" % (type(e).__name__, e)
    try:
        print("[aux_toolbudget] restart com.hermes.serve -> %s%s"
              % ("ok" if ok else "FAILED", (": " + err) if err else ""),
              flush=True)
    except Exception:
        pass
    return ok, err


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def _tb_settings():
    """The three knobs, defaults filled in. Same validation the plugin
    applies, so the card never shows a value the plugin would ignore."""
    out = dict(_TB_DEFAULTS)
    try:
        raw = (get_settings() or {}).get("tool_budget")                # noqa: F821
    except Exception:
        raw = None
    if isinstance(raw, dict):
        if isinstance(raw.get("enabled"), bool):
            out["enabled"] = raw["enabled"]
        if isinstance(raw.get("spill"), bool):
            out["spill"] = raw["spill"]
        mc = raw.get("max_chars")
        if isinstance(mc, (int, float)) and not isinstance(mc, bool):
            mc = int(mc)
            if _TB_MIN_CHARS <= mc <= _TB_MAX_CHARS:
                out["max_chars"] = mc
    return out


def _tb_write_settings(patch):
    """Fresh read-modify-write under the shared lock, the way
    set_prewarm_enabled does it — never a cached copy."""
    with _state_lock:                                                  # noqa: F821
        s = get_settings() or {}                                       # noqa: F821
        cur = s.get("tool_budget")
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(patch)
        s["tool_budget"] = cur
        write_json(SETTINGS_FILE, s)                                   # noqa: F821
    return _tb_settings()


# ---------------------------------------------------------------------------
# stats — read back off the plugin's own log
# ---------------------------------------------------------------------------
def _tb_log_rows():
    """The tail of tool-budget.jsonl as dicts. A half-written first line is
    dropped, a malformed line is skipped, a missing file is []."""
    rows = []
    try:
        size = _tb_os.path.getsize(_TB_LOG)
    except OSError:
        return rows
    try:
        with open(_TB_LOG, "rb") as fh:
            if size > _TB_LOG_TAIL_BYTES:
                fh.seek(size - _TB_LOG_TAIL_BYTES)
                fh.readline()                       # drop the partial line
            blob = fh.read()
    except OSError:
        return rows
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _tb_json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _tb_dir_bytes(path):
    total = 0
    try:
        for d, _dirs, files in _tb_os.walk(path):
            for f in files:
                try:
                    total += _tb_os.path.getsize(_tb_os.path.join(d, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _tb_stats(rows):
    today = _tb_datetime.date.today().isoformat()
    agg = {"calls": 0, "kept_chars": 0, "omitted_chars": 0,
           "est_tokens_saved": 0, "spills": 0, "tools": {}}
    for r in rows:
        if not str(r.get("ts") or "").startswith(today):
            continue
        agg["calls"] += 1
        agg["kept_chars"] += int(r.get("kept_chars") or 0)
        agg["omitted_chars"] += int(r.get("omitted_chars") or 0)
        agg["est_tokens_saved"] += int(
            r.get("est_tokens_saved")
            or round((r.get("omitted_chars") or 0) / _TB_CHARS_PER_TOKEN))
        if r.get("spill_path"):
            agg["spills"] += 1
        t = str(r.get("tool") or "?")
        agg["tools"][t] = agg["tools"].get(t, 0) + 1
    # the original size today's results would have been, for "kept X of Y"
    agg["orig_chars"] = agg["kept_chars"] + agg["omitted_chars"]

    last = None
    if rows:
        r = rows[-1]
        last = {
            "ts": r.get("ts") or "",
            "tool": r.get("tool") or "",
            "kept_chars": int(r.get("kept_chars") or 0),
            "omitted_chars": int(r.get("omitted_chars") or 0),
            "est_tokens_saved": int(r.get("est_tokens_saved") or 0),
            "spill_path": r.get("spill_path") or "",
        }
    return {"today": agg, "last": last, "rows_seen": len(rows),
            "spill_bytes": _tb_dir_bytes(_TB_SPILL),
            "log": _TB_LOG, "spill_dir": _TB_SPILL}


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------
def _tb_choices(ctx_len):
    out = []
    for c in _TB_CHOICES:
        toks = int(round(c / _TB_CHARS_PER_TOKEN))
        out.append({
            "chars": c,
            "est_tokens": toks,
            "pct_window": round(100.0 * toks / max(1, ctx_len), 1),
        })
    return out


def _tb_payload():
    inst = _tb_install_state()
    ctx_len = _tb_context_length()
    s = _tb_settings()
    enabled_cfg = _tb_enabled_in_config()
    rows = _tb_log_rows()
    observed = _tb_observed(rows)
    # enabled_cfg is True / False / None (unknown — see _tb_enabled_in_config).
    # Every derived flag below has to keep the third state distinct: "we could
    # not read it" must never collapse into "it is off", which is what made the
    # card offer an Install button over a state nobody had read.
    known = enabled_cfg is not None
    return {
        "ok": True,
        "installed": bool(inst.get("present")),
        "install": inst,
        "enabled_in_config": enabled_cfg,
        "config_state_known": known,
        "active": bool(inst.get("present")) and enabled_cfg is True
        and s["enabled"],
        "settings": s,
        "defaults": dict(_TB_DEFAULTS),
        "limits": {"min_chars": _TB_MIN_CHARS, "max_chars": _TB_MAX_CHARS},
        "choices": _tb_choices(ctx_len),
        "context_length": ctx_len,
        "chars_per_token": _TB_CHARS_PER_TOKEN,
        "est_tokens": int(round(s["max_chars"] / _TB_CHARS_PER_TOKEN)),
        "pct_window": round(100.0 * (s["max_chars"] / _TB_CHARS_PER_TOKEN)
                            / max(1, ctx_len), 1),
        "stats": _tb_stats(rows),
        "observed": observed,
        "enabled_at": _tb_enabled_at(),
        # Plugins are discovered when the process starts, so turning the plugin
        # ON needs one restart. The three knobs do not — the plugin re-reads
        # settings.json on every over-budget call.
        "restart_required": bool(inst.get("present") and enabled_cfg is True
                                 and not observed
                                 and not _tb_serve_started_after(_tb_enabled_at())),
        "restart_note": ("Plugins load when the agent service starts, so a "
                         "newly enabled budget applies from the next restart "
                         "of the agent backend. Changing the size, or turning "
                         "it off, takes effect on the very next tool call."),
        "config_path": _TB_CFG,
        "config_key": "plugins.enabled",
        "helper_error": _TB_HELPER_ERR,
    }


def _tb_mark_enabled():
    """Stamp the moment the config entry was added, so "has this actually
    loaded yet?" can be answered without asking launchd anything."""
    try:
        with open(_TB_ENABLED_AT, "w", encoding="utf-8") as fh:
            fh.write(_tb_datetime.datetime.now().isoformat(timespec="seconds"))
        _tb_os.chmod(_TB_ENABLED_AT, 0o600)
    except OSError:
        pass


def _tb_enabled_at():
    try:
        with open(_TB_ENABLED_AT, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


_tb_serve_cache = {"t": 0.0, "v": None}


def _tb_serve_started_after(stamp):
    """Did `hermes serve` start after the config entry was written? One
    pgrep + ps, cached 30 s — enough to stop the card asking for a restart
    that already happened before the first truncation shows up in the log."""
    import subprocess, time as _t
    if not stamp:
        return False
    now = _t.time()
    if now - _tb_serve_cache["t"] < 30 and _tb_serve_cache["v"] is not None:
        return _tb_serve_cache["v"] >= stamp
    started = ""
    try:
        pids = subprocess.run(["/usr/bin/pgrep", "-f", "hermes serve"], capture_output=True,
                              text=True, timeout=3).stdout.split()
        if pids:
            out = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", pids[0]],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            if out:
                started = _tb_datetime.datetime.strptime(out, "%a %b %d %H:%M:%S %Y").isoformat(timespec="seconds")
    except Exception:
        started = ""
    _tb_serve_cache.update(t=now, v=started)
    return bool(started) and started >= stamp


def _tb_observed(rows):
    """Has the plugin done anything since the config entry was written?

    There is no RPC that asks a running `hermes serve` which plugins it
    loaded, and shelling out to launchctl/ps on every poll is not worth it.
    So the honest signal is evidence: a truncation logged at or after the
    moment we enabled it means the restart has happened and the plugin is
    live. No evidence means "not observed yet" — which the card words as a
    reminder to restart, never as a failure.
    """
    stamp = _tb_enabled_at()
    if not stamp:
        return bool(rows)
    for r in reversed(rows):
        if str(r.get("ts") or "") >= stamp:
            return True
    return False


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def _tb_get(ctx):
    try:
        return _tb_payload()
    except Exception as e:
        return {"ok": False,
                "error": "tool budget could not be read — %s: %s"
                         % (type(e).__name__, e)}, 500


def _tb_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    patch = {}

    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            return {"ok": False, "error": "enabled must be true or false"}, 400
        patch["enabled"] = body["enabled"]

    if "spill" in body:
        if not isinstance(body["spill"], bool):
            return {"ok": False, "error": "spill must be true or false"}, 400
        patch["spill"] = body["spill"]

    if "max_chars" in body:
        mc = body["max_chars"]
        if isinstance(mc, bool) or not isinstance(mc, (int, float)):
            return {"ok": False, "error": "max_chars must be a number"}, 400
        mc = int(mc)
        if not (_TB_MIN_CHARS <= mc <= _TB_MAX_CHARS):
            return {"ok": False,
                    "error": "max_chars must be between %d and %d"
                             % (_TB_MIN_CHARS, _TB_MAX_CHARS)}, 400
        patch["max_chars"] = mc

    want_install = body.get("install") is True
    want_restart = body.get("restart") is True
    if not patch and not want_install and not want_restart:
        return {"ok": False,
                "error": "send at least one of enabled, max_chars, spill, "
                         "install, restart"}, 400

    # A kickstart -k kills whatever the agent is mid-sentence on. Refuse
    # BEFORE anything is written, so a refused request changes nothing at all
    # and the caller can simply retry — the same rule aux_evals.py applies
    # before it runs the suite.
    if want_restart and _tb_busy_jobs():
        return {"ok": False, "busy": True,
                "error": "a chat turn is running right now — the restart would "
                         "interrupt it; try again when it finishes"}, 409

    install_ok = False
    config_changed = False
    backup = None
    if want_install:
        if not _TB_HELPER:
            return {"ok": False, "error": _TB_HELPER_ERR}, 500
        ok, err = _tb_install_plugin()
        if not ok:
            return {"ok": False, "error": err}, 500
        install_ok = True
        try:
            print("[aux_toolbudget] installed the plugin at %s (source %s)"
                  % (_TB_PLUGIN_DST, _TB_PLUGIN_SRC), flush=True)
        except Exception:
            pass
        try:
            config_changed, backup = _tb_enable_in_config()
            if config_changed or not _tb_enabled_at():
                _tb_mark_enabled()
            try:
                print("[aux_toolbudget] plugins.enabled %s %s (backup: %s)"
                      % ("+= " + _TB_NAME if config_changed else "already had",
                         _TB_CFG, backup or "none"), flush=True)
            except Exception:
                pass
        except (OSError, RuntimeError) as e:
            return {"ok": False,
                    "error": "the plugin is installed but %s could not be "
                             "written — %s" % (_TB_CFG, e)}, 500

    if patch:
        _tb_write_settings(patch)

    restarted, restart_error = False, ""
    if want_restart:
        restarted, restart_error = _tb_restart_serve()

    after = _tb_payload()
    return {
        "ok": True,
        # "the install step ran and succeeded", not "something changed" —
        # config_changed/backup say whether anything was actually written.
        "install_ok": install_ok,
        "config_changed": config_changed,
        "backup": backup,
        "restarted": restarted,
        "restart_error": restart_error,
        **after,
    }


register_get("/api/tool/budget", _tb_get)      # noqa: F821 (server.py)
register_post("/api/tool/budget", _tb_post)    # noqa: F821 (server.py)
