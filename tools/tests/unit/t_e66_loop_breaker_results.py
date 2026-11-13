#!/usr/bin/env python3
"""E66 (docs/audits/2026-11-09-codebase-audit.md) — loop-breaker blocked the
third identical read even when the answers had changed (a growing log, a page
still loading) or the earlier calls had failed, and its message said nothing
could have changed.

Now a post_tool_call hook records each read's outcome: only repeats whose
earlier answers were identical AND successful count (Hermes' own
"no progress" rule), identical failures have their own limit and message,
and with no result reported the message claims only what is known.

Loads the REAL plugin file; drives pre/post the way Hermes' model_tools does
(post carries status "ok" | "error" | "blocked"). No Hermes, no network.
"""
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
PDIR = os.path.join(REPO, "skills-snapshot", "_plugins", "loop-breaker")
PLUGIN = os.path.join(PDIR, "__init__.py")
PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (("  — " + str(extra)[:300]) if extra else ""))


def fresh():
    spec = importlib.util.spec_from_file_location("loop_breaker_e66", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


K = dict(task_id="task-1", session_id="S1", turn_id="T1")


def run(lb, tool, args, result, status=None):
    """One tool call: pre, then (when allowed) post with Hermes' status."""
    r = lb._pre_tool_call(tool_name=tool, args=args, **K)
    if isinstance(r, dict) and r.get("action") == "block":
        post = getattr(lb, "_post_tool_call", None)
        if post:
            post(tool_name=tool, args=args, result=json.dumps({"error": r["message"]}),
                 status="blocked", **K)
        return r
    if status is None:
        try:
            d = json.loads(result)
            status = "error" if isinstance(d, dict) and d.get("error") else "ok"
        except Exception:
            status = "ok"
    post = getattr(lb, "_post_tool_call", None)
    if post:
        post(tool_name=tool, args=args, result=result, status=status, **K)
    return r


def blocked(r):
    return isinstance(r, dict) and r.get("action") == "block"


print("E66: loop-breaker counts only repeats that got the same answer")

# 1. a growing log: every answer differs
lb = fresh()
a = {"path": "/tmp/build.log"}
rs = [run(lb, "read_file", a, json.dumps({"content": "line\n" * (i + 1)})) for i in range(6)]
check("a read whose answer changes is never blocked", not any(blocked(r) for r in rs), rs)

# 2. transient failures, then success
lb = fresh()
q = {"query": "qwen 3.8 release date"}
rs = [run(lb, "web_search", q, json.dumps({"error": "timeout"})),
      run(lb, "web_search", q, json.dumps({"error": "timeout"})),
      run(lb, "web_search", q, json.dumps({"results": ["x"]}))]
check("two failures then a retry: the 3rd call is allowed", not any(blocked(r) for r in rs), rs)

# 3. the real loop: identical successful answers
lb = fresh()
same = json.dumps({"results": ["a", "b"]})
rs = [run(lb, "web_search", q, same) for _ in range(4)]
check("identical answers: 1st and 2nd allowed", not blocked(rs[0]) and not blocked(rs[1]), rs[:2])
check("identical answers: 3rd and 4th blocked", blocked(rs[2]) and blocked(rs[3]), rs[2:])
msg = rs[2].get("message", "") if blocked(rs[2]) else ""
check("the message says what is true: the same result 2 times",
      "same result 2 times" in msg and "could change" not in msg and "nothing new" not in msg, msg)

# 4. key order in a JSON answer is not a change
lb = fresh()
rs = [run(lb, "web_search", q, '{"a":1,"b":2}'), run(lb, "web_search", q, '{"b":2,"a":1}'),
      run(lb, "web_search", q, '{"a":1,"b":2}')]
check("the same JSON with keys reordered counts as the same answer", blocked(rs[2]), rs)

# 5. same, different, same, same -> the count restarts at the change
lb = fresh()
rs = [run(lb, "browser_snapshot", {}, "A"), run(lb, "browser_snapshot", {}, "B"),
      run(lb, "browser_snapshot", {}, "B"), run(lb, "browser_snapshot", {}, "B")]
check("a changed answer restarts the count", not blocked(rs[2]) and blocked(rs[3]), rs)

# 6. identical failures have their own limit and an honest message
lb = fresh()
err = json.dumps({"error": "HTTP 503"})
rs = [run(lb, "web_extract", {"url": "https://x"}, err) for _ in range(5)]
check("three identical failures are allowed", not any(blocked(r) for r in rs[:3]), rs[:3])
check("the 4th identical failing call is blocked", blocked(rs[3]) and blocked(rs[4]), rs[3:])
fm = rs[3].get("message", "") if blocked(rs[3]) else ""
check("...and the message says it failed, not that nothing changed",
      "failed 3 times" in fm and "same result" not in fm, fm)

# 7. an error status with an ok-looking body is still a failure
lb = fresh()
rs = [run(lb, "web_search", q, "same", status="error") for _ in range(3)]
check("status=error counts as a failure, not as an unchanged answer", not any(blocked(r) for r in rs), rs)

# 8. a state-changing call still resets everything
lb = fresh()
rs = [run(lb, "web_search", q, same), run(lb, "web_search", q, same),
      run(lb, "terminal", {"command": "date"}, json.dumps({"output": "x", "exit_code": 0})),
      run(lb, "web_search", q, same)]
check("a non-read call in between resets the count", not any(blocked(r) for r in rs), rs)

# 9. with NO post_tool_call the old count applies, with an honest message
lb = fresh()
rs = [lb._pre_tool_call(tool_name="web_search", args=q, **K) for _ in range(3)]
check("no results reported: the 3rd call is still blocked", blocked(rs[2]), rs)
m9 = rs[2].get("message", "") if blocked(rs[2]) else ""
check("...and the message does not claim the answer could not change",
      "could change" not in m9 and "anything new" not in m9 and "2 times" in m9, m9)

# 10. the hook is registered and declared
reg = []


class Ctx:
    def register_hook(self, name, fn):
        reg.append(name)


fresh().register(Ctx())
check("post_tool_call is registered", "post_tool_call" in reg, reg)
check("plugin.yaml declares post_tool_call",
      "- post_tool_call" in open(os.path.join(PDIR, "plugin.yaml")).read())
lb = fresh()
check("post never raises on junk", lb._post_tool_call() is None
      and lb._post_tool_call(tool_name="web_search", args=None, result=object()) is None
      and lb._post_tool_call(tool_name="web_search", args={}, result=b"\xff") is None)

# 11. session end still tidies the new state
lb = fresh()
run(lb, "web_search", q, same)
lb._on_session_end(session_id="S1", task_id="task-1", turn_id="T1")
check("on_session_end drops the turn's state", not lb._counts and not lb._order, lb._counts)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
