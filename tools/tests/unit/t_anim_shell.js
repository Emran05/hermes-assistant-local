// t_anim_shell.js — the real dashboard page on the motion runtime
// (docs/plans/motion-system-2026-10.md §6.1 load order, §6.7 the legacy
// animate() shim and HA()/ANIM_NULL).
//
// index.html is not owned by the runtime cluster, so its two edits arrive
// through merge notes: the script tags and the §6.7 shim below (SHIM_*). Until
// the page carries them this suite applies the SAME text in memory, so the
// merge note is exercised against the real page; afterwards it runs the page
// as it is. Either way it boots index.html's real inline shell after
// aux_anim.js (the §6.1 order), with aux_prefs.js and aux_glass.js, against
// the recording library stubs.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const PAGE = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");

// ---- the merge note's index.html text (kept identical to merge_notes) ----
const OLD_ANIMATE =
  "// Motion One (vanilla Framer Motion) — spring animations for pop-outs.\n" +
  "const M=window.Motion||null;\n" +
  "const animate=(el,kf,opt)=>{try{return M?M.animate(el,kf,opt):el.animate(kf,opt);}catch(e){}};\n";
const SHIM_ANIMATE =
  "// Motion 14 behind the motion runtime (aux_anim.js -> window.HermesAnim,\n" +
  "// docs/plans/motion-system-2026-10.md 6.7). animate() keeps its call shape for\n" +
  "// the ~60 call sites; HermesAnim.legacy turns the WAAPI-style `easing` Motion\n" +
  "// ignores into `ease`, and is instant while reduced, hidden or quiet.\n" +
  "const M=window.Motion||null;\n" +
  "const animate=(el,kf,opt)=>{try{const A=window.HermesAnim;\n" +
  "  return A?A.legacy(el,kf,opt):(M?M.animate(el,kf,opt):el.animate(kf,opt));}catch(e){}};\n" +
  "// HA(): the runtime, or ANIM_NULL when aux_anim.js did not load. ANIM_NULL lands\n" +
  "// every call at its end state at once (exit's done and flip's mutate run before\n" +
  "// return); hold() is a plain setTimeout, because reading time is not motion.\n" +
  "const ANIM_NULL=(()=>{const H=()=>({done:true,finished:Promise.resolve(),cancel(){},finish(){}});\n" +
  "  const run=f=>{try{if(typeof f==='function')f();}catch(e){}return H();};const seen=new Set();\n" +
  "  return Object.freeze({v:0,T:null,live:()=>false,quiet:f=>f(),once:k=>!seen.has(k)&&!!seen.add(k),\n" +
  "    reduced:()=>document.documentElement.hasAttribute('data-reduce')||matchMedia('(prefers-reduced-motion: reduce)').matches,\n" +
  "    ready:()=>false,load:()=>Promise.resolve(false),enter:H,stagger:H,draw:H,words:H,settle(){},stats:()=>null,\n" +
  "    exit:(t,p,d)=>run(d),flip:(s,m)=>run(m),isFlipping:()=>false,legacy:(el,kf,o)=>animate(el,kf,o),\n" +
  "    spring:(el,v)=>{animate(el,v,{duration:0});return H();},\n" +
  "    sequence:(n,b,o)=>{o=o||{};try{if(o.instant)o.instant();}catch(e){}return run(o.done);},\n" +
  "    hold:(s,f)=>{let id=setTimeout(()=>{id=0;run(f);},(+s||0)*1000);\n" +
  "      return {done:false,finished:Promise.resolve(),cancel(){clearTimeout(id);id=0;},finish(){if(id){clearTimeout(id);id=0;run(f);}}};},\n" +
  "    count:(el,to,o)=>{try{const s=String(o&&o.fmt?o.fmt(+to):Math.round(+to||0).toLocaleString());\n" +
  "      if(el&&el.textContent!==s)el.textContent=s;if(el)el.__haVal=+to;}catch(e){}return H();}});})();\n" +
  "const HA=()=>window.HermesAnim||ANIM_NULL;\n";
const OLD_REVEAL = "function revealStagger(nodes,step){\n  if(REDUCE||!nodes||!nodes.length)return;\n";
const SHIM_REVEAL = "function revealStagger(nodes,step){\n  const A=window.HermesAnim;\n" +
  "  if(A)return A.stagger(nodes,'rise',{step:(step||55)/1000});\n  if(REDUCE||!nodes||!nodes.length)return;\n";
