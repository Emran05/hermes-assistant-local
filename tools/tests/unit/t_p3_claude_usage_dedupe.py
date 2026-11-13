#!/usr/bin/env python3
"""2026-10-08 audit P3 ("Other aux") — Claude-usage tokens were not
de-duplicated by message/request id.

Claude Code logs one API message as several records: streaming partials that
share message.id + requestId, carry the same input/cache usage and a growing
output count, the last one final; a forked/resumed session copies them into a
second file. The scan summed every record, so input and cache were counted 2-4x
(on the owner's logs half of all usage records were such copies). Each
(message.id, requestId) now counts once, as its fullest copy; a record without
both ids still counts as before.

Exec's the REAL aux_claude_usage.py over a throwaway HOME (no ~/.claude)."""
import datetime, io, json, os, shutil, sys, tempfile, time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_claude_usage.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def load(home):
    g = {"__name__": "cu_dedupe", "HOME": home,
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "_widget_cache": {}, "register_get": lambda p, f: None,
         "register_post": lambda p, f: None, "get_layout": lambda: {"order": []},
         "save_layout": lambda l: None, "WIDGETS": {}, "EXPANDERS": {},
         "_cached": lambda k, ttl, fn: fn()}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec(ts, mid, rid, out, cwd="/Users/you/alpha", sid="s1"):
    r = {"type": "assistant", "timestamp": iso(ts), "sessionId": sid, "cwd": cwd,
         "message": {"model": "claude-opus-5",
                     "usage": {"input_tokens": 3, "output_tokens": out,
                               "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 400}}}
    if mid:
        r["message"]["id"] = mid
    if rid:
        r["requestId"] = rid
    return json.dumps(r) + "\n"


def main():
    home = tempfile.mkdtemp(prefix="cu-dedupe-")
    try:
        run(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def run(home):
    # _cu_build() buckets "today" from local midnight on the REAL clock and takes
    # no clock of its own, so these records (now - 100 s ... now - 8 s) must all
    # be after midnight: a run that starts in the first 3 minutes of a day, or
    # the last 30 s before one, waits until 00:03 (at most ~3.5 min, rarely)
    _lt = time.localtime()
    _into = _lt.tm_hour * 3600 + _lt.tm_min * 60 + _lt.tm_sec
    if _into < 180 or _into > 86400 - 30:
        time.sleep((180 - _into) if _into < 180 else (86400 - _into + 180))
    now = time.time()
    pa = os.path.join(home, ".claude", "projects", "-Users-x-alpha")
    pb = os.path.join(home, ".claude", "projects", "-Users-x-beta")
    os.makedirs(pa)
    os.makedirs(pb)
    live = os.path.join(pa, "s1.jsonl")
    with io.open(live, "w") as f:
        f.write(rec(now - 100, "msg_A", "req_A", 2))     # thinking block (partial)
        f.write(rec(now - 99, "msg_A", "req_A", 2))      # text block (partial)
        f.write(rec(now - 98, "msg_A", "req_A", 500))    # final
        f.write(rec(now - 50, None, None, 7))            # an old-format record, no ids
    fork = os.path.join(pb, "s2.jsonl")
    with io.open(fork, "w") as f:                       # a resumed session's copy
        f.write(rec(now - 98, "msg_A", "req_A", 500, cwd="/Users/you/beta", sid="s2"))
    os.utime(fork, (now - 3600, now - 3600))            # older than the live file

    g = load(home)
    d = g["_cu_build"]()
    print("one API message logged as three partials + a forked copy")
    check("counts as one message (plus the id-less record)", d["messages"] == 2, d["messages"])
    check("its cache tokens count once", d["today"]["cr"] == 2 * 5000, d["today"]["cr"])
    check("its input counts once", d["today"]["in"] == 2 * 3, d["today"]["in"])
    check("its output is the final count, not the sum of partials",
          d["today"]["out"] == 500 + 7, d["today"]["out"])
    projs = {p["name"]: p for p in d["projects"]}
    check("attributed to the newest file's project, once", projs.get("alpha", {}).get("msgs") == 2
          and "beta" not in projs, sorted(projs))
    check("both sessions are still seen", d["sessions"] == 2, d["sessions"])

    print("a message still streaming, then finished (incremental scan)")
    with io.open(live, "a") as f:
        f.write(rec(now - 10, "msg_B", "req_B", 1))
    d = g["_cu_build"]()
    check("the partial counts as one message", d["messages"] == 3, d["messages"])
    with io.open(live, "a") as f:
        f.write(rec(now - 9, "msg_B", "req_B", 1))
        f.write(rec(now - 8, "msg_B", "req_B", 321))
    d = g["_cu_build"]()
    check("the finished message replaces it, not adds to it", d["messages"] == 3, d["messages"])
    check("with its final output", d["today"]["out"] == 500 + 7 + 321, d["today"]["out"])
    cold = load(home)["_cu_build"]()
    check("the incremental answer equals a cold scan",
          (d["messages"], d["today"], d["week"]) == (cold["messages"], cold["today"], cold["week"]),
          (d["today"], cold["today"]))

    print("same message id, different request (a retry)")
    with io.open(live, "a") as f:
        f.write(rec(now - 5, "msg_B", "req_C", 40))
    d = g["_cu_build"]()
    check("is a separate message", d["messages"] == 4, d["messages"])


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
