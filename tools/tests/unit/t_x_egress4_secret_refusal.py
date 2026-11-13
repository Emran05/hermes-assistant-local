#!/usr/bin/env python3
"""X-prompts-egress-4 — the credential REFUSAL test no longer rejects ordinary
owner text, while the output REDACTOR stays exactly as broad as it was.

aux_convos' _CV_SECRET_RE matches any credential word, `:` or `=`, then six
non-space characters.  That is right for redacting MCP/export output (it only
mangles code like `token = tokenizer.encode(text)`), and the mirrored block
stays byte-identical.  But the same regex was also the REFUSAL predicate for:
  * aux_memlayer._ml_looks_secret — POST /api/memory/facts and the import;
  * hermes_mcp._looks_secret     — before memory_facts(add=…);
  * aux_foryou._fy_secret_filter — which calls _ml_looks_secret by name and
    withholds a flagged You-Model entry from the profile.
So "Rotate the GitHub token: monthly" could not be remembered, and "Prefers
password: manager over notes" vanished from For-You.  A labeled match now
counts only when its VALUE looks like a credential (a digit, a symbol, an auth
scheme word, or 16+ characters that are not a dotted identifier).

Real server.py import (aux chain: aux_convos, aux_memlayer, aux_foryou) + the
real Handler on a free loopback port, throwaway HOME; the real hermes_mcp.py.
No model, no ~/.hermes, no network.

Fails on the old code: every benign sentence below is refused / withheld.
"""
import contextlib, io, json, os, re, shutil, socket, sys, tempfile, threading, urllib.error, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-xe4-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(os.path.join(DASH, "chats"), exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
server._ix_cal_lines = lambda: []
from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

ALLOW = os.path.join(HOME, "mcp-allow.json")
json.dump({"tools": {}}, open(ALLOW, "w"))
os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M                       # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:300]))


def post(path, body):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                               data=json.dumps(body).encode(),
                               headers={"Host": "127.0.0.1:%d" % PORT,
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


BENIGN = [
    "Rotate the GitHub token: monthly",
    "Prefers password: manager over notes",
    "Telegram bot token: stored in the env file",
    "Use the Authorization: header for calls",
    "token = tokenizer.encode(text)",
    "secret = os.environ[KEY]",
    "csrf_token = request.cookies",
    "max_token=DEFAULT_MAX",
    "The API key: rotate it quarterly.",
    "Rotate the token: monthly.",
    "token = tokenizer.encode.",
]
SECRET = [
    "password: hunter22xyz",
    "api_key=sk-abcdef",
    "the secret: CorrectHorseBatteryStaple",
    # review: a sentence-final period is not a dotted identifier
    "My wifi password: CorrectHorseBatteryStaple.",
    "password: ThisIsMyLongPassphrase...",
    "password: hunter2!x",
    '"api_key": "abcd-efgh"',
    "Authorization: Bearer abcdefghijkl",
    "token: a/b/c/d/e/f",
    "OPENAI_API_KEY=" + "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2",   # raw vendor shape too
    "my key is ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",  # unlabeled shape
]
try:
    print("=== the redactor is unchanged (output stays broad) ===")
    for t in ("token = tokenizer.encode(text)", "Rotate the GitHub token: monthly"):
        ok("_cv_redact still redacts %r" % t, "[redacted]" in server._cv_redact(t),
           server._cv_redact(t))

    print("\n=== aux_memlayer._ml_looks_secret ===")
    for t in BENIGN:
        ok("not a credential: %r" % t, server._ml_looks_secret(t) is False)
    for t in SECRET:
        ok("credential: %r" % t, server._ml_looks_secret(t) is True)

    print("\n=== POST /api/memory/facts (the owner's card) ===")
    c, d = post("/api/memory/facts", {"text": "Rotate the GitHub token: monthly",
                                      "origin": "card"})
    ok("an ordinary sentence is stored", c == 200 and d.get("ok") is True, (c, d))
    c, d = post("/api/memory/facts", {"text": "my password: hunter22xyz", "origin": "card"})
    ok("a real-looking credential is still refused",
       c == 400 and "credential" in str(d.get("error")), (c, d))

    print("\n=== For-You's withholding filter (calls _ml_looks_secret by name) ===")
    keep, st = server._fy_secret_filter()
    ok("the filter ran with the scrubber", st.get("scanned") is True, st)
    ok("an ordinary preference is kept for the profile",
       keep("Prefers password: manager over notes") is True)
    ok("a credential is still withheld", keep("password: hunter22xyz") is False)
    ok("withheld count is exactly one", st.get("withheld") == 1, st)

    print("\n=== hermes_mcp._looks_secret (before memory_facts add) ===")
    for t in BENIGN:
        ok("MCP: not a credential: %r" % t, M._looks_secret(t) is False)
    for t in SECRET:
        ok("MCP: credential: %r" % t, M._looks_secret(t) is True)
    corpus = BENIGN + SECRET + ["token: monthly", "token: " + "x" * 16,
                                "token: a.b.c.d.e.f.g.h.i", "password: café_noir"]
    ok("MCP and memlayer agree on every sample",
       all(M._looks_secret(t) == server._ml_looks_secret(t) for t in corpus),
       [(t, M._looks_secret(t), server._ml_looks_secret(t)) for t in corpus
        if M._looks_secret(t) != server._ml_looks_secret(t)])

    print("\n=== a regex without a value group keeps the old, strict answer ===")
    stub = re.compile(r"\b(token|password)\b\s*[:=]\s*\S{6,}", re.I)
    m = stub.search("Rotate the token: monthly")
    real = server._CV_SECRET_RE
    server._CV_SECRET_RE = stub                  # resolved by name at call time
    try:
        ok("memlayer fails closed on a stub regex",
           server._ml_looks_secret("Rotate the token: monthly") is True)
    finally:
        server._CV_SECRET_RE = real
    ok("MCP fails closed on a stub regex", M._secret_value(m) is True)
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
