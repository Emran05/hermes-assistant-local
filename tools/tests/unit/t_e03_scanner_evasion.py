#!/usr/bin/env python3
"""E03 review (audit 2026-11-09): what the first E03 fix still let through.

The fix made decide() re-scan the command with a copy of upstream's
DANGEROUS_PATTERNS.  Upstream's detector does more than match those patterns
over the text, and the copy did less, so with one class on Auto a SINGLE
command (no shell operator) still ran with no card:

  * `"cp" k ~/.ssh/authorized_keys "pkill -9"` — upstream de-obfuscates the
    command word, so serve reports the force-kill (earlier in its list) and
    the scanner here, which does not, saw no credential write;
  * `cp "pkill -9" //Users/me/.ssh/authorized_keys` — upstream folds a home
    prefix written with doubled slashes to ~/, the copy folded only the exact
    spelling;
  * and the scan itself could be made to run for hours: two of upstream's
    patterns backtrack exponentially (`hermes --a --a … x`, `> .env.a.a…/`),
    several are cubic, the regex engine holds the GIL, and decide() is
    reachable from GET /api/permissions/test.

Drives the REAL permissions.py against a throwaway HOME.  The timing probes
run in a child process with a timeout, so a regression fails instead of
hanging the tier.

Run:  python3 t_e03_scanner_evasion.py
"""
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
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


HOME = os.path.realpath(tempfile.mkdtemp(prefix="e03b-scan-"))
os.environ["HOME"] = HOME                 # permissions.py resolves ~ at import
os.environ.pop("HERMES_HOME", None)
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import permissions as P                   # noqa: E402

PKILL = "force kill processes"
CHMOD = "world/other-writable permissions"
PENV = "overwrite project env/config file"


def serve(cmd, first_key):
    """The approval.request payload serve builds: ONE key, the first match."""
    return {"command": cmd, "pattern_key": first_key,
            "pattern_keys": [first_key], "description": first_key}


