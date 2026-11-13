#!/usr/bin/env python3
"""R2-15 (round 2 of the 2026-10-21 audit): the approval audit log is private
and holds no raw secrets.

permissions.py's audit log (~/.hermes/dashboard/permissions-log.jsonl, plus
its single .1 rotation) stores the command text of every approval.  It was
opened with open(..., "a"), so it was created at the default umask — 0644 —
and the command went in verbatim, so an `export OPENAI_API_KEY=sk-…` that
needed approval sat world-readable on disk.

Checks, all against the REAL permissions.py in a throwaway HOME:
  * a fresh log is created 0600 even under umask 022;
  * a pre-existing 0644 log and 0644 .1 rotation are tightened to 0600;
  * a rotated generation is 0600;
  * labeled, Bearer and unlabeled secrets in `command` are scrubbed before
    storage, including one straddling the CMD_TRUNC cut, while an ordinary
    command survives untouched;
  * the scrubber in permissions.py is byte-identical to aux_convos.py's
    mirrored block (the B21 invariant, now with a third copy).

Run:  python3 t_r2_15_audit_log.py
"""
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return os.environ.get("HERMES_REPO") or os.path.dirname(
        os.path.dirname(os.path.dirname(_HERE)))


REPO = _find_repo()
DASH = os.path.join(REPO, "dashboard")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


