// V01 (docs/audits/2026-11-12-visual-audit.md) — the Data & Network card's
// table was unreadable: five columns in a 673 px panel left When and What
// 82/112 px wide while one nowrap "off" line held the Switch column at
// 272 px. The card now lays each row out as a grid (CSS only, measured in the
// capture tool). A table whose parts are not display:table-* loses its table
// semantics in WebKit, so the card puts explicit ARIA table roles on the SAME
// elements after it renders. This suite runs the REAL aux_onboarding.js +
// aux_network.js on the mini DOM and checks those roles, and that the shared
// markup the first-run sheet uses is unchanged.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const T = harness("V01 network table roles");

(async function () {
  const w = createWindow({ html: "<!doctype html><html><head></head><body>" +
    '<div id="view-mind"><section id="sec-connections"><div class="set-body"></div></section></div>' +
    "</body></html>" });
  w.__routes = {
    "/api/watchtower": { ok: true, master: { briefings: true, news: true }, recent: [] },
    "/api/claude/foryou": { ok: true, enabled: false },
    "/api/claude/escalate": { ok: true, enabled: false },
    "/api/network/last": { ok: true, last: {} }
  };
  for (const rel of ["dashboard/aux_onboarding.js", "dashboard/aux_network.js"]) {
    w.__run(fs.readFileSync(path.join(REPO, rel), "utf8"), rel);
  }
  await w.hermesNetwork.mount();
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  const d = w.document;
  const card = d.getElementById("mind-extra-network");
  const t = card && card.querySelector("table.onb-net");
  T.check("the card rendered its table", !!t);
  if (!t) return T.done();
  const facts = w.hermesOnboarding.NETWORK_FACTS || [];
  T.check("the table has role=table", t.getAttribute("role") === "table", t.getAttribute("role"));
  const groups = card.querySelectorAll("table.onb-net thead, table.onb-net tbody");
  T.check("thead and tbody are rowgroups", groups.length === 2 &&
    Array.from(groups).every((g) => g.getAttribute("role") === "rowgroup"));
  const trs = Array.from(card.querySelectorAll("table.onb-net tr"));
  T.check("every row (header + one per fact) has role=row",
    trs.length === facts.length + 1 && trs.every((r) => r.getAttribute("role") === "row"), trs.length);
  const ch = Array.from(card.querySelectorAll("table.onb-net thead th"));
  T.check("the five column headers are columnheaders",
    ch.length === 5 && ch.every((h) => h.getAttribute("role") === "columnheader"),
    ch.map((h) => h.getAttribute("role")));
  const rh = Array.from(card.querySelectorAll("table.onb-net tbody th"));
  T.check("each row's 'Goes to' is a rowheader",
    rh.length === facts.length && rh.every((h) => h.getAttribute("role") === "rowheader"));
  const tds = Array.from(card.querySelectorAll("table.onb-net td"));
  T.check("every data cell is role=cell (4 per row)",
    tds.length === facts.length * 4 && tds.every((c) => c.getAttribute("role") === "cell"), tds.length);
  T.check("the switches are still live checkboxes in the table",
    card.querySelectorAll("table.onb-net input[type=checkbox]").length ===
      facts.filter((f) => f.toggle).length);

  // the shared markup (what the first-run sheet renders) carries no roles:
  // the sheet stays a real display:table and needs none
  const sheet = w.hermesOnboarding.networkTableHTML({ prefs: {}, inputs: true });
  T.check("networkTableHTML() itself is unchanged (no role attributes)", !/role=/.test(sheet));

  // the card's CSS: rows become grids, the Switch column is a fixed track
  const css = w.hermesNetwork.CSS();
  T.check("card CSS lays rows out as a grid with a fixed Switch track",
    /tbody tr\{display:grid;grid-template-columns:minmax\(0,1fr\) minmax\(0,1\.3fr\) 132px/.test(css));
  T.check("card CSS never lets the table scroll sideways",
    /\.onb-tablewrap\{position:relative;overflow-x:visible\}/.test(css));
  T.done();
})().catch((e) => { console.log("  FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
