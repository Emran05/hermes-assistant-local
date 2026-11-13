#!/usr/bin/env python3
"""C86 — the Claude-usage scan re-reads only what changed.

_cu_scan used to re-read every Claude Code log modified in the last 8 days
(~480 MB on the owner's Mac) each time its 60 s cache expired. The logs are
append-only JSONL, so each file's parsed records are now kept by inode + size +
mtime: an unchanged file is not opened, a grown one is read from where the last
scan stopped, a rewritten one is re-read whole — and the payload must be exactly
what a cold scan of the same files produces.

Exec's the REAL aux_claude_usage.py into a namespace with server.py's globals
stubbed, over a throwaway HOME; `open` is instrumented to count files opened
and bytes read. No ~/.claude, no dashboard."""
import datetime, io, json, os, shutil, sys, tempfile, threading, time

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


IO = {"opens": [], "bytes": 0}


class Counted:
    def __init__(self, f):
        self._f = f

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()

    def __iter__(self):
        for line in self._f:
            IO["bytes"] += len(line)
            yield line

    def read(self, *a):
        b = self._f.read(*a)
        IO["bytes"] += len(b)
        return b

    def readline(self, *a):
        b = self._f.readline(*a)
        IO["bytes"] += len(b)
        return b

    def __getattr__(self, k):
        return getattr(self._f, k)


def counting_open(path, *a, **kw):
    if str(path).endswith(".jsonl"):
        IO["opens"].append(os.path.basename(str(path)))
    return Counted(io.open(path, *a, **kw))


def load(home):
    g = {"__name__": "cu_c86", "HOME": home, "open": counting_open,
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "_widget_cache": {}, "register_get": lambda p, f: None,
         "register_post": lambda p, f: None, "get_layout": lambda: {"order": []},
         "save_layout": lambda l: None, "WIDGETS": {}, "EXPANDERS": {}}
    g["_cached"] = lambda k, ttl, fn: fn()
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec(ts, sid, cwd, model, i, o, cr=0, cc=0):
    return json.dumps({"type": "assistant", "timestamp": iso(ts), "sessionId": sid, "cwd": cwd,
                       "message": {"model": model, "content": [{"type": "text", "text": "x" * 400}],
                                   "usage": {"input_tokens": i, "output_tokens": o,
                                             "cache_read_input_tokens": cr,
                                             "cache_creation_input_tokens": cc}}})


def stable(d):
    d = json.loads(json.dumps(d))
    d.pop("generated", None)
    (d.get("window") or {}).pop("reset_in", None)
    return d


def cold(home):
    """the same files through a brand-new module instance (empty cache)"""
    return stable(load(home)["_cu_build"]())


