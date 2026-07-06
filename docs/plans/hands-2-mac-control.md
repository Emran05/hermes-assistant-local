# Hands, Part 2 — Mac Control (general desktop driving)

**Status:** spec / not yet built · **Author:** Claude (Opus 4.8) · **Date:** 2026-07-05
**Sibling spec:** `hands-1-*` (sandboxed Claude Code self-upgrade) — this doc is the *direct desktop control* half.

> One-line thesis: give the local Hermes agent (Qwen3-30B) **hands on the real desktop** — open apps, drive them, play media, screenshot and send it to the user, read what's on screen — where every consequential action rides the **existing** flight-recorder + graduated-permission rails. Unlike Hands-1, this path is **NOT sandboxed**: it is *meant* to touch the live desktop. Containment here is the *approval gate + recorder + hard blocklists*, not sandbox-exec.

---

## 0. Ground truth (grepped, not assumed)

Everything below is verified against the live tree today:

| Claim | Evidence |
|---|---|
| Dashboard server is `dashboard/server.py` (2552 lines) | `wc -l dashboard/server.py` |
| Aux modules exec'd into server globals, sorted, after `expanders_extra.py` | `server.py:2135` `_AUX_FILES = ["expanders_extra.py"] + sorted(aux_*.py)`; `server.py:2144 exec(_f.read(), globals())` |
| Route registrars | `server.py:2107 register_get`, `server.py:2111 register_post`; handlers take a `RouteCtx` (`.q1()`, `.body`) |
| Flight recorder API | `aux_recorder.py:533 recorder_record_local(tool, target, kind, reversible, source, **kw)`; `computer_use` is already classified `kind="computer"`, `REVERSIBLE_POLICY["computer"]="no"` (`aux_recorder.py:` TOOL_KIND / REVERSIBLE_POLICY tables) |
| `/api/undo` structurally refuses computer actions | `UNDO_WHITELIST = {"write","shell"}` (aux_recorder.py) — a computer action can never be "undone", it can only be recorded |
| Permission engine | `permissions.py` — `decide()`, `TIERS=("auto","ask","never")`, `SHIPPED_DEFAULT="ask"`, per-class `floor`, sidecar-hash tamper detection; imported (not exec'd) by both `hermes_rpc.py` and `aux_permissions.py` |
| **Action-bus template exists** | `aux_shortcuts.py` — allowlist + `permissions.decide()` + single-use ticket (`_SB_TICKETS`, 5-min TTL) + `recorder_record_local` on every attempt. This spec clones its shape. |
| computer_use tool | `~/.hermes/hermes-agent/tools/computer_use/tool.py` — `_SAFE_ACTIONS={capture,wait,list_apps}` always allowed; `_DESTRUCTIVE_ACTIONS={click,type,key,drag,scroll,...}` go through `_request_approval`; `_BLOCKED_KEY_COMBOS` (logout/lock/empty-trash) and `_BLOCKED_TYPE_PATTERNS` (`curl|bash`, `sudo rm -rf`, fork bomb) are **hard-blocked regardless of approval** |
| Send-media syntax | `hermes send --to telegram "MEDIA:/tmp/x.png"` (confirmed in `hermes send --help`: "To send an image/document as an attachment, use MEDIA:<path> in the message text") |
| Screenshot works | `screencapture -x <path>` → verified 3456×2234 PNG written this session |
| Read-what's-on-screen already exists | `~/.hermes/skills/apple/screen-oracle/SKILL.md` (pure read path, capture→vision_analyze→answer→delete; never clicks; credential-redaction invariant) + `scripts/grab.sh` |
| Scriptable-app control already exists | `~/.hermes/skills/apple/osascript-cookbook/SKILL.md` (+ scripts/) and `~/.hermes/skills/computer-use/remote-computer-control/SKILL.md` |
| Widget registry | `server.py:1781 WIDGETS = {...}`; aux modules extend it (`aux_claude_usage.py:432 WIDGETS["claude_usage"]=...`, `EXPANDERS[...]`). Frontend cards chain `window.mindExtras` (`aux_shortcuts.js`). |
| Folder-grant reuse | `server.py:950 ACCESS_FILE=DATA/access.json`, `953 get_access()` — the scope system Hands-1 reuses; Mac-control is desktop-scoped, not folder-scoped, so it does **not** consume access.json |

**What this means:** ~80% of Mac-control already exists as skills. The *new* work is a **thin governed action-bus** (`aux_mac.py`) that (a) gives the dashboard a "take the wheel" quick-action, (b) forces `screencapture → Telegram` and `open -a` through a recorded, permission-gated endpoint, and (c) adds an **Agent Desktop** panel showing live desktop-control activity. The skill work is *consolidation*, not greenfield.

---

## 1. Goal & acceptance criteria

**Goal:** The user can say (from Telegram, hub, or CLI) "open Chrome and play lo-fi on YouTube", "screenshot my screen and send it to me", "open Notes and jot this down", "what's on my screen?" — and Hermes does it on the *real* desktop, with every consequential action logged to the flight recorder and consequential-class actions gated by the permission engine.

**Acceptance criteria (all must hold):**

1. **AC1 — Open + drive an app.** "Open Chrome and play a YouTube video" launches Chrome (`open -a`), navigates to a YouTube search, and starts playback. Recorded.
2. **AC2 — Screenshot → me.** "Screenshot my screen and send it to me" produces a PNG via `screencapture -x` and delivers it to the user's locked Telegram via `hermes send --to telegram "MEDIA:<path>"`. The send is a *consequential* action → gated (or auto if user pre-trusted `desktop-capture-send`). Recorded twice: the capture (read) and the send (net/consequential).
3. **AC3 — Read the screen.** "What's on my screen?" runs the **existing** screen-oracle read path (capture→vision→answer→delete). Never clicks. Credential-redaction invariant holds.
4. **AC4 — Jot a note.** "Open Notes and write X" uses the osascript-cookbook `notes_append.applescript` recipe (one gated `terminal` call) — not a fragile click session.
5. **AC5 — Every computer_use action is recorded** as `kind="computer"`, marked irreversible (`reversible="no"`), and appears in the Agent Desktop panel + flight recorder.
6. **AC6 — Hard blocklist holds.** The agent *cannot* type `sudo rm -rf /`, `curl … | bash`, or press log-out/lock/empty-trash combos — blocked *before* any approval, at the tool layer (`_BLOCKED_TYPE_PATTERNS`, `_BLOCKED_KEY_COMBOS`).
7. **AC7 — Consequential actions still gate.** Sending a message, a purchase click, or anything the permission engine classes non-`auto` surfaces an approval ticket; nothing fires on a bare request when the class is `ask`/`never`.
8. **AC8 — No secret exfiltration.** The screenshot/vision path never transcribes passwords/OTP/card numbers (screen-oracle invariant), and the send path is locked to the single configured Telegram user — never Gmail.
9. **AC9 — Dashboard surface.** An **Agent Desktop** panel shows: current frontmost app + a live thumbnail (opt-in), recent desktop-control actions from the recorder, pending approvals, and 1–2 quick-actions ("Take a screenshot & send it to me", "Let the agent take the wheel").
10. **AC10 — Degrades safe.** If the recorder is unavailable, the bus refuses to run (mirrors `aux_shortcuts.py:525` "refusing to run unrecorded"). If cua-driver/TCC is not granted, the bus returns a clear remediation message, never a silent no-op.

**Explicit non-goals:** no sandbox-exec here (that's Hands-1); no new vision model; no autonomous multi-step "agent takes over for 10 minutes unattended" mode in v1 (each consequential step is gated or pre-trusted per class).

---

## 2. Architecture (exact components)

```
User (Telegram / hub / CLI)
        │  "screenshot my screen and send it to me"
        ▼
Hermes agent (Qwen3-30B)  ── picks skill: apple/mac-control
        │
        ├─(read path)──► screen-oracle skill ─ screencapture -x ─ vision_analyze ─ answer ─ delete
        │
        ├─(scriptable app)► osascript-cookbook ─ one gated `terminal` call (osa.sh / *.applescript)
        │
        ├─(pixel driving)─► computer_use tool ─ cua-driver ─ _SAFE vs _DESTRUCTIVE ─ _request_approval
        │                        (hard blocklists enforced here, pre-approval)
        │
        └─(governed bus)─► POST /api/mac/*  (aux_mac.py, NEW)
                                 │  permissions.decide()  → ticket (ask) | run (auto) | 403 (never)
                                 │  recorder_record_local(...)   ← EVERY attempt + outcome
                                 ├─ screencapture -x <scratch.png>
                                 ├─ hermes send --to telegram "MEDIA:<path>"   (consequential → gated)
                                 └─ open -a "<App>" [url]
        ▼
Dashboard "Agent Desktop" panel (aux_mac.js) ─ frontmost app, recent actions, pending approvals, quick-actions
```

**Component inventory (new vs. reused):**

| Component | New? | Path |
|---|---|---|
| `aux_mac.py` — governed Mac-control bus (routes, ticket flow, recorder wiring) | **NEW** | `dashboard/aux_mac.py` |
| `aux_mac.js` — Agent Desktop card | **NEW** | `dashboard/aux_mac.js` |
| `desktop-control` + `desktop-capture-send` permission classes | **NEW** (2 rows in `CLASS_META`) | `dashboard/permissions.py` |
| `apple/mac-control` skill (recipe hub) | **NEW** | `~/.hermes/skills/apple/mac-control/SKILL.md` |
| Flight recorder | reused | `aux_recorder.py:533 recorder_record_local` |
| Permission engine | reused | `permissions.py decide()/audit()` |
| computer_use tool + blocklists | reused | `tools/computer_use/tool.py` |
| screen-oracle (read path) | reused | `~/.hermes/skills/apple/screen-oracle/` |
| osascript-cookbook (scriptable apps) | reused | `~/.hermes/skills/apple/osascript-cookbook/` |
| `hermes send` media delivery | reused | `hermes send --to telegram "MEDIA:<path>"` |
| Folder-grant (access.json) | **not used** — desktop scope, not folder scope | `server.py:953` |

---

## 3. Data model

Everything reuses existing stores; the only *new* persistent config is a small policy file mirroring `shortcuts.json`.

**3.1 Recorder rows (existing schema, `~/.hermes/dashboard/recorder.db`).** Mac-control writes via `recorder_record_local`:

```python
recorder_record_local(
    tool="mac_control",            # or "computer_use" for pixel actions (already recorded by the tool)
    target="screencapture→telegram",
    kind="computer",               # read for capture-only; "net" for the send leg
    reversible="no",               # computer/net are never in UNDO_WHITELIST
    source="dashboard",            # or "telegram"/"cli"
    status="done"|"pending"|"blocked"|"error",
    summary="sent 1 screenshot to Telegram (user)",
    args={"action":"capture_send","app":None},  # capped at ARGS_CAP=8192
)
```

**3.2 New config `~/.hermes/dashboard/mac-control.json` (mode 600), mirrors `shortcuts.json`:**

```json
{
  "enabled": false,                     // master switch — installing changes nothing
  "capture_send": {"exposed": true},    // the one low-risk convenience action
  "app_open": {"allowlist": ["Google Chrome","Notes","Music","TextEdit"]},
  "live_thumbnail": {"enabled": false}, // Agent Desktop opt-in preview (privacy)
  "take_the_wheel": {"exposed": false}  // computer_use pixel-driving quick-action, off by default
}
```

**3.3 In-memory ticket table (not persisted), clone of `aux_shortcuts.py:_SB_TICKETS`:** `_MAC_TICKETS[token] = {token, action, args, pk, tier, src, state, created, expires}`, 5-min TTL, dies with the process, single-use.

**3.4 Permission classes** added to `permissions.py CLASS_META` (mirrors `shortcuts-run` at `permissions.py:95`):

| class id | label | risk | default | floor | notes |
|---|---|---|---|---|---|
| `desktop-control` | Desktop pixel control (click/type/drag) | high | `ask` | `ask` | maps `computer_use` destructive actions; **never** representable as `auto` (floor `ask`) |
| `desktop-capture-send` | Screenshot & send to me | med | `ask` | `""` | the *only* Mac-control class the user may promote to `auto` (send is locked to their own Telegram) |

Add both ids to the critical-list / heuristic map (`permissions.py:307 _class_of`, `_heuristic`), keyed `desktop-control:*` and `desktop-capture-send:*`.

---

## 4. Backend — aux module + endpoints (exact names)

New file `dashboard/aux_mac.py` — exec'd into server globals by the loader (`server.py:2135`; sorts after `aux_recorder.py`/`aux_permissions.py` so `recorder_record_local` + `register_post` exist, and after `aux_shortcuts.py` — irrelevant ordering, no shared names). **Only defines new names** (`MC_*`, `_mc_*`, `mac_*`). `import permissions as _mc_perm`, `import time` (no `datetime` — the exec'd-module datetime private-alias gotcha noted in CLAUDE.md; `aux_shortcuts.py` also uses only `time`).

**Endpoints (all mirror the shortcuts bus shape):**

| Method + path | Handler | Purpose |
|---|---|---|
| `GET /api/mac` | `mac_get_handler(ctx)` | panel state: `enabled`, frontmost app (osascript), recent recorder rows (tool in `mac_control`,`computer_use`), pending tickets, config, cua/TCC health |
| `POST /api/mac/config` | `mac_config_handler(ctx)` | user toggles (enable, expose capture_send, app allowlist, live_thumbnail) — writes `mac-control.json` (600) |
| `POST /api/mac/capture-send` | `mac_capture_send_handler(ctx)` | AC2 — `screencapture -x` → gate (`desktop-capture-send`) → `hermes send … MEDIA:` → record. Returns ticket if `ask`. |
| `POST /api/mac/open` | `mac_open_handler(ctx)` | AC1/AC4 — `open -a "<App>" [url]`, app must be in allowlist; low-risk, recorded; `never` for non-allowlisted |
| `POST /api/mac/wheel` | `mac_wheel_handler(ctx)` | AC9 "take the wheel" — kicks a `computer_use` task via the agent turn; `desktop-control` (floor `ask`) → always ticket; refuses if `take_the_wheel.exposed==false` |
| `GET /api/mac/thumb` | `mac_thumb_handler(ctx)` | opt-in downscaled live frontmost thumbnail for the panel (only when `live_thumbnail.enabled`); served as data-URI, never persisted |
| `POST /api/mac/ticket` | (redeem, via `mac_*` handlers) | `{ticket, approved:true}` → `_mc_redeem` → run; clone of `aux_shortcuts.py:_sb_redeem` |

Registered at the bottom of the file:

```python
register_get("/api/mac", mac_get_handler)
register_get("/api/mac/thumb", mac_thumb_handler)
register_post("/api/mac/config", mac_capture_send_handler and mac_config_handler)  # separate lines
register_post("/api/mac/capture-send", mac_capture_send_handler)
register_post("/api/mac/open", mac_open_handler)
register_post("/api/mac/wheel", mac_wheel_handler)
```

**Gate flow (identical discipline to `aux_shortcuts.py:520–583`):**

1. `if not callable(globals().get("recorder_record_local")): return 503 "refusing to run unrecorded"` (AC10).
2. Gate 1 — **allowlist**: `open` app not in allowlist, or `capture_send` not exposed → `never` → 403 + audit `auto-denied`.
3. Gate 2 — **engine**: `_mc_perm.decide({"pattern_key": pk, "command": cmd})`.
   - `never` → 403, `_mc_audit(... "auto-denied")`, record `blocked`.
   - `auto` → run now (only `desktop-capture-send` can reach here; `desktop-control` floor clamps to `ask`).
   - `ask` → mint single-use ticket, record `pending`, return `{needs_approval:true, ticket, expires_in}`. **Requester cannot approve their own ticket** (message copied from shortcuts bus) — the user confirms in the panel.
4. On run: subprocess with **argv list, `shell=False`** (flag-injection guard: reject app names / paths starting with `-`, mirrors shortcuts bus). Record outcome (`done`/`error`).

**Argument discipline:** app name validated against allowlist (exact match); URL for `open -a Chrome <url>` must be `http(s)://` scheme only; capture path is always a server-chosen scratch file in `~/.hermes/cache/mac-control/` (never a user-supplied path → no arbitrary-file read into Telegram).

---

## 5. Frontend — Agent Desktop surface

New file `dashboard/aux_mac.js`, auto-served at `/aux_mac.js`, chains `window.mindExtras` exactly like `aux_shortcuts.js:19–24`. Renders one card `#mind-extra-mac` into the Mind view. Reuses index.html globals `esc()`, `animate()`, `revealStagger()`, `REDUCE`, 12-hour time helper — all `typeof`-guarded. Zero emoji, bespoke SVG icons, per CLAUDE.md design laws.

**Also register a hub widget** so it appears in the tile grid: `WIDGETS["agent_desktop"] = {"title":"Agent Desktop","icon":"activity","size":"card","cat":"agent","provider": mac_widget_payload}` and `EXPANDERS["agent_desktop"] = expand_agent_desktop` (pattern from `aux_claude_usage.py:432`).

**Panel contents:**
- **Header:** master enable toggle (writes `mac-control.json.enabled`); cua-driver/TCC health chip (green/amber with remediation link to `hermes computer-use doctor`).
- **Now:** frontmost app name (from `GET /api/mac`) + optional live thumbnail (only if `live_thumbnail.enabled`; a privacy toggle sits right next to it).
- **Quick-actions (AC9):**
  - "Take a screenshot & send it to me" → `POST /api/mac/capture-send` → shows the returned ticket → user Approves in-panel → confirms delivery.
  - "Let the agent take the wheel" → `POST /api/mac/wheel` with a one-line task prompt → ticket → approve → live action feed.
- **Pending approvals:** ticket cards with Approve / Deny (Approve posts `{ticket, approved:true}`).
- **Recent desktop actions:** last N recorder rows where `tool ∈ {mac_control, computer_use}`, each with an "irreversible" chip (because `kind="computer"` is never in `UNDO_WHITELIST`) and 12-hour timestamp.

---

## 6. The skill(s) to author

**Primary: `~/.hermes/skills/apple/mac-control/SKILL.md`** — a *recipe hub* that routes the agent to the right substrate and always to the governed path. It does not reimplement screen-oracle or osascript-cookbook; it *cites* them (same "substrate" pattern osascript-cookbook uses).

SKILL.md front-matter + sections:

```markdown
---
name: mac-control
description: "Drive the real Mac for the user: open & operate apps, play media, screenshot→send, read the screen. Routes to the fastest safe substrate (osascript > scriptable app > governed bus > pixel computer_use) and ALWAYS through the recorded, permission-gated path. Never bypasses approval for sends/purchases."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [macOS, desktop-control, screenshot, media, apps, automation, gated]
    category: apple
    related_skills: [screen-oracle, osascript-cookbook, remote-computer-control, imessage, apple-notes]
---
```

**Substrate decision table (the skill's core):**

| Task | Use | Why |
|---|---|---|
| Read/explain what's on screen | **screen-oracle** (never clicks) | pure read path, auto-deletes, redacts credentials |
| Scriptable app (Notes, Reminders, Music, Safari, Finder) | **osascript-cookbook** — one gated `terminal` call | deterministic, no pixel driving |
| Open an app / open a URL | `POST /api/mac/open` (or `open -a`) | recorded, allowlisted |
| Screenshot → send to me | `POST /api/mac/capture-send` | recorded + gated send, locked to user's Telegram |
| Non-scriptable / pixel-only UI (drag a slider, click an unlabeled button) | **computer_use** (remote-computer-control skill) | last resort; hard blocklists apply |

**Concrete, tested recipes (each with the exact command, verified safe-to-run marked ✅):**

1. **✅ Open an app + prove it** — `open -a "TextEdit"` then `osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'` (**ran this session → returned `TextEdit`**).
2. **✅ Screenshot** — `screencapture -x ~/.hermes/cache/mac-control/shot-$(date +%s).png` (**ran → 3456×2234 PNG written**).
3. **✅ Send it** — `hermes send --to telegram "MEDIA:<path>"` (syntax confirmed via `hermes send --help`; not fired in this doc to avoid a real send).
4. **Chrome → YouTube → play** — `open -a "Google Chrome" "https://www.youtube.com/results?search_query=lofi"`, then computer_use to click the first result (destructive → approval), or the `apple/osascript-cookbook/scripts/tabs.jxa` recipe to open the URL directly.
5. **Notes jot** — reuse `osascript-cookbook/scripts/notes_append.applescript` (one gated `terminal` call).
6. **Music.app play** — `osascript -e 'tell application "Music" to play'` (scriptable, no pixels).

**Safety preamble in the skill (mandatory):** "Sends, purchases, and deletes ALWAYS go through the approval gate — never auto. Screenshots may contain secrets: the screen-oracle credential-redaction invariant applies to anything you capture. You cannot type `sudo rm -rf`, `curl|bash`, or press logout/lock — the tool blocks these before approval; do not try to work around it."

---

## 7. Safety model — invariant by invariant (what CANNOT happen, and WHY)

| Invariant | Mechanism | Why it *cannot* be violated |
|---|---|---|
| **Every computer_use action is recorded, irreversible-marked** | `TOOL_KIND["computer_use"]="computer"`, `REVERSIBLE_POLICY["computer"]="no"`; `recorder_record_local` on every bus call; bus refuses to run if recorder is down (`503`, AC10) | Recording is a *precondition* of running (checked before subprocess spawn), not a side effect. `computer` is never in `UNDO_WHITELIST={write,shell}`, so it can't be silently un-done either. |
| **Consequential actions (send/purchase/delete) still gate** | `permissions.decide()` gate 2; `desktop-control` floor `ask` (cannot be `auto`), `desktop-capture-send` can be `auto` only after explicit user promotion; `auto` on a floored class is clamped read-side (`permissions.py` floor clamp) | Even a hand-edited `mac-control.json` or `permissions.json` can't lift `desktop-control` above `ask` — the floor clamps at both write and read time, and a sidecar-hash mismatch suspends all AUTO. |
| **Destructive keystrokes/commands impossible** | `_BLOCKED_KEY_COMBOS` (logout/lock/empty-trash) + `_BLOCKED_TYPE_PATTERNS` (`curl|bash`, `sudo rm -rf`, fork bomb) enforced in `tools/computer_use/tool.py` *before* `_request_approval` | Blocklist is checked at the tool boundary regardless of approval level; approving the action doesn't unlock a blocked combo. |
| **No secret exfiltration via screenshot** | screen-oracle credential-redaction invariant (never transcribe passwords/OTP/cards); capture path is a server-chosen scratch file, auto-purged; send locked to the single Telegram user | The agent can't send a screenshot to an arbitrary recipient (target is fixed to home Telegram channel), and the vision path won't read secret values into text. Gmail send is never used. |
| **Send target locked** | `hermes send --to telegram` with the one configured chat; no user-supplied target in the bus | The bus never accepts a `--to` override from the agent; it's hard-coded to the home Telegram channel (matches the "Telegram locked to the one user" invariant). |
| **Installing the feature changes nothing** | `mac-control.json.enabled` defaults `false`; `SHIPPED_DEFAULT="ask"`; `desktop-control` floor `ask`; nothing auto-runs until the user flips toggles | Same fail-safe posture as `permissions.py` and `aux_shortcuts.py` — merely landing the code grants zero new silent capability. |
| **Requester can't self-approve** | ticket redemption message + panel-only approval (copied from shortcuts bus) | An `ask`-tier action needs a *separate* human confirmation in the dashboard; the agent's own request can't carry `approved:true`. |
| **This path is NOT sandboxed — and that's correct** | Mac-control is *meant* to touch the real desktop; containment is the recorder + gate + blocklist, not sandbox-exec (which is Hands-1's containment for *code writes*) | Sandboxing the desktop would defeat the purpose ("open Chrome" must reach the real Chrome). The compensating controls are the four rows above. |

**Threat cases walked through:**
- *Rogue "open" with a shell payload* → app name validated against allowlist, argv `shell=False`, names starting `-` refused → no injection.
- *"Screenshot and send to attacker@x"* → bus ignores any target; send is hard-locked to home Telegram → impossible.
- *"Type my password into a form"* → `type` is destructive → approval; if it matches a blocked pattern it's refused pre-approval; the agent is told it cannot type secrets (skill preamble + no access to `.env`).
- *"Empty the trash / log me out"* → `_BLOCKED_KEY_COMBOS` → hard refusal.

---

## 8. Edge cases

1. **cua-driver / TCC not granted** → `check_computer_use_requirements` fails; bus returns a remediation message (link to `hermes computer-use doctor`), records `error`, never silent no-op (AC10). Pixel actions unavailable but `open -a` / `screencapture` / osascript still work.
2. **screencapture returns 0 but writes nothing** (TCC-denied) → verify the file exists + is non-empty before sending (screen-oracle's `check_output` already does this; reuse the pattern) — never send a 0-byte "screenshot".
3. **Multi-display** → `screencapture -x` grabs main display; `screencapture -D <n>` for others; expose display index in `capture-send` args (validated integer).
4. **App name collision / not installed** → `open -a` errors; catch, record `error`, tell the user.
5. **Ticket expiry mid-approval** → `_mc_sweep_tickets` on every op; expired token → 410-style "ticket expired, re-request".
6. **Recorder DB locked** → the record call is best-effort with a lock (`_rec_lock`); if the *pre-run* record can't be written, refuse (AC10) rather than run unlogged.
7. **Live thumbnail privacy** → off by default; when on, downscaled + never persisted (data-URI only) + a visible "live" indicator on the panel.
8. **Frontmost app is a password prompt / secure field** → screen-oracle redaction; for pixel driving, the agent is instructed not to type into credential fields (skill preamble).
9. **Aux load order** → `aux_mac.py` only defines new `MC_*`/`mac_*` names and reads `recorder_record_local`/`register_post` from globals at call time (not import time), so any sort position after `aux_recorder.py` is safe.
10. **`datetime` gotcha** → use `time` only in `aux_mac.py` (the exec'd-module private-alias trap; `aux_shortcuts.py` does the same).

---

## 9. Test plan (safe — no real destructive ops, no `--yolo` of hermes)

**Already executed this session (proof):**
- ✅ `open -a "TextEdit"` launched; `osascript … frontmost` returned `TextEdit`.
- ✅ `screencapture -x <scratch>/proof.png` → PNG 3456×2234, 4.0 MB, `file` confirms `PNG image data`.
- ✅ `hermes send --help` confirms `MEDIA:<path>` attachment syntax and `--to telegram` targeting.

**Unit / gate tests (no side effects):**
1. **Gate refuses when recorder down** — stub `recorder_record_local=None`, call `mac_capture_send_handler` → expect `503 "refusing to run unrecorded"`.
2. **Allowlist deny** — `POST /api/mac/open {"app":"Calculator"}` with Calculator not in allowlist → `403 blocked`, recorder row `blocked`, audit `auto-denied`.
3. **Floor clamp** — hand-write `permissions.json {"classes":{"desktop-control":"auto"}}` → `decide({"pattern_key":"desktop-control:x"})` must return `ask` (floor clamp), never `auto`.
4. **Ticket single-use** — mint ticket, redeem twice → second redemption `410/expired`.
5. **Self-approval refused** — a `run` request cannot carry `approved:true`; only `{ticket, approved:true}` from the panel redeems.
6. **Flag-injection guard** — `open` with `app:"-FooBar"` → refused.

**Blocklist tests (prove the "sandbox-of-intent" holds — the Hands-2 analog of Hands-1's sandbox-escape test):**
7. **Blocked type** — call the computer_use `type` action with `"sudo rm -rf /"` → `_is_blocked_type` matches → refused *before* approval. Assert no approval prompt was even reached.
8. **Blocked combo** — `key` action `"cmd+shift+q"` (logout) → `_BLOCKED_KEY_COMBOS` refusal. Assert refusal is independent of `_session_auto_approve`.
9. **Send-target lock** — attempt to pass a `--to` override through `/api/mac/capture-send`; assert the bus ignores it and delivery target remains home Telegram (dry-run: stub the `hermes send` subprocess, assert argv contains only `--to telegram`).

**Integration (safe, opt-in, requires user present):**
10. Flip `enabled` + expose `capture_send` in the panel → click "Take a screenshot & send it to me" → approve the ticket → confirm one PNG arrives in Telegram and one recorder row `done` appears in Agent Desktop. (This is the only test that produces a real send; it's user-initiated and gated.)
11. "What's on my screen?" via screen-oracle → answer returned, scratch capture deleted, no send.

**Explicitly NOT tested destructively:** no real `rm`, no real logout, no purchase click, no `--dangerously-skip-permissions`, no Gmail send.

---

## 10. Sequencing

1. **Permission classes** — add `desktop-control` + `desktop-capture-send` to `permissions.py CLASS_META` + `_class_of`/`_heuristic` (floors set). Ship-inert (`SHIPPED_DEFAULT=ask`). *Test 3 first — the floor is the keystone.*
2. **`aux_mac.py` core** — config load/save, recorder wiring, `GET /api/mac`, `/api/mac/config`. No run paths yet.
3. **`/api/mac/capture-send`** — screencapture → gate → `hermes send MEDIA:` → record. (AC2) Run tests 1,9,10.
4. **`/api/mac/open`** — allowlisted app/URL launch. (AC1/AC4) Tests 2,6.
5. **`aux_mac.js` + `WIDGETS["agent_desktop"]`** — panel, quick-actions, pending approvals, recent actions. (AC9)
6. **`/api/mac/wheel`** — computer_use "take the wheel" ticket path. Tests 4,5,7,8.
7. **`apple/mac-control` SKILL.md** — recipe hub citing screen-oracle + osascript-cookbook. (AC1–AC4)
8. **Live thumbnail** (opt-in, last — privacy-sensitive).
9. Full test-plan pass; hand the diff to the user.

Steps 1–4 deliver AC1/AC2/AC5/AC6/AC7 (the safe, high-value core) before any pixel-driving lands.

---

## 11. Open questions

1. **Send target config** — confirm the home Telegram channel id the bus should hard-lock to (from `~/.hermes/config.yaml` / `.env`); the bus must never accept an agent-supplied target. Is `hermes send --to telegram` (home channel) sufficient, or should the bus pin an explicit `telegram:<chat_id>`?
2. **`desktop-capture-send` auto-promotion** — do we let the user promote screenshot-send to `auto` (frictionless "send me my screen") or keep it `ask` forever like `desktop-control`? Spec allows `auto` (floor `""`); user may prefer floor `ask`.
3. **"Take the wheel" scope** — v1 gates *each* consequential step. Does the user want a time-boxed "autonomous for N minutes on this task" mode later, and if so with what circuit-breaker (step budget? idle timeout? one big approval up front)?
4. **Live thumbnail cadence** — poll interval + whether to blur/redact detected credential fields in the preview.
5. **Overlap with Shortcuts bus** — some tasks (e.g. "play music") can go via a macOS Shortcut (`aux_shortcuts.py`) *or* osascript. Do we prefer Shortcuts when one exists (user-curated, already gated) and fall back to osascript?
6. **Memory ceiling** — pixel computer_use + vision are heavy; should `/api/mac/wheel` check `mlx_admission` and defer when the model is backing off at 50 GB? (Read path via screen-oracle already uses the local vision model.)

---

## Appendix — verification log (this session)

```
$ open -a "TextEdit" ; osascript -e '...frontmost...'   → TextEdit
$ screencapture -x <scratch>/proof.png                  → PNG 3456×2234, 4.0MB
$ hermes send --help                                    → "MEDIA:<path>" attachment, --to telegram
grep: recorder_record_local  → aux_recorder.py:533
grep: register_get/post      → server.py:2107/2111
grep: _AUX_FILES exec loop   → server.py:2135–2144
grep: TOOL_KIND/REVERSIBLE   → computer:"no", UNDO_WHITELIST={write,shell}
grep: CLASS_META/floor/decide→ permissions.py (SHIPPED_DEFAULT=ask)
grep: _BLOCKED_* + _DESTRUCTIVE_ACTIONS → tools/computer_use/tool.py:78–140
template: aux_shortcuts.py (allowlist+decide+ticket+record) → cloned shape
```
