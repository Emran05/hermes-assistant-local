#!/usr/bin/env python3
"""Regression test for C12 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: _cached(key, ttl, fn) stored whatever fn() returned for the whole
ttl. A fetch that failed — {"error": ...} from weather, every markets quote an
error row, the RSS widget's empty list when every feed was unreachable —
replaced the last good payload and stuck for up to 30 minutes after one
network blip.

AFTER:
  * a failed result (an {"error"} dict, all-error rows, or fn raising) never
    replaces the last good one: the good one is served while it is younger
    than 3 x ttl, and the fetch is retried after 60 s, doubling per failure
    up to the ttl;
  * past that window the failure itself comes back (or the exception is
    raised, as before), so a long outage is not papered over;
  * an explicit _widget_cache.pop()/clear() (an invalidation) also forgets the
    last good value;
  * w_rss / w_hackernews report "every source failed" as an error instead of
    an empty list that looks like data.

The REAL functions are ast-extracted from dashboard/server.py; the clock is a
fake `time` module so ttl/backoff are exact; no network.
"""
import os
import sys
import types
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check

CACHE_NEW = ["_widget_cache_good", "_widget_cache_fails", "_CACHE_RETRY_S",
             "_CACHE_STALE_X", "_WidgetCache", "_cache_failed"]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t


def namespace(clock, **extra):
    ns = dict(time=types.SimpleNamespace(time=clock.time), os=os, urllib=urllib)
    ns.update(extra)
    H.extract(["_widget_cache", "_cached"], ns, optional=CACHE_NEW)
    return ns


class Source:
    """fn() for _cached: returns/raises whatever .next is, counts calls."""

    def __init__(self, val):
        self.next, self.calls = val, 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


