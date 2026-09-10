"""Backlog #13 harness — /api/sessions/{search,meta,export} + list_sessions().

Runs the REAL server.py Handler (guard, dispatch, aux exec) against a
throwaway HOME whose chats/ holds copies of two real conversations plus
crafted fixtures.  Never touches ~/.hermes and never wakes the model:
server.main() (which starts the briefing / idle / memory threads) is never
called, only the HTTP server is.
"""
import json, os, shutil, sys, tempfile, threading, time, urllib.request, urllib.error

SP   = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
# The owner's REAL home, captured before HOME is pointed at the throwaway.
REAL_HOME = os.path.expanduser("~")
HOME = tempfile.mkdtemp(prefix="hermes-convos-")
PORT = "7799"

# ---- throwaway HOME --------------------------------------------------------
shutil.rmtree(HOME, ignore_errors=True)
CH = os.path.join(HOME, ".hermes", "dashboard", "chats")
os.makedirs(CH, exist_ok=True)

# REAL_HOME, not expanduser(): this line moves the day someone reorders the
# file and the HOME redirect below lands first.
REAL = os.path.join(REAL_HOME, ".hermes", "dashboard", "chats")
copied = []
for fn in ("chat-2026-08-31-qglb4.json", "autoroute-test-1787079206.json"):
    src = os.path.join(REAL, fn)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(CH, fn)); copied.append(fn)

def wr(sid, obj, mtime=None):
    p = os.path.join(CH, sid + ".json")
    json.dump(obj, open(p, "w"), indent=1)
    if mtime: os.utime(p, (mtime, mtime))

NOW = time.time()
wr("chat-2026-09-01-inject", {"title": "", "messages": [
    {"role": "user", "text": "how do I escape <img src=x onerror=alert(1)> in the widget?", "ts": NOW - 900},
    {"role": "bot", "text": "Use esc() — it turns & < > \" into entities. The widget grid never sees raw HTML.", "ts": NOW - 890, "err": False},
]}, NOW - 890)

wr("chat-2026-09-02-secrets", {"title": "Token plumbing",
    "serve_sid": "sid-abc123", "serve_key": "key-should-never-export",
    "messages": [
    {"role": "user", "text": "where does the serve token live?", "ts": NOW - 500},
    {"role": "bot", "text": "It is at ~/.hermes/dashboard/serve-token (0600).\n"
        "Read it with: cat /Users/you/.hermes/dashboard/serve-token\n"
        "Then send Authorization: Bearer hx_9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c\n"
        "or a raw Bearer hx_standalone01234567 header\n"
        "config: serve_key = 8f2c1d9e0a7b6c5d4e3f2a1b\n"
        "```python\nprint('fenced code survives')\n```", "ts": NOW - 480, "err": False},
    {"role": "bot", "text": "VERDICT — keep the token out of the transcript.", "ts": NOW - 470,
     "deep": {"model": "sonnet", "ms": 21312, "reason": "score 3.0", "depth": "quick"}},
    {"role": "tool", "text": "terminal: cat serve-token", "ts": NOW - 475, "tool": "terminal"},
    {"role": "bot", "text": "approval please", "ts": NOW - 474, "approval": {"cmd": "rm -rf"}},
]}, NOW - 470)

# 1.1.5: raw, UNLABELED secrets — no "token:" in front of any of them, so the
# only thing that can catch these is the new _cv_redact_raw pass. Every value
# is a fake with the right SHAPE (vendor prefix + length), plus two negatives
# that must survive untouched.
RAW = {
    "openai":    "sk-Tr0ub4dor3AbCdEfGhIjKlMnOpQrSt",
    "anthropic": "sk-ant-api03-A1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "ghp":       "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "gh_pat":    "github_pat_11ABCDEFG0abcdefghijkl_1a2b3c4d5e6f7g8h9i0j",
    "aws":       "AKIAIOSFODNN7EXAMPLE",
    "slack":     "xoxb-1234567890-1234567890-AbCdEfGhIjKlMnOpQrSt",
    "google":    "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7",
    "jwt":       "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3O"
                 "DkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "telegram":  "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX",
}
RAW_PEM = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
           "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdz\n"
           "cnNhAAAAAwEAAQAAAYEAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
           "-----END OPENSSH PRIVATE KEY-----")
RAW_TG_URL = ("https://api.telegram.org/bot123456789:"
              "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX/sendMessage")
