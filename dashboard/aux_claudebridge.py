# aux_claudebridge.py — the "Claude Bridge": Hermes's heavy-thinking channel.
#
# The two-brain architecture: the local Qwen model is the firehose (cheap,
# high-volume, always-on); Claude — reached headlessly via `claude -p` on the
# user's Max plan — is the deep-reasoning engine for the hard calls (connecting
# a world signal to a goal, analysing an opportunity, drafting a suggested move,
# reasoning about the user's network). This module is the mechanism.
#
# exec'd into server.py's globals by the aux-module loader (after
# expanders_extra.py, sorted among the other aux_*.py). It may use these
# server.py globals: HOME, register_get, register_post, RouteCtx. It imports ALL
# its own stdlib deps (exec'd code cannot rely on server.py's function-local
# imports) and defines only NEW names (CB_*, _cb_*, claude_think) so it clobbers
# nothing.
#
# What a bridge call does (claude_think / POST /api/claude/think):
#   * runs `claude -p` in a FRESH, empty scratch cwd (no project/file access),
#   * with --system-prompt-file ~/.hermes/claude-bridge-prompt.md (the byte-stable
#     reasoning persona — coding-agent default prompt fully replaced),
#   * --model sonnet (quick) or opus (deep) + a matching --effort,
#   * --output-format text, and the enumerated --disallowedTools lockout
#     (Bash Edit Write NotebookEdit WebFetch WebSearch Task) so the call has no
#     capability to write files, run code, or touch the network — even a fully
#     jailbroken reply is just TEXT the caller re-gates,
#   * on the user's Max OAuth (ANTHROPIC_API_KEY is stripped from the child env so
#     a stray key can never hijack auth/billing).
#
# Budget posture (the user's directive): UNLIMITED Claude for thinking / working
# toward the user's goals; GATE (refuse + route to the human-approved
# suggest->Claude-Code path) for substantial autonomous CODE generation; REFUSE
# anything potentially harmful (exfiltration / credentials / destructive /
# approval-bypass). The system prompt is the innermost defence layer; the gating
# below is coarse defense-in-depth ON TOP of it, and the tool-lockout is the
# load-bearing structural control. The Claude Usage widget (aux_claude_usage) is
# the visible governor of spend.
#
# MASTER SWITCH (2026-09-03; default flipped to OFF 2026-09-10, audit A02):
# settings.json `claude_escalation.enabled` (default false, requires an
# explicit boolean true) gates claude_think() itself — see
# claude_escalation_enabled() and GET/POST /api/claude/escalate. Because
# claude_think is the only code path that runs `claude -p`, that one check
# turns the whole second brain off for every caller: the auto-router, the
# manual Escalate button and For-You. A settings.json that exists but fails to
# parse fails the switch CLOSED (not open) and is surfaced as `config_error`
# on GET /api/claude/bridge.
#
# Every call (including refusals) appends one line to
# ~/.hermes/dashboard/claude-bridge-log.jsonl (0600) — ts, depth, model, a
# TRUNCATED task summary (no secrets, no user-context, no response text), ms, ok
# — so usage is auditable.

import os
import re
import sys
import json
import time
import glob
import shutil
import tempfile
import subprocess

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
CB_PROMPT_PATH = os.path.join(HOME, ".hermes", "claude-bridge-prompt.md")
CB_LOG_PATH    = os.path.join(HOME, ".hermes", "dashboard", "claude-bridge-log.jsonl")
# Recent FULL dialogues (task + response) so the UI can show what the agent asked
# Claude and what it answered. Local only, 0600, ring-capped — the user's own data.
CB_RECENT_PATH = os.path.join(HOME, ".hermes", "dashboard", "claude-recent.json")
CB_RECENT_MAX  = 30
CB_TASK_CAP    = 6000
CB_RESP_CAP    = 12000