def main():
    home = tempfile.mkdtemp(prefix="c86-")
    try:
        run(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = tempfile.mkdtemp(prefix="c86-mem-")
    try:
        memory(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def memory(home):
    """The cache lives as long as the dashboard (~42k records on the owner's
    Mac). A session id and a (message.id, requestId) tuple kept per record cost
    ~550-585 bytes each (~24 MB there); one session set per file and a one-string
    key bring it to ~370-395. Measured with tracemalloc after the scan's own
    result is dropped, so only the cache is counted."""
    import gc, tracemalloc
    print("memory held between scans")
    pa = os.path.join(home, ".claude", "projects", "-Users-x-alpha")
    os.makedirs(pa)
    now, n = time.time(), 0
    for fi in range(4):
        with io.open(os.path.join(pa, "m%d.jsonl" % fi), "w") as f:
            for k in range(1500):
                for _blk in range(2):            # one record per content block
                    r = json.loads(rec(now - 4000 + k, "6f0c1e2a-93d4-4b1f-8e7a-%012d" % fi,
                                       "/Users/you/alpha", "claude-opus-5", 2, 900 + k, 40000 + k, 6000))
                    r["requestId"] = "req_011CUaBcDeFgHiJkLm%06d%02d" % (k, fi)
                    r["message"]["id"] = "msg_01AbCdEfGhIjKlMnOp%06d%02d" % (k, fi)
                    r["message"]["content"] = []
                    f.write(json.dumps(r) + "\n")
                    n += 1
    g = load(home)
    gc.collect()
    tracemalloc.start()
    try:
        r = g["_cu_scan"]()
        check("each API message parsed once, four sessions", len(r[0]) == n // 2 and len(r[1]) == 4, (len(r[0]), len(r[1])))
        del r
        gc.collect()
        held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    check("under 470 bytes held per record", held / n < 470, round(held / n))


def run(home):
    now = time.time()
    pa = os.path.join(home, ".claude", "projects", "-Users-x-alpha")
    pb = os.path.join(home, ".claude", "projects", "-Users-x-beta")
    os.makedirs(pa)
    os.makedirs(pb)
    big = os.path.join(pa, "s1.jsonl")
    with io.open(big, "w") as f:
        for k in range(300):
            f.write(json.dumps({"type": "user", "message": {"content": "q" * 300}}) + "\n")
            f.write(rec(now - 7200 + k * 10, "s1", "/Users/you/alpha", "claude-opus-5", 10, 20, 100, 5) + "\n")
        f.write("{not json\n")
    small = os.path.join(pb, "s2.jsonl")
    with io.open(small, "w") as f:
        f.write(rec(now - 600, "s2", "/Users/you/beta", "claude-sonnet-5", 7, 3) + "\n")
        f.write(rec(now - 300, "s2", "/Users/you/beta", "claude-haiku-5", 1, 1))   # no newline yet
    old = os.path.join(pb, "old.jsonl")
    with io.open(old, "w") as f:
        f.write(rec(now - 20 * 86400, "s0", "/Users/you/beta", "claude-opus-5", 999, 999) + "\n")
    os.utime(old, (now - 20 * 86400, now - 20 * 86400))

    g = load(home)
    build = g["_cu_build"]

    print("first scan")
    IO["opens"].clear(); IO["bytes"] = 0
    d1 = stable(build())
    first_bytes = IO["bytes"]
    check("reads both live logs", sorted(IO["opens"]) == ["s1.jsonl", "s2.jsonl"], IO["opens"])
    check("counts every usage record, incl. an unterminated last line", d1["messages"] == 302, d1["messages"])
    check("sessions", d1["sessions"] == 2, d1["sessions"])
    check("identical to a cold scan", d1 == cold(home))

    print("nothing changed")
    IO["opens"].clear(); IO["bytes"] = 0
    d2 = stable(build())
    check("no log is opened again", IO["opens"] == [], IO["opens"])
    check("no bytes are read", IO["bytes"] == 0, IO["bytes"])
    check("same payload", d2 == d1)

    print("the live session grows")
    size_before = os.path.getsize(big)
    with io.open(big, "a") as f:
        for k in range(3):
            f.write(rec(now - 60 + k, "s1", "/Users/you/alpha", "claude-opus-5", 4, 5) + "\n")
    IO["opens"].clear(); IO["bytes"] = 0
    d3 = stable(build())
    appended = os.path.getsize(big) - size_before
    check("only the grown log is opened", IO["opens"] == ["s1.jsonl"], IO["opens"])
    check("only the appended bytes (+ the 64-byte fingerprint, checked and renewed) are read",
          IO["bytes"] <= appended + 2 * 64, (IO["bytes"], appended, size_before))
    check("the three new records count once", d3["messages"] == 305, d3["messages"])
    check("identical to a cold scan", d3 == cold(home))

    print("the unterminated line is finished, then another is written")
    with io.open(small, "a") as f:
        f.write("\n" + rec(now - 30, "s3", "/Users/you/beta", "claude-haiku-5", 2, 2) + "\n")
    IO["opens"].clear(); IO["bytes"] = 0
    d4 = stable(build())
    incr_bytes = IO["bytes"]
    check("the finished line is not counted twice", d4["messages"] == 306, d4["messages"])
    check("a new session id is seen", d4["sessions"] == 3, d4["sessions"])
    check("identical to a cold scan", d4 == cold(home))
    check("the first scan read far more than the incremental ones", first_bytes > 40 * incr_bytes,
          (first_bytes, incr_bytes))

    print("a log rewritten in place (same inode)")
    with io.open(small, "r+") as f:
        f.seek(0)
        f.write(rec(now - 500, "s9", "/Users/you/beta", "claude-opus-5", 50, 50) + "\n")
        f.write(rec(now - 400, "s9", "/Users/you/beta", "claude-opus-5", 60, 60) + "\n")
        f.write(rec(now - 300, "s9", "/Users/you/beta", "claude-opus-5", 70, 70) + "\n")
        f.truncate()
    d5 = stable(build())
    check("is re-read whole, not resumed mid-record", d5 == cold(home), (d5["messages"], cold(home)["messages"]))
    check("its old records are gone", d5["sessions"] == 2, d5["sessions"])

    print("a log leaves the 8-day window")
    os.utime(small, (now - 9 * 86400, now - 9 * 86400))
    d6 = stable(build())
    check("its records drop out", d6["messages"] == 303, d6["messages"])
    check("and its parse state is forgotten", small not in g.get("_CU_FILES", {}) and big in g.get("_CU_FILES", {}),
          sorted(os.path.basename(k) for k in g.get("_CU_FILES", {})))
    check("identical to a cold scan", d6 == cold(home))

    print("two scans at once")
    with io.open(big, "a") as f:
        f.write(rec(now - 5, "s1", "/Users/you/alpha", "claude-opus-5", 1, 1) + "\n")
    out = []
    ts = [threading.Thread(target=lambda: out.append(stable(build()))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("concurrent scans agree and count the new record once",
          len(out) == 4 and all(o == out[0] for o in out) and out[0]["messages"] == 304,
          [o["messages"] for o in out])


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
