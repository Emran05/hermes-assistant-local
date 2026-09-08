"""tool-budget — a hard cap on how much of a tool result reaches the model.

WHY
---
Every tool result is appended to the conversation verbatim.  One `read_file`
of a build log, one chatty `terminal` command, one `web_extract` of a long
page can add tens of thousands of tokens to a 65,536-token window whose
prefill is compute-bound (~750 tok/s on this Mac, so tokens are seconds).
Hermes Agent has no global cap at that seam: `model_tools.py` returns the
tool's string straight into `make_tool_result_message`.  Every serious harness
caps it — Codex CLI at 256 lines / 10 KiB head+tail, DeepSeek's dsh with
head/tail retention plus a spill-to-disk retrieval hint — so this plugin adds
the same discipline without forking the runtime.

HOW
---
`transform_tool_result` (hermes_cli/plugins.py VALID_HOOKS; fired in
model_tools.py right after `post_tool_call` and before the result is appended
back into conversation context).  A callback that returns a **string**
replaces the result; the first string wins; non-string returns are ignored;
the whole dispatch is wrapped in try/except upstream, so a throwing hook is
fail-open.  We are fail-open a second time on our own side: every path in this
module is inside a try/except that returns None (= leave the result alone).

The hook runs for every entry point that dispatches a tool — `hermes serve`
(the dashboard and Telegram), `hermes -z`, and an interactive CLI — because
they all go through the same `model_tools.py` dispatch.

WHAT IT DOES
------------
* Under budget  -> returns None (unchanged).  No file is read, nothing is
  written.  A result of 4,000 chars or fewer cannot exceed any *legal* budget
  (the floor of the configurable range), so the fast path does not even read
  the settings file.
* Over budget   -> keeps a head and a tail with a per-tool bias:
    - `terminal` / `process` / `execute_code` / `read_terminal`: 35 % head,
      65 % tail — a command's exit status, stack trace and error text are at
      the END, and that is the part the model needs.
    - everything else (`read_file`, `search_files`, `web_extract`,
      `web_search`, `session_search`, …): 65 % head, 35 % tail — a file, a
      page or a result list is most informative at the top, and the tail keeps
      "is there more after this" visible.
  Cuts land on line boundaries when the text has lines.  Slicing is done on
  `str`, i.e. on codepoints, so a cut can never fall inside a UTF-8 sequence.
* JSON that fits after minification is kept WHOLE — a parseable result is
  worth more intact than head/tail'd, and `separators=(",",":")` often saves
  enough on its own.
* The FULL original is spilled to
  `~/.hermes/dashboard/spill/<YYYY-MM-DD>/<tool>-<8hex>.txt` (dir 0700, file
  0600) and the marker between head and tail names the path, so the model can
  get the rest with `read_file` + an offset or `search_files` over it.
  Day-directories older than seven days are deleted by `_gc_spill()`, which
  runs once per day from the first over-budget call AND once from `register()`
  — a machine that stops going over budget must still shed the last week
  rather than keeping it forever.  That cache is real on-disk exposure, so it
  is named in the "What stays local" list of Settings › Connections › Data &
  Network; if the retention or the path changes, change that row too.

WHEN THE SPILL ITSELF FAILS
---------------------------
The marker has THREE branches, not two: saved, "spill is off" (the owner's
choice), and "could NOT be saved to disk".  Printing the owner's choice when
the disk actually refused the write told the model — and the log — the one
comforting thing that was not true.  The JSONL row carries `spill_failed`.

THE MARKER SAYS "TRUNCATED", NOT "BROKEN"
-----------------------------------------
dsh's rule: truncated != incomplete.  A model that reads "…" in the middle of
a result and concludes the command failed will re-run it — which is exactly
the loop this plugin exists to avoid.  So the marker states in words that the
tool SUCCEEDED and only the middle was removed, gives the byte/line count, and
gives the retrieval path.

CONFIG — read FRESH on every over-budget call
---------------------------------------------
`~/.hermes/dashboard/settings.json`, key `tool_budget`:

    {"enabled": true, "max_chars": 24000, "spill": true}

24,000 chars is ~6,700 tokens at 3.6 chars/token — about 10 % of the 65,536
window this machine runs (`model.context_length` in ~/.hermes/config.yaml).
That is the number a single tool result may spend; the file, the log or the
page is still on disk, in full, one `read_file` away.  A missing, unreadable
or corrupt settings file means the defaults, never a failure.  `max_chars`
outside 4,000–200,000 is ignored (the same range the dashboard validates), so
a hand-edited 10 can never blind the agent.

No import from hermes-agent, the dashboard or PyYAML: stdlib only, so the
plugin loads in any process that loads plugins and is trivially testable with
`HERMES_HOME` pointed at a sandbox.
"""