# depth -> (model alias, effort level). sonnet/medium is the routine hard call;
# opus/xhigh is the genuinely-hard call, reserved (expensive on the plan).
CB_MODELS  = {"quick": "sonnet", "deep": "opus"}
CB_EFFORT  = {"quick": "medium", "deep": "xhigh"}
CB_TIMEOUT = {"quick": 180, "deep": 600}      # generous — deep thinking is slow

# The exact enumerated deny-list verified against Claude Code 2.1.201 (a lone
# "*" is NOT a documented deny-all). Passed as ONE argv element (variadic
# <tools...>), matching the smoke-tested form.
CB_DISALLOWED = "Bash Edit Write NotebookEdit WebFetch WebSearch Task"

CB_SUMMARY_MAX = 160          # task-summary truncation for the audit log
CB_LOG_TAIL    = 4000         # bytes of the log to read back for /bridge status

# --- master escalation switch (2026-09-03; default flipped 2026-09-10 -----
# audit A02) ------------------------------------------------------------------
# Until now there was no single off-switch for "talk to Claude": auto_route.mode
# only covered the per-turn auto-router, so the manual Escalate button and
# For-You's _fy_claude_moves kept spending the Max plan even with routing off.
# claude_think() is the ONLY function that actually shells out to `claude -p`,
# so one gate at the top of it is the complete, unbypassable off-switch for
# every caller (router, button, For-You, anything added later).
#
# Default OFF, and FAIL CLOSED (2026-09-10 audit A02): outbound inference to a
# hosted model is the one privacy-sensitive thing this app can do on its own,
# so it must never happen without an explicit, present, boolean `true` in
# settings.json `claude_escalation.enabled`. A missing file (fresh install), a
# missing key, a non-boolean value, or a settings.json that fails to parse all
# resolve to False — see claude_escalation_enabled(). This matches the README
# ("off unless you turn it on") and closes the gap where a corrupt or absent
# settings file used to fall open instead.
CB_ESC_DEFAULT = False
CB_MSG_ESC_OFF = ("Claude escalation is switched off — turn it on in the model menu "
                  "or Settings › Claude Bridge.")
_CB_ESC_LOGGED = {"done": False}      # stderr note once per process, not per call
# Last settings-read failure (corrupt/unreadable settings.json), surfaced on
# GET /api/claude/bridge as `config_error` so a broken file is visible in the
# UI rather than only a stderr line. None when the last read was clean (or
# there simply is no settings file yet — that is not an error).
_CB_CONFIG_ERROR = {"error": None, "logged": False}


# --------------------------------------------------------------------------
# gating (defense-in-depth) — refuse substantial code-gen + harmful framings
# BEFORE any claude call. Coarse on purpose: the system prompt is the primary
# layer, this is the cheap net that fails closed toward the human-approved path.
# --------------------------------------------------------------------------
# Design (rewritten 2026-08-18 after a 5/18 false-positive rate on realistic
# escalations — "memory leak", "password field", ".env approach", "wipe the
# cache", "should I create a component" were all refused): the gate matches
# INTENT, not vocabulary. The tool-lockout is the load-bearing control (Claude
# can't read/run/send anything), so this net only has to catch (a) an ask to
# obtain/move secrets, (b) an ask for a destructive/approval-bypassing action,
# (c) an imperative "produce the code" request. Mentioning a password, a leak,
# a .env file or a component in a QUESTION is a reasoning topic, not a threat.

# --- (a) secrets: an ACTION verb close to a secret NOUN --------------------
_CB_SECRET_NOUN = (
    r"(?:\.env\b|\benv\s+file|\bcredentials?\b|\bpasswords?\b|\bapi[ _-]?keys?\b"
    r"|\bprivate[ _-]?keys?\b|\bsecret[ _-]?(?:key|token|value|file)s?\b|\bsecrets\b"
    r"|\b(?:access|oauth|auth|bearer|session)[ _-]?tokens?\b|\bssh[ _-]?keys?\b"
    r"|\bid_rsa\b|~?/?\.ssh\b|\.aws/credentials|\bkeychain\b|\bcookies?\b)")
