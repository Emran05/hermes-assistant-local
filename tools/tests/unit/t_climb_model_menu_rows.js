// Model menu rows (design climb 2026-11-12): one title line (name, badges,
// and the action or state word on that same line), one spec line whose full
// text is also in Details, warnings kept on the row, an action that looks
// like a button and a state that does not, and the model in use (running or
// asleep) being the row keyboard focus lands on when the menu opens.
//
// Runs the REAL index.html in the mini DOM (_a11y_dom.js); no dashboard.
"use strict";
const { bootDashboard, DEFAULT_MODELS, harness } = require("./_a11y_dom.js");
const T = harness();

function fixture() {
  const f = JSON.parse(JSON.stringify(DEFAULT_MODELS));
  f.idle_suspended = true;                    // the model in use is asleep
  f.models = [
    { id: "a/First-9B", label: "First 9B", downloaded: true, ram: 7, fit: "ok", download_gb: 6, note: "background lane · fast" },
    { id: "a/Unc-27B", label: "Unc 27B", active: true, downloaded: true, ram: 19, fit: "ok", download_gb: 17,
      uncensored: true, verified: false, note: "no refusals · abliterated 27B · MTP ~2x" },
    { id: "a/Tight-35B", label: "Tight 35B", downloaded: false, ram: 46, fit: "tight", download_gb: 20, note: "MoE" },
    { id: "a/Huge-70B", label: "Huge 70B", downloaded: false, ram: 60, fit: "no", download_gb: 40 },
    // round 3: a verified:false row with a passing drill record shows ONE state
    { id: "a/Drilled-27B", label: "Drilled 27B", downloaded: true, ram: 19, fit: "ok", download_gb: 17,
      uncensored: true, verified: false, drilled: true, drill_pass: true, drill_score: 6, drill_of: 6,
      license_note: "Apache-2.0", note: "no refusals · abliterated 27B · 4-bit subfolder · unverified until drilled" },
    { id: "a/Failed-9B", label: "Failed 9B", downloaded: true, ram: 7, fit: "ok", download_gb: 6,
      verified: true, drilled: true, drill_pass: false, drill_score: 2, drill_of: 6, license_note: "Apache-2.0", note: "fast" }
  ];
  f.drill_running = null;
  return f;
}

