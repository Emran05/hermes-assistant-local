---
name: osascript-cookbook
description: "Battle-tested AppleScript/JXA recipes: control Mac apps (Notes, Reminders, Music, Finder, Safari) with one gated terminal call instead of a computer-use session."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [AppleScript, JXA, osascript, macOS, Notes, Reminders, Safari, Finder, Music, automation, TCC]
    related_skills: [apple-notes, apple-reminders]
---

# osascript Cookbook

AppleScript/JXA is the fastest, most reliable way to drive scriptable Mac apps: one
approval-gated `terminal` call, deterministic output, no screen driving. This skill is
the substrate every other `apple/` skill should cite. Set once per session:

```bash
OSA="${HERMES_HOME:-$HOME/.hermes}/skills/apple/osascript-cookbook/scripts/osa.sh"
```

## 1. Decision table — pick the cheapest tool that works

| Situation | Use |
|---|---|
| Target app is scriptable (Notes, Reminders, Music, Finder, Safari, Mail, Calendar, System Events…) | **osascript** via `$OSA` (this skill) |
| A macOS Shortcut already exists for the action | `curl -s http://127.0.0.1:7788/api/shortcuts/run` — the gated Shortcuts bus (approval-ticketed, allowlisted). Check `GET /api/shortcuts` first; if the bus isn't live yet, fall back to osascript. |
| Neither: app has no dictionary, no Shortcut (most third-party GUIs) | **computer_use** — documented last resort. Slower, recorded by the flight recorder, and every action is irreversible-marked. |

Rule of thumb: if you're about to screenshot-and-click a scriptable app, stop and use a recipe below instead.

## 2. Quoting & escaping doctrine (esc-patterns — load-bearing, read this)

**NEVER interpolate user text into a single-quoted `osascript -e '...'` string.** An
apostrophe in the user's text ends your quote; a `"` breaks the AppleScript string;
newlines and emoji corrupt silently. You will not get an error — you will get wrong data.

Instead, user data rides **argv** and the script body stays a constant:

```bash
"$OSA" "any user text: it's \"fine\" — even
newlines" <<'OSA'
on run argv
	return "got: " & item 1 of argv
end run
OSA
```

`osa.sh` invokes `osascript - "$arg"...` (script on stdin, args as argv) or
`osascript <file> "$arg"...` with `-f`. The heredoc delimiter is **quoted** (`<<'OSA'`)
so bash never touches the script body either.

**Prefer JXA for anything structured.** `osascript -l JavaScript` returning
`JSON.stringify(...)` gives you parseable JSON instead of AppleScript's comma-soup
(`item 1 of {a, b}` text mangling):

```bash
"$OSA" -l JavaScript "world" <<'OSA'
function run(argv) { return JSON.stringify({hello: argv[0]}); }
OSA
```

Wrapper flags: `-l JavaScript` (JXA), `-f <file>` (run a bundled script), `--` (end of
options if the first user arg could start with `-`).

## 3. Recipes

Every recipe is tagged **READ** (no state change; safe tier) or **MUTATE** (changes user
data; rides the graduated approval tiers — never pre-approve, let the gate fire).
All app-targeting recipes pop a one-time TCC dialog on first use — see §4.

### Notes

**Search (READ)** — returns JSON `[{name, folder}]`:

```bash
"$OSA" -l JavaScript "meeting" <<'OSA'
function run(argv) {
  const q = argv[0].toLowerCase();
  const notes = Application('Notes').notes();
  const hits = [];
  for (let i = 0; i < notes.length && hits.length < 25; i++) {
    if (notes[i].name().toLowerCase().indexOf(q) !== -1)
      hits.push({ name: notes[i].name(), folder: notes[i].container().name() });
  }
  return JSON.stringify(hits);
}
OSA
```
Expected: `[{"name":"Meeting notes","folder":"Notes"}]` (or `[]`). Failure modes: exit 1
empty stderr = TCC (§4); slow on thousands of notes (body access is the slow part —
this recipe reads names only).

