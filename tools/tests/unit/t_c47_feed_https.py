#!/usr/bin/env python3
"""C47 — no news feed is fetched over cleartext http.

The CNN world feed was http://rss.cnn.com/… (that host has no https, and the
feed has not published since 2023-04), and an owner-added http feed went out
in cleartext too; their headlines reach the brief, Telegram and two model
prompts. Drives the real _intel_feeds / _intel_fetch_feed from
aux_watchtower.py (see _wt_harness.py) with urlopen replaced by a recorder.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
RSS = (b'<?xml version="1.0"?><rss><channel><item><title>Hello</title>'
       b'<link>https://example.org/a</link></item></channel></rss>')


def main():
    print("C47: feeds are https only")
    g, _home, _data = H.load()
    g["get_settings"] = lambda: {"intel_feeds": ["http://blog.example.org/feed"]}
    feeds = g["_intel_feeds"]()
    builtin = list(g["_INTEL_FEEDS"]) + list(g["_WORLD_FEEDS"])
    check("every built-in feed is https",
          all(f[2].startswith("https://") for f in builtin),
          [f[2] for f in builtin if not f[2].startswith("https://")])

    with H.patch_urlopen(lambda req: H.FakeResp(RSS, req.full_url)) as net:
        for f in feeds:
            g["_intel_fetch_feed"](f[2], f[1], f[0])
    check("no request goes out in cleartext",
          net.calls and all(u.startswith("https://") for u, _d in net.calls),
          [u for u, _d in net.calls if not u.startswith("https://")])
    check("an owner's http feed is fetched over https instead",
          any(u == "https://blog.example.org/feed" for u, _d in net.calls),
          [u for u, _d in net.calls][-2:])

    with H.patch_urlopen(lambda req: H.FakeResp(RSS, "http://downgraded.example/feed")):
        out = g["_intel_fetch_feed"]("https://example.org/feed", "X", "News")
    check("a redirect down to http is dropped", out == [], out)
    with H.patch_urlopen(lambda req: H.FakeResp(RSS, req.full_url)):
        out = g["_intel_fetch_feed"]("https://example.org/feed", "X", "News")
    check("an https feed still parses", len(out) == 1 and out[0]["title"] == "Hello", out)
    H.finish()


if __name__ == "__main__":
    main()
