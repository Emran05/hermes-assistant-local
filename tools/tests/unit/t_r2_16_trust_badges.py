#!/usr/bin/env python3
"""R2-16 (round 2 of the 2026-10-21 audit): every action the approval path
writes to the audit log gets a named badge in the Trust panel.

hermes_rpc.py's approval queue writes `expired`, `timeout`, `desync-denied`
and `respond-failed` next to the older auto-approved / asked / user-* set,
but aux_trust.js's ACT_BADGE only knew the older set, so "Recent decisions"
showed those four as their raw ids on a muted, unnamed badge. And
permissions.audit()'s docstring still listed the old vocabulary.

The action strings are read out of the REAL hermes_rpc.py and
aux_shortcuts.py source (so a new one added there fails this test until it
has a badge), each is rendered through the REAL aux_trust.js in node over a
minimal fake DOM, and the badge must carry a label that is not the raw id.

Run:  python3 t_r2_16_trust_badges.py
"""
import ast
import json
import os
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


HARNESS = r"""
const fs = require("fs");
let mounted = null;
function el(tag) {
  return { tag, innerHTML: "", id: "", className: "", listeners: {}, attrs: {},
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-trust") mounted = c; return c; },
    remove() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
}
const grid = el("div");
global.window = global;
global.document = {
  getElementById(id) { return id === "view-mind" ? grid : (id === "trust-css" ? {} : null); },
  createElement: el, head: el("head"),
};
// esc() is the REAL one, cut out of index.html's inline script (E138: a stand-in
// tests a different escaping from the one the dashboard ships)
global.esc = require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
const actions = JSON.parse(process.argv[3]);
let payload = null;
global.fetch = async () => ({ json: async () => payload });
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  const out = {};
  for (const a of actions) {               // one render per action: recent shows 8
    payload = { ok: true, trusted: true, exists: true, classes: [],
                recent: [{ ts: 1.8e9, action: a, pattern_key: "k-" + a, command: "" }] };
    mounted = null;
    await window.trustPanel();
    const html = mounted ? mounted.innerHTML : "";
    const m = /<span class="tp-badge" style="--bd:var\(([^)]*)\)">([^<]*)<\/span>/.exec(html);
    out[a] = m ? { color: m[1], label: m[2] } : null;
  }
  process.stdout.write(JSON.stringify(out));
})().catch(e => process.stdout.write(JSON.stringify({ error: String(e) })));
"""


# where the action sits in each audit helper's positional arguments
_ACTION_ARG = {"_audit": 1, "_forget_all": 0, "_sb_audit": 4}


def _strings(node):
    """Every action string an argument expression can evaluate to."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _strings(node.body) | _strings(node.orelse)
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
            and isinstance(node.left, ast.Constant) and node.left.value == "user-"):
        return {"user-approve", "user-deny"}   # "user-" + choice, choice in approve|deny
    return set()


def _actions_in(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    acts = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        i = _ACTION_ARG.get(name)
        if i is not None and len(n.args) > i:
            acts |= _strings(n.args[i])
    return acts


print("\n== the action vocabulary the approval path writes ==")
rpc = _actions_in(os.path.join(DASH, "hermes_rpc.py"))
bus = _actions_in(os.path.join(DASH, "aux_shortcuts.py"))
NEW = {"expired", "timeout", "desync-denied", "respond-failed"}
check("hermes_rpc.py writes the four newer actions (the finding)", NEW <= rpc, sorted(rpc))
ALL = sorted(rpc | bus | {"policy-change"})
print("  actions: %s" % ", ".join(ALL))

print("\n== aux_trust.js names every one ==")
tmp = tempfile.mkdtemp(prefix="hermes-r216-")
try:
    hp = os.path.join(tmp, "h.js")
    open(hp, "w").write(HARNESS)
    p = subprocess.run(["node", hp, os.path.join(DASH, "aux_trust.js"), json.dumps(ALL)],
                       capture_output=True, text=True, timeout=60)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
try:
    got = json.loads(p.stdout or "{}")
except ValueError:
    got = {"error": (p.stdout + p.stderr)[-400:]}
check("harness ran", "error" not in got, got.get("error"))
for a in ALL:
    b = got.get(a) or {}
    check("%s renders a named badge (%r)" % (a, b.get("label")),
          bool(b.get("label")) and b.get("label") != a, b)
check("a delivery failure and an out-of-sync denial read as failures, not neutral",
      (got.get("respond-failed") or {}).get("color") == "--bad"
      and (got.get("desync-denied") or {}).get("color") == "--bad", got)
check("an unanswered timeout is not shown as a plain grey entry",
      (got.get("timeout") or {}).get("color") not in (None, "--muted"), got.get("timeout"))

print("\n== permissions.audit() documents the same vocabulary ==")
HOME = tempfile.mkdtemp(prefix="hermes-r216-home-")
os.environ["HOME"] = HOME
sys.path.insert(0, DASH)
try:
    import permissions as P   # noqa: E402
    doc = P.audit.__doc__ or ""
    for a in sorted(rpc):
        check("the docstring lists %s" % a, a in doc, doc[:200])
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
