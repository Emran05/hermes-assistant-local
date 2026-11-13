#!/usr/bin/env python3
"""C47.2 (docs/audits/2026-10-21-codebase-audit.md) — the News Desk's built-in
feeds are all fetched over https.

BEFORE: NEWS_DESK_SECTIONS in expanders_extra.py listed BBC World as
http://feeds.bbci.co.uk/news/world/rss.xml, so every News Desk pop-out fetched
that feed in cleartext (the https URL answers 200). C47 fixed the same feed in
aux_watchtower's _WORLD_FEEDS but not here.

AFTER: every built-in News Desk feed is https, and expand_rss() — the REAL
function, with _http_text replaced by a recorder — sends no cleartext request.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _extra_harness as H  # noqa: E402

check = H.check
RSS = ('<?xml version="1.0"?><rss><channel><item><title>Hello %s</title>'
       '<link>https://example.org/%s</link></item></channel></rss>')


def main():
    print("C47.2: News Desk feeds are https only")
    calls = []

    def http_text(url, timeout=6):
        calls.append(url)
        return RSS % (len(calls), len(calls))

    g = H.server_defs({"_strip_html", "_http_url"}, {"re": H.re, "urllib": H.urllib})
    g = H.load(dict(g, _http_text=http_text))
    builtin = [u for _name, feeds in g["NEWS_DESK_SECTIONS"] for u, _lb in feeds]
    check("there are built-in feeds to check", len(builtin) >= 10, builtin)
    check("every built-in News Desk feed is https",
          all(u.startswith("https://") for u in builtin),
          [u for u in builtin if not u.startswith("https://")])
    check("BBC World is fetched from its https URL",
          "https://feeds.bbci.co.uk/news/world/rss.xml" in builtin, builtin)

    out = g["expand_rss"]()
    check("the desk still builds sections", len(out.get("sections") or []) >= 4, out)
    check("every feed was requested", len(calls) == len(builtin), (len(calls), len(builtin)))
    check("no request goes out in cleartext",
          calls and all(u.startswith("https://") for u in calls),
          [u for u in calls if not u.startswith("https://")])
    H.finish()


if __name__ == "__main__":
    main()
