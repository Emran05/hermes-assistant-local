#!/usr/bin/env python3
"""E03 (audit 2026-11-09): a Trust class on Auto must never auto-approve a
command the policy has not fully judged.

Upstream Hermes reports only the FIRST DANGEROUS_PATTERN a command matches,
so with process-control on Auto `pkill -9 node; cp mykey.pub
~/.ssh/authorized_keys` arrived as one key ("force kill processes") and
decide() answered Auto: the credential write behind it ran with no card.

Drives the REAL permissions.py against a throwaway HOME with payloads shaped
exactly like serve's approval.request (pattern_key = the first match,
pattern_keys = [that key], command = the command text):
  * layer 1 — decide() scans the command against every pattern it knows and
    resolves most-restrictive over all matches (a NEVER behind an Auto prefix
    is auto-DENIED again, even under the shipped defaults);
  * layer 2 — any shell control operator or substitution turns Auto into Ask
    with a reason the card shows;
  * no command text at all turns Auto into Ask;
  * single commands in an Auto class still auto-approve, and Never / floors
    behave as before.

Run:  python3 t_e03_compound_auto.py
"""
import json
import os
import shutil
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
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-e03-")
os.environ["HOME"] = HOME                 # permissions.py resolves ~ at import
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import permissions as P                   # noqa: E402

PKILL = "force kill processes"
CHMOD = "world/other-writable permissions"
FORCE = "git force push (rewrites remote history)"


def serve(cmd, first_key):
    """The approval.request payload serve builds: ONE key, the first match."""
    return {"command": cmd, "pattern_key": first_key,
            "pattern_keys": [first_key], "description": first_key,
            "allow_permanent": True}


def policy(classes, patterns=None):
    r = P.permissions_set({"op": "reset"})
    assert r.get("ok"), r
    for cid, tier in classes.items():
        r = P.permissions_set({"op": "set_class", "class": cid, "tier": tier})
        assert r.get("ok"), r
    for k, tier in (patterns or {}).items():
        r = P.permissions_set({"op": "set_pattern", "pattern": k, "tier": tier})
        assert r.get("ok"), r