const OLD_TAGS = '<script src="/motion.min.js"></script>\n<script src="/aux_md.js"></script>\n';
const NEW_TAGS = '<script src="/vendor/motion/motion-14.6.0.min.js"></script>\n<script src="/aux_anim.js"></script>\n<script src="/aux_md.js"></script>\n';

function page() {
  let h = PAGE, applied = [];
  if (h.indexOf("A.legacy(el,kf,opt)") < 0) {
    if (h.indexOf(OLD_ANIMATE) < 0 || h.indexOf(OLD_REVEAL) < 0) return { html: null, applied: applied };
    h = h.replace(OLD_ANIMATE, SHIM_ANIMATE).replace(OLD_REVEAL, SHIM_REVEAL); applied.push("6.7 shim");
  }
  if (h.indexOf('src="/aux_anim.js"') < 0 && h.indexOf(OLD_TAGS) >= 0) { h = h.replace(OLD_TAGS, NEW_TAGS); applied.push("script tags"); }
  return { html: h, applied: applied };
}
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}
const HUB = { order: ["clock", "notes"], model_online: true, widgets: {
  clock: { meta: { title: "Clock", size: "card", cat: "life", icon: "clock" }, data: { time: "9:41" } },
  notes: { meta: { title: "Notes", size: "card", cat: "life", icon: "notes" }, data: { text: "" } } } };