_CB_SECRET_VERB = (
    r"\b(?:read|open|cat|dump|print|show|reveal|display|list|extract|grab|fetch|pull"
    r"|copy|steal|harvest|collect|exfiltrat\w*|leak|send|upload|post|email|mail|paste"
    r"|share|forward|transmit|export|include|attach|retrieve|get|obtain|find|locate"
    r"|expose|log)\b")
# verb ... noun (within ~50 chars) OR noun ... verb (passive: "credentials to send")
_CB_HARM_SECRETS = re.compile(
    _CB_SECRET_VERB + r"[^.?!\n]{0,50}?" + _CB_SECRET_NOUN
    + r"|" + _CB_SECRET_NOUN + r"[^.?!\n]{0,30}?\b(?:to|and)\s+(?:send|upload|post|email|paste|share|exfiltrat\w*|leak)\b",
    re.I)
# --- (b) destructive / approval bypass ---------------------------------------
_CB_HARM_DESTRUCT = re.compile(
    r"\brm\s+-[a-z]*r[a-z]*f?\b|\brm\s+-[a-z]*f[a-z]*r\b"
    r"|\bformat\s+(?:the\s+|my\s+)?(?:disk|drive|volume|ssd|mac)\b"
    r"|\b(?:wipe|erase|destroy|nuke|shred|delete)\s+(?:the\s+|my\s+|all\s+(?:of\s+)?(?:the\s+|my\s+)?)?"
    r"(?:disk|drive|mac|machine|laptop|system|home\s+(?:dir|directory|folder)|data|files|everything|backups?|repo|repository|database|db)\b"
    r"|\bdelete\s+(?:all|everything)\b",
    re.I)
# approval bypass — refused even when phrased as a question ("how do I bypass…")
_CB_HARM_BYPASS = re.compile(
    r"\b(?:bypass|disable|skip|circumvent|turn\s+off|remove)\w*\s+(?:the\s+)?(?:approval|permission|safety|guard|gate|confirmation)s?\b"
    r"|\bwithout\s+(?:asking|approval|permission|confirmation)\b",
    re.I)
# --- (c) explicit "produce the code" ask ------------------------------------
# Imperative produce-verb + code artefact IN THE SAME SENTENCE, and that
# sentence is not a question ("should I create a component?" is design).
_CB_CODE_STRONG = re.compile(
    r"\bfull\s+implementation\b"
    r"|\b(?:entire|complete|whole)\s+(?:implementation|program|module|file|feature|codebase)\b"
    r"|\bwrite\s+(?:me\s+|us\s+)?(?:the\s+|a\s+|an\s+)?(?:full|entire|complete|working|production)?\s*"
    r"(?:code|implementation|program|script|module|class|function|component|widget|endpoint|parser|handler|plugin|patch|diff|pr|pull\s+request)\b"
    r"|\b(?:implement|build|create|generate|scaffold|code\s+up|port|rewrite|refactor)\s+(?:me\s+|us\s+)?(?:the\s+|a\s+|an\s+|this\s+|these\s+|that\s+)?"
    r"(?:\w+\s+){0,3}?(?:code|implementation|program|script|module|class|function|component|widget|endpoint|endpoints|parser|handler|plugin|package|library|codebase|file|files|patch|diff|pr|pull\s+request|api|cli|daemon|service|aux_\w+)\b"
    r"|\b\d+\s+endpoints?\b",
    re.I)
_CB_CODE_FILE = re.compile(
    r"\b[\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|kt|c|cc|cpp|h|hpp|cs|php|swift|sh|sql|vue|svelte)\b",
    re.I)
_CB_CODE_FILE_VERB = re.compile(
    r"\b(?:write|implement|create|generate|scaffold|rewrite|refactor|port|patch|edit|modify|fix)\b", re.I)
