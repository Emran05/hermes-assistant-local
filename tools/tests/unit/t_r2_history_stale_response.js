// Round-2 R2-30 — loadHistory() cleared #msgs only at the START, before
// awaiting /api/history. Switch conversations quickly (sidebar clicks, the
// dropdown, a search hit, New chat) and a slower, OLDER response appended its
// bubbles into the newer conversation. Each load now remembers its
// conversation and a sequence number and draws nothing once either moved on.
//
// Boots the REAL index.html in the mini DOM (_a11y_dom.js) with an
// /api/history route the test answers out of order.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

function msgsFor(sid) {
  return { messages: [{ role: "user", text: "question in " + sid }, { role: "bot", text: "answer in " + sid }] };
}

(async function main() {
  console.log("R2-30: an older /api/history response never draws into a newer conversation");
  const pending = {};
  const w = await bootDashboard({ routes: {
    "/api/history": function (url) {
      const sid = new URL(url, "http://x").searchParams.get("session");
      return new Promise(function (r) { (pending[sid] = pending[sid] || []).push(function () { r(msgsFor(sid)); }); });
    }
  } });
  const d = w.document;
  const bubbles = function () { return Array.from(d.getElementById("msgs").querySelectorAll(".bubble")).map(function (b) { return b.textContent.replace(/\s+/g, " ").trim(); }); };
  const answer = async function (sid) { const f = (pending[sid] || []).shift(); if (f) f(); await w.__tick(); };

  await scenario("older answers last", async function () {
    w.__run("session='chat-a'"); const pa = w.__run("loadHistory()");
    w.__run("session='chat-b'"); const pb = w.__run("loadHistory()");
    await answer("chat-b"); await answer("chat-a"); await pa; await pb;
    const b = bubbles();
    T.check("only the newer conversation's messages are shown", b.length === 2 && b.every(function (t) { return /chat-b/.test(t); }), b);
  });

  await scenario("older answers first", async function () {
    w.__run("session='chat-c'"); const pc = w.__run("loadHistory()");
    w.__run("session='chat-d'"); const pd = w.__run("loadHistory()");
    await answer("chat-c"); await answer("chat-d"); await pc; await pd;
    const b = bubbles();
    T.check("the stale load draws nothing, the current one draws once", b.length === 2 && b.every(function (t) { return /chat-d/.test(t); }), b);
  });

  await scenario("New chat while a load is in flight", async function () {
    w.__run("session='chat-e'"); const pe = w.__run("loadHistory()");
    d.getElementById("newchat").click(); await w.__tick();
    await answer("chat-e"); await pe;
    const b = bubbles();
    T.check("the fresh conversation stays empty", b.length === 0 && !!d.getElementById("msgs").querySelector(".empty-chat"), b);
  });

  await scenario("an ordinary load still renders", async function () {
    w.__run("session='chat-f'"); const pf = w.__run("loadHistory()");
    await answer("chat-f"); await pf;
    const b = bubbles();
    T.check("both of its bubbles are drawn", b.length === 2 && /question in chat-f/.test(b[0]), b);
    T.check("histLoading is back to zero", w.__run("histLoading") === 0);
  });
  T.done();
})();