NEG = {
    "sentence": "The quick brown fox jumped over a dog!!!",   # exactly 40 chars
    "gitsha":   "9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a",   # 40 hex, a real sha
}
wr("chat-2026-09-03-rawsecrets", {"title": "Raw shapes", "messages": [
    {"role": "user", "text": "paste of a scratch file, no labels anywhere",
     "ts": NOW - 300},
    {"role": "bot", "text": "\n".join(
        ["%s %s" % (k, v) for k, v in sorted(RAW.items())] +
        [RAW_TG_URL, RAW_PEM] +
        ["not a secret: " + NEG["sentence"], "commit " + NEG["gitsha"]]),
     "ts": NOW - 290, "err": False},
]}, NOW - 290)

wr("chat-2026-08-01-old", {"title": "Old pinned thing", "pinned": True, "messages": [
    {"role": "user", "text": "remind me about the WIDGET registry", "ts": NOW - 9e5},
    {"role": "bot", "text": "The widget registry lives in server.py; a widget is {title,icon,size,cat,provider}.", "ts": NOW - 9e5 + 5, "err": False},
]}, NOW - 890000)

wr("__prewarm__", {"title": "__prewarm__", "messages": [
    {"role": "user", "text": "Reply with exactly: ok — widget", "ts": NOW},
    {"role": "bot", "text": "ok", "ts": NOW},
]}, NOW)

wr("chat-empty", {"title": "", "messages": []}, NOW)

# ---- boot the real Handler -------------------------------------------------
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = PORT
sys.path.insert(0, REPO)
import server                                    # noqa: E402  (after env)
assert server.HOME == HOME, server.HOME
from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", int(PORT)), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % PORT

def req(path, body=None, headers=None):
    h = {"Host": "127.0.0.1:%s" % PORT}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return f.status, dict(f.headers), f.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")

FAIL = []
TOTAL = [0]
def check(name, cond, detail=""):
    TOTAL[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + str(detail)[:220]) if (detail and not cond) else ""))
    if not cond: FAIL.append(name)

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (TOTAL[0] - len(FAIL), len(FAIL))))

print("copied real chats:", copied)

# ================= 1. search =================
print("\n--- GET /api/sessions/search")
st, hd, bd = req("/api/sessions/search?q=widget")
res = json.loads(bd)
ids = [r["session"] for r in res]
check("200 + JSON list", st == 200 and isinstance(res, list), (st, bd[:200]))
check("case-insensitive hit ('widget' matches 'WIDGET')", "chat-2026-08-01-old" in ids, ids)
check("hits both fixtures", "chat-2026-09-01-inject" in ids, ids)
check("prewarm session never listed", "__prewarm__" not in ids, ids)
check("empty-message chat skipped", "chat-empty" not in ids, ids)
check("newest first", res[0]["ts"] >= res[-1]["ts"], [r["ts"] for r in res])
r0 = [r for r in res if r["session"] == "chat-2026-09-01-inject"][0]
check("snippet is plain text (no markup emitted)", "<mark" not in r0["snippet"] and "<span" not in r0["snippet"], r0["snippet"])
check("snippet keeps raw chat text unescaped for the client to escape",
      "<img src=x onerror=alert(1)>" in r0["snippet"], r0["snippet"])
mk = r0["snippet"][r0["mark_start"]:r0["mark_start"] + r0["mark_len"]]
check("mark offsets point at the match", mk.lower() == "widget", (mk, r0["snippet"]))
check("snippet <= 2*60 + q + ellipses", len(r0["snippet"]) <= 60 * 2 + len("widget") + 4, len(r0["snippet"]))
check("keys are exactly the contract", set(r0) == {"session", "title", "ts", "snippet", "mark_start", "mark_len", "hits"}, sorted(r0))

st, hd, bd = req("/api/sessions/search?q=" + urllib.parse.quote("the"))
res2 = json.loads(bd)
check("multi-hit counts every occurrence", any(r["hits"] > 1 for r in res2), [(r["session"], r["hits"]) for r in res2])
mk2 = [r for r in res2 if r["mark_len"]][0]
check("mark valid on every result", all(
    r["snippet"][r["mark_start"]:r["mark_start"] + r["mark_len"]].lower() == "the" for r in res2),
    [(r["session"], r["snippet"][r["mark_start"]:r["mark_start"] + r["mark_len"]]) for r in res2])

