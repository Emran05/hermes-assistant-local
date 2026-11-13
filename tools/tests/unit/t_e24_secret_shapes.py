#!/usr/bin/env python3
"""E24 / E82 — the credential shapes the fact check and the shared redactor
used to miss.

2026-11-09 audit E24: aux_memlayer._ml_looks_secret (and its MCP copy
hermes_mcp._looks_secret) missed Stripe `sk_live_`/`sk_test_`/`rk_live_`,
Hugging Face `hf_`, `npm_`, GitLab `glpat-`, Google `ya29.` access tokens,
`1//` refresh tokens, bare `GOCSPX-` client secrets and `scheme://user:pass@`
URL userinfo — and it skipped the redactor's PROSE rule, so "my password is
Summer2026!" was redacted from exports yet stored as a fact.  E82: the
mirrored redaction block (aux_convos.py, hermes_mcp.py, permissions.py) missed
the same shapes.

Real code only: aux_convos.py exec'd the way server.py execs an aux file,
aux_memlayer's credential test AST-extracted by name, the real hermes_mcp.py
imported, and permissions.py's copy of the block exec'd from its own bytes.
Offline; HERMES_MCP_ALLOW in a throwaway dir.  Every token here is a FAKE with
the right shape, assembled from pieces so the file holds no literal key.
"""
import ast
import contextlib
import io
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


TMP = tempfile.mkdtemp(prefix="hermes-e24-")
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "mcp-allow.json")
os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"

# ---- aux_convos.py + aux_memlayer's credential test -----------------------
G = {"__name__": "aux_e24", "re": re, "os": os, "time": time, "json": json,
     "CHATS": TMP, "PREWARM_SESSION": "__prewarm__",
     "SESSION_RE": re.compile(r"^[A-Za-z0-9._-]{1,80}$"),
     "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None,
     "load_chat": lambda sid: {}, "chat_path": lambda sid: os.path.join(TMP, sid),
     "save_chat_update": lambda sid, fn: None, "sys": sys}
_p = os.path.join(DASH, "aux_convos.py")
exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), G)
ML = os.path.join(DASH, "aux_memlayer.py")
tree = ast.parse(open(ML, encoding="utf-8").read(), ML)
WANT = {"_ml_log", "_ml_g", "_MlNoScrubber", "_ml_scrubber", "_ml_looks_secret"}
picked = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
          and n.name in WANT]
G["_ml_state"] = {"ready": False, "error": "", "logged": set()}
exec(compile(ast.Module(body=picked, type_ignores=[]), ML, "exec"), G)
ML_LOOKS = G["_ml_looks_secret"]
CV_REDACT = G["_cv_redact"]

sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402
assert M.ALLOW_PATH.startswith(TMP), M.ALLOW_PATH


def _block(path):
    txt = open(path, encoding="utf-8").read()
    a = txt.index("# ===== BEGIN MIRRORED SECRET BLOCK")
    b = txt.index("# ===== END MIRRORED SECRET BLOCK")
    return txt[a:b]


P = {"re": re}
exec(compile(_block(os.path.join(DASH, "permissions.py")), "permissions-block", "exec"), P)
PERM_REDACT = P["_cv_redact"]

A = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY"          # 32 alnum, a fake body
SECRETS = {
    "Stripe live key, labelled loosely": ("Stripe live key: sk_" "live_51" + A[:24],
                                          "sk_" "live_51" + A[:24]),
    "bare sk_live_": ("use sk_" "live_" + A[:26] + " for prod", "sk_" "live_" + A[:26]),
    "bare sk_test_": ("sk_" "test_" + A[:24], "sk_" "test_" + A[:24]),
    "bare rk_live_": ("restricted rk_" "live_" + A[:24], "rk_" "live_" + A[:24]),
    "bare hf_": ("hf_" + A[:30] + "Zq", "hf_" + A[:30] + "Zq"),
    "bare npm_": ("npm_" + A + "abcd", "npm_" + A + "abcd"),
    "bare glpat-": ("glpat-" + A[:20], "glpat-" + A[:20]),
    "Google access ya29.": ("ya29." "a0AfH6SM" + A + "-_x", "ya29." "a0AfH6SM" + A),
    "Google refresh 1//": ("refresh 1//" "0gX" + A + "Q", "1//" "0gX" + A),
    "bare GOCSPX-": ("GOCSPX-" + A[:28], "GOCSPX-" + A[:28]),
    "https userinfo": ("clone https://alice:S3cretPassw0rd@git.example.com/r.git",
                       "S3cretPassw0rd"),
    "postgres userinfo": ("DATABASE postgres://admin:hunter2pw@db.internal:5432/app",
                          "hunter2pw"),
    "prose: my password is": ("my password is Summer2026!", "Summer2026!"),
    "prose: the bank passcode is": ("the bank passcode is 482193", "482193"),
    "labelled passcode:": ("Gym locker passcode: 9931-77", "9931-77"),
    "labelled passphrase:": ("wallet passphrase: correct-horse-77", "correct-horse-77"),
}