import binascii
import datetime
import json
import os
import shutil
import sys
import threading

# ---------------------------------------------------------------------------
# defaults + limits
# ---------------------------------------------------------------------------
_DEFAULTS = {"enabled": True, "max_chars": 24000, "spill": True}

# The configurable range.  MIN_MAX_CHARS doubles as the fast-path threshold:
# a result at or below it cannot exceed any legal budget, so it is returned
# without reading the settings file at all.
_MIN_MAX_CHARS = 4000
_MAX_MAX_CHARS = 200000

# chars per token — the ratio aux_promptbudget.py measured against the model
# server's own prompt_tokens lines.  Used only for the human-facing estimate
# in the log; nothing here depends on it being exact.
_CHARS_PER_TOKEN = 3.6

# Tools whose interesting part is at the END.  A shell command's exit code,
# traceback and error message all land last.
_TAIL_HEAVY = frozenset((
    "terminal", "process", "execute_code", "read_terminal", "close_terminal",
))
_TAIL_HEAVY_SPLIT = (0.35, 0.65)      # head, tail
_HEAD_HEAVY_SPLIT = (0.65, 0.35)      # the default, for files/pages/searches

_SPILL_TTL_DAYS = 7
_LOG_MAX_BYTES = 2 * 1024 * 1024      # rotate tool-budget.jsonl at ~2 MB

_lock = threading.Lock()
_gc_day = [""]                        # last date the spill GC ran (mutable box)

# Fail-open is right; fail-SILENT is not. Both swallowing paths below say so
# once per process on stderr (which lands in ~/.hermes/logs/serve.log), and
# never again: this runs on every tool call, so a repeating failure must not
# become the log.
_said = {"hook": False, "log": False}