(async function main() {
  console.log("model menu rows");
  const w = await bootDashboard({ routes: { "/api/models": fixture() }, aux: ["aux_promotion.js"] });
  const d = w.document, menu = d.getElementById("model-menu"), pill = d.getElementById("model-pill");
  await w.__run("loadModels()"); await w.__tick();
  const row = (id) => menu.querySelector('.mmi[data-id="' + id + '"]');
  const first = row("a/First-9B"), unc = row("a/Unc-27B"), tight = row("a/Tight-35B"), huge = row("a/Huge-70B");

  T.check("the action word sits on the title line, beside the name",
    [first, unc, tight, huge].every((r) => r && r.querySelector(".mmt .act") && r.querySelector(".mmt .nm")));
  T.check("badges sit on the title line too", unc.querySelector(".mmt .mmtags .mmunc") && /unverified until drilled/.test(unc.querySelector(".mmtags").textContent));
  T.check("an action is a button-like chip", !first.querySelector(".act").classList.contains("st") && first.querySelector(".act").textContent === "switch");
  T.check("a state word is plain text ('sleeping', 'won't fit')",
    unc.querySelector(".act").classList.contains("st") && unc.querySelector(".act").textContent === "sleeping" &&
    huge.querySelector(".act").classList.contains("st"));
  T.check("the asleep model in use is the current row", unc.getAttribute("aria-current") === "true" && !first.hasAttribute("aria-current"));
  T.check("the uncensored warning stays on the row, said once",
    unc.querySelector(".mmunc-l").textContent === "no refusals — the approval gate is the only brake" &&
    !/no refusals/.test(unc.querySelector(".rm2").textContent));
  T.check("one spec line per row, its full text in Details",
    [first, unc, tight].every((r) => r.querySelectorAll(".rm2").length === 1) &&
    /no refusals · abliterated 27B/.test(d.getElementById("mmdd-a_Unc-27B").textContent));
  T.check("a fit 'ok' line moved off the row (Details has RAM needed)",
    !first.querySelector(".mmfit") && /~7 GB of 64 GB/.test(d.getElementById("mmdd-a_First-9B").textContent));
  T.check("tight and no keep their reason on the row",
    /tight/.test((tight.querySelector(".mmfit.warn") || {}).textContent || "") && /too big/.test((huge.querySelector(".mmfit.bad") || {}).textContent || ""));
  T.check("the Details panel is still a sibling after the row", unc.nextElementSibling && unc.nextElementSibling.id === "mmdd-a_Unc-27B");

  // ---- round 3: one verification state, drill + licence in Details ----------
  await w.__run("loadModels()"); await w.__tick(); await w.__tick();
  const drilled = row("a/Drilled-27B"), failed = row("a/Failed-9B"), unc2 = row("a/Unc-27B"), first2 = row("a/First-9B");
  const states = (r) => [...r.querySelectorAll(".mmunv")].map((x) => x.textContent);
  T.check("a passing drill record is the row's one state (no 'unverified', no '— undrilled')",
    JSON.stringify(states(drilled)) === JSON.stringify(["✓ drilled 6/6"]) && !/undrilled|unverified/.test(drilled.textContent), states(drilled));
  T.check("a failed record is the one state", JSON.stringify(states(failed)) === JSON.stringify(["✗ failed 2/6"]), states(failed));
  T.check("an undrilled verified:false row says 'unverified until drilled' once",
    JSON.stringify(states(unc2)) === JSON.stringify(["unverified until drilled"]) && !/undrilled/.test(unc2.textContent.replace("unverified until drilled", "")));
  T.check("a verified, undrilled row shows no state chip", states(first2).length === 0);
  T.check("no drill line or Drill button left on any row", !menu.querySelector(".mmi .pro-line, .mmi .pro-drill, .mmi .pro-lic"));
  const ddD = d.getElementById("mmdd-a_Drilled-27B"), ddU = d.getElementById("mmdd-a_Unc-27B");
  T.check("Details holds the drill record, the Re-drill button and the licence",
    /✓ drilled 6\/6/.test(ddD.textContent) && ddD.querySelector(".pro-drill") && ddD.querySelector(".pro-drill").textContent === "Re-drill" &&
    /Licence/.test(ddD.textContent) && /Apache-2.0/.test(ddD.textContent));
  T.check("an undrilled downloaded row's Details offers Drill", ddU.querySelector(".pro-drill") && ddU.querySelector(".pro-drill").textContent === "Drill" && /— undrilled/.test(ddU.textContent));
  let drillPosts = 0;
  w.confirm = () => true;
  const origFetch = w.fetch;
  w.fetch = (u, o) => { if (u === "/api/models/drill" && o && o.method === "POST") { drillPosts++; return Promise.resolve({ json: () => Promise.resolve({ ok: false, error: "test" }) }); } return origFetch(u, o); };
  w.alert = () => {};
  let rowClicked = false; const oc = drilled.onclick; drilled.onclick = () => { rowClicked = true; };
  ddD.querySelector(".pro-drill").click(); await w.__tick();
  drilled.onclick = oc; w.fetch = origFetch;
  T.check("the Details Drill button still starts a drill (POST) and never fires the row's switch", drillPosts === 1 && !rowClicked);
  // an open Details panel survives the 30 s rebuild, Drill row included
  d.querySelector('.mmdt[data-det="a/Drilled-27B"]').click();
  await w.__run("loadModels()"); await w.__tick(); await w.__tick();
  const ddD2 = d.getElementById("mmdd-a_Drilled-27B");
  T.check("an open Details panel stays open, with its Drill row, after a rebuild",
    !ddD2.hidden && ddD2.querySelector(".pro-drill") && ddD2.querySelectorAll(".pro-drill").length === 1);
  const spec = drilled.querySelector(".rm2");
  T.check("the spec line is whole facts (no ellipsis), the RAM figure last and whole",
    [...spec.querySelectorAll(".sf")].map((x) => x.textContent.replace(/\u00a0·$/, "")).join("|") === "abliterated 27B|4-bit subfolder|~19GB RAM");
  const tr = drilled.querySelector(".mmt > .mmtags.row");
  T.check("an uncensored row's two marks share ONE line under the name (a full-width grid row, badge then state)",
    tr && tr.parentNode === drilled.querySelector(".mmt") && !drilled.querySelector(".mmn .mmtags") &&
    [...tr.children].map((x) => x.className).join("|") === "mmunc|mmunv pass" &&
    /grid-column:1\/-1/.test(require("fs").readFileSync(require("path").resolve(__dirname, "../../../dashboard/index.html"), "utf8").match(/\.mmi \.mmtags\.row\{[^}]*\}/)[0]));
  T.check("a row with only a state chip keeps it beside the name", failed.querySelector(".mmn .mmtags .mmunv") && !failed.querySelector(".mmtags.row"));
  T.check("the name and the action stay on the title line's two cells",
    [drilled, unc2].every((r) => { const k = [...r.querySelector(".mmt").children]; return k[0].classList.contains("mmn") && k[1].classList.contains("act"); }));
  T.check("the uncensored sentence stays on the row", drilled.querySelector(".mmunc-l") && drilled.querySelector(".mmunc").textContent === "Uncensored");

  menu.hidden = true;
  pill.focus(); pill.click(); await w.__tick();
  T.check("opened from the keyboard, focus lands on the model in use", d.activeElement === row("a/Unc-27B"),
    d.activeElement && (d.activeElement.dataset.id || d.activeElement.id));

  T.done();
})().catch((e) => { console.log("FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
