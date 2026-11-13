#!/usr/bin/env python3
"""Review R4-seams: the three copies of "is this labeled value a credential?"
agree.

aux_convos' _CV_SECRET_RE is a broad OUTPUT redactor (`token: <anything>`).
Three callers that must REFUSE or WARN rather than redact narrow it with the
same test of the value — a digit, a symbol or an auth scheme word, or 16+
characters that are not a dotted identifier:

  * aux_memlayer._ml_looks_secret   (nested `value_is_secret`): a fact is not stored
  * hermes_mcp._secret_value        : memory_facts(add=) is refused before the round trip
  * aux_ghchanges._ghc_value_is_secret : the C-secret-shape warning on a GitHub change

Each says "change both or neither" about the others, and nothing compared
them: a rule relaxed in one let a credential through that surface only. This
suite runs all three on the same matches of the real _CV_SECRET_RE — a fixed
table and a seeded sweep — and they must answer alike.

The functions are cut out of their files by AST and run on their own; no
dashboard, no store, no network.
"""
import ast
import os
import random
import re
import sys

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
DASH = os.path.join(ROOT, "dashboard")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


def cut(path, name, inside=None):
    """The source of top-level function `name` (or of `name` nested directly in
    top-level function `inside`), compiled alone."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    body = tree.body
    if inside:
        outer = next((n for n in body if isinstance(n, ast.FunctionDef) and n.name == inside), None)
        body = outer.body if outer else []
    node = next((n for n in body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if node is None:
        return None
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name]


def regex():
    with open(os.path.join(DASH, "aux_convos.py"), encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "_CV_SECRET_RE")
    ns = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "aux_convos.py", "exec"), ns)
    return ns["_CV_SECRET_RE"]


mem = cut(os.path.join(DASH, "aux_memlayer.py"), "value_is_secret", inside="_ml_looks_secret")
mcp = cut(os.path.join(DASH, "hermes_mcp.py"), "_secret_value")
ghc = cut(os.path.join(DASH, "aux_ghchanges.py"), "_ghc_value_is_secret")
check("aux_memlayer._ml_looks_secret still nests value_is_secret", callable(mem))
check("hermes_mcp._secret_value exists", callable(mcp))
check("aux_ghchanges._ghc_value_is_secret exists", callable(ghc))
RX = regex()
check("aux_convos._CV_SECRET_RE has the value group the three read", RX.groups >= 2, RX.groups)
if not (callable(mem) and callable(mcp) and callable(ghc)):
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1)


def verdicts(text):
    out = []
    for m in RX.finditer(text):
        out.append((m.group(0), bool(mem(m)), bool(mcp(m)), bool(ghc(m))))
    return out


print("\n== a fixed table: what is a credential, and what is a word ==")
TABLE = [
    ("token: monthly", False), ("password: required", False), ("secret = manager", False),
    ("token = tokenizer.encode", False), ("api_key: os.environ", False),
    ("password: hunter22xyz", True), ("token: a/b-c9", True), ("secret=correct-horse-battery", True),
    ("password: CorrectHorseBatteryStaple", True), ("password: CorrectHorseBatteryStaple.", True),
    ("token: Bearer abcdefghijkl", True), ("api_key: AAAAAAAAAAAAAAAA", True),
    ("api_key: AAAAAAAAAAAAAAA", False), ("passcode: 493817", True),
]
for text, want in TABLE:
    v = verdicts(text)
    check("%-44s -> all three say %s" % (text, want),
          bool(v) and all(row[1:] == (want, want, want) for row in v), v)

print("\n== a seeded sweep over the real regex's matches ==")
rnd = random.Random(20261110)
LABELS = ["token", "password", "passwd", "secret", "api_key", "apikey", "api-key", "passphrase",
          "passcode", "authorization", "access_token", "client_secret"]
SEPS = [": ", "=", " = ", ":", ": Bearer ", "= Basic ", ': "', "='"]
ALPH = ["abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "._", "-/+=", "@!#"]
n = bad = 0
first = None
for _ in range(6000):
    kinds = rnd.sample(ALPH, rnd.randint(1, 3))
    val = "".join(rnd.choice(rnd.choice(kinds)) for _i in range(rnd.randint(1, 26)))
    text = "%s%s%s%s" % (rnd.choice(LABELS), rnd.choice(SEPS), val, rnd.choice(["", ".", " and more", '"']))
    for row in verdicts(text):
        n += 1
        if not (row[1] == row[2] == row[3]):
            bad += 1
            first = first or (text, row)
check("the sweep exercised the regex (%d matches)" % n, n > 3000, n)
check("memory check, MCP and GitHub check answered alike on every match", bad == 0, (bad, first))


class NoGroup(object):
    """A match from a regex with no value group (a stub, a rewrite)."""
    def group(self, i=0):
        raise IndexError(i)

    def start(self, i=0):
        raise IndexError(i)

    def end(self, i=0):
        raise IndexError(i)


check("a match with no value group fails closed in all three",
      mem(NoGroup()) is True and mcp(NoGroup()) is True and ghc(NoGroup()) is True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
