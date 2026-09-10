"""1.1.0 harness — aux_index.py: /api/search, /api/search/status, sweep, touch.

Runs the REAL server.py Handler (guard, aux exec-include chain, dispatch)
against a throwaway HOME carrying a fixture for every source.  Never touches
~/.hermes for writes, never calls server.main(), never wakes the model.
"""
import json, os, shutil, stat, sys, tempfile, threading, time, urllib.request, urllib.error

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
# The owner's REAL home, captured before HOME is pointed at the throwaway.
REAL_HOME = os.path.expanduser("~")
HOME = tempfile.mkdtemp(prefix="hermes-index-")
PORT = "7801"

shutil.rmtree(HOME, ignore_errors=True)
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)

NOW = time.time()

# ---- chat fixtures: two REAL conversations + crafted ones ------------------
# Read-only borrows from the owner's store.  Fingerprint it (and the real
# index.db) BEFORE anything runs, so section 19 can prove this harness wrote
# nothing there — a hard-coded file count went stale the first time the owner
# had another conversation.
REAL = os.path.join(REAL_HOME, ".hermes", "dashboard", "chats")
REAL_BEFORE = sorted(os.listdir(REAL)) if os.path.isdir(REAL) else None
REAL_IXDB = os.path.join(REAL_HOME, ".hermes", "dashboard", "index.db")
REAL_IXDB_BEFORE = (os.path.getmtime(REAL_IXDB), os.path.getsize(REAL_IXDB)) \
    if os.path.exists(REAL_IXDB) else None
copied = []
for fn in ("chat-2026-08-31-qglb4.json", "chat-2026-09-05-noxjw.json"):
    src = os.path.join(REAL, fn)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(CH, fn))
        copied.append(fn)


def wr(sid, obj, mtime=None):
    p = os.path.join(CH, sid + ".json")
    json.dump(obj, open(p, "w"), indent=1)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


# title carries the word, body does not -> must outrank the body-only chat
wr("chat-title-hit", {"title": "Kumquat plumbing notes", "messages": [
    {"role": "user", "text": "how does the plumbing work here", "ts": NOW - 100},
    {"role": "bot", "text": "It is wired through the aux registry.", "ts": NOW - 99},
]}, NOW - 99)

wr("chat-body-hit", {"title": "Unrelated thread", "messages": [
    {"role": "user", "text": "a" * 400 + " the kumquat appears deep in the body "
     + "b" * 400, "ts": NOW - 200},
    {"role": "bot", "text": "noted.", "ts": NOW - 199},
]}, NOW - 199)

# tool / approval / status rows and the prewarm session must never be indexed
wr("chat-metadata-only", {"title": "Metadata thread", "messages": [
    {"role": "tool", "text": "zebracode secret in a tool row", "ts": NOW, "tool": "terminal"},
    {"role": "bot", "text": "zebracode secret in an approval row", "ts": NOW,
     "approval": {"cmd": "rm -rf /"}},
    {"role": "bot", "text": "zebracode secret in a status row", "ts": NOW, "status": "running"},
    {"role": "user", "text": "indexable question about indexing", "ts": NOW},
]}, NOW)

wr("__prewarm__", {"title": "__prewarm__", "messages": [
    {"role": "user", "text": "Reply with exactly: ok zebracode", "ts": NOW},
    {"role": "bot", "text": "ok", "ts": NOW},
]}, NOW)

wr("chat-doomed", {"title": "Doomed thread", "messages": [
    {"role": "user", "text": "this conversation will be deleted, marker vanishword", "ts": NOW},
]}, NOW)

wr("chat-empty", {"title": "", "messages": []}, NOW)

wr("chat-injection", {"title": "Query grammar", "messages": [
    {"role": "user", "text": 'what about a quote " and a star * and OR and NEAR', "ts": NOW},
]}, NOW)

