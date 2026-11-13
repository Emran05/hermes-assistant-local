#!/usr/bin/env python3
"""Review X-approval-flow-5: a Needs-you draft turn must never act.

BEFORE: _ny_start_draft ran a full serve turn on the hidden DRAFT_SESSION
(C38).  No chat view can show that session's approval card and the widget's
pollDraft never rendered one, so:
  * a card nobody could see held the one-draft lock (every other row's Draft
    answered 409) for the ~290 s approval TTL;
  * the ONLY approval such a turn could ever get was a Trust-policy "auto"
    tier — so a stranger's quoted message that slipped the fence could drive
    any auto class (process-control, git-destructive, ...) unattended.

AFTER: the draft job is marked `no_human`, and hermes_rpc._Approvals answers
every approval.request of such a job with an immediate deny at the head of
the queue — auto tier or not — so nothing waits and nothing runs.  The
Needs-you inbox does not list such a job's request as "Approval needed".

Drives the REAL hermes_rpc._Approvals (imported) against a recording fake
serve, and the REAL aux_needsyou._ny_start_draft / _ny_collect_approval in a
throwaway HOME.  Against the pre-fix files the auto tier sends "once" and the
draft job carries no flag.
"""
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
HOME = tempfile.mkdtemp(prefix="hermes-af5-")
os.environ["HOME"] = HOME                        # BEFORE permissions/hermes_rpc
os.makedirs(os.path.join(HOME, ".hermes", "dashboard"), exist_ok=True)
sys.path.insert(0, _HERE)
import _ny_env as E                                             # noqa: E402

sys.path.insert(0, E.DASH)
import hermes_rpc as rpc                                        # noqa: E402


class Serve(object):
    """Records approval.respond; answers like a serve with one pending entry."""

    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def call(self, method, params, timeout=None):
        self.calls.append((method, dict(params)))
        if self.fail_first and len(self.calls) == 1:
            raise rpc.RPCError("refused")
        return {"resolved": 1}


def run(job, tier, fail_first=False):
    srv = Serve(fail_first=fail_first)
    ap = rpc._Approvals(srv, "sid-1", job, [], lambda: {})
    auto = {"auto": "once", "never": "deny"}.get(tier)
    ap.add({"command": "kill -9 1234", "pattern_key": "process-kill",
            "request_id": "r1"}, {"tier": tier, "reason": "policy"}, auto=auto)
    return srv, ap


def choices(srv):
    return [p.get("choice") for m, p in srv.calls if m == "approval.respond"]


print("=== X-approval-flow-5: a no_human turn's requests are denied at once ===")
for tier in ("auto", "ask", "never"):
    job = {"id": "j", "state": "running", "approval": None, "no_human": True}
    srv, ap = run(job, tier)
    E.check("tier %s -> serve is told deny, never once" % tier,
            choices(srv) == ["deny"], choices(srv))
    E.check("tier %s -> nothing left waiting (no card, no lock held)" % tier,
            job.get("state") != "approval" and not ap.q, (job.get("state"), ap.q))
    E.check("tier %s -> the status says why" % tier,
            "never runs anything" in (job.get("status") or ""), job.get("status"))

print("=== ...and ordinary turns are unchanged ===")
job = {"id": "j2", "state": "running", "approval": None}
srv, ap = run(job, "auto")
E.check("a normal job's auto tier still answers once", choices(srv) == ["once"],
        choices(srv))
job = {"id": "j3", "state": "running", "approval": None}
srv, ap = run(job, "ask")
E.check("a normal job's ask tier is still a card",
        choices(srv) == [] and job.get("state") == "approval", (choices(srv), job))

print("=== ...serve refusing the deny falls back to a card (never to once) ===")
job = {"id": "j4", "state": "running", "approval": None, "no_human": True}
srv, ap = run(job, "auto", fail_first=True)
E.check("no 'once' was ever sent", "once" not in choices(srv), choices(srv))
E.check("the request is left as a card for the draft row's Deny",
        job.get("state") == "approval" and
        bool((job.get("approval") or {}).get("approval_id")), job)

print("=== aux_needsyou marks its draft job, and keeps it out of the inbox ===")
g, data = E.load()
started = []
g["_new_job"] = lambda session: {"id": "draftjob", "session": session,
                                 "state": "running", "approval": None,
                                 "done": False, "ts": time.time()}
g["_chat_worker"] = lambda job, session, prompt: started.append(job)
g["save_chat_update"] = lambda session, fn: None
g["access_preamble"] = lambda: ""
res = g["_ny_start_draft"]({"id": "message:+15550005001", "source": "message",
                            "sender": "Ana", "summary": "can you call me?"})
for _ in range(100):
    if started:
        break
    time.sleep(0.01)
job = started[0] if started else {}
E.check("the draft started", isinstance(res, dict) and res.get("ok"), res)
E.check("the draft job is no_human", job.get("no_human") is True, job)
E.check("...and still no_escalate (C37)", job.get("no_escalate") is True, job)
g["CHAT_JOBS"]["draftjob"] = dict(job, state="approval",
                                  approval={"command": "kill -9 1234",
                                            "approval_id": "a5a5a5a5a5a5a5a5",
                                            "_ts": time.time()})
g["CHAT_JOBS"]["chatjob"] = {"id": "chatjob", "session": "chat-x",
                             "state": "approval", "done": False, "ts": time.time(),
                             "approval": {"command": "echo hi",
                                          "approval_id": "b5b5b5b5b5b5b5b5"}}
ids = [r["id"] for r in g["_ny_collect_approval"]({}, time.time())]
E.check("a draft's request is not an 'Approval needed' row",
        not any(i.startswith("approval:draftjob") for i in ids), ids)
E.check("a real chat approval still is",
        "approval:chatjob:b5b5b5b5b5b5b5b5" in ids, ids)

sys.exit(E.finish())
