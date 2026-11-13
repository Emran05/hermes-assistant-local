#!/usr/bin/env python3
"""E06 (audit 2026-11-09): the known-secret canaries read ~/.hermes/.env the
way Hermes does (python-dotenv), so an inline comment, quotes, `export`,
escapes or a multi-line quoted value cannot turn the hard C-secret-known block
off for that secret. Both the dotenv-parsed value and the raw remainder of the
line stay canaries.

Table-tests the REAL _ghc_env_values (exec'd from aux_ghchanges.py through
t_gh_changes.py's harness), then proves the block end to end for the inline
comment line the audit reproduced.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
S = "8123456789:AAH-envsecretenvsecretenvsecret99"
S2 = "sk-test-Q1w2E3r4T5y6U7i8O9p0Zz"


def main():
    w = H.World("e06")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    env_values = ns["_ghc_env_values"]
    tmp = tempfile.mkdtemp(prefix="env-", dir=w.tmp)
    n = [0]

    def vals(text):
        n[0] += 1
        p = os.path.join(tmp, "e%d.env" % n[0])
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
        return env_values(p)

    print("== the dotenv value is a canary ==")
    table = [
        ("plain", "TOKEN_A=%s\n" % S, S),
        ("double-quoted", 'TOKEN_A="%s"\n' % S, S),
        ("single-quoted", "TOKEN_A='%s'\n" % S, S),
        ("export", "export TOKEN_A=%s\n" % S, S),
        ("spaces around = and export", "export   TOKEN_A =  %s  \n" % S, S),
        ("CRLF", "TOKEN_A=%s\r\nOTHER_KEY=x\r\n" % S, S),
        ("unquoted + inline comment (1 space)", "TOKEN_A=%s # the prod bot\n" % S, S),
        ("unquoted + inline comment (3 spaces)", "TOKEN_A=%s   # the prod bot\n" % S, S),
        ("unquoted + tab before #", "TOKEN_A=%s\t# note\n" % S, S),
        ("# without a space is part of the value", "TOKEN_A=%s#x\n" % S, S + "#x"),
        ("double-quoted + comment", 'TOKEN_A="%s" # work account\n' % S, S),
        ("single-quoted + comment", "TOKEN_A='%s' # work account\n" % S, S),
        ("a quoted # is part of the value", 'TOKEN_A="%s #x"\n' % S, S + " #x"),
        ("escaped double quote", 'TOKEN_A="ab\\"%s"\n' % S, 'ab"' + S),
        ("escaped backslash", 'TOKEN_A="%s\\\\"\n' % S, S + "\\"),
        ("\\n escape in double quotes", 'TOKEN_A="%s\\nline2"\n' % S, S + "\nline2"),
        ("single quotes keep \\n literally", "TOKEN_A='%s\\n'\n" % S, S + "\\n"),
        ("escaped single quote", "TOKEN_A='a\\'%s'\n" % S, "a'" + S),
        ("multi-line double-quoted", 'TOKEN_A="%s\nline2"\nNEXT_KEY=%s\n' % (S, S2),
         S + "\nline2"),
        ("multi-line single-quoted", "TOKEN_A='%s\nline2'\n" % S, S + "\nline2"),
        ("quoted key", "'TOKEN_A'=%s\n" % S, S),
        ("no closing quote: dotenv's regex closes at the last \\\"",
         'TOKEN_A="%s\\"\n' % S, S + "\\"),
    ]
    for name, text, want in table:
        got = vals(text)
        check("%-40s -> %r in canaries" % (name, want[:24]), want in got, got)

    import time
    t0 = time.time()
    got = vals('TOKEN_A="' + '\\"' * 20000 + "\nSECRET_B=" + S2 + "\n")
    check("10k escaped quotes and no closing one parse in linear time (no regex "
          "backtracking blow-up)", time.time() - t0 < 2 and isinstance(got, list),
          round(time.time() - t0, 2))

    print("\n== the raw remainder stays a canary too ==")
    got = vals("TOKEN_A=%s   # the prod bot\n" % S)
    check("an inline-comment line keeps the raw remainder", (S + "   # the prod bot") in got, got)
    got = vals('TOKEN_A="%s" # work\n' % S)
    check("...and so does a quoted + comment line", ('"%s" # work' % S) in got, got)

    print("\n== what is NOT a canary ==")
    got = vals("# TOKEN_A=%s\n  # SECRET_B=%s\n" % (S, S2))
    check("commented-out lines", got == [], got)
    got = vals("PLAIN_SETTING=%s\nNAME=%s\n" % (S, S2))
    check("keys that do not name a token/key/secret/password", got == [], got)
    got = vals("TOKEN_A=\nTOKEN_B=''\n")
    check("empty values add nothing non-empty", all(v == "" for v in got), got)
    got = vals('TOKEN_A="%s\nline2"\nPLAIN=%s\n' % (S, S2))
    check("a multi-line value does not swallow the next line's key", S2 not in got, got)
    got = vals('TOKEN_A="%s\nline2"\nNEXT_KEY=%s\n' % (S, S2))
    check("...and the next secret-keyed line is still read", S2 in got, got)
    got = vals('TOKEN_A="unterminated %s\nSECRET_B=%s\n' % (S, S2))
    check("an unterminated quote does not crash, and keeps the raw text",
          isinstance(got, list) and any(S in v for v in got), got)

    print("\n== end to end: the hard block holds with an inline comment ==")
    w2 = H.World("e06b")
    w2.repo("octo/app", dict(H.SEED_FILES))
    w2.start()
    ns2 = H.load(w2, secrets_env={"TELEGRAM_BOT_TOKEN": S + "   # the prod bot",
                                  "OPENAI_API_KEY": '"%s" # work account' % S2})
    H.connect(w2, ns2, ["octo/app"])
    sb = H.prepare(ns2, "octo/app")
    H.write(sb, "src/conf.py", "BOT = '%s'\n" % S)
    st, r = H.request(ns2, "octo/app", "e06-raw")
    check("the bare token from an inline-comment line -> C-secret-known block",
          st == 200 and r.get("approvable") is False and any(
              c["id"] == "C-secret-known" and c["level"] == "block" for c in r.get("checks") or []),
          (st, [(c["id"], c["level"]) for c in r.get("checks") or []]))
    ns2["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    H.write(sb, "src/conf.py", "KEY = '%s'\n" % S2)
    st, r = H.request(ns2, "octo/app", "e06-quoted")
    check("the bare value of a quoted + comment line -> C-secret-known block",
          st == 200 and r.get("approvable") is False and any(
              c["id"] == "C-secret-known" for c in r.get("checks") or []),
          (st, [(c["id"], c["level"]) for c in r.get("checks") or []]))

    w.stop()
    w2.stop()
    H.finish()


if __name__ == "__main__":
    main()
