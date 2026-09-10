#!/usr/bin/env python3
"""1.3.1 item 1 — `memory_chars` survives the job.

server.py stamps job["memory_chars"] when the memory layer injects a block, but
CHAT_JOBS drops a finished job after an hour, so every trace span older than
that lost the number.  aux_metrics now writes it onto the per-turn metrics row
and aux_trace reads it back from there when the live job is gone.

Exec's both aux modules the way server.py does, against a throwaway HOME.
No model, no network, no dashboard, and the owner's ~/.hermes is never touched.
"""
import io, os, sys, json, time, shutil, datetime, tempfile, threading, uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(name)


def read_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def build_metrics(home):
    """aux_metrics.py in a namespace shaped like server.py's globals."""
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_metrics_t131", "HOME": home, "HERE": DASH, "DATA": data,
         "read_json": read_json, "write_json": write_json,
         "_cached": lambda k, ttl, fn: fn(), "_widget_cache": {},
         "_state_lock": threading.Lock(), "CHAT_JOBS": {},
         "_jobs_lock": threading.Lock(),
         "agent_paused": lambda: False, "active_model": lambda: "test/Model-1B",
         "model_online": lambda: False, "_mlx_footprint_gb": lambda: 0.0,
         "hub_data": lambda *a, **k: {}, "switch_model": lambda *a, **k: {},
         "agent_power": lambda *a, **k: {},
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "uuid": uuid,
         # the sampler thread would poll a machine we are not testing
         "_metrics_thread_started": True}
    path = os.path.join(DASH, "aux_metrics.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), g)
    return g


def build_trace(home, met_dir, chat_jobs):
    data = os.path.join(home, ".hermes", "dashboard")
    g = {"__name__": "aux_trace_t131", "HOME": home, "HERE": DASH, "DATA": data,
         "MET_DIR": met_dir, "REC_DB": os.path.join(data, "recorder.db"),
         "CHAT_JOBS": chat_jobs, "_jobs_lock": threading.Lock(),
         "read_json": read_json, "write_json": write_json,
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    path = os.path.join(DASH, "aux_trace.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), g)
    return g


def finish_turn(g, session, **fields):
    """Drive one MeteredJob through the lifecycle that ends in a metrics row."""
    job = g["_new_job"](session)
    for k, v in fields.items():
        job[k] = v
    job["text"] = "streaming"
    job.update({"reply": "done", "ok": True, "done": True})
    return job


def turn_rows(met_dir):
    lt = time.localtime()
    p = os.path.join(met_dir, "metrics-%04d-%02d-%02d.jsonl"
                     % (lt.tm_year, lt.tm_mon, lt.tm_mday))
    out = []
    with io.open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("kind") == "turn":
                out.append(r)
    return out


def run(tmp):
    home = os.path.join(tmp, "h")
    g = build_metrics(home)
    met_dir = g["MET_DIR"]

    print("\n1. the per-turn metrics row carries memory_chars")
    finish_turn(g, "s-mem", memory_chars=4321)
    rows = turn_rows(met_dir)
    check("one turn row written", len(rows) == 1, rows)
    check("memory_chars persisted", rows[0].get("memory_chars") == 4321,
          rows[0].get("memory_chars"))
    check("the rest of the row is unchanged",
          rows[0].get("ok") is True and rows[0].get("path") == "serve"
          and rows[0].get("model") == "test/Model-1B", rows[0])

    print("\n2. a turn with no memory block records null, not 0")
    finish_turn(g, "s-none")
    rows = turn_rows(met_dir)
    check("key present on every turn row", "memory_chars" in rows[1], rows[1])
    check("absent measurement is null", rows[1]["memory_chars"] is None,
          rows[1]["memory_chars"])

    print("\n3. a junk value degrades to null instead of poisoning the row")
    finish_turn(g, "s-junk", memory_chars="not a number")
    rows = turn_rows(met_dir)
    check("unparsable -> null", rows[2]["memory_chars"] is None, rows[2])
    finish_turn(g, "s-str", memory_chars="512")
    rows = turn_rows(met_dir)
    check("numeric string -> int", rows[3]["memory_chars"] == 512, rows[3])

    print("\n4. metrics NEVER break a turn (the whole module's contract)")

    # A real MeteredJob whose memory_chars read raises: the new line must be
    # inside the same try/except the rest of _met_finish_turn lives in.
    class Exploding(g["MeteredJob"]):
        def get(self, k, d=None):
            if k == "memory_chars":
                raise RuntimeError("boom")
            return dict.get(self, k, d)

    before = len(turn_rows(met_dir))
    try:
        g["_met_finish_turn"](Exploding({"id": "x", "session": "s", "ok": True}),
                              time.time())
        check("a raising memory_chars read is swallowed", True)
    except Exception as e:
        check("a raising memory_chars read is swallowed", False, repr(e))
    check("and no half-built row is written",
          len(turn_rows(met_dir)) == before, before)

    print("\n5. aux_trace reads it off the metrics row with NO live job")
    day = datetime.date.today()
    lo = time.mktime(day.timetuple())
    hi = lo + 86400
    t = build_trace(home, met_dir, {})            # CHAT_JOBS empty: job is gone
    spans, _traces = t["_tr_day_spans"](day, lo, hi, {}, [], [], {})
    turns = [s for s in spans if s.get("kind") == "turn"]
    check("four turn spans built", len(turns) == 4, len(turns))
    attrs = dict(turns[0]["attrs"])
    check("hermes.memory_chars on the span from the metrics row",
          attrs.get("hermes.memory_chars") == 4321, attrs.get("hermes.memory_chars"))
    check("a null memory_chars is omitted, not zeroed",
          "hermes.memory_chars" not in dict(turns[1]["attrs"]),
          dict(turns[1]["attrs"]).get("hermes.memory_chars"))

    print("\n6. a LIVE job still wins over the row (same value, fresher source)")
    rows = turn_rows(met_dir)
    jid = rows[0]["job"]
    live = {jid: {"session": "live-session", "memory_chars": 9999}}
    t2 = build_trace(home, met_dir, live)
    spans2, _ = t2["_tr_day_spans"](day, lo, hi, {}, [], [], live)
    a2 = dict([s for s in spans2 if s.get("kind") == "turn"][0]["attrs"])
    check("live job overrides", a2.get("hermes.memory_chars") == 9999,
          a2.get("hermes.memory_chars"))
    check("session comes from the live job too",
          a2.get("hermes.session") == "live-session", a2.get("hermes.session"))

    print("\n7. a live job with NO memory_chars falls back to the row")
    live2 = {jid: {"session": "live-session"}}
    t3 = build_trace(home, met_dir, live2)
    spans3, _ = t3["_tr_day_spans"](day, lo, hi, {}, [], [], live2)
    a3 = dict([s for s in spans3 if s.get("kind") == "turn"][0]["attrs"])
    check("falls back to the persisted row", a3.get("hermes.memory_chars") == 4321,
          a3.get("hermes.memory_chars"))


def main():
    tmp = tempfile.mkdtemp(prefix="t131mem-")
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
