#!/usr/bin/env python3
"""The Pause window (2026-11-12 review, item 5).

A Pause now waits up to PAUSE_DOWN_WAIT_S for the server to go before it
writes `agent-paused`. In that window there is no marker, so a chat (or Wake
now) sent then could restart the model the owner is pausing. server.py keeps
an in-memory "pause in progress" (`_pause_busy`, set by agent_power for the
whole pause) and consults it where a turn or a wake would start the model:

  * POST /api/chat fails fast with the paused reply, no job, nothing stored;
  * POST /api/agent/wake answers 409 {pausing} and starts nothing;
  * a chat worker already running waits for the pause to decide instead of
    waking the model: a pause that worked ends the turn ("nothing was run",
    no wake); a pause that failed lets the turn go on and wake as before.

agent_paused() keeps its meaning (the marker file). Real server.py import
and Handler on a loopback port, throwaway HOME; model/wake are stubs.
"""
import json
import os
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
HOME = tempfile.mkdtemp(prefix="hermes-pausewin-")
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
server.SB_BIN = os.path.join(HOME, "no-shortcuts-cli")
server._ix_cal_lines = lambda: []
server.mlx_admission = lambda: (True, 1.0, 50.0)
server.agent_idle_suspended = lambda: False
server.model_online = lambda: False
WAKES = []
REAL_WAKE = server.agent_wake            # the real one, for the last section
server.agent_wake = lambda wait=True: (WAKES.append(time.time()), False)[1]
TURNS = []


class NoServe(object):
    def __init__(self):
        TURNS.append(1)
        raise server.hermes_rpc.WSError("no serve in this test")


server.hermes_rpc.ServeSession = NoServe

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
        print("  FAIL %s  %s" % (name, str(extra)[:500]))


def req(path, body=None):
    h = {"Host": "127.0.0.1:%d" % PORT, "Content-Type": "application/json"}
    r = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def worker_job(sid):
    def _au(chat):
        chat["messages"].append({"role": "user", "text": "hi", "ts": time.time(),
                                 "awaiting_reply": True})
    server.save_chat_update(sid, _au)
    job = server._new_job(sid)
    th = threading.Thread(target=server._chat_worker, args=(job, sid, "hi"), daemon=True)
    th.start()
    return job, th


def main():
    busy = getattr(server, "_pause_busy", None)
    ok("server.py keeps a 'pause in progress' flag", busy is not None)
    if busy is None:
        print("TESTS %d passed %d failed" % (PASS, FAIL))
        return 1
    print("\nwhile a pause is deciding")
    busy.set()
    try:
        c, d = req("/api/chat", {"message": "hello", "session": "chat-pw-a"})
        ok("THE FIX: /api/chat fails fast with the paused reply", c == 200
           and d.get("ok") is False and "paused" in (d.get("reply") or "") and not d.get("job"), (c, d))
        ok("...and stores nothing", not os.path.exists(os.path.join(CH, "chat-pw-a.json")))
        n = len(WAKES)
        c, d = req("/api/agent/wake", {})
        ok("THE FIX: Wake now is refused and starts nothing", c == 409 and d.get("pausing") is True
           and len(WAKES) == n, (c, d, len(WAKES) - n))
        ok("agent_paused() keeps its meaning (no marker yet)", server.agent_paused() is False)

        print("\na turn already running when the pause began, pause succeeds")
        job, th = worker_job("chat-pw-b")
        time.sleep(0.6)
        ok("THE FIX: the worker waits, it does not wake the model", WAKES == [] and th.is_alive(),
           (WAKES, th.is_alive()))
        with open(server.PAUSE_FILE, "w") as f:
            f.write(str(time.time()))
        busy.clear()
        th.join(5)
        ok("the turn ends: nothing was run", job.get("done") and "nothing was run" in job.get("reply", "")
           and WAKES == [] and TURNS == [], (job.get("reply"), WAKES, TURNS))
        os.remove(server.PAUSE_FILE)
    finally:
        busy.clear()

    print("\na turn waiting on a pause that FAILED goes on as before")
    busy.set()
    job, th = worker_job("chat-pw-c")
    time.sleep(0.4)
    busy.clear()              # the pause failed: no marker
    th.join(5)
    ok("control: it wakes the model as it always did", len(WAKES) == 1, WAKES)

    print("\nagent_wake itself refuses while a Pause decides (every starter is covered)")
    # the eval suite, the idle loop's Telegram wake and a worker already past
    # its own check all call agent_wake directly (2026-11-12 re-review, D)
    starts = []
    real_start = server._mlx_start
    server._mlx_start = lambda uid=None: (starts.append(1), True)[1]
    try:
        busy.set()
        got = REAL_WAKE(wait=False)
        ok("THE FIX: a wake during a Pause starts nothing", starts == [] and not got, (starts, got))
        busy.clear()
        REAL_WAKE(wait=False)
        ok("control: with no Pause deciding, the same call starts the model", starts == [1], starts)
    finally:
        busy.clear()
        server._mlx_start = real_start
    srv.shutdown()
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
