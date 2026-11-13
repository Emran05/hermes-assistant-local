// E28 (docs/audits/2026-11-09-codebase-audit.md), the Settings half: holding
// Space or Enter on Settings › Claude Bridge › "Escalate to Claude" sent one
// POST per auto-repeat (12 from one held key) and could leave the privacy
// switch ON. Now a repeat is ignored, a flip in flight ignores further input,
// and the switch shows what the SERVER answered.
//
// The REAL index.html + aux_settings_shell.js run in the mini DOM
// (_a11y_dom.js) against a stateful, slow /api/claude/escalate.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("E28: a held key flips the escalation switch once");
  const state = { enabled: false, posts: [], hold: null, answer: null, fail: null };
  const w = await bootDashboard({
    aux: ["aux_settings_shell.js"],
    routes: {
      "/api/claude/escalate": function (url, init) {
        if (init && init.method === "POST") {
          const want = JSON.parse(init.body).enabled === true;
          state.posts.push(want);
          state.enabled = state.answer === null ? want : state.answer;
          if (state.fail === "500") return { __status: 500, body: { error: "boom" } };
          if (state.fail === "drop") return { __reject: "network down" };
          const reply = { ok: true, enabled: state.enabled };
          return state.hold ? state.hold.then(function () { return reply; }) : reply;
        }
        return { enabled: state.enabled };
      }
    }
  });
  await w.__tick();
  const d = w.document;
  const tog = function () { return d.getElementById("set-esc-tog"); };
  T.check("the switch is rendered", !!tog());
  const checked = function () { return tog().getAttribute("aria-checked"); };

  // 1. a held Space: one keyDown, then auto-repeats
  state.posts = [];
  let ev = w.__key(tog(), " ");
  for (let i = 0; i < 11; i++) w.__key(tog(), " ", { repeat: true });
  await w.__tick(); await w.__tick();
  T.check("one held Space sends ONE POST", state.posts.length === 1, state.posts);
  T.check("...asking to turn it ON", state.posts[0] === true, state.posts);
  T.check("...and the switch reads ON", checked() === "true", checked());
  T.check("the key is consumed", ev.defaultPrevented);

  // 2. a repeat alone (no first press reached us) does nothing
  state.posts = [];
  ev = w.__key(tog(), "Enter", { repeat: true });
  await w.__tick();
  T.check("a lone auto-repeat sends nothing", state.posts.length === 0, state.posts);
  T.check("...but is still consumed", ev.defaultPrevented);

  // 3. while a flip is in flight, every further press is ignored
  let release; state.hold = new Promise(function (r) { release = r; });
  state.posts = [];
  w.__key(tog(), "Enter");
  await w.__tick();
  w.__key(tog(), "Enter");
  w.__click(tog());
  w.__key(tog(), " ");
  await w.__tick();
  T.check("presses during a flight are dropped", state.posts.length === 1 && state.posts[0] === false, state.posts);
  T.check("the switch says it is busy", tog().getAttribute("aria-busy") === "true", tog().getAttribute("aria-busy"));
  release(); state.hold = null;
  await w.__tick(); await w.__tick();
  T.check("after the flight the switch reads OFF", checked() === "false", checked());
  T.check("...and is no longer busy", tog().getAttribute("aria-busy") !== "true");
  state.posts = [];
  w.__key(tog(), "Enter");
  await w.__tick(); await w.__tick();
  T.check("a new press after the flight works", state.posts.length === 1 && state.posts[0] === true, state.posts);

  // 4. the switch shows the server's answer, not what it asked for
  state.answer = false; state.posts = [];
  w.__key(tog(), "Enter");                        // currently ON -> asks OFF; server says false
  await w.__tick(); await w.__tick();
  state.posts = [];
  w.__key(tog(), "Enter");                        // asks ON; server refuses and stays OFF
  await w.__tick(); await w.__tick();
  T.check("asked ON", state.posts[0] === true, state.posts);
  T.check("the server said OFF, so the switch reads OFF", checked() === "false", checked());
  state.answer = null;

  // 4b. (review) a failed POST: never stuck busy, and the switch re-reads the
  // server, which may have applied the flip before its answer was lost
  let evs = 0;
  w.addEventListener("hermes:claude-escalation", function () { evs++; });
  for (const mode of ["500", "drop"]) {
    state.fail = mode; state.posts = [];
    const before = checked();
    w.__key(tog(), "Enter");
    for (let i = 0; i < 6; i++) await w.__tick();
    state.fail = null;
    T.check(mode + ": one POST was sent", state.posts.length === 1, state.posts);
    T.check(mode + ": not left busy", tog().getAttribute("aria-busy") !== "true");
    T.check(mode + ": the switch shows what the server now holds",
            checked() === String(state.enabled) && checked() !== before, [checked(), state.enabled]);
    T.check(mode + ": the error line says so",
            /Could not reach/.test((d.getElementById("set-esc-err") || {}).textContent || ""));
    T.check(mode + ": the other surfaces were told (hermes:claude-escalation)", evs > 0, evs);
    evs = 0;
    state.posts = [];
    w.__key(tog(), "Enter");
    for (let i = 0; i < 4; i++) await w.__tick();
    T.check(mode + ": the next press works", state.posts.length === 1, state.posts);
  }

  // 5. the same file's kill switch: an auto-repeat does not pause again
  const kill = d.getElementById("set-kill");
  T.check("the kill row exists", !!kill);
  if (kill) {
    let pauses = 0;
    w.pauseAgent = function () { pauses++; };
    w.__key(kill, "Enter", { repeat: true });
    w.__key(kill, " ", { repeat: true });
    T.check("a held key on the kill row does not pause per repeat", pauses === 0, pauses);
  }

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
