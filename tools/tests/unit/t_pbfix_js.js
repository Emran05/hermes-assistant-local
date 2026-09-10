// Headless render check for aux_promptbudget.js — review fix 4: a probe that
// failed must be VISIBLE, and the numbers it silently changed must be
// relabelled. The repo's documented pattern: eval the file in node with
// stubbed globals and drive the pure renderer. No DOM, no fetch, no dashboard.
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const file = path.join(REPO, "dashboard", "aux_promptbudget.js");

global.window = global;                 // the module picks W = window
(0, eval)(fs.readFileSync(file, "utf8"));

const PB = global.hermesPromptBudget;
let pass = 0, fail = 0;
const ok = (n, c, d) => { if (c) { pass++; console.log("  ok   " + n); }
                          else { fail++; console.log("  FAIL " + n + (d !== undefined ? "  — " + JSON.stringify(d).slice(0, 400) : "")); } };

// A payload shaped exactly like _pb_build's, measured cleanly.
const GOOD = {
  ok: true,
  prompt_size_ok: true, probe_ok: true, degraded: false,
  tokens_are_tools_only: false,
  prompt_size: { system_prompt: { bytes: 20700 }, skills_index: { bytes: 8988, chars: 8988 } },
  prompt_size_error: null, probe_error: null,
  config_key: "platform_toolsets.cli",
  profile: "full", profile_order: ["full", "lean", "focused"],
  lean_profile: ["web", "terminal"], focused_profile: ["web"],
  profiles: [
    { key: "full", label: "Full", gives_up: "Every tool.", est_tokens: 20500, est_seconds: 27.3, tool_count: 33, tools_json_bytes: 55275, pct_saved: 0 },
    { key: "lean", label: "Balanced", gives_up: "No browser.", est_tokens: 18400, est_seconds: 24.5, tool_count: 22, tools_json_bytes: 47931, pct_saved: 10 },
    { key: "focused", label: "Focused", gives_up: "No screen control.", est_tokens: 17000, est_seconds: 22.6, tool_count: 18, tools_json_bytes: 41000, pct_saved: 17 }
  ],
  current: { est_tokens: 20500, est_seconds: 27.3, tool_count: 33, tools_json_bytes: 55275 },
  toolsets: [
    { key: "web", label: "Web", detail: "search", tools: 3, bytes: 4200, enabled: true, lean: true },
    { key: "browser", label: "Browser", detail: "automation", tools: 12, bytes: 12000, enabled: true, lean: false }
  ],
  bytes_per_token: 3.6, prefill_tok_s: 750,
  restart_required: false, restart_note: "New conversations pick this up."
};

// Both probes dead — what _pb_build now answers instead of a clean ok:true.
const DEAD = Object.assign({}, GOOD, {
  prompt_size_ok: false, probe_ok: false, degraded: true,
  tokens_are_tools_only: true,
  prompt_size: null,
  prompt_size_error: "RuntimeError: hermes prompt-size exited 127",
  probe_error: "RuntimeError: the Hermes venv interpreter was not found",
  current: { est_tokens: 15354, est_seconds: 20.5, tool_count: 33, tools_json_bytes: 55275 }
});

const PS_DEAD = Object.assign({}, GOOD, {
  prompt_size_ok: false, degraded: true, tokens_are_tools_only: true,
  prompt_size: null,
  prompt_size_error: "RuntimeError: hermes prompt-size exited 127"
});

const PROBE_DEAD = Object.assign({}, GOOD, {
  probe_ok: false, degraded: true,
  probe_error: "RuntimeError: the Hermes venv interpreter was not found"
});

console.log("\n=== every state renders without throwing ===");
for (const [name, data] of Object.entries({ loading: null, good: GOOD, dead: DEAD, ps_dead: PS_DEAD, probe_dead: PROBE_DEAD,
                                            err: { ok: false, error: "boom <script>x</script>" } })) {
  let html = null, threw = null;
  try { html = PB.cardHTML({ data, picked: {}, choice: "full" }); } catch (e) { threw = e; }
  ok("renders " + name, !threw && typeof html === "string" && html.length > 100, threw && String(threw));
  if (html) {
    ok("  " + name + ": has the title", html.includes("Prompt budget"));
    ok("  " + name + ": balanced <style>", (html.match(/<style>/g) || []).length === (html.match(/<\/style>/g) || []).length);
    ok("  " + name + ": no emoji", !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));
  }
}

