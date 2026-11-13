// C97 (docs/audits/2026-10-21-codebase-audit.md) — the hub Tasks widget: the
// check box ignored Enter/Space, the icon-only Add and Delete buttons had no
// name, and every task operation re-renders the hub, which dropped keyboard
// focus on <body>. The REAL index.html runs in the mini DOM against a fake
// /api/tasks store; nothing touches the dashboard.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

function tasksBackend(initial) {
  const store = { tasks: JSON.parse(JSON.stringify(initial)) }, ops = [];
  let n = 100;
  return {
    store: store, ops: ops,
    routes: {
      "/api/tasks": function (url, init) {
        const b = JSON.parse(init.body || "{}"); ops.push(b);
        if (b.op === "add") store.tasks.unshift({ id: String(++n), text: b.text, done: false });
        if (b.op === "toggle") store.tasks.forEach(function (t) { if (t.id === b.id) t.done = !t.done; });
        if (b.op === "delete") store.tasks = store.tasks.filter(function (t) { return t.id !== b.id; });
        return { ok: true };
      },
      "/api/hub": function () {
        return { order: ["tasks"], model_online: true,
          widgets: { tasks: { meta: { title: "Tasks", size: "card", cat: "productivity", icon: "spark" },
                              data: { tasks: JSON.parse(JSON.stringify(store.tasks)) } } } };
      }
    }
  };
}
const START = [{ id: "a", text: "Buy milk", done: false }, { id: "b", text: "Call Bob", done: true },
               { id: "c", text: "File taxes", done: false }];
function card(d) { return d.querySelector('#wgrid .w[data-id="tasks"]'); }
function check(d, id) { const r = Array.from(card(d).querySelectorAll(".task")).find(function (x) { return x.dataset.id === id; }); return r ? r.querySelector(".task-check") : null; }
function del(d, id) { const c = check(d, id); return c ? c.parentNode.querySelector("button") : null; }
async function settle(w) { await w.__tick(); w.__flushTimers(1); await w.__tick(); }

