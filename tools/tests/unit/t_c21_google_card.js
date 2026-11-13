// C21 (docs/audits/2026-10-21-codebase-audit.md) — the card half.
//
// BEFORE: the Google card rendered "Connected · read-only" for any token file,
// whatever the last refresh said, so a revoked grant or a Testing-mode client's
// 7-day refresh expiry looked connected forever.
//
// AFTER: needs_reauth renders "Reconnect needed" with the cause; a transient
// refresh failure keeps "Connected" but says the refresh failed; and (C51) an
// older grant wider than read-only is not called read-only. The REAL
// dashboard/aux_google.js is evaluated in node:vm against a stub DOM with fetch
// mocked — nothing reaches the dashboard.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

function mkEl(tag) {
  return {
    tag, id: "", className: "", innerHTML: "", textContent: "", style: {},
    children: [], parent: null,
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); },
    querySelector() { return null; },
    addEventListener() {}
  };
}
const grid = mkEl("div"); grid.id = "view-mind";
const head = mkEl("head");
const doc = {
  head,
  createElement: mkEl,
  getElementById(id) {
    if (id === "view-mind") return grid;
    for (const p of [grid, head]) for (const c of p.children) if (c.id === id) return c;
    return null;
  }
};
let STATUS = null;
function fakeFetch(url) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(STATUS) });
}
const esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
const ctx = { console, setTimeout, clearTimeout, fetch: fakeFetch, document: doc, esc,
              Promise, JSON, Date, Math, Number, String, Array, Object, isFinite };
ctx.window = ctx;
vm.createContext(ctx);
const file = path.join(REPO, "dashboard", "aux_google.js");
vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });

const BASE = {
  ok: true, connected: true, scopes: [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly"],
  missing_features: [], reason: "", checked_at: 1792600000, scope_excess: []
};
async function card(st) {
  STATUS = Object.assign({}, BASE, st);
  await ctx.window.mindExtras();
  const c = doc.getElementById("mind-extra-google");
  return c ? c.innerHTML : "";
}

(async function () {
  let html = await card({});
  check("a healthy grant reads Connected · read-only", /Connected · read-only/.test(html));
  check("...and says the token refreshes itself", /refreshes itself/.test(html));

  html = await card({ needs_reauth: true, reason: "refresh_failed:invalid_grant",
                      refresh_failed_at: 1792600000 });
  check("a dead grant reads Reconnect needed", /Reconnect needed/.test(html), html.slice(0, 300));
  check("...and NOT Connected · read-only", !/Connected · read-only/.test(html));
  check("...and names Google's reason", /invalid_grant/.test(html));
  check("...and says the calendar is not reaching Hermes", /not reaching/.test(html));
  check("...and drops the 'refreshes itself' reassurance", !/refreshes itself/.test(html));

  html = await card({ needs_reauth: false, reason: "refresh_failed:network_error",
                      refresh_failed_at: 1792600000 });
  check("a transient failure keeps Connected", /Connected · read-only/.test(html));
  check("...but says the last refresh failed", /refresh failed/.test(html) &&
        /network_error/.test(html));

  html = await card({ scope_excess: ["https://www.googleapis.com/auth/calendar"],
                      scopes: BASE.scopes.concat(["https://www.googleapis.com/auth/calendar"]) });
  check("an older, wider grant is not called read-only (C51)",
        /wider than read-only/.test(html) && !/Connected · read-only/.test(html));
  check("...and its calendar scope is labelled read and write",
        /Calendar · read and write/.test(html));

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL harness crashed: " + (e && e.stack || e));
  console.log("TESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
