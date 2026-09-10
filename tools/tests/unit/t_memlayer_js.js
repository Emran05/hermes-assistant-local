// aux_memlayer.js — the review gate's visible half. Pure renderers only, evaled
// in node with the harness stubs the module documents (no DOM work needed:
// cardHTML/listHTML/factHTML/visible are all pure).
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_memlayer.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail || "")));
  if (!cond) fails.push(name);
}
global.window = global;
global.document = undefined;            // the card must not need one to render
eval(fs.readFileSync(FILE, "utf8"));
const ML = global.hermesMemLayer;

function fact(o) {
  return Object.assign({ id: 1, text: "a fact", kind: "fact", source: "user",
                         last_used_ts: 0, uses: 0, pinned: false,
                         archived: false, in_snapshot: false, review: false },
                       o);
}

console.log("\n1. a row waiting for review is marked and has an Approve button");
const held = fact({ id: 7, text: "Jane K knows the NYC AI scene",
                    source: "people", kind: "person", review: true });
const h = ML.factHTML(held, false);
check("marked", /class="[^"]*is-review/.test(h), h);
check("says why", /needs review/.test(h) && /not injected until you approve/.test(h), h);
check("has an Approve button", /data-act="approve"/.test(h), h);
const plain = ML.factHTML(fact({}), false);
check("an ordinary row has neither", !/is-review/.test(plain) &&
      !/data-act="approve"/.test(plain), plain);

console.log("\n2. the Needs review filter");
const S = { facts: [held, fact({ id: 8, text: "typed by the owner" })],
            filter: "", kind: "", reviewOnly: false };
check("off -> both rows", ML.visible(S).length === 2);
S.reviewOnly = true;
check("on -> only the held one", ML.visible(S).length === 1 &&
      ML.visible(S)[0].id === 7);
S.filter = "nothing matches this";
check("it composes with the text filter", ML.visible(S).length === 0);

console.log("\n3. the card offers the filter and Approve all only when needed");
const base = { loaded: true, facts: [held], settings:
               { enabled: true, budget_chars: 600, episodic: true },
               stats: { live: 3, review: 2, pinned: 1, in_snapshot: 4,
                        archived: 0 },
               preview: { text: "", ran: false }, filter: "", kind: "" };
let card = ML.cardHTML(base);
check("the count is in the button", /Needs review \(2\)/.test(card), "");
check("Approve all is offered", /data-act="approve-all"/.test(card), "");
check("and the stat row shows it", /needs review/.test(card), "");
const none = JSON.parse(JSON.stringify(base));
none.stats.review = 0;
none.facts = [fact({})];
card = ML.cardHTML(none);
check("with nothing waiting, neither is shown",
      !/data-act="review"/.test(card) && !/data-act="approve-all"/.test(card), "");

console.log("\n4. the import line reports every verdict, with reasons");
const imp = JSON.parse(JSON.stringify(base));
imp.imported = { added: 2, updated: 1, same: 9, skipped: 1, refused: 1,
                 failed: 1, stale: 3, needs_review: 3,
                 reasons: ["NOW.md: that looks like a credential — not storing it"] };
card = ML.cardHTML(imp);
check("added/updated/unchanged", /2 new, 1 updated, 9 unchanged/.test(card), "");
check("stale", /3 archived as stale/.test(card), "");
check("waiting for review", /3 waiting for review/.test(card), "");
check("refused, skipped, failed",
      /1 refused/.test(card) && /1 skipped/.test(card) && /1 failed/.test(card), "");
check("and the reason is rendered under it",
      /mlreasons/.test(card) && /looks like a credential/.test(card), "");

console.log("\n5. every interpolation is still escaped");
const nasty = fact({ id: 9, text: '<img src=x onerror="alert(1)">',
                     source: '"><script>', review: true });
const nh = ML.factHTML(nasty, false);
check("no raw tag survives", nh.indexOf("<img") < 0 &&
      nh.indexOf("<script>") < 0, nh);
check("it is escaped, not dropped", /&lt;img/.test(nh), nh);

console.log("");
console.log("\nTESTS " + (checks - fails.length) + " passed " +
            fails.length + " failed");
if (fails.length) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
console.log("ALL PASS");
