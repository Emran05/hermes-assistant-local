#!/usr/bin/env python3
"""Regression test for audit finding A03 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: `save_chat()` only refused a save that SHORTENED the message list.  Two
workers (the local turn and the Claude auto-route answer) could each load the
same N-message conversation, each append their own reply, and each save N+1 —
both writes succeeded and the second silently replaced the first one's answer.
Equal-length stale snapshots could overwrite metadata or un-register a branch
the same way.  `job.done` was also published by hermes_rpc.run_turn BEFORE
_finish_chat_job() wrote the reply, so the UI could show a reply as delivered
that never reached chats/<session>.json.
(docs/audits/reproduce_20260910.py's chat_lost_update() is the two-append case.)

AFTER: every conversation carries a `rev` counter that each locked save bumps,
and save_chat() refuses a snapshot whose rev is behind the file on disk — the
stale writer gets a caller-visible ValueError instead of quietly winning.
Every read-modify-write caller goes through save_chat_update(), which holds
_state_lock across load + mutate + save, so two concurrent appends produce N+2.
Messages get a stable `id`.  And `done` is now set ONLY by _finish_chat_job,
AFTER the reply is persisted, with `persisted: true/false` alongside it.

Real functions, extracted from the real files with `ast`.  No dashboard, no
network, no model; a throwaway tempfile.mkdtemp() holds the chats.
"""
import ast
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {n.name for n in found} == set(names), \
        "missing %s in %s" % (set(names) - {n.name for n in found}, relative)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"),
         namespace)


def chat_namespace(scratch):
    ns = dict(os=os, json=json, time=time, threading=threading, sys=sys,
              _state_lock=threading.Lock(), CHATS=str(scratch))
    definitions("dashboard/server.py",
                ["read_json", "write_json", "chat_path", "load_chat",
                 "_new_message_id", "stamp_message_ids", "_save_chat_locked",
                 "save_chat", "save_chat_update"], ns)
    return ns


# ---------------------------------------------------------------------------
# 1. THE AUDIT PROBE, as a regression assertion.
#    Two workers load N, each appends, both save.
# ---------------------------------------------------------------------------
def test_two_stale_appends():
    with tempfile.TemporaryDirectory(prefix="hermes-a03-") as d:
        ns = chat_namespace(Path(d))
        ns["save_chat"]("concurrent",
                        {"messages": [{"role": "user", "text": "Hi"}]})
        first = ns["load_chat"]("concurrent")
        second = ns["load_chat"]("concurrent")
        first["messages"].append({"role": "bot", "text": "Local answer"})
        second["messages"].append({"role": "bot", "text": "Claude answer"})

        ns["save_chat"]("concurrent", first)
        err = None
        try:
            ns["save_chat"]("concurrent", second)
        except ValueError as e:
            err = str(e)
        check("THE FIX: the second, stale save is REFUSED (was: silently won)",
              err is not None, "no exception")
        check("...and the error says it is behind the file on disk",
              err is not None and "revision" in err, err)
        stored = [m["text"] for m in ns["load_chat"]("concurrent")["messages"]]
        check("the first worker's answer survives",
              stored == ["Hi", "Local answer"], stored)

        # the refused writer's recovery path: reload and append again
        again = ns["load_chat"]("concurrent")
        again["messages"].append({"role": "bot", "text": "Claude answer"})
        ns["save_chat"]("concurrent", again)
        stored = [m["text"] for m in ns["load_chat"]("concurrent")["messages"]]
        check("a reload-then-retry keeps BOTH answers",
              stored == ["Hi", "Local answer", "Claude answer"], stored)


# ---------------------------------------------------------------------------
# 2. equal-length stale snapshots cannot overwrite metadata either
# ---------------------------------------------------------------------------
def test_stale_metadata():
    with tempfile.TemporaryDirectory(prefix="hermes-a03-") as d:
        ns = chat_namespace(Path(d))
        ns["save_chat"]("meta", {"messages": [{"role": "user", "text": "Hi"}],
                                 "title": "Original"})
        stale = ns["load_chat"]("meta")            # a rename dialog opens…
        ns["save_chat_update"]("meta", lambda c: c.update(pinned=True))
        stale["title"] = "Renamed"                 # …and saves the old copy
        err = None
        try:
            ns["save_chat"]("meta", stale)
        except ValueError as e:
            err = str(e)
        check("an equal-length stale metadata save is refused",
              err is not None, "no exception")
        check("the pin that landed in between survives",
              ns["load_chat"]("meta").get("pinned") is True)


