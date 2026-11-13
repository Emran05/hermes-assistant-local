// The chat lives on the Agent page only (owner decision 2026-11-12).
//
// REPLACES t_climb_settings_chat.js, which pinned the Settings collapse of
// the old right-hand chat column, its Show-chat button and the remembered
// choice (hermes_chat_settings). The Hub and Settings now have no chat column
// at all, no Expand / Hide / Show-chat controls and no chat modes; anything
// outside the Agent page that needs the chat takes the owner there
// (openChat), and an approval that arrives while the chat is off screen
// raises a toast that leads there — the view is never switched unasked.
//
// Runs the REAL index.html + aux_agent.js (+ aux_quickask.js for the
// menu-bar hand-off) in the mini DOM.
"use strict";
const fs = require("fs"), path = require("path");
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const read = (f) => fs.readFileSync(path.join(REPO, "dashboard", f), "utf8");

async function boot(extra) {
  return bootDashboard({
    aux: ["aux_agent.js"].concat(extra || []),
    before: function (w) {
      // what the old design left in an owner's storage
      w.localStorage.setItem("hermes_chat", "full");
      w.localStorage.setItem("hermes_chat_settings", "open");
      // the harness's named access for id="toast" would shadow index.html's
      // function toast() (a browser lets the declaration win)
      delete w.toast;
    }
  });
}
const onAgent = (d) => d.documentElement.hasAttribute("data-agentview");
const shown = (w, el) => { const v = computedValue(w, el, "display"); return !(v && v.value === "none"); };