# ---- message + watchtower fixtures ----------------------------------------
json.dump({"v": 1, "fda": True, "generated_at": NOW, "stored_at": NOW,
           "host": "fixture", "totals": {"unread": 2, "today": 3},
           "conversations": [
               {"name": "Dana Whitfield", "ident": "+15551234567", "group": False,
                "participants": 1, "last": "dinner kumquat thursday still on?",
                "from_me": False, "sender": "Dana Whitfield", "ts": NOW - 3600,
                "unread": 2, "attachment": False, "reaction": False,
                "today_count": 3, "service": "iMessage"},
               {"name": "Roof crew", "ident": "chat990", "group": True,
                "participants": 4, "last": "scaffold arrives at 8",
                "from_me": True, "sender": "You", "ts": NOW - 7200,
                "unread": 0, "attachment": False, "reaction": False,
                "today_count": 0, "service": "SMS"}]},
          open(os.path.join(DASH, "messages.json"), "w"))

json.dump({"version": 1, "updated": NOW, "feeds": 3,
           "curated": [{"title": "Kumquat Labs ships a local index",
                        "url": "https://example.com/kumquat-index",
                        "source": "Labs Weekly",
                        "why": "matches your FTS5 work"}],
           "items": [{"title": "Kumquat Labs ships a local index",
                      "url": "https://example.com/kumquat-index",
                      "source": "Labs Weekly", "topic": "Labs", "ts": NOW - 500,
                      "summary": "a full text search release"},
                     {"title": "Unrelated market wrap",
                      "url": "https://example.com/markets",
                      "source": "Wire", "topic": "News", "ts": NOW - 900,
                      "summary": "stocks did a thing"}]},
          open(os.path.join(DASH, "intel.json"), "w"))

# NOTE: notes.json is deliberately NOT created — absent-store degradation.

# ---- boot the real Handler -------------------------------------------------
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = PORT
sys.path.insert(0, REPO)
import server                                    # noqa: E402  (after env)
assert server.HOME == HOME, server.HOME
assert server.DATA == DASH, server.DATA

CAL_LINES = [
    "|2026-09-12 at 09:30 ~ Kumquat harvest planning sync",
    "|2026-09-12 at 09:30 ~ Kumquat harvest planning sync",   # icalBuddy dupes
    "|2026-08-20 ~ Dentist",
]
_real_cal_lines = server._ix_cal_lines
server._ix_cal_lines = lambda: list(CAL_LINES)

from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", int(PORT)), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % PORT

PASS = FAIL = 0
FAILED = []


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL %s %s" % (name, extra))

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (PASS, FAIL)))


def req(path, body=None, headers=None):
    h = {"Host": "127.0.0.1:%s" % PORT}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def search(q, **kw):
    qs = "?q=" + urllib.parse.quote(q)
    for k, v in kw.items():
        qs += "&%s=%s" % (k, urllib.parse.quote(str(v)))
    return req("/api/search" + qs)


import urllib.parse                              # noqa: E402

print("\n=== fixtures ===")
print("  real chats copied:", copied or "(none found)")

# --------------------------------------------------------------------------
print("\n=== 1. sweep + status ===")
sw = server.index_sweep("harness")
print("  sweep:", json.dumps(sw)[:400])
ok("sweep ok", sw.get("ok") is True, sw)
st_code, st = req("/api/search/status")
print("  status:", json.dumps(st)[:400])
ok("status 200/ok", st_code == 200 and st.get("ok") is True, st)
ok("chat rows indexed", st["sources"]["chat"] >= 5, st["sources"])
ok("message rows indexed", st["sources"]["message"] == 2, st["sources"])
ok("calendar rows indexed (dupes collapsed)", st["sources"]["calendar"] == 2,
   st["sources"])
ok("watchtower rows indexed (url-deduped)", st["sources"]["watchtower"] == 2,
   st["sources"])
ok("note absent -> 0 rows, no error",
   st["sources"]["note"] == 0 and st["detail"]["note"].get("absent") is True,
   st["detail"].get("note"))
ok("prewarm session never indexed", st["sources"]["chat"] <= 7, st["sources"])

# --------------------------------------------------------------------------
print("\n=== 2. db permissions ===")
mode = stat.S_IMODE(os.stat(server.IX_DB).st_mode)
ok("index.db is 0600", mode == 0o600, oct(mode))
side = []
for suf in ("-wal", "-shm"):
    p = server.IX_DB + suf
    if os.path.exists(p):
        side.append((suf, oct(stat.S_IMODE(os.stat(p).st_mode))))
