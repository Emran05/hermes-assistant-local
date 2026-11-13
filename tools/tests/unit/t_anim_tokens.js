// t_anim_tokens.js — one source for motion tokens (docs/plans/motion-system-
// 2026-10.md §6.4): aux_anim.js's HermesAnim.T is the source, index.html's
// :root mirrors the curves and durations as CSS custom properties, and this
// suite locks the two together.
//
// Durations and curves do not depend on the theme, so the theme trap (every
// palette block re-declaring every colour) does not apply to them — but a
// duration declared twice must agree everywhere it is declared.
//
// index.html is not owned by the runtime cluster, so the new tokens arrive
// through a merge note. Until index.html loads /aux_anim.js only the tokens it
// already declares are checked (and the pending ones are listed as SKIP);
// from the moment it loads the runtime, every mirrored token is REQUIRED.
// MERGE_LINES below is the exact text the merge note hands over, and it is
// checked against T here so the note cannot drift from the source either.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const HTML = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");

const MERGE_LINES =
  "    --ease-out:cubic-bezier(.22,1,.36,1); --ease-expo:cubic-bezier(.16,1,.3,1);\n" +
  "    --d-press:90ms; --d-micro:150ms; --d-small:220ms; --d-pop:320ms; --d-view:360ms;\n" +
  "    --d-layout:420ms; --d-surface:450ms;\n";
const EASE_VARS = { "--gel": "gel", "--press": "press", "--pop": "pop", "--ease-out": "out", "--ease-expo": "expo" };
const DUR_VARS = ["press", "micro", "small", "pop", "view", "layout", "surface"];
const ALWAYS = ["--gel", "--press", "--pop"];

function bez(v) {
  const m = /^cubic-bezier\(([^)]*)\)$/.exec(String(v).trim());
  if (!m) return null;
  const a = m[1].split(",").map(Number);
  return a.length === 4 && a.every(isFinite) ? a : null;
}
function ms(v) {
  const m = /^(-?\d*\.?\d+)(ms|s)$/.exec(String(v).trim());
  return m ? (m[2] === "s" ? +m[1] * 1000 : +m[1]) : null;
}
function same(a, b) { return !!a && !!b && a.length === b.length && a.every(function (x, i) { return Math.abs(x - b[i]) < 1e-9; }); }
function decls(text) {
  // every declaration of a mirrored token anywhere in the text (not var() uses)
  const out = [], re = /(--(?:gel|press|pop|ease-out|ease-expo|d-[a-z]+))\s*:\s*([^;}\n]+)/g;
  let m;
  while ((m = re.exec(text))) out.push({ name: m[1], value: m[2].trim() });
  return out;
}
function styleText(html) {
  const out = []; const re = /<style[^>]*>([\s\S]*?)<\/style>/gi; let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out.join("\n");
}

(function main() {
  console.log("motion tokens: aux_anim.js T == index.html :root");
  const w = createWindow({});
  w.__run(SRC, "aux_anim.js");
  const TK = w.HermesAnim && w.HermesAnim.T;
  T.check("aux_anim.js exposes HermesAnim.T", !!TK && !!TK.ease && !!TK.dur);
  if (!TK) return T.done();

  // the merge note's lines are generated from, and agree with, T
  const note = decls(MERGE_LINES);
  T.check("the merge note declares --ease-out, --ease-expo and the seven --d-*", note.length === 9);
  note.forEach(function (d) {
    if (EASE_VARS[d.name]) T.check("merge note " + d.name + " == T.ease." + EASE_VARS[d.name], same(bez(d.value), TK.ease[EASE_VARS[d.name]]), d.value);
    else { const k = d.name.slice(4); T.check("merge note " + d.name + " == T.dur." + k + " x 1000", ms(d.value) === Math.round(TK.dur[k] * 1000), d.value); }
  });

  const css = styleText(HTML);
  const found = decls(css);
  T.check("index.html's stylesheets were read", css.length > 10000 && found.length >= 3, found.length);
  const byName = {};
  found.forEach(function (d) { (byName[d.name] = byName[d.name] || []).push(d.value); });
  const loadsRuntime = /<script src="\/aux_anim\.js"><\/script>/.test(HTML);
  const required = ALWAYS.concat(loadsRuntime ? ["--ease-out", "--ease-expo"].concat(DUR_VARS.map(function (k) { return "--d-" + k; })) : []);
  required.forEach(function (n) { T.check("index.html declares " + n, !!byName[n], Object.keys(byName)); });
  if (!loadsRuntime) {
    const pending = ["--ease-out", "--ease-expo"].concat(DUR_VARS.map(function (k) { return "--d-" + k; })).filter(function (n) { return !byName[n]; });
    if (pending.length) console.log("  SKIP " + pending.length + " mirrored tokens not in index.html yet (" + pending.join(" ") +
      "): the merge note adds them; required once index.html loads /aux_anim.js");
  }
  Object.keys(byName).forEach(function (n) {
    byName[n].forEach(function (v, i) {
      const where = n + (byName[n].length > 1 ? " (declaration " + (i + 1) + " of " + byName[n].length + ")" : "");
      if (EASE_VARS[n]) T.check(where + " == T.ease." + EASE_VARS[n], same(bez(v), TK.ease[EASE_VARS[n]]), [v, TK.ease[EASE_VARS[n]]]);
      else {
        const k = n.slice(4);
        T.check(where + " names a T.dur token", Object.prototype.hasOwnProperty.call(TK.dur, k), k);
        if (Object.prototype.hasOwnProperty.call(TK.dur, k)) T.check(where + " == T.dur." + k + " x 1000 ms", ms(v) === Math.round(TK.dur[k] * 1000), [v, TK.dur[k]]);
      }
    });
  });
  // the page's own JS spring curve is the same out-curve
  const sp = /const SPRING\s*=\s*'([^']+)'/.exec(HTML);
  T.check("index.html's SPRING const is cubic-bezier(T.ease.out)", !!sp && same(bez(sp[1]), TK.ease.out), sp && sp[1]);
  // where the tokens live: the light :root block, not a palette block
  if (loadsRuntime) {
    const root = /:root\{([\s\S]*?)\n  \}/.exec(css);
    T.check("the mirrored tokens sit in the first :root block", !!root && /--d-layout\s*:/.test(root[1]) && /--ease-out\s*:/.test(root[1]));
  }
  T.done();
})();