# ---------------------------------------------------------------------------
# 3. save_chat_update: two threads appending give N+2
# ---------------------------------------------------------------------------
def test_concurrent_updates():
    with tempfile.TemporaryDirectory(prefix="hermes-a03-") as d:
        ns = chat_namespace(Path(d))
        ns["save_chat"]("race", {"messages": [{"role": "user", "text": "Go"}]})
        start = threading.Barrier(2)
        errors = []

        def worker(label):
            try:
                start.wait(timeout=5)
                ns["save_chat_update"](
                    "race",
                    lambda c: c["messages"].append({"role": "bot",
                                                    "text": label}))
            except Exception as e:                       # pragma: no cover
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in ("Local answer", "Claude answer")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        check("neither concurrent appender raised", not errors, errors)
        texts = sorted(m["text"]
                       for m in ns["load_chat"]("race")["messages"])
        check("THE FIX: 1 + 2 appends = 3 messages, both replies kept",
              texts == ["Claude answer", "Go", "Local answer"], texts)

        # and at scale
        ns["save_chat"]("many", {"messages": []})
        gate = threading.Barrier(10)

        def bump(i):
            gate.wait(timeout=5)
            for k in range(10):
                ns["save_chat_update"](
                    "many",
                    lambda c: c["messages"].append({"role": "bot",
                                                    "text": "%d-%d" % (i, k)}))

        ts = [threading.Thread(target=bump, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        n = len(ns["load_chat"]("many")["messages"])
        check("100 appends from 10 threads all land", n == 100, n)


# ---------------------------------------------------------------------------
# 4. rev + stable message ids
# ---------------------------------------------------------------------------
def test_rev_and_ids():
    with tempfile.TemporaryDirectory(prefix="hermes-a03-") as d:
        ns = chat_namespace(Path(d))
        ns["save_chat"]("rev", {"messages": [{"role": "user", "text": "one"}]})
        check("the first save stamps rev 1", ns["load_chat"]("rev")["rev"] == 1)
        ns["save_chat_update"]("rev", lambda c: c["messages"].append(
            {"role": "bot", "text": "two"}))
        check("every locked save bumps rev", ns["load_chat"]("rev")["rev"] == 2)

        msgs = ns["load_chat"]("rev")["messages"]
        check("every message has an id", all(m.get("id") for m in msgs), msgs)
        check("ids are distinct", len({m["id"] for m in msgs}) == 2)
        ids = [m["id"] for m in msgs]
        ns["save_chat_update"]("rev", lambda c: c.update(title="T"))
        check("ids are STABLE across later saves",
              [m["id"] for m in ns["load_chat"]("rev")["messages"]] == ids)

        # a file written by an older build has no rev; it must still save
        ns["write_json"](ns["chat_path"]("legacy"),
                         {"messages": [{"role": "user", "text": "old"}]})
        legacy = ns["load_chat"]("legacy")
        legacy["messages"].append({"role": "bot", "text": "new"})
        ns["save_chat"]("legacy", legacy)
        check("a pre-rev chat file still saves (and gains a rev)",
              ns["load_chat"]("legacy")["rev"] == 1)

        # a rev AHEAD of disk is not a lost update (the file was moved aside /
        # restored); it must not be refused
        ahead = ns["load_chat"]("rev")
        ahead["rev"] = 999
        ns["save_chat"]("rev", ahead)
        check("a rev ahead of disk is allowed (only BEHIND is a lost update)",
              ns["load_chat"]("rev")["rev"] == ns["load_chat"]("rev")["rev"])

        # truncate=True stays the explicit opt-out from both guards
        stale = ns["load_chat"]("rev")
        ns["save_chat_update"]("rev", lambda c: c["messages"].append(
            {"role": "bot", "text": "three"}))
        stale["messages"] = stale["messages"][:1]
        ns["save_chat"]("rev", stale, truncate=True)
        check("truncate=True is still the explicit opt-out",
              len(ns["load_chat"]("rev")["messages"]) == 1)


# ---------------------------------------------------------------------------
# 5. `done` is published only after the reply is persisted
# ---------------------------------------------------------------------------
def test_done_after_persist():
    with tempfile.TemporaryDirectory(prefix="hermes-a03-") as d:
        ns = chat_namespace(Path(d))
        definitions("dashboard/server.py", ["_finish_chat_job"], ns)
        order = []
        real_update = ns["save_chat_update"]

        def watched(session, fn):
            order.append(("save", bool(job.get("done"))))
            return real_update(session, fn)

        ns["save_chat_update"] = watched
        ns["save_chat"]("job", {"messages": [{"role": "user", "text": "Hi"}]})
        job = {"reply": "Answer", "ok": True, "done": False}
        ns["_finish_chat_job"](job, "job")
        check("the save happened while the job was still NOT done",
              order == [("save", False)], order)
        check("THE FIX: done is published only after the write",
              job["done"] is True)
        check("...and persisted says it worked", job["persisted"] is True)
        stored = [m["text"] for m in ns["load_chat"]("job")["messages"]]
        check("the reply is in the transcript", stored == ["Hi", "Answer"])

        # a second call must not append the reply twice
        ns["_finish_chat_job"](job, "job")
        check("_finish_chat_job is idempotent (no duplicated reply)",
              len(ns["load_chat"]("job")["messages"]) == 2)

        # a save that cannot succeed must still finish the job, and SAY SO
        def broken(session, fn):
            raise OSError("read-only file system")

        ns["save_chat_update"] = broken
        job2 = {"reply": "Lost", "ok": True, "done": False}
        ns["_finish_chat_job"](job2, "job")
        check("a failed persist still marks the job done (no hung poll)",
              job2["done"] is True)
        check("THE FIX: persisted is False so the UI can say so",
              job2["persisted"] is False)


# ---------------------------------------------------------------------------
# 6. every read-modify-write caller uses save_chat_update (source guard)
# ---------------------------------------------------------------------------
_RMW = {
    "dashboard/server.py": "POST /api/chat user append + _finish_chat_job",
    "dashboard/aux_convos.py": "rename / pin",
    "dashboard/aux_autoroute.py": "the Claude deep answer",
    "dashboard/aux_branch.py": "the parent's branch registration",
    "dashboard/aux_needsyou.py": "the draft prompt",
}


def test_callers_converted():
    for rel, what in _RMW.items():
        src = (REPO / rel).read_text()
        # aux_needsyou resolves server.py globals by NAME at call time
        # (_ny_fn("save_chat_update")), so match the name, not a call.
        check("%s uses save_chat_update (%s)" % (os.path.basename(rel), what),
              "save_chat_update" in src)
    # the load→append→save shape must be gone from the converted files
    for rel in ("dashboard/aux_convos.py", "dashboard/aux_autoroute.py",
                "dashboard/aux_needsyou.py"):
        src = (REPO / rel).read_text()
        check("%s no longer calls the raw save_chat()" % os.path.basename(rel),
              "save_chat(" not in src.replace("save_chat_update(", ""),
              rel)

    server = (REPO / "dashboard/server.py").read_text()
    finish = server.split("def _finish_chat_job")[1].split("\ndef ")[0]
    check("_finish_chat_job sets persisted before done",
          finish.index('job["persisted"]') < finish.index('job["done"] = True'))
    check("nothing in _finish_chat_job runs after done is set",
          finish.split('job["done"] = True')[1].split("\n\n")[0].strip()
          .startswith("#"),
          finish.split('job["done"] = True')[1][:80])
    rpc = (REPO / "dashboard/hermes_rpc.py").read_text()
    check("hermes_rpc.run_turn no longer publishes done itself",
          "done=True" not in rpc.split("def run_turn")[1])
    check("the poll endpoint reports persisted", '"persisted": job.get' in server)
    ui = (REPO / "dashboard/index.html").read_text()
    check("the UI toasts when a reply was not persisted",
          "d.persisted===false" in ui)
    # aux_index must keep re-indexing conversations saved the new way
    ix = (REPO / "dashboard/aux_index.py").read_text()
    check("aux_index wraps save_chat_update too (search stays fresh)",
          "def save_chat_update(session, mutate_fn):" in ix)


def main():
    print("A03: concurrent chat appends overwrite each other "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    print("\n1. the audit probe: two stale loads, two appends")
    test_two_stale_appends()
    print("\n2. equal-length stale metadata saves")
    test_stale_metadata()
    print("\n3. concurrent save_chat_update")
    test_concurrent_updates()
    print("\n4. rev counter and stable message ids")
    test_rev_and_ids()
    print("\n5. done is published after the reply is persisted")
    test_done_after_persist()
    print("\n6. every read-modify-write caller is converted")
    test_callers_converted()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
