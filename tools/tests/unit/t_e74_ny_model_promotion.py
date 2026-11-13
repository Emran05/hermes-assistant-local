#!/usr/bin/env python3
"""E74 (docs/audits/2026-11-09-codebase-audit.md): the optional Needs-you
model pass (off by default) could promote to Now a row the rules kept out:
a deadline days in the past (a negative delta is "<= 24 h"), an automated
sender (a phishing short code asking for urgency), or a stranger.

AFTER: a promotion to `now` needs confidence >= 0.8 AND a deadline inside the
documented window (missed by at most 6 h, or due within 24 h) AND a VIP or
known, non-automated sender.

Drives the REAL _ny_model_pass through _ny_env with the lane's HTTP answer
stubbed in-process (no socket is opened).
"""
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()
g["get_settings"] = lambda: {"needs_you": {"model_pass": True}}
g["bg_online"] = lambda: True
g["bg_lane"] = lambda: {"lane": "bg", "model": "m",
                        "chat_url": "http://127.0.0.1:9/v1/chat/completions"}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(items, answer):
    def urlopen(req, timeout=None, **kw):
        return Resp(json.dumps({"choices": [{"message": {
            "content": json.dumps(answer)}}]}).encode())
    real = urllib.request.urlopen
    urllib.request.urlopen = urlopen
    try:
        return g["_ny_model_pass"](items, NOW)
    finally:
        urllib.request.urlopen = real


def item(iid, tier, dl):
    return {"id": iid, "sender": "x", "sender_tier": tier, "summary": "reply YES",
            "requires_reply": True, "deadline_ts": dl}


items = [
    item("message:old", "vip", NOW - 3 * 86400),       # three days past
    item("message:just-missed", "vip", NOW - 2 * 3600),  # 2 h past: still live
    item("message:edge-past", "vip", NOW - 6 * 3600 + 60),
    item("message:too-past", "vip", NOW - 6 * 3600 - 60),
    item("message:auto", "automated", NOW + 3600),
    item("message:stranger", "unknown", NOW + 3600),
    item("message:ok-vip", "vip", NOW + 3600),
    item("message:ok-known", "known", NOW + 3600),
    item("message:far", "vip", NOW + 30 * 3600),
    item("message:nodl", "vip", 0),
]
ans = [{"id": it["id"], "bucket": "now", "confidence": 0.95, "reason": "NOW"}
       for it in items]
out = run(items, ans)
got = sorted(out)
E.check("only in-window deadlines from vip/known senders are promoted",
        got == sorted(["message:just-missed", "message:edge-past",
                       "message:ok-vip", "message:ok-known"]), got)
for iid in ("message:old", "message:too-past", "message:auto",
            "message:stranger", "message:far", "message:nodl"):
    E.check("refused: %s" % iid, iid not in out)
low = run([item("message:ok-vip", "vip", NOW + 3600)],
          [{"id": "message:ok-vip", "bucket": "now", "confidence": 0.7}])
E.check("a confident-enough answer is still required", low == {}, low)

sys.exit(E.finish())
