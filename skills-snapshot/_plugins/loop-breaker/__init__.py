"""loop-breaker — a pre_tool_call hard guard against degenerate tool loops.

hermes does not dedupe identical successive tool calls; it only stops a runaway
at the max-iteration cap (default ~50). A small local model can loop by
re-issuing the SAME web_search / terminal command over and over (observed: 54
identical web_search calls on one query before the cap fired). This plugin
blocks the 3rd (and later) identical (tool_name + normalized args) call within a
turn and hands the model a message telling it to vary the call or conclude.

Contract (hermes_cli.plugins.get_pre_tool_call_block_message):
  callback kwargs: tool_name, args(dict), task_id, session_id, tool_call_id,
                   turn_id, api_request_id, middleware_trace
  return {"action": "block", "message": "..."} to block; anything else allows.
Never raises — a throwing hook must not break a turn.
"""

import hashlib
import json
import threading

# 3 = allow two identical attempts, block from the third on. Legitimate work
# almost never issues the byte-identical tool call three times in one turn.
_THRESHOLD = 3
_MAX_SCOPES = 12          # bound memory: keep counters for the last N turns/tasks

_lock = threading.Lock()
_counts = {}             # scope_key -> {call_hash: count}
_order = []              # scope_keys in insertion order (for pruning)


def _scope_key(turn_id, task_id, session_id):
    return turn_id or task_id or session_id or "default"


def _call_hash(tool_name, args):
    try:
        blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                          default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha1((tool_name + "\x1f" + blob).encode("utf-8",
                                                           "replace")).hexdigest()


def _pre_tool_call(tool_name="", args=None, task_id="", session_id="",
                   turn_id="", **_):
    try:
        if not tool_name or not isinstance(args, dict):
            return None
        scope = _scope_key(turn_id, task_id, session_id)
        h = _call_hash(tool_name, args)
        with _lock:
            bucket = _counts.get(scope)
            if bucket is None:
                bucket = _counts[scope] = {}
                _order.append(scope)
                while len(_order) > _MAX_SCOPES:
                    old = _order.pop(0)
                    _counts.pop(old, None)
            bucket[h] = bucket.get(h, 0) + 1
            n = bucket[h]
        if n >= _THRESHOLD:
            return {
                "action": "block",
                "message": (
                    "[loop-breaker] BLOCKED: you have already called `%s` with "
                    "these exact arguments %d times this turn and the result was "
                    "the same each time. Do NOT repeat it. If you were searching, "
                    "either try a materially different query ONCE or stop and "
                    "answer honestly with what you have — say plainly if the thing "
                    "may not exist or is too new to confirm. Re-running the "
                    "identical call will keep being blocked." % (tool_name, n)
                ),
            }
    except Exception:
        return None
    return None


def _on_session_end(session_id="", **_):
    # tidy: drop any scope buckets tied to this session so memory stays bounded
    try:
        with _lock:
            for scope in [s for s in _order if s == session_id]:
                _counts.pop(scope, None)
                try:
                    _order.remove(scope)
                except ValueError:
                    pass
    except Exception:
        pass


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
