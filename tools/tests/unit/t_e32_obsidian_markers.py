#!/usr/bin/env python3
"""E32 (docs/audits/2026-11-09-codebase-audit.md) — obsidian_daily.py must never
replace the owner's own daily-note text.

With the END marker line deleted by hand, the next run appended a second block
and the run after that matched from the ORPHAN start marker to the new block's
end, deleting everything in between (the owner's notes included).

Runs the REAL script as a subprocess against throwaway vaults (TZ pinned, HOME
scratch, offline).  Rules checked:
  * a replaced span never contains a second START (or an END before its own);
  * with no well-formed START..END pair, a fresh block is APPENDED and every
    existing byte stays where it was;
  * every byte outside the replaced generated block(s) is unchanged.
"""
import json, os, shutil, subprocess, sys, tempfile, time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DAILY = os.path.join(REPO, "obsidian_daily.py")
START = "%% hermes:brief:start %%"
END = "%% hermes:brief:end %%"
PASS = [0]
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  ok   " + name)
    else:
        FAILS.append(name)
        print("  FAIL " + name + "  <- %r" % (detail,))


TMP = tempfile.mkdtemp(prefix="t-e32-")
VAULT = os.path.join(TMP, "vault")
os.makedirs(os.path.join(VAULT, "Daily"))
BJ = os.path.join(TMP, "briefing.json")
TS = 1793030400
os.environ["TZ"] = "UTC"
time.tzset()
DAY = time.strftime("%Y-%m-%d", time.localtime(TS))
NOTE = os.path.join(VAULT, "Daily", DAY + ".md")


def run(reply):
    json.dump({"generated_at": TS, "reply": reply}, open(BJ, "w"))
    env = dict(os.environ, HOME=os.path.join(TMP, "home"), TZ="UTC")
    r = subprocess.run([sys.executable, DAILY, VAULT, BJ], capture_output=True,
                       text=True, env=env, timeout=60)
    return r.returncode, r.stdout + r.stderr


def read():
    with open(NOTE, "rb") as f:
        return f.read().decode("utf-8")


def write(txt):
    with open(NOTE, "wb") as f:
        f.write(txt.encode("utf-8"))


def outside(txt):
    """Everything not inside a well-formed adjacent START..END pair."""
    out, pos = [], 0
    while True:
        s = txt.find(START, pos)
        if s < 0:
            break
        e = txt.find(END, s + len(START))
        s2 = txt.find(START, s + len(START))
        if e < 0:
            break
        if 0 <= s2 < e:
            out.append(txt[pos:s2]); pos = s2
            continue
        out.append(txt[pos:s]); pos = e + len(END)
    out.append(txt[pos:])
    return "".join(out)


# --- the verifier's sequence -------------------------------------------------
print("E32 — the verifier's sequence (END line deleted, notes written below)")
if os.path.exists(NOTE):
    os.remove(NOTE)
rc, out = run("BRIEF v1")
check("run 1 files the block", rc == 0 and "BRIEF v1" in read(), out)
t = read().replace(END + "\n", "")
t += "\n## My meeting notes\n- call the bank at 3\n"
write(t)
rc, out = run("BRIEF v2")
t2 = read()
check("run 2 appends a fresh block", rc == 0 and t2.count(START) == 2 and "BRIEF v2" in t2, t2)
check("run 2 keeps every byte that was there", t2.startswith(t), t2)
rc, out = run("BRIEF v3")
t3 = read()
check("run 3 keeps the owner's notes", "## My meeting notes\n- call the bank at 3\n" in t3, t3)
check("run 3 keeps the orphan block and its text", "BRIEF v1" in t3, t3)
check("run 3 replaced only the well-formed block", "BRIEF v3" in t3 and "BRIEF v2" not in t3, t3)
check("run 3: everything outside the generated block is byte-identical",
      outside(t3) == outside(t2), (outside(t3), outside(t2)))
check("run 3 did not add a third block", t3.count(START) == 2, t3)
rc, out = run("BRIEF v4")
t4 = read()
check("run 4 still two blocks, notes kept", t4.count(START) == 2 and "call the bank" in t4
      and "BRIEF v4" in t4, t4)

print("\nE32 — a brief that contains a marker cannot nest the block")
write("mine\n")
rc, out = run("evil " + END + " tail " + START + " x")
rc, out = run("clean brief")
t5 = read()
check("markers inside a brief are dropped", t5.count(START) == 1 and t5.count(END) == 1
      and "clean brief" in t5 and "evil" not in t5 and t5.startswith("mine\n"), t5)