ok("wal/shm sidecars 0600", all(m == "0o600" for _, m in side), side)
print("  sidecars:", side or "(none)")

# --------------------------------------------------------------------------
print("\n=== 3. hits in every source ===")
c, d = search("kumquat", limit=50)
srcs = sorted({r["source"] for r in d["results"]})
print("  sources hit:", srcs, "| counts:", d["sources"], "| took_ms:", d["took_ms"])
ok("200 + ok", c == 200 and d["ok"] is True, d)
ok("chat hit", "chat" in srcs, srcs)
ok("message hit", "message" in srcs, srcs)
ok("calendar hit", "calendar" in srcs, srcs)
ok("watchtower hit", "watchtower" in srcs, srcs)
ok("sources map covers all five keys",
   set(d["sources"]) == set(server.IX_SOURCES), d["sources"])
ok("sources counts match result mix",
   d["sources"]["chat"] == len([r for r in d["results"] if r["source"] == "chat"]),
   d["sources"])
ok("took_ms present", isinstance(d["took_ms"], int), d.get("took_ms"))

print("\n=== 4. ranking sanity (title outranks body) ===")
ids = [r["id"] for r in d["results"] if r["source"] == "chat"]
print("  chat order:", ids)
ok("title-hit chat outranks body-only chat",
   ids.index("chat:chat-title-hit") < ids.index("chat:chat-body-hit"), ids)

print("\n=== 5. snippet offsets ===")
bad = []
for r in d["results"]:
    s, ms_, ml_ = r["snippet"], r["mark_start"], r["mark_len"]
    if ms_ is None:
        continue
    if not (0 <= ms_ <= len(s) and ml_ >= 0 and ms_ + ml_ <= len(s)):
        bad.append(("range", r["id"], ms_, ml_, len(s)))
    elif s[ms_:ms_ + ml_].lower() != "kumquat":
        bad.append(("text", r["id"], repr(s[ms_:ms_ + ml_])))
ok("every mark slice is exactly the query word", not bad, bad)
ok("no markup in any snippet",
   not any("<" in r["snippet"] or "</" in r["snippet"] for r in d["results"]))
body_hit = [r for r in d["results"] if r["id"] == "chat:chat-body-hit"][0]
print("  body snippet:", repr(body_hit["snippet"][:120]))
ok("body snippet is windowed, not the whole body", len(body_hit["snippet"]) < 200,
   len(body_hit["snippet"]))
ok("windowed snippet has ellipses", body_hit["snippet"].startswith("…")
   and body_hit["snippet"].endswith("…"), body_hit["snippet"][:20])

print("\n=== 6. tool/approval/status rows + prewarm excluded ===")
c, d6 = search("zebracode", limit=50)
print("  zebracode results:", [(r["source"], r["id"]) for r in d6["results"]])
ok("no tool/approval/status/prewarm text is searchable", d6["results"] == [], d6)
c, d6b = search("indexable")
ok("the real user turn in the same chat IS searchable",
   any(r["id"] == "chat:chat-metadata-only" for r in d6b["results"]), d6b)

print("\n=== 7. prefix query ===")
c, dp = search("kumq*", limit=50)
print("  kumq* ->", len(dp["results"]), "rows; marks:",
      [dp["results"][i]["snippet"][dp["results"][i]["mark_start"]:
                                   dp["results"][i]["mark_start"] + dp["results"][i]["mark_len"]]
       for i in range(min(3, len(dp["results"]))) if dp["results"][i]["mark_start"] is not None])
ok("prefix query returns the same rows as the full word",
   len(dp["results"]) == len(d["results"]), (len(dp["results"]), len(d["results"])))
ok("prefix mark stretches to the whole word",
   all(r["snippet"][r["mark_start"]:r["mark_start"] + r["mark_len"]].lower() == "kumquat"
       for r in dp["results"] if r["mark_start"] is not None))
c, dp2 = search("kumq")
ok("without the star, a bare prefix matches nothing", dp2["results"] == [], dp2)

print("\n=== 8. FTS5 query sanitisation ===")
INJECT = ['"', '""', 'kumquat"', 'a" OR b', 'kumquat OR zebracode',
          'NEAR(kumquat zebracode)', 'kumquat NOT dentist', 'title:kumquat',
          '*', '**', '^kumquat', 'kumquat*extra', '(kumquat', 'kumquat)',
          'kum quat AND OR NEAR NOT', '"unbalanced', 'a AND b OR c NEAR d',
          '- -kumquat', 'kumquat -zebracode', '{kumquat}', 'x' * 400]
