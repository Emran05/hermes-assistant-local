#!/usr/bin/env python3
"""Regression test for C07.1 (round 2, w9; follow-up to audit C07).

BEFORE: (1) drilling the SERVING model while it was idle-suspended (or down
without the marker) woke nothing: `_pro_wait_ready` polled a dead server for
120 s and the drill failed with "model never came up". (2) A case whose
completion raised a connection refusal, a timeout or a non-JSON body was
scored as a FAILED case ("case error: URLError ...") and that score was
stored in promotion.json as the model's verdict — a server crash turned a
good model into "failed the drill", and switch_model then warned about it.

AFTER: `_pro_wake_serving()` calls agent_wake() (by name) first when the
serving model is asleep or down-and-unpaused; a case that raises one of
`_PRO_INFRA_EXC` (OSError — URLError, socket timeout, connection refused —,
http.client.HTTPException, json.JSONDecodeError) aborts the run with `err`
set and NO verdict stored; `last.pass` is None for an aborted run. A real
case-logic exception is still scored as that case's failure.

Exec's the WHOLE real dashboard/aux_promotion.py into a stub namespace
(throwaway DATA dir), then replaces only the one network call
(`_pro_completion`) and the clock (`time`, so the 120 s wait is instant).
No model, no network. On the old code the wake checks fail (agent_wake is
never called and the drill errors) and the infra checks fail (a verdict is
written to promotion.json).
"""
import io
import json
import os
import socket
import sys
import tempfile
import time as _real_time
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


class FakeClock:
    """time stand-in: sleep() advances the clock instead of blocking."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


OK_RESP = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


class Server:
    """The model server: down until woken; then answers, or raises `fail`
    on the `fail_at`-th completion after it came up."""

    def __init__(self, up=False, fail=None, fail_at=None):
        self.up, self.fail, self.fail_at, self.n = up, fail, fail_at, 0

    def completion(self, mid, messages, tools=None, max_tokens=0, timeout=0):
        if not self.up:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        self.n += 1
        if self.fail is not None and self.fail_at is not None and self.n >= self.fail_at:
            raise self.fail
        return OK_RESP


def load(d, server, asleep=False, online=None, paused=False, wake_ok=True):
    marker = os.path.join(d, "agent-idle-suspended")
    if asleep:
        open(marker, "w").close()
    wakes = []

    def agent_wake(wait=True, timeout=90):
        wakes.append({"wait": wait, "timeout": timeout})
        if not wake_ok:
            return False
        try:
            os.remove(marker)
        except OSError:
            pass
        server.up = True
        return True

    def read_json(p, default):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return default

    ns = {"__name__": "aux_promotion_test", "DATA": d,
          "MODEL_URL": "http://127.0.0.1:1/v1/models", "CHAT_JOBS": {},
          "read_json": read_json,
          "_model_registry": lambda: [{"id": "org/model"}],
          "_model_downloaded": lambda m: True, "_hf_cache_dir": lambda m: d,
          "active_model": lambda: "org/model", "agent_paused": lambda: paused,
          "agent_idle_suspended": lambda: os.path.exists(marker),
          "model_online": (online if online is not None else (lambda: server.up)),
          "agent_wake": agent_wake,
          "switch_model": lambda mid: {"ok": True},
          "models_payload": lambda: {"models": []},
          "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    path = os.path.join(DASH, "aux_promotion.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), ns)
    ns["_pro_completion"] = server.completion   # the only network call
    ns["time"] = FakeClock()                    # the 120 s wait is instant
    ns["_pro_parser"] = lambda mid: None        # no mlx_lm import
    return ns, wakes


def stored(d):
    try:
        with open(os.path.join(d, "promotion.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run(ns):
    ns["_PRO_STATE"].update(running="org/model")
    ns["promotion_drill_run"]("org/model")
    return ns["_PRO_STATE"]


def test_wake():
    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=False)
        ns, wakes = load(d, srv, asleep=True)
        st = run(ns)
        check("idle-suspended serving model: the drill calls agent_wake() first "
              "(was: waited 120 s for a model nothing woke)", len(wakes) == 1, wakes)
        check("...waiting for it, with the load budget",
              wakes and wakes[0]["wait"] is True and wakes[0]["timeout"] >= 120, wakes)
        check("...then the drill runs and finishes without an error",
              st.get("error") is None, st.get("error"))
        check("...and its verdict is stored", "org/model" in stored(d), stored(d))
        last = st.get("last") or {}
        check("...last.pass is a real boolean for a finished run",
              isinstance(last.get("pass"), bool), last)

    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=False)
        ns, wakes = load(d, srv, asleep=False)      # crashed, no marker
        st = run(ns)
        check("down WITHOUT the asleep marker (crash): also woken",
              len(wakes) == 1 and st.get("error") is None, (wakes, st.get("error")))

    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=True)
        ns, wakes = load(d, srv)
        st = run(ns)
        check("serving model already online: no wake", wakes == [], wakes)
        check("...and the drill stores its verdict", "org/model" in stored(d)
              and st.get("error") is None)

    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=False)
        ns, wakes = load(d, srv, paused=True)
        run(ns)
        check("paused (deliberate park) and no idle marker: the drill never wakes it",
              wakes == [], wakes)

    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=False)
        ns, wakes = load(d, srv, asleep=True, wake_ok=False)
        st = run(ns)
        err = st.get("error") or ""
        check("a wake that does not come up: err says so",
              "never came up" in err and "did not wake" in err, err)
        check("...no verdict stored", stored(d) == {}, stored(d))
        check("...last.pass is None (no verdict either way)",
              (st.get("last") or {}).get("pass", "missing") is None, st.get("last"))


INFRA = [
    ("connection refused (URLError)",
     urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))),
    ("read timeout (socket.timeout)", socket.timeout("timed out")),
    ("server crashed mid-reply (RemoteDisconnected)", None),
    ("non-JSON body (JSONDecodeError)", json.JSONDecodeError("Expecting value", "<html>", 0)),
]


def test_infra():
    import http.client
    for label, exc in INFRA:
        if exc is None:
            exc = http.client.RemoteDisconnected("Remote end closed connection")
        with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
            # n=1 is _pro_wait_ready's probe; cases start at n=2 — fail case 3
            srv = Server(up=True, fail=exc, fail_at=4)
            ns, _ = load(d, srv)
            st = run(ns)
            err = st.get("error") or ""
            check("%s in a case: the run stops with err set" % label,
                  "no verdict stored" in err, err)
            check("...and NO verdict is written to promotion.json (was: stored "
                  "as a failed case)", stored(d) == {}, stored(d))
            check("...last.pass is None, not False",
                  (st.get("last") or {}).get("pass", "missing") is None, st.get("last"))

    with tempfile.TemporaryDirectory(prefix="hermes-c07a-") as d:
        srv = Server(up=True, fail=KeyError("tool_calls"), fail_at=4)
        ns, _ = load(d, srv)
        st = run(ns)
        rec = stored(d).get("org/model") or {}
        bad = [c for c in rec.get("cases", []) if "case error: KeyError" in c.get("detail", "")]
        check("a case-logic exception (KeyError) is still scored as that case's failure",
              st.get("error") is None and bad and not bad[0]["pass"], (st.get("error"), rec))


def main():
    for fn in (test_wake, test_infra):
        try:
            fn()
        except Exception as e:
            FAILS.append(fn.__name__)
            print("  FAIL %s raised %s: %s" % (fn.__name__, type(e).__name__, e))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
