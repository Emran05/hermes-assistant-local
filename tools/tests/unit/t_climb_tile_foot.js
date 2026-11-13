// Hub tiles carry a foot line built from data the tile already has (design
// climb 2026-11-12): Weather's low-to-high range with the temperature on it,
// System's free disk and uptime, and the city/description line keeps its
// click-to-change-city target. Evals the REAL weatherHTML(), sysFootText()
// and esc() from index.html; nothing is invented when a field is missing.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const HTML = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

const src = ["esc", "weatherHTML", "sysFootText"].map((n) => D.extractFunction(HTML, n)).join("\n");
const api = new Function(src + "\nreturn {weatherHTML, sysFootText};")();

console.log("weather tile");
const w = api.weatherHTML({ configured: true, city: "Hoboken <x>", temp: 58, desc: "Overcast", hi: 61, lo: 48 });
check("the big temperature is there", w.indexOf('<div class="w-big num">58&deg;</div>') === 0, w);
check("description and city stay the click-to-change target",
  /class="w-sub linkish wcitychg"[^>]*>Overcast &middot; Hoboken &lt;x&gt;<\/div>/.test(w), w);
check("the foot carries the low and the high", /class="w-foot trange"/.test(w) && w.indexOf("48&deg;") > 0 && w.indexOf("61&deg;") > 0, w);
const left = /<i style="left:([\d.]+)%"><\/i>/.exec(w);
check("the marker sits at the temperature's place in the range", left && Math.abs(+left[1] - (10 / 13 * 100)) < 0.1, left);
const flat = api.weatherHTML({ configured: true, city: "X", temp: 50, desc: "Clear", hi: 50, lo: 50 });
check("an empty range draws no bar but still shows H and L", !/trange/.test(flat) && /H:50&deg; L:50&deg;/.test(flat), flat);
const none = api.weatherHTML({ configured: true, city: "X", temp: 50, desc: "Clear" });
check("no high/low means no foot (nothing invented)", !/w-foot/.test(none), none);
check("an unconfigured tile still asks for a city", /class="wcity"/.test(api.weatherHTML({ configured: false })));

console.log("system tile");
check("free disk and uptime in hours", api.sysFootText({ disk_free_gb: 1134, uptime_min: 779 }) === (1134).toLocaleString() + " GB free · up 12h 59m",
  api.sysFootText({ disk_free_gb: 1134, uptime_min: 779 }));
check("days past 24 h", api.sysFootText({ uptime_min: 3 * 1440 + 125 }) === "up 3d 2h");
check("minutes under an hour", api.sysFootText({ uptime_min: 7 }) === "up 7m");
check("missing fields leave nothing", api.sysFootText({}) === "" && api.sysFootText(null) === "");

console.log("wiring");
check("liveSystem refreshes the foot in place", /\.sysfoot'\)\.forEach/.test(D.extractFunction(HTML, "liveSystem")));
check("the clock tile has its zone line and updateClock fills it",
  /clock-zone/.test(HTML.slice(HTML.indexOf("const RENDER={"), HTML.indexOf("const RENDER={") + 400)) &&
  /\.clock-zone/.test(D.extractFunction(HTML, "updateClock")));

console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
