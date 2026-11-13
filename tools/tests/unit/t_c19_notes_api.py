"""C19 + B05, the server half — POST/GET /api/notes through the REAL Handler.

C19: a note over 8,000 chars was cut to 8,000 under {ok:true}; an empty or
cut-off body parsed as {} and wiped the note. B05: any editor's save won, so a
hub card holding an older copy overwrote an edit made in the pop-out.

Contract checked here: GET -> {text, rev, limit}; POST {text, base_rev} ->
200 {ok, rev} | 400 (text missing / not a string / bad base_rev) |
413 {ok:false, error:"too_long", limit} | 409 {ok:false, conflict:true, text,
rev}; aux_index's re-registered route and server.py's inline one answer
identically (both call notes_post); a refused save changes nothing on disk.

Throwaway HOME, real server.py import (aux chain included), a loopback port
the offline guard does not block. No model, no ~/.hermes write.
"""
import json, os, shutil, socket, sys, tempfile, threading, urllib.error, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-c19-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(os.path.join(DASH, "chats"), exist_ok=True)
# watchtower fully off so no module-load loop can do anything
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))

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


def req(path, body=None, raw=None, headers=None):
    h = {"Host": "127.0.0.1:%d" % PORT}
    h.update(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h,
                               method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt
    except Exception as e:                       # a dropped connection
        return None, repr(e)


def stored():
    try:
        return json.load(open(server.NOTES_FILE))
    except (OSError, ValueError):
        return None


try:
    LIMIT = getattr(server, "NOTES_MAX_CHARS", 8000)
    print("\n=== GET ===")
    c, g = req("/api/notes")
    ok("GET /api/notes answers {text, rev, limit}", c == 200 and isinstance(g, dict)
       and g.get("text") == "" and g.get("rev") == 0 and isinstance(g.get("limit"), int), (c, g))

    print("\n=== C19: no silent truncation ===")
    long_ = "word " * 2400                       # 12,000 chars, the audit's case
    c, r = req("/api/notes", {"text": long_, "base_rev": 0})
    ok("a 12,000-char note is accepted", c == 200 and r.get("ok") is True, (c, r))
    ok("... and stored WHOLE, not cut to 8,000", (stored() or {}).get("text") == long_,
       len((stored() or {}).get("text") or ""))
    rev = (r or {}).get("rev") if isinstance(r, dict) else None
    ok("the save returns the new rev", rev == 1, r)
    ok("the limit is well above 8,000 and under the index's 200,000-char body cap",
       8000 < LIMIT <= 200000, LIMIT)
    over = "z" * (LIMIT + 1)
    c, r = req("/api/notes", {"text": over, "base_rev": rev})
    ok("over the limit -> 413 too_long with the limit", c == 413 and r.get("ok") is False
       and r.get("error") == "too_long" and r.get("limit") == LIMIT, (c, str(r)[:200]))
    ok("... and the stored note is untouched", (stored() or {}).get("text") == long_)
    exact = "q" * LIMIT
    c, r = req("/api/notes", {"text": exact, "base_rev": rev})
    ok("exactly at the limit is accepted", c == 200 and r.get("ok") is True, (c, str(r)[:200]))
    ok("... and stored whole", (stored() or {}).get("text") == exact)
    rev = r.get("rev")
    c, r = req("/api/notes", {"text": long_, "base_rev": rev})
    rev = r.get("rev")

    print("\n=== C19: empty / cut-off / malformed bodies never wipe the note ===")
    for label, raw in (("empty JSON object", b"{}"),
                       ("no body at all", b""),
                       ("cut-off JSON", b'{"text": "half a no'),
                       ("text is a number", b'{"text": 5}'),
                       ("text is null", b'{"text": null}'),
                       ("a JSON list", b'["text"]')):
        c, r = req("/api/notes", raw=raw)
        ok("%s -> 400" % label, c == 400 and isinstance(r, dict) and r.get("ok") is False, (c, r))
        ok("%s leaves the note intact" % label, (stored() or {}).get("text") == long_,
           len((stored() or {}).get("text") or ""))
    c, r = req("/api/notes", {"text": "x", "base_rev": "3"})
    ok("a non-integer base_rev -> 400", c == 400, (c, r))
    c, r = req("/api/notes", {"text": "x", "base_rev": True})
    ok("a boolean base_rev -> 400", c == 400, (c, r))
    ok("... note still intact", (stored() or {}).get("text") == long_)

    print("\n=== B05: a stale editor is refused ===")
    c, r = req("/api/notes", {"text": "base", "base_rev": rev})
    base_rev = r.get("rev")
    ok("hub + pop-out both loaded rev %s" % base_rev, c == 200 and isinstance(base_rev, int), (c, r))
    c, r = req("/api/notes", {"text": "base + popout paragraph", "base_rev": base_rev})
    ok("pop-out saves first", c == 200 and r.get("rev") == base_rev + 1, (c, r))
    c, r = req("/api/notes", {"text": "base!", "base_rev": base_rev})
    ok("the hub card's stale save -> 409 conflict", c == 409 and r.get("conflict") is True
       and r.get("ok") is False, (c, r))
    ok("409 carries the current text + rev", r.get("text") == "base + popout paragraph"
       and r.get("rev") == base_rev + 1, r)
    ok("the pop-out's paragraph survives", (stored() or {}).get("text") == "base + popout paragraph")
    c, r = req("/api/notes", {"text": "base + popout paragraph", "base_rev": base_rev})
    ok("a stale save of IDENTICAL text is not a conflict", c == 200 and r.get("ok") is True, (c, r))
    c, r = req("/api/notes", {"text": "base + popout paragraph!", "base_rev": base_rev + 1})
    ok("saving against the current rev succeeds", c == 200 and r.get("rev") == base_rev + 2, (c, r))
    c, r = req("/api/notes", {"text": "legacy page"})
    ok("a save with NO base_rev (a not-yet-reloaded window) is still accepted",
       c == 200 and r.get("ok") is True, (c, r))
    c, g = req("/api/notes")
    ok("GET reflects text + rev", g.get("text") == "legacy page" and g.get("rev") == base_rev + 3, g)
    hub = server.w_notes()
    ok("the hub provider carries rev + limit", hub.get("rev") == g.get("rev")
       and hub.get("limit") == LIMIT, hub)

    print("\n=== both routes are the same code ===")
    aux = server.POST_ROUTES.get("/api/notes")
    ok("aux_index still owns POST /api/notes", aux is not None)
    inline_saved = server.POST_ROUTES.pop("/api/notes")
    try:
        c1, r1 = req("/api/notes", raw=b"{}")
        c2, r2 = req("/api/notes", {"text": "z" * (LIMIT + 1)})
        c3, r3 = req("/api/notes", {"text": "inline", "base_rev": 0})
    finally:
        server.POST_ROUTES["/api/notes"] = inline_saved
    ok("inline route: empty body -> 400", c1 == 400, (c1, r1))
    ok("inline route: too long -> 413", c2 == 413 and isinstance(r2, dict) and r2.get("error") == "too_long", (c2, str(r2)[:120]))
    ok("inline route: stale base_rev -> 409", c3 == 409 and r3.get("conflict") is True, (c3, r3))
    ok("aux route answers the same as the inline one", [
        req("/api/notes", raw=b"{}")[0], req("/api/notes", {"text": "z" * (LIMIT + 1)})[0],
        req("/api/notes", {"text": "inline", "base_rev": 0})[0]] == [c1, c2, c3])

    print("\n=== a successful save still re-indexes the note ===")
    c, g = req("/api/notes")
    c, r = req("/api/notes", {"text": "quokka marmalade plan", "base_rev": g["rev"]})
    server._ix_drain_once()
    c, s = req("/api/search?q=quokka")
    ok("the note is searchable after one drain", any(x.get("source") == "note" for x in s.get("results", [])), s)
    c, r = req("/api/notes", {"text": "pelican", "base_rev": 0})
    server._ix_drain_once()
    c, s = req("/api/search?q=pelican")
    ok("a refused save indexes nothing", s.get("results") == [], s)
    ok("notes.json stays 0600", (os.stat(server.NOTES_FILE).st_mode & 0o777) == 0o600,
       oct(os.stat(server.NOTES_FILE).st_mode & 0o777))
except Exception as e:                           # a crash is a failure, not a pass
    import traceback
    traceback.print_exc()
    ok("suite ran to the end", False, repr(e))
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
