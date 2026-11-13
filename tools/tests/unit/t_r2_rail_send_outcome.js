// Round-2 R2-33 / review X-frontend-2 — the Screen rail's task box (aux_desktop
// submitTask, B26) hands its text to index.html's sendMsg (B11). sendMsg
// resolved to undefined whether the turn ran, was refused 409 busy (a turn
// this window did not start: after a reload mid-turn, or the menubar
// conversation with a Quick Ask turn in flight), was refused outright (paused,
// memory ceiling) or never reached the dashboard. submitTask had already
// cleared the box and then always said "Done — the reply is in the chat", so
// the task text was lost and the status claimed success.
// sendMsg now resolves to {sent, reason, message} / {sent:true, ok}, and the
// rail keeps the task in its box until /api/chat accepts the turn, puts it back
// with the reason when nothing was sent, and says so when the turn errored.
//
// Boots the REAL index.html (sendMsg/streamJob) and the REAL aux_desktop.js in
// the mini DOM (_a11y_dom.js); /api/chat and /api/chat/poll are mocked.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
// see t_r2_sidebar_search_delete.js: the mini DOM's named access would let
// the #toast element shadow index.html's function toast()
const NO_NAMED_TOAST = function (w) { delete w.toast; };

async function rail(routes) {
  const w = await bootDashboard({ before: NO_NAMED_TOAST, aux: ["aux_desktop.js"], routes: routes });
  const d = w.document;
  const root = d.createElement("div");
  root.innerHTML = '<textarea id="desk-task"></textarea><span class="desk-status" id="desk-status"></span>' +
    '<button class="primary" id="desk-send">Send to agent</button>';
  d.body.appendChild(root);
  T.check("the rail's compose card is wired", w.deskWireCompose(root) === true);
  return { w, d, task: root.querySelector("#desk-task"), send: root.querySelector("#desk-send"), status: root.querySelector("#desk-status") };
}
async function settle(w, n) { for (let i = 0; i < (n || 12); i++) { w.__flushTimers(1); await w.__tick(2); } }
const BUSY = "Hermes is still answering your previous message in this conversation. Wait for that reply (or answer its approval), then send this again.";

(async function main() {
  console.log("R2-33 / X-frontend-2: the rail reports what really happened to its task");

  await scenario("409 busy", async function () {
    const r = await rail({ "/api/chat": { __status: 409, body: { ok: false, busy: true, busy_job: null, reply: BUSY } } });
    r.task.value = "Open Safari and find the flight";
    r.send.click(); await settle(r.w);
    T.check("the task text is put back", r.task.value === "Open Safari and find the flight", r.task.value);
    T.check("the status gives the server's reason, not 'Done'", /still answering/.test(r.status.textContent) && !/Done/.test(r.status.textContent), r.status.textContent);
    T.check("Send works again", r.send.disabled === false);
  });

  await scenario("dashboard unreachable", async function () {
    const r = await rail({ "/api/chat": { __reject: "Load failed" } });
    r.task.value = "Open Safari";
    r.send.click(); await settle(r.w);
    T.check("the task text is put back", r.task.value === "Open Safari", r.task.value);
    T.check("the status says it could not reach the dashboard", /Could not reach/.test(r.status.textContent), r.status.textContent);
  });

  await scenario("refused (agent paused)", async function () {
    const r = await rail({ "/api/chat": { ok: false, reply: "The agent is paused to save memory. Resume it from the model menu (top right), then send this again." } });
    r.task.value = "Open Notes";
    r.send.click(); await settle(r.w);
    T.check("the task text is put back", r.task.value === "Open Notes", r.task.value);
    T.check("the status gives the reason", /paused/.test(r.status.textContent), r.status.textContent);
  });

  await scenario("accepted, then done", async function () {
    let polls = 0;
    const r = await rail({ "/api/chat": { ok: true, job: "J1" },
      "/api/chat/poll": function () { polls++; return polls < 3 ? { ok: true, state: "running", text: "" } : { ok: true, done: true, state: "done", reply: "opened it", persisted: true }; } });
    r.task.value = "Open Safari";
    r.send.click(); await r.w.__tick(6);
    T.check("once /api/chat takes the turn the box clears", r.task.value === "", r.task.value);
    T.check("...and the status says it is running", /running in the chat/.test(r.status.textContent), r.status.textContent);
    await settle(r.w, 20);
    T.check("when it ends the status says Done", /Done/.test(r.status.textContent), r.status.textContent);
  });

  await scenario("accepted, ended in an error", async function () {
    const r = await rail({ "/api/chat": { ok: true, job: "J2" },
      "/api/chat/poll": { ok: true, done: true, state: "done", err: true, reply: "The agent timed out.", persisted: true } });
    r.task.value = "Open Mail";
    r.send.click(); await settle(r.w);
    T.check("the status does not claim success", !/Done/.test(r.status.textContent) && /error/.test(r.status.textContent), r.status.textContent);
  });

  // review: sendMsg's outcome was only written after the stream returned, so
  // a stream that threw after /api/chat had taken the turn reported
  // sent:false / "Could not reach" — the rail put a task that IS running back
  // in its box, inviting a second send
  await scenario("accepted, then the stream throws", async function () {
    const r = await rail({ "/api/chat": { ok: true, job: "J4" }, "/api/chat/poll": function () { return null; } });
    r.task.value = "Open Maps";
    let out = null;
    r.send.click(); await settle(r.w);
    T.check("the task is not put back (it was sent)", r.task.value === "", r.task.value);
    T.check("...and the status does not say it was unreachable", !/Could not reach/.test(r.status.textContent), r.status.textContent);
    r.w.__run("sendMsg('again')").then(function (x) { out = x; }); await settle(r.w);
    T.check("sendMsg resolves to sent:true, ok:false", !!out && out.sent === true && out.ok === false && out.job === "J4", out);
    const errs = Array.from(r.d.getElementById("msgs").querySelectorAll(".bubble.err")).map(function (b) { return b.textContent; });
    T.check("...and draws no 'Could not reach the dashboard' bubble", !errs.some(function (t) { return /Could not reach/.test(t); }), errs);
  });

  await scenario("sendMsg's own answer", async function () {
    const r = await rail({ "/api/chat": { __status: 409, body: { ok: false, busy: true, busy_job: null, reply: BUSY } } });
    let out = null;
    r.w.__run("sendMsg('hello')").then(function (x) { out = x; }); await settle(r.w);
    T.check("a 409 resolves to sent:false, reason busy", !!out && out.sent === false && out.reason === "busy", out);
    r.w.__routes["/api/chat"] = { ok: true, job: "J3" };
    r.w.__routes["/api/chat/poll"] = { ok: true, done: true, state: "done", reply: "hi", persisted: true };
    out = null; r.w.__run("sendMsg('hello')").then(function (x) { out = x; }); await settle(r.w);
    T.check("a finished turn resolves to sent:true, ok:true", !!out && out.sent === true && out.ok === true && out.job === "J3", out);
    out = null; r.w.__run("sendMsg('   ')").then(function (x) { out = x; }); await settle(r.w, 2);
    T.check("nothing to send resolves to sent:false", !!out && out.sent === false && out.reason === "empty", out);
  });
  T.done();
})();
