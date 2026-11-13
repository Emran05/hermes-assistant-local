// C103 (docs/audits/2026-10-21-codebase-audit.md): the Needs-you rows repeat
// Done / Snooze / Open / Draft reply, and none of those buttons carried the
// row's subject in its accessible name — a screen reader's button list read
// "Done, Done, Done" with nothing to tell the rows apart.
//
// Renders the REAL dashboard/aux_needsyou.js (hub widget and pop-out, with a
// snooze row open) in a vm sandbox and checks every action button: its
// accessible name names the row's subject, keeps the visible word in it
// (label-in-name), and no two buttons share one.  Offline, no DOM.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_needsyou.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}

const esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
function unesc(s) {
  return s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// a host element whose buttons can be "clicked" through wire()'s onclick
function host() {
  const h = { innerHTML: "", textContent: "", __btns: [] };
  h.querySelectorAll = function (sel) {
    if (sel !== "button[data-a]") return [];
    const out = [];
    const re = /<button\b([^>]*)>/g;
    let m;
    while ((m = re.exec(h.innerHTML))) {
      const attrs = m[1];
      const a = (attrs.match(/data-a="([^"]*)"/) || [])[1];
      if (!a) continue;
      // the data-id of the row this button sits in
      const before = h.innerHTML.slice(0, m.index);
      const rows = before.match(/data-id="([^"]*)"/g) || [];
      const id = rows.length ? unesc(rows[rows.length - 1].slice(9, -1)) : "";
      out.push({
        getAttribute: function (k) {
          if (k === "data-a") return a;
          const mm = attrs.match(new RegExp(k + '="([^"]*)"'));
          return mm ? unesc(mm[1]) : null;
        },
        closest: function () {
          return id ? { getAttribute: function () { return id; } } : null;
        }
      });
    }
    h.__btns = out;
    return out;
  };
  h.getClientRects = function () { return []; };
  return h;
}

const sandbox = {
  console, esc, relTime: function () { return "2h ago"; },
  RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
  setTimeout: function () { return 0; },
  fetch: function () { return Promise.reject(new Error("offline")); },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const now = Date.now() / 1000;
const payload = {
  ok: true, never_count: 0, metrics: {}, next_brief: null, generated_at: now,
  now: [
    { id: "message:+15550000001", source: "message", sender: "Ana Lee",
      title: "Ana Lee", summary: "can you send it by 5pm?", bucket: "now",
      received_at: now - 60, deadline_ts: now + 3600, reason: "r" },
    { id: "message:+15550000002", source: "message", sender: "Ben \"B\" Ode",
      title: "Ben", summary: "are you free?", bucket: "now",
      received_at: now - 60, reason: "r" },
    { id: "calendar:evt|x", source: "calendar", sender: "Calendar",
      title: "Design review", bucket: "now", received_at: now - 60,
      deadline_ts: now + 1800, reason: "r" },
  ],
  today: [], later: [
    { id: "watchtower:u1", source: "watchtower", title: "An article",
      bucket: "later", received_at: now - 600, reason: "r" },
  ],
};

function buttons(html) {
  const out = [];
  const re = /<button\b([^>]*)>([^<]*)<\/button>/g;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[1];
    if (!/data-a="(done|snz|open|draft|rc|snooze)"/.test(attrs)) continue;
    const lab = attrs.match(/aria-label="([^"]*)"/);
    out.push({ a: attrs.match(/data-a="([^"]*)"/)[1], text: unesc(m[2]),
               label: lab ? unesc(lab[1]) : null });
  }
  return out;
}

function audit(tag, html, subjects) {
  const bs = buttons(html);
  check(tag + ": the row actions rendered", bs.length >= 3 * subjects.length,
        bs.length);
  const unnamed = bs.filter(function (b) { return !b.label; });
  check(tag + ": every row action has an aria-label", !unnamed.length,
        JSON.stringify(unnamed.slice(0, 3)));
  const named = bs.filter(function (b) { return b.label; });
  const noSubj = named.filter(function (b) {
    return !subjects.some(function (s) { return b.label.indexOf(s) >= 0; });
  });
  check(tag + ": each name says whose row it is", !noSubj.length,
        JSON.stringify(noSubj.slice(0, 3)));
  const lin = named.filter(function (b) {
    return b.label.toLowerCase().indexOf(b.text.toLowerCase()) < 0;
  });
  check(tag + ": the visible word is part of the name (label-in-name)",
        !lin.length, JSON.stringify(lin.slice(0, 3)));
  const seen = {}, dup = [];
  named.forEach(function (b) {
    if (seen[b.label]) dup.push(b.label);
    seen[b.label] = 1;
  });
  check(tag + ": no two buttons share a name", !dup.length, dup.join(" | "));
  return bs;
}

console.log("=== C103: hub widget ===");
const w = host(), meta = host();
sandbox.RENDER.needsyou(w, payload, meta);
const hub = audit("widget", w.innerHTML, ["Ana Lee", 'Ben "B" Ode', "Design review"]);
check("widget: a quote in a name is escaped, not an attribute break",
      w.innerHTML.indexOf('aria-label="Done: Ben &quot;B&quot; Ode"') >= 0,
      (w.innerHTML.match(/aria-label="Done: Ben[^>]*/) || [""])[0]);
check("widget: Done names Ana Lee",
      hub.some(function (b) { return b.label === "Done: Ana Lee"; }),
      JSON.stringify(hub.slice(0, 2)));

console.log("=== C103: the snooze choices ===");
// open Ana's snooze row the way a click does
const snz = w.__btns.length ? w.__btns : w.querySelectorAll("button[data-a]");
const s = snz.find(function (b) {
  return b.getAttribute("data-a") === "snz" &&
    b.closest().getAttribute("data-id") === "message:+15550000001";
});
check("found Ana's Snooze button", !!s);
if (s) {
  s.onclick({ preventDefault: function () {}, stopPropagation: function () {} });
}
const choices = buttons(w.innerHTML).filter(function (b) { return b.a === "snooze"; });
check("the three snooze choices are open", choices.length === 3, choices.length);
check("each snooze choice names the action and the row",
      choices.length === 3 && choices.every(function (b) {
        return b.label && /^Snooze /.test(b.label) && /: Ana Lee$/.test(b.label);
      }), JSON.stringify(choices));
audit("widget+snooze", w.innerHTML, ["Ana Lee", 'Ben "B" Ode', "Design review"]);

console.log("=== C103: pop-out ===");
const p = host();
sandbox.EXPAND_RENDER.needsyou(p, payload);
const pop = audit("pop-out", p.innerHTML,
                  ["Ana Lee", 'Ben "B" Ode', "Design review", "An article"]);
check("pop-out: re-file buttons carry the row too",
      pop.some(function (b) { return b.label === "Move to today: An article"; }) &&
      pop.some(function (b) { return b.label === "Never: Ana Lee"; }),
      JSON.stringify(pop.filter(function (b) { return b.a === "rc"; }).slice(0, 4)));

console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
