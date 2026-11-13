#!/usr/bin/env python3
"""R2-73 — a credential split across lines is redacted in a single-line MCP field.

hermes_mcp._line() (every one-line field: a search hit's title/snippet, a
calendar title, a Needs-you sender/summary) ran _plain() — which redacts —
and only THEN joined the field's lines.  The labeled-secret rule matches a
value on the same line as its key (by design: B21), so
"password:\\nhunter22xyz" went through _redact() untouched and the join turned
it into "password: hunter22xyz", the exact one-line form it redacts.  The
lines are now joined first.

Imports the REAL dashboard/hermes_mcp.py with HERMES_MCP_ALLOW pointing at a
throwaway file and stubs only dash_get.  No dashboard, no network.

Fails on the old code: every split form below comes out unredacted, and the
needs_you tool prints the password.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
TMP = tempfile.mkdtemp(prefix="hermes-r2-73-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
json.dump({"tools": {"messages_search": True}}, open(ALLOW, "w"))
os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s  — %r" % (name, detail))


SECRET = "hunter22xyz"
try:
    print("=== _line(): the split forms are redacted like the one-line form ===")
    one = M._line("password: " + SECRET)
    check("baseline: the one-line form is redacted", SECRET not in one and "[redacted]" in one, one)
    for label, raw in (("\\n", "password:\n" + SECRET),
                       ("\\r\\n", "password:\r\n" + SECRET),
                       ("\\n + indent", "password:\n    " + SECRET),
                       ("U+2028", "password: " + SECRET),
                       ("U+2029", "password: " + SECRET),
                       ("U+0085", "password:\x85" + SECRET),
                       ("blank line", "api_key =\n\n" + SECRET),
                       ("JSON-ish", '"token":\n"' + SECRET + '"')):
        out = M._line(raw)
        check("split by %s -> redacted, one line" % label,
              SECRET not in out and "[redacted]" in out and "\n" not in out, out)
    check("a limit still applies after the collapse",
          len(M._line("word " * 100, 40)) <= 40, M._line("word " * 100, 40))
    check("ordinary multi-line text is just joined",
          M._line("Lunch with\nSam  at\tnoon") == "Lunch with Sam at noon",
          M._line("Lunch with\nSam  at\tnoon"))
    check("None and non-strings still work", M._line(None) == "" and M._line(42) == "42")

    print("\n=== review: what the fence turns into a space is one before redaction ===")
    # untrusted.line()/body() turn C1 controls and U+2028/U+2029 into spaces
    # AFTER _plain() redacted; str.split() does not treat \x80-\x84/\x86-\x9f as
    # whitespace, so those slipped through _line()'s join, and _plain()'d text
    # that is fenced as a block had no join at all.
    for label, raw in (("C1 before the colon", "password\x86: " + SECRET),
                       ("C1 after the colon", "password:\x9f " + SECRET),
                       ("C1 glued to the value", "password:\x80" + SECRET)):
        out = M._line(raw)
        check("_line, %s -> redacted" % label, SECRET not in out, out)
    for label, raw in (("U+2028", "password: " + SECRET),
                       ("U+2029", "password: " + SECRET),
                       ("U+0085", "password:\x85" + SECRET),
                       ("C1", "password\x86: " + SECRET)):
        out = "\n".join(M._fence("notes", [M._plain(raw)]))
        check("_plain + fence, %s -> redacted" % label, SECRET not in out, out[-120:])
    check("_plain keeps \\t \\n \\r for multi-line fields",
          M._plain("a\tb\nc\rd") == "a\tb\nc\rd", M._plain("a\tb\nc\rd"))
    M.dash_get = lambda path, params=None, **kw: {
        "ok": True, "sources": {"note": 1},
        "results": [{"id": "note:1", "source": "note", "ref": "notes",
                     "title": "wifi password\x86: " + SECRET,
                     "snippet": "router token:\x9f " + SECRET}]}
    out = str(M._search("wifi", "", 5))
    check("hermes_search end to end never prints the password",
          "wifi password" in out and SECRET not in out, out[-300:])

    print("\n=== end to end: a Needs-you row from a message preview ===")
    M.dash_get = lambda path, params=None, **kw: {
        "ok": True, "built_at": 0,
        "now": [{"id": "m1", "source": "message", "sender": "IT desk",
                 "summary": "your new password:\n" + SECRET,
                 "reason": "a question", "bucket": "now"}],
        "today": [], "later": [], "never_count": 0, "sources": {"message": True}}
    out = str(M.t_needs_you({}))
    check("the row is in the answer", "IT desk" in out, out[:400])
    check("needs_you never prints the password", SECRET not in out, out[:400])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
