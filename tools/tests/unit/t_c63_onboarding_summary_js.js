// Regression test for audit finding C63 (docs/audits/2026-10-21-codebase-audit.md),
// client half: the first-run sheet's closing summary said "Assistant model:
// <pick>" whatever /api/onboarding/apply actually did. Since C63 the apply
// result says whether the pick became the active model (`primary_active`) and,
// when it did not (the model server was running, or the pick is not local
// yet), why (`primary_note`). The summary must carry that note.
//
// Extracts the REAL `summarize()` from dashboard/aux_onboarding.js (no DOM, no
// dashboard) and runs it against stubbed sheet state.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_onboarding.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}

// Brace-match the function body, skipping string literals and comments.
function extract(src, header) {
  const at = src.indexOf(header);
  if (at < 0) return null;
  let i = src.indexOf("{", at), depth = 0;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {
      for (i++; i < src.length && src[i] !== c; i++) if (src[i] === "\\") i++;
      continue;
    }
    if (c === "/" && src[i + 1] === "/") { i = src.indexOf("\n", i); continue; }
    if (c === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i) + 1; continue; }
    if (c === "{") depth++;
    if (c === "}" && --depth === 0) return src.slice(at, i + 1);
  }
  return null;
}

// summarize() is each step's own summary since round 3 (sumModels for this
// step); the helpers it calls are extracted with it
const SRC = fs.readFileSync(FILE, "utf8");
const HEADERS = ["function summarize()", "function sumModels()", "function sumPrefs()",
  "function sumWelcome()", "function extLines(st)", "function liveSteps()",
  "function fitsById()", "function labelOf(id)", "function gb(n)",
  // E22 (2026-11-09 audit): what the download really did, read by sumModels
  "function dlFailed(id)", "function dlAllDone()", "function primaryNote(primary, ap)"];
const parts = HEADERS.map((h) => extract(SRC, h));
const fn = parts.every(Boolean) ? parts.join("\n") : null;
check("summarize() and its helpers found in aux_onboarding.js", !!fn,
      HEADERS.filter((h, i) => !parts[i]));
if (fn) {
  const P = "mlx-community/Qwen3.5-4B-4bit";
  const ctx = { S: null, currentTheme: () => "system", STEPS: [{ key: "welcome" },
                { key: "models" }, { key: "prefs" }, { key: "done" }],
                warnOnce: () => {}, hookCtx: () => ({}) };
  vm.createContext(ctx);
  vm.runInContext(fn + "\nthis.summarize = summarize;", ctx, { filename: FILE });
  function lineFor(dl) {
    ctx.S = {
      prefs: {}, pick: { primary: P, background: null },
      data: { recommendation: { primary: P, background: null,
                                fits: [{ id: P, label: "Qwen3.5-4B" }] } },
      dl: dl
    };
    return (ctx.summarize() || []).find(l => l.indexOf("Assistant model:") === 0) || "";
  }
  const note = "the assistant model is running now — switch to it from the model menu to use it";
  let l = lineFor({ started: true, ids: [P],
                    applied: { primary: P, primary_active: false, primary_note: note } });
  check("model server was running: the summary says to switch in the model menu",
        l.indexOf(note) >= 0, l);
  l = lineFor({ started: true, ids: [P], applied: { primary: P, primary_active: true } });
  check("pick activated: plain 'Assistant model' line, no caveat",
        l === "Assistant model: Qwen3.5-4B, no background lane", l);
  l = lineFor({ started: true, ids: [P], applied: { primary: P, primary_active: false } });
  check("not activated without a note: a generic 'switch from the model menu' caveat",
        l.indexOf("model menu") >= 0, l);
  const dlLine = (applied) => {
    lineFor({ started: true, ids: [P], applied: applied });
    return (ctx.summarize() || []).find((x) => x.indexOf("Downloading") === 0) || "";
  };
  check("a started download quotes the size apply reported (round 3: a Ready figure)",
        dlLine({ primary: P, primary_active: true, download_gb: 3.1 }) ===
        "Downloading ~3.1 GB in the background — the model menu shows progress.",
        dlLine({ primary: P, primary_active: true, download_gb: 3.1 }));
  check("...and says no size when apply gave none",
        dlLine({ primary: P, primary_active: true }) ===
        "Downloading in the background — the model menu shows progress.");
  l = lineFor(null);
  // since the models step's Continue posts the picks (C63 round 2), "no apply
  // result" means nothing reached apply — the line must not claim the pick
  check("no apply result: the summary says unchanged, never the pick",
        /^Assistant model: unchanged/.test(l), l);
}

console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
