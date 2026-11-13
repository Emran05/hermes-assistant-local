// Round-2 C96.1 (C96, 2026-10-21) — click-only targets get a keyboard way in:
//   * a hub widget card opened only on click: its title is now a button
//     (Enter/Space open the pop-out);
//   * Widget Center tiles and sidebar rows (conversations, Tools) were
//     click-only <div>s: they are Tab stops with Enter/Space;
//   * "Ask Hermes about this" lived only on contextmenu: Shift+F10 and the
//     ContextMenu key open it for whatever is focused inside a card;
//   * reordering was drag-only: in Customize, Alt+Arrow moves a card through
//     layoutOp and focus follows it.
//
// Boots the REAL index.html in the mini DOM (_a11y_dom.js) against stateful
// /api/hub and /api/layout routes.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

function hubRoutes(state) {
  const META = { clock: { title: "Clock", size: "tile", cat: "life" }, tasks: { title: "Tasks", size: "card", cat: "productivity" },
    weather: { title: "Weather", size: "tile", cat: "life" } };
  return {
    "/api/hub": function () {
      const widgets = {};
      state.order.forEach(function (id) { widgets[id] = { meta: META[id], data: id === "tasks" ? { tasks: [] } : {} }; });
      return { model_online: true, order: state.order.slice(), widgets: widgets };
    },
    "/api/layout": function (u, init) {
      const b = JSON.parse(init.body); state.posts.push(b);
      const o = state.order, i = o.indexOf(b.id);
      if (b.op === "move" && i >= 0) { const j = i + (b.dir === "down" ? 1 : -1); if (j >= 0 && j < o.length) { o[i] = o[j]; o[j] = b.id; } }
      if (b.op === "add" && i < 0) o.push(b.id);
      return { ok: true, order: o.slice() };
    },
    "/api/catalog": { catalog: [{ id: "clock", title: "Clock", cat: "life" }, { id: "weather", title: "Weather", cat: "life" }] },
    "/api/expand": { rich: false }
  };
}
const titleOf = function (d, id) { return d.querySelector('#wgrid .w[data-id="' + id + '"] .w-title'); };