**Create / append (MUTATE)** — bundled script, creates if absent:

```bash
"$OSA" -f "${OSA%osa.sh}notes_append.applescript" "Grocery List" "milk, eggs"
```
Expected: `created note: Grocery List` or `appended to note: Grocery List`.
Gotcha: Notes bodies are HTML — escape `< > &` in the text first if it may contain them.

### Reminders

**Add with due date (MUTATE)** — dates are built from AppleScript **date objects**
(`set year of d to …`), never parsed from locale strings like `"7/10/2026 9:00 AM"`
(silently wrong outside en_US):

```bash
"$OSA" -f "${OSA%osa.sh}reminder_add.applescript" "Call dentist" 2026 7 10 9 0
```
Expected: `created reminder "Call dentist" due Fri Jul 10 9:00 AM`.
**Confirm back to the user in exactly that 12-hour form.** Failure modes: TCC on first
run; Reminders under iCloud sync may take seconds to appear on other devices (that's
normal, don't retry).

### Music

**Now playing (READ)**:

```bash
"$OSA" -l JavaScript <<'OSA'
function run() {
  const m = Application('Music');
  if (!m.running()) return JSON.stringify({running:false});
  const st = m.playerState();
  const o = { running: true, state: st };
  if (st !== 'stopped') {
    const t = m.currentTrack;
    o.track = t.name(); o.artist = t.artist(); o.album = t.album();
  }
  return JSON.stringify(o);
}
OSA
```
Expected: `{"running":true,"state":"playing","track":"…","artist":"…","album":"…"}`.

**Play / pause (MUTATE — mild, but it changes device state)**:

```bash
"$OSA" <<'OSA'
tell application "Music" to playpause
OSA
```
Expected: no output, exit 0. Failure: `stopped` state with an empty library — nothing to play.

### Finder

**Reveal in Finder (READ — window state only)**:

```bash
"$OSA" "/Users/me/Documents/report.pdf" <<'OSA'
on run argv
	tell application "Finder"
		reveal POSIX file (item 1 of argv) as alias
		activate
	end tell
end run
OSA
```
Failure: `File … wasn't found` if the path doesn't exist — check with `test -e` first.

**Tag a file (MUTATE)** — tags are xattrs; do it in the shell, not AppleScript:

```bash
xattr -w com.apple.metadata:_kMDItemUserTags \
  '<plist version="1.0"><array><string>Hermes</string></array></plist>' "/path/to/file"
```

**Move to Trash (MUTATE)** — **NEVER `rm` user files.** Trash is user-recoverable; rm is not:

```bash
"$OSA" "/Users/me/old-draft.txt" <<'OSA'
on run argv
	tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)
end run
OSA
```
Expected: Finder item reference on stdout. The file lands in `~/.Trash`, restorable via Finder > Put Back.

### Safari

**List windows/tabs (READ)** — JSON via bundled JXA:

```bash
"$OSA" -l JavaScript -f "${OSA%osa.sh}tabs.jxa"
```
Expected: `{"running":true,"windows":[{"window":1,"tabs":[{"index":1,"title":"…","url":"…"}]}]}`.
Returns `{"running":false,"windows":[]}` if Safari isn't running — it deliberately does
not launch Safari for a read.

**Open URL (MUTATE — navigation)**:

```bash
"$OSA" "https://example.com" <<'OSA'
on run argv
	tell application "Safari"
		if not running then launch
		activate
		if (count of windows) is 0 then
			make new document with properties {URL:item 1 of argv}
		else
			tell front window to set current tab to (make new tab with properties {URL:item 1 of argv})
		end if
	end tell
end run
OSA
```

**Add to Reading List (MUTATE)**:

```bash
"$OSA" "https://example.com/article" <<'OSA'
on run argv
	tell application "Safari" to add reading list item (item 1 of argv)
end run
OSA
```
Failure: duplicate URLs are accepted silently (Safari dedupes on its side).

### System Events

**Frontmost app + window titles (READ)**:

```bash
"$OSA" -l JavaScript <<'OSA'
function run() {
  const se = Application('System Events');
  const p = se.applicationProcesses.whose({frontmost: true})[0];
  let wins = [];
  try { wins = p.windows().map(w => w.name()); } catch (e) {}
  return JSON.stringify({ app: p.name(), windows: wins });
}
OSA
```
Expected: `{"app":"Safari","windows":["Apple — Start Page"]}`. Failure: reading *window
titles* of other apps may additionally require Accessibility (not just Automation)
consent — `not allowed assistive access` in stderr means System Settings > Privacy &
Security > Accessibility.

## 4. TCC consent — the one-time dialog that can hang you forever

The **first** time this process automates each app, macOS pops a per-app consent dialog
("…wants access to control Notes"). Rules:

1. **Never burn a first-run inside an unattended run** (cron, watchtower, `hermes -z`,
   launchd). The dialog blocks the osascript call *forever* — the run just hangs.
   Schedule an **attended first-run per app**: tell the user "next command will pop a
   consent dialog for <App> — click OK", run the READ recipe once, confirm.
2. Grants attach to the *calling process* and **may not inherit under launchd/serve
   contexts** — a grant earned in Terminal does not automatically cover the dashboard's
   serve process. If a previously-working recipe starts failing only under launchd,
   suspect this.
3. `osascript` exiting 1 with **empty stderr** (or `-1743` / `Not authorized`) = TCC
   denial. `osa.sh` prints `OSA-TCC-DENIED` when it sees this pattern. **Do not retry**
   — say so and ask the user to grant it (System Settings > Privacy & Security >
   Automation) or schedule an attended run.
4. **Maintain the grant ledger in memory.** Keep a §-delimited entry in MEMORY.md like:
   `§ osascript TCC grants (terminal context): Notes ✓ 2026-07-05, Reminders ✗, Safari ✗ §`
   Update it every time a first-run succeeds or a denial is hit, and check it before
   planning unattended work.

## 5. App-state guards — the #1 flake

Targeting an app that isn't running either auto-launches it (slow, sometimes surprising
UI) or errors. Always guard:

