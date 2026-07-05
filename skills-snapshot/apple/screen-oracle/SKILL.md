---
name: screen-oracle
description: "'What am I looking at?' — turn the visible screen into answers. Screenshot + vision_analyze pipelines for explaining errors, extracting tables/text, describing charts, and diffing screen state. Pure read path: captures, analyzes, answers, deletes. Never clicks."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [screenshot, vision, OCR, screen, macOS, read-only, error-explain, extract]
    category: apple
    related_skills: [computer-use, ocr-and-documents]
---

# Screen Oracle

Answer questions about whatever is on the user's screen right now. Capture → `vision_analyze` → answer (or extract to an artifact). This skill is a **pure read path**:

- **NEVER click, type, scroll, or move the mouse.** If the task requires interaction ("close that dialog", "click retry"), STOP, load the `computer-use` skill instead, and tell the user you are escalating to it.
- Captures are scratch files in `~/.hermes/cache/screen-oracle/`. Delete them after answering. **NO silent screen archive, ever.** Saving anything outside the cache (e.g. to ~/Desktop) is a write — propose it and let the approval flow run.
- **HARD INVARIANT — credentials:** if a capture contains passwords, 2FA/OTP codes, credit-card numbers, seed phrases, or filled credential fields, summarize *around* them ("a password field, filled") — **never transcribe the values**, not into the answer, not into an artifact, not into memory.

## Scripts

- `scripts/grab.sh` — timestamped full/region/window captures into `~/.hermes/cache/screen-oracle/`; window bounds looked up via osascript System Events; auto-deletes captures older than 1 hour on every run. Modes: `full [display_n]`, `region X,Y,W,H`, `window "App"`, `frontmost`, `list-windows`, `clean`.

## 1. Capture decision table

Two capture paths exist. Pick deliberately:

| Situation | Use | Why |
|---|---|---|
| Quick look-and-answer ("what's this error?") | `terminal` → `scripts/grab.sh` | Cheap, fast, region/window-precise, no MCP round-trip |
| Interaction might follow (user may ask you to click next) | `computer_use` tool, action `screenshot` | Same pixel space cua will click in later; cua-driver (com.trycua.driver) has its own TCC grant so it **always works**, and runs in the background — doesn't steal the cursor |
| Need one window or region only | `grab.sh window "App"` / `grab.sh region X,Y,W,H` | Smaller image = sharper, cheaper vision_analyze |
| Multi-display | `computer_use` `switch_display` then `screenshot`; or `grab.sh full 2` | screencapture `-D n` selects display n (1-based) |
| Terminal capture comes back black/empty | `computer_use` screenshot | See gotcha below |

Recipes:

```bash
# Whole screen (display 1), path is printed on stdout:
bash ~/.hermes/skills/apple/screen-oracle/scripts/grab.sh full

# Front window of the frontmost app (best default for "what am I looking at?"):
bash ~/.hermes/skills/apple/screen-oracle/scripts/grab.sh frontmost

# A specific app's front window:
bash ~/.hermes/skills/apple/screen-oracle/scripts/grab.sh window "Safari"

# A region (X,Y,W,H in screen points):
bash ~/.hermes/skills/apple/screen-oracle/scripts/grab.sh region 0,0,1200,400

# What windows exist right now (app | title | x,y,w,h) — no capture:
bash ~/.hermes/skills/apple/screen-oracle/scripts/grab.sh list-windows
```

**Gotcha — black/empty screencapture:** `screencapture` needs a Screen Recording TCC grant for the *spawning context*. Build-time test (2026-07-05, interactive terminal spawned by the hermes host): **real image, not black — terminal path verified working.** But under a different launch context (launchd, `hermes serve` restart after an OS update) it may silently produce a black or near-empty PNG. `grab.sh` warns on suspiciously small files; if vision_analyze reports a black/blank image, fall back to the `computer_use` screenshot tool and record which path works in memory as a `§` entry (e.g. `§ screen-oracle: terminal screencapture OK as of <date>` or `§ screen-oracle: terminal capture BLACK under serve — use computer_use screenshot`), so future sessions don't re-discover it.

**Gotcha — first System Events call:** `window`/`frontmost`/`list-windows` use osascript System Events, which needs an Automation/Accessibility grant; the very first call from a fresh context may fail or stall once while macOS shows the consent prompt. Retry once before concluding it's broken.

**Gotcha — Retina 2x:** grab.sh coordinates (`region`, window bounds) are in screen *points*; the PNG comes out at 2x *pixels* on Retina. If you later hand coordinates from an analyzed image to `computer_use`, divide pixel coordinates by the scale factor (usually 2) — or better, take a fresh `computer_use` screenshot and work in its coordinate space.

