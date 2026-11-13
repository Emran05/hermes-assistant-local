#!/usr/bin/env python3
"""C126 follow-up (round 2 of the 2026-10-21 audit): Clear removes the key it
was shown for, a padded key is flagged, and "Review & re-trust" shows a DIFF.

Three gaps the first C126 pass left:
  * clear_pattern stripped the key it was sent, so a hand-edited
    " recursive delete " (stray spaces) got a Clear button that popped
    "recursive delete" — nothing — and saved anyway, re-signing the file;
  * that padded key is inert (no approval ever carries it, so decide() never
    matches it) yet the panel listed it as a live override of its class;
  * the re-trust review listed the WHOLE policy, so the one line an outside
    edit added was buried among everything the owner had already approved.

Python half: the REAL permissions.py against a throwaway HOME.  JS half: the
REAL aux_trust.js in node over a minimal fake DOM, fed the real payload.
Fails on the old code: the padded Clear leaves the key, a missing key answers
ok, there is no signed copy / `signed` view, and the review lists an
unchanged, already-signed class.

Run:  python3 t_c126_retrust_diff.py
"""
import hashlib
import json
import os
import shutil
import stat
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


HOME = tempfile.mkdtemp(prefix="hermes-c126b-")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import permissions as P   # noqa: E402

FORCE = "git force push (rewrites remote history)"
RMRF = "recursive delete"
PADDED = " recursive delete "
SQL = "SQL DROP"

HARNESS = r"""
const fs = require("fs");
const posts = [], confirms = [];
let mounted = null;
function el(tag) {
  return { tag, innerHTML: "", id: "", className: "", listeners: {}, attrs: {},
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-trust") mounted = c; return c; },
    remove() {},
    querySelector(sel) {
      if (sel === "#tp-retrust" && /id="tp-retrust"/.test(this.innerHTML)) {
        if (!this._rt) this._rt = el("button");
        return this._rt;
      }
      return null;
    },
    querySelectorAll() { return []; } };
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
global.confirm = m => { confirms.push(m); return false; };
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") posts.push(JSON.parse(opt.body));
  return { json: async () => payload };
};
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await window.trustPanel();
  const out = { html: mounted ? mounted.innerHTML : "" };
  const rt = mounted && mounted.querySelector("#tp-retrust");
  if (rt && rt.listeners.click) await rt.listeners.click();
  out.msg = confirms[0] || "";
  out.posts = posts;
  process.stdout.write(JSON.stringify(out));
})().catch(e => process.stdout.write(JSON.stringify({ error: String(e) })));
"""


