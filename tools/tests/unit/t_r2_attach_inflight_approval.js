// Round-2 review X-approval-flow-1 — an approval card that no window is
// streaming could not be answered, and the one-turn-per-chat 409 (B11/C113)
// then locked the conversation until hermes_rpc's 290 s TTL denied it.
// The card was drawn only inside streamJob, whose only callers were sendMsg
// and Quick Ask's hand-off: after a ⌘R / relaunch the reopened conversation
// showed the question and no card; a re-send got 409 {busy, busy_job} and
// sendMsg ignored busy_job; the Needs-you approval row's Open just scrolled
// whatever chat was on screen.
// Now (index.html): loadHistory attaches to the conversation's running turn
// (/api/history's inflight_job, server.py), a 409 reloads the conversation
// with busy_job as the hint and attaches, and window.hermesOpenConversation
// (used by aux_needsyou.js's Open on an approval row) opens the row's own
// conversation — never a reserved one (R2-32). streamJob streams a job once
// per window, so Quick Ask's hand-off does not draw the reply twice.
//
// Boots the REAL index.html (plus the real aux_needsyou.js / aux_quickask.js
// where needed) in the mini DOM (_a11y_dom.js) against a mocked turn that is
// waiting on an approval.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
const NO_NAMED_TOAST = function (w) { delete w.toast; };
const CMD = 'computer_use type "hello" in "Notes"';
const BUSY = "Hermes is still answering your previous message in this conversation. Wait for that reply (or answer its approval), then send this again.";

// one running turn J7 in chat-appr, waiting on approval a1 until answered
function serverState(opts) {
  opts = opts || {};
  const st = { answered: null, approves: [], chats: [], acts: [] };
  st.routes = {
    "/api/history": function (url) {
      const sid = new URL(url, "http://x").searchParams.get("session");
      const d = { messages: sid === (opts.sess || "chat-appr") ? [{ role: "user", text: "type hello in notes" }] : [] };
      if (!opts.oldServer) d.inflight_job = (sid === (opts.sess || "chat-appr") && !st.answered) ? (opts.job || "J7") : null;
      return d;
    },
    "/api/chat/poll": function () {
      if (st.answered) return { ok: true, state: "done", done: true, reply: "Typed it.", persisted: true };
      return { ok: true, state: "approval", done: false, text: "", status: "", approval: { approval_id: "a1", command: CMD } };
    },
    "/api/chat/approve": function (u, init) { const b = JSON.parse(init.body); st.approves.push(b); st.answered = b.choice; return { ok: true }; },
    "/api/chat": function (u, init) { st.chats.push(JSON.parse(init.body)); return { __status: 409, body: { ok: false, busy: true, busy_job: opts.job || "J7", reply: BUSY } }; },
    "/api/needsyou/act": function (u, init) { const b = JSON.parse(init.body); st.acts.push(b); return { ok: true, open: "main", ref: opts.ref || "chat-appr" }; }
  };
  return st;
}
async function settle(w, n) { for (let i = 0; i < (n || 10); i++) { w.__flushTimers(1); await w.__tick(2); } }
function card(d) { const b = d.getElementById("msgs").querySelectorAll(".approval"); return b.length ? b[0] : null; }
function botTexts(d) { return Array.from(d.getElementById("msgs").querySelectorAll(".bubble.bot")).map(function (b) { return (b.textContent || "").trim(); }).filter(Boolean); }

