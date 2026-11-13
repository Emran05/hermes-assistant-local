#!/usr/bin/env python3
"""C43 — a curated pick links to the feed's own URL, never the model's spelling.

_intel_curate checked the model's URL with _intel_norm_url, which drops the
query and fragment, then STORED the model's string — so a model steered by a
feed headline could append "?utm=…" / "#…" (or a different case) and that link
reached the brief and Telegram. Drives the real _intel_curate from
aux_watchtower.py (see _wt_harness.py) with a fake model answer; no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


def main():
    print("C43: curated URLs come from the pool")
    g, _home, _data = H.load()
    g["model_online"] = lambda: True
    pool = [{"title": "Lab ships a model", "source": "OpenAI",
             "url": "https://example.org/post?id=7"},
            {"title": "Second", "source": "DeepMind", "url": "https://example.org/two"}]
    answer = [
        {"title": "Lab ships a model", "source": "OpenAI", "why": "w",
         "url": "https://EXAMPLE.org/post?id=7&ref=https://evil.example/x#frag"},
        {"title": "dup", "source": "OpenAI", "why": "w",
         "url": "https://example.org/post?other=1"},
        {"title": "Second", "source": "DeepMind", "why": "w",
         "url": "https://example.org/two/"},
        {"title": "Invented", "source": "x", "why": "w", "url": "https://evil.example/"},
    ]

    def model(req):
        return H.FakeResp(json.dumps({"choices": [{"message": {
            "content": json.dumps(answer)}}]}), req.full_url)
    with H.patch_urlopen(model):
        out = g["_intel_curate"](pool)
    urls = [o.get("url") for o in out or []]
    check("the pick stores the pool's url, not the model's",
          urls[:1] == ["https://example.org/post?id=7"], urls)
    check("no model-added query or fragment survives",
          all("evil" not in u and "#" not in u and "ref=" not in u for u in urls), urls)
    check("a trailing-slash spelling maps back to the pool url",
          "https://example.org/two" in urls, urls)
    check("two spellings of one item are one pick", len(urls) == 2, urls)
    check("an invented url is still rejected",
          "https://evil.example/" not in urls, urls)
    H.finish()


if __name__ == "__main__":
    main()