_CB_QUESTION_LEAD = re.compile(
    r"^\s*(?:should|would|could|can|is|are|do|does|did|how|why|what|which|when|where|who|whether|if|compare|weigh|evaluate|assess|explain|analy[sz]e|think|help me (?:decide|think|weigh|choose|understand)|is it|isn't|any thoughts|thoughts on|opinion|pros?\b|cons?\b|trade-?offs?)\b",
    re.I)
_CB_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+|\n+")

CB_MSG_CODEGEN = (
    "Refused — this is a substantial code-generation request, and the Claude Bridge is a "
    "reasoning-only channel (it runs with all file/exec/network tools locked out). "
    "Autonomous code changes do not go through the bridge; they go through the human-approved "
    "path: surface a suggestion for the user, and on their explicit approval hand it to the "
    "`autonomous-ai-agents/claude-code` skill. I can still help you THINK about it — the design, "
    "the interfaces, the tradeoffs, the risks. Reframe the request as a reasoning question and "
    "send it again.")
CB_MSG_HARM = (
    "Refused — this task reads as accessing or exfiltrating secrets/credentials, or as a "
    "destructive or approval-bypassing action. The bridge reasons over the user's own goals and "
    "never reads secrets, moves data off the Mac, or proposes consequential/irreversible actions "
    "outside the approval gate. If there's a legitimate goal behind this, restate it as a "
    "reasoning question and I'll help with that.")


def _cb_is_question(sentence):
    st = sentence.strip()
    return st.endswith("?") or bool(_CB_QUESTION_LEAD.match(st))


def _cb_is_codegen(task):
    """True only for an imperative 'produce the code' ask. Questions about
    code (design, tradeoffs, 'should I create X', 'why does the parser…') pass."""
    for sent in _CB_SENT_SPLIT.split(task or ""):
        if not sent.strip() or _cb_is_question(sent):
            continue
        if _CB_CODE_STRONG.search(sent):
            return True
        if _CB_CODE_FILE.search(sent) and _CB_CODE_FILE_VERB.search(sent):
            return True
    return False


def _cb_is_harmful(text):
    text = text or ""
    if _CB_HARM_SECRETS.search(text) or _CB_HARM_BYPASS.search(text):
        return True
    # destructive commands: an imperative ("rm -rf ~/x", "wipe the disk") is
    # refused; a QUESTION that merely mentions one ("what does rm -rf teach
    # about approval gates?", "should I wipe the disk?") is a reasoning topic.
    for sent in _CB_SENT_SPLIT.split(text):
        if sent.strip() and _CB_HARM_DESTRUCT.search(sent) and not _cb_is_question(sent):
            return True
    return False


def _cb_gate(task, context=""):
    """Return (reason, message) to refuse, or None to allow.
    Task AND context are scanned for harmful INTENT (injected instructions in
    scraped world-text are part of the threat model); code-gen is judged on the
    task alone."""
    if _cb_is_harmful(task) or _cb_is_harmful(context):
        return ("harmful", CB_MSG_HARM)
    if _cb_is_codegen(task or ""):
        return ("codegen", CB_MSG_CODEGEN)
    return None


# --------------------------------------------------------------------------
# claude CLI resolution + child env (Max OAuth, node on PATH under launchd)
# --------------------------------------------------------------------------
_CB_BIN_CACHE = {}


def _cb_claude_bin():
    """Resolve the claude CLI. launchd's PATH excludes the nvm bin dir, so we
    look there explicitly before falling back to PATH. Cached once found."""
    hit = _CB_BIN_CACHE.get("path")
    if hit:
        return hit
    cands = sorted(
        glob.glob(os.path.join(HOME, ".nvm", "versions", "node", "*", "bin", "claude")),
        reverse=True)
    cands += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude",
              os.path.join(HOME, ".local", "bin", "claude")]
    w = shutil.which("claude")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            _CB_BIN_CACHE["path"] = c
            return c
    return None


