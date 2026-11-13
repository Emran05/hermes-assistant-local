"""C58 + C108 — the unified search index after a deletion.

C58: POST /api/sessions/delete removed only chats/<id>.json; the transcript
stayed in index.db, so /api/search (and MCP hermes_search, which proxies it)
kept returning it for up to the 30-minute sweep, and clicking the hit made the
deleted id the active session again.

C108: the FTS schema was created once, at import. Deleting index.db under a
running dashboard left every search and sweep failing "no such table" until a
restart.

Real server.py import (aux chain included) + the real Handler on a free
loopback port, throwaway HOME, icalBuddy stubbed out. No model, no ~/.hermes.
"""
import json, os, shutil, socket, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-c58-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time()


def chat(sid, words):
    json.dump({"title": "", "messages": [
        {"role": "user", "text": "please remember %s" % words, "ts": NOW},
        {"role": "bot", "text": "noted, %s" % words, "ts": NOW + 1}]},
        open(os.path.join(CH, sid + ".json"), "w"))


chat("chat-2026-10-21-secret", "the vault code wombatsecret")
chat("chat-2026-10-21-keeper", "the garden plan kumquatkeep")

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server._ix_cal_lines = lambda: []                # never shell out to icalBuddy

from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:300]))


def req(path, body=None):
    h = {"Host": "127.0.0.1:%d" % PORT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def search(q):
    return req("/api/search?q=" + urllib.parse.quote(q))[1]


def ids(res):
    return [r.get("id") for r in (res or {}).get("results", [])]


try:
    sw = server.index_sweep("c58")
    ok("first sweep ok", sw.get("ok") is True, sw)

    print("\n=== C58: a deleted conversation leaves search at once ===")
    ok("the doomed chat is indexed", "chat:chat-2026-10-21-secret" in ids(search("wombatsecret")),
       search("wombatsecret"))
    c, d = req("/api/sessions/delete", {"session": "chat-2026-10-21-secret"})
    ok("delete answers ok", c == 200 and d.get("ok") is True, (c, d))
    ok("the file is gone", not os.path.exists(os.path.join(CH, "chat-2026-10-21-secret.json")))
    res = search("wombatsecret")
    ok("its transcript is NOT searchable right after the delete (no sweep, no drain)",
       res.get("ok") is True and ids(res) == [], res)
    con = server._ix_conn()
    try:
        n_items = con.execute("SELECT COUNT(*) FROM items WHERE id=?",
                              ("chat:chat-2026-10-21-secret",)).fetchone()[0]
        n_all = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        n_fts = con.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    finally:
        con.close()
    ok("its row is gone from index.db", n_items == 0, n_items)
    ok("items and items_fts stay in lockstep", n_all == n_fts, (n_all, n_fts))
    ok("other conversations are untouched", "chat:chat-2026-10-21-keeper" in ids(search("kumquatkeep")),
       search("kumquatkeep"))

    print("\n=== C58: a sweep holding the index lock does not stall the delete ===")
    chat("chat-2026-10-21-slowdel", "the slowpoke otterslow")
    server.index_sweep("c58-slow")
    ok("the slow-delete chat is indexed", "chat:chat-2026-10-21-slowdel" in ids(search("otterslow")))
    server._ix_lock.acquire()                    # stand-in for a sweep inside icalBuddy
    rel = threading.Timer(5.0, server._ix_lock.release)
    rel.start()
    t0 = time.time()
    c, d = req("/api/sessions/delete", {"session": "chat-2026-10-21-slowdel"})
    took = time.time() - t0
    ok("delete answers within ~1 s while the index lock is held", c == 200 and took < 3.0, (c, round(took, 2)))
    rel.join()
    deadline = time.time() + 10
    while time.time() < deadline and ids(search("otterslow")):
        time.sleep(0.2)
    ok("... and the row is pruned as soon as the lock frees", ids(search("otterslow")) == [], search("otterslow"))
    c, d = req("/api/sessions/delete", {"session": "chat-2026-10-21-nonesuch"})
    ok("deleting an unknown conversation still answers ok", c == 200 and d.get("ok") is True, (c, d))
    c, d = req("/api/sessions/delete", {"session": "../../etc"})
    ok("a bad id is ignored", c == 200 and "chat:chat-2026-10-21-keeper" in ids(search("kumquatkeep")))

    print("\n=== C58: a touch that read the chat before the delete cannot write it back ===")
    chat("chat-2026-10-21-racer", "the racing marmotrace")
    server.index_sweep("c58-race")
    ok("the racing chat is indexed", "chat:chat-2026-10-21-racer" in ids(search("marmotrace")))
    real_resolve = server._IX_RESOLVERS["chat"]
    resolved, go, first = threading.Event(), threading.Event(), [True]

    def paused(ident):                           # the 2 s drain thread, preempted
        it = real_resolve(ident)                 # right after it read the chat
        if first[0] and ident == "chat-2026-10-21-racer":
            first[0] = False
            resolved.set()
            go.wait(10)
        return it
    server._IX_RESOLVERS["chat"] = paused
    try:
        drain = threading.Thread(target=server._ix_apply_touch, args=("chat", "chat-2026-10-21-racer"))
        drain.start()
        resolved.wait(10)
        c, d = req("/api/sessions/delete", {"session": "chat-2026-10-21-racer"})
        go.set()
        drain.join(10)
    finally:
        server._IX_RESOLVERS["chat"] = real_resolve
    deadline = time.time() + 5
    while time.time() < deadline and ids(search("marmotrace")):
        time.sleep(0.2)                          # the delete's prune may still be waiting
    ok("the in-flight touch does not resurrect the deleted transcript",
       c == 200 and ids(search("marmotrace")) == [], (c, search("marmotrace")))

    print("\n=== C108: deleting index.db no longer kills search until a restart ===")
    db = server.IX_DB
    # leave a stale WAL behind the way a live WAL-mode db would
    con = server._ix_conn()
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("INSERT INTO ix_meta(k, v) VALUES('stale', 'x') ON CONFLICT(k) DO UPDATE SET v='x'")
    con.commit()
    os.remove(db)
    con.close()
    if not os.path.exists(db + "-wal"):
        open(db + "-wal", "wb").write(b"\x37\x7f\x06\x82" + b"\x00" * 28)   # a WAL header, no frames
    res = search("kumquatkeep")
    ok("search answers ok right after index.db was deleted", res.get("ok") is True and "error" not in res, res)
    c, st = req("/api/search/status")
    ok("status answers ok", st.get("ok") is True and "error" not in st, st)
    ok("the recreated db is 0600", (os.stat(db).st_mode & 0o777) == 0o600, oct(os.stat(db).st_mode & 0o777))
    deadline = time.time() + 15
    while time.time() < deadline and "chat:chat-2026-10-21-keeper" not in ids(search("kumquatkeep")):
        time.sleep(0.2)
    ok("a rebuild sweep repopulates the index without a restart",
       "chat:chat-2026-10-21-keeper" in ids(search("kumquatkeep")), search("kumquatkeep"))
    ok("the deleted conversation does not come back with it", ids(search("wombatsecret")) == [])
    sw = server.index_sweep("after-rebuild")
    while sw.get("error") == "sweep already running" and time.time() < deadline + 15:
        time.sleep(0.2)                          # the rebuild sweep is still finishing
        sw = server.index_sweep("after-rebuild")
    ok("a later sweep works", sw.get("ok") is True and "error" not in json.dumps(sw.get("stats", {}).get("chat")),
       sw)
    con = server._ix_conn()
    try:
        integ = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    ok("the rebuilt index passes integrity_check", integ == "ok", integ)

    print("\n=== C108: a sweep running at the deletion does not swallow the rebuild ===")
    server._ix_sweep_lock.acquire()              # stand-in for a sweep in progress
    try:
        os.remove(db)
        res = search("kumquatkeep")              # recreates the schema, queues the rebuild
        ok("search answers ok after a second deletion", res.get("ok") is True and "error" not in res, res)
        time.sleep(0.5)
        ok("the rebuild waits while another sweep holds the lock", ids(search("kumquatkeep")) == [])
    finally:
        server._ix_sweep_lock.release()
    deadline = time.time() + 15
    while time.time() < deadline and "chat:chat-2026-10-21-keeper" not in ids(search("kumquatkeep")):
        time.sleep(0.2)
    ok("... and runs once that sweep is done (not skipped as 'already running')",
       "chat:chat-2026-10-21-keeper" in ids(search("kumquatkeep")), search("kumquatkeep"))
except Exception as e:
    import traceback
    traceback.print_exc()
    ok("suite ran to the end", False, repr(e))
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