(async function main() {
  console.log("X-approval-flow-1: a waiting approval is reachable from any window");

  await scenario("reopening the conversation (after a reload)", async function () {
    const st = serverState();
    const w = await bootDashboard({ before: NO_NAMED_TOAST, routes: st.routes });
    const d = w.document;
    w.__run("session='chat-appr'"); w.__run("loadHistory()"); await settle(w);
    const c = card(d);
    T.check("the approval card the turn waits on is shown", !!c && c.querySelector("code").textContent === CMD, c && c.textContent);
    T.check("Send is held while that turn runs", d.getElementById("send").disabled === true);
    c.querySelector("button.ok").click(); await settle(w);
    T.check("Approve answers THAT job's approval", st.approves.length === 1 && st.approves[0].job === "J7" && st.approves[0].approval_id === "a1" && st.approves[0].choice === "approve", st.approves);
    T.check("the reply streams into the conversation", botTexts(d).some(function (t) { return /Typed it/.test(t); }), botTexts(d));
    T.check("...and Send is free again", d.getElementById("send").disabled === false);
  });

  await scenario("a send refused 409 busy (server without inflight_job)", async function () {
    const st = serverState({ oldServer: true });
    const w = await bootDashboard({ before: NO_NAMED_TOAST, routes: st.routes });
    const d = w.document;
    w.__run("session='chat-appr'"); w.__run("loadHistory()"); await settle(w);
    T.check("(no attach without the server's hint)", !card(d));
    d.getElementById("input").value = "hello?";
    w.__run("sendMsg()"); await settle(w);
    T.check("the refused text goes back into the composer", d.getElementById("input").value === "hello?", d.getElementById("input").value);
    const c = card(d);
    T.check("the busy turn's approval card is shown (attached via busy_job)", !!c && c.querySelector("code").textContent === CMD);
    T.check("only one card", d.getElementById("msgs").querySelectorAll(".approval").length === 1);
  });

  await scenario("Needs-you: Open on an approval row", async function () {
    const st = serverState();
    const w = await bootDashboard({ before: NO_NAMED_TOAST, aux: ["aux_needsyou.js"], routes: st.routes });
    const d = w.document;
    const before = w.__run("session");
    const host = d.createElement("div"); d.body.appendChild(host);
    w.__needsyou.draw(host, { now: [{ id: "J7", source: "approval", title: "Approval needed", summary: CMD, sender: "Hermes",
      bucket: "now", received_at: Date.now() / 1000 - 60, open: "main", ref: "chat-appr" }], today: [], later: [], never_count: 0, sources: {} }, null, false);
    const open = host.querySelector('button[data-a="open"]');
    T.check("the row has an Open button", !!open);
    open.click(); await settle(w);
    T.check("Open switches to the row's own conversation", w.__run("session") === "chat-appr" && before !== "chat-appr", [before, w.__run("session")]);
    const c = card(d);
    T.check("...where the approval card is shown", !!c && c.querySelector("code").textContent === CMD);
  });

  await scenario("Needs-you: a reserved conversation is never opened", async function () {
    const st = serverState({ ref: "needsyou", sess: "needsyou", job: "D1" });
    const w = await bootDashboard({ before: NO_NAMED_TOAST, routes: st.routes });
    const before = w.__run("session");
    let r = null; w.__run("hermesOpenConversation('needsyou')").then(function (x) { r = x; }); await settle(w, 3);
    T.check("hermesOpenConversation refuses the drafts conversation", r === false && w.__run("session") === before, [r, w.__run("session")]);
    r = null; w.__run("hermesOpenConversation('__prewarm__')").then(function (x) { r = x; }); await settle(w, 3);
    T.check("...and the prewarm key", r === false && w.__run("session") === before);
  });

  await scenario("Quick Ask hand-off to the main window", async function () {
    const st = serverState({ sess: "menubar", job: "J9" });
    const w = await bootDashboard({ before: NO_NAMED_TOAST, aux: ["aux_quickask.js"], routes: st.routes });
    const d = w.document;
    T.check("the hand-off shim exists", typeof w.hermesQuickAskResume === "function");
    w.hermesQuickAskResume("J9"); await settle(w);
    T.check("one approval card, not one per stream", d.getElementById("msgs").querySelectorAll(".approval").length === 1,
      d.getElementById("msgs").querySelectorAll(".approval").length);
    card(d).querySelector("button.ok").click(); await settle(w, 14);
    const replies = botTexts(d).filter(function (t) { return /Typed it/.test(t); });
    T.check("the reply is drawn once", replies.length === 1, botTexts(d));
    T.check("no stray thinking bubble is left", !d.getElementById("msgs").querySelector(".dots"));
  });

  // review: the hand-off used to stream `job` itself after loading the
  // history, so a turn that had finished by then (answered in the popover, or
  // it simply ended) was drawn twice — from the history and from its poll
  await scenario("Quick Ask hand-off of a turn that already finished", async function () {
    const st = serverState({ sess: "menubar", job: "J9" });
    st.answered = "approve";
    const hist = st.routes["/api/history"];
    st.routes["/api/history"] = function (url) { const d = hist(url); d.messages = d.messages.concat([{ role: "bot", text: "Typed it." }]); return d; };
    const w = await bootDashboard({ before: NO_NAMED_TOAST, aux: ["aux_quickask.js"], routes: st.routes });
    const d = w.document;
    w.hermesQuickAskResume("J9"); await settle(w, 14);
    const replies = botTexts(d).filter(function (t) { return /Typed it/.test(t); });
    T.check("its reply is shown once", replies.length === 1, botTexts(d));
    T.check("...with no thinking bubble", !d.getElementById("msgs").querySelector(".dots"));
  });

  await scenario("Quick Ask hand-off, dashboard without inflight_job", async function () {
    const st = serverState({ sess: "menubar", job: "J9", oldServer: true });
    const w = await bootDashboard({ before: NO_NAMED_TOAST, aux: ["aux_quickask.js"], routes: st.routes });
    const d = w.document;
    w.hermesQuickAskResume("J9"); await settle(w);
    T.check("the handed-off job is still attached (its id is the hint)",
      d.getElementById("msgs").querySelectorAll(".approval").length === 1);
  });
  T.done();
})();
