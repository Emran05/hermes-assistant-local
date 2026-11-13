#!/usr/bin/env python3
"""C126 (audit 2026-10-21): the Trust panel shows pattern-level overrides, and
"Review & re-trust" shows what it is about to bless.

decide() resolves a pattern's own tier BEFORE its class tier, and five classes
(git-destructive among them) have no floor, so
{"git force push (rewrites remote history)": "auto"} auto-approves a force push
while the Git row still reads "Ask". GET /api/permissions returned `patterns`
but aux_trust.js never read it, and the untrusted banner's only button POSTed
`retrust` the moment it was clicked.

Python half: the REAL permissions.py (imported against a throwaway HOME) —
its payload carries one display row per pattern tier with its class and what
decide() will actually do with it (floor and untrusted clamps included).
JS half: the REAL aux_trust.js in node with a minimal fake DOM, fed that
payload — the override is rendered with a Clear button, the class row is
marked custom, and the re-trust button confirms first, listing the Auto.

Run:  python3 t_c126_trust_pattern_overrides.py
"""
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
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-c126-")
os.environ["HOME"] = HOME                 # permissions.py resolves ~ at import
sys.path.insert(0, os.path.join(REPO, "dashboard"))
import permissions as P                   # noqa: E402

FORCE = "git force push (rewrites remote history)"
RMRF = "recursive delete"

HARNESS = r"""
const fs = require("fs");
const posts = [], confirms = [];
let confirmAnswer = false, mounted = null;
function el(tag) {
  const e = { tag, innerHTML: "", id: "", className: "", listeners: {}, disabled: false,
    attrs: {},
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
    querySelectorAll(sel) {
      if (sel === "[data-pclear]") {
        if (!this._pc) {
          this._pc = [];
          const re = /data-pclear="(\d+)"/g; let m;
          while ((m = re.exec(this.innerHTML))) {
            const b = el("button"); b.attrs["data-pclear"] = m[1]; this._pc.push(b);
          }
        }
        return this._pc;
      }
      return [];
    },
  };
  return e;
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
global.confirm = m => { confirms.push(m); return confirmAnswer; };
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") posts.push(JSON.parse(opt.body));
  return { json: async () => payload };
};
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  const out = {};
  await window.trustPanel();
  const card = mounted;
  out.html = card ? card.innerHTML : "";
  const rt = card && card.querySelector("#tp-retrust");
  out.has_retrust = !!rt;
  if (rt && rt.listeners.click) {
    confirmAnswer = false;
    await rt.listeners.click();
    out.posts_after_decline = posts.slice();
    out.confirm_msg = confirms[0] || "";
    confirmAnswer = true;
    await rt.listeners.click();
    out.posts_after_accept = posts.slice();
  }
  posts.length = 0;
  const pcs = card ? card.querySelectorAll("[data-pclear]") : [];
  out.clear_buttons = pcs.length;
  if (pcs.length && pcs[0].listeners.click) {
    await pcs[0].listeners.click();
    out.clear_posts = posts.slice();
  }
  process.stdout.write(JSON.stringify(out));
})().catch(e => { process.stdout.write(JSON.stringify({ error: String(e) })); });
"""

try:
    print("\n== permissions.py: one display row per pattern tier ==")
    r = P.permissions_set({"op": "set_pattern", "pattern": FORCE, "tier": "auto"})
    check("a pattern-level auto on an unfloored class is accepted (the finding)",
          r.get("ok") is True, r)
    pay = P.permissions_payload()
    git = [c for c in pay["classes"] if c["id"] == "git-destructive"][0]
    check("…while its class row still reads Ask", git["tier"] == "ask", git["tier"])
    rows = pay.get("pattern_overrides")
    row = (rows or [{}])[0]
    check("the payload lists it with its class and what decide() will do",
          isinstance(rows, list) and row.get("pattern") == FORCE
          and row.get("class") == "git-destructive" and row.get("tier") == "auto"
          and row.get("effective") == "auto" and row.get("class_tier") == "ask", rows)
    check("…which is exactly what decide() does",
          P.decide({"pattern_key": FORCE,
                    "command": "git push --force origin main"})["tier"] == "auto")

    # an out-of-band edit: a floored pattern set to auto, by hand
    pol = json.load(open(P.PERM_FILE))
    pol["patterns"][RMRF] = "auto"
    with open(P.PERM_FILE, "w") as f:
        json.dump(pol, f)
    pay = P.permissions_payload()
    by = {o["pattern"]: o for o in pay.get("pattern_overrides") or []}
    check("a hand edit is untrusted and every auto shows as clamped to ask",
          pay["trusted"] is False and by.get(FORCE, {}).get("effective") == "ask"
          and "re-trusted" in by.get(FORCE, {}).get("clamped_by", ""), by.get(FORCE))
    check("a floored class's auto names the safety floor",
          by.get(RMRF, {}).get("effective") == "ask"
          and by.get(RMRF, {}).get("clamped_by") == "safety floor", by.get(RMRF))

    print("\n== aux_trust.js renders and guards it ==")
    tmp = tempfile.mkdtemp(prefix="hermes-c126-js-")
    hp, cp = os.path.join(tmp, "h.js"), os.path.join(tmp, "p.json")
    open(hp, "w").write(HARNESS)
    open(cp, "w").write(json.dumps(pay))
    p = subprocess.run(["node", hp, os.path.join(REPO, "dashboard", "aux_trust.js"), cp],
                       capture_output=True, text=True, timeout=60)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        js = json.loads(p.stdout or "{}")
    except Exception:
        js = {"error": p.stdout[-300:] + p.stderr[-300:]}
    check("harness ran", "error" not in js, js.get("error"))
    html = js.get("html", "")
    check("every pattern override is rendered", "Pattern overrides" in html
          and FORCE in html and RMRF in html, html[-600:])
    check("each one has a Clear button", js.get("clear_buttons") == 2, js.get("clear_buttons"))
    check("the Git class row is marked custom",
          'data-class="git-destructive"' in html and
          'class="tp-cust"' in html.split('data-class="git-destructive"')[1].split('class="tp-row"')[0],
          "")
    check("the header counts the custom entries", "2 custom" in html, "")
    check("declining the re-trust review sends nothing",
          js.get("has_retrust") and js.get("posts_after_decline") == [],
          js.get("posts_after_decline"))
    msg = js.get("confirm_msg", "")
    check("the review lists the Auto pattern by name", FORCE in msg and "Auto" in msg, msg)
    check("accepting it re-trusts", js.get("posts_after_accept") == [{"op": "retrust", "sha": pay.get("sha")}],
          js.get("posts_after_accept"))
    check("Clear removes that one pattern",
          (js.get("clear_posts") or [{}])[0].get("op") == "clear_pattern"
          and (js.get("clear_posts") or [{}])[0].get("pattern") in (FORCE, RMRF),
          js.get("clear_posts"))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
