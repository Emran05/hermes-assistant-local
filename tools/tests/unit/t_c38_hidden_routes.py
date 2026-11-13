#!/usr/bin/env python3
"""C38 follow-up (k02 review #5 + cross-review X-http-plumbing-4): the hidden
Needs-you drafts conversation stays hidden on EVERY route.

C38 put `needsyou` next to `__prewarm__` in server.py's HIDDEN_SESSIONS: its
bot turns are drafts written from a stranger's message, so it must never be
listed, read back, searched or indexed (the MCP chat tools proxy exactly those
routes, and would bypass messages_search:false).  /api/history, list_sessions,
/api/chat and aux_index honoured it, but aux_convos' export and meta routes
and aux_branch's validator still compared only against PREWARM_SESSION:

  * GET  /api/sessions/export?session=needsyou served the whole transcript;
  * POST /api/sessions/meta renamed / pinned it;
  * POST /api/sessions/branch copied it into an ordinary chat-YYYY-... file,
    which the sidebar, the FTS index and /api/sessions/search then served —
    exactly what MCP chats_search / hermes_search proxy.

Also hermes_mcp's chat_get pre-check now refuses the id itself instead of
relying on /api/history alone.

Real server.py import (aux chain included) + the real Handler on a free
loopback port, throwaway HOME; the real hermes_mcp.py with dash_get stubbed.
No model, no ~/.hermes, no network.

Fails on the old code: export answers 200 with the draft text, meta answers
200, branch answers ok and the copy is listed, indexed and chat-searchable,
and chat_get('needsyou') reaches the dashboard.
"""
import contextlib, io, json, os, shutil, socket, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-c38h-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time()
DRAFT = {"title": "Needs you — drafts", "messages": [
    {"role": "user", "text": "Draft a reply to the message from Dana.", "ts": NOW},
    {"role": "bot", "text": "Hi Dana, thanks for telling me about the biopsy "
                            "results on Friday. Thinking of you.", "ts": NOW + 1}]}
json.dump(DRAFT, open(os.path.join(CH, "needsyou.json"), "w"))
NORMAL = "chat-2026-10-26-normal"
json.dump({"title": "garden", "messages": [
    {"role": "user", "text": "plant the kumquat tree", "ts": NOW},
    {"role": "bot", "text": "done, kumquat noted", "ts": NOW + 1}]},
    open(os.path.join(CH, NORMAL + ".json"), "w"))

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


def req(path, body=None, raw=False):
    h = {"Host": "127.0.0.1:%d" % PORT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            txt = resp.read().decode()
            return resp.status, (txt if raw else json.loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def files():
    return sorted(f for f in os.listdir(CH) if f.endswith(".json"))


try:
    server.index_sweep("c38h")
    before = files()
    draft_bytes = open(os.path.join(CH, "needsyou.json"), "rb").read()

    print("\n=== export / meta refuse the drafts conversation ===")
    c, d = req("/api/sessions/export?session=needsyou", raw=True)
    ok("GET /api/sessions/export?session=needsyou -> 400",
       c == 400 and "biopsy" not in json.dumps(d), (c, d))
    c, d = req("/api/sessions/meta", {"session": "needsyou", "title": "mine now"})
    ok("POST /api/sessions/meta rename -> 400", c == 400 and d.get("ok") is False, (c, d))
    c, d = req("/api/sessions/meta", {"session": "needsyou", "pinned": True})
    ok("POST /api/sessions/meta pin -> 400", c == 400 and d.get("ok") is False, (c, d))
    ok("the drafts file is byte-identical",
       open(os.path.join(CH, "needsyou.json"), "rb").read() == draft_bytes)
    c, d = req("/api/sessions/export?session=__prewarm__", raw=True)
    ok("prewarm is still refused by export", c == 400, (c, d))

    print("\n=== branch / tree refuse it ===")
    c, d = req("/api/sessions/branch", {"session": "needsyou", "at_index": 2})
    ok("POST /api/sessions/branch {session:'needsyou'} is refused",
       c == 400 and d.get("ok") is False, (c, d))
    ok("no new conversation file was written", files() == before, files())
    c, d = req("/api/sessions/tree?session=needsyou")
    ok("GET /api/sessions/tree?session=needsyou is refused", c == 400, (c, d))
    server.index_sweep("c38h-after")
    c, d = req("/api/sessions")
    listed = [s.get("id") or s.get("session") for s in (d if isinstance(d, list) else d.get("sessions", []))]
    ok("nothing drafts-shaped is listed", all(x in (NORMAL,) for x in listed), listed)
    c, d = req("/api/search?q=biopsy")
    ok("the draft text is not in the unified index", c == 200 and d.get("results") == [], d)
    c, d = req("/api/sessions/search?q=biopsy")
    ok("the draft text is not chat-searchable",
       c == 200 and "biopsy" not in json.dumps(d), d)

    print("\n=== an ordinary conversation is unaffected ===")
    c, d = req("/api/sessions/export?session=" + NORMAL, raw=True)
    ok("export of a normal chat still works", c == 200 and "kumquat" in d, (c, str(d)[:200]))
    c, d = req("/api/sessions/meta", {"session": NORMAL, "title": "garden plan"})
    ok("rename of a normal chat still works", c == 200 and d.get("title") == "garden plan", (c, d))
    c, d = req("/api/sessions/branch", {"session": NORMAL, "at_index": 2})
    ok("branch of a normal chat still works", c == 200 and d.get("ok") is True, (c, d))
    c, d = req("/api/sessions/tree?session=" + NORMAL)
    ok("tree of a normal chat still works", c == 200 and d.get("ok") is True, (c, d))

    print("\n=== MCP chat_get refuses the id before any request ===")
    allow = os.path.join(HOME, "mcp-allow.json")
    json.dump({"tools": {}}, open(allow, "w"))
    os.environ["HERMES_MCP_ALLOW"] = allow
    os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"
    with contextlib.redirect_stderr(io.StringIO()):
        import hermes_mcp as M                   # noqa: E402
    calls = []
    M.dash_get = lambda path, params=None, **kw: (calls.append((path, params)) or
                                                  {"messages": DRAFT["messages"], "title": ""})
    for sid in ("needsyou", "__prewarm__"):
        try:
            out = M.t_chat_get({"session": sid})
            ok("chat_get(%r) is refused" % sid, False, out)
        except M.ToolError:
            ok("chat_get(%r) is refused" % sid, True)
    ok("... without asking the dashboard", calls == [], calls)
    out = M.t_chat_get({"session": NORMAL})
    ok("chat_get of a normal id still goes through", calls and calls[-1][0] == "/api/history", calls)
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
