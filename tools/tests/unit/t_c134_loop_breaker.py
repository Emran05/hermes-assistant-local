#!/usr/bin/env python3
"""loop-breaker plugin — what it blocks and how it scopes a turn.

Audit 2026-10-21 C134 (it blocked the 3rd identical call of ANY tool, stateful
ones included, and told the model the results had been identical) and C135
(execute_code's nested calls fell back to a conversation-wide bucket, and the
session-end tidy compared tuple-free keys with the session id and never
matched). Loads the REAL plugin file the agent loads; no Hermes, no network.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PLUGIN = os.path.join(REPO, "skills-snapshot", "_plugins", "loop-breaker",
                      "__init__.py")

PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (("  — " + str(extra)[:300]) if extra else ""))


def fresh():
    spec = importlib.util.spec_from_file_location("loop_breaker_test", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(lb, tool, args, turn="T1", task="task-1", session="S1"):
    return lb._pre_tool_call(tool_name=tool, args=args, task_id=task,
                             session_id=session, turn_id=turn)


def blocked(r):
    return isinstance(r, dict) and r.get("action") == "block"


def main():
    print("loop-breaker: blocks repeated READS only, scoped to one turn")

    # --- C134: stateful tools repeat on purpose ------------------------------
    lb = fresh()
    scroll = {"action": "scroll", "app": "Safari", "direction": "down", "amount": 5}
    rs = [call(lb, "computer_use", scroll) for _ in range(4)]
    check("C134: computer_use scroll x4 is never blocked",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    seq = [("computer_use", {"action": "capture", "app": "Safari"}),
           ("computer_use", {"action": "click", "element": 3}),
           ("computer_use", {"action": "capture", "app": "Safari"}),
           ("computer_use", {"action": "type", "text": "x"}),
           ("computer_use", {"action": "capture", "app": "Safari"})]
    rs = [call(lb, t, a) for t, a in seq]
    check("C134: capture -> click -> capture -> type -> capture: 3rd capture allowed",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    wait = {"action": "wait", "session_id": "proc_1", "timeout": 120}
    rs = [call(lb, "process", wait) for _ in range(3)]
    check("C134: process wait x3 on a background build is never blocked",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    rs = [call(lb, "terminal", {"command": "curl -s localhost:3000/health"})
          for _ in range(3)]
    check("C134: a repeated terminal health check is never blocked",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    rd = {"path": "/tmp/x.py"}
    rs = []
    for i in range(3):
        rs.append(call(lb, "read_file", rd))
        rs.append(call(lb, "patch", {"path": "/tmp/x.py", "old_string": str(i),
                                      "new_string": str(i + 1)}))
    rs.append(call(lb, "read_file", rd))
    check("C134: read -> patch -> read -> patch -> read is progress, never blocked",
          not any(blocked(r) for r in rs), rs)

    # --- the loop it exists for still breaks ---------------------------------
    lb = fresh()
    q = {"query": "qwen 3.8 release date"}
    rs = [call(lb, "web_search", q) for _ in range(4)]
    check("identical web_search: 1st and 2nd allowed",
          not blocked(rs[0]) and not blocked(rs[1]), rs[:2])
    check("identical web_search: 3rd and 4th blocked",
          blocked(rs[2]) and blocked(rs[3]), rs[2:])
    msg = (rs[2] or {}).get("message", "") if isinstance(rs[2], dict) else ""
    check("block message no longer claims the results were identical",
          "result was the same" not in msg and "same each time" not in msg, msg)
    check("block message counts the earlier calls (2), not the blocked one",
          "2 times" in msg, msg)

    lb = fresh()
    rs = [call(lb, "web_search", q), call(lb, "web_search", q),
          call(lb, "terminal", {"command": "date"}), call(lb, "web_search", q)]
    check("a state-changing call in between resets the read counter",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    rs = [call(lb, "web_search", {"query": "a"}), call(lb, "web_search", {"query": "b"}),
          call(lb, "web_search", {"query": "a"}), call(lb, "web_search", {"query": "b"})]
    check("different arguments are counted separately",
          not any(blocked(r) for r in rs), rs)

    lb = fresh()
    rs = [call(lb, "web_search", q, turn="T1"), call(lb, "web_search", q, turn="T1"),
          call(lb, "web_search", q, turn="T2")]
    check("a new turn starts a new count", not blocked(rs[2]), rs)

    # --- C135: execute_code's nested calls belong to the turn that ran it ----
    lb = fresh()
    call(lb, "execute_code", {"code": "..."}, turn="T1", task="task-1")
    nested = [call(lb, "web_search", q, turn="", task="task-1", session="")
              for _ in range(2)]
    lb._on_session_end(session_id="S1", task_id="task-1", turn_id="T1")
    call(lb, "execute_code", {"code": "..."}, turn="T2", task="task-1")
    r = call(lb, "web_search", q, turn="", task="task-1", session="")
    check("C135: a nested call in the NEXT turn is not counted with the last turn's",
          not any(blocked(x) for x in nested) and not blocked(r), [nested, r])

    lb = fresh()
    call(lb, "execute_code", {"code": "..."}, turn="T1", task="task-1")
    rs = [call(lb, "web_search", q, turn="", task="task-1", session="")
          for _ in range(3)]
    check("C135: a loop INSIDE one execute_code script is still broken",
          not blocked(rs[0]) and not blocked(rs[1]) and blocked(rs[2]), rs)

    lb = fresh()
    call(lb, "web_search", q, turn="T1", task="task-1", session="S1")
    call(lb, "read_file", {"path": "/x"}, turn="T1", task="task-1", session="S1")
    lb._on_session_end(session_id="S1", task_id="task-1", turn_id="T1")
    check("C135: on_session_end drops the finished turn's counters",
          not lb._counts and not lb._order, (lb._counts, lb._order))
    check("C135: on_session_end drops the task -> turn attribution",
          not getattr(lb, "_task_turn", {}), getattr(lb, "_task_turn", None))

    lb = fresh()
    call(lb, "web_search", q, turn="T1", task="task-1", session="S1")
    call(lb, "web_search", q, turn="U1", task="task-2", session="S2")
    lb._on_session_end(session_id="S1", task_id="task-1", turn_id="T1")
    check("on_session_end leaves another session's live turn alone",
          len(lb._counts) == 1, lb._counts)

    lb = fresh()
    for i in range(40):
        call(lb, "web_search", q, turn="T%d" % i, task="task-%d" % i)
    check("memory stays bounded (scopes and task map)",
          len(lb._order) <= lb._MAX_SCOPES and len(lb._counts) <= lb._MAX_SCOPES
          and len(getattr(lb, "_task_turn", {})) <= getattr(lb, "_MAX_TASKS", 64),
          (len(lb._order), len(lb._counts)))

    lb = fresh()
    check("never raises on junk input",
          call(lb, "web_search", None) is None
          and lb._pre_tool_call() is None and lb._on_session_end() is None)

    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
