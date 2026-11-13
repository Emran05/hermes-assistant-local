#!/usr/bin/env python3
"""E121 — POST /api/memory/layer takes only JSON booleans.

2026-11-09 audit E121: the route coerced `enabled` and `episodic` with
bool(...), so the STRING "false" switched the memory layer ON (the last of the
C59 cases).  Now any non-boolean value is a 400 and nothing is written.

Real server.py import (the aux chain, aux_memlayer included) + the real
Handler on a free loopback port, throwaway HOME.  No model, no ~/.hermes, no
network.
"""
import json, os, socket, sys, tempfile, threading, urllib.error, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-e121-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(os.path.join(DASH, "chats"), exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
server._ix_cal_lines = lambda: []
from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:300]))


def post(body):
    r = urllib.request.Request("http://127.0.0.1:%d/api/memory/layer" % PORT,
                               data=json.dumps(body).encode(),
                               headers={"Host": "127.0.0.1:%d" % PORT,
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def stored():
    try:
        return (json.load(open(os.path.join(DASH, "settings.json"))) or {}).get("memory_layer")
    except (OSError, ValueError):
        return None


try:
    st, d = post({"enabled": False, "episodic": False})
    ok("a JSON false is accepted", st == 200 and d.get("ok"), (st, d))
    ok("layer stored off", (stored() or {}).get("enabled") is False, stored())
    before = json.dumps(stored(), sort_keys=True)

    for key in ("enabled", "episodic"):
        for bad in ("false", "true", "0", 0, 1, None, [], {}, "off"):
            st, d = post({key: bad})
            ok("%s=%r is a 400" % (key, bad), st == 400 and d.get("ok") is False, (st, d))
            ok("%s=%r wrote nothing" % (key, bad),
               json.dumps(stored(), sort_keys=True) == before, stored())

    st, d = post({"enabled": False, "episodic": "false", "budget_chars": 1200})
    ok("one bad field refuses the whole patch", st == 400, (st, d))
    ok("...and the good fields were not applied either",
       json.dumps(stored(), sort_keys=True) == before, stored())

    st, d = post({"enabled": True})
    ok("a JSON true still turns it on", st == 200 and d["settings"]["enabled"] is True, (st, d))
    st, d = post({"budget_chars": 900})
    ok("a patch without the booleans still works",
       st == 200 and d["settings"]["budget_chars"] == 900 and d["settings"]["enabled"] is True,
       (st, d))
finally:
    srv.shutdown()

print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