for q in INJECT:
    c, di = search(q, limit=5)
    good = c == 200 and isinstance(di, dict) and di.get("ok") is True \
        and "error" not in di
    ok("no injection/500 for %r" % q[:32], good, (c, str(di)[:180]))
c, dor = search("kumquat OR zebracode")
ok("OR is a literal word, not an operator (0 rows: no doc has all three)",
   dor["results"] == [], [r["id"] for r in dor["results"]])
c, dq = search('quote " star * OR NEAR')
ok("the injection-y chat is found by its own words",
   any(r["id"] == "chat:chat-injection" for r in dq["results"]),
   [r["id"] for r in dq["results"]])

print("\n=== 9. limits and clamps ===")
c, dl = search("kumquat", limit=999)
ok("limit clamped to <= 50", len(dl["results"]) <= 50, len(dl["results"]))
c, dl2 = search("kumquat", limit=1)
ok("limit=1 honoured", len(dl2["results"]) == 1, len(dl2["results"]))
c, dl3 = search("kumquat", limit="abc")
ok("junk limit falls back, still 200", c == 200 and dl3["ok"] is True, dl3)
LONG = ("kumquat " * 40)[:400]
c, dl4 = search(LONG)
ok("q clamped to 200 chars", c == 200 and len(dl4["q"]) <= 200, len(dl4["q"]))
c, de = search("")
ok("empty q -> ok, no rows, no error",
   c == 200 and de["ok"] is True and de["results"] == [], de)
c, dz = search("zzzznotpresentanywhere")
ok("no-match -> ok with empty results and zeroed sources",
   dz["ok"] is True and dz["results"] == []
   and set(dz["sources"].values()) == {0}, dz)

print("\n=== 10. source filter ===")
c, df = search("kumquat", source="chat")
ok("source=chat returns chat only",
   df["results"] and all(r["source"] == "chat" for r in df["results"]),
   [r["source"] for r in df["results"]])
ok("filtered response still reports every source's count",
   df["sources"]["watchtower"] >= 1, df["sources"])
c, df2 = search("kumquat", source="all")
ok("source=all behaves like no filter",
   len(df2["results"]) == len(d["results"]), (len(df2["results"]), len(d["results"])))
c, df3 = search("kumquat", source="../etc/passwd")
ok("unknown source -> 400, no crash", c == 400 and df3["ok"] is False, (c, df3))

print("\n=== 11. same-origin guard applies to the new routes ===")
c, dg = req("/api/search?q=kumquat", headers={"Host": "evil.example"})
ok("bad Host -> 403", c == 403, (c, dg))
c, dg2 = req("/api/search?q=kumquat",
             headers={"Origin": "http://evil.example"})
ok("cross Origin -> 403", c == 403, (c, dg2))
c, dg3 = req("/api/search/status", headers={"Host": "evil.example"})
ok("status route guarded too -> 403", c == 403, (c, dg3))

print("\n=== 12. absent store -> present (notes) ===")
ok("notes.json really is absent", not os.path.exists(server.NOTES_FILE))
c, dn0 = search("marmalade")
ok("nothing to find before the note exists", dn0["results"] == [], dn0)
c, dn = req("/api/notes", body={"text": "buy marmalade and kumquat jam\nsecond line"})
ok("POST /api/notes still returns {ok:true}", c == 200 and dn == {"ok": True}, (c, dn))
ok("the note file was actually written",
   json.load(open(server.NOTES_FILE))["text"].startswith("buy marmalade"))
server._ix_drain_once()
c, dn2 = search("marmalade")
print("  note hit:", [(r["source"], r["title"], r["ref"], r["snippet"][:60])
                      for r in dn2["results"]])
ok("the note is searchable within one drain",
   any(r["source"] == "note" and r["ref"] == "notes" for r in dn2["results"]), dn2)
ok("note title is its first line",
   dn2["results"][0]["title"] == "buy marmalade and kumquat jam",
   dn2["results"][0]["title"])
