---
name: mac-control
description: "Drive the real Mac for the user: open & operate apps, play media, screenshot-and-send-to-me, read what's on screen. Routes to the fastest safe substrate (screen-oracle for reading · osascript-cookbook for scriptable apps · open -a for launching · computer_use for pixel-only UIs) and ALWAYS through the recorded, approval-gated path. Sends are hard-locked to the user's own Telegram; sends/purchases/deletes never auto-fire."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [macOS, desktop-control, screenshot, media, apps, automation, gated, computer-use]
    category: apple
    related_skills: [screen-oracle, osascript-cookbook, remote-computer-control, apple-notes, apple-reminders, imessage]
---

# Mac Control

Give the assistant **hands on the real desktop** — open apps, drive them, play
media, screenshot the screen and send it to the user, read what's on screen.
This is NOT sandboxed: it touches the live Mac on purpose. Containment is the
**flight recorder + the approval gate + hard blocklists**, not isolation.

This skill is a **recipe hub**. It does not reimplement reading (`screen-oracle`)
or scriptable-app control (`osascript-cookbook`); it routes you to the cheapest
safe substrate and, for anything consequential, to the gated path.

```bash
MC="${HERMES_HOME:-$HOME/.hermes}/skills/apple/mac-control/scripts"
```

## 1. Substrate decision table — pick the cheapest tool that works

| Task | Use | Why |
|---|---|---|
| Read / explain / extract what's on screen | **`apple/screen-oracle`** (never clicks) | pure read path: capture → `vision_analyze` → answer → delete; redacts credentials |
| Scriptable app (Notes, Reminders, Music, Safari, Finder, Mail, Calendar, System Events) | **`apple/osascript-cookbook`** — one gated `terminal` call | deterministic, no pixel driving, no fragile click session |
| A macOS Shortcut already exists for the action | the gated Shortcuts bus (`GET/POST /api/shortcuts*`) | user-curated, already allowlisted + approval-ticketed |
| Launch an app / open a URL | `open -a` via `$MC/open_app.sh` | recorded, http(s)-only, flag-injection-guarded |
| Screenshot → send it to me | `$MC/capture_send.sh` | capture + send **hard-locked to the user's own Telegram** |
| Non-scriptable / pixel-only UI (drag a slider, click an unlabeled button) | **`computer_use`** (see `computer-use/remote-computer-control`) | last resort; slower; every action recorded + irreversible; hard blocklists apply |

Rule of thumb: if you're about to screenshot-and-click a *scriptable* app, stop
and use an osascript recipe instead.

## 2. Recipes (each verified safe-to-run marked ✅)

### 2.1 Open an app and prove it worked ✅
```bash
"$MC/open_app.sh" "TextEdit"
"$MC/frontmost.sh"          # -> "TextEdit"  (System Events, pure read)
```

### 2.2 Screenshot the screen and send it to the user ✅
The send target is **hard-locked to the user's Telegram home channel** — the
script accepts no recipient/chat_id, ever. **Send is opt-in:** by default the
script only PREVIEWS (captures + prints the exact send command) and sends
nothing. Deliver only after the user says "send it".
```bash
"$MC/capture_send.sh" "here's your screen"                 # PREVIEW only (no send)
MC_CONFIRM_SEND=1 "$MC/capture_send.sh" "here's your screen"  # actually send (after user OK)
MC_DISPLAY=2 "$MC/capture_send.sh"                         # a specific display (integer)
```
The capture is a server-chosen scratch file in `~/.hermes/cache/mac-control/`
(never a user-supplied path), verified non-empty before sending (a TCC-denied
capture writes a 0-byte file — the script refuses to send it and tells you to
run `hermes computer-use doctor`). **A send is a consequential action** (see §5):
preview first, get an explicit "send it" from the user (or route through the
approval gate), and only then re-run with `MC_CONFIRM_SEND=1`.

### 2.3 What's on my screen? → defer to screen-oracle ✅
Do NOT click. Load `apple/screen-oracle` and run its read path
(`scripts/grab.sh frontmost` → `vision_analyze` → answer → delete). The
credential-redaction invariant applies: never transcribe passwords / OTP /
card numbers out of a capture.

### 2.4 Chrome → YouTube → play
```bash
"$MC/open_app.sh" "Google Chrome" "https://www.youtube.com/results?search_query=lofi"
```
Then, to click the first result (a pixel action on a non-scriptable page), use
`computer_use` (destructive → approval; recorded). Prefer the
`osascript-cookbook/scripts/tabs.jxa` recipe when you only need to open a URL in
a tab (no click needed).

