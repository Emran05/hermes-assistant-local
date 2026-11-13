#!/usr/bin/env python3
"""Regression test for E96 (docs/audits/2026-11-09-codebase-audit.md).

BEFORE: when a provider RAISED and there was no recent good value,
_cached() re-raised before it recorded the retry deadline, so every hub poll,
prewarm tick and pop-out ran a full network attempt during an outage. C12's
"retried after 60 s, doubling per failure" held only for {"error"} dicts.

AFTER: the deadline and the exception are recorded before re-raising; inside
the back-off window the stored failure is raised again WITHOUT calling the
provider; past it the provider is retried and the window doubles (<= ttl);
a success, or an explicit pop()/clear() (an invalidation), forgets it.

The REAL functions are ast-extracted from dashboard/server.py; the clock is a
fake `time` module; no network.
"""
import os
import sys
import traceback
import types
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check

CACHE_NEW = ["_widget_cache_good", "_widget_cache_fails", "_CACHE_RETRY_S",
             "_CACHE_STALE_X", "_WidgetCache", "_cache_failed"]


class Clock:
    def __init__(self):
        self.t = 2_000_000.0

    def time(self):
        return self.t


class Source:
    def __init__(self, val):
        self.next, self.calls = val, 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.next, Exception):     # a fresh one, as a real fetch
            raise type(self.next)(*self.next.args)
        return self.next


def call(cached, key, ttl, src):
    try:
        return ("ok", cached(key, ttl, src))
    except Exception as e:  # noqa: BLE001
        return ("raised", e)


def main():
    clk = Clock()
    ns = dict(time=types.SimpleNamespace(time=clk.time), os=os, urllib=urllib)
    H.extract(["_widget_cache", "_cached"], ns, optional=CACHE_NEW)
    cached = ns["_cached"]

    # -- the outage: fn raises, nothing good to fall back on ------------------
    src = Source(OSError("getaddrinfo failed"))
    r = [call(cached, "crypto", 300, src) for _ in range(10)]
    check("the first call raises the provider's exception",
          r[0][0] == "raised" and isinstance(r[0][1], OSError), r[0])
    check("ten polls inside the back-off window hit the network ONCE (was 10)",
          src.calls == 1, src.calls)
    check("...and every one of them still raises the failure (not None, not data)",
          all(k == "raised" and isinstance(e, OSError) for k, e in r), r)
    check("...the same exception, so callers' except clauses are unchanged",
          all(e is r[0][1] for _, e in r))
    tb_len = len(traceback.extract_tb(r[-1][1].__traceback__))
    check("re-raising does not grow the traceback without bound", tb_len < 6, tb_len)

    clk.t += 59
    call(cached, "crypto", 300, src)
    check("no retry at 59 s", src.calls == 1, src.calls)
    clk.t += 2
    k, _ = call(cached, "crypto", 300, src)
    check("a retry at 61 s, which still raises", src.calls == 2 and k == "raised",
          (src.calls, k))
    clk.t += 61
    call(cached, "crypto", 300, src)
    check("the next window doubles to 120 s (no call at +61)", src.calls == 2, src.calls)
    clk.t += 60
    call(cached, "crypto", 300, src)
    check("...and fires after it", src.calls == 3, src.calls)
    for _ in range(6):                  # windows: 240 -> capped at ttl 300
        clk.t += 301
        call(cached, "crypto", 300, src)
    n0 = src.calls
    clk.t += 299
    call(cached, "crypto", 300, src)
    check("the window never exceeds the ttl (no call at 299 s)", src.calls == n0)
    clk.t += 2
    call(cached, "crypto", 300, src)
    check("...a call past the ttl retries", src.calls == n0 + 1)

    # -- recovery -------------------------------------------------------------
    clk.t += 301
    src.next = {"price": 1}
    check("recovery returns the data", cached("crypto", 300, src) == {"price": 1})
    src.next = OSError("down again")
    clk.t += 301
    call(cached, "crypto", 300, src)          # good value exists: served stale
    n1 = src.calls
    ok_k, v = call(cached, "crypto", 300, src)
    check("after recovery a raise falls back to the good value (C12 unchanged)",
          ok_k == "ok" and v == {"price": 1} and src.calls == n1, (ok_k, v))

    # -- invalidation forgets the stored failure ------------------------------
    src = Source(OSError("x"))
    call(cached, "inv", 300, src)
    ns["_widget_cache"].pop("inv", None)
    src.next = {"v": 1}
    check("pop() forgets the stored failure: the next call refetches",
          cached("inv", 300, src) == {"v": 1} and src.calls == 2, src.calls)
    src = Source(OSError("x"))
    call(cached, "inv2", 300, src)
    ns["_widget_cache"].clear()
    src.next = {"v": 2}
    check("clear() forgets it too", cached("inv2", 300, src) == {"v": 2}
          and src.calls == 2, src.calls)
    # a provider that writes its own fresh value (aux_google's prime loop) wins
    src = Source(OSError("x"))
    call(cached, "primed", 300, src)
    ns["_widget_cache"]["primed"] = (clk.t, {"fresh": True})
    check("a fresh value written straight into _widget_cache is served",
          cached("primed", 300, src) == {"fresh": True} and src.calls == 1)

    # -- an {error} dict with nothing good: unchanged -------------------------
    src = Source({"error": "down"})
    check("an {error} dict is still returned, not raised",
          cached("e", 300, src) == {"error": "down"})
    cached("e", 300, src)
    check("...and backed off as before", src.calls == 1, src.calls)

    # -- other keys are independent -------------------------------------------
    a, b = Source(OSError("a")), Source({"ok": 1})
    call(cached, "ka", 300, a)
    check("a failing key does not affect another key",
          cached("kb", 300, b) == {"ok": 1} and b.calls == 1)

    # -- a plain-dict _widget_cache (several suites stub it) still works ------
    clk2 = Clock()
    ns2 = dict(time=types.SimpleNamespace(time=clk2.time), os=os, urllib=urllib)
    H.extract(["_cached"], ns2, optional=CACHE_NEW)
    ns2["_widget_cache"] = {}
    s2 = Source(OSError("z"))
    r2 = [call(ns2["_cached"], "p", 300, s2) for _ in range(3)]
    check("with a plain dict cache the back-off holds too",
          s2.calls == 1 and all(k == "raised" for k, _ in r2), (s2.calls, r2))

    H.finish()


if __name__ == "__main__":
    main()