def _cb_env(claude):
    """Child env: pass through (so it finds the user's Max OAuth via HOME) but
    STRIP any API key so a stray one can never override the subscription auth,
    and prepend the claude/node bin dir so node resolves under launchd."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    bindir = os.path.dirname(claude)                 # the nvm bin dir holds node too
    if bindir:
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env.setdefault("CI", "1")                        # non-interactive hygiene
    return env


# --------------------------------------------------------------------------
# audit log — one JSONL line per call (0600, no secrets / context / response)
# --------------------------------------------------------------------------
def _cb_summary(task):
    s = " ".join((task or "").split())               # collapse whitespace
    return s[:CB_SUMMARY_MAX]


def _cb_log(entry):
    """Append one line; a failure here is logged to stderr, never fails a call."""
    try:
        os.makedirs(os.path.dirname(CB_LOG_PATH), mode=0o700, exist_ok=True)
        existed = os.path.exists(CB_LOG_PATH)
        line = json.dumps(entry, ensure_ascii=False)
        with open(CB_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if not existed:
            try:
                os.chmod(CB_LOG_PATH, 0o600)
            except OSError:
                pass
    except Exception as e:                            # pragma: no cover
        print("[aux_claudebridge] log write failed: %s" % e, file=sys.stderr)


def _cb_recent_add(entry):
    """Prepend one full dialogue to the ring store (0600). Never fails a call."""
    try:
        os.makedirs(os.path.dirname(CB_RECENT_PATH), mode=0o700, exist_ok=True)
        try:
            with open(CB_RECENT_PATH, encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                items = []
        except Exception:
            items = []
        items.insert(0, entry)
        items = items[:CB_RECENT_MAX]
        tmp = CB_RECENT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CB_RECENT_PATH)
    except Exception as e:                                # pragma: no cover
        print("[aux_claudebridge] recent write failed: %s" % e, file=sys.stderr)


def _cb_recent(n=20):
    try:
        with open(CB_RECENT_PATH, encoding="utf-8") as f:
            items = json.load(f)
        return items[:n] if isinstance(items, list) else []
    except Exception:
        return []


def _cb_settings_dict():
    """Read settings.json OURSELVES rather than through server.py's
    read_json(), which folds a missing OR a corrupt file into the same `{}`
    default — indistinguishable from "no opinion yet". Distinguishing them is
    the whole point here: a missing file is normal (nothing has ever written
    one) and must read as False with no fuss, but a file that exists and fails
    to parse is a configuration PROBLEM the owner should be told about, not a
    silent no-op. Returns (dict, error_or_None)."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return (data if isinstance(data, dict) else {}), None
    except FileNotFoundError:
        return {}, None
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)


def claude_escalation_enabled():
    """Is the Claude bridge allowed to run at all? settings.json
    `claude_escalation.enabled` — default False, and requires an explicit
    boolean `true` (2026-09-10 audit A02: outbound inference must be opt-in,
    never opt-out).

    Read fresh on EVERY call rather than cached: settings.json is a few hundred
    bytes, so the cost is nil next to a multi-second `claude -p`, and it means
    the toggle takes effect on the very next call with no restart, no cache
    invalidation and no cross-thread state (chat worker, For-You thread and
    the HTTP thread all see the same file).

    Any read problem — the file exists but fails to parse, a permission error,
    anything but "no file yet" — FAILS CLOSED (returns False) rather than
    falling back to CB_ESC_DEFAULT, and is logged once per process plus
    surfaced as `config_error` on GET /api/claude/bridge, so a broken
    settings.json is visible instead of silently (and invisibly) either
    disabling or re-enabling outbound calls."""
    s, err = _cb_settings_dict()
    if err:
        _CB_CONFIG_ERROR["error"] = err
        if not _CB_CONFIG_ERROR["logged"]:
            _CB_CONFIG_ERROR["logged"] = True
            print("[aux_claudebridge] settings.json unreadable (%s) — Claude "
                  "escalation stays OFF until it is fixed" % err, file=sys.stderr)
        return False
    _CB_CONFIG_ERROR["error"] = None
    cfg = s.get("claude_escalation")
    if isinstance(cfg, dict):
        return cfg.get("enabled") is True
    return CB_ESC_DEFAULT