def run_js(payload):
    tmp = tempfile.mkdtemp(prefix="hermes-c126b-js-")
    try:
        hp, cp = os.path.join(tmp, "h.js"), os.path.join(tmp, "p.json")
        open(hp, "w").write(HARNESS)
        open(cp, "w").write(json.dumps(payload))
        p = subprocess.run(["node", hp, os.path.join(REPO, "dashboard", "aux_trust.js"), cp],
                           capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        return json.loads(p.stdout or "{}")
    except ValueError:
        return {"error": (p.stdout + p.stderr)[-400:]}


def hand_edit(mut):
    pol = json.load(open(P.PERM_FILE))
    mut(pol)
    with open(P.PERM_FILE, "w") as f:
        json.dump(pol, f)
    st = os.stat(P.PERM_FILE)        # a same-second rewrite must still re-read
    os.utime(P.PERM_FILE, (st.st_atime, st.st_mtime + 5))


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def pats():
    return json.load(open(P.PERM_FILE)).get("patterns", {})


try:
    print("\n== Clear removes exactly the key it was shown for ==")
    P.permissions_set({"op": "set_pattern", "pattern": FORCE, "tier": "auto"})
    hand_edit(lambda p: p["patterns"].__setitem__(PADDED, "never"))
    P.permissions_set({"op": "retrust", "sha": P.permissions_payload()["sha"]})
    pay = P.permissions_payload()
    by = {o["pattern"]: o for o in pay.get("pattern_overrides") or []}
    row = by.get(PADDED, {})
    check("the padded key is listed (so it can be cleared)", bool(row), sorted(by))
    check("…flagged invalid on load", row.get("invalid") == "whitespace", row)
    check("…with its class's tier as what really runs",
          row.get("class") == "destructive-delete" and row.get("effective") == "ask"
          and "never matches" in (row.get("clamped_by") or ""), row)
    check("decide() indeed ignores it (destructive-delete stays Ask)",
          P.decide({"pattern_key": RMRF})["tier"] == "ask")
    check("a clean key is not flagged", "invalid" not in by.get(FORCE, {}), by.get(FORCE))

    r = P.permissions_set({"op": "clear_pattern", "pattern": PADDED})
    check("clearing the padded key answers ok", r.get("ok") is True, r)
    check("…and the padded key is gone (the finding: it used to stay)",
          PADDED not in pats() and FORCE in pats(), pats())

    before = sha(P.PERM_FILE)
    r = P.permissions_set({"op": "clear_pattern", "pattern": "no such pattern"})
    check("clearing a key that is not stored is a 404, not ok",
          r.get("ok") is False and r.get("_status") == 404, r)
    check("…and does not rewrite (re-sign) the file", sha(P.PERM_FILE) == before)

    r = P.permissions_set({"op": "clear_pattern", "pattern": "  " + FORCE + " "})
    check("a padded copy of a clean key still clears it (API callers)",
          r.get("ok") is True and FORCE not in pats(), (r, pats()))

    # review C126: a key of spaces only is listed invalid with a Clear button
    # too, and Clear used to answer 400 "empty pattern" for it
    hand_edit(lambda p: p["patterns"].__setitem__("   ", "auto"))
    P.permissions_set({"op": "retrust", "sha": P.permissions_payload()["sha"]})
    row = {o["pattern"]: o for o in P.permissions_payload()["pattern_overrides"]}.get("   ", {})
    check("a spaces-only key is listed, flagged invalid", row.get("invalid") == "whitespace", row)
    r = P.permissions_set({"op": "clear_pattern", "pattern": "   "})
    check("…and Clear removes it (it was a 400)", r.get("ok") is True and "   " not in pats(),
          (r, pats()))
    r = P.permissions_set({"op": "clear_pattern", "pattern": "  "})
    check("a blank pattern with no such key is still a 400",
          r.get("ok") is False and r.get("_status") == 400, r)

    print("\n== every signature keeps a signed copy ==")
    P.permissions_set({"op": "set_class", "class": "git-destructive", "tier": "never"})
    P.permissions_set({"op": "set_pattern", "pattern": SQL, "tier": "never"})
    P.permissions_set({"op": "set_pattern", "pattern": FORCE, "tier": "auto"})
    sc = getattr(P, "SIGNED_COPY", "")
    check("the signed copy exists", bool(sc) and os.path.exists(sc), sc)
    if sc and os.path.exists(sc):
        check("…mode 0600", stat.S_IMODE(os.stat(sc).st_mode) == 0o600)
        check("…and is exactly what the sidecar signed",
              sha(sc) == open(P.SIDECAR).read().strip())
    check("a trusted payload carries no signed view", "signed" not in P.permissions_payload())

    # an out-of-band edit: one class loosened to Auto, one pattern added,
    # one signed pattern removed; Git (signed Never) and FORCE (signed Auto)
    # are untouched
    def edit(p):
        p["classes"]["process-control"] = "auto"
        p["patterns"][RMRF + " (long flag)"] = "never"
        del p["patterns"][SQL]
    hand_edit(edit)
    pay = P.permissions_payload()
    sv = pay.get("signed") or {}
    check("the hand edit is untrusted", pay["trusted"] is False)
    check("the payload carries the last signed policy",
          sv.get("patterns") == {SQL: "never", FORCE: "auto"}
          and (sv.get("classes") or {}).get("process-control") == "ask"
          and (sv.get("classes") or {}).get("git-destructive") == "never"
          and len(sv.get("classes") or {}) == len(P.CLASS_ORDER), sv)

    print("\n== the review is the diff ==")
    js = run_js(pay)
    check("harness ran", "error" not in js, js.get("error"))
    msg = js.get("msg", "")
    check("declining sends nothing", js.get("posts") == [], js.get("posts"))
    check("the loosened class is listed as a change",
          "Process & service control: Ask → Auto" in msg, msg)
    check("the added pattern is listed as new", "(long flag)”" in msg and "(new)" in msg, msg)
    check("the removed pattern is listed as removed",
          "“%s”: removed (was Never)" % SQL in msg, msg)
    check("an unchanged, already-signed class is NOT listed as a change (it was)",
          "Git history" not in msg.split("Auto-run also resumes")[0], msg)
    check("the already-signed Auto is named as resuming, not as a change",
          "Auto-run also resumes" in msg and FORCE in msg.split("Auto-run also resumes")[-1],
          msg)
    check("it says what it compared against", "last policy the dashboard signed" in msg, msg)
    check("the Auto count covers the changes only (1)", "including 1 Auto" in msg, msg)

    print("\n== the panel shows a padded key as ignored ==")
    hand_edit(lambda p: p["patterns"].__setitem__(" " + FORCE, "never"))
    pay = P.permissions_payload()
    js = run_js(pay)
    html = js.get("html", "")
    prow = html.split('data-invalid="1"', 1)[-1].split("tp-prow", 1)[0] if 'data-invalid="1"' in html else ""
    check("the padded row is marked invalid and badged Ignored",
          bool(prow) and ">Ignored<" in prow and "never matches" in prow, html[-700:])
    git_row = html.split('data-class="git-destructive"', 1)[-1].split('class="tp-row"', 1)[0]
    check("…and does not count as a Git override (only FORCE does)",
          "1 pattern set differently" in git_row, git_row[:600])

    # review C126: the review dialog used to count a padded Auto key as an
    # Auto that "runs without asking", though decide() never matches it
    hand_edit(lambda p: p["patterns"].__setitem__(" " + FORCE, "auto"))
    msg = run_js(P.permissions_payload()).get("msg", "")
    check("the review names a padded Auto key as ignored",
          ("“ %s”" % FORCE) in msg and "ignored, never matches" in msg, msg)
    check("…and does not count it as an Auto that runs (still 1: process-control)",
          "including 1 Auto" in msg, msg)

    print("\n== no copy, or a stale one: the whole policy, as before ==")
    os.remove(P.SIGNED_COPY)
    check("a missing copy omits the signed view", "signed" not in P.permissions_payload())
    js = run_js(P.permissions_payload())
    check("…and the review falls back to the whole policy",
          "Git history" in js.get("msg", "") and "last policy the dashboard signed"
          not in js.get("msg", ""), js.get("msg"))
    open(P.SIGNED_COPY, "w").write('{"patterns": {}}')
    check("a copy the sidecar did not sign is ignored", "signed" not in P.permissions_payload())

    print("\n== a policy signed before the copy existed gets one on load ==")
    P.permissions_set({"op": "retrust", "sha": P.permissions_payload()["sha"]})
    os.remove(P.SIGNED_COPY)
    P._STATE["mtime"] = None
    P._load()
    check("the first trusted load writes the copy",
          os.path.exists(P.SIGNED_COPY) and sha(P.SIGNED_COPY) == sha(P.PERM_FILE))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
