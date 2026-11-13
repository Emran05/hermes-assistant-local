#!/usr/bin/env python3
"""E115 (audit 2026-11-09): "Review & re-trust" signs only the policy the
owner reviewed.

Before: op:"retrust" re-read permissions.json at click time and signed
whatever was there, so a second out-of-band edit landing while the review
dialog was open was signed unseen (its Auto entries went live). The same
held for a Trust-panel save over an untrusted policy, which re-signs the
whole file after the same review.

Now GET /api/permissions carries `sha` (the sha256 of the file the review was
built from), the re-trust request must send it back, and a mismatch is a 409
with nothing signed; a missing sha is a 400. A save over an UNTRUSTED policy
is bound the same way; a save over a trusted one is unchanged.

Python half: the REAL permissions.py on a throwaway HOME, plus the REAL
aux_permissions.py route (the 409 reaches HTTP). JS half: the REAL
aux_trust.js in node sends the sha it was given.

Run:  python3 t_e115_retrust_bound.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %r" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-e115-")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import permissions as P                    # noqa: E402

FORCE = "git force push (rewrites remote history)"


def file_sha():
    with open(P.PERM_FILE, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def hand_edit(fn):
    pol = json.load(open(P.PERM_FILE))
    fn(pol)
    with open(P.PERM_FILE, "w") as f:
        json.dump(pol, f)
    P._STATE["mtime"] = None


def trusted():
    return P._load()[1]


# the real route registrar
ROUTES = {}
g = {"register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
     "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f)}
exec(compile(open(os.path.join(REPO, "dashboard", "aux_permissions.py")).read(),
             "aux_permissions.py", "exec"), g)


class Ctx:
    def __init__(self, body):
        self.body = body


try:
    P.permissions_set({"op": "set_class", "class": "file-perms", "tier": "ask"})
    check("trusted to start", trusted() is True)
    pay = P.permissions_payload()
    check("the payload carries the sha of the file it shows",
          pay.get("sha") == file_sha(), (pay.get("sha"), file_sha()))

    # an out-of-band edit: untrusted, the owner opens the review (GET)
    hand_edit(lambda p: p["classes"].__setitem__("git-destructive", "auto"))
    review = P.permissions_payload()
    check("the edit is untrusted", review["trusted"] is False)
    shown = review["sha"]

    # while the dialog is open a SECOND edit lands
    hand_edit(lambda p: p.setdefault("patterns", {}).__setitem__(FORCE, "auto"))
    r = P.permissions_set({"op": "retrust", "sha": shown})
    check("THE FIX: re-trust of a file that changed since the review is refused 409",
          r.get("ok") is False and r.get("_status") == 409 and r.get("stale") is True, r)
    check("…and nothing was signed (still untrusted)", trusted() is False)
    v = P.decide({"pattern_key": FORCE, "command": "git push --force origin main"})
    check("…so the unseen Auto is still suspended", v["tier"] == "ask", v)

    status, body = None, None
    out = ROUTES[("POST", "/api/permissions")](Ctx({"op": "retrust", "sha": shown}))
    check("the route answers HTTP 409", isinstance(out, tuple) and out[1] == 409, out)

    for bad in ({"op": "retrust"}, {"op": "retrust", "sha": None},
                {"op": "retrust", "sha": 123}, {"op": "retrust", "sha": ""}):
        r = P.permissions_set(bad)
        check("re-trust without the reviewed sha is refused: %s" % json.dumps(bad),
              r.get("ok") is False and r.get("_status") in (400, 409), r)
    check("…and nothing was signed", trusted() is False)

    # re-review: now the sha matches and the re-trust signs exactly that file
    review2 = P.permissions_payload()
    r = P.permissions_set({"op": "retrust", "sha": review2["sha"]})
    check("re-trust with the sha of the reviewed file succeeds", r.get("ok") is True, r)
    check("…and the file is trusted", trusted() is True)
    check("…the signature is over the reviewed bytes",
          open(P.SIDECAR).read().strip() == review2["sha"])

    # an untrusted save is bound the same way
    hand_edit(lambda p: p["classes"].__setitem__("process-control", "auto"))
    shown = P.permissions_payload()["sha"]
    hand_edit(lambda p: p["classes"].__setitem__("container-lifecycle", "auto"))
    for body in ({"op": "set_class", "class": "file-perms", "tier": "never", "sha": shown},
                 {"op": "set_pattern", "pattern": FORCE, "tier": "never", "sha": shown},
                 {"op": "clear_pattern", "pattern": FORCE, "sha": shown},
                 {"op": "set_class", "class": "file-perms", "tier": "never"}):
        r = P.permissions_set(body)
        check("a save over an untrusted file that changed since review is refused: %s"
              % body["op"], r.get("ok") is False and r.get("_status") in (400, 409), r)
    check("…nothing signed", trusted() is False)
    cur = P.permissions_payload()["sha"]
    r = P.permissions_set({"op": "set_class", "class": "file-perms", "tier": "never", "sha": cur})
    check("…with the current sha it saves (and re-signs)", r.get("ok") is True and trusted(), r)

    # a trusted policy: saves need no sha (unchanged behaviour)
    r = P.permissions_set({"op": "set_class", "class": "file-perms", "tier": "ask"})
    check("a save over a trusted policy needs no sha", r.get("ok") is True, r)

    # no file at all: the empty sha re-trusts (nothing to sign)
    os.remove(P.PERM_FILE)
    P._STATE["mtime"] = None
    pay = P.permissions_payload()
    check("no policy file: sha is empty", pay.get("sha") == "", pay.get("sha"))
    r = P.permissions_set({"op": "retrust", "sha": ""})
    check("…and re-trust with it is ok (nothing to sign)", r.get("ok") is True, r)
    check("…and writes no policy file", not os.path.exists(P.PERM_FILE))

    # --- aux_trust.js sends the sha it was shown --------------------------
    hand_edit_src = {"classes": {"git-destructive": "auto"}, "patterns": {}}
    with open(P.PERM_FILE, "w") as f:
        json.dump(hand_edit_src, f)
    P._STATE["mtime"] = None
    payload = P.permissions_payload()
    js = r"""