def _cb_set_escalation(enabled):
    """Persist the switch through server.py's settings_update() — ONE locked
    read-modify-write of settings.json that touches only `claude_escalation`.

    This function used to take no lock at all and write the whole settings blob
    back from its own read, which is how the 2026-09-10 audit (A01) could show
    a background weather refresh reverting an explicit opt-out. The switch is
    the single most privacy-relevant value in the file: it must never be
    written from a snapshot, and it must never be collateral damage in someone
    else's write."""
    seen = {}

    def _apply(s):
        cfg = s.get("claude_escalation")
        cfg = cfg if isinstance(cfg, dict) else {}
        seen["prev"] = cfg.get("enabled")
        cfg["enabled"] = bool(enabled)
        s["claude_escalation"] = cfg

    settings_update(_apply)                                        # noqa: F821
    prev = seen.get("prev")
    # Always leave a trace: the owner turned this off once and later found it
    # back on with nothing in the log to say who did it.
    try:
        print("[aux_claudebridge] escalation switch %s -> %s" % (prev, bool(enabled)), flush=True)
    except Exception:
        pass
    return bool(enabled)


def _cb_norm_depth(depth):
    d = str(depth or "quick").strip().lower()
    if d in ("deep", "opus", "high", "xhigh", "max", "hard", "heavy"):
        return "deep"
    return "quick"