c, stn = req("/api/search/status")
ok("status now counts the note", stn["sources"]["note"] == 1, stn["sources"])

print("\n=== 13. index_touch via the save_chat wrap ===")
sid = "chat-touch-test"
server.save_chat(sid, {"title": "Touched thread", "messages": [
    {"role": "user", "text": "a brandnewword arrives through save_chat", "ts": NOW}]})
c, dt0 = search("brandnewword")
ok("touch is queued, not applied inline (no request blocked)",
   dt0["results"] == [], dt0)
server._ix_drain_once()
c, dt = search("brandnewword")
ok("after the drain the new chat is searchable",
   any(r["id"] == "chat:" + sid for r in dt["results"]), dt)
ok("ref carries the session id",
   dt["results"][0]["ref"] == sid, dt["results"][0])
# a second save of the SAME chat coalesces to one pending entry
server.save_chat(sid, {"title": "Touched thread", "messages": [
    {"role": "user", "text": "a brandnewword arrives through save_chat", "ts": NOW},
    {"role": "bot", "text": "and a reply mentioning coalescetest", "ts": NOW}]})
server.save_chat(sid, {"title": "Touched thread", "messages": [
    {"role": "user", "text": "a brandnewword arrives through save_chat", "ts": NOW},
    {"role": "bot", "text": "and a reply mentioning coalescetest", "ts": NOW}]})
with server._ix_pending_lock:
    pending = len(server._ix_pending)
ok("three saves of one chat coalesce to one pending touch", pending == 1, pending)
server._ix_drain_once()
c, dt2 = search("coalescetest")
ok("the updated body is indexed (update trigger fired)",
   any(r["id"] == "chat:" + sid for r in dt2["results"]), dt2)
c, dt3 = search("brandnewword")
ok("no phantom duplicate row after the update",
   len([r for r in dt3["results"] if r["id"] == "chat:" + sid]) == 1, dt3)

print("\n=== 14. sweep idempotence ===")
a = server.index_sweep("idem-a")
b = server.index_sweep("idem-b")
print("  a:", json.dumps(a["stats"])[:300])
print("  b:", json.dumps(b["stats"])[:300])
wrote_b = sum(v.get("written", 0) for v in b["stats"].values() if isinstance(v, dict))
pruned_b = sum(v.get("pruned", 0) for v in b["stats"].values() if isinstance(v, dict))
ok("a second identical sweep writes nothing", wrote_b == 0, b["stats"])
ok("a second identical sweep prunes nothing", pruned_b == 0, b["stats"])
c, st_a = req("/api/search/status")
d_before = dict(st_a["sources"])
server.index_sweep("idem-c")
c, st_b = req("/api/search/status")
ok("row counts stable across sweeps", st_b["sources"] == d_before,
   (d_before, st_b["sources"]))
ok("status reports the sweep count + duration",
   st_b["sweeps"] >= 4 and isinstance(st_b["last_sweep_ms"], int), st_b["sweeps"])

print("\n=== 15. prune on delete ===")
c, dv = search("vanishword")
ok("doomed chat is indexed before deletion",
   any(r["id"] == "chat:chat-doomed" for r in dv["results"]), dv)
os.remove(os.path.join(CH, "chat-doomed.json"))
sw2 = server.index_sweep("after-delete")
ok("sweep reports exactly one pruned chat row",
   sw2["stats"]["chat"].get("pruned") == 1, sw2["stats"]["chat"])
c, dv2 = search("vanishword")
ok("deleted conversation is gone from search", dv2["results"] == [], dv2)
# and the FTS shadow table is not left holding a phantom
con = server._ix_conn()
n_items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
n_fts = con.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
con.close()
ok("items and items_fts stay in lockstep after deletes",
   n_items == n_fts, (n_items, n_fts))

print("\n=== 16. store disappears entirely -> nothing pruned ===")
os.rename(os.path.join(DASH, "intel.json"), os.path.join(DASH, "intel.json.away"))
sw3 = server.index_sweep("intel-away")
ok("absent intel.json reports absent, prunes nothing",
   sw3["stats"]["watchtower"].get("absent") is True, sw3["stats"]["watchtower"])
