"""loop-breaker — a pre_tool_call hard guard against degenerate tool loops.

hermes does not dedupe identical successive tool calls; it only stops a runaway
at the max-iteration cap (default ~50). A small local model can loop by
re-issuing the SAME web_search over and over (observed: 54 identical web_search
calls on one query before the cap fired). This plugin blocks the 3rd (and
later) identical (tool_name + normalized args) call of a READ-ONLY tool within
a turn and hands the model a message telling it to vary the call or conclude.

SCOPE (audit 2026-10-21 C134). Only the idempotent read/search tools in
`_IDEMPOTENT` are ever blocked — Hermes' own list from
agent/tool_guardrails.py, so the two mechanisms agree on what "read-only"
means. Repeating a STATEFUL call with the same arguments is normal and its
result differs each time: computer_use capture/scroll/key, process wait/poll,
a terminal health check. Those are never counted. And any call to a tool that
is not read-only (patch, write_file, terminal, computer_use...) resets the
turn's counters, because the world may have changed: read_file -> patch ->
read_file -> patch -> read_file is progress, not a loop.

RESULTS (audit 2026-11-09 E66). A repeat is a loop only when it gets the same
answer: a growing log, a page still loading or a search that failed on a
transient error is worth reading again. So `post_tool_call` records each
read's outcome, and the pre hook counts only repeats whose earlier answers
were IDENTICAL and NOT errors (what Hermes' own agent/tool_guardrails.py
calls no progress): the 3rd identical call is blocked only after the first two
returned the same successful result. A changed answer starts the count again.
Failures are counted on their own: the same read failing three times in a
row blocks the 4th, with a message that says so. If no `post_tool_call`
arrives for a call (an older Hermes, a path that does not emit it), the plugin
falls back to counting the calls themselves, and its message then claims only
what it knows — the same read, asked again with no state-changing call in
between — never that the answer could not have changed.

TURN SCOPE (C135). Counters are per (session, turn). Tool calls made from
inside an `execute_code` script carry no turn_id (only task_id), so they are
attributed to the turn that last ran for that task — the turn that started the
script — instead of a conversation-wide bucket that never reset. on_session_end
(fired at the end of every turn) drops that turn's counters.

Contract (hermes_cli.plugins.get_pre_tool_call_block_message):
  callback kwargs: tool_name, args(dict), task_id, session_id, tool_call_id,
                   turn_id, api_request_id, middleware_trace
  return {"action": "block", "message": "..."} to block; anything else allows.
post_tool_call (model_tools._emit_post_tool_call_hook, observational): the
  same identifiers plus result, status ("ok" | "error" | "blocked" | ...),
  error_type, error_message, duration_ms; its return value is ignored.
Never raises — a throwing hook must not break a turn.
"""

import hashlib
import json
import threading

# 3 = allow two identical attempts, block from the third on. Legitimate work
# almost never issues the byte-identical READ three times in one turn with
# nothing in between.
_THRESHOLD = 3
# the same read FAILING this many times in a row blocks the next attempt
_FAIL_THRESHOLD = 3
_MAX_SCOPES = 12          # bound memory: keep counters for the last N turns
_MAX_TASKS = 64           # bound the task -> current-turn map the same way

# agent/tool_guardrails.py IDEMPOTENT_TOOL_NAMES (Hermes 0.18), copied rather
# than imported so the plugin loads anywhere.
_IDEMPOTENT = frozenset((
    "read_file", "search_files", "web_search", "web_extract", "session_search",
    "browser_snapshot", "browser_console", "browser_get_images",
    "mcp_filesystem_read_file", "mcp_filesystem_read_text_file",
    "mcp_filesystem_read_multiple_files", "mcp_filesystem_list_directory",
    "mcp_filesystem_list_directory_with_sizes", "mcp_filesystem_directory_tree",
    "mcp_filesystem_get_file_info", "mcp_filesystem_search_files",
))

_lock = threading.Lock()
_counts = {}             # scope_key -> {call_hash: entry (see _entry)}
_order = []              # scope_keys in insertion order (for pruning)
_task_turn = {}          # task_id -> (session_id, turn_id) of its running turn


def _scope_key(turn_id, task_id, session_id):
    """(session, turn). Caller holds _lock."""
    if turn_id:
        if task_id:
            _task_turn.pop(task_id, None)
            _task_turn[task_id] = (session_id or "", turn_id)
            while len(_task_turn) > _MAX_TASKS:
                _task_turn.pop(next(iter(_task_turn)))
        return (session_id or "", turn_id)
    if task_id and task_id in _task_turn:
        return _task_turn[task_id]          # nested call (execute_code)
    return (session_id or "", "task:" + (task_id or "default"))


def _call_hash(tool_name, args):
    try:
        blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                          default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha1((tool_name + "\x1f" + blob).encode("utf-8",
                                                           "replace")).hexdigest()


def _entry():
    """Per (scope, call): `calls` seen by the pre hook; once a result has
    arrived (`seen`), `same` = consecutive identical successful answers ending
    with the last one, `fails` = consecutive failures ending with it."""
    return {"calls": 0, "seen": False, "hash": None, "same": 0, "fails": 0}


