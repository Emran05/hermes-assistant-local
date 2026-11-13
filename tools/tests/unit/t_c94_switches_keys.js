// C94 (docs/audits/2026-10-21-codebase-audit.md), the Shortcuts and Apple
// apps half (the watchtower switches are another branch's): both cards drew
// their switches as an unnamed <span role=switch> with no tabindex and no key
// handler, so the keyboard could not reach them and VoiceOver read "switch"
// with no idea what it controls.
//
// The REAL aux_shortcuts.js and aux_appleapps.js run on the real index.html
// in the mini DOM (_a11y_dom.js) against fake /api answers.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
function posts(w, path) {
  return w.__fetches.filter(function (f) { return f.url.split("?")[0] === path && f.init && f.init.method === "POST"; })
    .map(function (f) { return JSON.parse(f.init.body || "{}"); });
}

(async function main() {
  console.log("C94: Shortcuts + Apple apps switches are named, focusable, keyboard-operable");

  await scenario("Shortcuts expose switch", async function () {
    const state = { exposed: { s1: false, s2: true } };
    let hold = null;
    const w = await bootDashboard({
      aux: ["aux_shortcuts.js"],
      routes: {
        "/api/shortcuts": function () {
          return { ok: true, available: true, exposed_count: 1, pending: [], recent: [],
            shortcuts: [
              { id: "s1", name: "Log Water", exposed: state.exposed.s1, risk: { level: "low", label: "local" } },
              { id: "s2", name: "Send Daily Note", exposed: state.exposed.s2, risk: { level: "high", label: "sends" } }
            ] };
        },
        "/api/shortcuts/config": function (url, init) {
          const b = JSON.parse(init.body); state.exposed[b.id] = b.exposed;
          return hold ? hold.then(function () { return { ok: true }; }) : { ok: true };
        }
      }
    });
    const d = w.document;
    w.__run("setView('mind')");                  // the card lives in Settings: it must be on screen to take focus
    await w.__run("shortcutsPanel()"); await w.__tick();
    const togs = Array.from(d.querySelectorAll("#mind-extra-shortcuts .sb-tog"));
    T.check("one switch per shortcut", togs.length === 2, togs.length);
    T.check("each switch is a focusable role=switch", togs.every(function (t) { return t.getAttribute("role") === "switch" && t.tabIndex === 0; }));
    T.check("each switch is named after its shortcut", togs[0].getAttribute("aria-label") === "Expose Log Water to the agent", togs[0].getAttribute("aria-label"));
    T.check("...and carries its state", togs[0].getAttribute("aria-checked") === "false" && togs[1].getAttribute("aria-checked") === "true");

    togs[0].focus();
    T.check("a switch can take focus", d.activeElement === togs[0]);
    const ev = w.__key(togs[0], "Enter");
    await w.__tick();
    T.check("Enter flips the switch (after the confirm)", posts(w, "/api/shortcuts/config").length === 1 &&
      posts(w, "/api/shortcuts/config")[0].exposed === true && posts(w, "/api/shortcuts/config")[0].id === "s1");
    T.check("Enter is consumed", ev.defaultPrevented);
    const t0 = d.querySelector('#mind-extra-shortcuts .sb-row[data-id="s1"] .sb-tog');
    T.check("the re-rendered switch shows the new state", t0 && t0.getAttribute("aria-checked") === "true");
    T.check("focus is back on the same switch after the re-render", d.activeElement === t0, d.activeElement && d.activeElement.className);

    // Space, and a held key while the save is in flight
    let release; hold = new Promise(function (r) { release = r; });
    const before = posts(w, "/api/shortcuts/config").length;
    w.__key(t0, " "); w.__key(t0, " "); w.__key(t0, " ");
    T.check("while saving the switch says it is busy", t0.getAttribute("aria-disabled") === "true");
    release(); hold = null; await w.__tick();
    T.check("Space flips it once however long it is held", posts(w, "/api/shortcuts/config").length === before + 1, posts(w, "/api/shortcuts/config"));
    T.check("...back off", state.exposed.s1 === false);
    // review: once the save has settled the busy guard is gone, so the
    // auto-repeat tail of the same press flipped it again
    const n1 = posts(w, "/api/shortcuts/config").length;
    const tr = d.querySelector('#mind-extra-shortcuts .sb-row[data-id="s1"] .sb-tog');
    const rep = w.__key(tr, " ", { repeat: true }); await w.__tick();
    T.check("an auto-repeat after the save settled does not flip it again", posts(w, "/api/shortcuts/config").length === n1 && rep.defaultPrevented,
      posts(w, "/api/shortcuts/config").slice(n1));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("Apple apps switches", async function () {
    const state = { reminders: false, notes: true };
    const w = await bootDashboard({
      aux: ["aux_appleapps.js"],
      before: function (w0) {
        // the Settings › Connections panel aux_settings_shell.js builds
        const p = w0.document.createElement("div"); p.id = "sec-connections";
        p.innerHTML = '<div class="set-body"></div>';
        w0.document.body.appendChild(p);
      },
      routes: {
        "/api/apple_apps": function (url, init) {
          if (init && init.method === "POST") Object.assign(state, JSON.parse(init.body));
          return { ok: true, reminders: state.reminders, notes: state.notes };
        }
      }
    });
    const d = w.document;
    await w.__run("window.hermesAppleApps.mount()"); await w.__tick();
    const sws = Array.from(d.querySelectorAll("#mind-extra-appleapps .aasw"));
    T.check("one switch per app", sws.length === 2, sws.length);
    T.check("each is a focusable role=switch", sws.every(function (s) { return s.getAttribute("role") === "switch" && s.tabIndex === 0; }));
    T.check("each is named after its app", sws[0].getAttribute("aria-label") === "Reminders automation" &&
      sws[1].getAttribute("aria-label") === "Notes automation", sws.map(function (s) { return s.getAttribute("aria-label"); }));
    T.check("...and carries its state", sws[0].getAttribute("aria-checked") === "false" && sws[1].getAttribute("aria-checked") === "true");
    sws[0].focus();
    T.check("a switch can take focus", d.activeElement === sws[0]);
    const ev = w.__key(sws[0], " ");
    T.check("Space is consumed", ev.defaultPrevented);
    const busy = d.querySelector('#mind-extra-appleapps .aasw[data-k="reminders"]');
    T.check("while saving it says it is busy", busy && busy.getAttribute("aria-disabled") === "true");
    T.check("...and keeps focus through the busy repaint", d.activeElement === busy);
    await w.__tick();
    const now = d.querySelector('#mind-extra-appleapps .aasw[data-k="reminders"]');
    T.check("Space switched Reminders on", state.reminders === true && now.getAttribute("aria-checked") === "true");
    T.check("focus is on the Reminders switch after the repaint", d.activeElement === now);
    w.__key(now, "Enter"); await w.__tick();
    T.check("Enter switches it back off", state.reminders === false);
    // review: the auto-repeat tail of the same press (after the save settled)
    const cur = d.querySelector('#mind-extra-appleapps .aasw[data-k="reminders"]');
    const rep = w.__key(cur, "Enter", { repeat: true }); await w.__tick();
    T.check("an auto-repeat after the save settled does not flip it again", state.reminders === false && rep.defaultPrevented);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