try:
    print("E03 — owner put process-control, file-perms, git-destructive on Auto")
    policy({"process-control": "auto", "file-perms": "auto",
            "git-destructive": "auto"})

    # --- the verifier's exact compound commands: never Auto ---------------
    compound = [
        ("pkill -9 node; cp mykey.pub ~/.ssh/authorized_keys", PKILL),
        ("cp mykey.pub ~/.ssh/authorized_keys; pkill -9 node", PKILL),
        ("chmod 777 f && dd if=/dev/zero of=/dev/disk4", CHMOD),
        ("dd if=/dev/zero of=/dev/disk4 && chmod 777 f", CHMOD),
        ("pkill -9 node\ncp mykey.pub ~/.ssh/authorized_keys", PKILL),
        ("pkill -9 nosuchproc; sed -i '' s/manual/off/ ~/.hermes/config.yaml", PKILL),
        ("pkill -9 nosuchproc; git push --force origin main", PKILL),
        ("pkill -9 node && echo x >> ~/.zshrc", PKILL),
    ]
    for cmd, key in compound:
        v = P.decide(serve(cmd, key))
        check("not Auto: %r -> %s" % (cmd, v["tier"]), v["tier"] != "auto", v)

    # a credential/disk NEVER behind an Auto prefix is auto-DENIED again
    for cmd, key in compound[:5]:
        v = P.decide(serve(cmd, key))
        check("the NEVER part wins: %r" % cmd,
              v["tier"] == "never" and v["class"] in ("credential-write", "disk-device"),
              v)

    # Ask-floor part behind an Auto prefix: asks, and names the real class
    v = P.decide(serve(compound[5][0], PKILL))
    check("hermes-self behind pkill asks and names hermes-self",
          v["tier"] == "ask" and v["class"] == "hermes-self", v)

    # --- operators / substitutions with nothing else dangerous: Ask --------
    ops = [
        "pkill -9 node; echo done",
        "pkill -9 node && echo ok",
        "pkill -9 node || true",
        "pkill -9 node | tee log",
        "pkill -9 node &",
        "pkill -9 node\necho next",
        "pkill -9 node\r\necho next",
        "pkill -9 $(cat pidname)",
        "pkill -9 `cat pidname`",
        "pkill -9 node > >(cat)",
        "diff <(pkill -9 node) x",
        "chmod 777 f <<EOF\nhello\nEOF",
        "pkill -9 node 2>&1",
    ]
    for cmd in ops:
        v = P.decide(serve(cmd, PKILL if "pkill" in cmd else CHMOD))
        check("operator -> Ask: %r" % cmd, v["tier"] == "ask", v)
        check("…and the card says why: %r" % cmd,
              "more than one command" in v.get("reason", ""), v.get("reason"))

    # serve redacts the command; a redacted span can hide a second command
    # (agent/redact.py turns a PEM-shaped span, operators and all, into one
    # "[REDACTED PRIVATE KEY]"), so a command with a redaction mark asks
    for cmd in ("pkill -9 node [REDACTED PRIVATE KEY]",
                "pkill -9 node --token=***",
                "pkill -9 node --key=sk-p...7890",
                "pkill -9 node «redacted:api-key…»",
                "chmod 777 [redacted private key]"):
        v = P.decide(serve(cmd, PKILL if "pkill" in cmd else CHMOD))
        check("redacted -> Ask: %r" % cmd,
              v["tier"] == "ask" and "redacted" in v.get("reason", ""), v)

    # a key that is Auto by PATTERN override is held to the same rule
    policy({}, {FORCE: "auto"})
    v = P.decide(serve("git push --force origin main; rm notes.txt", FORCE))
    check("pattern-override Auto + operator -> Ask", v["tier"] == "ask", v)
    v = P.decide(serve("git push --force origin main", FORCE))
    check("pattern-override Auto, single command -> still Auto", v["tier"] == "auto", v)
    policy({"process-control": "auto", "file-perms": "auto",
            "git-destructive": "auto"})

    # a second pattern INSIDE one simple command (no operator): most-restrictive
    v = P.decide(serve("sudo -S chmod 777 /tmp/f", CHMOD))
    check("sudo -S + chmod 777 in one command -> privilege-escalation Never",
          v["tier"] == "never" and v["class"] == "privilege-escalation", v)
    v = P.decide(serve("sudo -A git push --force origin main", FORCE))
    check("sudo -A + force push in one command -> Never", v["tier"] == "never", v)

    # --- no command text: Auto cannot be judged, so Ask --------------------
    for p in ({"pattern_key": PKILL},
              {"pattern_key": PKILL, "pattern_keys": [PKILL]},
              {"pattern_key": PKILL, "command": ""},
              {"pattern_key": PKILL, "command": "   "},
              {"pattern_key": PKILL, "command": None},
              {"pattern_key": PKILL, "command": 42}):
        v = P.decide(p)
        check("no command text -> Ask: %s" % json.dumps(p), v["tier"] == "ask", v)
    check("permissions_test without a command asks too",
          P.permissions_test(PKILL)["verdict"]["tier"] == "ask")

    # --- single commands still auto-approve (the feature is not broken) ----
    singles = [
        ("pkill -9 node", PKILL),
        ("pkill -9 -f 'my server'", PKILL),
        ("git push --force origin main", FORCE),
        ("git push --force-with-lease origin feature/x", FORCE),
        ("chmod 777 out.txt", CHMOD),
        ("chmod -R 777 build/", CHMOD),
    ]
    for cmd, key in singles:
        v = P.decide(serve(cmd, key))
        check("single command still Auto: %r" % cmd, v["tier"] == "auto", v)
    check("permissions_test with the command still says Auto",
          P.permissions_test(PKILL, "pkill -9 node")["verdict"]["tier"] == "auto")

    # --- Never and floors unchanged ----------------------------------------
    policy({"process-control": "never"})
    v = P.decide(serve("pkill -9 node", PKILL))
    check("process-control Never still auto-denies", v["tier"] == "never", v)
    v = P.decide(serve("pkill -9 node; echo hi", PKILL))
    check("Never + operator still auto-denies (never weakened to Ask)",
          v["tier"] == "never", v)
    policy({})
    v = P.decide(serve("cp mykey.pub ~/.ssh/authorized_keys",
                       "copy/move file into sensitive credential/SSH/shell-rc path"))
    check("credential-write single command stays Never", v["tier"] == "never", v)
    v = P.decide(serve("pkill -9 node; cp mykey.pub ~/.ssh/authorized_keys", PKILL))
    check("shipped defaults: the credential write behind pkill is Never, not an Ask card",
          v["tier"] == "never", v)
    v = P.decide(serve("pkill -9 node", PKILL))
    check("shipped default for process-control is still Ask", v["tier"] == "ask", v)

    # hand-written floor violation still clamps (and the clamp is not undone)
    pol = json.load(open(P.PERM_FILE))
    pol["classes"]["destructive-delete"] = "auto"
    raw = (json.dumps(pol) + "\n").encode()
    with open(P.PERM_FILE, "wb") as f:
        f.write(raw)
    P._write_signed(raw)
    P._STATE["mtime"] = None
    v = P.decide(serve("rm -r build", "recursive delete"))
    check("floor clamp: destructive-delete auto -> Ask", v["tier"] == "ask" and v["clamped"], v)

    # untrusted policy: still suspends Auto on a single command
    policy({"process-control": "auto"})
    with open(P.PERM_FILE, "a") as f:
        f.write(" ")
    P._STATE["mtime"] = None
    v = P.decide(serve("pkill -9 node", PKILL))
    check("untrusted policy still suspends Auto", v["tier"] == "ask", v)

    # --- every error path resolves to Ask ----------------------------------
    policy({"process-control": "auto"})
    orig = P._scan_command
    try:
        def boom(_c):
            raise RuntimeError("x")
        P._scan_command = boom
        v = P.decide(serve("pkill -9 node", PKILL))
        check("a scanner error asks (never Auto)", v["tier"] == "ask", v)
    finally:
        P._scan_command = orig
    v = P.decide(serve("pkill -9 node" + "x" * 300000, PKILL))
    check("a command longer than SCAN_MAX is not judged -> Ask",
          v["tier"] == "ask" and "too long" in v.get("reason", ""), v)
    v = P.decide(serve("pkill -9 node #" + "x" * (P.SCAN_MAX - 20), PKILL))
    check("a long single command under SCAN_MAX is still Auto", v["tier"] == "auto", v)
    v = P.decide(serve("pkill -9 node" + "; x" * 1000, PKILL))
    check("a long compound command asks", v["tier"] == "ask", v)

    # obfuscation the upstream normaliser undoes is undone here too
    for cmd in ("pkill -9 node${IFS}&&${IFS}cp k ~/.ssh/authorized_keys",
                "pkill -9 node; c\\p k ~/.ssh/authorized_keys",
                "pkill -9 node; cp k %s/.ssh/authorized_keys" % HOME,
                "pkill -9 node; ｃｐ k ~/.ssh/authorized_keys"):
        v = P.decide(serve(cmd, PKILL))
        check("obfuscated compound is Never: %r" % cmd, v["tier"] == "never", v)

    # the local copy of upstream's table names exactly the keys PATTERN_CLASS maps
    names = {d for _r, d in P._DANGER_PATTERNS}
    check("the scanner knows every mapped terminal pattern and no other",
          names == set(P.PATTERN_CLASS) - {"execute_code", "mcp_elicitation"},
          (names ^ (set(P.PATTERN_CLASS) - {"execute_code", "mcp_elicitation"})))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
