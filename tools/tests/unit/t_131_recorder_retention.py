#!/usr/bin/env python3
"""1.3.1 item 3 — a TTL for recorder.db's `actions` table.

The table had none: three observation legs write a row per tool call from every
surface and nothing ever removed one.  A daily sweep now deletes rows older than
settings.json's `recorder.retain_days` — EXCEPT the undo material, which is the
whole point of the recorder: status='undone' rows and rows still holding a
snapshot_ref survive whatever their age, and the window itself is clamped up to
the undo-trash TTL (14 days) so the two can never disagree.

Section 7 runs the sweep against a read-only SNAPSHOT of the owner's LIVE
recorder.db, taken with sqlite's own backup API into a temp dir.  The live
database is opened mode=ro, its row count is asserted unchanged, and no
statement is ever executed against it — capture-and-restore taken to its
strongest form: nothing about the owner's real store is ever written, only
compared before and after.  On a machine with no such file yet (a fresh
checkout, CI) this section is skipped rather than failed.

Exec's aux_recorder.py the way server.py does, against a throwaway HOME.
No model, no network, no dashboard.
"""
import io, os, sys, json, time, shutil, sqlite3, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_recorder.py")
LIVE_DB = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard", "recorder.db")
DAY = 86400
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


def make_settings_update(settings, lock):
    """Stand-in for server.py's settings_update() — ONE locked
    read-modify-write of settings.json, the only way an aux module is allowed
    to write it since the 2026-09-10 audit (A01)."""
    def settings_update(mutate_fn):
        with lock:
            s = read_json(settings, {}) or {}
            if not isinstance(s, dict):
                s = {}
            out = mutate_fn(s)
            if isinstance(out, dict):
                s = out
            write_json(settings, s)
            return json.loads(json.dumps(s))
    return settings_update


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    settings = os.path.join(data, "settings.json")
    lock = threading.Lock()
    g = {"__name__": "aux_recorder_t131", "HOME": home,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(home, ".hermes", "state.db"),
         "SETTINGS_FILE": settings,
         "read_json": read_json, "write_json": write_json,
         "get_settings": lambda: read_json(settings, {}) or {},
         "_state_lock": lock, "_widget_cache": {},
         "settings_update": make_settings_update(settings, lock),
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         # the reconciler polls state.db every 5s; not what is under test
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def insert(db, rows):
    con = sqlite3.connect(db)
    try:
        for r in rows:
            con.execute(
                "INSERT INTO actions(tool_call_id, ts, session, source, tool, args,"
                " target, kind, reversible, status, summary, snapshot_ref, origin)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["ts"], "s", "test", r.get("tool", "read_file"), "",
                 r.get("target", "/x"), r.get("kind", "read"),
                 r.get("reversible", "n/a"), r.get("status", "ok"), "",
                 r.get("snapshot_ref", ""), "test"))
        con.commit()
    finally:
        con.close()


def count(db, where="1=1", params=()):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM actions WHERE " + where, params).fetchone()[0]
    finally:
        con.close()


def ids(db):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return sorted(r[0] for r in con.execute("SELECT tool_call_id FROM actions"))
    finally:
        con.close()


