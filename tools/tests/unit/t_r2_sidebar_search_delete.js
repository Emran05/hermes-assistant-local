// Round-2 R2-31 — three sidebar / search bugs:
//   1. cvWhen() showed "in 1d" for anything up to a week ahead, including a
//      calendar hit two hours away;
//   2. Enter inside the 200 ms search debounce clicked the first result of
//      the PREVIOUS query (the list on screen had not been re-run yet);
//   3. the chat header's Delete (#delchat) ignored the response and switched
//      to a fresh conversation even when the delete had failed.
//
// Boots the REAL index.html in the mini DOM (_a11y_dom.js).
"use strict";
// The mini DOM exposes every element id as a window property before the page
// runs (browser "named access"), but unlike a browser it lets the #toast
// element shadow index.html's own function toast() — drop that one binding.
const NO_NAMED_TOAST = function (w) { delete w.toast; };
const { bootDashboard, harness, Event } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("R2-31: relative times ahead, Enter in the debounce, Delete checks its answer");

  await scenario("cvWhen ahead of now", async function () {
    const w = await bootDashboard();
    const at = function (dt) { return w.__run("cvWhen(Date.now()/1000+(" + dt + "))"); };
    T.check("two hours ahead reads in hours", at(2 * 3600) === "in 2h", at(2 * 3600));
    T.check("thirty minutes ahead reads in minutes", at(30 * 60) === "in 30m", at(30 * 60));
    T.check("twenty hours ahead is still hours", at(20 * 3600) === "in 20h", at(20 * 3600));
    T.check("three days ahead reads in days", at(3 * 86400 + 60) === "in 3d", at(3 * 86400 + 60));
    T.check("the past is unchanged", at(-2 * 3600) === "2h ago", at(-2 * 3600));
  });

  await scenario("Enter inside the debounce", async function () {
    const w = await bootDashboard({ routes: {
      "/api/search": function (url) {
        const q = new URL(url, "http://x").searchParams.get("q");
        return { ok: true, q: q, results: [{ id: "chat:chat-" + q, source: "chat", title: "Talk about " + q, ts: 0,
          snippet: q, mark_start: 0, mark_len: q.length, ref: "chat-" + q }], sources: { chat: 1 } };
      }
    } });
    const d = w.document, q = d.getElementById("chat-q");
    w.__run("cvOpenFind()");
    q.value = "older"; q.dispatchEvent(new Event("input", { bubbles: true }));
    w.__flushTimers(1); await w.__tick();
    const shown = d.getElementById("cvfind-list").querySelector(".cs-res");
    T.check("the first query's results are on screen", !!shown && /older/.test(shown.textContent), shown && shown.textContent);
    q.value = "newer"; q.dispatchEvent(new Event("input", { bubbles: true }));   // debounce queued, not fired
    w.__key(q, "Enter"); await w.__tick(); await w.__tick();
    T.check("Enter opens the CURRENT query's first hit", w.__run("session") === "chat-newer", w.__run("session"));
  });

  await scenario("#delchat when the delete fails", async function () {
    const w = await bootDashboard({ before: NO_NAMED_TOAST, routes: { "/api/sessions/delete": { __status: 500, body: { ok: false } } } });
    const d = w.document;
    w.__run("session='chat-keep'");
    d.getElementById("delchat").click(); await w.__tick();
    T.check("the conversation stays open", w.__run("session") === "chat-keep", w.__run("session"));
    T.check("the owner is told it did not work", /Could not delete/.test(d.getElementById("toast").textContent), d.getElementById("toast").textContent);
  });

  await scenario("#delchat when the delete answers ok:false", async function () {
    const w = await bootDashboard({ routes: { "/api/sessions/delete": { ok: false } } });
    w.__run("session='chat-keep2'");
    w.document.getElementById("delchat").click(); await w.__tick();
    T.check("the conversation stays open", w.__run("session") === "chat-keep2", w.__run("session"));
  });

  await scenario("#delchat when the delete works", async function () {
    const posted = [];
    const w = await bootDashboard({ routes: { "/api/sessions/delete": function (u, init) { posted.push(JSON.parse(init.body)); return { ok: true }; } } });
    w.__run("session='chat-gone'");
    w.document.getElementById("delchat").click(); await w.__tick();
    T.check("it deleted the open conversation", posted.length === 1 && posted[0].session === "chat-gone", posted);
    T.check("...and switched to a fresh one", w.__run("session") !== "chat-gone" && /^chat-/.test(w.__run("session")), w.__run("session"));
  });

  await scenario("#delchat cancelled", async function () {
    const posted = [];
    const w = await bootDashboard({ routes: { "/api/sessions/delete": function () { posted.push(1); return { ok: true }; } } });
    w.__confirm = false;
    w.__run("session='chat-stay'");
    w.document.getElementById("delchat").click(); await w.__tick();
    T.check("nothing is sent and nothing changes", posted.length === 0 && w.__run("session") === "chat-stay");
  });
  T.done();
})();
