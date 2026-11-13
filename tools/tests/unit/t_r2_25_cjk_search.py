#!/usr/bin/env python3
"""R2-25 — a word inside an unbroken CJK run is searchable.

FTS5's unicode61 tokenizer splits on spaces and punctuation only, so
東京に行きます is ONE token and searching 東京 (or 会議 inside 会議は月曜日です)
found nothing.  aux_index now keeps a trigram shadow index, `items_tri`, over
the rows that contain CJK, kept in step by its own triggers, and answers a
query with a CJK token from it: 3+ character tokens as trigram phrases,
shorter ones as substring tests.  An index.db from before the change is
backfilled the first time a connection opens it.

Execs the REAL aux_index.py into a stub globals dict against a throwaway
HOME (calendar stubbed out).  No dashboard, no network.

Fails on the old code: every CJK sub-word search came back empty, there was
no items_tri to migrate or keep in step, and /api/search/status had no
`trigram` flag.
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s  %r" % (name, extra))


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_index.py")

TMP = tempfile.mkdtemp(prefix="hermes-r2-25-")
DATA = os.path.join(TMP, "home", ".hermes", "dashboard")
CH = os.path.join(DATA, "chats")
os.makedirs(CH, exist_ok=True)
NOW = time.time()


def chat(sid, title, *turns):
    msgs = []
    for i, t in enumerate(turns):
        msgs.append({"role": "user" if i % 2 == 0 else "bot", "text": t, "ts": NOW + i})
    json.dump({"title": title, "messages": msgs},
              io.open(os.path.join(CH, sid + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False)


chat("chat-zh", "", "我明天要去东京开会", "好的，祝你一路顺风")
chat("chat-ja", "出張メモ", "東京に行きます。会議は月曜日です", "了解しました")
chat("chat-ko", "", "서울에서 만나요", "네 좋아요")
chat("chat-mixed", "hermes plans", "hermes の東京オフィス", "ok")
chat("chat-ascii", "Kumquat notes", "the kumquat harvest is on thursday", "noted")

g = {"__name__": "aux_index_r2_25", "DATA": DATA,
     "register_get": lambda p, f: None, "register_post": lambda p, f: None}
exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
g["_ix_cal_lines"] = lambda: None             # never shell out to icalBuddy


class Ctx:
    def __init__(self, **q):
        self.q = q

    def q1(self, k):
        return self.q.get(k)


def search(q, **kw):
    r = g["_ix_search"](Ctx(q=q, **kw))
    return r[0] if isinstance(r, tuple) else r


def ids(res):
    return [r.get("id") for r in (res or {}).get("results", [])]


def scalar(sql, db=None):
    con = sqlite3.connect(db or g["IX_DB"])
    try:
        return con.execute(sql).fetchone()[0]
    except sqlite3.Error as e:                   # e.g. no items_tri (old code)
        return "%s: %s" % (type(e).__name__, e)
    finally:
        con.close()


try:
    sw = g["index_sweep"]("r2-25", sources=("chat",))
    ok("sweep ok", sw.get("ok") is True, sw)
    ok("status carries trigram:true", g["_ix_status"]().get("trigram") is True,
       g["_ix_status"]())
    ok("ONLY the four CJK rows are in items_tri",
       scalar("SELECT COUNT(*) FROM items_tri") == 4
       and scalar("SELECT COUNT(*) FROM items") == 5,
       (scalar("SELECT COUNT(*) FROM items_tri"), scalar("SELECT COUNT(*) FROM items")))

    print("\n=== a word inside an unbroken CJK run ===")
    for q, want in (("东京", "chat:chat-zh"), ("会議", "chat:chat-ja"),
                    ("月曜日", "chat:chat-ja"), ("서울", "chat:chat-ko"),
                    ("出張", "chat:chat-ja")):
        res = search(q)
        ok("%s finds %s" % (q, want), res.get("ok") is True and want in ids(res), res)
    res = search("会議")
    hit = (res.get("results") or [{}])[0]
    sn, ms, ml = hit.get("snippet", ""), hit.get("mark_start"), hit.get("mark_len")
    ok("the mark covers the CJK word in the snippet",
       isinstance(ms, int) and sn[ms:ms + ml] == "会議", (sn, ms, ml))
    ok("CJK results carry the per-source counts",
       res.get("sources", {}).get("chat") == 1, res.get("sources"))
    ok("a word absent from every row finds nothing", ids(search("大阪")) == [])
    ok("a 3+ char word absent everywhere finds nothing", ids(search("大阪城")) == [])

    print("\n=== mixed queries, filters, grammar ===")
    ok("hermes 東京 -> only the row with both",
       ids(search("hermes 東京")) == ["chat:chat-mixed"], ids(search("hermes 東京")))
    ok("東京 -> both rows that contain it",
       sorted(ids(search("東京"))) == ["chat:chat-ja", "chat:chat-mixed"], ids(search("東京")))
    ok("a trailing * on a CJK query is harmless",
       "chat:chat-ja" in ids(search("会議*")), search("会議*"))
    res = search('東京" OR NEAR(')
    ok("FTS grammar in a CJK query is searched as words, never an error",
       res.get("ok") is True, res)
    ok("source filter applies to the CJK path",
       ids(search("東京", source="message")) == [] and search("東京", source="message")["ok"])
    ok("ASCII search is unchanged (unicode61 path)",
       ids(search("kumquat")) == ["chat:chat-ascii"], ids(search("kumquat")))

    print("\n=== items_tri stays in step with items ===")
    chat("chat-ja", "出張メモ", "the trip was cancelled", "ok")          # CJK only in the title now
    chat("chat-zh", "", "no more chinese here", "fine")                 # no CJK at all
    chat("chat-ascii", "Kumquat notes", "kumquat 収穫 thursday", "noted")  # gains CJK
    os.remove(os.path.join(CH, "chat-ko.json"))
    g["index_sweep"]("r2-25-step", sources=("chat",))
    ok("a row that lost its CJK left items_tri", ids(search("东京")) == [], search("东京"))
    ok("a row with CJK only in its title stays findable by it",
       ids(search("出張")) == ["chat:chat-ja"], ids(search("出張")))
    ok("... and its old body is gone from the shadow copy", ids(search("会議")) == [])
    ok("a row that gained CJK entered items_tri",
       ids(search("収穫")) == ["chat:chat-ascii"], ids(search("収穫")))
    ok("a deleted row left items_tri", ids(search("서울")) == [], search("서울"))
    ok("items_tri row count tracks the CJK rows (ja, mixed, ascii)",
       scalar("SELECT COUNT(*) FROM items_tri") == 3, scalar("SELECT COUNT(*) FROM items_tri"))
    con = sqlite3.connect(g["IX_DB"])
    try:
        con.execute("INSERT INTO items_tri(items_tri) VALUES('integrity-check')")
        con.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
        integ = True
    except sqlite3.DatabaseError as e:
        integ = e
    finally:
        con.close()
    ok("both FTS indexes pass integrity-check", integ is True, integ)

    print("\n=== an index.db from before R2-25 is backfilled ===")
    old = os.path.join(DATA, "old-index.db")
    con = sqlite3.connect(old)
    con.executescript(g["_IX_SCHEMA"])             # the base schema: no items_tri
    con.execute("INSERT INTO items(id, source, ts, title, body, ref, meta) "
                "VALUES ('chat:legacy', 'chat', 1, 'legacy', '昨日の議事録を送ります', 'legacy', '{}')")
    con.execute("INSERT INTO items(id, source, ts, title, body, ref, meta) "
                "VALUES ('chat:legacy2', 'chat', 2, 'legacy2', 'plain english', 'legacy2', '{}')")
    con.commit()
    con.close()
    ok("the legacy db has no items_tri", scalar(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='items_tri'", old) == 0)
    g["IX_DB"] = old
    res = search("議事録")
    ok("the first search on it finds the CJK word (no sweep needed)",
       ids(res) == ["chat:legacy"], res)
    ok("backfill copied only the CJK row",
       scalar("SELECT COUNT(*) FROM items_tri", old) == 1)
    g["_ix_conn"]().close()
    ok("a second connection does not backfill twice",
       scalar("SELECT COUNT(*) FROM items_tri", old) == 1)

    print("\n=== no trigram tokenizer: CJK queries still answer ===")
    g["_ix_tri_ok"][:] = [False]                  # as on SQLite < 3.34
    res = search("議事録")
    ok("the probe reports the shadow index unusable",
       g["_ix_status"]().get("trigram") is False, g["_ix_status"]())
    ok("falls back to the unicode61 path without an error",
       res.get("ok") is True and "error" not in res, res)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
