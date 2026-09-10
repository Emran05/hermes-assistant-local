#!/usr/bin/env python3
"""1.3.1 item 2 — the background lane in the context meter.

aux_context tailed only ~/.hermes/logs/mlx-server.log, so every token the
briefing / watchtower / For-You producers spend on the :8081 lane was invisible.
/api/context/recent now reads mlx-bg.log too and tags every row `lane`.  What
must NOT change: the header chip and /api/context/turn stay primary-only (they
describe the chat turn that just ended), and the read stays bounded at
_CX_TAIL_BYTES PER LOG.

Exec's aux_context.py the way server.py does against a throwaway HOME with two
synthetic logs.  No model, no network, no dashboard.
"""
import io, os, sys, json, time, shutil, datetime, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_context.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(name)


class Ctx:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body if body is not None else {}

    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d


CFG = """model:
  default: mlx-community/Qwen3.8-27B-4bit
  context_length: 65536
compression:
  enabled: true
  threshold: 0.5
  target_ratio: 0.2
  protect_last_n: 20
  protect_first_n: 3
"""


def build(home):
    os.makedirs(os.path.join(home, ".hermes", "logs"), exist_ok=True)
    io.open(os.path.join(home, ".hermes", "config.yaml"), "w",
            encoding="utf-8").write(CFG)
    g = {"__name__": "aux_context_t131", "HOME": home,
         "DATA": os.path.join(home, ".hermes", "dashboard"),
         "_state_lock": threading.Lock(), "CHAT_JOBS": {},
         "active_model": lambda: "mlx-community/Qwen3.8-27B-4bit",
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def pair(ts, model, prompt, cached, gen=40, stream="True", prefill_s="1.20"):
    """The exact two lines an mlx server writes per request."""
    t = ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return (
        "%s - INFO - Prefill completed: request=req-%d prompt_tokens=%d "
        "cached_tokens=%d elapsed=%ss rate=900.0 tok/s\n"
        % (t, prompt, prompt, cached, prefill_s) +
        "%s - INFO - Request completed: endpoint=/chat/completions model=%s "
        "stream=%s backend=continuous_batching prompt_tokens=%d "
        "generated_tokens=%d elapsed=2.500s prefill=900.0 tok/s decode=40.0 tok/s "
        "finish_reason=stop in_flight=0\n"
        % (t, model, stream, prompt, gen))


def run(tmp):
    home = os.path.join(tmp, "h")
    logs = os.path.join(home, ".hermes", "logs")
    os.makedirs(logs, exist_ok=True)
    base = datetime.datetime.now().replace(microsecond=0) - datetime.timedelta(minutes=30)

    # primary: three requests of one conversation, growing
    p = ""
    for i, (pr, ca) in enumerate([(10000, 9000), (12000, 10000), (14000, 12000)]):
        p += pair(base + datetime.timedelta(seconds=i * 20), "primary/27B", pr, ca)
    # ...plus one auxiliary (stream=False) call that must be filtered out
    p += pair(base + datetime.timedelta(seconds=70), "primary/27B", 94, 0, stream="False")
    io.open(os.path.join(logs, "mlx-server.log"), "w", encoding="utf-8").write(p)

    # bg: two requests, interleaved IN TIME with the primary ones
    b = ""
    for i, (pr, ca) in enumerate([(30000, 28000), (33000, 30000)]):
        b += pair(base + datetime.timedelta(seconds=10 + i * 20), "bg/9B", pr, ca)
    io.open(os.path.join(logs, "mlx-bg.log"), "w", encoding="utf-8").write(b)

    g = build(home)

    print("\n1. both logs are read and every row names its lane")
    d = g["_cx_recent"](Ctx({"n": ["20"]}))
    check("ok", d.get("ok") is True, d)
    lanes = [t["lane"] for t in d["turns"]]
    check("five conversation rows (the stream=False one is filtered)",
          len(lanes) == 5, lanes)
    check("three primary", lanes.count("primary") == 3, lanes)
    check("two bg", lanes.count("bg") == 2, lanes)
    check("lane counts reported", d["lanes"] == {"primary": 3, "bg": 2}, d["lanes"])
    check("both log paths reported",
          set(d["logs"]) == {"primary", "bg"}
          and d["logs"]["bg"].endswith("mlx-bg.log"), d.get("logs"))
    check("`log` still names the primary (back-compat)",
          d["log"].endswith("mlx-server.log"), d["log"])

    print("\n2. the merge is chronological, not lane-by-lane")
    eps = [t["epoch"] for t in d["turns"]]
    check("rows are ordered oldest-first", eps == sorted(eps), eps)
    check("the lanes interleave", lanes != sorted(lanes, key=lambda x: x != "primary"),
          lanes)

    print("\n3. `compacted` is computed WITHIN a lane, never across the merge")
    # The bg lane's 30k prompt landing between two 10-12k primary requests
    # would read as a >30% drop if prev were taken from the merged list.
    for t in d["turns"]:
        if t["lane"] == "primary":
            check("primary row not falsely compacted (prev=%s)"
                  % t["prev_prompt_tokens"], t["compacted"] is False, t)
    prevs = {t["epoch"]: t["prev_prompt_tokens"] for t in d["turns"]}
    firsts = [t for t in d["turns"] if t["prev_prompt_tokens"] is None]
    check("each lane starts with prev=None (one per lane)", len(firsts) == 2, firsts)
    check("primary prev chain is primary-only",
          [t["prev_prompt_tokens"] for t in d["turns"] if t["lane"] == "primary"]
          == [None, 10000, 12000], prevs)

    print("\n4. a real compaction inside a lane is still caught")
    p2 = p + pair(base + datetime.timedelta(seconds=90), "primary/27B", 4000, 0)
    io.open(os.path.join(logs, "mlx-server.log"), "w", encoding="utf-8").write(p2)
    d2 = g["_cx_recent"](Ctx({"n": ["20"]}))
    last = [t for t in d2["turns"] if t["lane"] == "primary"][-1]
    check("14000 -> 4000 reads as compacted", last["compacted"] is True, last)

    print("\n5. lane= narrows without changing anything else")
    db = g["_cx_recent"](Ctx({"n": ["10"], "lane": ["bg"]}))
    check("only bg rows", {t["lane"] for t in db["turns"]} == {"bg"}, db["turns"])
    check("lane echoed", db["lane"] == "bg", db.get("lane"))
    check("primary not even counted", "primary" not in db["lanes"], db["lanes"])
    dp = g["_cx_recent"](Ctx({"lane": ["primary"]}))
    check("only primary rows", {t["lane"] for t in dp["turns"]} == {"primary"},
          dp["turns"])
    dj = g["_cx_recent"](Ctx({"lane": ["nonsense"]}))
    check("an unknown lane means no filter, not no rows",
          dj["lane"] is None and len(dj["turns"]) == 6, dj.get("lane"))

    print("\n6. n is clamped and applies to the MERGED tail")
    d3 = g["_cx_recent"](Ctx({"n": ["2"]}))
    check("n=2 returns the two newest overall", len(d3["turns"]) == 2, d3["count"])
    check("total counts both lanes", d3["total"] == 6, d3["total"])
    for raw, want in (("0", 1), ("999", 6), ("abc", 6)):
        dn = g["_cx_recent"](Ctx({"n": [raw]}))
        check("n=%r -> %d row(s)" % (raw, want), dn["count"] == want, dn["count"])

    print("\n7. THE CHIP STAYS PRIMARY-ONLY (/api/context/turn)")
    lo = (base + datetime.timedelta(seconds=5)).timestamp()
    hi = (base + datetime.timedelta(seconds=35)).timestamp()
    t = g["_cx_turn"](Ctx({"since": ["%f" % lo], "until": ["%f" % hi]}))
    check("the window found only primary requests",
          t.get("found") is True and t.get("lane") == "primary", t)
    check("bg prompts never leak into the meter",
          t.get("prompt_tokens") in (10000, 12000, 14000), t.get("prompt_tokens"))
    # a window that contains ONLY bg traffic must answer "nothing here"
    lo2 = (base + datetime.timedelta(seconds=8)).timestamp()
    hi2 = (base + datetime.timedelta(seconds=12)).timestamp()
    t2 = g["_cx_turn"](Ctx({"since": ["%f" % lo2], "until": ["%f" % hi2]}))
    check("a bg-only window is not found", t2.get("found") is False, t2)

    print("\n8. the read stays bounded — _CX_TAIL_BYTES PER LOG")
    check("cap unchanged", g["_CX_TAIL_BYTES"] == 512 * 1024, g["_CX_TAIL_BYTES"])
    big = os.path.join(tmp, "big")
    os.makedirs(os.path.join(big, ".hermes", "logs"), exist_ok=True)
    io.open(os.path.join(big, ".hermes", "config.yaml"), "w",
            encoding="utf-8").write(CFG)
    filler = ("2026-01-01 00:00:00,000 - INFO - noise " + "x" * 200 + "\n")
    for name in ("mlx-server.log", "mlx-bg.log"):
        with io.open(os.path.join(big, ".hermes", "logs", name), "w",
                     encoding="utf-8") as f:
            while f.tell() < 3 * 1024 * 1024:
                f.write(filler)
            f.write(pair(datetime.datetime.now(), "m/x", 500, 100))
    gb = build(big)
    t0 = time.time()
    db2 = gb["_cx_recent"](Ctx({"n": ["5"]}))
    ms = (time.time() - t0) * 1000.0
    check("two 3 MB logs still answer", db2["ok"] is True and db2["total"] == 2,
          db2.get("total"))
    check("and quickly (<400 ms), i.e. only the tail was read", ms < 400,
          "%.0f ms" % ms)

    print("\n9. an absent bg log is not an error (the lane may never have run)")
    os.remove(os.path.join(logs, "mlx-bg.log"))
    g2 = build(os.path.join(tmp, "h"))
    d4 = g2["_cx_recent"](Ctx({}))
    check("still ok", d4["ok"] is True, d4)
    check("bg counted as zero", d4["lanes"].get("bg") == 0, d4["lanes"])
    check("primary rows unaffected",
          {t["lane"] for t in d4["turns"]} == {"primary"}, d4["turns"])


def main():
    tmp = tempfile.mkdtemp(prefix="t131cx-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("TESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    if FAILS:
        print("FAILED: %d  (%s)" % (len(FAILS), ", ".join(FAILS)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
