// C88 — the System pop-out stops polling when it is closed.
//
// EXPAND_RENDER.system starts a 5 s /api/expand?id=system poll (~86 ms of CPU
// and 8 process spawns per call on the server) and only stopped it once its
// element left the DOM. closePop() only HIDES #wpop, so after a close the body
// stayed connected and the poll ran until some other pop-out replaced it —
// about 145k spawns a day if the System card was the last one opened.
// Evals the REAL expand.js against the DOM in _k14b_dom.js.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

const env = D.makeEnv();
const calls = [];
D.install(env, {
  fetch: function (url) { calls.push(String(url)); return new Promise(function () {}); },
  EXPAND_RENDER: {},
  esc: require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc"),
  barRows: () => "", statGrid: () => "", relTime: () => "", icon: () => ""
});
global.requestAnimationFrame = function () { return 1; };   // no paint: only the poll is under test
// eslint-disable-next-line no-eval
(0, eval)(fs.readFileSync(path.join(REPO, "dashboard", "expand.js"), "utf8"));

document.body.innerHTML = '<div class="modal" id="wpop"><section id="wpop-sheet"><div class="w-body"></div></section></div>';
const pop = document.getElementById("wpop");
const body = pop.querySelector(".w-body");
EXPAND_RENDER.system(body, { sys: {}, mem: {}, history: {} });
const polls = () => calls.filter((u) => u.indexOf("/api/expand?id=system") === 0).length;
const live = () => env.liveIntervals().filter((t) => t.ms === 5000).length;

console.log("open");
check("one 5 s poll is running", live() === 1, live());
env.tick();
check("an open pop-out polls", polls() === 1, calls);

console.log("the window is hidden (another app in front)");
document.hidden = true;
env.tick();
check("no poll while the page is hidden", polls() === 1, calls);
check("but the timer survives for when it comes back", live() === 1, live());
document.hidden = false;
env.tick();
check("polling resumes when visible", polls() === 2, calls);

console.log("closePop(): #wpop hidden, body still in the DOM");
pop.hidden = true;
check("(the body is still connected)", body.isConnected);
env.tick();
check("no poll after close", polls() === 2, calls);
check("and the interval is cleared", live() === 0, live());
env.tick(); env.tick();
check("still nothing later", polls() === 2, calls);

console.log("reopen");
pop.hidden = false;
EXPAND_RENDER.system(body, { sys: {}, mem: {}, history: {} });
env.tick();
check("a reopened pop-out polls again, once", polls() === 3 && live() === 1, [polls(), live()]);

console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