# --------------------------------------------------------------------------
# the bridge call
# --------------------------------------------------------------------------
def claude_think(task, user_context="", depth="quick"):
    """Run one heavy-thinking call to Claude via `claude -p` on the Max plan.

    Returns {ok, text, model, depth, ms, tokens, [error]|[refused, reason]}.
    Pure reasoning: no tools, fresh scratch cwd, byte-stable system prompt.
    """
    t0 = time.time()
    depth = _cb_norm_depth(depth)
    task = task if isinstance(task, str) else ("" if task is None else str(task))
    user_context = user_context if isinstance(user_context, str) else ""
    summary = _cb_summary(task)

    def _ms():
        return int((time.time() - t0) * 1000)

    if not task.strip():
        return {"ok": False, "text": "", "model": None, "depth": depth,
                "ms": _ms(), "tokens": None, "error": "empty task"}

    # 0) master switch — the choke point. Checked BEFORE the content gate (and
    # before any logging) because a switched-off bridge is a configuration
    # answer, not a security verdict: nothing about the task matters. The
    # refusal keeps the module's existing shape ({ok:False, refused:True,
    # reason, text}) so every caller handles it with code it already has —
    # _cb_think_handler returns it verbatim, aux_autoroute's _ar_think_thread
    # renders res["text"] as the deep-card error, aux_foryou logs the reason and
    # falls through to the local model. Deliberately NOT written to
    # claude-bridge-log.jsonl: that log (and the recent_24h counter the bridge
    # status card shows) is a record of Claude USAGE, and a refused-by-switch
    # call spent nothing — logging it would inflate the usage the user is
    # trying to cut. One stderr line the first time per process instead, so the
    # dashboard log shows why the second brain went quiet without a line per turn.
    if not claude_escalation_enabled():
        if not _CB_ESC_LOGGED["done"]:
            _CB_ESC_LOGGED["done"] = True
            print("[aux_claudebridge] escalation is OFF (settings.json "
                  "claude_escalation.enabled=false) — refusing bridge calls",
                  file=sys.stderr)
        return {"ok": False, "refused": True, "reason": "escalation_off",
                "text": CB_MSG_ESC_OFF, "model": None, "depth": depth,
                "ms": _ms(), "tokens": None}

    # 1) gate (defense-in-depth) — refuse before spending any Claude quota
    refusal = _cb_gate(task, user_context)
    if refusal is not None:
        reason, msg = refusal
        _cb_log({"ts": time.time(), "depth": depth, "model": None,
                 "task_summary": summary, "ms": _ms(), "ok": False,
                 "refused": True, "reason": reason})
        return {"ok": False, "refused": True, "reason": reason, "text": msg,
                "model": None, "depth": depth, "ms": _ms(), "tokens": None}

    # 2) preconditions
    claude = _cb_claude_bin()
    if not claude:
        _cb_log({"ts": time.time(), "depth": depth, "model": None,
                 "task_summary": summary, "ms": _ms(), "ok": False,
                 "error": "claude cli not found"})
        return {"ok": False, "text": "", "model": None, "depth": depth,
                "ms": _ms(), "tokens": None, "error": "claude CLI not found"}
    if not os.path.isfile(CB_PROMPT_PATH):
        _cb_log({"ts": time.time(), "depth": depth, "model": None,
                 "task_summary": summary, "ms": _ms(), "ok": False,
                 "error": "bridge prompt missing"})
        return {"ok": False, "text": "", "model": None, "depth": depth,
                "ms": _ms(), "tokens": None,
                "error": "bridge prompt file missing: " + CB_PROMPT_PATH}

    model = CB_MODELS[depth]
    effort = CB_EFFORT[depth]

    # 3) per-call message envelope: context + task ride in the USER message so
    # the system file stays byte-stable (warm prefill). Data, never commands.
    ctxblk = user_context.strip() or "(none provided)"
    message = "USER-CONTEXT:\n%s\n\nTASK:\n%s" % (ctxblk, task.strip())

    argv = [claude, "-p", message,
            "--model", model,
            "--effort", effort,
            "--system-prompt-file", CB_PROMPT_PATH,
            "--output-format", "text",
            "--disallowedTools", CB_DISALLOWED]

    env = _cb_env(claude)
    scratch = None
    ok, text, err = False, "", ""
    try:
        scratch = tempfile.mkdtemp(prefix="hermes-think-")   # empty cwd -> no file access
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           timeout=CB_TIMEOUT[depth], cwd=scratch, env=env,
                           stdin=subprocess.DEVNULL)
        text = (p.stdout or "").strip()
        if p.returncode == 0:
            ok = bool(text)
            if not ok:
                err = "empty response from claude"
        else:
            err = ("claude exited %d" % p.returncode) + \
                  ((": " + (p.stderr or "").strip()[:500]) if (p.stderr or "").strip() else "")
    except subprocess.TimeoutExpired:
        err = "timed out after %ds" % CB_TIMEOUT[depth]
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    ms = _ms()
    now = time.time()
    logent = {"ts": now, "depth": depth, "model": model,
              "task_summary": summary, "ms": ms, "ok": ok}
    if not ok:
        logent["error"] = err[:200]
    _cb_log(logent)
    # full local dialogue for the "show me what Claude did" UI (0600, ring)
    _cb_recent_add({
        "ts": now, "depth": depth, "model": model, "ms": ms, "ok": ok,
        "task": (task or "")[:CB_TASK_CAP],
        "context": (user_context or "")[:2000],
        "response": (text or "")[:CB_RESP_CAP],
        "error": (err[:300] if not ok else ""),
    })

    out = {"ok": ok, "text": text, "model": model, "depth": depth,
           "ms": ms, "tokens": None}
    if not ok:
        out["error"] = err
    return out