st, hd, bd = req("/api/sessions/search?q=")
check("empty q -> []", st == 200 and json.loads(bd) == [], bd[:120])
st, hd, bd = req("/api/sessions/search?q=" + urllib.parse.quote("zzz-no-such-string"))
check("no match -> []", json.loads(bd) == [], bd[:120])
st, hd, bd = req("/api/sessions/search?q=" + urllib.parse.quote("serve token"))
check("multi-word substring works", any(r["session"] == "chat-2026-09-02-secrets" for r in json.loads(bd)), bd[:200])
st, hd, bd = req("/api/sessions/search?q=" + urllib.parse.quote("terminal: cat"))
check("tool/approval metadata rows are not searched", json.loads(bd) == [], bd[:200])

# ================= 2. meta =================
print("\n--- POST /api/sessions/meta")
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-09-01-inject", "pinned": True})
check("pin ok", st == 200 and json.loads(bd)["pinned"] is True, (st, bd[:160]))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-09-01-inject",
                                        "title": "  Escaping\x07 wid\x00gets   in  the grid  "})
d = json.loads(bd)
check("control chars stripped + whitespace collapsed", d["title"] == "Escaping wid gets in the grid", repr(d["title"]))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-09-01-inject", "title": "x" * 200})
check("title capped at 80", len(json.loads(bd)["title"]) == 80, len(json.loads(bd)["title"]))
st, hd, bd = req("/api/sessions/meta", {"session": "../../etc/passwd", "pinned": True})
check("path traversal refused (400)", st == 400, (st, bd[:120]))
st, hd, bd = req("/api/sessions/meta", {"session": "__prewarm__", "pinned": True})
check("prewarm refused (400)", st == 400, (st, bd[:120]))
check("prewarm file untouched", "pinned" not in json.load(open(os.path.join(CH, "__prewarm__.json"))))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-does-not-exist", "pinned": True})
check("unknown session -> 404 (the '(new conversation)' row cannot be pinned)", st == 404, (st, bd[:120]))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-09-01-inject"})
check("no-op body refused", st == 400, (st, bd[:120]))
saved = json.load(open(os.path.join(CH, "chat-2026-09-01-inject.json")))
check("messages preserved through meta writes", len(saved["messages"]) == 2, list(saved))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-09-02-secrets", "title": ""})
check("empty title clears the rename", json.loads(bd)["title"] == "", bd[:120])

# ---- list_sessions() through /api/sessions
st, hd, bd = req("/api/sessions")
sess = json.loads(bd)["sessions"]
check("/api/sessions carries pinned", all("pinned" in s for s in sess), sess[:2])
check("pinned rows sort first", [s["pinned"] for s in sess][:2] == [True, True], [(s["id"], s["pinned"]) for s in sess])
check("custom title wins", any(s["id"] == "chat-2026-09-01-inject" and s["title"].startswith("xxxx") for s in sess),
      [(s["id"], s["title"][:24]) for s in sess])
check("prewarm never listed", "__prewarm__" not in [s["id"] for s in sess], [s["id"] for s in sess])
check("cleared title falls back to the excerpt", any(
    s["id"] == "chat-2026-09-02-secrets" and s["title"].startswith("where does") for s in sess),
    [(s["id"], s["title"][:24]) for s in sess])

# ================= 3. export =================
print("\n--- GET /api/sessions/export")
st, hd, md = req("/api/sessions/export?session=chat-2026-09-02-secrets")
check("200", st == 200, (st, md[:160]))
check("Content-Type text/markdown", hd.get("Content-Type", "").startswith("text/markdown"), hd.get("Content-Type"))
cd = hd.get("Content-Disposition", "")
check("Content-Disposition attachment + slug + date", cd.startswith('attachment; filename="hermes-') and cd.endswith('.md"'), cd)
import re as _re
check("filename slug is ascii-safe and dated",
      bool(_re.fullmatch(r'attachment; filename="hermes-[a-z0-9-]+-\d{4}-\d{2}-\d{2}\.md"', cd)), cd)
check("title line first", md.startswith("# "), md[:60])
check("date line present", bool(_re.search(r"\n\n[A-Z][a-z]+ \d{1,2}, \d{4}\n", md)), md[:160])
check("**You** block", "**You**" in md, md[:200])
check("**Hermes** block", "**Hermes**" in md, md[:200])
check("Claude deep answer quoted + labelled", "> **Claude (sonnet)**" in md and "> VERDICT" in md, md[-400:])
check("fenced code preserved", "```python\nprint('fenced code survives')\n```" in md, md[-600:])
check("tool row omitted", "terminal: cat serve-token" not in md, md)
check("approval row omitted", "approval please" not in md, md)
check("no serve_key value", "key-should-never-export" not in md and "8f2c1d9e0a7b6c5d4e3f2a1b" not in md, md)
check("no serve_sid value", "sid-abc123" not in md, md)
check("bearer token redacted", "hx_9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c" not in md
      and "Authorization: [redacted]" in md, md)