# Ordinary facts an owner tells an assistant, several with credential words in
# a harmless sense.  None of them may be refused as a fact.
BENIGN = [
    "My password manager is 1Password",
    "The Wi-Fi password is on the fridge",
    "Keep the spare house key under the blue pot",
    "I changed my password last Tuesday",
    "The API key is stored in the 1Password vault",
    "Prefers token-based auth over cookies",
    "Hates when apps ask for a password twice",
    "Has a YubiKey for 2FA on every account",
    "My secret is that I love karaoke",
    "Key meeting with Sarah on Friday at 3pm",
    "Uses ssh keys with a passphrase for GitHub",
    "The office door code changes every month",
    "Tunes token budgets for the 27B model, usually max_tokens=4096",
    "The repo is at https://github.com/Emran05/hermes-assistant-local",
    "Clones with git@github.com:Emran05/hermes.git",
    "Wants a reminder to rotate the GitHub token every 90 days",
    "The Stripe dashboard is where invoices live",
    "Follows Hugging Face model releases closely",
    "Uses npm and pnpm for JavaScript projects",
    "Uses GitLab at work and GitHub at home",
    "The keynote is at 9:30 AM in Hall B",
    "Secret Santa budget is $50 this year",
    "The password was changed on 2024-01-01",
    "Lives near 221B Baker Street, London",
    "The staging database is postgres://db.internal:5432/app",
    "Favorite coffee: flat white with oat milk",
    "Uses the macOS keychain for passwords, never plain text",
    "Her access token expired yesterday, she re-logged in",
    "The secret key to good sleep is no screens after 10pm",
    "The proxy is http://user@proxy.local:8080 with no password",
    "Token of thanks: send Maria flowers on her birthday",
    "Prefers sk_live keys never to be pasted in chat",
]

print("--- E24: the fact check refuses every shape (both copies)")
for name, (text, secret) in sorted(SECRETS.items()):
    check("_ml_looks_secret flags %s" % name, ML_LOOKS(text) is True, text)
    check("hermes_mcp._looks_secret flags %s" % name, M._looks_secret(text) is True, text)

print("--- E24: ordinary facts still pass (both copies)")
for text in BENIGN:
    check("_ml_looks_secret accepts %r" % text, ML_LOOKS(text) is False)
    check("hermes_mcp._looks_secret accepts %r" % text, M._looks_secret(text) is False)

print("--- E82: the redactor removes every shape (all three copies)")
for label, fn in (("aux_convos", CV_REDACT), ("hermes_mcp", M._redact),
                  ("permissions", PERM_REDACT)):
    for name, (text, secret) in sorted(SECRETS.items()):
        out = fn(text)
        check("%s redacts %s" % (label, name),
              secret not in out and "[redacted]" in out, out)
    for text in BENIGN:
        if "://" in text:
            out = fn(text)
            check("%s leaves the URL %r alone" % (label, text), out == text, out)

print("--- E82: URL userinfo keeps scheme and host")
out = CV_REDACT("postgres://admin:hunter2pw@db.internal:5432/app")
check("only the user:password part is replaced",
      out == "postgres://[redacted]@db.internal:5432/app", out)
check("redacting twice changes nothing", CV_REDACT(out) == out, CV_REDACT(out))

print("--- E82: linear on hostile input")
for label, s in (("a run of ://", "a://" * 60000),
                 ("a long userinfo run without @", "x://" + "a:" * 100000),
                 ("a long 1// run", "1//" * 60000),
                 ("a long ya29. run", "ya29." * 40000)):
    t0 = time.time()
    CV_REDACT(s)
    dt = time.time() - t0
    check("%s (%d chars) scrubs in < 1 s (%.3f s)" % (label, len(s), dt), dt < 1.0)

print("--- the three copies are the same bytes")
b1 = _block(os.path.join(DASH, "aux_convos.py"))
for f in ("hermes_mcp.py", "permissions.py"):
    check("%s block byte-identical to aux_convos.py" % f,
          _block(os.path.join(DASH, f)) == b1)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