def _complain(slot, msg):
    """One stderr line the FIRST time a swallowed failure happens."""
    if _said.get(slot):
        return
    _said[slot] = True
    try:
        print("tool-budget: " + msg, file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# paths — resolved per call so HERMES_HOME can be repointed in a test
# ---------------------------------------------------------------------------
def _hermes_home():
    h = (os.environ.get("HERMES_HOME") or "").strip()
    return h if h else os.path.join(os.path.expanduser("~"), ".hermes")


def _dash_dir():
    return os.path.join(_hermes_home(), "dashboard")


def _settings_file():
    return os.path.join(_dash_dir(), "settings.json")


def _spill_root():
    return os.path.join(_dash_dir(), "spill")


def _log_file():
    return os.path.join(_dash_dir(), "tool-budget.jsonl")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def _read_config():
    """Fresh read of settings.json -> the three knobs. Never raises."""
    cfg = dict(_DEFAULTS)
    try:
        with open(_settings_file(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return cfg
    tb = raw.get("tool_budget") if isinstance(raw, dict) else None
    if not isinstance(tb, dict):
        return cfg
    if isinstance(tb.get("enabled"), bool):
        cfg["enabled"] = tb["enabled"]
    if isinstance(tb.get("spill"), bool):
        cfg["spill"] = tb["spill"]
    mc = tb.get("max_chars")
    if isinstance(mc, (int, float)) and not isinstance(mc, bool):
        try:
            mc = int(mc)
        except Exception:
            mc = None
        if mc is not None and _MIN_MAX_CHARS <= mc <= _MAX_MAX_CHARS:
            cfg["max_chars"] = mc
    return cfg


# ---------------------------------------------------------------------------
# spill
# ---------------------------------------------------------------------------
def _safe_tool(name):
    out = []
    for ch in str(name or "tool")[:40]:
        out.append(ch if (ch.isalnum() or ch in "._-") else "_")
    return ("".join(out) or "tool")


def _today():
    return datetime.date.today().isoformat()


def _gc_spill():
    """Delete spill day-directories older than _SPILL_TTL_DAYS. Best effort,
    once per day, and every failure is swallowed — a full disk or a locked
    file must never cost the agent a tool result."""
    root = _spill_root()
    try:
        cutoff = datetime.date.today() - datetime.timedelta(days=_SPILL_TTL_DAYS)
        for name in os.listdir(root):
            try:
                day = datetime.date.fromisoformat(name)
            except Exception:
                continue                      # not one of ours; leave it alone
            if day < cutoff:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    except Exception:
        pass


def _maybe_gc():
    day = _today()
    with _lock:
        if _gc_day[0] == day:
            return
        _gc_day[0] = day
    _gc_spill()


def _spill_write(tool, text):
    """Write the FULL original. Returns the path, or None if it could not be
    written (in which case the marker simply omits the retrieval hint)."""
    day = _today()
    d = os.path.join(_spill_root(), day)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            os.chmod(d, 0o700)                # makedirs honours umask
            os.chmod(_spill_root(), 0o700)
        except OSError:
            pass
        for _ in range(4):                    # O_EXCL: never clobber a spill
            token = binascii.hexlify(os.urandom(4)).decode("ascii")
            path = os.path.join(d, "%s-%s.txt" % (_safe_tool(tool), token))
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(text)
            return path
    except Exception:
        return None
    return None


def _display_path(path):
    """`~/.hermes/...` rather than the owner's real home — shorter in the
    model's context and it keeps a username out of the transcript."""
    if not path:
        return ""
    home = os.path.expanduser("~")
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


# ---------------------------------------------------------------------------
# the retention function — PURE, and the thing the unit tests drive
# ---------------------------------------------------------------------------
def _marker(omitted_lines, omitted_chars, spill_path, spill_failed=False):
    """The note that replaces the middle.

    THREE branches, not two. "(spill is off)" was printed both when the owner
    had switched spilling off and when the spill WRITE had failed — the model
    was told the middle was deliberately discarded when in fact the disk had
    refused it, which is the one case a human needs to know about. `spill_failed`
    is the caller's intent ("I tried and could not"), so the third branch says
    exactly that.
    """
    if spill_path:
        where = (" full output saved to %s; use read_file with an offset, or "
                 "search_files on it —" % _display_path(spill_path))
    elif spill_failed:
        where = (" the full output could NOT be saved to disk, so the omitted "
                 "middle is gone; re-run the tool to see it —")
    else:
        where = " the omitted middle was not saved (spill is off) —"
    return ("\n[… %s lines / %s chars omitted by tool-budget —%s the tool "
            "SUCCEEDED and its output was complete; this text was TRUNCATED "
            "to fit the context window, it is not an error and not a partial "
            "result …]\n" % (format(omitted_lines, ","),
                             format(omitted_chars, ","), where))


def retain(text, max_chars, tool_name="", spill_path=None, spill_failed=False):
    """Return `text` cut down to about `max_chars`, head + marker + tail.

    Pure and side-effect free — the spill is written by the caller and its
    path passed in, so this function can be unit-tested without touching the
    filesystem.  Returns `text` unchanged when it already fits.

    Slicing happens on `str`, i.e. on codepoints: a cut can never land inside
    a UTF-8 sequence, whatever the encoding of the source.
    """
    if not isinstance(text, str):
        return text
    if len(text) <= max_chars:
        return text

    # Reserve room for the marker using its worst case (both counters at
    # len(text)), so the real marker — whose numbers are smaller — always
    # fits inside what we reserved.  Deterministic, one pass, no iteration.
    reserve = len(_marker(len(text), len(text), spill_path, spill_failed))
    budget = max_chars - reserve
    if budget < 200:
        # A pathologically small budget: keep the head only, still marked.
        budget = max(200, int(max_chars * 0.25))

    head_ratio, _tail_ratio = (_TAIL_HEAVY_SPLIT if tool_name in _TAIL_HEAVY
                               else _HEAD_HEAVY_SPLIT)
    head_n = int(budget * head_ratio)
    tail_n = budget - head_n

    head = text[:head_n]
    tail = text[len(text) - tail_n:] if tail_n > 0 else ""

    # Cut on line boundaries when the text has lines, but never throw away
    # more than half of either slice chasing one.
    if "\n" in text:
        i = head.rfind("\n")
        if i >= 0 and i >= head_n // 2:
            head = head[:i + 1]
        j = tail.find("\n")
        if j >= 0 and j <= tail_n // 2:
            tail = tail[j + 1:]

    omitted_chars = len(text) - len(head) - len(tail)
    omitted_lines = max(0, text.count("\n") - head.count("\n") - tail.count("\n"))
    return head + _marker(omitted_lines, omitted_chars, spill_path,
                          spill_failed) + tail


def _minified_json(text, max_chars):
    """If `text` is JSON and minifying it gets under budget, return the
    minified form — a whole, parseable result beats a truncated one."""
    s = text.lstrip()
    if not s or s[0] not in "[{":
        return None                     # cheap reject before paying json.loads
    try:
        obj = json.loads(text)
    except Exception:
        return None
    try:
        out = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None
    return out if len(out) <= max_chars else None


# ---------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------
def _append_log(row):
    """One JSON line per truncation, rotated at ~2 MB. Best effort — but a
    best effort that says so once when it fails, because the dashboard card
    reads this file to decide whether the plugin is running at all: a silently
    unwritable log reads there as "the plugin never loaded"."""
    path = _log_file()
    try:
        line = json.dumps(row, ensure_ascii=False) + "\n"
    except Exception as e:
        _complain("log", "the truncation log row could not be encoded, "
                         "accounting is incomplete — %s: %s"
                         % (type(e).__name__, e))
        return
    try:
        os.makedirs(_dash_dir(), mode=0o700, exist_ok=True)
    except Exception:
        pass
    with _lock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as e:
            _complain("log", "the truncation log could not be written (%s), "
                             "results are still trimmed but the dashboard will "
                             "report nothing — %s: %s"
                             % (path, type(e).__name__, e))


# ---------------------------------------------------------------------------
# the hook
# ---------------------------------------------------------------------------
def transform_tool_result(tool_name="", args=None, result="", session_id="",
                          task_id="", turn_id="", **_):
    """Return a replacement string, or None to leave the result untouched.

    Never raises: every failure mode ends in `return None`, which the runtime
    reads as "no plugin wanted to change this".
    """
    try:
        if not isinstance(result, str):
            return None
        n = len(result)
        # Fast path — no settings read, no stat, no write.  Nothing at or
        # below the floor of the legal budget range can be over budget.
        if n <= _MIN_MAX_CHARS:
            return None

        cfg = _read_config()
        if not cfg["enabled"]:
            return None
        max_chars = cfg["max_chars"]
        if n <= max_chars:
            return None

        # Whole beats truncated when the whole thing fits.
        minified = _minified_json(result, max_chars)
        if minified is not None:
            return minified

        # The INTENT has to travel with the outcome: "spill is off" and "the
        # spill failed" are different sentences to put in front of a model.
        spill_path = None
        spill_wanted = bool(cfg["spill"])
        if spill_wanted:
            _maybe_gc()
            spill_path = _spill_write(tool_name, result)
        spill_failed = spill_wanted and not spill_path
        if spill_failed:
            _complain("spill", "the full output could not be written under %s; "
                               "results are still trimmed but the omitted "
                               "middle is not recoverable" % _spill_root())

        out = retain(result, max_chars, tool_name=tool_name,
                     spill_path=spill_path, spill_failed=spill_failed)
        if not isinstance(out, str) or out == result:
            return None

        omitted = n - len(out)
        _append_log({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "tool": str(tool_name or ""),
            "orig_chars": n,
            "kept_chars": len(out),
            "omitted_chars": max(0, omitted),
            "est_tokens_saved": int(round(max(0, omitted) / _CHARS_PER_TOKEN)),
            "spill_path": spill_path or "",
            "spill_failed": spill_failed,
            "session": str(session_id or task_id or turn_id or ""),
        })
        return out
    except Exception as e:
        # Fail-open, and SAY SO. Before this line a broken hook meant every
        # tool result silently entered the window untrimmed with nothing
        # anywhere to explain why the dashboard card had stopped counting.
        _complain("hook", "hook failed, results pass through untrimmed — "
                          "%s: %s" % (type(e).__name__, e))
        return None


def register(ctx):
    # Best-effort GC at load, not only inside an over-budget call: a machine
    # that stops going over budget would otherwise keep the last week of full
    # tool outputs on disk forever, since nothing else ever visits the tree.
    try:
        _maybe_gc()
    except Exception:
        pass
    ctx.register_hook("transform_tool_result", transform_tool_result)
