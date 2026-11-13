#!/usr/bin/env python3
"""C48 — the News switch stops the watchtower's feed egress.

The Data & Network card presents "News and breaking alerts" as the switch for
the news-feed row, but intel_loop -> _intel_gather fetched ~22 feeds hourly
with no master check, and the brief/prewarm fetched rss / HN / GitHub too.
Drives the real aux_watchtower.py (see _wt_harness.py) with urlopen replaced
by a recorder; nothing leaves the process.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
EMPTY = b'<?xml version="1.0"?><rss><channel></channel></rss>'


def main():
    print("C48: News off means no feed fetch")
    g, _home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    calls = {"rss": 0, "gh": 0, "hn": 0, "mk": 0, "cr": 0, "gather": 0}

    def bump(k, ret=None):
        def f(*a, **kw):
            calls[k] += 1
            return ret if ret is not None else {}
        return f
    g["EXPANDERS"]["rss"] = bump("rss", {"sections": []})
    g["EXPANDERS"]["markets"] = bump("mk")
    g["w_github"], g["w_hackernews"], g["w_crypto"] = bump("gh"), bump("hn"), bump("cr")

    # ---- News OFF -------------------------------------------------------
    H.wj(wt, {"master": {"briefings": True, "news": False}})
    with H.patch_urlopen(lambda req: H.FakeResp(EMPTY, req.full_url)) as net:
        g["_intel_gather"]()
    check("News off: the hourly gather fetches no feed", net.calls == [], net.calls[:3])

    real_gather = g["_intel_gather"]
    g["_intel_gather"] = bump("gather")
    # R2-21: the manual poke is POST /api/intel/gather; GET /api/intel is read-only
    post_gather = g["__routes__"]["post"].get("/api/intel/gather")
    res = post_gather(H.Ctx()) if post_gather else {}
    import time
    time.sleep(0.2)
    g["_intel_gather"] = real_gather
    check("News off: POST /api/intel/gather does not start a gather",
          post_gather is not None and calls["gather"] == 0
          and res.get("gathering") is False, (calls["gather"], res))

    check("News off: _rss_data() does not call the rss provider",
          g["_rss_data"]() == {} and calls["rss"] == 0, calls)
    g["_prewarm"]()
    check("News off: prewarm skips rss / GitHub / Hacker News",
          calls["rss"] == 0 and calls["gh"] == 0 and calls["hn"] == 0, calls)
    check("News off: prewarm still warms markets and crypto for the brief",
          calls["mk"] == 1 and calls["cr"] == 1, calls)
    secs, _deg = g["_brief_build_sections"]()
    check("News off: brief news sections say so",
          all(secs[k].get("note") == "News updates are off." for k in ("world", "ai", "underground")),
          {k: secs[k] for k in ("world", "ai", "underground")})
    check("News off: the brief fetched no GitHub / HN",
          calls["gh"] == 0 and calls["hn"] == 0, calls)
    rule = {"type": "rss_keyword", "params": {"keywords": ["outage"]}}
    fire, _sig, ctx = g["_eval_rss"](rule["params"])
    check("News off: a news-keyword test fetches nothing", not fire and calls["rss"] == 0,
          (fire, ctx, calls))

    # ---- News ON: the same paths do fetch (the switch is the gate) -------
    H.wj(wt, {"master": {"briefings": True, "news": True}})
    with H.patch_urlopen(lambda req: H.FakeResp(EMPTY, req.full_url)) as net:
        g["_intel_gather"]()
    check("News on: the gather fetches the feeds", len(net.calls) >= 20, len(net.calls))

    # ---- R2-21: a GET never starts a gather, a POST does ----------------
    g["_intel_gather"] = bump("gather")
    got = g["intel_handler"](H.Ctx(query={"gather": ["1"]}))
    time.sleep(0.2)
    check("R2-21: GET /api/intel?gather=1 starts no gather (a cross-site "
          "navigation is a GET)", calls["gather"] == 0 and "gathering" not in got,
          (calls["gather"], sorted(got)))
    check("R2-21: ... and still answers the read-only store",
          got.get("ok") is True and "items" in got, sorted(got))
    check("R2-21: no GET route is registered for /api/intel/gather",
          "/api/intel/gather" not in g["__routes__"]["get"], sorted(g["__routes__"]["get"]))
    res = post_gather(H.Ctx()) if post_gather else {}
    time.sleep(0.2)
    g["_intel_gather"] = real_gather
    check("R2-21: News on: POST /api/intel/gather starts one gather",
          calls["gather"] == 1 and res.get("gathering") is True, (calls["gather"], res))
    g["_prewarm"]()
    check("News on: prewarm warms the news providers",
          calls["rss"] == 1 and calls["gh"] == 1 and calls["hn"] == 1, calls)

    # ---- a broken watchtower.json reads as News off (C20) -----------------
    with open(wt, "w") as f:
        f.write("{not json")
    with H.patch_urlopen(lambda req: H.FakeResp(EMPTY, req.full_url)) as net:
        g["_intel_gather"]()
    check("corrupt config: no feed fetch", net.calls == [], len(net.calls))
    H.finish()


if __name__ == "__main__":
    main()