const fs = require("fs");
const { indexFunction } = require(process.argv[1]);
const posts = [];
let mounted = null;
function el(tag) {
  const e = { tag, _html: "", id: "", className: "", listeners: {}, disabled: false, attrs: {},
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; this._q = {}; },
    getAttribute(k) { return this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-trust") mounted = c; return c; },
    remove() {}, classList: { contains() { return false; }, add() {}, remove() {} },
    querySelector(sel) {
      if (sel === "#tp-retrust" && /id="tp-retrust"/.test(this._html)) return (this._rt ||= el("button"));
      return null;
    },
    querySelectorAll() { return []; },
  };
  return e;
}
const grid = el("div");
global.window = global;
global.document = { getElementById(id) { return id === "view-mind" ? grid : (id === "trust-css" ? {} : null); },
  createElement: el, head: el("head") };
global.esc = indexFunction("esc");
global.REDUCE = true;
global.confirm = () => true;
const payload = JSON.parse(process.argv[3]);
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") { posts.push(JSON.parse(opt.body)); return { json: async () => ({ ok: true }) }; }
  return { json: async () => payload };
};
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await window.trustPanel();
  const rt = mounted && mounted.querySelector("#tp-retrust");
  if (rt && rt.listeners.click) await rt.listeners.click();
  process.stdout.write(JSON.stringify(posts));
})().catch(e => process.stdout.write(JSON.stringify({ error: String(e) })));
"""
    out = subprocess.run(["node", "-e", js,
                          os.path.join(REPO, "tools", "tests", "lib", "index_helpers.js"),
                          os.path.join(REPO, "dashboard", "aux_trust.js"), json.dumps(payload)],
                         capture_output=True, text=True, timeout=60)
    try:
        posts = json.loads(out.stdout or "null")
    except ValueError:
        posts = None
    check("UI: Review & re-trust posts the sha of the payload it reviewed",
          posts == [{"op": "retrust", "sha": payload["sha"]}], (posts, out.stderr[-400:]))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
