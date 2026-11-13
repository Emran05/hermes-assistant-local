#!/usr/bin/env python3
"""2026-10-08 audit, P3 prose (watchtower): two unbounded inputs.

* `_clamp_int` caught TypeError/ValueError but not OverflowError, and
  json.loads accepts `Infinity`, so POST /api/watchtower {"op":"set_daily_cap",
  "n":Infinity} (or any clamped int field) answered 500.
* `_intel_fetch_feed` read the whole body with no cap.
Drives the real aux_watchtower.py (see _wt_harness.py); no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def main():
    print("watchtower P3: Infinity and oversized feeds")
    g, _home, _data = H.load()
    ci = g["_clamp_int"]
    try:
        v = ci(float("inf"), 1, 200, 20)
    except Exception as e:                       # the old code raised here
        v = "raised %s" % type(e).__name__
    check("_clamp_int(Infinity) falls back to the default", v == 20, v)
    check("_clamp_int(-Infinity) falls back to the default", ci(float("-inf"), 1, 200, 7) == 7)
    check("_clamp_int still clamps a number", ci("250", 1, 200, 20) == 200)

    body = json.loads('{"op": "set_daily_cap", "n": Infinity}')
    res = g["watchtower_post_handler"](H.Ctx(body=body))
    status = res[1] if isinstance(res, tuple) else 200
    check("set_daily_cap with Infinity is not a 500", status != 500, res)

    big = b'<?xml version="1.0"?><rss><channel><item><title>x</title>' \
          b'<link>https://example.org/a</link></item>' + b" " * (9 * 1024 * 1024) + \
          b"</channel></rss>"
    with H.patch_urlopen(lambda req: H.FakeResp(big, req.full_url)):
        out = g["_intel_fetch_feed"]("https://example.org/feed", "X", "News")
    check("a feed body over the cap is dropped", out == [], len(out))
    small = b'<?xml version="1.0"?><rss><channel><item><title>x</title>' \
            b'<link>https://example.org/a</link></item></channel></rss>'
    with H.patch_urlopen(lambda req: H.FakeResp(small, req.full_url)):
        out = g["_intel_fetch_feed"]("https://example.org/feed", "X", "News")
    check("a normal feed still parses", len(out) == 1, out)
    H.finish()


if __name__ == "__main__":
    main()
