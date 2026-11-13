#!/usr/bin/env python3
"""POST /api/chat/stop, end to end through the dashboard (2026-11-12).

Real server.py import (aux chain included: aux_metrics' MeteredJob,
aux_autoroute's worker wrapper) and the real Handler on a free loopback port,
throwaway HOME. The REAL _chat_worker and hermes_rpc.run_turn run each turn;
only hermes_rpc.ServeSession is a fake serve the test feeds, and the model's
online/wake checks are stubs. No model, no serve, no network.

Checks:
  * /api/chat/stop: unknown job 404 {gone}, wrong-typed job 400, a done job
    200 {already_done}, a second stop 200 {stopping} that sends nothing;
  * /api/chat/poll gains can_stop / stopping / stopped (additive);
  * stop mid-stream: session.interrupt goes out, the streamed text is kept,
    "Stopped by you." follows it, err stays true (ok false), stopped true;
  * the transcript on disk: the user message (its awaiting_reply marker
    cleared), the partial reply with the note, `stopped: true`; GET
    /api/history shows no lost-reply notice;
  * the next /api/chat in that conversation is accepted at once (no 409);
  * a waiting approval: after Stop /api/chat/approve is refused 409 stale and
    serve is only ever told "deny";
  * stop before submission (the model still waking): the job is done at once
    with "nothing was run", and when the wake returns no prompt.submit is sent;
  * a stop counts as owner activity (note_user_activity).
"""
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-stop-route-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server.SB_BIN = os.path.join(HOME, "no-shortcuts-cli")
server._ix_cal_lines = lambda: []
server.agent_paused = lambda: False
server.mlx_admission = lambda: (True, 1.0, 50.0)
server.agent_idle_suspended = lambda: False
ONLINE = [True]
server.model_online = lambda: ONLINE[0]
WAKE_GATE = threading.Event()
WAKES = []
WAKE_RET = [True]


def _wake(wait=True):
    WAKES.append(time.time())
    WAKE_GATE.wait(10)
    return WAKE_RET[0]


server.agent_wake = _wake
ACTIVITY = []
_real_note = server.note_user_activity
server.note_user_activity = lambda: (ACTIVITY.append(time.time()), _real_note())[1]

rpc = server.hermes_rpc
rpc._perm = None
rpc.STOP_GRACE = 2.0


class FakeServe(object):
    current = None

    def __init__(self):
        self.q = queue.Queue()
        self.calls = []
        FakeServe.current = self

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append((method, dict(params or {})))
        if on_sent:
            on_sent()
        if method == "session.create":
            return {"session_id": "s1", "stored_session_id": "k1"}
        if method == "prompt.submit":
            return {"status": "streaming"}
        if method == "session.interrupt":
            self.push("message.complete", text="Operation interrupted.",
                      status="interrupted")
            return {"status": "interrupted"}
        if method == "approval.respond":
            return {"resolved": 1}
        if method == "session.status":
            return {"output": "Agent Running: No\nModel: (unknown)"}
        return {}

    def next_event(self, timeout):
        try:
            return self.q.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None

    def push(self, t, **payload):
        self.q.put({"type": t, "session_id": "s1", "payload": payload})

    def close(self):
        pass

    def methods(self):
        return [m for m, _ in self.calls]


ALL_FAKES = []


def _mk():
    f = FakeServe()
    ALL_FAKES.append(f)
    return f


rpc.ServeSession = _mk

from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:600]))


def req(path, body=None, raw=None):
    h = {"Host": "127.0.0.1:%d" % PORT}
    data = None
    if body is not None or raw is not None:
        data = raw if raw is not None else json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def poll(jid):
    return req("/api/chat/poll?job=" + jid)[1]