console.log("\n=== review fix 4: a clean measurement says nothing alarming ===");
const good = PB.cardHTML({ data: GOOD, picked: { web: true, browser: true }, choice: "full" });
ok("no error paragraph", !/class="pberr"/.test(good));
ok("labels are the normal ones", /prefix tokens/.test(good) && /first token/.test(good));
ok("does not claim tool schemas only", !/tool schemas only/.test(good));

console.log("\n=== review fix 4: prompt-size failure is stated AND relabels ===");
const psd = PB.cardHTML({ data: PS_DEAD, picked: {}, choice: "full" });
ok("the failure is rendered", /class="pberr"/.test(psd), psd.slice(0, 300));
ok("it names the real error", /prompt-size exited 127/.test(psd));
ok("it explains the consequence", /count the tool schemas only/.test(psd));
ok("the token stat is relabelled", /tool schemas only<\/span>/.test(psd), psd.slice(0, 4000));
ok("the seconds stat is relabelled", /schemas only<\/span>/.test(psd));
ok("the old label is gone", !/>prefix tokens</.test(psd));
// the point of the fix: it is at the TOP of the body, above the numbers it
// invalidates, and nowhere near the collapsed Advanced section.
const bodyAt = psd.indexOf('<div class="body">');
const errAt = psd.indexOf('class="pberr"', bodyAt);
const statsAt = psd.indexOf('class="pbstats"');
const detsAt = psd.indexOf('<details class="pbadv"');
ok("the error is inside the body", errAt > bodyAt, [bodyAt, errAt]);
ok("the error comes BEFORE the stats it invalidates", errAt < statsAt, [errAt, statsAt]);
ok("the error is NOT inside the collapsed details", errAt < detsAt, [errAt, detsAt]);

console.log("\n=== review fix 4: the probe_error branch can now actually fire ===");
const prd = PB.cardHTML({ data: PROBE_DEAD, picked: {}, choice: "full" });
ok("the probe failure is rendered even though rows fell back",
   /venv interpreter was not found/.test(prd), prd.slice(0, 400));
ok("it says the list is a fallback", /built-in\s*\n?\s*fallback|fallback/.test(prd));
ok("the toolset rows are still offered", /input type="checkbox" data-ts="web"/.test(prd));
ok("prompt-size labels stay honest when only the probe died",
   />prefix tokens</.test(prd) && !/tool schemas only/.test(prd));

console.log("\n=== both dead ===");
const dead = PB.cardHTML({ data: DEAD, picked: {}, choice: "full" });
ok("both errors appear", /prompt-size exited 127/.test(dead) && /venv interpreter/.test(dead));
ok("two error paragraphs", (dead.match(/class="pberr"/g) || []).length >= 2,
   (dead.match(/class="pberr"/g) || []).length);
ok("the profile table still renders", /Balanced/.test(dead) && /Focused/.test(dead));

console.log("\n=== escaping ===");
const nasty = Object.assign({}, GOOD, {
  prompt_size_ok: false, prompt_size_error: 'RuntimeError: <script>alert(1)</script>',
  probe_ok: false, probe_error: '<img src=x onerror=1>'
});
const nastyHtml = PB.cardHTML({ data: nasty, picked: {}, choice: "full" });
ok("probe/prompt-size errors are escaped",
   nastyHtml.includes("&lt;script&gt;") && !nastyHtml.includes("<script>alert"),
   nastyHtml.slice(0, 300));
ok("the img payload is escaped", !/<img src=x/.test(nastyHtml));

console.log("\n=== the pure helpers still behave ===");
ok("classify() lean", PB.classify({ web: true, terminal: true }, ["web", "terminal"], ["web", "terminal", "browser"], ["web"]) === "lean");
ok("classify() full", PB.classify({ web: true, terminal: true, browser: true }, ["web", "terminal"], ["web", "terminal", "browser"], ["web"]) === "full");
ok("classify() custom", PB.classify({ browser: true }, ["web", "terminal"], ["web", "terminal", "browser"], ["web"]) === "custom");
ok("selectionFor(full) is null", PB.selectionFor({ data: GOOD, choice: "full" }) === null);
ok("estimate() null when nothing moved",
   PB.estimate(GOOD, { web: true, browser: true }) === null);
ok("estimate() moves when a toolset is dropped",
   (PB.estimate(GOOD, { web: true }) || {}).tool_count === 21,
   PB.estimate(GOOD, { web: true }));

console.log("\n=== no-DOM safety ===");
let threw = null;
try { PB.paint(); } catch (e) { threw = e; }
ok("paint() is a no-op without a document", threw === null, threw && String(threw));
ok("exports mindExtras chain", typeof global.mindExtras === "function");

console.log("\nTESTS " + pass + " passed " + fail + " failed");
process.exit(fail ? 1 : 0);