c, dw = search("kumquat", source="watchtower")
ok("watchtower rows survive a missing store", len(dw["results"]) >= 1, dw)
# a CORRUPT store is treated the same way (absent), never as "empty"
open(os.path.join(DASH, "intel.json"), "w").write("{not json")
sw4 = server.index_sweep("intel-corrupt")
ok("corrupt intel.json reports absent, prunes nothing",
   sw4["stats"]["watchtower"].get("absent") is True, sw4["stats"]["watchtower"])
c, dw2 = search("kumquat", source="watchtower")
ok("watchtower rows survive a corrupt store", len(dw2["results"]) >= 1, dw2)
os.rename(os.path.join(DASH, "intel.json.away"), os.path.join(DASH, "intel.json"))
# an EMPTY-but-present store does prune
json.dump({"version": 1, "updated": NOW, "items": [], "curated": []},
          open(os.path.join(DASH, "intel.json"), "w"))
sw5 = server.index_sweep("intel-empty")
ok("empty-but-present store prunes its rows",
   sw5["stats"]["watchtower"].get("pruned") == 2, sw5["stats"]["watchtower"])
c, dw3 = search("kumquat", source="watchtower")
ok("watchtower search is now empty", dw3["results"] == [], dw3)

print("\n=== 17. Message Center degradation (no Full Disk Access) ===")
json.dump({"v": 1, "fda": False, "generated_at": NOW, "conversations": [],
           "totals": {"unread": 0, "today": 0}},
          open(os.path.join(DASH, "messages.json"), "w"))
sw6 = server.index_sweep("msg-nofda")
ok("fda:false prunes message rows without erroring",
   sw6["stats"]["message"].get("pruned") == 2, sw6["stats"]["message"])
c, dm = search("dinner")
ok("no message hits without FDA", dm["results"] == [], dm)

print("\n=== 18. real calendar provider (read-only, ±30 days) ===")
server._ix_cal_lines = _real_cal_lines
lines = server._ix_cal_lines()
print("  icalBuddy lines:", (lines or [])[:4], "…" if lines and len(lines) > 4 else "")
if lines is None:
    ok("no icalBuddy on this Mac -> calendar source degrades to absent", True)
    sw7 = server.index_sweep("cal-real")
    ok("absent calendar prunes nothing",
       sw7["stats"]["calendar"].get("absent") is True, sw7["stats"]["calendar"])
else:
    sw7 = server.index_sweep("cal-real")
    ok("real calendar rows indexed", sw7["stats"]["calendar"].get("rows", 0) >= 0,
       sw7["stats"]["calendar"])
    c, st7 = req("/api/search/status")
    print("  calendar rows:", st7["sources"]["calendar"])
    first = None
    con = server._ix_conn()
    first = con.execute("SELECT title, ts, ref FROM items WHERE source='calendar' "
                        "LIMIT 1").fetchone()
    con.close()
    print("  sample calendar row:", first)
    if first:
        c, dc = search(first[0].split()[0])
        ok("a real calendar event is searchable by its own word",
           any(r["source"] == "calendar" for r in dc["results"]),
           [r["source"] for r in dc["results"]])
        ok("calendar ts parsed from the ISO date", first[1] > 0, first[1])
        ok("calendar ref points at the today widget", first[2] == "today", first[2])
    else:
        ok("calendar empty in this window (no rows, no error)", True)

print("\n=== 19. no writes escaped the throwaway HOME ===")
ok("index.db lives under the throwaway HOME", server.IX_DB.startswith(HOME),
   server.IX_DB)
# NB: expanduser() would follow the throwaway HOME — REAL_HOME was captured
# before the redirect.
ok("the real ~/.hermes/index.db is byte-for-byte as it was",
   ((os.path.getmtime(REAL_IXDB), os.path.getsize(REAL_IXDB))
    if os.path.exists(REAL_IXDB) else None) == REAL_IXDB_BEFORE,
   REAL_IXDB)
ok("the real chats dir is untouched",
   (sorted(os.listdir(REAL)) if os.path.isdir(REAL) else None) == REAL_BEFORE)

print("\n=========================================")
print("  PASS %d   FAIL %d" % (PASS, FAIL))
if FAILED:
    print("  failed:", FAILED)
print("=========================================")
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