## 2. vision_analyze prompt cookbook

Run `vision_analyze` on the captured PNG path. Purpose-built prompts — copy, don't improvise:

- **Summarize this window:** "Describe what application and document/page is shown, what the user appears to be doing, and the 3 most important pieces of information visible. Be concrete; quote visible titles."
- **Extract a table:** "Transcribe the table in this image as tab-separated values, one row per line, first line = headers. Preserve every cell exactly; use an empty cell for unreadable values. Output only the TSV."
- **Read an error verbatim:** "Transcribe the exact text of the error dialog/message in this image, character for character, including error codes and file paths. Then, on a new line after 'CONTEXT:', note which app produced it."
- **Describe a chart:** "Describe this chart: chart type, axes and their ranges, each series, the overall trend, and any notable outliers or inflection points. Give approximate values for the highest and lowest points."
- **Diff screen vs text:** "Compare the text visible in this image against the following reference text. List every difference (missing, added, changed) as bullets. Reference: <paste text>."
- **General OCR:** "Transcribe all readable text in this image top-to-bottom, left-to-right. Mark unreadable spans as [illegible]. Do not summarize — transcribe."

Append to every prompt when the screen might be sensitive: "If any passwords, one-time codes, or credential values are visible, write [REDACTED] instead of the value."

## 3. OCR-to-artifact flow

When the user wants the text *kept*, not just answered:

1. Capture (window-scoped if possible — tighter crop, better OCR).
2. `vision_analyze` with the OCR or table prompt above.
3. Clean up via the dashboard transform endpoint (local, keyless):
   ```bash
   curl -s -X POST http://127.0.0.1:7788/api/clip/transform \
     -H 'Content-Type: application/json' \
     -d '{"op":"cleanup","text":"<extracted text>"}'
   ```
   (Use it to strip OCR artifacts, fix whitespace, normalize the TSV. If the endpoint is unavailable, clean up inline yourself.)
4. Deliver: reply inline for short results. For "save it" requests, **propose the write first** — e.g. `~/Desktop/screen-extract-<topic>.md` — and let the approval tier gate it. Never save silently.
5. Delete the scratch capture: `bash .../grab.sh clean` (or rely on the 1-hour auto-clean, but prefer immediate deletion after sensitive screens).

## 4. Recipes

### "Explain this error"
1. `grab.sh frontmost` (the dialog is almost always on the frontmost app; if the user names the app, `grab.sh window "App"`).
2. `vision_analyze` with the **error-verbatim** prompt.
3. Answer with: the exact error text, what it means, and the concrete fix (commands/steps). If the fix requires clicking something, say so and offer to escalate to the `computer-use` skill — do not click from here.
4. `grab.sh clean`.

### "Pull the numbers out of this dashboard"
1. Identify the window: `grab.sh list-windows`, then `grab.sh window "<App>"` (or `region` for one panel).
2. `vision_analyze` with the **table-as-TSV** prompt; for gauges/charts also run the **chart** prompt.
3. Optionally pipe through `/api/clip/transform` to normalize, then reply with a clean table. Offer (don't assume) saving to a file.
4. Timestamp the answer — "as of 1:45 PM" (12-hour format, always) — dashboard numbers go stale.

### "What changed on this screen since my last capture?" (two-shot diff)
1. Shot A already exists in `~/.hermes/cache/screen-oracle/` from earlier in the session (check `ls -t` there; remember auto-clean removes files older than 1 hour — if A is gone, say so and offer to start a fresh baseline).
2. Take shot B the same way A was taken (same mode/window, so framing matches).
3. `vision_analyze` shot A with the **general OCR/summarize** prompt, then shot B with: "Compare against this description of the earlier state and list what changed (appeared, disappeared, values that differ): <A's analysis>". Two calls — vision_analyze takes one image at a time.
4. Report the delta with both timestamps in 12-hour format ("between 1:12 PM and 1:45 PM: …").
5. Clean both captures unless the user wants to keep watching — then keep only the newest as the next baseline and say you're doing so.

## 5. Hygiene & hard rules

- Scratch captures live **only** in `~/.hermes/cache/screen-oracle/`; grab.sh auto-deletes anything older than 1 hour, and you should `grab.sh clean` immediately after answering on sensitive screens. No silent screen archive, ever.
- This skill **never clicks** — zero `computer_use` actions except `screenshot`/`switch_display`. Interaction requests = escalate to the `computer-use` skill, explicitly.
- **Never transcribe** passwords, 2FA codes, or credential-field contents — summarize around them. This applies to answers, artifacts, memory, and Telegram replies alike.
- Everything stays on this machine. The only thing that may leave is the user's own locked Telegram reply, and only if they asked for one.
- Timestamps in answers: 12-hour format.