check("standalone Bearer redacted once", "hx_standalone01234567" not in md
      and "Bearer [redacted]" in md and "[redacted] [redacted]" not in md, md)
check("~/.hermes path redacted", "~/.hermes/dashboard/serve-token" not in md
      and "/Users/you/.hermes" not in md and "[redacted path]" in md, md)
st, hd, bd = req("/api/sessions/export?session=__prewarm__")
check("prewarm export refused", st == 400, (st, bd[:120]))
st, hd, bd = req("/api/sessions/export?session=" + urllib.parse.quote("../../../etc/passwd"))
check("traversal export refused", st in (400, 404), (st, bd[:120]))
st, hd, bd = req("/api/sessions/export?session=chat-nope")
check("unknown export -> 404", st == 404, (st, bd[:120]))
st, hd, md2 = req("/api/sessions/export?session=chat-2026-08-01-old")
check("real chat exports", st == 200 and "**You**" in md2 and "**Hermes**" in md2, md2[:200])

# ================= 4. same-origin guard still applies =================
print("\n--- guard")
st, hd, bd = req("/api/sessions/search?q=widget", headers={"Origin": "http://evil.example"})
check("cross-origin search refused", st == 403, (st, bd[:120]))
st, hd, bd = req("/api/sessions/meta", {"session": "chat-2026-08-01-old", "pinned": False},
                 headers={"Sec-Fetch-Site": "cross-site"})
check("cross-site meta POST refused", st == 403, (st, bd[:120]))
st, hd, bd = req("/api/sessions/export?session=chat-2026-08-01-old", headers={"Host": "evil.example"})
check("rebound-host export refused", st == 403, (st, bd[:120]))

# ================= 5. raw secret redaction, both copies (1.1.5 fix 6) ======
print("\n--- raw (unlabeled) secret redaction")
import hermes_mcp as MCP                              # noqa: E402
assert MCP.ALLOW_PATH.startswith(HOME), MCP.ALLOW_PATH   # never the real one

# Both scrubbers, driven by the SAME table: aux_convos' export path and the
# MCP server's _redact must agree shape for shape or the "mirrored" claim is
# a comment, not a fact.
for label, fn in (("aux_convos._cv_redact", server._cv_redact),
                  ("hermes_mcp._redact", MCP._redact)):
    for k, v in sorted(RAW.items()):
        out = fn("look at this: " + v + " ok?")
        check("%s redacts %s" % (label, k), v not in out and "[redacted]" in out, out)
    out = fn("head\n" + RAW_PEM + "\ntail")
    check("%s redacts a PEM private key block" % label,
          "BEGIN OPENSSH PRIVATE KEY" not in out and "b3BlbnNzaC" not in out
          and "[redacted]" in out, out.replace("\n", "\\n")[:160])
    out = fn(RAW_TG_URL)
    check("%s redacts a telegram token inside the bot URL" % label,
          "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX" not in out, out)
    # negatives — a redactor that eats prose gets switched off
    for k, v in sorted(NEG.items()):
        check("%s leaves %s alone" % (label, k), fn(v) == v, fn(v))
    check("%s leaves ordinary prose alone" % label,
          fn("Deploy 1.1.5 to the dashboard on Sept 3, 2026 at 14:30.") ==
          "Deploy 1.1.5 to the dashboard on Sept 3, 2026 at 14:30.")
    # the pre-existing rules must still work after the new pass was chained on
    check("%s still redacts a labeled value" % label,
          "abc123def456" not in fn("serve_key=abc123def456"), fn("serve_key=abc123def456"))
    check("%s still redacts a bare Bearer" % label,
          fn("Bearer hx_0123456789abcdef") == "Bearer [redacted]",
          fn("Bearer hx_0123456789abcdef"))
    check("%s still redacts a ~/.hermes path" % label,
          fn("~/.hermes/dashboard/serve-token") == "[redacted path]",
          fn("~/.hermes/dashboard/serve-token"))

# the two copies are the same bytes, not just the same behaviour today
def _block(path):
    keep, out = False, []
    for line in open(path):
        if "BEGIN MIRRORED SECRET BLOCK" in line: keep = True
        if keep: out.append(line)
        if "END MIRRORED SECRET BLOCK" in line and keep: break
    return "".join(out)
_b1 = _block(os.path.join(REPO, "aux_convos.py"))
_b2 = _block(os.path.join(REPO, "hermes_mcp.py"))
check("mirrored block found in both files", len(_b1) > 500 and len(_b2) > 500, (len(_b1), len(_b2)))
check("mirrored block is BYTE-identical", _b1 == _b2,
      "aux_convos=%d bytes hermes_mcp=%d bytes" % (len(_b1), len(_b2)))