(async function main() {
  console.log("C96.1: cards, tiles and rows are operable from the keyboard");

  await scenario("hub card title", async function () {
    const st = { order: ["clock", "tasks"], posts: [] };
    const w = await bootDashboard({ routes: hubRoutes(st) });
    const d = w.document;
    const t = titleOf(d, "clock");
    T.check("the card's title is a named Tab stop", !!t && t.getAttribute("role") === "button" && t.tabIndex === 0 && /Clock/.test(t.getAttribute("aria-label") || ""),
      t && [t.getAttribute("role"), t.tabIndex, t.getAttribute("aria-label")]);
    t.focus();
    const ev = w.__key(t, "Enter"); await w.__tick();
    T.check("Enter on it opens the pop-out", ev.defaultPrevented && !d.getElementById("wpop").hidden);
    w.__key(d.activeElement, "Escape"); w.__flushTimers(2); await w.__tick();
    T.check("Esc closes it and focus returns to the title", d.getElementById("wpop").hidden && d.activeElement === titleOf(d, "clock"));
    w.__key(titleOf(d, "clock"), " "); await w.__tick();
    T.check("Space opens it too", !d.getElementById("wpop").hidden);
    w.__run("closePop()"); w.__flushTimers(2); await w.__tick();
  });

  await scenario("Shift+F10 / ContextMenu key", async function () {
    const st = { order: ["clock", "tasks"], posts: [] };
    const w = await bootDashboard({ routes: hubRoutes(st) });
    const d = w.document;
    const t = titleOf(d, "tasks"); t.focus();
    const ev = w.__key(t, "F10", { shiftKey: true }); await w.__tick();
    const menu = d.querySelector("body>.ctxmenu");
    T.check("Shift+F10 opens the card's menu", ev.defaultPrevented && !!menu, !!menu);
    T.check("...offering Ask Hermes about this", !!menu && /Ask Hermes about this/.test(menu.textContent));
    T.check("...with focus on its first item", !!menu && menu.contains(d.activeElement));
    w.__key(d.activeElement, "Escape"); await w.__tick();
    T.check("Esc closes it and gives focus back", !d.querySelector("body>.ctxmenu") && d.activeElement === t);
    w.__key(t, "ContextMenu"); await w.__tick();
    T.check("the ContextMenu key opens it too", !!d.querySelector("body>.ctxmenu"));
  });

  await scenario("Customize: Alt+Arrow moves a card", async function () {
    const st = { order: ["clock", "tasks"], posts: [] };
    const w = await bootDashboard({ routes: hubRoutes(st) });
    const d = w.document;
    d.getElementById("edit-toggle").click(); await w.__tick();
    const t = titleOf(d, "tasks");
    T.check("in Customize the title says how to move it", /Alt\+arrow/i.test(t.getAttribute("aria-label") || ""), t.getAttribute("aria-label"));
    t.focus();
    const ev = w.__key(t, "ArrowUp", { altKey: true }); await w.__tick(); await w.__tick();
    T.check("Alt+ArrowUp asks the server to move it up", ev.defaultPrevented && st.posts.some(function (p) { return p.op === "move" && p.id === "tasks" && p.dir === "up"; }), st.posts);
    const ids = Array.from(d.querySelectorAll("#wgrid .w")).map(function (x) { return x.dataset.id; });
    T.check("the grid is re-rendered in the new order", ids.join() === "tasks,clock", ids);
    T.check("focus follows the moved card", d.activeElement === titleOf(d, "tasks"), d.activeElement && (d.activeElement.className || d.activeElement.tagName));
    w.__key(d.activeElement, "ArrowRight", { altKey: true }); await w.__tick(); await w.__tick();
    T.check("Alt+ArrowRight moves it back down", st.order.join() === "clock,tasks", st.order);
    // review: overlapping moves raced their own re-renders of the grid. Two
    // presses in a row run one after the other; a key HELD down (auto-repeat)
    // adds nothing while a move is still running. And Alt+Arrow inside a text
    // field is the field's own (move by word), never a card move.
    st.posts.length = 0;
    const t2 = titleOf(d, "tasks"); t2.focus();
    let inflight = 0, overlap = false;
    const lay = w.__routes["/api/layout"];
    w.__routes["/api/layout"] = async function (u, init) {
      if (++inflight > 1) overlap = true;
      await new Promise(function (r) { setImmediate(r); });
      try { return lay(u, init); } finally { inflight--; }
    };
    w.__key(t2, "ArrowUp", { altKey: true }); w.__key(t2, "ArrowDown", { altKey: true });
    for (let i = 0; i < 4; i++) await w.__tick();
    T.check("two presses in a row both move it, one after the other", !overlap && st.posts.map(function (p) { return p.dir; }).join() === "up,down" && st.order.join() === "clock,tasks", [overlap, st.posts, st.order]);
    st.posts.length = 0;
    const t3 = titleOf(d, "tasks"); t3.focus();
    w.__key(t3, "ArrowUp", { altKey: true }); w.__key(t3, "ArrowUp", { altKey: true, repeat: true }); w.__key(t3, "ArrowUp", { altKey: true, repeat: true });
    for (let i = 0; i < 4; i++) await w.__tick();
    T.check("a held key adds no backlog while a move runs", st.posts.length === 1, st.posts);
    w.__key(d.activeElement, "ArrowDown", { altKey: true }); for (let i = 0; i < 3; i++) await w.__tick();
    T.check("...and the next press after it works", st.posts.length === 2 && st.order.join() === "clock,tasks", [st.posts, st.order]);
    w.__routes["/api/layout"] = lay;
    const fld = d.createElement("input");
    d.querySelector('#wgrid .w[data-id="tasks"] .w-body').appendChild(fld); fld.focus();
    st.posts.length = 0;
    const fe = w.__key(fld, "ArrowLeft", { altKey: true }); await w.__tick(); await w.__tick();
    T.check("Alt+Arrow in a text field inside a card is left to the field", !fe.defaultPrevented && st.posts.length === 0, st.posts);
    st.posts.length = 0;
    d.getElementById("edit-toggle").click(); await w.__tick();
    titleOf(d, "tasks").focus(); w.__key(d.activeElement, "ArrowUp", { altKey: true }); await w.__tick();
    T.check("outside Customize Alt+Arrow does nothing", st.posts.length === 0, st.posts);
  });

  await scenario("Widget Center tiles", async function () {
    const st = { order: ["clock"], posts: [] };
    const w = await bootDashboard({ routes: hubRoutes(st) });
    const d = w.document;
    d.getElementById("add-widget").click(); await w.__tick(); await w.__tick();
    const tiles = Array.from(d.querySelectorAll("#wc-grid .mcard"));
    const wx = tiles.find(function (x) { return x.dataset.id === "weather"; });
    const ck = tiles.find(function (x) { return x.dataset.id === "clock"; });
    T.check("every tile is a named Tab stop", tiles.length === 2 && tiles.every(function (x) { return x.getAttribute("role") === "button" && x.tabIndex === 0; }),
      tiles.map(function (x) { return [x.getAttribute("role"), x.tabIndex]; }));
    T.check("an added widget's tile says so", ck && ck.getAttribute("aria-disabled") === "true" && /on your Hub/.test(ck.getAttribute("aria-label")), ck && ck.getAttribute("aria-label"));
    T.check("an available one is named Add …", wx && wx.getAttribute("aria-label") === "Add Weather", wx && wx.getAttribute("aria-label"));
    wx.focus(); w.__key(wx, "Enter"); await w.__tick(); await w.__tick();
    T.check("Enter on it adds the widget", st.posts.some(function (p) { return p.op === "add" && p.id === "weather"; }), st.posts);
    T.check("...and the tile now reports it is added", wx.getAttribute("aria-disabled") === "true", wx.getAttribute("aria-label"));
  });

  await scenario("sidebar rows", async function () {
    const chats = [];
    const w = await bootDashboard({ routes: {
      "/api/sessions": { sessions: [{ id: "chat-trip", title: "Trip plan" }], total: 1 },
      "/api/actions": { actions: [{ label: "Plan my day", prompt: "plan my day" }] },
      "/api/chat": function (u, init) { chats.push(JSON.parse(init.body).message); return { ok: true, reply: "ok" }; }
    } });
    const d = w.document;
    // the sidebar is drawn by renderChatSide() (shown on the Agent page only
    // since the Hub's full-screen chat went, 2026-11-12)
    w.__run("renderChatSide()"); await w.__tick(); await w.__tick();
    const row = Array.from(d.querySelectorAll("#cs-convos .cs-item")).find(function (r) { return /Trip plan/.test(r.textContent); });
    const t = row && row.querySelector(".t");
    T.check("a conversation's title is a named Tab stop", !!t && t.getAttribute("role") === "button" && t.tabIndex === 0, t && [t.getAttribute("role"), t.tabIndex]);
    t.focus(); w.__key(t, "Enter"); await w.__tick();
    T.check("Enter on it opens that conversation", w.__run("session") === "chat-trip", w.__run("session"));
    const tool = Array.from(d.querySelectorAll("#cs-tools .cs-item")).find(function (r) { return /Plan my day/.test(r.textContent); });
    T.check("a Tools row is a Tab stop", !!tool && tool.getAttribute("role") === "button" && tool.tabIndex === 0);
    tool.focus(); w.__key(tool, " "); await w.__tick(); await w.__tick();
    T.check("Space on it runs the action", chats.indexOf("plan my day") >= 0, chats);
  });
  T.done();
})();