def child(code, timeout):
    """Run `code` (which prints one JSON line) against the real module in a
    fresh process; None when it did not finish in time."""
    env = {"HOME": HOME, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HERMES_E03_DASH": os.path.join(REPO, "dashboard")}
    pre = ("import json, os, sys, time\n"
           "sys.path.insert(0, os.environ['HERMES_E03_DASH'])\n"
           "import permissions as P\n")
    try:
        r = subprocess.run([sys.executable, "-B", "-c", pre + code], env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": (r.stderr or r.stdout)[-400:]}


try:
    for cid in ("process-control", "file-perms", "git-destructive", "project-config"):
        r = P.permissions_set({"op": "set_class", "class": cid, "tier": "auto"})
        assert r.get("ok"), r

    # ------------------------------------------------------------------
    print("E03 review — a command word upstream de-obfuscates is never Auto")
    # serve reports the force-kill for each of these: the quoted "pkill -9"
    # argument matches earlier in its list than the pattern its de-obfuscated
    # command word matches
    obscured = [
        '"cp" k ~/.ssh/authorized_keys "pkill -9"',
        "c'p' k ~/.ssh/authorized_keys 'pkill -9'",
        'c"p" k ~/.ssh/authorized_keys "pkill -9"',
        '${x:-cp} k ~/.ssh/authorized_keys "pkill -9"',
        '${x/y/cp} k ~/.ssh/authorized_keys "pkill -9"',
        "$'cp' k ~/.ssh/authorized_keys 'pkill -9'",
        '"rm" -rf ~/Documents "pkill -9"',
        'env "rm" -rf ~/Documents "pkill -9"',
        'sudo -u root "rm" -rf ~/Documents "pkill -9"',
        'A=1 B=2 "rm" -rf ~/Documents "pkill -9"',
        'command "rm" -rf ~/Documents "pkill -9"',
        'nohup time "sed" -i s/manual/off/ ~/.hermes/config.yaml "pkill -9"',
        '"sed" -i s/manual/off/ ~/.hermes/config.yaml "pkill -9"',
        'X="a b" pkill -9 node',
        '$CMD -rf ~/Documents "pkill -9"',
    ]
    for cmd in obscured:
        v = P.decide(serve(cmd, PKILL))
        check("not Auto: %r -> %s" % (cmd, v["tier"]), v["tier"] != "auto", v)
    v = P.decide(serve(obscured[0], PKILL))
    check("…and the card says why", "cannot be fully checked" in v.get("reason", ""),
          v.get("reason"))

    # subshell / brace group / brace expansion / ${…}, and control characters:
    # upstream re-reads the text at each of these, the scanner does not
    for cmd in ("{pkill,-9,node}", "(pkill -9 node)", "pkill -9 ${name}",
                "chmod 777 {a,b}", "find . -exec chmod 777 {} +",
                "pkill -9 node\x1b[0m", "pkill -9 no\x9bde", "pkill -9 node\x07",
                "pkill -9 node\x0b-x", "pkill\x00 -9 node", "pkill -9 node\x7f"):
        v = P.decide(serve(cmd, CHMOD if "chmod" in cmd else PKILL))
        check("not Auto: %r -> %s" % (cmd, v["tier"]), v["tier"] == "ask", v)

    # ------------------------------------------------------------------
    print("\nE03 review — a home prefix is folded the way upstream folds it")
    folded = [
        ('cp "pkill -9" /%s/.ssh/authorized_keys' % HOME, PKILL, "credential-write"),
        ('cp "pkill -9" %s/.ssh/authorized_keys' % HOME.replace("/", "//"), PKILL,
         "credential-write"),
        ("cp evil /%s/.hermes/.env" % HOME, PENV, "credential-write"),
        ("cp evil %s/.hermes/.env" % HOME.replace("/", "//"), PENV, "credential-write"),
        ('mv "pkill -9" //Users/me/.zshrc', PKILL, "credential-write"),
        ('cp "pkill -9" /Users//someone/.ssh/authorized_keys', PKILL, "credential-write"),
        ('sed -i s/a/b/ /%s/.hermes/config.yaml "pkill -9"' % HOME, PKILL, "hermes-self"),
    ]
    for cmd, key, cls in folded:
        v = P.decide(serve(cmd, key))
        check("%s wins: %r -> %s" % (cls, cmd.replace(HOME, "<home>"), v["tier"]),
              v["tier"] != "auto" and cls in v["classes"], v)
    # the any-user fold is an extra variant, never a replacement: it rewrites
    # text, so applied alone it can also REMOVE a match
    v = P.decide(serve("chmod 777 tee $\\HOME/.ssh/a", CHMOD))
    check("a fold never hides a match: tee $\\HOME/.ssh/a is still a write to ~/.ssh",
          v["tier"] == "ask" and v["class"] == "system-config", v)

    # ------------------------------------------------------------------
    print("\nE03 review — plain single commands still auto-approve")
    for cmd, key in (
            ("pkill -9 node", PKILL),
            ("pkill -9 -f 'my server'", PKILL),
            ('pkill -9 -f "node server.js"', PKILL),
            ("sudo pkill -9 node", PKILL),
            ("sudo -u www pkill -9 node", PKILL),
            ("env LANG=C pkill -9 node", PKILL),
            ("LANG=C pkill -9 node", PKILL),
            ("nohup pkill -9 node", PKILL),
            ("/usr/bin/pkill -9 node", PKILL),
            ('chmod 777 "my file.txt"', CHMOD),
            ("chmod 777 %s/proj/out.txt" % HOME, CHMOD),
            ("chmod -R 777 /Users/me/proj/build", CHMOD),
            ("git push --force origin 'feature/x'",
             "git force push (rewrites remote history)"),
            ("cp .env.example .env", PENV)):
        v = P.decide(serve(cmd, key))
        check("still Auto: %r" % cmd.replace(HOME, "<home>"), v["tier"] == "auto", v)

    # ------------------------------------------------------------------
    print("\nE03 review — the scan cannot be made to run away")
    probes = {
        "hermes --a x30": "hermes " + "--a " * 30 + "x",
        "hermes -a x60": "hermes " + "-a " * 60 + "x",
        "hermes -a b x40": "hermes " + "-a b " * 40 + "x",
        "> .env.a x40 /": "echo x > .env" + ".a" * 40 + "/",
        "tee .env.a x40 /": "tee .env" + ".a" * 40 + "/",
        "cp k .env.a x40 / z": "cp k .env" + ".a" * 40 + "/ z",
    }
    out = child("probes = %r\n"
                "res = {}\n"
                "for name, cmd in probes.items():\n"
                "    t = time.perf_counter()\n"
                "    P.permissions_test('force kill processes', cmd)\n"
                "    res[name] = time.perf_counter() - t\n"
                "print(json.dumps(res))\n" % (probes,), 30)
    check("exponential-backtracking probes finish at all", isinstance(out, dict)
          and "error" not in out, out if out else "timed out after 30 s")
    for name in probes:
        t = (out or {}).get(name)
        check("…%s in well under a second (%s)" % (name, "%.4fs" % t if t is not None else "n/a"),
              isinstance(t, float) and t < 1.0, t)

    # the polynomial worst cases at the longest command the scanner accepts
    out = child(
        "N = P.SCAN_MAX\n"
        "def pad(u, pre=''):\n"
        "    return pre + u * ((N - len(pre)) // len(u))\n"
        "def three(a, b, c, pre=''):\n"
        "    k = (N - len(pre)) // 3\n"
        "    return pre + (a * (k // len(a)) + b * (k // len(b)) + c * (k // len(c)))\n"
        "cases = {'perl -i': pad('perl -i '), 'perl -i (2 variants)': pad('perl -i ', '\\\\x'),\n"
        "         'tee a/ . (3 variants)': three('tee ', 'a/', '.', '\\\\x /Users/me/c/d '), 'git branch -d': pad('git branch -d '),\n"
        "         'openssl base64': pad('openssl base64 '), 'cp cp': pad('cp ', '\\\\x'),\n"
        "         'sudo sudo': pad('sudo ')}\n"
        "res = {'N': N}\n"
        "for name, cmd in cases.items():\n"
        "    assert len(cmd) <= N, name\n"
        "    t = time.perf_counter()\n"
        "    P.decide({'pattern_key': 'force kill processes', 'command': cmd})\n"
        "    res[name] = time.perf_counter() - t\n"
        "print(json.dumps(res))\n", 60)
    check("worst-case commands of SCAN_MAX characters finish at all", isinstance(out, dict)
          and "error" not in out, out if out else "timed out after 60 s")
    for name, t in sorted((out or {}).items()):
        if name == "N":
            continue
        check("…%s at %s chars under 2 s (%.3fs)" % (name, (out or {}).get("N"), t),
              isinstance(t, float) and t < 2.0, t)

    # ------------------------------------------------------------------
    print("\nE03 review — the two rewritten fragments match exactly what upstream's do")
    check("every rewritten fragment is still in upstream's table",
          all(any(a in rx for rx, _d in P._DANGER_PATTERNS) for a, _b in P._DP_LINEAR),
          [a for a, _b in P._DP_LINEAR])
    check("no compiled pattern keeps an ambiguous fragment",
          not any(a in rx.pattern for rx, _d in P._DANGER_COMPILED for a, _b in P._DP_LINEAR))
    check("the table itself is untouched (still upstream's text)",
          [rx.pattern for rx, _d in P._DANGER_COMPILED]
          == [P._dp_linear(rx) for rx, _d in P._DANGER_PATTERNS]
          and any(P._dp_linear(rx) != rx for rx, _d in P._DANGER_PATTERNS))
    alphabets = (".a/ \"", "-a ")
    lengths = (7, 10)
    for (amb, lin), alpha, top in zip(P._DP_LINEAR, alphabets, lengths):
        a, b = re.compile(amb, P._DP_RE_FLAGS), re.compile(lin, P._DP_RE_FLAGS)
        diff, n = [], 0
        for k in range(top + 1):
            for tup in itertools.product(alpha, repeat=k):
                s = "".join(tup)
                n += 1
                if bool(a.fullmatch(s)) != bool(b.fullmatch(s)):
                    diff.append(s)
        check("%s == %s on all %d strings over %r up to %d chars"
              % (amb, lin, n, alpha, top), not diff, diff[:5])
    # …and in place: each affected pattern, upstream's text vs the compiled one
    ctx = []
    for tup in itertools.product(("-a", "--b", "-", "x", "gateway", "stop", "-p"), repeat=4):
        ctx.append("hermes " + " ".join(tup) + " gateway stop")
        ctx.append("hermes " + "  ".join(tup))
    for tail in itertools.product((".a", ".", "/", "a", " ", '"', ";"), repeat=4):
        t = "".join(tail)
        ctx += ["echo x > .env" + t, "tee d/.env" + t, "cp k .env" + t, "mv a config.yaml" + t]
    bad = []
    for (rx_src, desc), (rx_new, _d) in zip(P._DANGER_PATTERNS, P._DANGER_COMPILED):
        if rx_src == rx_new.pattern:
            continue
        rx_old = re.compile(rx_src, P._DP_RE_FLAGS)
        for s in ctx:
            if bool(rx_old.search(s)) != bool(rx_new.search(s)):
                bad.append((desc, s))
    check("the %d rewritten patterns agree with upstream's on %d commands"
          % (sum(1 for (a, _x), (b, _y) in zip(P._DANGER_PATTERNS, P._DANGER_COMPILED)
                 if a != b.pattern), len(ctx)), not bad, bad[:5])

    # a new repeated group in the table must be audited for ambiguity first
    def repeated_groups(rx):
        out, stack, i, n = [], [], 0, len(rx)
        while i < n:
            c = rx[i]
            if c == "\\":
                i += 2
                continue
            if c == "[":
                i += 1
                if i < n and rx[i] == "^":
                    i += 1
                if i < n and rx[i] == "]":
                    i += 1
                while i < n and rx[i] != "]":
                    i += 2 if rx[i] == "\\" else 1
                i += 1
                continue
            if c == "(":
                stack.append(i)
            elif c == ")":
                s = stack.pop()
                if i + 1 < n and rx[i + 1] in "*+{":
                    out.append(rx[s:i + 2])
            i += 1
        return out

    AUDITED = {
        r'(-[^\s]*\s+)*',            # one flag token per turn, split by spaces
        r'(-[^\s]+\s+)*',
        r'(?:\s+-\S+)*',
        r'(?:[^\s/"\'`]+/)*',        # one path segment per turn, split by /
        r'(?:\.[^/\s"\'`]+)*',       # AMBIGUOUS -> _DP_LINEAR[0]
        r'(?:-{1,2}\S+(?:\s+\S+)?\s+)*',   # AMBIGUOUS -> _DP_LINEAR[1]
    }
    found = set()
    for rx, _d in P._DANGER_PATTERNS:
        found.update(repeated_groups(rx))
    check("every repeated group in the table has been audited for backtracking",
          found == AUDITED, sorted(found ^ AUDITED))
    check("SCAN_MAX keeps the cubic patterns affordable", P.SCAN_MAX <= 1024, P.SCAN_MAX)

    # The table was compared, regex for regex and in order, with the installed
    # upstream's DANGEROUS_PATTERNS (Hermes 0.18.0, 19d4174454) and was equal.
    # Upstream is not in this repo, so that comparison cannot run here; this
    # pins the compared text, so an edit to the table is a deliberate one:
    # compare it with upstream again, audit any new repeated group, then
    # update the digest.
    digest = hashlib.sha256(json.dumps([list(p) for p in P._DANGER_PATTERNS]).encode()).hexdigest()
    check("the pattern table is the text that was compared with upstream",
          digest == "210ed58787e22e7d288aa41a04ef87991eb69ba97ac8517a77a592b80065fd06", digest)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