# and end-to-end, through the real export route
st, hd, rawmd = req("/api/sessions/export?session=chat-2026-09-03-rawsecrets")
check("raw-secret chat exports", st == 200, (st, rawmd[:120]))
for k, v in sorted(RAW.items()):
    check("export: %s never leaves the process" % k, v not in rawmd, k)
check("export: PEM body gone", "b3BlbnNzaC" not in rawmd, rawmd[:200])
check("export: telegram bot URL token gone",
      "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawX" not in rawmd, rawmd[:200])
check("export: the 40-char sentence survives", NEG["sentence"] in rawmd, rawmd[-300:])
check("export: the git sha survives", NEG["gitsha"] in rawmd, rawmd[-300:])

# ================= 6. hermes_mcp allowlist mode (1.1.5 fix 8) ==============
print("\n--- load_allow() re-locks the allowlist to 0600")
_alt = os.path.join(HOME, ".hermes", "loose-allow.json")
json.dump({"tools": {"notes_search": True, "messages_search": True},
           "max_results": 5}, open(_alt, "w"))
os.chmod(_alt, 0o644)
check("pre-condition: file really is 0644",
      oct(os.stat(_alt).st_mode & 0o777) == "0o644", oct(os.stat(_alt).st_mode & 0o777))
_saved_path = MCP.ALLOW_PATH
MCP.ALLOW_PATH = _alt
try:
    _a = MCP.load_allow()
finally:
    MCP.ALLOW_PATH = _saved_path
check("0644 -> 0600 after a successful read",
      oct(os.stat(_alt).st_mode & 0o777) == "0o600", oct(os.stat(_alt).st_mode & 0o777))
check("the file's contents are still honoured", _a["max_results"] == 5, _a)
check("...including a tool the owner turned on", _a["tools"]["messages_search"] is True, _a)
# idempotent: a second load on an already-0600 file changes nothing
MCP.ALLOW_PATH = _alt
try:
    MCP.load_allow()
finally:
    MCP.ALLOW_PATH = _saved_path
check("second load leaves it 0600",
      oct(os.stat(_alt).st_mode & 0o777) == "0o600", oct(os.stat(_alt).st_mode & 0o777))

# ================= 7. disk guard fails open, loudly (1.1.5 fix 4) ==========
print("\n--- _disk_free_gb / _dir_size_gb say when they stop guarding")
import io as _io, contextlib as _ctx                    # noqa: E402
server._DISK_GUARD_WARNED.clear()
_buf = _io.StringIO()
with _ctx.redirect_stderr(_buf):
    _v1 = server._disk_free_gb("/no/such/volume/hermes-harness")
    _v2 = server._disk_free_gb("/no/such/volume/hermes-harness")
check("still fails open (None, never a refusal on a guess)",
      _v1 is None and _v2 is None, (_v1, _v2))
_lines = [l for l in _buf.getvalue().splitlines() if "disk guard" in l]
check("exactly ONE line per process, not one per poll", len(_lines) == 1, _lines)
check("the line names the helper", "_disk_free_gb" in _lines[0], _lines[0])
check("the line says the guard is failing open", "FAILING OPEN" in _lines[0], _lines[0])
check("the line carries the errno message", "No such file" in _lines[0], _lines[0])
check("_disk_short still answers False on an unknown free number",
      server._disk_short(19.0, None) is False)
server._DISK_GUARD_WARNED.clear()
_buf2 = _io.StringIO()
with _ctx.redirect_stderr(_buf2):
    server._disk_guard_warn("_dir_size_gb", OSError(13, "Permission denied"))
    server._disk_guard_warn("_dir_size_gb", OSError(13, "Permission denied"))
check("_dir_size_gb is rate-limited the same way",
      _buf2.getvalue().count("_dir_size_gb") == 1, _buf2.getvalue())
check("a real free-space read still works",
      isinstance(server._disk_free_gb(), float), server._disk_free_gb())
server._DISK_GUARD_WARNED.clear()

print("\n==== %d checks, %d FAILED %s" % (TOTAL[0], len(FAIL), FAIL or ""))
_sample = os.path.join(tempfile.mkdtemp(prefix="hermes-convos-out-"),
                       "convos-export-sample.md")
open(_sample, "w").write(md)
print("export sample written to " + _sample)
srv.shutdown()
sys.exit(1 if FAIL else 0)