(async function main() {
  console.log("the dashboard page on the motion runtime");
  const P = page();
  if (!P.html) { console.log("SKIP index.html's animate()/revealStagger() lines changed; update SHIM_* (and the merge note) to match"); console.log("TESTS 0 passed 0 failed"); return; }
  console.log("  (merge-note text applied in memory: " + (P.applied.join(", ") || "none, the page already carries it") + ")");
  const html = P.html;
  T.check("the page loads Motion, then aux_anim, before the inline shell (§6.1)", (function () {
    const iM = html.indexOf('src="/vendor/motion/motion-'), iA = html.indexOf('src="/aux_anim.js"'), iI = html.indexOf("<script>\nconst $=");
    return iM > 0 && iM < iA && iA < iI;
  })());

  let L = null;
  const withRuntime = function (w0) { L = S.install(w0, {}); w0.__run(ANIM, "aux_anim.js"); };

  await scenario("boot with the runtime", async function () {
    const w = await bootDashboard({ html: html, aux: ["aux_prefs.js", "aux_glass.js"], routes: { "/api/hub": HUB }, before: withRuntime });
    const d = w.document;
    T.check("the page boots with no error", w.__errors.length === 0, w.__errors.map(String));
    T.check("HermesAnim is there, and HA() returns it", !!w.HermesAnim && w.__run("HA()===window.HermesAnim"));
    T.check("aux_prefs still wraps Motion.animate (the second reduced-motion layer)", !!w.Motion.animate.__hermesStill);
    const cards = d.querySelectorAll("#wgrid .w");
    T.check("the hub rendered its cards", cards.length === 2, cards.length);
    const boot = L.of("motion", "animate").filter(function (c) { return c.el && c.el.classList && c.el.classList.contains("w"); });
    T.check("renderHub's revealStagger went through HermesAnim.stagger (Motion, rise)", boot.length === 2 &&
      JSON.stringify(boot[0].kf) === '{"opacity":[0,1],"y":[10,0]}' && boot[1].opt.delay > 0, boot.map(function (c) { return c.opt.delay; }));
    L.finishAll(); await w.__tick();
    T.check("...and the cards end under CSS control", Array.prototype.every.call(cards, function (c) { return c.style.opacity === "" && c.style.transform === ""; }));
    const el = d.createElement("div"); d.body.appendChild(el); w.__el = el;
    w.__run("animate(window.__el,{opacity:[0,1],scale:[0.92,1]},{duration:0.45,delay:0.1,easing:'cubic-bezier(.2,.7,.3,1)'})");
    let o = L.of("motion", "animate").pop().opt;
    T.check("animate() now delivers the easing its author wrote (E5: Motion 11 ignored `easing`)", JSON.stringify(o.ease) === "[0.2,0.7,0.3,1]" && o.duration === .45 && !("easing" in o), o);
    d.documentElement.setAttribute("data-reduce", "1");
    await w.__tick();
    w.__run("animate(window.__el,{opacity:[0,1]},{duration:0.45,delay:0.1,repeat:Infinity})");
    o = L.of("motion", "animate").pop().opt;
    T.check("the in-app switch: animate() asks Motion for no time (t_c03's contract, through both layers)", o.duration === 0 && o.delay === 0 && o.repeat === 0, o);
    const n0 = L.of("motion", "animate").length;
    w.__host = d.getElementById("wgrid");
    w.__run("revealStagger(window.__host.querySelectorAll('.w'),35)");
    T.check("revealStagger under reduce: nothing animates", L.of("motion", "animate").length === n0);
    T.check("Motion's skipAnimations follows the switch", w.Motion.MotionGlobalConfig.skipAnimations === true);
    d.documentElement.removeAttribute("data-reduce");
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("the Preferences switch reaches the runtime", async function () {
    const w = await bootDashboard({ html: html, aux: ["aux_prefs.js"], before: withRuntime });
    const d = w.document, A = w.HermesAnim;
    const el = d.createElement("div"); d.body.appendChild(el);
    const h = A.enter(el, "rise");
    const gear = d.getElementById("prefs-btn"), row = d.querySelector('#prefs-menu [role="menuitemcheckbox"]');
    T.check("(the Preferences menu is there)", !!gear && !!row);
    gear.click(); row.click();
    await w.__tick();
    T.check("turning Reduce motion on lands a running HermesAnim animation", d.documentElement.hasAttribute("data-reduce") && h.done === true);
    T.check("...and later calls are instant", A.enter(el, "pop").done === true && A.reduced());
    row.click(); await w.__tick();
    T.check("...off again: live", !d.documentElement.hasAttribute("data-reduce") && A.live());
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("without the runtime: ANIM_NULL", async function () {
    const w = await bootDashboard({ html: html, aux: ["aux_prefs.js"], before: function (w0) { L = S.install(w0, {}); } });
    const d = w.document;
    T.check("the page boots with no error and no HermesAnim", w.__errors.length === 0 && !w.HermesAnim, w.__errors.map(String));
    T.check("HA() is ANIM_NULL", w.__run("HA()===ANIM_NULL && Object.isFrozen(ANIM_NULL)"));
    const r = w.__run("(function(){var o={};var el=document.createElement('div');" +
      "var x=0;HA().exit(el,'popOut',function(){x++;});o.exit=x;" +
      "var m=0;HA().flip(el,function(){m++;});o.flip=m;" +
      "var s=[];HA().sequence('swap',function(){s.push('build');},{instant:function(){s.push('instant');},done:function(){s.push('done');}});o.seq=s.join();" +
      "var n=document.createElement('span');HA().count(n,1234);o.count=n.textContent;" +
      "HA().count(n,2000,{fmt:function(v){return '$'+v;}});o.fmt=n.textContent;" +
      "o.once=[HA().once('a'),HA().once('a')].join();o.quiet=HA().quiet(function(){return 7;});" +
      "o.enter=HA().enter(el,'rise').done;o.live=HA().live();return o;})()");
    T.check("exit runs done before returning", r.exit === 1);
    T.check("flip runs mutate", r.flip === 1);
    T.check("sequence runs instant then done, never build", r.seq === "instant,done", r.seq);
    T.check("count writes the final text (with fmt)", r.count === "1,234" && r.fmt === "$2000", [r.count, r.fmt]);
    T.check("once/quiet/enter/live behave", r.once === "true,false" && r.quiet === 7 && r.enter === true && r.live === false, r);
    const t0 = w.__timers.length;
    w.__run("window.__held=0;window.__hh=HA().hold(1.3,function(){window.__held++;})");
    T.check("hold is one setTimeout of the reading time", w.__timers.length === t0 + 1 && w.__timers[w.__timers.length - 1].ms === 1300);
    w.__run("window.__hh.finish();window.__hh.finish();");
    T.check("...finish() runs it once, now", w.__held === 1);
    w.__el = d.createElement("div");
    w.__run("animate(window.__el,{opacity:[0,1]},{duration:0.3})");
    T.check("animate() falls back to Motion directly", L.of("motion", "animate").length >= 1);
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