(async function main() {
  console.log("the Hub and Settings have no chat");
  let w = await boot();
  let d = w.document;
  const col = d.querySelector(".chatcol"), deck = d.querySelector(".deck");
  T.check("the old chat-mode keys are forgotten at load",
    w.localStorage.getItem("hermes_chat") === null && w.localStorage.getItem("hermes_chat_settings") === null);
  T.check("no Expand / Hide / Show-chat controls exist",
    !d.getElementById("chat-max") && !d.getElementById("chat-hide") && !d.getElementById("chat-restore"));
  T.check("no chat-mode switch is left in the page", w.__run("typeof setChatMode") === "undefined" &&
    w.__run("typeof setSettingsChatOpen") === "undefined" && !deck.hasAttribute("data-chat"));
  T.check("the deck is one column", /^minmax\(0,\s*1fr\)$/.test((computedValue(w, deck, "grid-template-columns") || {}).value || ""),
    computedValue(w, deck, "grid-template-columns"));
  T.check("on the Hub the parked chat is not shown", col.parentNode === deck && !shown(w, col) && !onAgent(d));
  w.__run("setView('mind')"); await w.__tick();
  T.check("on Settings it is not shown either, and nothing moved", col.parentNode === deck && !shown(w, col) && !onAgent(d));
  w.__run("setView('hub')"); await w.__tick();

  console.log("anything that needs the chat opens the Agent page");
  w.__run("askAbout('test')"); await w.__tick();
  const inp = d.getElementById("input");
  T.check("askAbout from the Hub lands on the Agent page", onAgent(d) && !d.getElementById("view-agent").hidden &&
    d.getElementById("view-hub").hidden && col.parentNode === d.getElementById("agent-stream"));
  T.check("...with the chat shown there", shown(w, col));
  T.check("...the text in the composer and the composer focused",
    inp.value === 'Regarding "test" — ' && d.activeElement === inp, [inp.value, d.activeElement && d.activeElement.id]);
  T.check("the Agent tab is the selected tab", d.getElementById("tab-agent").getAttribute("aria-selected") === "true");
  w.__run("setView('hub')"); await w.__tick();
  T.check("leaving puts the chat back in the deck, unshown", col.parentNode === deck && !shown(w, col) && !onAgent(d));
  inp.value = "";
  w.__run("askAbout('the weather', true)"); await w.__tick();
  T.check("a verbatim ask (the pop-out ask box) is put in as typed", onAgent(d) && inp.value === "the weather");
  w.__run("setView('hub')"); await w.__tick();

  let r = null;
  w.__run("hermesOpenConversation('chat-trip')").then(function (x) { r = x; });
  for (let i = 0; i < 4; i++) await w.__tick();
  T.check("opening a conversation from elsewhere (Needs-you's Open) goes to the Agent page",
    r === true && onAgent(d) && w.__run("session") === "chat-trip", [r, w.__run("session")]);
  w.__run("setView('hub')"); await w.__tick();

  console.log("an approval while the chat is off screen");
  const toast = d.getElementById("toast");
  w.__run("chatApprovalAway()");
  T.check("on the Hub: a toast says an action is waiting", toast.classList.contains("on") && /waiting for your approval/.test(toast.textContent), toast.textContent);
  T.check("...and takes a click while it is up", toast.style.pointerEvents === "auto" && typeof toast.onclick === "function");
  T.check("...the view is not switched by itself", !onAgent(d) && !d.getElementById("view-hub").hidden);
  toast.onclick();
  await w.__tick();
  T.check("a click on it opens the Agent page", onAgent(d) && col.parentNode === d.getElementById("agent-stream"));
  T.check("...and the toast goes inert again", !toast.classList.contains("on") && toast.onclick === null && toast.style.pointerEvents === "");
  w.__run("toast('something else')"); await w.__tick();
  w.__run("chatApprovalAway()");
  T.check("on the Agent page no toast is raised (the card is in view)", toast.textContent === "something else");
  w.__run("setView('mind')"); await w.__tick();
  w.__run("chatApprovalAway()"); await w.__tick();
  T.check("on Settings the same toast is raised", /waiting for your approval/.test(toast.textContent) && toast.style.pointerEvents === "auto");
  w.__run("toast('replaced')"); await w.__tick(); await w.__tick();
  T.check("replaced by another line, the toast stops taking clicks at once",
    toast.onclick === null && toast.style.pointerEvents === "" && !onAgent(d));
  w.__run("chatApprovalAway()"); await w.__tick();
  toast.classList.remove("on"); await w.__tick(); await w.__tick();
  T.check("faded, it stops taking clicks at once", toast.onclick === null && toast.style.pointerEvents === "");
  w.__run("setView('hub')"); await w.__tick();
  T.check("no page error", !w.__errors.length, w.__errors.map(String).join(" | "));

  console.log("Quick Ask's hand-off");
  w = await boot(["aux_quickask.js"]);
  d = w.document;
  T.check("the main-window shim is defined", typeof w.hermesQuickAskResume === "function");
  w.hermesQuickAskResume(null);
  for (let i = 0; i < 4; i++) await w.__tick();
  T.check("Continue in main / openApproval open the Agent page on the menubar conversation",
    onAgent(d) && w.__run("session") === "menubar" && !d.querySelector(".deck").hasAttribute("data-chat"));
  T.check("...and store no chat mode", w.localStorage.getItem("hermes_chat") === null);
  T.check("no page error", !w.__errors.length, w.__errors.map(String).join(" | "));

  console.log("the sources");
  const html = read("index.html");
  T.check("the approval path raises the away toast before drawing its card",
    /chatApprovalAway\(\);\s*const b=ensureBubble\(\);/.test(html));
  T.check("an approval in another conversation leads to that conversation",
    /toastGo\(msg\+' Click to open it\.'[^;]*hermesOpenConversation\(sess\)/.test(html));
  T.check("askAbout opens the chat before it touches the composer",
    /function askAbout[\s\S]{0,260}openChat\(\);[\s\S]{0,120}const inp=\$\('input'\)/.test(html));
  const all = ["index.html"].concat(fs.readdirSync(path.join(REPO, "dashboard")).filter((f) => /\.js$/.test(f)));
  const stale = all.filter((f) => /setChatMode|data-chat|dataset\.chat|chat-restore|chat-max|chat-hide|setSettingsChatOpen|chatModeNow/.test(read(f)));
  T.check("no dashboard file still drives the old chat modes", stale.length === 0, stale);
  T.check("You-Model's interview and Needs-you's Open go through openChat",
    /openChat\(\)/.test(read("aux_youmodel.js")) && /openChat\(\)/.test(read("aux_needsyou.js")));
  T.done();
})().catch((e) => { console.log("FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