def scenario(name, before, expect_append, must_keep=()):
    write(before)
    rc, out = run("NEW BRIEF")
    after = read()
    check(name + ": exit 0", rc == 0, out)
    check(name + ": new brief filed", "NEW BRIEF" in after, after)
    for k in must_keep:
        check(name + ": keeps %r" % k, k in after, after)
    if expect_append:
        check(name + ": appended, every old byte kept", after.startswith(before), after)
        check(name + ": exactly one more START", after.count(START) == before.count(START) + 1, after)
    else:
        check(name + ": outside the generated block is byte-identical",
              outside(after) == outside(before), (outside(after), outside(before)))
        check(name + ": marker count unchanged", after.count(START) == before.count(START)
              and after.count(END) == before.count(END), after)


print("\nE32 — duplicate, nested and reordered markers")
G = START + "\n> Filed by Hermes at 8:00 AM\n\nold\n" + END
scenario("a normal block", "# d\n\nmine above\n\n" + G + "\n\nmine below\n", False,
         ("mine above", "mine below"))
scenario("two well-formed blocks", "a\n" + G + "\nmid\n" + G + "\nz\n", False, ("mid",))
scenario("nested START START END END", "a\n" + START + "\nOWNER1\n" + START + "\ngen\n" + END
         + "\nOWNER2\n" + END + "\nz\n", False, ("OWNER1", "OWNER2"))
scenario("reordered END before START", "a\n" + END + "\nOWNER\n" + START + "\nOWNER2\n", True,
         ("OWNER", "OWNER2"))
scenario("only an END", "a\n" + END + "\nOWNER\n", True, ("OWNER",))
scenario("only a START", "a\n" + START + "\nOWNER\n", True, ("OWNER",))
scenario("no trailing newline, orphan START", "a\n" + START + "\nOWNER no newline", True,
         ("OWNER no newline",))
scenario("START mid-line in the owner's prose", "I typed " + START + " by hand\nOWNER\n" + G + "\n",
         False, ("I typed", "OWNER"))
scenario("CRLF note with an orphan START", "a\r\n" + START + "\r\nOWNER\r\n", True, ("OWNER\r\n",))
scenario("CRLF note with a block", "a\r\nmine\r\n" + START + "\r\nold\r\n" + END + "\r\nafter\r\n",
         False, ("mine\r\n", "after\r\n"))

print("\nE32 (review) — a marker quoted inside a line of prose is the owner's text")
quoted = "the markers are `" + START + "` and `" + END + "` - remember\n"
write(quoted)
rc, out = run("NEW BRIEF")
after = read()
check("an inline START..END quote survives byte-for-byte", after.startswith(quoted), after)
check("...and the brief is appended after it", "NEW BRIEF" in after[len(quoted):], after)


def line_outside(txt):
    """Independent oracle: the owner's lines, i.e. every line not inside a pair
    of lines that are exactly START and END with no START line between."""
    lines = txt.replace("\r\n", "\n").split("\n")
    keep, i = [], 0
    while i < len(lines):
        if lines[i] == START:
            j = i + 1
            while j < len(lines) and lines[j] not in (START, END):
                j += 1
            if j < len(lines) and lines[j] == END:
                i = j + 1
                continue
        keep.append(lines[i])
        i += 1
    return [ln for ln in keep if ln.startswith("OWN")]


print("\nE32 (review) — fuzz: 3,000 random notes, five runs each")
import importlib.util, random
_spec = importlib.util.spec_from_file_location("obsidian_daily_e32", DAILY)
OD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OD)
rng = random.Random(32)
lost, grew, refused = [], [], 0
for n in range(3000):
    parts = []
    for k in range(rng.randint(0, 9)):
        r = rng.random()
        if r < 0.15:
            parts.append(START)
        elif r < 0.3:
            parts.append(END)
        elif r < 0.4:
            parts.append("OWN%d_%d says %s inline" % (n, k, rng.choice([START, END])))
        else:
            parts.append("OWN%d_%d" % (n, k))
    nl = rng.choice(["\n", "\r\n"])
    note = (rng.choice(["", "﻿"]) + nl.join(parts) + rng.choice(["", nl]))
    want = line_outside(note)
    sizes = []
    for run_no in range(5):
        new = OD._refile(note, OD._block({"generated_at": TS, "reply": "B%d" % run_no}))
        if new is None:
            refused += 1
            break
        note = new
        sizes.append(len(note))
        got = line_outside(note)
        it = iter(got)
        if not all(w in it for w in want):
            lost.append((n, note))
            break
    if len(sizes) == 5 and sizes[4] != sizes[1]:
        grew.append(n)
check("no owner line outside a block is ever lost or reordered", not lost, lost[:2])
check("the note reaches a steady size by the second run", not grew, grew[:5])
check("the byte-identity guard never had to refuse", refused == 0, refused)

print("\nE32 — an unreadable note is never rewritten")
with open(NOTE, "wb") as f:
    f.write(b"\xff\xfe not utf-8 " + START.encode() + b"\nx\n" + END.encode() + b"\n")
raw = open(NOTE, "rb").read()
rc, out = run("NEW BRIEF")
check("a note that is not UTF-8 is left byte-for-byte", open(NOTE, "rb").read() == raw, out)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASS[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
