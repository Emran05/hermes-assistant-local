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


# --------------------------------------------------------------------------
# gating (defense-in-depth) — refuse substantial code-gen + harmful framings
# BEFORE any claude call. Coarse on purpose: the system prompt is the primary
# layer, this is the cheap net that fails closed toward the human-approved path.
# --------------------------------------------------------------------------
# Harmful / exfiltration / credentials / destructive / approval-bypass. Scanned
# over TASK + USER-CONTEXT (context is scanned too because injected instructions
# in scraped world-text are part of the threat model).
_CB_HARM_RE = re.compile(
    r"(\.env\b"
    r"|\bexfiltrat\w*"
    r"|\bleak(?:ing|ed|s)?\b"
    r"|\bcredential"
    r"|\bpassword"
    r"|\bapi[ _-]?key"
    r"|\bprivate[ _-]?key"
    r"|\bsecret[ _-]?(?:key|token|value|file|s)"
    r"|\bsecrets\b"
    r"|\baccess[ _-]?token"
    r"|\boauth[ _-]?token"
    r"|\bssh[ _-]?key"
    r"|\bid_rsa\b"
    r"|\.ssh\b"
    r"|\.aws/credentials"
    r"|\bkeychain\b"
    r"|\brm\s+-rf\b"
    r"|\bwipe\b"
    r"|\bdestroy\b"
    r"|\bformat\s+(?:the\s+)?(?:disk|drive|volume)"
    r"|\bdelete\s+(?:all|everything)"
    r"|\bbypass\w*\s+(?:the\s+)?approval"
    r"|\bdisable\w*\s+(?:the\s+)?approval"
    r")", re.I)

# Substantial code generation. Scanned over TASK only (this is about what the
# caller is asking Claude to PRODUCE). Three catchers: an unambiguous "full
# implementation" / "N endpoints" phrase; a code filename next to a produce-verb;
# or a produce-verb within a short window of a code artefact.
_CB_CODE_STRONG = re.compile(
    r"\bfull\s+implementation\b"
    r"|\b(?:entire|complete|whole)\s+(?:implementation|program|module|file|feature|codebase)\b"
    r"|\bfrom\s+scratch\b"
    r"|\bwrite\s+(?:the\s+)?(?:full|entire|complete)?\s*code\b"
    r"|\b\d+\s+endpoints?\b", re.I)
_CB_CODE_FILE = re.compile(
    r"\b[\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|kt|c|cc|cpp|h|hpp|cs|php|swift|sh|sql|vue|svelte)\b",
    re.I)
_CB_CODE_VERB = re.compile(
    r"\b(?:write|implement|implementing|build|building|create|creating|generate|generating"
    r"|code|refactor|develop|developing|produce|program|scaffold|rewrite|port)\b", re.I)
_CB_CODE_OBJ = re.compile(
    r"\b(?:module|subroutine|function|method|class|endpoint|endpoints|implementation"
    r"|codebase|library|package|component|widget|aux_\w+|parser|handler|daemon)\b", re.I)

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


def _cb_is_codegen(task):
    t = task or ""
    if _CB_CODE_STRONG.search(t):
        return True
    if _CB_CODE_FILE.search(t) and _CB_CODE_VERB.search(t):
        return True
    verbs = [m.start() for m in _CB_CODE_VERB.finditer(t)]
    if verbs:
        for om in _CB_CODE_OBJ.finditer(t):
            if any(abs(om.start() - v) <= 60 for v in verbs):
                return True
    return False


def _cb_gate(task, context=""):
    """Return (reason, message) to refuse, or None to allow."""
    scan = (task or "") + "\n" + (context or "")
    if _CB_HARM_RE.search(scan):
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


register_post("/api/claude/think", _cb_think_handler)
register_get("/api/claude/bridge", _cb_bridge_handler)
register_get("/api/claude/recent", _cb_recent_handler)
