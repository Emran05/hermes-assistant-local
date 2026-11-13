// C127 (audit 2026-10-21): You-Model people cards work for non-Latin and
// accented names.
//
// The card's file name came from slugify(name), which lower-cased the name and
// replaced every run of [^a-z0-9] with "-". A name with no ASCII letters
// (李明, Мария) slugged to "" and the card could not be created at all ("Give
// the person a name first."); accented names were mangled ("José García" ->
// jos-garc-a) and two names sharing only their Latin part ("Анна Smith",
// "Мария Smith") collided on "smith". This drives the REAL aux_youmodel.js in
// node: a small fake DOM renders the card, fills the Add-person fields, clicks
// the button and captures the /api/memory/create request.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_youmodel.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

// aux_memory.py MEM_NAME_RE, the rule /api/memory/create enforces on the base name
const MEM_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md$/;

// ---- a fake DOM just big enough for render() + wire() ----------------------
const FIELDS = { nm: "", nt: "" };
function el(tag) {
  const e = {
    tag, listeners: {}, innerHTML: "", disabled: false, id: "",
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-youmodel") STATE.card = c; return c; },
    remove() {}, getAttribute() { return null; },
    querySelector(sel) {
      if (sel === "#ym-paddb" && /id="ym-paddb"/.test(this.innerHTML)) {
        if (!this._addb) {
          this._addb = el("button");
          this._addb.parentNode = { querySelector: s => s === ".nm" ? { value: FIELDS.nm }
                                    : s === ".nt" ? { value: FIELDS.nt } : null };
        }
        return this._addb;
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
  return e;
}
const STATE = { card: null, posts: [], data: null };
const grid = el("div");
global.window = global;
global.document = {
  getElementById(id) { return id === "view-mind" ? grid : (id === "ym-css" ? {} : null); },
  createElement: el, head: el("head"),
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") {
    STATE.posts.push({ url, body: JSON.parse(opt.body) });
    return { status: 200, json: async () => ({ ok: true }) };
  }
  return { status: 200, json: async () => STATE.data };
};
(0, eval)(fs.readFileSync(FILE, "utf8"));

function data(people) {
  return { ok: true, filled: 0, total: 5, empty: false, files: [], people: people || [] };
}

async function addPerson(name, note) {
  STATE.posts.length = 0;
  STATE.data = data();
  await window.youModelPanel();
  FIELDS.nm = name; FIELDS.nt = note || "";
  const b = STATE.card.querySelector("#ym-paddb");
  await b.listeners.click();
  await new Promise(r => setTimeout(r, 0));
  const p = STATE.posts.find(x => x.url === "/api/memory/create");
  return p ? p.body : null;
}

(async () => {
  console.log("\n1. a name with no ASCII letters still gets a card");
  const li = await addPerson("李明", "former colleague");
  check("李明 -> a create request", !!li, li);
  const base = li ? li.name.replace(/^people\//, "") : "";
  check("with a file name the memory API accepts", MEM_NAME_RE.test(base), base);
  check("and the real name as the card's first line",
        li && li.content === "李明 — former colleague", li && li.content);
  const li2 = await addPerson("李明");
  check("the same name maps to the same card (so 'exists' still works)",
        li2 && li2.name === (li && li.name), [li2 && li2.name, li && li.name]);
  const ma = await addPerson("Мария");
  check("a different non-Latin name gets a different card",
        ma && li && ma.name !== li.name, [ma && ma.name, li && li.name]);

  console.log("\n2. accents fold instead of breaking the word");
  const jg = await addPerson("José García");
  check("José García -> people/jose-garcia.md", jg && jg.name === "people/jose-garcia.md",
        jg && jg.name);
  const zo = await addPerson("Zoë O’Brien");
  check("Zoë O’Brien -> people/zoe-obrien.md", zo && zo.name === "people/zoe-obrien.md",
        zo && zo.name);
  const ascii = await addPerson("Jane Kim");
  check("a plain ASCII name is unchanged", ascii && ascii.name === "people/jane-kim.md",
        ascii && ascii.name);

  console.log("\n3. names that share only their Latin part do not collide");
  const a = await addPerson("Анна Smith"), m = await addPerson("Мария Smith");
  check("two different people, two different cards", a && m && a.name !== m.name,
        [a && a.name, m && m.name]);
  check("both valid file names", a && m && MEM_NAME_RE.test(a.name.slice(7)) &&
        MEM_NAME_RE.test(m.name.slice(7)), [a && a.name, m && m.name]);

  console.log("\n4. the card list shows the person's name, not the file's hash");
  STATE.data = data([{ name: li ? li.name : "people/x.md", slug: base.replace(/\.md$/, ""),
                       preview: "李明 — former colleague", content: "李明 — former colleague",
                       mtime: 0, etag: "e" }]);
  await window.youModelPanel();
  const html = STATE.card ? STATE.card.innerHTML : "";
  check("the title is 李明", /<div class="pn">李明<\/div>/.test(html), html.slice(-400));

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.log("  FAIL harness threw " + e); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
