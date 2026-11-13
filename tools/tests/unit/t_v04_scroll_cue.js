// t_v04_scroll_cue.js — visual audit 2026-11-12, V04(b).
//
// The model menu, the Widget Center and the pop-out (the Markets watchlist)
// ended mid-row with nothing saying they scroll. index.html's scrollCue()
// marks a scroller [data-more] while content is hidden below its edge, and for
// the menu and the Widget Center keeps a sticky <i class="scue"> as the LAST
// child — the model menu is rebuilt with innerHTML (and +=) every 30 s by
// loadModels(), so the cue has to come back after every rebuild, exactly once.
//
// The REAL scrollCue cut out of index.html, run in the mini DOM.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const PAGE = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");

function fnSrc(name) {
  const lines = PAGE.split("\n");
  const i = lines.findIndex(function (l) { return l.startsWith("function " + name + "("); });
  if (i < 0) return null;
  for (let j = i + 1; j < lines.length; j++) if (lines[j].startsWith("}")) return lines.slice(i, j + 1).join("\n");
  return null;
}
const FIX = '<!doctype html><html><head></head><body>' +
  '<div id="menu" role="menu"><div class="mmhead">head</div><div class="mmi">a</div></div>' +
  '<section id="pop"><div class="w-body">x</div><div class="askrow"></div></section>' +
  '<div id="short"><p>one line</p></div>' +
  '</body></html>';
const tick = function () { return new Promise(function (r) { setImmediate(r); }); };

(async function main() {
  console.log("scrollCue: an end-of-list cue only while content is hidden below (V04)");
  const SRC = fnSrc("scrollCue");
  T.check("index.html defines scrollCue", !!SRC);
  if (!SRC) T.done();
  const w = createWindow({ html: FIX });
  w.__run(SRC + "\nwindow.scrollCue=scrollCue;", "scrollCue");
  const d = w.document;
  async function settle() { for (let i = 0; i < 4; i++) { await tick(); w.__flushTimers(3); } await tick(); }
  function box(el, h, sh) { el.__rect = { left: 0, top: 0, width: 300, height: h }; el.__scrollHeight = sh; el.scrollTop = 0; }
  const more = function (el) { return el.getAttribute("data-more") !== null; };
  const cues = function (el) { return el.children.filter ? el.children.filter(function (c) { return c.classList.contains("scue"); })
    : Array.prototype.filter.call(el.children, function (c) { return c.classList.contains("scue"); }); };

  const menu = d.getElementById("menu"), pop = d.getElementById("pop"), short = d.getElementById("short");
  box(menu, 400, 900); box(pop, 500, 800); box(short, 200, 200);
  w.scrollCue(menu, true); w.scrollCue(pop, false); w.scrollCue(short, true);
  await settle();

  T.check("a scroller with content below its edge is marked data-more", more(menu) && more(pop));
  T.check("a list that fits is never marked", !more(short));
  T.check("withEl: one aria-hidden .scue, kept as the last child", cues(menu).length === 1 &&
    menu.lastElementChild === cues(menu)[0] && cues(menu)[0].getAttribute("aria-hidden") === "true");
  T.check("without withEl no element is added (the pop-out's fade is CSS on its ask row)", cues(pop).length === 0);

  // scrolled to the end: the cue goes; back up: it returns
  menu.scrollTop = 500; menu.dispatchEvent(new w.Event("scroll"));
  await settle();
  T.check("at the end of the scroll the mark is removed", !more(menu));
  menu.scrollTop = 120; menu.dispatchEvent(new w.Event("scroll"));
  await settle();
  T.check("scrolled back up, the mark returns", more(menu));

  // loadModels-style rebuild: innerHTML = then += (which re-serialises whatever is there)
  menu.innerHTML = '<div class="mmhead">head</div>' + '<div class="mmi">a</div><div class="mmi">b</div>';
  menu.innerHTML += '<div class="mmpower" id="mm-power">p</div>';
  await settle();
  menu.innerHTML += '<div class="mmmem">m</div>';
  await settle();
  T.check("after an innerHTML rebuild the cue is back, exactly once, last", cues(menu).length === 1 &&
    menu.lastElementChild === cues(menu)[0], menu.children.length);
  T.check("the rebuilt rows are all still there", menu.querySelectorAll(".mmi").length === 2 &&
    !!menu.querySelector("#mm-power") && !!menu.querySelector(".mmmem"));

  // content that shrinks to fit drops the mark on the mutation alone
  menu.__scrollHeight = 400; menu.scrollTop = 0;
  menu.innerHTML = '<div class="mmi">only</div>';
  await settle();
  T.check("content that now fits drops the mark", !more(menu));

  // calling it twice wires once
  w.scrollCue(menu, true); await settle();
  T.check("a second call does not add a second cue", cues(menu).length === 1);

  // wiring: the three surfaces the audit named
  T.check("the model menu, the Widget Center and the pop-out are wired",
    /scrollCue\(\$\('model-menu'\),true\)/.test(PAGE) &&
    /scrollCue\(document\.querySelector\('#wcenter>\.sheet'\),true\)/.test(PAGE) &&
    /scrollCue\(\$\('wpop-sheet'\),false\)/.test(PAGE));
  T.check("the cue never takes a click", /\.scue\{[^}]*pointer-events:none/.test(PAGE) &&
    /\.popsheet \.askrow::before\{[^}]*pointer-events:none/.test(PAGE));
  T.check("no timer in scrollCue", !/setTimeout|setInterval/.test(SRC));
  T.done();
})();
