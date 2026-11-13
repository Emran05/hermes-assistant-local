#!/usr/bin/env python3
"""Review X-approval-flow-4: Needs-you approval rows were keyed by JOB id.

BEFORE: _ny_collect_approval built every row as `approval:<job id>` with
received_at = the job's ts.  One turn can raise a card per action (each with
its own dashboard-minted approval_id, k03), and approvals are not a threaded
source, so B04's supersede never clears their decisions: marking the first
"Approval needed" row Done (or snoozing it) silently hid every LATER approval
of the same turn — a different command, never seen.

AFTER: the row is `approval:<job id>:<approval_id>` and received_at is the
card's own `_ts`; a head without an approval_id keeps the old key.

Real aux_needsyou.py, throwaway HOME.  Against the pre-fix module the two
approvals share an id and the second is hidden by the first one's Done.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()

JOB = {"id": "job-1", "session": "s1", "state": "approval", "done": False,
       "ts": NOW - 300,
       "approval": {"command": 'computer_use click on element #4 in "Mail"',
                    "approval_id": "a1a1a1a1a1a1a1a1", "_ts": NOW - 120}}
g["CHAT_JOBS"]["job-1"] = JOB

rows = g["_ny_collect_approval"]({}, NOW)
E.check("one approval row", len(rows) == 1, rows)
first = rows[0]["id"] if rows else ""
E.check("the row names the card, not just the job",
        first == "approval:job-1:a1a1a1a1a1a1a1a1", first)
E.check("received_at is when the CARD was raised",
        rows and abs(rows[0]["received_at"] - (NOW - 120)) < 1e-6, rows)

p = g["_ny_build"](NOW)
g["_ny_cache"]["payload"] = p
E.check("the first card is in Now", first in E.visible_ids(p), E.visible_ids(p))
r = g["_ny_act_handler"](E.Ctx(body={"id": first, "action": "done"}))
E.check("Done on the first card", isinstance(r, dict) and r.get("ok"), r)

# the owner answers card 1 in chat; the agent's next action raises card 2
JOB["approval"] = {"command": 'computer_use type "wire $5,000 to ..." in "Mail"',
                   "approval_id": "b2b2b2b2b2b2b2b2", "_ts": NOW - 5}
rows = g["_ny_collect_approval"]({}, NOW)
second = rows[0]["id"] if rows else ""
E.check("a different card gets a different id", second and second != first,
        (first, second))
p = g["_ny_build"](NOW)
E.check("the second card is NOT hidden by the first one's Done",
        second in E.visible_ids(p), E.visible_ids(p))
E.check("...and it is in Now", second in [i["id"] for i in p["now"]],
        [i["id"] for i in p["now"]])

# a Snooze on card 2 does not reach card 3 either
g["_ny_cache"]["payload"] = p
r = g["_ny_act_handler"](E.Ctx(body={"id": second, "action": "snooze",
                                     "until": "1h"}))
E.check("Snooze on the second card", isinstance(r, dict) and r.get("ok"), r)
JOB["approval"] = {"command": "rm -rf ~/Downloads/old",
                   "approval_id": "c3c3c3c3c3c3c3c3", "_ts": NOW - 1}
p = g["_ny_build"](NOW)
third = "approval:job-1:c3c3c3c3c3c3c3c3"
E.check("a third card is visible despite the snooze", third in E.visible_ids(p),
        E.visible_ids(p))

# an approval head without an id (older hermes_rpc) keeps the job-only key
g["CHAT_JOBS"]["job-2"] = {"id": "job-2", "session": "s2", "state": "approval",
                           "done": False, "ts": NOW - 60,
                           "approval": {"command": "echo hi"}}
ids = [r["id"] for r in g["_ny_collect_approval"]({}, NOW)]
E.check("no approval_id -> approval:<job>", "approval:job-2" in ids, ids)

# a hostile approval_id cannot smuggle separators or markup into the id
g["CHAT_JOBS"]["job-3"] = {"id": "job-3", "session": "s3", "state": "approval",
                           "done": False, "ts": NOW - 60,
                           "approval": {"command": "x", "_ts": float("inf"),
                                        "approval_id": 'zz"><b>|:'}}
r3 = [r for r in g["_ny_collect_approval"]({}, NOW) if r["id"].startswith("approval:job-3")]
E.check("odd approval_id characters are dropped",
        r3 and r3[0]["id"] == "approval:job-3:zzb", r3)
E.check("a non-finite _ts falls back to the job ts",
        r3 and r3[0]["received_at"] == NOW - 60, r3)

sys.exit(E.finish())