def _bucket(scope):
    """Caller holds _lock."""
    bucket = _counts.get(scope)
    if bucket is None:
        bucket = _counts[scope] = {}
        _order.append(scope)
        while len(_order) > _MAX_SCOPES:
            old = _order.pop(0)
            _counts.pop(old, None)
    return bucket


def _result_hash(result):
    """Canonical JSON when the result parses (key order cannot fake a change),
    else the text itself."""
    try:
        if isinstance(result, (bytes, bytearray)):
            result = result.decode("utf-8", "replace")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                return hashlib.sha1(result.encode("utf-8", "replace")).hexdigest()
        blob = json.dumps(result, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
    except Exception:
        blob = repr(result)
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def _failed(result, status):
    if isinstance(status, str) and status and status != "ok":
        return True
    try:
        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, dict) and data.get("error"):
            return True
    except Exception:
        pass
    return isinstance(result, str) and result.startswith("Error")


def _pre_tool_call(tool_name="", args=None, task_id="", session_id="",
                   turn_id="", **_):
    try:
        if not tool_name or not isinstance(args, dict):
            return None
        with _lock:
            scope = _scope_key(turn_id, task_id, session_id)
            if tool_name not in _IDEMPOTENT:
                # A call that can change the world: earlier reads may now
                # answer differently, so nothing before this is a loop.
                if scope in _counts:
                    _counts[scope] = {}
                return None
            h = _call_hash(tool_name, args)
            bucket = _bucket(scope)
            e = bucket.get(h)
            if e is None:
                e = bucket[h] = _entry()
            e["calls"] += 1
            calls, seen, same, fails = e["calls"], e["seen"], e["same"], e["fails"]
        if seen:
            if fails >= _FAIL_THRESHOLD:
                return {
                    "action": "block",
                    "message": (
                        "[loop-breaker] BLOCKED: `%s` with these exact arguments "
                        "has failed %d times in a row this turn. Do NOT retry it "
                        "unchanged: read the last error, then change the "
                        "arguments, use a different tool, or stop and tell the "
                        "user what is blocking you. Re-running the identical "
                        "call will keep being blocked." % (tool_name, fails)
                    ),
                }
            if same >= _THRESHOLD - 1:
                return {
                    "action": "block",
                    "message": (
                        "[loop-breaker] BLOCKED: `%s` with these exact arguments "
                        "has returned the same result %d times this turn. Use "
                        "the result you already have. Do NOT repeat it. If you "
                        "were searching, either try a materially different "
                        "query ONCE or stop and answer honestly with what you "
                        "have — say plainly if the thing may not exist or is "
                        "too new to confirm. Re-running the identical call "
                        "will keep being blocked." % (tool_name, same)
                    ),
                }
            return None
        if calls >= _THRESHOLD:
            # No result has been reported for this call (no post_tool_call):
            # say only what is known.
            return {
                "action": "block",
                "message": (
                    "[loop-breaker] BLOCKED: you have already called `%s` with "
                    "these exact arguments %d times this turn, with no "
                    "state-changing call in between. Do NOT repeat it. Use the "
                    "results you already have; if you were searching, either "
                    "try a materially different query ONCE or stop and answer "
                    "honestly with what you have — say plainly if the thing "
                    "may not exist or is too new to confirm. Re-running the "
                    "identical call will keep being blocked." % (tool_name, calls - 1)
                ),
            }
    except Exception:
        return None
    return None


def _post_tool_call(tool_name="", args=None, result=None, task_id="",
                    session_id="", turn_id="", status=None, **_):
    try:
        if not tool_name or tool_name not in _IDEMPOTENT or not isinstance(args, dict):
            return None
        if status == "blocked":          # ours or another guard's: no answer
            return None
        failed = _failed(result, status)
        rh = None if failed else _result_hash(result)
        with _lock:
            scope = _scope_key(turn_id, task_id, session_id)
            h = _call_hash(tool_name, args)
            bucket = _bucket(scope)
            e = bucket.get(h)
            if e is None:
                e = bucket[h] = _entry()
            if failed:
                e["fails"] += 1
                e["same"] = 0
                e["hash"] = None
            else:
                e["fails"] = 0
                e["same"] = e["same"] + 1 if (e["hash"] is not None and e["hash"] == rh) else 1
                e["hash"] = rh
            e["seen"] = True
    except Exception:
        pass
    return None


def _on_session_end(session_id="", task_id="", turn_id="", **_):
    # Tidy: on_session_end fires at the end of EVERY turn (agent/
    # turn_finalizer.py), so drop that turn's counters and the task's turn
    # attribution. Scope keys are (session, turn) tuples; the old code compared
    # them with the bare session id and never matched.
    try:
        def ends(scope):
            if turn_id:
                return scope[1] == turn_id
            return bool(session_id) and scope[0] == session_id
        with _lock:
            for scope in [s for s in _order
                          if ends(s) or (task_id and s[1] == "task:" + task_id)]:
                _counts.pop(scope, None)
                try:
                    _order.remove(scope)
                except ValueError:
                    pass
            for t in [t for t, v in _task_turn.items() if t == task_id or ends(v)]:
                _task_turn.pop(t, None)
    except Exception:
        pass


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
