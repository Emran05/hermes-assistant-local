#!/usr/bin/env python3
"""B21 — the export / MCP / trace secret scrubber, and the export's title.

2026-10-08 audit B21: the scrubber missed a JSON-quoted key
(`"api_key": "sk-…"`), names that only END in a credential word
(OPENAI_API_KEY=, GITHUB_TOKEN=, client_secret:), `Authorization: Basic`,
and prose ("my password is hunter22"); and the export's title line and its
download filename were never scrubbed at all.

Execs the REAL dashboard/aux_convos.py into a stub server.py globals dict (the
export route, _cv_redact) and imports the REAL dashboard/hermes_mcp.py (its
mirrored copy), with HERMES_MCP_ALLOW in a throwaway dir.  Offline; never
touches ~/.hermes.
"""
import json
import os
import re
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


TMP = tempfile.mkdtemp(prefix="hermes-b21-")
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "mcp-allow.json")
os.environ.setdefault("HERMES_MCP_DASHBOARD", "http://127.0.0.1:9")


# ---- aux_convos.py, exec'd the way server.py execs an aux file -------------
class _Raw(object):
    def __init__(self, body, ctype, headers=None):
        self.body, self.ctype, self.headers = body, ctype, headers or {}


CHATS = {}
G = {"__name__": "aux_convos_under_test", "re": re, "os": os, "time": time,
     "json": json, "CHATS": TMP, "PREWARM_SESSION": "__prewarm__",
     "SESSION_RE": re.compile(r"^[A-Za-z0-9._-]{1,80}$"),
     "RawResponse": _Raw,
     "register_get": lambda *a, **k: None,
     "register_post": lambda *a, **k: None,
     "load_chat": lambda sid: json.loads(json.dumps(CHATS.get(sid) or {})),
     "chat_path": lambda sid: os.path.join(TMP, sid + ".json"),
     "save_chat_update": lambda sid, fn: None}
_src = open(os.path.join(DASH, "aux_convos.py"), encoding="utf-8").read()
exec(compile(_src, os.path.join(DASH, "aux_convos.py"), "exec"), G)
REDACT = G["_cv_redact"]

sys.path.insert(0, DASH)
import hermes_mcp as M  # noqa: E402

assert M.ALLOW_PATH.startswith(TMP), M.ALLOW_PATH

# Fakes with the right SHAPE, split so this file never holds a literal token.
SK = "sk-" "proj-abc123def456"
GHP = "ghp_" "shortish1234567"
POSITIVE = {
    "JSON-quoted key": ('{"api_key": "%s"}' % SK, SK),
    "JSON-quoted key, no space": ('{"client_secret":"GOCSPX-abcdefghijkl"}',
                                  "GOCSPX-abcdefghijkl"),
    "JSON quoted inside a string": ('{\\"api_key\\": \\"%s\\"}' % SK, SK),
    "OPENAI_API_KEY=": ("OPENAI_API_KEY=%s" % SK, SK),
    "OPENAI_API_KEY='…'": ("export OPENAI_API_KEY='%s'" % SK, SK),
    "GITHUB_TOKEN=": ("GITHUB_TOKEN=%s" % GHP, GHP),
    "client_secret:": ("client_secret: s3cr3tvalue", "s3cr3tvalue"),
    "authToken:": ('authToken: "abcdef123"', "abcdef123"),
    "aws_secret_access_key =": ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG",
                                "wJalrXUtnFEMI/K7MDENG"),
    "STRIPE_SECRET_KEY=": ('STRIPE_SECRET_KEY="sk_live_abcdef"', "sk_live_abcdef"),
    "x-api-key header": ('curl -H "X-Api-Key: abc123456xyz" x', "abc123456xyz"),
    "Authorization: Basic": ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    "Authorization: Token": ("Authorization: Token abc123def456", "abc123def456"),
    "Authorization: Bearer": ("Authorization: Bearer hx_9f3a2b7c8d1e4f5a6b7c",
                              "hx_9f3a2b7c8d1e4f5a6b7c"),
    "a value with punctuation": ('"password": "hunter2!x"', "hunter2!x"),
    "prose: my password is": ("my password is hunter22", "hunter22"),
    "prose: the API key was": ("the API key was abc-12345", "abc-12345"),
    "prose: the access token was": ("the access token was 9f8e7d6c5b4a",
                                    "9f8e7d6c5b4a"),
    "prose: a symbol mid-word": ("the password is p@ssword", "p@ssword"),
}
NEGATIVE = [
    "max_tokens=4096", "prompt_tokens: 19127", "cached_tokens=12345678",
    "token_budget: 200000", "tokenizer: qwen3-8b", "est_tokens 21143",
    "the token is expired", "the secret is out", "the API key was invalid!",
    "the token is 2 hours old", "password was changed on 2024-01-01",
    "secretary: Maria123", "Basic training is 10 weeks",
    "The quick brown fox jumped over a dog!!!",
    "commit 9f3a2b7c8d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a",
    "Deploy 1.1.5 to the dashboard on Sept 3, 2026 at 14:30.",
    "token: [redacted]",
]