HOME = tempfile.mkdtemp(prefix="hermes-r215-")
os.environ["HOME"] = HOME
os.umask(0o022)
sys.path.insert(0, DASH)
try:
    import permissions as P   # noqa: E402

    def fresh():
        """Re-import against an empty HOME so module state starts clean."""
        shutil.rmtree(os.path.join(HOME, ".hermes"), ignore_errors=True)
        return importlib.reload(P)

    def entries(mod):
        with open(mod.LOG_FILE, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    JOB = {"id": "job-1", "session": "s-1"}
    V = {"tier": "ask", "class": "arbitrary-exec"}

    print("\n== a fresh log is created 0600 ==")
    M = fresh()
    M.audit(JOB, {"pattern_key": "shell command via -c/-lc flag",
                  "command": "bash -c 'ls -la'"}, V, "asked")
    check("log exists", os.path.exists(M.LOG_FILE))
    check("fresh log is 0600 under umask 022 (%o)" % mode(M.LOG_FILE),
          mode(M.LOG_FILE) == 0o600)

    print("\n== a pre-existing 0644 log and .1 are tightened ==")
    M = fresh()
    os.makedirs(M.DASH_DIR, mode=0o700)
    for p in (M.LOG_FILE, M.LOG_FILE + ".1"):
        with open(p, "w") as f:
            f.write('{"ts": 1, "action": "asked", "command": "old"}\n')
        os.chmod(p, 0o644)
    M.audit(JOB, {"pattern_key": "x", "command": "echo hi"}, V, "asked")
    check("existing live log tightened to 0600 (%o)" % mode(M.LOG_FILE),
          mode(M.LOG_FILE) == 0o600)
    check("existing .1 rotation tightened to 0600 (%o)" % mode(M.LOG_FILE + ".1"),
          mode(M.LOG_FILE + ".1") == 0o600)
    check("the old line is kept (append, not truncate)",
          [e.get("command") for e in entries(M)] == ["old", "echo hi"],
          entries(M))

    print("\n== a rotated generation is 0600 ==")
    M = fresh()
    os.makedirs(M.DASH_DIR, mode=0o700)
    with open(M.LOG_FILE, "w") as f:
        f.write("x" * (M.LOG_MAX + 10) + "\n")
    os.chmod(M.LOG_FILE, 0o644)
    if hasattr(M, "_LOG_OLD_FIXED"):
        M._LOG_OLD_FIXED[0] = True   # past the once-per-process pass: rotation alone
    M.audit(JOB, {"pattern_key": "x", "command": "echo rotate"}, V, "asked")
    check("rotated .1 exists", os.path.exists(M.LOG_FILE + ".1"))
    check("rotated .1 is 0600 (%o)" % mode(M.LOG_FILE + ".1"),
          mode(M.LOG_FILE + ".1") == 0o600)
    check("new live file is 0600 (%o)" % mode(M.LOG_FILE), mode(M.LOG_FILE) == 0o600)
    check("new live file holds only the new line",
          [e.get("command") for e in entries(M)] == ["echo rotate"])

    print("\n== command text is scrubbed before storage ==")
    SK = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0" * 2
    GHP = "ghp_" + "Z" * 36
    BEARER = "hx_" + "q" * 30
    cases = [
        ("labeled env assignment", "export OPENAI_API_KEY=%s && ./run" % ("sk-" + "x" * 30),
         "sk-" + "x" * 30),
        ("unlabeled Anthropic key", "curl -d '%s' https://example.invalid" % SK, SK),
        ("Bearer header", "curl -H 'Authorization: Bearer %s' https://x.invalid" % BEARER,
         BEARER),
        ("GitHub token in a URL", "git clone https://%s@github.com/o/r" % GHP, GHP),
    ]
    M = fresh()
    for label, cmd, secret in cases:
        M.audit(JOB, {"pattern_key": "x", "command": cmd}, V, "asked")
    got = entries(M)
    raw = open(M.LOG_FILE, encoding="utf-8").read()
    for (label, cmd, secret), e in zip(cases, got):
        check("%s is redacted in the stored entry" % label,
              secret not in e.get("command", "") and "[redacted" in e.get("command", ""),
              e.get("command"))
        check("%s is nowhere in the file" % label, secret not in raw)

    # a secret straddling the CMD_TRUNC cut: truncating first would leave a
    # prefix shorter than the scrubber's minimum length, i.e. kept verbatim
    pad = "echo " + "a" * (M.CMD_TRUNC - 5 - 14)
    straddle = pad + " " + SK
    M = fresh()
    M.audit(JOB, {"pattern_key": "x", "command": straddle}, V, "asked")
    c = entries(M)[0]["command"]
    check("a key straddling CMD_TRUNC leaves no fragment (%r)" % c[-20:],
          "sk-ant-" not in c, c[-40:])
    check("the stored command is still capped at CMD_TRUNC", len(c) <= M.CMD_TRUNC, len(c))

    # review R2-15: the scrub runs on the approval path over a command that
    # can be a whole script, and the PEM rule is quadratic in BEGIN lines with
    # no END — so only a bounded window past the cut may be scrubbed.  A PEM
    # key straddling the cut must still go.
    M = fresh()
    seen = []
    real_scrub = M._audit_scrub
    M._audit_scrub = lambda t: (seen.append(len(t)), real_scrub(t))[1]
    flood = "-----BEGIN RSA PRIVATE KEY-----\n" * 6000      # ~192 KB, no END
    import time as _time
    t0 = _time.perf_counter()
    M.audit(JOB, {"pattern_key": "x", "command": flood}, V, "asked")
    took = _time.perf_counter() - t0
    M._audit_scrub = real_scrub
    slack = getattr(M, "CMD_SCRUB_SLACK", None)
    check("the scrubber sees a bounded window, not the whole command (%s)" % seen,
          seen and slack is not None and max(seen) <= M.CMD_TRUNC + slack
          and max(seen) < len(flood), (seen, slack))
    check("a 192 KB BEGIN-line flood audits in well under 1.5 s (%.2fs)" % took,
          took < 1.5, took)
    check("…and is still logged, capped", len(entries(M)) == 1
          and len(entries(M)[0]["command"]) <= M.CMD_TRUNC)
    pem = ("-----BEGIN OPENSSH PRIVATE KEY-----\n" + ("b3BlbnNzaC1rZXktdjEAAAAA" * 3 + "\n") * 60
           + "-----END OPENSSH PRIVATE KEY-----")
    M = fresh()
    M.audit(JOB, {"pattern_key": "x",
                  "command": "echo " + "a" * (M.CMD_TRUNC - 200) + " && cat <<EOF\n" + pem
                  + "\nEOF\n" + "echo tail " * 4000}, V, "asked")
    c = entries(M)[0]["command"]
    check("a PEM key straddling the cut leaves no header or body (%r)" % c[-60:],
          "BEGIN OPENSSH" not in c and "b3BlbnNzaC1rZXkt" not in c, c[-120:])

    M = fresh()
    plain = ("git reset --hard HEAD~1 && rm -rf build/ && echo token is expired"
             " && sed -i '' s/a/b/ ~/.hermes/config.yaml")
    M.audit(JOB, {"pattern_key": "x", "command": plain}, V, "asked")
    check("an ordinary command (a ~/.hermes path included) is stored unchanged",
          entries(M)[0]["command"] == plain, entries(M)[0]["command"])

    print("\n== the scrubber is aux_convos.py's mirrored block, byte for byte ==")

    def _block(path):
        txt = open(path, encoding="utf-8").read()
        a = txt.index("# ===== BEGIN MIRRORED SECRET BLOCK")
        b = txt.index("# ===== END MIRRORED SECRET BLOCK")
        return txt[a:b]

    b1 = _block(os.path.join(DASH, "aux_convos.py"))
    try:
        b3 = _block(os.path.join(DASH, "permissions.py"))
    except ValueError:
        b3 = ""
    check("permissions.py carries the mirrored block byte-identical (%d vs %d bytes)"
          % (len(b1), len(b3)), b1 == b3)
    src = open(os.path.join(DASH, "permissions.py"), encoding="utf-8").read()
    body = src.split("def _audit_scrub(", 1)[-1].split("\ndef ", 1)[0]
    check("_audit_scrub applies every value rule of the block",
          all(n in body for n in ("_CV_SECRET_RE", "_CV_BEARER_RE",
                                  "_CV_PROSE_SECRET_RE", "_cv_redact_raw(")), body[:200])
    check("permissions.audit() uses it", "_audit_scrub(" in
          src.split("def audit(", 1)[-1].split("\ndef ", 1)[0])
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