### 2.5 Jot a note
Reuse `osascript-cookbook/scripts/notes_append.applescript` — one gated
`terminal` call. Do not run a computer_use click session for a scriptable app.

### 2.6 Play music (Music.app, scriptable) ✅
```bash
OSA="${HERMES_HOME:-$HOME/.hermes}/skills/apple/osascript-cookbook/scripts/osa.sh"
"$OSA" <<'OSA'
on run argv
  tell application "Music" to play
end run
OSA
```

## 3. Watch it happen — the Agent Desktop panel

Everything you do on screen is visible to the user in the dashboard's **Agent
Desktop** surface (`http://127.0.0.1:7788`, Desktop tab): a live screenshot
stream, the `computer_use` timeline (every click/type/capture, irreversible-
marked, 12-hour times), and a "Capture now" button. Those dashboard captures are
**local-only** (0600, ring-buffered, loopback-only, auto-pruned) — they are
never sent anywhere. Do not copy a dashboard frame into a reply or a send.

## 4. Degrade safe

- **cua-driver / TCC not granted** → pixel `computer_use` is unavailable. `open -a`,
  `screencapture`, and osascript still work. Surface the remediation
  (`hermes computer-use doctor`) — never a silent no-op.
- **Empty screenshot** → `capture_send.sh` refuses to send a 0-byte file.
- **App not installed** → `open -a` errors; report it, don't retry blindly.

## 5. SAFETY — invariants you MUST follow (non-negotiable)

The `computer_use` type/key blocklist is **defense-in-depth, not a boundary** —
it is small and bypassable, so the *real* controls are the recorder + the
approval gate + these rules. Follow them even when the tool would let you
proceed:

1. **Send target is hard-locked.** Screenshots/messages go to the user's own
   Telegram home channel and nowhere else. Never pass a `--to platform:chat_id`
   override; never send to Gmail; never send to any agent-chosen recipient.
   `capture_send.sh` enforces this in code — do not work around it.
2. **Sends / purchases / deletes / any irreversible click are CONSEQUENTIAL.**
   They never auto-fire. Get an explicit user "do it", and route through the
   permission gate (`permissions.decide`) or a confirmation. On a bare/ambiguous
   request, stop and ask — do not act.
3. **Every `computer_use` action is recorded** (`kind="computer"`,
   `reversible="no"`) and shows in the Agent Desktop timeline + flight recorder.
   A computer action can never be "undone" (`UNDO_WHITELIST={write,shell}`), only
   recorded. If the recorder is unavailable, do not run consequential actions.
4. **Clipboard-paste is a type-equivalent (§4.8).** `write_clipboard(payload)`
   then `key cmd+v` is exactly a `type` of `payload` and bypasses the type
   blocklist entirely. Treat any **write_clipboard-then-paste as consequential**:
   apply the same "no secrets, no blocked patterns, gate it" rules you would to
   `type`. Do not smuggle a command or a password through the clipboard.
5. **osascript `do shell script` is a shell action (§4.8).** Running
   `osascript -e 'do shell script "…"'` executes a shell command. Route it
   through the **terminal** permission gate exactly like any other shell command —
   it is not a free bypass of the shell gate, and it is not covered by the
   type blocklist.
6. **No self-approval / do not drive the approval surface (§4.9).** You may
   NEVER use `computer_use` (mouse/keyboard) to click an Approve / Confirm /
   "approve & restart" / permission-promotion control in the Hermes dashboard,
   or to answer an approval prompt — that would forge a human approval. Approvals
   are the user's, out-of-band. Before any pixel driving, if `frontmost.sh`
   reports the Hermes dashboard/approval window is frontmost, do not click into
   it. (The Agent Desktop panel intentionally exposes no merge / approve-and-
   restart control.)
7. **You cannot type destructive commands or press destructive combos.** The
   tool hard-blocks `sudo rm -rf`, `curl … | bash`, fork bombs, and
   logout/lock/empty-trash key combos **before** any approval. Do not try to
   route around the block (e.g. via clipboard, chunked typing, or
   `do shell script`) — rules 4 and 5 close those doors too.
8. **Screenshots may contain secrets.** The `screen-oracle` credential-redaction
   invariant applies to anything you capture: never transcribe passwords, OTP
   codes, card numbers, or seed phrases into a reply, an artifact, or memory.
   Dashboard captures stay local-only; the Telegram send goes only to the user.