def run(tmp):
    now = time.time()
    home = os.path.join(tmp, "h")
    g = build(home)
    db = g["REC_DB"]

    print("\n1. the clamp is pure and the floor is the undo-trash TTL")
    check("UNDO_TRASH_TTL is still 14 days", g["UNDO_TRASH_TTL"] == 14 * DAY,
          g["UNDO_TRASH_TTL"])
    check("min == that, in days", g["REC_RETAIN_MIN_DAYS"] == 14,
          g["REC_RETAIN_MIN_DAYS"])
    clamp = g["_rec_retain_clamp"]
    table = [(None, 90), (90, 90), (0, 0), (-5, 0), (1, 14), (13, 14), (14, 14),
             (365, 365), (99999, 3650), ("30", 30), ("abc", 90), ("", 90),
             (30.9, 30),
             # bool is an int subclass in Python but is REJECTED here on
             # purpose (falls back to the default, like None/"abc"): a
             # hand-edited or buggily-written settings.json `"retain_days":
             # false` must not silently mean "0 = keep forever".
             (True, 90), (False, 90)]
    for raw, want in table:
        check("clamp(%r) == %r" % (raw, want), clamp(raw) == want, clamp(raw))

    print("\n2. the setting is a fresh read-modify-write of settings.json")
    write_json(g["SETTINGS_FILE"], {"tickers": ["AAPL"], "recorder": {"note": "keep me"}})
    check("default when the key is absent", g["_rec_retain_days"]() == 90)
    got = g["_rec_set_retain_days"](7)
    s = read_json(g["SETTINGS_FILE"], {})
    check("a typed 7 is stored as the clamped 14", got == 14 and
          s["recorder"]["retain_days"] == 14, s)
    check("other top-level keys survive", s.get("tickers") == ["AAPL"], s)
    check("other keys INSIDE recorder survive", s["recorder"].get("note") == "keep me", s)
    check("read back", g["_rec_retain_days"]() == 14)
    g["_rec_set_retain_days"](90)

    print("\n3. the sweep deletes plain old rows and keeps the undo material")
    insert(db, [
        {"id": "old-plain",     "ts": now - 100 * DAY},
        {"id": "old-plain-2",   "ts": now - 91 * DAY},
        {"id": "old-undone",    "ts": now - 100 * DAY, "status": "undone",
         "kind": "write", "reversible": "yes"},
        {"id": "old-snapshot",  "ts": now - 100 * DAY, "kind": "write",
         "reversible": "yes", "snapshot_ref": json.dumps({"commit": "abc123"})},
        {"id": "old-undone-and-snap", "ts": now - 200 * DAY, "status": "undone",
         "snapshot_ref": json.dumps({"commit": "d00d"})},
        {"id": "recent-plain",  "ts": now - 3 * DAY},
        {"id": "edge-89d",      "ts": now - 89 * DAY},
    ])
    check("7 rows in", count(db) == 7, count(db))
    res = g["_rec_sweep_retention"]()
    check("sweep ok", res.get("ok") is True, res)
    check("2 deleted", res["deleted"] == 2, res)
    check("both undone rows counted as kept", res["kept_undone"] == 2, res)
    check("the snapshot-only row counted as kept", res["kept_snapshot"] == 1, res)
    check("window echoed", res["days"] == 90, res)
    check("survivors are exactly the undo material + the fresh rows",
          ids(db) == ["edge-89d", "old-snapshot", "old-undone",
                      "old-undone-and-snap", "recent-plain"], ids(db))

    print("\n4. dry runs count without deleting")
    before = ids(db)
    dry = g["_rec_sweep_retention"](days=14, dry=True)
    # survivors are edge-89d, recent-plain (3d) and the three undo rows, so a
    # 14-day window has exactly ONE sweepable row left. `would_delete` is the
    # preview count either way; `deleted` is only ever nonzero on a REAL
    # sweep — a dry run that reported deleted=1 would be lying about having
    # deleted something.
    check("would delete the one plain row now outside a 14-day window",
          dry["would_delete"] == 1, dry)
    check("a dry run's own `deleted` count stays 0", dry["deleted"] == 0, dry)
    check("flagged dry", dry["dry"] is True, dry)
    check("nothing was actually removed", ids(db) == before, ids(db))

    print("\n5. retain_days=0 means keep forever and sweeps nothing")
    res0 = g["_rec_sweep_retention"](days=0)
    check("forever", res0["forever"] is True and res0["deleted"] == 0, res0)
    check("table untouched", ids(db) == before, ids(db))
    g["_rec_set_retain_days"](0)
    res0b = g["_rec_sweep_retention"]()
    check("and the setting alone is enough to disable it",
          res0b["forever"] is True, res0b)
    check("still untouched", ids(db) == before, ids(db))
    g["_rec_set_retain_days"](90)

    print("\n6. the routes round-trip (and preview before they delete)")
    d = g["recorder_retention_get"](Ctx())
    check("GET ok", d["ok"] is True and d["retain_days"] == 90, d)
    check("bounds published", (d["min_days"], d["max_days"], d["default_days"],
                               d["undo_trash_days"]) == (14, 3650, 90, 14), d)
    check("total is the real row count", d["total"] == count(db), d)
    check("preview present", d["next_sweep"]["would_delete"] == 0, d["next_sweep"])
    p = g["recorder_retention_post"](Ctx(body={"retain_days": 3}))
    check("POST clamps and says so", p["retain_days"] == 14 and p["asked"] == 3
          and p["clamped"] is True, p)
    check("POST previews what the sweep will take",
          p["next_sweep"]["would_delete"] == 1, p["next_sweep"])
    check("POST did NOT delete inline (the loop owns the sweep)",
          ids(db) == before, ids(db))
    check("and it armed the loop", g["_rec_retain_last"] == 0.0, g["_rec_retain_last"])
    for bad in ({}, {"retain_days": "abc"}, {"retain_days": None},
                {"retain_days": True}):
        r = g["recorder_retention_post"](Ctx(body=bad))
        ok = isinstance(r, tuple) and r[1] == 400 and r[0]["ok"] is False
        check("POST %r -> 400" % (bad,), ok, r)
    g["recorder_retention_post"](Ctx(body={"retain_days": 0}))
    check("0 is accepted verbatim as 'forever'",
          g["recorder_retention_get"](Ctx())["forever"] is True)
    g["_rec_set_retain_days"](90)

    print("\n7. AGAINST A SNAPSHOT OF THE OWNER'S LIVE recorder.db")
    if not os.path.exists(LIVE_DB):
        print("  --   no live recorder.db on this Mac; skipped")
        return
    live_before = count(LIVE_DB)
    copy = os.path.join(tmp, "recorder-copy.db")
    src = sqlite3.connect("file:%s?mode=ro" % LIVE_DB, uri=True)   # READ ONLY
    dst = sqlite3.connect(copy)
    try:
        src.backup(dst)                 # sqlite's own consistent snapshot
    finally:
        dst.close()
        src.close()
    check("snapshot has the same rows as the live db",
          count(copy) == live_before, (count(copy), live_before))

    # Inject the two shapes the owner's db happens not to contain, so the
    # exemption is proved on real data volume rather than on a toy table.
    insert(copy, [
        {"id": "t131-undone", "ts": time.time() - 400 * DAY, "status": "undone",
         "kind": "write", "reversible": "yes", "target": "/tmp/t131"},
        {"id": "t131-snap", "ts": time.time() - 400 * DAY, "kind": "write",
         "reversible": "yes", "target": "/tmp/t131b",
         "snapshot_ref": json.dumps({"workdir": "/tmp", "commit": "cafe"})},
        {"id": "t131-plain", "ts": time.time() - 400 * DAY},
    ])
    cut14 = time.time() - 14 * DAY
    # Independently re-derived from the module's own _REC_KEEP_SQL (De
    # Morgan's on "NOT (kept)"): a row survives if it is undone, holds a
    # snapshot, OR is itself a reversible write/shell action — that third
    # clause exists even without a snapshot_ref, so a hand check using only
    # the first two would silently pass on today's data and quietly rot the
    # day a real db actually has such a row.
    KEEP_CLAUSE = ("status!='undone' AND (snapshot_ref IS NULL OR snapshot_ref='') "
                   "AND NOT (reversible IN ('yes','partial') AND kind IN ('write','shell'))")
    old_plain = count(copy, "ts < ? AND " + KEEP_CLAUSE, (cut14,))
    dry = g["_rec_sweep_retention"](days=14, dry=True, db=copy)
    check("dry run's preview agrees with a hand-written count of the same predicate",
          dry["would_delete"] == old_plain, (dry["would_delete"], old_plain))
    check("a dry run's own `deleted` count stays 0", dry["deleted"] == 0, dry)
    check("dry run removed nothing from the copy",
          count(copy) == live_before + 3, count(copy))
    real = g["_rec_sweep_retention"](days=14, db=copy)
    check("the real sweep removed exactly what the dry run promised",
          real["deleted"] == old_plain and count(copy) == live_before + 3 - old_plain,
          (real["deleted"], count(copy)))
    surv = ids(copy)
    check("the injected undone row SURVIVED", "t131-undone" in surv)
    check("the injected snapshot row SURVIVED", "t131-snap" in surv)
    check("the injected plain row did not", "t131-plain" not in surv)
    check("nothing older than the cutoff is left without undo material",
          count(copy, "ts < ? AND " + KEEP_CLAUSE, (cut14,)) == 0)
    check("THE OWNER'S LIVE recorder.db IS UNTOUCHED",
          count(LIVE_DB) == live_before, (count(LIVE_DB), live_before))


def main():
    tmp = tempfile.mkdtemp(prefix="t131rec-")
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