print("--- the scrubber, both copies, one table")
for label, fn in (("aux_convos._cv_redact", REDACT), ("hermes_mcp._redact", M._redact),
                  ("hermes_mcp._plain", M._plain)):
    for name, (text, secret) in sorted(POSITIVE.items()):
        out = fn(text)
        check("%s: %s" % (label, name), secret not in out and "[redacted]" in out,
              out)
    for text in NEGATIVE:
        check("%s leaves %r alone" % (label, text), fn(text) == text, fn(text))

print("--- shape is kept")
out = REDACT('{"api_key": "%s", "n": 1}' % SK)
try:
    parsed = json.loads(out)
except ValueError:
    parsed = None
check("a JSON object stays valid JSON after scrubbing",
      parsed == {"api_key": "[redacted]", "n": 1}, out)
out = REDACT('{"a": "{\\"api_key\\": \\"%s\\"}"}' % SK)
try:
    inner = json.loads(json.loads(out)["a"])
except (ValueError, KeyError, TypeError):
    inner = None
check("JSON inside a JSON string stays valid at both levels",
      inner == {"api_key": "[redacted]"}, out)
check("Authorization: Bearer collapses to ONE marker",
      REDACT("Authorization: Bearer hx_0123456789abcdef") == "Authorization: [redacted]",
      REDACT("Authorization: Bearer hx_0123456789abcdef"))
check("an already-redacted value is not matched twice",
      REDACT(REDACT('password="hunter2!x"')) == 'password="[redacted]"',
      REDACT(REDACT('password="hunter2!x"')))

print("--- linear on hostile input")
for label, s in (("a long dotted run", "a." * 100000),
                 ("a run of credential suffixes", "secret" + "_key" * 25000),
                 ("a run of key:", "token:" * 16000),
                 ("a run of quotes", 'token"' + '"' * 100000),
                 ("a run of backslashes", "token" + "\\" * 100000),
                 ("prose padding", ("password is " + "a" * 100) * 900)):
    t0 = time.time()
    REDACT(s)
    dt = time.time() - t0
    check("%s (%d chars) scrubs in < 1 s (%.3f s)" % (label, len(s), dt), dt < 1.0)

print("--- the two copies are the same bytes")


def _block(path):
    txt = open(path, encoding="utf-8").read()
    a = txt.index("# ===== BEGIN MIRRORED SECRET BLOCK")
    b = txt.index("# ===== END MIRRORED SECRET BLOCK")
    return txt[a:b]


b1 = _block(os.path.join(DASH, "aux_convos.py"))
b2 = _block(os.path.join(DASH, "hermes_mcp.py"))
check("mirrored block byte-identical (%d vs %d bytes)" % (len(b1), len(b2)), b1 == b2)
for name in ("_CV_SECRET_RE", "_CV_PROSE_SECRET_RE", "_CV_BEARER_RE",
             "_CV_HERMES_PATH_RE", "def _cv_redact(", "def _cv_redact_raw("):
    check("the mirrored block carries %s" % name, name in b1)

print("--- the export's title line and filename")
CHATS["chat-b21-auto"] = {"title": "", "messages": [
    {"role": "user", "text": "my OPENAI_API_KEY=%s is broken, why?" % SK, "ts": 1.0e9},
    {"role": "bot", "text": "Rotate it.", "ts": 1.0e9 + 5}]}
CHATS["chat-b21-named"] = {"title": "GITHUB_TOKEN=%s notes" % GHP, "messages": [
    {"role": "user", "text": "hello", "ts": 1.0e9}]}
CHATS["chat-b21-cut"] = {"title": "", "messages": [
    # the secret straddles the auto-title's 48-character cut
    {"role": "user", "text": "please check this config: api_key=%s ok" % SK,
     "ts": 1.0e9}]}
for sid in CHATS:
    open(os.path.join(TMP, sid + ".json"), "w").write("{}")


class _Ctx(object):
    def __init__(self, sid):
        self.sid = sid

    def q1(self, k):
        return self.sid if k == "session" else None


for sid, secret in (("chat-b21-auto", SK), ("chat-b21-named", GHP),
                    ("chat-b21-cut", SK)):
    r = G["_cv_export"](_Ctx(sid))
    body = getattr(r, "body", "")
    title = body.split("\n", 1)[0]
    fname = (getattr(r, "headers", {}) or {}).get("Content-Disposition", "")
    check("%s: export answered markdown" % sid, isinstance(r, _Raw), r)
    check("%s: title line carries no secret" % sid,
          secret not in title and secret[:12] not in title, title)
    check("%s: filename carries no secret" % sid,
          secret.lower() not in fname.lower() and
          secret[:12].lower().replace("_", "-") not in fname.lower(), fname)
    check("%s: filename is still a dated ascii slug" % sid,
          bool(re.fullmatch(r'attachment; filename="hermes-[a-z0-9-]+-\d{4}-\d{2}-\d{2}\.md"',
                            fname)), fname)
    check("%s: body carries no secret" % sid, secret not in body, body[:200])
title = G["_cv_export"](_Ctx("chat-b21-auto")).body.split("\n", 1)[0]
check("the auto-title keeps its words", title.startswith("# my OPENAI_API_KEY=[redacted]"),
      title)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
