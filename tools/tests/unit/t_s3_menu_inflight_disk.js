// Review R4-seams (E105 / S2-5): the model menu's disk pre-check agrees with
// the server's refusal rule.
//
// download_model() takes what the downloads already in flight still have to
// fetch off the free space before it judges another pull. The menu's own
// pre-check judged every row against the WHOLE free space, so with one large
// download running it offered a second one the server then refused. Each
// downloading row now carries `remaining_gb` (server.py models_payload), and
// the menu subtracts their sum the way the server does:
//     (free - inflight) - download < headroom  ->  "not enough disk", inert.
//
// The REAL index.html runs in the mini DOM; /api/models is a fixture.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const NBSP = String.fromCharCode(160);
const plain = function (s) { return String(s == null ? "" : s).split(NBSP).join(" "); };

function row(id, o) {
  return Object.assign({ id: id, label: id.split("/")[1], ram: 8, note: "", active: false, downloaded: false,
    downloading: false, download_gb: null, remaining_gb: null, fit: "ok", download_error: null }, o || {});
}
function payload(models, free) {
  return { active: "org/live", paused: false, idle_suspended: false, idle_enabled: true, idle_min: 10, ram_gb: null,
    disk_free_gb: free, disk_headroom_gb: 5, mem: { machine_gb: 64 }, bg: { model: "", online: false, label: "" },
    thinking: { supported: false }, prewarm: {}, models: models };
}

(async function main() {
  console.log("S3: the menu's disk pre-check counts the downloads in flight");
  let current = null;
  const w = await bootDashboard({ routes: { "/api/models": function () { return current; } } });
  const d = w.document;
  const menu = d.getElementById("model-menu");
  const get = function (id) {
    const el = Array.prototype.slice.call(menu.querySelectorAll(".mmi")).filter(function (r) { return r.dataset.id === id; })[0];
    if (!el) return null;
    const dk = el.querySelector(".mmdisk"), act = el.querySelector(".act");
    return { disk: el.dataset.disk || null, inert: el.getAttribute("aria-disabled") === "true",
      act: act ? plain(act.textContent) : null, why: dk ? plain(dk.textContent) : null };
  };
  const show = async function (models, free) { current = payload(models, free); await w.loadModels(); await w.__tick(4); };
  const live = row("org/live", { active: true, downloaded: true, download_gb: 16 });

  // 40 GB free; a 20 GB pull is in flight with 18 GB still to come
  await show([live,
    row("org/a", { downloading: true, download_gb: 20, remaining_gb: 18 }),
    row("org/b", { download_gb: 18 }),            // (40 - 18) - 18 = 4  < 5   -> refused
    row("org/c", { download_gb: 3 }),             // (40 - 18) - 3  = 19       -> offered
    row("org/u", { download_gb: null })], 40);    // unknown size              -> never refused
  let b = get("org/b"), c = get("org/c"), a = get("org/a"), u = get("org/u");
  T.check("a second big pull beside one in flight is refused in the menu", b && b.disk === "short" && b.inert && b.act === "not enough disk", b);
  T.check("...and the reason names what the downloads in progress still need",
    b && /needs ~?18 GB/.test(b.why || "") && /40 GB free/.test(b.why || "") && /18 GB of it still to come for the downloads in progress/.test(b.why || ""), b && b.why);
  T.check("...and how much to free: 18 + 5 - (40 - 18) = 1 GB", b && /free up ~?1 GB/.test(b.why || ""), b && b.why);
  T.check("a small pull that still fits is offered", c && c.disk === null && !c.inert && /^download/.test(c.act || ""), c);
  T.check("the row that is itself downloading is never 'not enough disk'", a && a.disk === null && /^downloading/.test(a.act || ""), a);
  T.check("an unknown download size is never refused", u && u.disk === null && !u.inert, u);

  // the same disk with that download nearly finished: only its remainder counts
  await show([live,
    row("org/a", { downloading: true, download_gb: 20, remaining_gb: 2 }),
    row("org/b", { download_gb: 18 })], 40);      // (40 - 2) - 18 = 20
  b = get("org/b");
  T.check("with only 2 GB left to come the second pull is offered", b && b.disk === null && !b.inert, b);

  // exactly at the boundary, as the server computes it: (free - inflight) - dl == headroom is allowed
  await show([live,
    row("org/a", { downloading: true, download_gb: 20, remaining_gb: 17 }),
    row("org/b", { download_gb: 18 })], 40);      // 23 - 18 = 5  -> not < 5
  T.check("exactly the headroom left is allowed (the server's `<`)", get("org/b") && get("org/b").disk === null, get("org/b"));

  // an in-flight download of unknown size counts 0 (the server's rule), and odd values are ignored
  await show([live,
    row("org/a", { downloading: true, download_gb: null, remaining_gb: 0 }),
    row("org/x", { downloading: true, download_gb: 9, remaining_gb: "12" }),
    row("org/y", { downloading: true, download_gb: 9, remaining_gb: -4 }),
    row("org/z", { downloading: false, download_gb: 9, remaining_gb: 30 }),   // not in flight: its number is not counted
    row("org/b", { download_gb: 18 })], 40);
  T.check("unknown, non-numeric, negative and not-in-flight remainders take nothing off", get("org/b") && get("org/b").disk === null, get("org/b"));

  // nothing in flight: the old rule and the old words
  await show([live, row("org/b", { download_gb: 18 })], 20);
  b = get("org/b");
  T.check("nothing in flight: refused on the plain rule (20 - 18 < 5)", b && b.disk === "short", b);
  T.check("...with no in-flight clause", b && !/in progress/.test(b.why || "") && /free up ~?3 GB/.test(b.why || ""), b && b.why);

  // an older backend (no remaining_gb at all) keeps working
  const old = [live, { id: "org/a", label: "a", ram: 8, note: "", active: false, downloaded: false, downloading: true, download_gb: 20, fit: "ok" },
    { id: "org/b", label: "b", ram: 8, note: "", active: false, downloaded: false, downloading: false, download_gb: 18, fit: "ok" }];
  await show(old, 40);
  T.check("a payload without remaining_gb judges against the whole free space (no crash)", get("org/b") && get("org/b").disk === null, get("org/b"));
  T.done();
})().catch(function (e) { console.log("  FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