# --------------------------------------------------------------------------
# HTTP handlers
# --------------------------------------------------------------------------
def _cb_think_handler(ctx):
    try:
        b = ctx.body or {}
        task = b.get("task")
        if not isinstance(task, str) or not task.strip():
            return ({"ok": False, "error": "missing 'task' (non-empty string)"}, 400)
        context = b.get("context") or b.get("user_context") or ""
        if not isinstance(context, str):
            context = ""
        depth = b.get("depth", "quick")
        res = claude_think(task, user_context=context, depth=depth)
        return res
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _cb_log_tail(n=10):
    """Last n audited calls (already secret-free), newest first, + 24h count."""
    rows, recent_24h = [], 0
    now = time.time()
    try:
        if os.path.isfile(CB_LOG_PATH):
            sz = os.path.getsize(CB_LOG_PATH)
            with open(CB_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                if sz > CB_LOG_TAIL:
                    f.seek(sz - CB_LOG_TAIL)
                    f.readline()                      # drop partial first line
                lines = f.read().splitlines()
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                rows.append(rec)
                try:
                    if now - float(rec.get("ts") or 0) <= 86400:
                        recent_24h += 1
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    rows.reverse()
    return rows[:n], recent_24h


def _cb_bridge_handler(ctx):
    """Status: is the bridge armed, model defaults, recent (auditable) usage."""
    try:
        # Force a fresh settings read so config_error below reflects THIS
        # request, not whatever the last unrelated call to
        # claude_escalation_enabled() happened to leave behind.
        enabled = claude_escalation_enabled()
        present = os.path.isfile(CB_PROMPT_PATH) and os.path.getsize(CB_PROMPT_PATH) > 0
        mode = None
        if present:
            try:
                mode = oct(os.stat(CB_PROMPT_PATH).st_mode & 0o777)
            except OSError:
                mode = None
        claude = _cb_claude_bin()
        recent, recent_24h = _cb_log_tail(10)
        return {
            "ok": True,
            "enabled": enabled,
            "prompt_present": bool(present),
            "prompt_path": CB_PROMPT_PATH,
            "prompt_mode": mode,
            "claude_cli": claude,
            "claude_present": bool(claude),
            "models": {"quick": CB_MODELS["quick"], "deep": CB_MODELS["deep"]},
            "effort": {"quick": CB_EFFORT["quick"], "deep": CB_EFFORT["deep"]},
            "disallowed_tools": CB_DISALLOWED.split(),
            "auth": "max-oauth (no ANTHROPIC_API_KEY)",
            "recent_24h": recent_24h,
            "recent": recent,
            "log_path": CB_LOG_PATH,
            # 2026-09-10 audit A02: a settings.json that exists but fails to
            # parse fails escalation CLOSED; this is how that shows up
            # somewhere a person will actually see it instead of only stderr.
            "config_error": _CB_CONFIG_ERROR.get("error"),
        }
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


# --------------------------------------------------------------------------
# route registration
# --------------------------------------------------------------------------
def _cb_recent_handler(ctx):
    try:
        n = int(ctx.q1("n", "20") or "20")
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(30, n))
    return {"ok": True, "calls": _cb_recent(n)}


def _cb_escalate_get(ctx):
    return {"ok": True, "enabled": claude_escalation_enabled()}


def _cb_escalate_post(ctx):
    """{"enabled": bool}. Strict about the key being present — a body that
    forgot it must not silently flip the switch to False."""
    b = ctx.body or {}
    if "enabled" not in b:
        return ({"ok": False, "error": "missing 'enabled' (bool)"}, 400)
    try:
        return {"ok": True, "enabled": _cb_set_escalation(bool(b.get("enabled")))}
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)


register_post("/api/claude/think", _cb_think_handler)
register_get("/api/claude/escalate", _cb_escalate_get)
register_post("/api/claude/escalate", _cb_escalate_post)
register_get("/api/claude/bridge", _cb_bridge_handler)
register_get("/api/claude/recent", _cb_recent_handler)