- JXA: `if (!Application('Notes').running()) …` — decide: launch, or report "not running".
- AppleScript: `tell application "Notes" \n if not running then launch \n end tell`.
- For READ recipes, prefer **reporting not-running over launching** (see tabs.jxa) — a
  read should not visibly change the user's desktop.
- After `launch`, the app may need a beat before its objects exist; `delay 1` fixes the
  "Can't get window 1" class of flake.

## 6. Safety rails (non-negotiable)

- Every call rides the **approval-gated terminal** — MUTATE recipes will trigger the
  graduated tiers; that is correct behavior, never route around it.
- Label any new recipe you write READ or MUTATE so tiering stays clean.
- Finder deletions are **trash-not-delete**, always.
- TCC dialogs are **surfaced to the user**, never worked around.
- Confirm times back to the user in **12-hour format** ("Fri Jul 10 9:00 AM").

## Scripts

- `scripts/osa.sh` — the wrapper: argv passing, `-l JavaScript`, `-f file`, TCC-denial detection.
- `scripts/notes_append.applescript` — Notes create/append [MUTATE].
- `scripts/reminder_add.applescript` — Reminders add with date-object due date, 12-hour confirmation [MUTATE].
- `scripts/tabs.jxa` — Safari tab dump as JSON [READ].

## Zero-TCC smoke test (safe anywhere, even unattended)

Targets no application, so no consent dialog can fire:

```bash
"$OSA" -l JavaScript <<'OSA'
function run() {
  ObjC.import('stdlib');
  const app = Application.currentApplication(); app.includeStandardAdditions = true;
  const v = app.getVolumeSettings();
  return JSON.stringify({ now: new Date().toString(), outputVolume: v.outputVolume });
}
OSA
```

Expected: `{"now":"…","outputVolume":<0-100>}`. If this fails, the problem is the
wrapper/shell, not TCC.