(async function main() {
  console.log("C97: hub Tasks widget — keyboard, names, focus across re-renders");
  let be = tasksBackend(START), w = await bootDashboard({ routes: be.routes });
  let d = w.document;

  await scenario("names and roles", async function () {
    const c = card(d);
    T.check("the Tasks card rendered", !!c && c.querySelectorAll(".task").length === 3);
    T.check("the new-task field is named", c.querySelector(".newtask").getAttribute("aria-label") === "New task");
    const add = c.querySelector(".addtask");
    T.check("the icon-only Add button is named", add.getAttribute("aria-label") === "Add task");
    const ca = check(d, "a"), cb = check(d, "b");
    T.check("each check box is a focusable role=checkbox", ca.getAttribute("role") === "checkbox" && ca.tabIndex === 0);
    T.check("...named after its task", ca.getAttribute("aria-label") === "Buy milk");
    T.check("...with its state", ca.getAttribute("aria-checked") === "false" && cb.getAttribute("aria-checked") === "true");
    T.check("each icon-only Delete button names its task", del(d, "a").getAttribute("aria-label") === "Delete task: Buy milk");
  });

  await scenario("Enter and Space toggle, focus stays on the task", async function () {
    const ca = check(d, "a");
    ca.focus();
    const ev = w.__key(ca, "Enter");
    await settle(w);
    T.check("Enter on a check box toggles that task", be.ops.length === 1 && be.ops[0].op === "toggle" && be.ops[0].id === "a", be.ops);
    T.check("Enter is consumed (no page action)", ev.defaultPrevented);
    const ca2 = check(d, "a");
    T.check("the hub re-rendered the row", ca2 && ca2 !== ca && ca2.getAttribute("aria-checked") === "true");
    T.check("focus is back on the same task's check box", d.activeElement === ca2, d.activeElement && d.activeElement.className);
    const sp = w.__key(d.activeElement, " ");
    await settle(w);
    T.check("Space toggles it back", be.ops.length === 2 && be.ops[1].id === "a" && check(d, "a").getAttribute("aria-checked") === "false");
    T.check("Space does not scroll the page", sp.defaultPrevented);
    T.check("...and focus is still on it", d.activeElement === check(d, "a"));
    // review: a held key auto-repeats — the tail of one press must not toggle
    // the task again (each repeat used to POST a toggle; the end state was the
    // parity of how long the key was held)
    const n0 = be.ops.length;
    const held = w.__key(d.activeElement, " ", { repeat: true });
    w.__key(d.activeElement, " ", { repeat: true });
    await settle(w);
    T.check("a held Space toggles once per press, not once per auto-repeat", be.ops.length === n0, be.ops.slice(n0));
    T.check("...and the repeats are still consumed (no page scroll)", held.defaultPrevented);
  });

  await scenario("delete and add", async function () {
    del(d, "a").focus();
    del(d, "a").click();
    await settle(w);
    T.check("Delete removes the task", !check(d, "a") && be.ops[be.ops.length - 1].op === "delete");
    T.check("focus moves to the task that took its place", d.activeElement === check(d, "b"), d.activeElement && (d.activeElement.className || d.activeElement.tagName));
    const inp = card(d).querySelector(".newtask");
    inp.focus(); inp.value = "Water plants";
    w.__key(inp, "Enter");
    await settle(w);
    T.check("Enter in the field adds the task", be.store.tasks[0].text === "Water plants");
    T.check("focus is back in the (new) field, ready for the next one",
      d.activeElement && d.activeElement.classList.contains("newtask") && d.activeElement !== inp);
    // delete the last one: focus falls back to the field, never to <body>
    be.store.tasks = [{ id: "z", text: "Only", done: false }];
    await w.__run("renderHub()"); await settle(w);
    del(d, "z").focus(); del(d, "z").click(); await settle(w);
    T.check("deleting the last task leaves focus in the field", d.activeElement && d.activeElement.classList.contains("newtask"),
      d.activeElement && d.activeElement.tagName);
  });

  await scenario("the 60s refresh keeps focus", async function () {
    be = tasksBackend(START); w = await bootDashboard({ routes: be.routes }); d = w.document;
    // keyboard focus (:focus-visible — this DOM's only kind): refreshHub
    // leaves the card holding it alone altogether (C99)
    let x = del(d, "c");
    x.focus();
    await w.__run("refreshHub()"); await settle(w);
    T.check("keyboard focus: a periodic refresh leaves that card alone (C99)", del(d, "c") === x);
    T.check("...so focus stays on the same Delete button", d.activeElement === x, d.activeElement && d.activeElement.tagName);
    // mouse focus (a clicked button is not :focus-visible): the card IS
    // re-filled, and the Tasks renderer puts focus back (C97)
    x = del(d, "c");
    const real = x.matches.bind(x);
    x.matches = function (sel) { return sel === ":focus-visible" ? false : real(sel); };
    x.focus();
    await w.__run("refreshHub()"); await settle(w);
    T.check("a periodic refresh re-rendered the card", del(d, "c") !== x);
    T.check("...and put focus back on the same Delete button", d.activeElement === del(d, "c"), d.activeElement && d.activeElement.tagName);
  });

  await scenario("focus the user moved away is left alone", async function () {
    const ca = check(d, "a");
    ca.focus();
    ca.click();                                  // mouse: the POST is in flight...
    d.getElementById("input").focus();           // ...and the user goes to the chat box
    await settle(w);
    T.check("the toggle still landed", be.ops.some(function (o) { return o.op === "toggle" && o.id === "a"; }));
    T.check("the re-render does not pull focus back into the card", d.activeElement === d.getElementById("input"),
      d.activeElement && (d.activeElement.id || d.activeElement.className));
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
