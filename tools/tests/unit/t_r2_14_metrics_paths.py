#!/usr/bin/env python3
"""R2-14 (round 2 of the 2026-10-21 audit): /api/metrics counts what the chat
pipeline can actually do today.

  * The path classifier matched the status "serve backend unavailable, using
    one-shot mode", which C36 removed along with the one-shot fallback, so
    the "oneshot" bucket was dead and a turn refused because serve was down
    was counted as a serve turn. It is now its own "refused" bucket.
  * Approvals "pending" was asked - user-approve - user-deny, so every card
    the dashboard itself ended (expired, timeout, desync-denied,
    respond-failed — the audit actions hermes_rpc._Approvals writes) stayed
    "pending" forever. They now count as resolved and are exposed.

Execs the REAL dashboard/aux_metrics.py with the server globals it uses
stubbed (no sampler thread, no provider overrides) and drives a real
MeteredJob through the status/ok sequence server.py's _chat_worker produces.
On the old code the refused turn reads as "serve" and pending is 4.
"""
import json
import os
import sys
import tempfile
import threading
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = os.path.join(REPO, "dashboard", "aux_metrics.py")
HOME = tempfile.mkdtemp(prefix="hermes-r2-14-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def load():
    def read_json(path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default

    def write_json(path, obj):
        with open(path, "w") as f:
            json.dump(obj, f)

    ns = {"__name__": "aux_metrics_under_test", "HOME": HOME, "HERE": REPO,
          "DATA": DATA, "read_json": read_json, "write_json": write_json,
          "_cached": lambda key, ttl, fn: fn(), "_widget_cache": {},
          "_state_lock": threading.Lock(), "CHAT_JOBS": {},
          "_jobs_lock": threading.Lock(), "agent_paused": lambda: False,
          "active_model": lambda: "test-model", "model_online": lambda: False,
          "_mlx_footprint_gb": lambda: None,
          "register_get": lambda *a: None, "register_post": lambda *a: None,
          # no sampler thread, no wrapped providers in a unit test
          "_metrics_thread_started": True, "_met_wrapped": True}
    exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
    return ns


NS = load()


def run_job(status_seq, submitted=False, ok=False):
    job = NS["MeteredJob"]({"id": "j%d" % len(NS["MET_RINGS"]["turn"]),
                            "session": "chat-x", "state": "running", "text": "",
                            "status": "", "approval": None, "reply": "",
                            "ok": False, "done": False})
    for st in status_seq:
        job["status"] = st
    if submitted:
        job["_submitted_ts"] = job.t0 + 0.1
        job["text"] = "partial"
    job.update(reply="r", ok=ok, state="done")
    job["done"] = True
    return NS["MET_RINGS"]["turn"][-1]


def test_refused_path():
    rec = run_job(["serve backend unavailable — nothing was run"])
    check("THE FIX: a turn refused because serve was down is path 'refused'",
          rec.get("path") == "refused", rec)
    rec2 = run_job(["using terminal", ""], submitted=True, ok=True)
    check("a normal serve turn is still 'serve'", rec2.get("path") == "serve", rec2)
    rec3 = run_job(["Waking the model from sleep — about 30s…"])
    check("a turn that failed some other way before submit is not 'refused'",
          rec3.get("path") == "serve", rec3)
    stats = NS["_met_stats_from_turns"](list(NS["MET_RINGS"]["turn"]))
    check("THE FIX: /api/metrics paths has serve + refused (no dead 'oneshot')",
          stats["paths"] == {"serve": 2, "refused": 1}, stats["paths"])


def test_approval_endings():
    rows = (["asked"] * 6 + ["user-approve", "user-deny", "expired", "timeout",
                             "desync-denied", "respond-failed"]
            + ["auto-approved", "auto-denied"])
    with open(NS["PERM_LOG"], "w") as f:
        for a in rows:
            f.write(json.dumps({"ts": 1, "action": a, "command": "x"}) + "\n")
    c = NS["_met_approval_counts"]()
    check("THE FIX: cards the dashboard ended are not pending",
          c.get("pending") == 0, c)
    check("THE FIX: ...and each ending is exposed",
          (c.get("expired"), c.get("timeout"), c.get("desync_denied"),
           c.get("respond_failed")) == (1, 1, 1, 1), c)
    check("requested / approved / denied keep their meaning",
          (c["requested"], c["approved"], c["denied"]) == (8, 2, 2), c)
    with open(NS["PERM_LOG"], "a") as f:
        f.write(json.dumps({"ts": 2, "action": "asked", "command": "y"}) + "\n")
    check("a card still waiting is pending",
          NS["_met_approval_counts"]()["pending"] == 1,
          NS["_met_approval_counts"]())


def test_payload():
    p = NS["metrics_payload"]()
    check("/api/metrics builds", p.get("ok") is True, p.get("error"))
    check("...carries the refused bucket and the approval endings",
          "refused" in p["turns"]["paths"] and "timeout" in p["approvals"],
          (p["turns"]["paths"], p["approvals"]))


def main():
    for t in (test_refused_path, test_approval_endings, test_payload):
        print("-- %s" % t.__name__)
        try:
            t()
        except Exception:
            FAILS.append(t.__name__ + " raised")
            print("  FAIL %s raised:\n%s" % (t.__name__, traceback.format_exc()))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
