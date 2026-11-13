// R3 C1 (docs/plans/motion-system-2026-10.md §6.7, §7) — every Motion call the
// dashboard makes TODAY runs unchanged on the vendored Motion 14.6.0, so
// dashboard/motion.min.js (11.11.13) can be deleted and /motion.min.js can be
// an alias of dashboard/vendor/motion/motion-14.6.0.min.js with no shim.
//
// What is proven, all on the REAL vendored file in the mini DOM (_a11y_dom.js):
//   1. the five vendored builds load as plain scripts and define their globals;
//   2. INVENTORY: every animate()/wrapper call site in index.html, expand.js
//      and dashboard/aux_*.js is extracted from the source text (not a copied
//      list), its keyframes + options are evaluated as written, and the call
//      is run on Motion 14: no throw, the controls the callers use (.then and
//      .finished) are there and resolve, and the element lands on the final
//      keyframe. The `easing` option they pass is ignored by Motion 14 exactly
//      as Motion 11 ignored it (the curve only changes once the §6.7 legacy
//      shim translates it), and the timing they ask for reaches the browser;
//   3. the wrappers that sit between a call site and Motion are still the
//      shapes this inventory assumes (pinned against their source);
//   4. the real page: index.html's `const M=window.Motion` + `animate()` pick
//      Motion 14 up; aux_prefs' reduce-motion gate can still wrap
//      Motion.animate (the v14 export object is writable) and both of its
//      paths work; the model-swap overlay and the day-drill open/close run end
//      to end;
//   5. the frozen Quick Ask skeleton (app/main.swift QUICKASK_HTML) boots
//      against the alias's bytes and its entrance goes through Motion 14.
// Plain element.animate() (WAAPI) call sites never touch Motion and are only
// counted. Nothing here fetches, and nothing wakes a model.
"use strict";
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const { createWindow, bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const SVGNS = "http://www.w3.org/2000/svg";
const SPRING = "cubic-bezier(.22,1,.36,1)";

const MANIFEST = JSON.parse(fs.readFileSync(path.join(DASH, "vendor", "MANIFEST.json"), "utf8"));
const MOTION = MANIFEST.find(function (e) { return e.package === "motion"; });
const MOTION_SRC = fs.readFileSync(path.join(DASH, MOTION.file), "utf8");

// ---------------------------------------------------------------------------
// environment: a controllable clock (Motion reads performance.now) and an
// SVGElement that only SVG-namespace nodes are instances of — the mini DOM
// aliases it to Element, which would make Motion treat every <div> as SVG.
// ---------------------------------------------------------------------------
function fidelity(w) {
  let now = 1000;
  w.performance = { now: function () { return now; } };
  w.__clock = { get now() { return now; }, add: function (ms) { now += ms; } };
  w.SVGElement = class SVGElement { static [Symbol.hasInstance](x) { return !!x && x.namespaceURI === SVGNS; } };
}
// A Web Animation with no duration and no delay finishes on the next frame
// in a browser, AFTER the caller has attached onfinish (which is how Motion
// learns it ended). The mini DOM finishes it synchronously inside animate(),
// before anyone can listen — Motion 11 and 14 alike then never resolve. Make
// it browser-like for this suite (the requested timing is still reported).
(function browserLikeZeroLength() {
  let P = Object.getPrototypeOf(createWindow({}).document.createElement("div"));
  while (P && !Object.prototype.hasOwnProperty.call(P, "animate")) P = Object.getPrototypeOf(P);
  const native = P.animate;
  P.animate = function (kf, opt) {
    const t = typeof opt === "number" ? { duration: opt } : Object.assign({}, opt || {});
    if (t.duration > 0 || t.delay > 0) return native.call(this, kf, opt);
    const a = native.call(this, kf, Object.assign({}, t, { duration: 1 }));
    a.timing = t;
    const win = this.ownerDocument && this.ownerDocument.defaultView;
    win.setTimeout(function () { if (a.playState === "running") a.finish(); }, 0);
    return a;
  };
})();
function motionWindow() {
  const w = createWindow({});
  fidelity(w);
  w.__run(MOTION_SRC, MOTION.file);
  return w;
}
async function frames(w, n) {
  for (let i = 0; i < n; i++) { w.__clock.add(16); w.__flushTimers(1); await new Promise(function (r) { setImmediate(r); }); }
}
function running(w) { return (w.document.__allAnims || []).filter(function (a) { return a.playState === "running"; }); }
// the browser clock reaching the end: the mini DOM's Web Animations never end
// on their own, so after the JS clock has passed delay+duration, finish them
async function settle(w, ms) {
  await frames(w, Math.ceil((ms || 600) / 16) + 2);
  running(w).forEach(function (a) { a.finish(); });
  await frames(w, 40);
}
// An SVG node as a browser presents it: tag names are not upper-cased, and
// every SVGElement has `ownerSVGElement` (null on the outermost <svg>), which
// is how Motion 14 tells SVG from HTML ("ownerSVGElement" in el).
function svgTarget(d, tag, owner) {
  const el = d.createElementNS(SVGNS, tag);
  Object.defineProperty(el, "tagName", { value: tag });
  el.ownerSVGElement = owner || null;
  return el;
}

// ---------------------------------------------------------------------------
// transform comparison: "translateY(0px)", "translateY(0)", "none" and a bare
// x:0 are all the identity; compare the functions numerically, units aside
// ---------------------------------------------------------------------------
const TDEF = { translateX: 0, translateY: 0, scale: 1, scaleX: 1, scaleY: 1, rotate: 0 };
function parseTransform(s) {
  const out = Object.assign({}, TDEF);
  s = String(s == null ? "" : s).trim();
  if (!s || s === "none") return out;
  const re = /(translateX|translateY|scaleX|scaleY|scale|rotate)\(\s*([-\d.e]+)[a-z%]*\s*\)/g;
  let m, seen = 0;
  while ((m = re.exec(s))) { out[m[1]] = parseFloat(m[2]); seen++; }
  if (!seen) out.__raw = s;
  return out;
}
function sameTransform(a, b) {
  const x = parseTransform(a), y = parseTransform(b);
  if (x.__raw || y.__raw) return x.__raw === y.__raw;
  return Object.keys(TDEF).every(function (k) { return Math.abs(x[k] - y[k]) < 1e-6; });
}
const IND = { x: "translateX", y: "translateY", scale: "scale", scaleX: "scaleX", scaleY: "scaleY", rotate: "rotate" };
function finalOf(v) { return Array.isArray(v) ? v[v.length - 1] : v; }
// what the element must show once the call has finished
function expectedStyle(kf) {
  const out = {}, ind = [];
  for (const k of Object.keys(kf)) {
    if (IND[k]) ind.push(IND[k] + "(" + finalOf(kf[k]) + ")");
    else out[k] = String(finalOf(kf[k]));
  }
  if (ind.length) out.transform = ind.join(" ");
  return out;
}
function styleMatches(el, want) {
  const bad = [];
  for (const k of Object.keys(want)) {
    const got = el.style[k];
    const ok = k === "transform" ? sameTransform(got, want[k])
      : (k === "opacity" ? parseFloat(got) === parseFloat(want[k]) : String(got) === want[k]);
    if (!ok) bad.push(k + ": got " + JSON.stringify(got) + " want " + JSON.stringify(want[k]));
  }
  return bad;
}

// ---------------------------------------------------------------------------
// the inventory: extracted from the source text
// ---------------------------------------------------------------------------
// The wrappers between a call site and Motion. Each one's definition is pinned
// (`def`), and `opts` maps the caller's options to what reaches Motion.
const WRAPPERS = {
  // after the 6.7 shim: HermesAnim.legacy when aux_anim.js loaded, else Motion as before
  "index.html": { animate: { def: /const animate=\(el,kf,opt\)=>\{try\{const A=window\.HermesAnim;\s*return A\?A\.legacy\(el,kf,opt\):\(M\?M\.animate\(el,kf,opt\):el\.animate\(kf,opt\)\);\}catch\(e\)\{\}\};/ } },
  "expand.js": { _ddAnim: { def: /function _ddAnim\(elm,kf,op\)\{try\{const a=animate\(elm,kf,op\);return \(a&&a\.finished\)\?a\.finished:Promise\.resolve\(\);/ } },
  "aux_agent.js": { agAnimate: { def: /agAnimate = \(typeof animate === "function"\) \? animate : null;/ } },
  "aux_memory.js": { A: { def: /function A\(node, kf, opt\)\{\s*var H = ha\(\);\s*try\{ if\(H\) return H\.legacy\(node, kf, opt\); if\(typeof animate==='function'\) animate\(node, kf, opt\);/ } },
  "aux_youmodel.js": { A: { def: /function A\(node, kf, opt\) \{\s*var H = ha\(\);\s*try \{ if \(H\) return H\.legacy\(node, kf, opt\); if \(typeof animate === "function"\) animate\(node, kf, opt\);/ } },
  "aux_settings_shell.js": { ANIM: { def: /function ANIM\(el, kf, opt\) \{\s*try \{ if \(typeof animate === "function"\) return animate\(el, kf, opt\); \}/ } },
  "aux_desktop.js": { anim: { def: /function anim\(el, kf, opt\) \{\s*try \{ if \(typeof animate === "function"\) return animate\(el, kf, opt\); \}/ } },
  "aux_clip.js": { anim: { def: /var o = opts \|\| \{\}, ms = o\.duration \|\| 240;[\s\S]{0,40}?if \(typeof animate === "function"\) \{ animate\(node, frames, \{ duration: ms \/ 1000, easing: o\.easing \}\); return; \}/,
    opts: function (o) { o = o || {}; return { duration: (o.duration || 240) / 1000, easing: o.easing }; } } },
  "aux_quickask.js": { anim: { def: /var o = opt \|\| \{\}, ms = o\.duration \|\| 200;\s*if \(M && M\.animate\) return M\.animate\(el, kf, \{ duration: ms \/ 1000, easing: o\.easing \}\);/,
    opts: function (o) { o = o || {}; return { duration: (o.duration || 200) / 1000, easing: o.easing }; } } }
};
// Free variables a call site's keyframes/options read. Anything not here makes
// that site fail loudly ("add a binding"), never pass silently.
const BIND_ALL = { SPRING: SPRING, SPR: SPRING, SP: function () { return SPRING; }, i: 2, k: 2 };
const BIND = {
  "aux_agent.js": { h: { __tick: 30 } },
  "expand.js": { h: 120 },
  "aux_settings_shell.js": { top: 48 },
  "aux_trust.js": { tx: "translateX(100%)" }
};
// targets that are not a plain <div> (the source decides: these are pinned)
const TARGETS = {
  "aux_agent.js|ring": "svg:g", "aux_agent.js|ic": "svg:svg"
};
// single-value keyframes read the start from the element's current style
const START = {
  "aux_trust.js|thumb": { transform: "translateX(0%)" },
  "aux_settings_shell.js|ind": { top: "12px" }
};

function scanArgs(s, i) {               // s[i] === "("; top-level comma split
  let depth = 0, q = null, cur = "";
  const args = [];
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (q) { cur += c; if (c === "\\") { cur += s[++j]; continue; } if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === "`") { q = c; cur += c; continue; }
    if (c === "/" && s[j + 1] === "/") { j = s.indexOf("\n", j); if (j < 0) break; continue; }
    if (c === "(" || c === "[" || c === "{") { depth++; if (depth === 1 && c === "(") continue; }
    if (c === ")" || c === "]" || c === "}") { depth--; if (depth === 0) { args.push(cur.trim()); return args; } }
    if (c === "," && depth === 1) { args.push(cur.trim()); cur = ""; continue; }
    cur += c;
  }
  return null;
}
function inventory() {
  const files = fs.readdirSync(DASH).filter(function (f) { return f === "index.html" || f === "expand.js" || /^aux_.*\.js$/.test(f); }).sort();
  const sites = [], wrappersSeen = [], waapi = [];
  for (const f of files) {
    const s = fs.readFileSync(path.join(DASH, f), "utf8");
    const names = ["animate"].concat(Object.keys(WRAPPERS[f] || {}).filter(function (n) { return n !== "animate"; }));
    const re = new RegExp("(^|[^.\\w$])(" + names.join("|") + ")\\(", "g");
    let m;
    while ((m = re.exec(s))) {
      const at = m.index + m[1].length;
      if (/function\s*$/.test(s.slice(Math.max(0, at - 10), at))) continue;   // a definition
      const ls = s.lastIndexOf("\n", at) + 1, le = s.indexOf("\n", at);
      if (/^\s*(\/\/|\*|\/\*)/.test(s.slice(ls, le < 0 ? undefined : le))) continue;   // a comment
      const args = scanArgs(s, at + m[2].length);
      if (!args || args.length < 2) continue;
      const line = s.slice(0, at).split("\n").length;
      // a wrapper's own body forwards its parameters: pinned below, not a site
      // (index.html's ANIM_NULL.spring forwards its `v` the same way)
      if (/^(kf|frames)$/.test(args[1]) || (f === "index.html" && args[1] === "v")) { wrappersSeen.push(f + ":" + line); continue; }
      sites.push({ file: f, line: line, fn: m[2], target: args[0], kf: args[1], opt: args[2] || "undefined" });
    }
    const wre = /\.animate\(\s*\[/g;                 // element.animate([...]) — WAAPI, not Motion
    while ((m = wre.exec(s))) waapi.push(f + ":" + s.slice(0, m.index).split("\n").length);
  }
  return { sites: sites, wrappersSeen: wrappersSeen, waapi: waapi };
}
function evalIn(src, scope) {
  const names = Object.keys(scope);
  return Function.apply(null, names.concat(['"use strict"; return (' + src + ");"])).apply(null, names.map(function (n) { return scope[n]; }));
}

(async function main() {
  console.log("R3 C1: today's Motion calls on the vendored Motion 14.6.0");

  // ---- 1. the vendored builds load and define their globals --------------
  const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(DASH, MOTION.file))).digest("hex");
  T.check("the Motion file under test is the manifest's (sha256 " + sha.slice(0, 12) + "…)", sha === MOTION.sha256 && MOTION.version === "14.6.0");
  {
    const w = createWindow({});
    const loaded = [];
    for (const e of MANIFEST) {
      try { w.__run(fs.readFileSync(path.join(DASH, e.file), "utf8"), e.file); loaded.push(e.global); }
      catch (err) { T.check(e.file + " loads as a plain <script>", false, String(err)); }
    }
    T.check("all five load as plain scripts, in the loader's order", loaded.length === MANIFEST.length, loaded);
    T.check("window.Motion: animate() + MotionGlobalConfig (v14)", typeof w.Motion.animate === "function" && typeof w.Motion.MotionGlobalConfig === "object");
    T.check("window.gsap is 3.15.0", w.gsap && w.gsap.version === "3.15.0");
    T.check("window.Flip / window.CustomEase are the plugins", typeof w.Flip === "function" && typeof w.Flip.getState === "function" && typeof w.CustomEase.create === "function");
    T.check("window.anime: animate() and pauseOnDocumentHidden on by default", typeof w.anime.animate === "function" && w.anime.engine.pauseOnDocumentHidden === true);
    let gel = null;
    try { w.gsap.registerPlugin(w.Flip, w.CustomEase); gel = w.CustomEase.create("t.gel", ".34,1.56,.64,1")(0.5); } catch (e) { gel = String(e); }
    T.check("CustomEase takes the four-number form (gel(.5) overshoots to ~1.087)", typeof gel === "number" && Math.abs(gel - 1.087) < 0.002, gel);
    T.check("loading them threw no page error and started no interval", w.__errors.length === 0 && w.__intervals.length === 0, w.__errors.map(String));
  }

  // ---- 2. inventory --------------------------------------------------------
  const inv = inventory();
  console.log("  inventory: " + inv.sites.length + " Motion call sites, " + inv.wrappersSeen.length + " wrapper bodies, " + inv.waapi.length + " plain WAAPI calls");
  const perFile = {};
  inv.sites.forEach(function (s) { perFile[s.file] = (perFile[s.file] || 0) + 1; });
  console.log("  per file: " + Object.keys(perFile).map(function (f) { return f + " " + perFile[f]; }).join(", "));
  // round 3 wave B moved index.html's, aux_agent's and aux_recorder's sites onto
  // HermesAnim (26 remain across 16 files), so the floor follows the leftovers
  T.check("the extractor finds the call sites (>= 24 across >= 15 files)", inv.sites.length >= 24 && Object.keys(perFile).length >= 15, perFile);
  for (const f of Object.keys(WRAPPERS)) {
    const src = fs.readFileSync(path.join(DASH, f), "utf8");
    for (const n of Object.keys(WRAPPERS[f])) {
      T.check(f + ": the " + n + "() wrapper still forwards to Motion as this inventory assumes", WRAPPERS[f][n].def.test(src));
    }
  }

  const results = [];
  for (const site of inv.sites) {
    const id = site.file + ":" + site.line;
    const scope = Object.assign({}, BIND_ALL, BIND[site.file] || {});
    let kf, opt;
    try { kf = evalIn(site.kf, scope); opt = evalIn(site.opt, scope); }
    catch (e) { T.check(id + " evaluates as written", false, String(e) + " — add a binding for it in BIND"); continue; }
    const wrap = (WRAPPERS[site.file] || {})[site.fn];
    const sent = wrap && wrap.opts ? wrap.opts(opt) : opt;
    const w = motionWindow();
    const d = w.document;
    const kind = TARGETS[site.file + "|" + site.target] || "div";
    let target, els;
    if (kind === "list") {
      const host = d.createElement("div"); d.body.appendChild(host);
      for (let n = 0; n < 3; n++) { const r = d.createElement("div"); r.className = "row"; host.appendChild(r); }
      target = host.querySelectorAll(".row"); els = Array.from(target);
    } else if (kind.indexOf("svg:") === 0) {
      const svg = svgTarget(d, "svg"); d.body.appendChild(svg);
      target = kind === "svg:svg" ? svg : svg.appendChild(svgTarget(d, kind.slice(4), svg));
      els = [target];
    } else { target = d.createElement("div"); d.body.appendChild(target); els = [target]; }
    const start = START[site.file + "|" + site.target];
    if (start) els.forEach(function (e) { Object.keys(start).forEach(function (k) { e.style[k] = start[k]; }); });
    let ctl = null, threw = null, thenDone = false, finDone = false;
    try { ctl = w.Motion.animate(target, kf, sent); } catch (e) { threw = String(e && e.stack || e).split("\n")[0]; }
    if (ctl && typeof ctl.then === "function") ctl.then(function () { thenDone = true; });
    if (ctl && ctl.finished && typeof ctl.finished.then === "function") ctl.finished.then(function () { finDone = true; });
    // what reached the browser for THESE elements (Motion also runs a
    // throwaway feature probe on a detached <div>; that is not the call).
    // Transform groups (x + scale -> one WAAPI `transform`) start a frame later.
    await frames(w, 3);
    const timings = [];
    els.forEach(function (e) { (e.__anims || []).forEach(function (a) { if (a.playState === "running") timings.push(a.timing); }); });
    const total = ((sent && sent.delay) || 0) * 1000 + ((sent && sent.duration) || 0.3) * 1000;
    await settle(w, total + 100);
    const want = expectedStyle(kf);
    const bad = [];
    els.forEach(function (e) { bad.push.apply(bad, styleMatches(e, want)); });
    const wantMs = Math.round(((sent && sent.duration) || 0) * 1000), wantDelay = Math.round(((sent && sent.delay) || 0) * 1000);
    const timingOk = timings.every(function (t) { return t.duration === wantMs && (t.delay || 0) === wantDelay; });
    const easingIgnored = timings.every(function (t) { return t.easing === "ease-out"; });
    const ok = !threw && ctl && thenDone && finDone && !bad.length && timingOk && w.__errors.length === 0;
    results.push({ id: id, ok: ok, timings: timings.length, easingIgnored: easingIgnored, passesEasing: !!(sent && sent.easing) });
    T.check(id + " " + site.fn + "(" + site.target.slice(0, 28) + ", …) runs on Motion 14 and lands on its final keyframe",
      ok, { threw: threw, then: thenDone, finished: finDone, style: bad, timingOk: timingOk, timings: timings, errors: w.__errors.map(String) });
  }
  const accel = results.filter(function (r) { return r.timings > 0; });
  T.check("Motion 14 hands opacity/transform of " + accel.length + " sites to the browser (WAAPI) with their duration and delay",
    accel.length >= 22, accel.length);
  const eased = accel.filter(function (r) { return r.passesEasing; });
  T.check("the `easing` option those " + eased.length + " sites pass is ignored, exactly as on Motion 11: same curve as today",
    eased.length > 0 && eased.every(function (r) { return r.easingIgnored; }), eased.filter(function (r) { return !r.easingIgnored; }));
  T.check("plain element.animate() sites are WAAPI and never touch Motion (" + inv.waapi.length + ")", inv.waapi.length >= 2, inv.waapi);

  // ---- 2b. the one call site whose LOOK Motion 14 changes: an SVG child ------
  // Motion 11's vanilla build never rendered a transform on an SVG child (its
  // SVG renderer drops `transform` unless it has measured dimensions, and
  // nothing in the vanilla bundle measures them), so aux_agent's tickSigil —
  // rotate on <g class="sig-ring"> — has been a silent no-op. Motion 14 renders
  // it, and writes transform-box: fill-box inline; aux_agent's stylesheet gives
  // the ring `transform-origin:18px 18px`, authored for the default view-box,
  // which under fill-box is 18px from the ring's bounding-box corner (6,6): a
  // pivot at (24,24) instead of the centre, so each 12deg tick (cumulative)
  // walks the ring off the sigil — up to ~17 of its 36 units. The fix is one
  // CSS line in aux_agent.js (merge note in the R3 C1 report); until it lands
  // this prints a SKIP, which CI's FAIL_ON_SKIP turns into a failure.
  {
    const w2 = motionWindow(), d2 = w2.document;
    const svg = svgTarget(d2, "svg"); d2.body.appendChild(svg);
    const g = svg.appendChild(svgTarget(d2, "g", svg));
    let fin = false;
    w2.Motion.animate(g, { rotate: ["18deg", "30deg"] }, { duration: 0.4, easing: SPRING }).then(function () { fin = true; });
    await settle(w2, 500);
    T.check("Motion 14 renders a transform on an SVG child, with transform-box: fill-box and no transform-origin of its own",
      fin && g.style.transformBox === "fill-box" && sameTransform(g.style.transform, "rotate(30deg)") && !g.style.transformOrigin, g.style.cssText);
    const agent = fs.readFileSync(path.join(DASH, "aux_agent.js"), "utf8");
    const mk = agent.match(/<g class="sig-ring"[^>]*>(.*?)<\/g>/);
    const circles = mk ? Array.from(mk[1].matchAll(/<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"/g)).map(function (c) { return c.slice(1).map(Number); }) : [];
    const rule = (agent.match(/"\.sig-ring\{([^}]*)\}"/) || [])[1];
    const tick = /anim\(\)\.spring\(ring, \{ rotate: \[/.test(agent);
    T.check("aux_agent.js still ticks the sigil ring with a Motion rotate (HermesAnim.spring), and its markup/rule are where this check reads them",
      tick && circles.length >= 1 && typeof rule === "string", { tick: tick, circles: circles, rule: rule });
    const box = circles.reduce(function (b, c) {
      return { x0: Math.min(b.x0, c[0] - c[2]), y0: Math.min(b.y0, c[1] - c[2]), x1: Math.max(b.x1, c[0] + c[2]), y1: Math.max(b.y1, c[1] + c[2]) };
    }, { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity });
    // the pivot Motion 14's inline fill-box gives a CSS transform-origin
    const pivot = function (css) {
      const m = /transform-origin\s*:\s*([^;]+)/.exec(css || "");
      const parts = (m ? m[1].trim() : "50% 50%").split(/\s+/);
      const W = box.x1 - box.x0, H = box.y1 - box.y0;
      const kw = { left: "0%", top: "0%", center: "50%", right: "100%", bottom: "100%" };
      if (parts.length === 1) parts.push(parts[0] === "center" ? "center" : "50%");
      const one = function (v, base, size) { v = kw[v] || v; return v.slice(-1) === "%" ? base + parseFloat(v) / 100 * size : base + parseFloat(v); };
      return [one(parts[0], box.x0, W), one(parts[1], box.y0, H)];
    };
    const centre = [(box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2];
    const near = function (p) { return Math.abs(p[0] - centre[0]) < 0.01 && Math.abs(p[1] - centre[1]) < 0.01; };
    const FIX = "transform-box:fill-box;transform-origin:50% 50%";
    T.check("the merge-note fix (.sig-ring{" + FIX + "}) pivots the ring on its centre under Motion 14", near(pivot(FIX)), pivot(FIX));
    if (near(pivot(rule))) T.check("aux_agent.js .sig-ring pivots on its centre under Motion 14", true);
    else console.log("  SKIP pending merge note: aux_agent.js .sig-ring{" + rule + "} pivots at " + JSON.stringify(pivot(rule)) +
      " not " + JSON.stringify(centre) + " under Motion 14's fill-box; apply .sig-ring{" + FIX + "}");
  }

  // ---- 3. reduce-motion: Motion 14's instant path, as aux_prefs' gate asks --
  {
    const w = motionWindow(), d = w.document, el = d.createElement("div");
    d.body.appendChild(el);
    let done = false;
    w.Motion.animate(el, { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0, delay: 0, repeat: 0, repeatDelay: 0 }).then(function () { done = true; });
    await frames(w, 2);
    T.check("duration 0 (the gate's shape) lands at once and resolves, no running animation",
      done && el.style.opacity === "1" && sameTransform(el.style.transform, "none") && running(w).length === 0, { done: done, css: el.style.cssText });
    T.check("Motion 14's export object is writable (aux_prefs re-assigns Motion.animate)",
      (function () { const o = w.Motion.animate, f = function () {}; w.Motion.animate = f; const ok = w.Motion.animate === f; w.Motion.animate = o; return ok; })());
  }

  // ---- 4. the real page on Motion 14 ----------------------------------------
  const before = function (w) { fidelity(w); w.__run(MOTION_SRC, MOTION.file); };
  const w = await bootDashboard({ aux: ["aux_prefs.js", "expand.js"], before: before });
  const d = w.document, html = d.documentElement;
  T.check("index.html's `M` is the vendored Motion 14 and animate() is its wrapper",
    w.__run("M===window.Motion && typeof M.MotionGlobalConfig==='object' && typeof animate==='function'"));
  T.check("aux_prefs' reduce gate wrapped Motion 14's animate", w.Motion.animate.__hermesStill === true);
  {
    const el = d.createElement("div"); d.body.appendChild(el); w.__el = el;
    const c = w.__run("animate(window.__el,{opacity:[0,1],scale:[0.92,1]},{duration:0.45,easing:[.34,1.4,.64,1]})");
    T.check("the page's animate() returns Motion controls (.then/.finished/.stop), not a bare WAAPI Animation",
      c && typeof c.then === "function" && c.finished && typeof c.stop === "function" && !("playState" in c));
    let fin = false; c.finished.then(function () { fin = true; });
    await settle(w, 600);
    T.check("...and the element lands on its final keyframe", fin && el.style.opacity === "1" && sameTransform(el.style.transform, "none"), el.style.cssText);

    html.setAttribute("data-reduce", "1");
    const el2 = d.createElement("div"); d.body.appendChild(el2); w.__el = el2;
    let fin2 = false;
    w.__run("animate(window.__el,{opacity:[0,1]},{duration:0.45,delay:0.1,repeat:Infinity})").finished.then(function () { fin2 = true; });
    await frames(w, 2);
    T.check("in-app Reduce motion: the gate makes a Motion 14 call land at once, even an endless one",
      fin2 && el2.style.opacity === "1" && running(w).length === 0, { fin: fin2, css: el2.style.cssText });
    html.removeAttribute("data-reduce");

    const el3 = d.createElement("div"); d.body.appendChild(el3); w.__el = el3;
    let fin3 = false;
    w.__run("animate(window.__el,{opacity:[0,1]},{duration:5})").finished.then(function () { fin3 = true; });
    await frames(w, 3);
    T.check("a long Motion 14 animation runs on the browser (WAAPI)", running(w).length === 1);
    const gear = d.getElementById("prefs-btn"), row = d.querySelector('#prefs-menu [role="menuitemcheckbox"]');
    gear.click(); row.click();
    await frames(w, 6);
    T.check("turning Reduce motion on settles it: final state, controls resolved", html.hasAttribute("data-reduce") && fin3 && el3.style.opacity === "1" && running(w).length === 0,
      { reduce: html.hasAttribute("data-reduce"), fin: fin3, css: el3.style.cssText });
    row.click();
    T.check("...and off again", !html.hasAttribute("data-reduce"));
  }
  {
    w.__run("showModelSwap('Small 9B','Switching model')");
    const o = d.getElementById("model-swap");
    await settle(w, 600);
    // R3 C3 (§8.6): the overlay is GSAP sequences through HA(); with no
    // aux_anim.js loaded here that is ANIM_NULL, which lands each step at once
    T.check("model-swap overlay: show leaves the overlay and its card visible, nothing inline",
      o && !o.hidden && o.style.opacity === "" && o.querySelector(".swapcard").style.opacity === "", o && o.style.cssText);
    w.__run("hideModelSwap(true,'Small 9B')");
    await settle(w, 2000);
    T.check("...ready: after its reading time it hides, leaving no inline opacity", o.hidden === true && o.style.opacity === "", o.style.cssText);
    w.__run("showModelSwap('Small 9B','Switching model')");
    await settle(w, 600);
    T.check("...shown again after a hide it is fully visible (no exit opacity sticks)", !o.hidden && o.style.opacity === "", o.style.cssText);
    w.__run("failModelSwap('The server said no.')");
    await settle(w, 3000);
    T.check("...fail: same exit", o.hidden === true, o.style.cssText);
  }
  {
    const body = d.createElement("div"); body.hidden = true; d.body.appendChild(body); w.__b = body;
    body.__scrollHeight = 120;
    w.__run("_ddOpen(window.__b)");
    T.check("day-drill open: visible at once, animating", body.hidden === false && body.dataset.anim === "1");
    await settle(w, 500);
    T.check("...and cleans up after Motion 14's .finished resolves", body.dataset.anim === undefined && body.style.height === "" && body.style.opacity === "" && body.style.overflow === "",
      { anim: body.dataset.anim, css: body.style.cssText });
    w.__run("_ddClose(window.__b)");
    T.check("day-drill close: stays visible while the collapse runs (Motion 14 has .finished; 11 did not and hid at once)", body.hidden === false);
    await settle(w, 500);
    T.check("...then hides and cleans up", body.hidden === true && body.dataset.anim === undefined && body.style.cssText === "", body.style.cssText);
  }
  T.check("the page threw nothing on Motion 14", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));

  // ---- 5. the frozen Quick Ask skeleton against the alias's bytes -----------
  {
    const swift = fs.readFileSync(path.join(REPO, "app", "main.swift"), "utf8");
    const mm = swift.match(/let QUICKASK_HTML = """\n([\s\S]*?)"""/);
    const sk = mm ? mm[1] : "";
    const srcs = Array.from(sk.matchAll(/<script src="([^"]+)"><\/script>/g)).map(function (x) { return x[1]; });
    T.check("the popover skeleton still loads /motion.min.js first, then aux_quickask.js and aux_clip.js",
      JSON.stringify(srcs) === JSON.stringify(["/motion.min.js", "/aux_quickask.js", "/aux_clip.js"]), srcs);
    const qw = createWindow({ html: sk });
    fidelity(qw);
    const calls = [];
    for (const m of sk.matchAll(/<script( src="([^"]+)")?>([\s\S]*?)<\/script>/g)) {
      if (!m[2]) { qw.__run(m[3], "QUICKASK_HTML#inline"); continue; }
      // what the server answers for each src: /motion.min.js is VENDOR_ALIAS
      const file = m[2] === "/motion.min.js" ? MOTION.file : m[2].slice(1);
      qw.__run(fs.readFileSync(path.join(DASH, file), "utf8"), m[2]);
      if (m[2] === "/motion.min.js") {
        const real = qw.Motion.animate;
        qw.Motion.animate = function (el, kf, o) { calls.push({ el: el, kf: kf, o: o }); return real.apply(this, arguments); };
      }
    }
    await settle(qw, 400);
    const entry = calls[0];
    T.check("the popover's entrance goes through Motion 14 with its seconds-based timing",
      calls.length >= 1 && entry.o && entry.o.duration === 0.16 && JSON.stringify(entry.kf) === '{"opacity":[0,1]}', calls.map(function (c) { return c.o; }));
    T.check("...and lands (root fully opaque)", entry && entry.el && entry.el.style && entry.el.style.opacity === "1", entry && entry.el && entry.el.style && entry.el.style.cssText);
    T.check("the popover boots with no page error on Motion 14", qw.__errors.length === 0, qw.__errors.map(String).slice(0, 3));
  }

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
