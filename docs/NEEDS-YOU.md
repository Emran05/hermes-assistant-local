# Needs-you checklist (batched user actions)
Items only you can do; grouped for 2-3 sittings. I keep this current.

## Batch 1 (whenever convenient)
- [ ] Calendar under launchd: System Settings → Privacy & Security → Calendars
      → enable for the framework Python (path in CLAUDE.md) — unblocks Today widget.
(nothing else yet — items append as workstreams land)

## Batch 1 — quick eyeball (optional, functionally verified already)
- [ ] Mind view → "What it remembers" card: confirm the memory editor looks right
      (edit a fact, add one, watch the char meter, create/delete/restore a topic file).
      Backend + rendering are curl- and harness-verified; this is pure visual QA.

## DECISION NEEDED — default model (see docs/FINDINGS.md)
- [ ] Keep **Qwen3-30B-A3B** as the default local model? (Recommended — the Hermes-8B
      won't reliably call tools, which breaks the "agent does things" vision. Qwen ~17GB,
      fits your 64GB fine, and is now loaded + proven.) Currently left ON Qwen. Say the
      word to revert to Hermes-8B.

## Trust panel eyeball (optional — functionally proven)
- [ ] Mind view → "Trust & Permissions" card: the 17 action-classes with Auto/Ask/Never
      controls + floor padlocks. All three tiers are live-drilled and working; visual QA only.

## Menu-bar Quick-Ask + Clipboard (P2.2/P2.3) — quick eyeball (functionally verified)
- [ ] Look for the spark glyph in your menu bar; click it → chat popover opens.
- [ ] Press ⌃⌥Space (Control-Option-Space) from any app → popover toggles (no Accessibility prompt).
      Default hotkey is ⌃⌥Space (avoids Spotlight/Alfred) — tell me if you want a different combo.
- [ ] ⌘⇧V or the "Clipboard" button → runs a local transform on your clipboard.
- [ ] Optional: status-item right-click → "Open at Login".
      (Note: login-launch still opens the main window too — no clean API to suppress that.)

## Connect Google (read-only Gmail + Calendar + Contacts) — ~10 min, one time
Sending is IMPOSSIBLE by design: only read scopes are ever requested.
1. Google Cloud Console → create/select a project: https://console.cloud.google.com/projectselector2/home/dashboard
2. Enable exactly: **Gmail API**, **Google Calendar API**, **People API** (nothing else).
3. OAuth consent screen: External, keep in Testing, add YOUR account as a Test user.
4. Credentials → Create OAuth client ID → type **Desktop app** → Download JSON.
5. Dashboard → Mind → **Google** card → paste the JSON contents → Save.
6. Click **Open Google consent**, approve the three read-only items.
7. Browser will fail to load localhost:1 — EXPECTED. Copy the full address-bar URL.
8. Paste it into the card → Finish. Chip flips to Connected · read-only.
(Undo anytime: the card's Disconnect button.)

# ═══ THE ONE SITTING (~25 min) — everything below in one pass ═══
## 1. Message Center — Full Disk Access (2 min)
   System Settings → Privacy & Security → Full Disk Access → + →
   /Applications/Hermes Assistant.app (the APP, not python) → toggle ON →
   quit & reopen Hermes Assistant. Real conversations appear within ~60s.
   (Note: if we ever rebuild the app, macOS drops this grant — re-add takes 30s.)
## 2. Google connect (~12 min) — steps in the "Connect Google" section above.
## 3. Calendar TCC for launchd python (1 min, optional once Google connects):
   run `icalBuddy calendars` if prompted, or grant Calendars to python in Settings.
## 4. Quick eyeball QA (3 min): menu-bar spark glyph click → popover; ⌃⌥Space;
   ⌘⇧V clipboard sheet; Mind → Trust panel / memory editor / Google card /
   Watchtower card / Shortcuts card; hub → Claude Usage widget.
## 5. Say "push it" → I push batch 1 (all staged commits + tags v0.1/v0.2).
