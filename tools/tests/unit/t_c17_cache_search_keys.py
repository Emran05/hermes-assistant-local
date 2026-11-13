#!/usr/bin/env python3
"""C17 — market search keys no longer pile up in _widget_cache.

_cached never evicts, and /api/markets/search caches one key per typed query
(mkt_srch:<q>), so every query the add-ticker box ever sent stayed in the dict
until a settings POST cleared it. Expired search keys are now dropped and at
most _MKT_SRCH_CAP live ones are kept; every other key is left alone.

Runs the REAL code: server.py's _cached is pulled out by AST and the whole of
expanders_extra.py is exec'd with its globals stubbed (no network)."""
import ast, io, os, re, sys, time, types, urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")
EXTRA = os.path.join(REPO, "dashboard", "expanders_extra.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %r" % (detail,)))
    if not cond:
        FAILS.append(name)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t


def build():
    clock = Clock()
    fake_time = types.SimpleNamespace(time=clock.time, localtime=time.localtime,
                                      strftime=time.strftime, sleep=lambda s: None)
    calls = []

    def http_json(url, timeout=None):
        calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [""])[0]
        return {"quotes": [{"symbol": q.upper()[:5] or "X", "quoteType": "EQUITY", "shortname": q}]}

    g = {"__name__": "srv_c17", "time": fake_time, "os": os, "re": re, "urllib": urllib,
         "_http_json": http_json, "EXPANDERS": {},
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "get_settings": lambda: {}, "get_access": lambda: {"dirs": []}, "HOME": "/tmp"}
    # server.py's real cache: _cached plus its last-good fallback / retry
    # backoff (C12) and the _WidgetCache whose pop also forgets that fallback,
    # so an evicted search key cannot live on in _widget_cache_good
    names = {"_cached", "_cache_failed", "_WidgetCache", "_widget_cache",
             "_widget_cache_good", "_widget_cache_fails", "_CACHE_RETRY_S", "_CACHE_STALE_X"}
    tree = ast.parse(io.open(SERVER, encoding="utf-8").read())
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names) or (
                isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in names for t in node.targets)):
            exec(compile(ast.Module([node], []), SERVER, "exec"), g)
    exec(compile(io.open(EXTRA, encoding="utf-8").read(), EXTRA, "exec"), g)
    return g, clock, calls


def srch_keys(g):
    return [k for k in g["_widget_cache"] if str(k).startswith("mkt_srch:")]


def main():
    g, clock, calls = build()
    ms = g["market_search"]
    g["_widget_cache"]["weather:Paris"] = (clock.t, {"t": 1})        # someone else's key
    g["_widget_cache"]["mkt_q:AAPL"] = (clock.t - 10_000, {"p": 1})   # stale, not ours

    print("a burst of distinct queries (typing in the add-ticker box)")
    for i in range(500):
        ms("query %d" % i)
        clock.t += 0.05                    # 500 queries in 25 s, all inside the TTL
    n = len(srch_keys(g))
    check("search keys stay bounded under a burst", n <= 64, n)
    ng = len([k for k in g["_widget_cache_good"] if str(k).startswith("mkt_srch:")])
    check("...and so do their last-good copies (C12's fallback store)", ng <= 64, ng)
    check("the newest query is still cached", "mkt_srch:query 499" in g["_widget_cache"])
    before = len(calls)
    ms("query 499")
    check("a repeat inside the TTL is a cache hit (no fetch)", len(calls) == before, len(calls) - before)
    check("the answer is the real search result", ms("query 499")["results"][0]["symbol"] == "QUERY", ms("query 499"))

    print("time passes")
    clock.t += 61
    ms("fresh")
    keys = srch_keys(g)
    check("every expired search key is dropped on the next new query", keys == ["mkt_srch:fresh"],
          (len(keys), keys[:3]))
    check("other families are never touched", "weather:Paris" in g["_widget_cache"]
          and "mkt_q:AAPL" in g["_widget_cache"])

    print("a day of occasional searches")
    g2, clock2, _ = build()
    for i in range(2000):
        g2["market_search"]("t%d" % i)
        clock2.t += 45                     # one every 45 s for ~25 h
    n2 = len(srch_keys(g2))
    check("one day of searches leaves only the live ones", n2 <= 2, n2)
    check("an empty query caches nothing", g2["market_search"]("  ")["results"] == [] and
          "mkt_srch:" not in g2["_widget_cache"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAILS.append("crashed: %s" % e)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len([f for f in FAILS if not f.startswith("crashed")]),
                                          len(FAILS)))
    sys.exit(1 if FAILS else 0)
