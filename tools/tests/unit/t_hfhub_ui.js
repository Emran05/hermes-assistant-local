// Browse Hugging Face card (dashboard/aux_hfhub.js, 2026-11-12).
//
// Runs the REAL aux_hfhub.js with index.html's REAL esc() on a stub window
// (no DOM is needed for what is checked here): a pasted link or id becomes a
// repo id and nothing else does; every Hub string is escaped; a "no" offers
// no add button; a curated model already in the menu offers only the menu;
// the add request is confirm:true + the inspected sha + the download flag,
// and nothing is sent when the owner cancels the confirm(); the only
// outbound link is https://huggingface.co/<validated id>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_hfhub.js"), "utf8");
const esc = require(path.join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? " " + JSON.stringify(extra) : "")); }
}

check("index.html's esc() found", typeof esc === "function" && esc("<") === "&lt;");

function boot(opts) {
  opts = opts || {};
  const posts = [], opened = [], asked = [];
  const win = {
    document: { getElementById: () => null, readyState: "complete" },
    confirm: (t) => { asked.push(t); return opts.confirm !== false; },
    open: (u) => { opened.push(u); },
    fetch: async (url, init) => {
      if (init && init.method === "POST") posts.push({ url, body: JSON.parse(init.body), init });
      const data = opts.answer ? opts.answer(url, init) : { ok: true };
      return { ok: data.ok !== false, status: data.ok === false ? 400 : 200, json: async () => data };
    },
    setTimeout, clearTimeout, esc,
  };
  win.window = win;
  const ctx = vm.createContext(win);
  vm.runInContext(SRC, ctx, { filename: "aux_hfhub.js" });
  return { H: win.hermesHF, posts, opened, asked, win };
}