def main():
    clk = Clock()
    ns = namespace(clk)
    cached = ns["_cached"]
    GOOD = {"items": [{"title": "a"}]}
    BAD = {"configured": True, "error": "weather fetch failed"}

    # -- a failed fetch does not replace the last good one -------------------
    src = Source(GOOD)
    check("a good fetch is returned", cached("k", 900, src) == GOOD)
    clk.t += 100
    check("...and cached for the ttl", cached("k", 900, src) == GOOD and src.calls == 1)
    clk.t += 900
    src.next = BAD
    got = cached("k", 900, src)
    check("an {error} fetch after the ttl serves the last good payload "
          "(was: the error, for 15 min)", got == GOOD and src.calls == 2, got)
    clk.t += 59
    cached("k", 900, src)
    check("...the failure is retried after 60 s, not 15 min (no call at 59 s)",
          src.calls == 2, src.calls)
    clk.t += 2
    cached("k", 900, src)
    check("...a call at 61 s retries", src.calls == 3, src.calls)
    clk.t += 61
    cached("k", 900, src)
    check("...the next retry backs off to 120 s", src.calls == 3, src.calls)
    clk.t += 60
    cached("k", 900, src)
    check("...and fires after it", src.calls == 4, src.calls)
    src.next = {"items": [{"title": "b"}]}
    clk.t += 240
    got = cached("k", 900, src)
    check("recovery replaces the good payload and resets the backoff",
          got == {"items": [{"title": "b"}]} and src.calls == 5, got)

    # -- the stale window is bounded ------------------------------------------
    src = Source(GOOD)
    cached("w", 100, src)
    src.next = BAD
    clk.t += 250                         # good is 250 s old, < 3 x 100
    check("inside 3 x ttl the last good payload is served",
          cached("w", 100, src) == GOOD)
    clk.t += 100                         # 350 s old: past the window
    check("past 3 x ttl the failure itself is reported",
          cached("w", 100, src) == BAD)

    # a retry interval that reaches past the window must not carry the old
    # payload with it (review: weather was served up to 3.5 x ttl)
    src = Source(GOOD)
    cached("w2", 100, src)
    src.next = BAD
    clk.t += 250                         # stale served; retry would be 60 s
    cached("w2", 100, src)
    clk.t += 55                          # 305 s old: inside that 60 s retry
    check("the stale payload is never served past 3 x ttl, even inside a "
          "retry interval", cached("w2", 100, src) == BAD
          and src.calls == 3, src.calls)

    # -- exceptions -----------------------------------------------------------
    src = Source(GOOD)
    cached("x", 300, src)
    src.next = OSError("network down")
    clk.t += 301
    got = None
    try:
        got = cached("x", 300, src)
    except OSError:
        got = "raised"
    check("fn raising with a last good payload serves it (was: raised)",
          got == GOOD, got)
    src2 = Source(OSError("network down"))
    raised = 0
    for _ in range(2):
        try:
            cached("y", 300, src2)
        except OSError:
            raised += 1
    # E96 (2026-11-09): the second call is inside the back-off window, so it
    # re-raises the stored failure WITHOUT a second network attempt (it used
    # to retry on every call, so an outage re-hit the network on every poll).
    check("fn raising with nothing good still raises on every call, but is "
          "backed off (E96)", raised == 2 and src2.calls == 1, (raised, src2.calls))

    # -- a failure with nothing to fall back on -------------------------------
    src = Source(BAD)
    check("no good payload yet -> the failure is returned",
          cached("z", 900, src) == BAD)
    clk.t += 61
    cached("z", 900, src)
    check("...and cached only for the retry interval, not the ttl",
          src.calls == 2, src.calls)

    # -- what counts as failed ------------------------------------------------
    if "_cache_failed" in ns:
        cf = ns["_cache_failed"]
        check("_cache_failed: {error} dict", cf({"error": "x"}) is True)
        check("_cache_failed: every quote an error row",
              cf({"quotes": [{"symbol": "A", "error": True},
                             {"symbol": "B", "error": True}], "asof": None}))
        check("_cache_failed: some quotes fine -> data",
              not cf({"quotes": [{"symbol": "A", "error": True},
                                 {"symbol": "B", "price": 1}]}))
        check("_cache_failed: empty list / empty rows / numbers are data",
              not cf({"items": []}) and not cf([]) and not cf(0)
              and not cf(None) and not cf({"available": False, "reason": "x"}))
    else:
        check("_cache_failed exists in server.py", False)

    # -- invalidation forgets the last good payload ---------------------------
    src = Source(GOOD)
    cached("inv", 300, src)
    ns["_widget_cache"].pop("inv", None)
    src.next = BAD
    check("after an explicit pop() a failing refetch is NOT answered with the "
          "invalidated payload", cached("inv", 300, src) == BAD)
    src = Source(GOOD)
    cached("inv2", 300, src)
    ns["_widget_cache"].clear()
    src.next = BAD
    check("...same after clear()", cached("inv2", 300, src) == BAD)
    check("the cache is still a dict to every aux module",
          isinstance(ns["_widget_cache"], dict))

    # -- the RSS / HN providers report "every source failed" ------------------
    clk2 = Clock()
    net = {"up": True}

    def http_text(url, *a, **k):
        if not net["up"]:
            raise OSError("offline")
        return ('<rss><channel><item><title>Story %s</title>'
                '<link>https://e.example/%s</link></item></channel></rss>'
                % (url[-1], url[-1]))

    def http_json(url, *a, **k):
        if url.endswith("topstories.json"):
            return [1, 2]
        if not net["up"]:
            raise OSError("offline")
        return {"title": "HN %s" % url, "score": 1}

    pns = namespace(clk2, _http_text=http_text, _http_json=http_json,
                    get_settings=lambda: {"rss_feeds": ["https://f.example/1",
                                                        "https://f.example/2"]})
    # w_rss keeps only http(s) story links through _http_url (B18)
    H.extract(["w_rss", "w_hackernews"], pns, optional=["_http_url"])
    first = pns["w_rss"]()
    check("w_rss: a good fetch lists the stories", len(first.get("items", [])) == 2,
          first)
    net["up"] = False
    clk2.t += 901
    after = pns["w_rss"]()
    check("w_rss: every feed down -> the last good stories, not an empty "
          "list for 15 min", after == first, after)
    hn1 = pns["w_hackernews"]()
    pns["_widget_cache"].pop("hn", None)
    hn = pns["w_hackernews"]()
    check("w_hackernews: every story failing is reported as an error",
          bool(hn.get("error")) and hn.get("stories") == [], (hn1, hn))

    H.finish()


if __name__ == "__main__":
    main()