def until(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    return None


def disk(sid):
    with open(os.path.join(CH, sid + ".json")) as f:
        return json.load(f)


def start_turn(sid, text="tell me everything"):
    n = len(ALL_FAKES)
    c, d = req("/api/chat", {"message": text, "session": sid})
    ok("POST /api/chat starts a turn (%s)" % sid, c == 200 and d.get("job"), (c, d))
    jid = d.get("job") or ""
    until(lambda: len(ALL_FAKES) > n and server.CHAT_JOBS[jid].get("submitted"))
    return jid, ALL_FAKES[-1]


def test_shapes():
    print("\nroute shapes")
    c, d = req("/api/chat/stop", {"job": "nope"})
    ok("unknown job: 404 {ok:false, gone:true}", c == 404 and d == {"ok": False, "gone": True}, (c, d))
    for bad in ({"job": 5}, {"job": ["x"]}, {}, {"job": ""}):
        c, d = req("/api/chat/stop", bad)
        ok("wrong-typed job %r: 400" % (bad,), c == 400 and d.get("ok") is False, (c, d))
    c, d = req("/api/chat/stop", raw=b"[1,2]")
    ok("non-object body: 400", c == 400, (c, d))


def test_mid_stream():
    print("\nstop mid-stream")
    WAKE_GATE.set()
    sid = "chat-stop-a"
    jid, fake = start_turn(sid)
    p = poll(jid)
    ok("poll: can_stop while running", p.get("can_stop") is True
       and p.get("stopping") is False and p.get("stopped") is False, p)
    ok("poll keeps its existing fields", all(k in p for k in (
        "ok", "state", "text", "status", "approval", "done", "reply", "err",
        "persisted", "deep", "memory_chars")), sorted(p))
    fake.push("message.delta", text="The first part of a very long answer")
    until(lambda: "first part" in (poll(jid).get("text") or ""))
    n_act = len(ACTIVITY)
    c, d = req("/api/chat/stop", {"job": jid})
    ok("stop: 200 {ok:true, stopping:true}", c == 200 and d.get("ok") is True
       and d.get("stopping") is True, (c, d))
    ok("a stop is owner activity", len(ACTIVITY) > n_act)
    c2, d2 = req("/api/chat/stop", {"job": jid})
    ok("second stop: 200 {stopping} (or already done)", c2 == 200 and (
        d2.get("stopping") or d2.get("already_done")), (c2, d2))
    fin = until(lambda: (lambda q: q if q.get("done") else None)(poll(jid)))
    ok("the job ends", bool(fin), fin)
    fin = fin or {}
    ok("session.interrupt sent once", fake.methods().count("session.interrupt") == 1,
       fake.methods())
    ok("poll: stopped, not stoppable, err (ok false)",
       fin.get("stopped") is True and fin.get("can_stop") is False
       and fin.get("stopping") is False and fin.get("err") is True, fin)
    ok("poll reply keeps the text and adds the note",
       (fin.get("reply") or "").startswith("The first part of a very long answer")
       and (fin.get("reply") or "").rstrip().endswith("Stopped by you."), fin.get("reply"))
    ok("persisted", fin.get("persisted") is True, fin)
    msgs = disk(sid)["messages"]
    ok("transcript: user message then the stopped reply",
       [m.get("role") for m in msgs] == ["user", "bot"], msgs)
    ok("transcript: the awaiting_reply marker is cleared",
       not any(m.get("awaiting_reply") for m in msgs), msgs)
    ok("transcript: partial text + note, marked stopped",
       msgs[-1]["text"].startswith("The first part") and "Stopped by you." in msgs[-1]["text"]
       and msgs[-1].get("stopped") is True, msgs[-1])
    c, h = req("/api/history?session=" + sid)
    ok("history: no lost-reply notice", c == 200 and not any(
        m.get("kind") == "lost_reply" for m in h.get("messages", [])), h)
    c, d = req("/api/chat/stop", {"job": jid})
    ok("stop after done: 200 {already_done}", c == 200 and d.get("already_done") is True, (c, d))
    ok("...and nothing is sent", fake.methods().count("session.interrupt") == 1)
    # the conversation takes a new turn at once
    c, d = req("/api/chat", {"message": "and now?", "session": sid})
    ok("THE FEATURE: the next /api/chat is accepted (no stuck busy)",
       c == 200 and d.get("job") and not d.get("busy"), (c, d))
    nj = d.get("job")
    if nj:
        until(lambda: server.CHAT_JOBS[nj].get("submitted"))
        ALL_FAKES[-1].push("message.complete", text="fine", status="complete")
        until(lambda: poll(nj).get("done"))


def test_approval():
    print("\nstop with an approval waiting")
    sid = "chat-stop-b"
    jid, fake = start_turn(sid, "clean the folder")
    fake.push("approval.request", command="rm -rf ~/quokka")
    p = until(lambda: (lambda q: q if q.get("state") == "approval" else None)(poll(jid)))
    aid = ((p or {}).get("approval") or {}).get("approval_id")
    ok("the card is up", bool(aid), p)
    c, d = req("/api/chat/stop", {"job": jid})
    ok("stop accepted", c == 200 and d.get("ok"), (c, d))
    c, d = req("/api/chat/approve", {"job": jid, "choice": "approve", "approval_id": aid})
    ok("THE FEATURE: Approve after Stop is refused 409 stale",
       c == 409 and d.get("ok") is False and d.get("stale") is True, (c, d))
    fin = until(lambda: (lambda q: q if q.get("done") else None)(poll(jid))) or {}
    responds = [pp.get("choice") for m, pp in fake.calls if m == "approval.respond"]
    ok("serve was only ever told deny", responds and set(responds) == {"deny"}, responds)
    ok("the reply says the approval was denied and the owner stopped it",
       "denied" in (fin.get("reply") or "") and "Stopped by you." in (fin.get("reply") or ""),
       fin.get("reply"))


def test_before_submit():
    print("\nstop while the model is waking (before submission)")
    WAKE_GATE.clear()
    ONLINE[0] = False
    sid = "chat-stop-c"
    n = len(ALL_FAKES)
    c, d = req("/api/chat", {"message": "hello", "session": sid})
    jid = d.get("job") or ""
    ok("turn accepted", c == 200 and jid, (c, d))
    until(lambda: WAKES)
    t0 = time.time()
    c, d = req("/api/chat/stop", {"job": jid})
    ok("stop before submit: 200 {stopped, nothing_run}", c == 200
       and d.get("stopped") is True and d.get("nothing_run") is True, (c, d))
    p = poll(jid)
    ok("THE FEATURE: done at once, without waiting for the wake",
       p.get("done") is True and time.time() - t0 < 2, p)
    ok("the reply says nothing was run", "nothing was run" in (p.get("reply") or ""), p)
    ok("stopped flag", p.get("stopped") is True)
    msgs = disk(sid)["messages"]
    ok("transcript: user + stopped reply, marker cleared",
       [m.get("role") for m in msgs] == ["user", "bot"]
       and not msgs[0].get("awaiting_reply") and msgs[1].get("stopped") is True, msgs)
    # the wake finishes: the worker must not submit anything
    ONLINE[0] = True
    WAKE_GATE.set()
    time.sleep(0.6)
    ok("THE FEATURE: no prompt.submit after the wake returns",
       not any("prompt.submit" in f.methods() for f in ALL_FAKES[n:]),
       [f.methods() for f in ALL_FAKES[n:]])
    ok("the reply is not overwritten afterwards",
       "nothing was run" in (poll(jid).get("reply") or ""))
    ok("transcript holds exactly one reply", len(disk(sid)["messages"]) == 2,
       disk(sid)["messages"])
    ok("THE FIX (review 6): the worker opens no serve connection after a stop "
       "while waking", len(ALL_FAKES) == n, len(ALL_FAKES) - n)


def pre_stopped_turn(sid):
    """A turn stopped while its model was waking; returns (jid, worker done)."""
    c, d = req("/api/chat", {"message": "hello", "session": sid})
    jid = d.get("job") or ""
    until(lambda: len(WAKES) > pre_stopped_turn.n)
    c2, d2 = req("/api/chat/stop", {"job": jid})
    return jid, c2, d2


def test_review_pre_stop_paths():
    print("\n[review 6] pre-submit stop, then the wake FAILS")
    WAKE_GATE.clear()
    ONLINE[0] = False
    WAKE_RET[0] = False
    pre_stopped_turn.n = len(WAKES)
    jid, c, d = pre_stopped_turn("chat-stop-e")
    ok("stop accepted before submit", d.get("nothing_run") is True, d)
    WAKE_GATE.set()
    time.sleep(0.6)
    ok("THE FIX: the wake failure does not overwrite the stopped reply",
       (poll(jid).get("reply") or "").startswith("Stopped by you before it reached"),
       poll(jid).get("reply"))
    WAKE_RET[0] = True
    ONLINE[0] = True

    print("\n[review 6] pre-submit stop while session.create is in flight, which then raises")
    gate, hold = threading.Event(), threading.Event()
    real = FakeServe.call

    def call(self, method, params, timeout=30, on_sent=None):
        if method == "session.create" and not hold.is_set():
            hold.set()
            gate.wait(5)
            raise rpc.WSError("timeout waiting for session.create")
        return real(self, method, params, timeout, on_sent)
    FakeServe.call = call
    try:
        c, d = req("/api/chat", {"message": "hello", "session": "chat-stop-f"})
        jid = d.get("job") or ""
        until(hold.is_set)
        c, d = req("/api/chat/stop", {"job": jid})
        ok("stop accepted before submit", d.get("nothing_run") is True, d)
        gate.set()
        time.sleep(0.6)
    finally:
        FakeServe.call = real
    ok("THE FIX: the worker's error path does not overwrite the stopped reply",
       (poll(jid).get("reply") or "").startswith("Stopped by you before it reached"),
       poll(jid).get("reply"))
    ok("...and the transcript holds that one reply",
       [m.get("role") for m in disk("chat-stop-f")["messages"]] == ["user", "bot"]
       and "nothing was run" in disk("chat-stop-f")["messages"][-1]["text"],
       disk("chat-stop-f")["messages"])


def test_review_finish_once():
    print("\n[review 6] two finishers at once append ONE reply")
    sid = "chat-stop-g"

    def _au(chat):
        chat["messages"].append({"role": "user", "text": "q", "ts": time.time(),
                                 "awaiting_reply": True})
    server.save_chat_update(sid, _au)
    job = server._new_job(sid)
    job.update(reply="the reply", ok=True, state="done")
    real = server.save_chat_update
    first = threading.Event()

    def slow(sess, fn, *a, **k):
        if sess == sid and not first.is_set():
            first.set()
            time.sleep(0.4)          # the other finisher arrives meanwhile
        return real(sess, fn, *a, **k)
    server.save_chat_update = slow
    try:
        ts = [threading.Thread(target=server._finish_chat_job, args=(job, sid)) for _ in range(2)]
        ts[0].start()
        until(first.is_set)
        ts[1].start()
        for t in ts:
            t.join(5)
    finally:
        server.save_chat_update = real
    bots = [m for m in disk(sid)["messages"] if m.get("role") == "bot"]
    ok("THE FIX: exactly one bot reply on disk", len(bots) == 1, bots)


def test_review_stop_card_and_lock():
    print("\n[review 6] _chat_stop clears the card at once")
    job = {"id": "x1", "session": "s", "state": "approval", "done": False,
           "approval": {"approval_id": "A", "command": "rm -rf x"},
           "submit_claimed": True}
    body, code = server._chat_stop(job)
    ok("stopping", code == 200 and body.get("stopping") is True, body)
    ok("THE FIX: the card is gone the moment Stop returns",
       job.get("approval") is None and job.get("state") == "running", job)
    body, code = server._chat_approve(job, {"choice": "approve", "approval_id": "A"})
    ok("an Approve on it is refused stale", code == 409 and body.get("stale"), (code, body))

    print("\n[review 6] an Approve mid-way when Stop lands never survives the stop")
    entered, release = threading.Event(), threading.Event()

    class Job(dict):
        def __setitem__(self, k, v):
            if k == "_answered_approval":
                entered.set()
                release.wait(2)
            dict.__setitem__(self, k, v)
    job = Job(id="x2", session="s", state="approval", done=False, submit_claimed=True,
              approval={"approval_id": "B", "command": "rm -rf y"})
    out = {}
    ta = threading.Thread(target=lambda: out.update(a=server._chat_approve(
        job, {"choice": "approve", "approval_id": "B"})))
    ta.start()
    entered.wait(2)
    ts = threading.Thread(target=lambda: out.update(s=server._chat_stop(job)))
    ts.start()
    time.sleep(0.3)               # with the lock, the stop is waiting here
    release.set()
    ta.join(3)
    ts.join(3)
    ok("both answered", "a" in out and "s" in out, out)
    ok("THE FIX: no accepted click is left for the turn to forward after the stop",
       "pending_choice" not in job and "pending_approval_id" not in job, dict(job))


def test_autoroute():
    print("\nClaude's parallel answer for a stopped turn")
    persisted = []
    saved = (server._ar_persist, getattr(server, "claude_think", None))
    server._ar_persist = lambda *a, **k: persisted.append(a)
    server.claude_think = lambda *a, **k: {"ok": True, "text": "Claude's answer", "ms": 5}
    try:
        job = {"id": "ar1", "done": True, "stop_requested": True, "ts": time.time()}
        server._ar_think_thread(job, "chat-stop-d", "q", "quick", "r", 3.0)
        ok("THE FEATURE: a stopped turn's Claude answer is not persisted", persisted == [], persisted)
        ok("...and the job says why", (job.get("deep") or {}).get("stopped") is True, job.get("deep"))
        job = {"id": "ar2", "done": True, "ts": time.time()}
        server._ar_think_thread(job, "chat-stop-d", "q", "quick", "r", 3.0)
        ok("control: an unstopped turn's answer is persisted", len(persisted) == 1, persisted)
    finally:
        server._ar_persist, server.claude_think = saved


def main():
    for t in (test_shapes, test_mid_stream, test_approval, test_before_submit,
              test_autoroute, test_review_pre_stop_paths, test_review_finish_once,
              test_review_stop_card_and_lock):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok("%s did not raise" % t.__name__, False, repr(e))
    srv.shutdown()
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