(async function main() {
  console.log("1. links and ids");
  const { H } = boot();
  const cases = [
    ["https://huggingface.co/xtools-at/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-heretic", "xtools-at/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-heretic"],
    ["huggingface.co/org/name?x=1#y", "org/name"],
    ["https://www.huggingface.co/org/name/tree/main", "org/name"],
    ["  org/name  ", "org/name"],
    ["qwen 9b", null], ["org/../etc", null], ["org/na--me", null], ["/org/name", null],
    ["https://evil.example/org/name", null], ["javascript:alert(1)//a/b", null], ["org/<b>", null],
  ];
  for (const [inp, want] of cases) check("idFromInput(" + JSON.stringify(inp) + ") -> " + want, H.idFromInput(inp) === want, H.idFromInput(inp));

  console.log("\n2. escaping and per-verdict actions");
  const S = H._st;
  const id = "org/name";
  S.insp[id] = { verdict: "warn", sha: "a".repeat(40), in_roster: false, curated: false,
    blocks: [], warns: ["<img src=x onerror=alert(1)>"], notes: ["<script>1</script>"],
    facts: { download_gb: 5.98, ram_gb: 7, machine_gb: 64, fit: "ok", ctx: 262144, backend: "mlx_vlm",
      model_type: "<i>qwen3_5</i>", bits: 4, vision: true, thinking: true, license: "<u>x</u>",
      updated: "2026-11-08T03:38:20.000Z", files: 14 },
    alternatives: [{ id: "a/b", downloads: 10, uncensored: true }] };
  let html = H._detailHTML(id);
  const bare = html.replace(/="[^"]*"/g, "");
  check("hostile warnings/notes/facts are escaped", bare.indexOf("<img") < 0 && bare.indexOf("<script") < 0
    && bare.indexOf("<i>") < 0 && bare.indexOf("<u>") < 0 && html.indexOf("&lt;img") >= 0, html);
  check("warn: both add buttons, with the download size",
    /data-hfx="add-dl"[^>]*>Add and download · ~6 GB</.test(html) && /data-hfx="add"/.test(html), html);
  check("context reads 256k tokens, commit 7 chars", html.indexOf("256k tokens") >= 0 && html.indexOf(">aaaaaaa<") >= 0);
  check("alternatives listed as pickable rows", /data-hfx="pick" data-id="a\/b"/.test(html));

  S.insp[id] = Object.assign({}, S.insp[id], { verdict: "no", blocks: ["not an MLX build"], warns: [] });
  html = H._detailHTML(id);
  check("no: no add button, only the Hub link", !/data-hfx="add/.test(html) && /data-hfx="hub"/.test(html)
    && html.indexOf("Can’t run here") >= 0, html);
  S.insp[id] = Object.assign({}, S.insp[id], { verdict: "ok", blocks: [], in_roster: true, curated: true });
  html = H._detailHTML(id);
  check("curated and already listed: only the model menu", !/data-hfx="add/.test(html) && /data-hfx="menu"/.test(html));
  S.insp[id] = Object.assign({}, S.insp[id], { in_roster: true, curated: false });
  html = H._detailHTML(id);
  check("listed, not curated: offers to update its settings", html.indexOf("Update its settings") >= 0);
  S.insp[id] = { error: "<b>boom</b>" };
  html = H._detailHTML(id);
  check("an error is escaped and offers Try again", html.indexOf("&lt;b&gt;boom") >= 0 && /data-hfx="retry"/.test(html));
  const item = H._itemHTML({ id: "org/name", downloads: 18933, likes: 3, bits: 4, uncensored: true, in_roster: true,
    updated: "2026-11-08T03:38:20.000Z" });
  check("a result row: org dimmed, downloads, chips, collapsed", item.indexOf("18.9k downloads") >= 0
    && item.indexOf("Uncensored") >= 0 && item.indexOf("in your menu") >= 0 && /aria-expanded="false"/.test(item)
    && / hidden>/.test(item), item);

  console.log("\n3. adding");
  let b = boot({ confirm: false });
  b.H._st.insp["org/x"] = { verdict: "ok", sha: "c".repeat(40), facts: { download_gb: 6, uncensored: true, ram_gb: 7, machine_gb: 64 },
    blocks: [], warns: [], notes: [] };
  await b.H._add("org/x", true);
  check("cancelled confirm: nothing is sent", b.posts.length === 0 && b.asked.length === 1);
  check("...and the confirm named the curation, the uncensored brake and the size",
    /Not curated/.test(b.asked[0]) && /approval gate is the only brake/.test(b.asked[0]) && /~6 GB/.test(b.asked[0]), b.asked[0]);
  b = boot({ answer: (u, init) => (init && init.method === "POST") ? { ok: true, added: true, download: { ok: true } } : { ok: true } });
  b.H._st.insp["org/x"] = { verdict: "warn", sha: "c".repeat(40), facts: { download_gb: 6 }, blocks: [], warns: ["w"], notes: [] };
  await b.H._add("org/x", true);
  const p = b.posts[0] || {};
  check("add posts confirm:true, the inspected sha and download:true to the import route",
    p.url === "/api/models/hf/import" && p.body.confirm === true && p.body.sha === "c".repeat(40)
    && p.body.download === true && p.body.id === "org/x", p);
  check("...same-origin credentials, JSON", p.init && p.init.credentials === "same-origin"
    && p.init.headers["Content-Type"] === "application/json");
  check("...and the row now reads as listed", b.H._st.insp["org/x"].in_roster === true
    && /Downloading/.test((b.H._st.msg["org/x"] || [])[0] || ""), b.H._st.msg["org/x"]);
  b.H._st.insp["org/no"] = { verdict: "no", facts: {}, blocks: ["x"] };
  await b.H._add("org/no", false);
  check("a 'no' is never posted, even if the button were forced", b.posts.length === 1 && b.asked.length === 1);

  console.log("\n4. the outbound link");
  check("hub links are built only from a validated id",
    H.idFromInput("org/x") === "org/x" && H.idFromInput('org/x" onclick="1') === null);

  console.log("\nTESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
