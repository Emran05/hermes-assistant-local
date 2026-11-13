// t_c56_dictation_disclosure.js — the dictation surfaces say when something
// leaves the Mac.
//
// 2026-10-21 audit C56 (the dictation part): on macOS 26 the helper's first
// dictation in a locale makes macOS download Apple's speech model
// (AssetInventory), while Settings › Dictation said "Nothing here reaches the
// network" and the helper only logged the download after it had finished.
//
// The REAL aux_dictation.js card is rendered in node (the module documents the
// headless-harness contract) and the REAL main.swift is read for the order of
// the announcement and the download.  The helper is never built.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + " " + JSON.stringify(detail || "")); }
}

// ---- the Settings card -----------------------------------------------------
global.window = global;
global.document = undefined;
eval(fs.readFileSync(path.join(REPO, "dashboard", "aux_dictation.js"), "utf8"));
const DC = global.hermesDictation;
const card = DC.cardHTML({
  data: { ok: true, running: true, install: { built: true, rebuild_note: "Rebuild note." },
          status: { engine: "SpeechAnalyzer", mic: "granted" },
          settings: { cleanup: "rules", dictionary: {}, app_styles: {} },
          today: {}, recent: [] },
});
const foot = (card.match(/<p class="dcfoot">([\s\S]*?)<\/p>/) || [])[1] || "";
check("the footnote no longer claims nothing reaches the network",
      !/Nothing here reaches the network/i.test(card), foot);
check("it names the one download and who serves it",
      /speech model/i.test(foot) && /from Apple/.test(foot) && /macOS 26/.test(foot), foot);
check("it says it happens once and what is NOT sent",
      /once/.test(foot) && /no audio or text is sent/.test(foot), foot);
check("transcription is still described as on device", /runs on device/.test(foot), foot);

// ---- the helper announces the download BEFORE it starts ------------------
const swift = fs.readFileSync(path.join(REPO, "app", "dictation", "main.swift"), "utf8");
const i0 = swift.indexOf("static func installAssetsIfNeeded(");
const fn = i0 < 0 ? "" : swift.slice(i0, swift.indexOf("\n    }\n", i0));
const say = fn.indexOf("willDownload?(");
const dl = fn.indexOf("downloadAndInstall()");
check("installAssetsIfNeeded takes a willDownload callback", /willDownload:/.test(fn), fn.slice(0, 120));
check("the callback runs before the download, not after", say > -1 && dl > -1 && say < dl, [say, dl]);
check("and is awaited, so the menu line cannot land after the download",
      /await willDownload\?\(/.test(fn), "");
check("the log line says it is a download from Apple",
      /log\("downloading the .* speech model from Apple/.test(fn), "");
check("the live start passes the callback through",
      /func start\(onDownload:/.test(swift) && /willDownload: onDownload/.test(swift), "");
check("the menu shows it while it happens",
      /live\.start\(onDownload:[\s\S]{0,400}lastNote = "Downloading the/.test(swift), "");
check("after a download the asset status is asked again, not assumed",
      /assetInstalled = await AnalyzerSupport\.installed\(\)/.test(swift)
      && !/assetInstalled = true/.test(swift), "");
check("--selftest still never downloads (install defaults to false)",
      /static func transcribeFile\(_ url: URL, install: Bool = false\)/.test(swift), "");

console.log("\nTESTS " + pass + " passed " + fail + " failed");
process.exit(fail ? 1 : 0);
