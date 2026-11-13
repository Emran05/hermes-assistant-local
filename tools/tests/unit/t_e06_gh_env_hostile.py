#!/usr/bin/env python3
"""E06 review (audit 2026-11-09, round-4 review of c03a): the .env canary
reader cannot be stalled, and it never yields FEWER canaries than before.

Two things the first E06 fix left:

  * the unquoted-value comment cut used python-dotenv's own
    `re.sub(r"\\s+#.*", "", part)`, which is quadratic on a long run of blanks
    with no `#` after it: 260 KB of trailing blanks on one line held every
    change request (and the mirror lock) for about a minute;
  * if the dotenv-style parser ever raised, the scan went on with NO .env
    canaries at all -- the un-overridable C-secret-known block turned itself
    off silently (fail open). Now the plain line-by-line reading is the
    fallback, so every secret-keyed value is still a canary.

Drives the REAL _ghc_env_values and the real request route (t_gh_changes.py's
harness).
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
S = "8123456789:AAH-hostileenvhostileenvhostile99"
S2 = "sk-test-H0stileH0stileH0stileH0st"


def main():
    w = H.World("e06h")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w, secrets_env={"TELEGRAM_BOT_TOKEN": S, "OPENAI_API_KEY": '"%s" # work' % S2})
    H.connect(w, ns, ["octo/app"])
    env_values = ns["_ghc_env_values"]
    tmp = tempfile.mkdtemp(prefix="env-", dir=w.tmp)
    n = [0]

    def vals(text):
        n[0] += 1
        p = os.path.join(tmp, "h%d.env" % n[0])
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
        return env_values(p)

    print("== the comment cut is the dotenv one, value by value ==")
    table = [
        ("no comment", "TOKEN_A=%s\n" % S, S),
        ("one space then #", "TOKEN_A=%s # c\n" % S, S),
        ("tab then #", "TOKEN_A=%s\t# c\n" % S, S),
        ("blanks, then # after more blanks", "TOKEN_A=%s  \t  # c # d\n" % S, S),
        ("# glued to the value stays", "TOKEN_A=%s#c\n" % S, S + "#c"),
        ("a glued # then a spaced one", "TOKEN_A=%s#c # d\n" % S, S + "#c"),
        ("a value with inner spaces", "TOKEN_A=%s and more # c\n" % S, S + " and more"),
        ("trailing blanks, no #", "TOKEN_A=%s   \t \n" % S, S),
    ]
    for name, text, want in table:
        got = vals(text)
        check("%-34s -> %r" % (name, want[-10:]), want in got, got)
    got = vals("TOKEN_A=x # nothing\n")
    check("the cut leaves nothing of the comment", got == ["x", "x # nothing"], got)

    print("\n== a long run of blanks cannot stall the scan ==")
    t0 = time.time()
    got = vals("TOKEN_A=" + S + " \t" * 40000 + "\nSECRET_B=" + S2 + "\n")
    took = time.time() - t0
    check("80 KB of blanks after a value: read in under 3 s (%.2fs)" % took, took < 3, took)
    check("...and both secrets are canaries", S in got and S2 in got, [v[:20] for v in got])
    t0 = time.time()
    got = vals("TOKEN_A=" + S + " \t" * 40000 + "x\n")
    took = time.time() - t0
    check("the same with text after the blanks (%.2fs)" % took, took < 3
          and any(v.startswith(S) for v in got), took)

    print("\n== a parser failure keeps the line-by-line canaries (fails closed) ==")
    real = ns["_ghc_dotenv"]

    def boom(text):
        raise RuntimeError("parser bug")
    ns["_ghc_dotenv"] = boom
    try:
        with H.Stderr() as err:
            got = vals("TOKEN_A=%s\nexport SECRET_B='%s'\nPLAIN=x\n" % (S, S2))
        check("the scan still has both secret-keyed values", S in got and S2 in got, got)
        check("...and says once that the parser failed", "parser failed" in err.text, err.text)
        sb = H.prepare(ns, "octo/app")
        H.write(sb, "src/conf.py", "BOT = '%s'\n" % S)
        with H.Stderr():
            st, r = H.request(ns, "octo/app", "e06h-fallback")
        check("end to end: the token in a file is still a C-secret-known BLOCK",
              st == 200 and r.get("approvable") is False and any(
                  c["id"] == "C-secret-known" and c["level"] == "block"
                  for c in r.get("checks") or []),
              (st, [(c["id"], c["level"]) for c in r.get("checks") or []]))
    finally:
        ns["_ghc_dotenv"] = real

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
