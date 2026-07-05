# Menu-bar Quick-Ask — design spec (P2)

*Phase 2 · Workstream P2.4 "Menu-bar quick-ask" (DEVPLAN §Phase 2, table row #7).
Grounded in the code as of 2026-07-05: `app/main.swift`, `dashboard/server.py`,
`dashboard/hermes_rpc.py`, `dashboard/index.html`.*

**One-line intent:** an `NSStatusItem` in the macOS menu bar whose popover hosts a
tiny WKWebView chat that reuses the dashboard's existing job-based chat API
(`/api/chat` → poll → approve) for a fast, focused ask — without opening the
1440×900 main window. Read-only by posture: approvals are *handed to* the main
window, never actioned from the popover.

**Guiding constraint met:** zero `server.py` edits, zero new backend endpoints,
zero new background threads. The whole feature = Swift additions in
`app/main.swift` + one new static file `dashboard/aux_quickask.js` (auto-served)
+ exactly one `<script>` tag in `index.html` (orchestrator-applied). It reuses
the three chat endpoints verbatim, so graduated permissions (`permissions.decide`
inside `hermes_rpc.run_turn`) and the approval gate apply identically and for
free.

---

## Goal & acceptance criteria

Done means all of these are demonstrable on the rebuilt app:

1. **Status item present & theme-correct.** A bespoke monochrome spark glyph
   (`isTemplate = true`, no emoji — design law) appears in the menu bar and
   adapts to light/dark menu bars. Left-click toggles the popover open/closed.
2. **Global hotkey, no Accessibility TCC.** `⌃⌥Space` (Control-Option-Space,
   configurable) toggles the same popover from any frontmost app. It uses Carbon
   `RegisterEventHotKey` (system-wide hotkeys do **not** require the
   Accessibility grant that `NSEvent.addGlobalMonitorForEvents` would — this
   matters because the app must work without extra TCC prompts). On open the
   text field is first responder so typing lands in the input immediately.
3. **Ask streams in-place.** Typing a question + Enter POSTs to
   `POST /api/chat` with `session:"menubar"`, then polls `GET /api/chat/poll` and
   renders the streaming markdown reply with a caret; first visible token in
   < 2 s perceived on the current (Qwen3-30B) path. Follow-up asks in the same
   popover keep conversational context (the reserved `menubar` serve session is
   reused across turns).
4. **Approvals hand out, never in.** When a turn emits `state:"approval"`, the
   popover shows a *non-actionable* "Needs your approval — Approve in the main
   window" card (never Approve/Deny buttons). Clicking it activates the app,
   brings the main window forward, and resumes **the same job id** there via
   `window.hermesQuickAskResume(job)`, where the existing `.approval` renderer
   (index.html `streamJob`) provides working Approve/Deny that completes the turn.
5. **Transient & dismissable.** Clicking outside the popover or pressing Esc
   closes it (`NSPopover.behavior = .transient`). Reopening resumes the same
   `menubar` conversation view.
6. **Links are safe.** A hyperlink in an answer opens in the default browser via
   the app's existing `decidePolicyFor` / `createWebViewWith` navigation policy
   (reused on the popover's WKWebView) — it never navigates inside the popover.
7. **Launch at login.** An "Open at Login" toggle (status-item menu) registers/
   unregisters via `SMAppService.mainApp`; after enabling and rebooting, the
   status item is present without the main window opening.
8. **No policy bypass.** Because the turn flows through `hermes_rpc.run_turn`, a
   quick-ask that hits a class the user set to `auto` is auto-approved by
   `permissions.decide` exactly as in the main window, and a floored class
   (e.g. `destructive-delete`) still surfaces the approval card (never runs
   silently). Verifiable via `/api/permissions/test` + a staged pattern.

---

## Data model (files / JSON — exact shapes)

**No new server-side files or tables.** Quick-ask reuses the existing transcript
store under the reserved session id `menubar`.

- **Reserved session id:** the string constant `"menubar"`. It satisfies
  `server.py:SESSION_RE = ^[A-Za-z0-9._-]{1,80}$`, so `/api/chat`,
  `/api/history`, and `/api/sessions/delete` all accept it unchanged.
- **Transcript file:** `~/.hermes/dashboard/chats/menubar.json` — created lazily
  by `save_chat("menubar", …)`. Exact shape produced/consumed by
  `server.py:load_chat`/`save_chat` + `hermes_rpc.run_turn`:
  ```json
  {
    "title": "what's the weather",
    "messages": [
      {"role": "user", "text": "what's the weather", "ts": 1751760000.12},
      {"role": "bot",  "text": "…markdown…",          "ts": 1751760003.44, "err": false}
    ],
    "serve_sid": "sess_ephemeral_id",
    "serve_key": "stored_durable_session_id"
  }
  ```
  `serve_sid`/`serve_key` are written by `run_turn`'s `save_meta()` and are what
  give follow-up asks their multi-turn memory. Because it is a normal session,
  the `menubar` conversation also shows up in the main window's session list
  (`/api/sessions`) — desirable: "Open in main window" deep-links straight to it.

- **In-flight job** (`server.py:CHAT_JOBS`, in-memory, ephemeral): the popover
  holds only the returned `job` id string. Poll-response shape it renders
  (from `server.py` do_GET `/api/chat/poll`):
  ```json
  {"ok": true, "state": "running|approval|done", "text": "partial…",
   "status": "using terminal", "approval": {…}|null, "done": false,
   "reply": "final text", "err": false}
  ```
  `approval` payload fields the card reads (from `run_turn`): `command` /
  `summary` / `tool` / `name`, plus `_policy:{tier,class,reason}` from
  `permissions.decide`.

- **Client prefs (Swift, `UserDefaults` suite `local.hermes.assistant`):**
  | key | type | default | meaning |
  |-----|------|---------|---------|
  | `quickask.hotkey.keyCode` | Int (Carbon virtual keycode) | `49` (Space) | global hotkey key |
  | `quickask.hotkey.modifiers` | Int (Carbon modifier mask) | `controlKey \| optionKey` | global hotkey mods |
  Launch-at-login state is owned by `SMAppService` (not stored here). No secrets,
  no PII — upholds the "never handle user secrets" invariant.

---

## Backend

**No new backend code. No new `aux_*.py`. No new thread.** Quick-ask consumes
three endpoints that already exist and are already permission-aware. Listed here
as the integration contract:

### `POST /api/chat` (server.py do_POST, ~line 2394)
- **Request:** `{"message": "<text>", "session": "menubar"}` (`attachments` omitted;
  the popover has no upload surface).
- **Response 200:** `{"ok": true, "job": "<12-hex>"}` → start polling.
- **Paused fast-path:** if `agent_paused()`, returns
  `{"ok": false, "reply": "The agent is paused …"}` with **no `job`** — popover
  shows that reply verbatim and a "Resume from the model menu" hint.
- **400:** empty message or session failing `SESSION_RE` (won't happen for a
  fixed `"menubar"`, but the popover guards empty input client-side anyway).
- **Effect:** appends the user turn to `menubar.json`, spawns `_chat_worker` →
  `hermes_rpc.run_turn` on a daemon thread.

### `GET /api/chat/poll?job=<id>` (server.py do_GET, ~line 2146)
- **Response 200:** the poll-response shape above. **404** `{"ok":false,"gone":true}`
  if the job aged out (dashboard restarted) → popover shows "job was lost, re-ask".
- Popover polls every ~600 ms until `done:true` (or `approval`).

### `POST /api/chat/approve` (server.py do_POST, ~line 2427)
- **The popover NEVER calls this.** It is listed only because the *main window*
  calls it after hand-off. Request `{"job":"<id>","choice":"approve|deny"}`;
  sets `job["pending_choice"]`, which `run_turn` drains and forwards as
  `approval.respond {choice:"approve|deny"}`.

### `GET /api/health` (server.py do_GET, ~line 2142)
- `{"model_online": bool, "hermes_found": bool, "hermes_path": "…"}` — the popover
  pings this on open to render a "model starting…" state instead of a dead input.

### How it respects `permissions.py` (unchanged, inherited)
The popover turn runs the **identical** `run_turn` path as the hub chat. On every
`approval.request` event, `run_turn` calls `permissions.decide(payload)` (dashboard/
hermes_rpc.py ~line 284):
- `tier == "auto"` → responds `choice:"once"` upstream and continues (audited
  `auto-approved`). This is the *user's* pre-set policy, not a popover decision.
- `tier == "never"` → responds `choice:"deny"` (audited `auto-denied`).
- otherwise → sets `job["approval"]`, `job["state"]="approval"` → the popover
  surfaces the hand-off card. Floors in `CLASS_META` (e.g. `destructive-delete`,
  `credential-write`) are clamped to ask by `decide`, so no dangerous class can
  be auto-run from the menu bar.

**Static serving already handles the new JS.** `server.py` do_GET (~lines
2126–2141) already serves any `/aux_*.js` from `HERE` with
`Content-Type: application/javascript`. Dropping `dashboard/aux_quickask.js`
makes it live at `http://127.0.0.1:7788/aux_quickask.js` with **no route edit**.

---

## Frontend

Two documents, one JS file, one script tag.

### 1. Popover shell (embedded in `app/main.swift`, loaded via `loadHTMLString`)
A ~20-line bootstrap HTML string, loaded with `baseURL = DASH_URL`
(`http://127.0.0.1:7788/`) so the document's **origin is the dashboard** — every
`fetch('/api/chat')`, `/aux_quickask.js`, and `/motion.min.js` is same-origin
(and `NSAllowsLocalNetworking` is already true in Info.plist). Skeleton:

```html
<!doctype html><meta charset="utf-8">
<script>window.__HERMES_QUICKASK__=1;</script>
<div id="qa">starting your local assistant…</div>
<script src="/motion.min.js"></script>
<script src="/aux_quickask.js"></script>
```

If the dashboard is still coming up, the fetch of `/aux_quickask.js` fails and
the user sees the "starting…" placeholder; the app already `kickstart`s the
services on launch (`ensureServices`), and the popover retries on next open.

### 2. `dashboard/aux_quickask.js` (NEW — the whole UX + the main-window shim)
Context-detects which document it is in:

```js
if (window.__HERMES_QUICKASK__) buildPopover();      // popover document
else window.hermesQuickAskResume = resumeInMain;     // main-window shim
```

**`buildPopover()`** paints a compact Liquid-Glass card into `#qa`:
- **Header:** bespoke inline-SVG spark mark + "Quick Ask" + a hairline; a right-
  aligned "Open in main window" text-button (bridge `openMain`).
- **Transcript strip:** renders the `menubar` turns (fetched once from
  `/api/history?session=menubar`) as compact bubbles; a compact bespoke markdown
  renderer `qaMd(src)` — **escape-first, then format** (mirrors CLAUDE.md's
  `esc`-before-format rule that has bitten the expanders): HTML-escape, then a
  short regex pass for `**bold**`, `` `code` ``, `- list`, links, paragraphs.
  (The popover is a separate document and cannot reach index.html's `renderMd`,
  so it carries its own ~30-line renderer.)
- **Composer:** a single-line-growing `<textarea>` with placeholder
  "Ask Hermes…", Enter = send, Shift-Enter = newline; a send button (bespoke
  arrow SVG). A footer hint shows the active hotkey (fetched from Swift via a
  bridge `getHotkey`, or a static default label).
- **States:**
  | state | UI |
  |-------|-----|
  | `idle` | focused input, hint line |
  | `starting` | `/api/health` says `model_online:false` → "Model is starting…", send disabled, re-poll health every 2 s |
  | `paused` | `/api/chat` returned `{ok:false}` with no job → show its `reply` + "Resume in the model menu" |
  | `thinking` | dots + `status` ("using terminal") |
  | `writing` | streaming markdown + `▌` caret |
  | `approval` | amber card: tool/command (`approval.command\|summary\|tool`) + one button "Approve in the main window" → bridge `openApproval(job)`; then popover shows "Handed to the main window." |
  | `done` | rendered answer; input re-enabled for a follow-up; "Open in main window" persists |
  | `gone`/`error` | friendly one-liner + retry |
- **Send flow:** `POST /api/chat {message, session:"menubar"}` → if `job`, run a
  poll loop mirroring index.html `streamJob` but **omitting** the Approve/Deny
  branch (on `state==='approval'` it renders the hand-off card and stops polling).
- **Animations (Motion One `animate()`):** popover content fade/translateY-in on
  open; the answer bubble fades in; the caret pulse. Respect
  `prefers-reduced-motion`.
- **Bridge calls (to Swift):**
  `window.webkit.messageHandlers.hermes.postMessage({action, ...})`:
  - `{action:"openMain", session:"menubar"}` — activate app + focus main window +
    load the `menubar` session.
  - `{action:"openApproval", job:"<id>"}` — same, then resume that job's approval
    in the main window.
  - `{action:"close"}` — dismiss the popover.
  - `{action:"resize", h:<px>}` — (optional) grow the popover to fit content.

**`resumeInMain(job)`** (main-window context, attached to `window`): reuses the
already-verified index.html globals — no re-implementation:
```js
window.hermesQuickAskResume = async function(job){
  session = 'menubar';                              // let session (index.html:1887)
  localStorage.setItem('hermes_session','menubar');
  setChatMode('full');                             // index.html:1014 — open chat
  await loadHistory();                             // index.html:1933
  const thinking = addBubble('', 'bot', false);    // index.html:1917
  streamJob(job, thinking);                        // index.html:1989 — resumes THIS job,
};                                                 //   incl. its Approve/Deny card
```
`streamJob(job, …)` polls `/api/chat/poll?job=<job>` for the *same server-side
job* (which is still alive in `CHAT_JOBS`, blocked on `pending_choice`), renders
the existing `.approval` card, and its Approve button POSTs `/api/chat/approve`
— the turn continues. (Top-level `function` declarations and the top-level `let
session` share one global lexical scope across classic scripts, so the later-
loaded aux file both calls and reassigns them — verified against index.html.)

### 3. The ONE `index.html` edit (orchestrator-applied)
A single tag immediately before `</body>`:
```html
<script src="/aux_quickask.js"></script>
```
In the main window `__HERMES_QUICKASK__` is undefined, so the file only defines
`window.hermesQuickAskResume` and adds nothing visible.

---

## Integration points (verified names)

`app/main.swift` — additions to `final class AppDelegate` (all reuse existing
plumbing):
- **New stored props:** `var statusItem: NSStatusItem!`, `var popover: NSPopover!`,
  `var quickWebView: WKWebView!`, `var hotKeyRef: EventHotKeyRef?`,
  `var quickLoaded = false`.
- **`applicationDidFinishLaunching`** (line 20): call new `installStatusItem()`,
  `installPopover()`, `installHotKey()` after `buildMenu()`.
- **`installStatusItem()`:** `NSStatusBar.system.statusItem(withLength:
  .variableLength)`; `button.image = sparkTemplateImage()` (draw the same star
  path used by `render-icon.swift`, `isTemplate = true`); `button.action =
  #selector(toggleQuickAsk)`, `button.target = self`. Add a right-click menu
  (Open Quick Ask / Open Main Window / Open at Login toggle / Quit).
- **`installPopover()`:** `NSPopover()`, `behavior = .transient`,
  `contentSize = NSSize(380, 460)`; `contentViewController` = a small
  `NSViewController` whose `view` is `quickWebView`. Build `quickWebView` with a
  `WKWebViewConfiguration` whose `userContentController.add(self, name: "hermes")`.
  **Reuse:** `quickWebView.uiDelegate = self` (the existing
  `runJavaScriptAlert/Confirm/TextInputPanel` at lines 75–101 sheet on `window`),
  `quickWebView.navigationDelegate = self` (existing `decidePolicyFor`
  lines 103–113 + `createWebViewWith` lines 66–70 send external links to the
  browser). Load lazily on first show via `loadHTMLString(BOOTSTRAP, baseURL:
  DASH_URL)` (DASH_URL exists, line 10).
- **`@objc func toggleQuickAsk()`:** if `popover.isShown` → `performClose`; else
  `NSApp.activate(ignoringOtherApps: true)`, load if needed, `popover.show(
  relativeTo: button.bounds, of: button, preferredEdge: .minY)`, then
  `popover.contentViewController?.view.window?.makeKey()` and
  `quickWebView.evaluateJavaScript("window.__qaFocus&&__qaFocus()")` to focus the
  input.
- **`installHotKey()` (Carbon):** `RegisterEventHotKey(keyCode, modifiers,
  hotKeyID, GetEventDispatcherTarget(), 0, &hotKeyRef)` +
  `InstallEventHandler` for `kEventClassKeyboard/kEventHotKeyPressed` → dispatch
  to `toggleQuickAsk` on the main queue. No Accessibility permission required.
- **`WKScriptMessageHandler` conformance** (`userContentController(_:didReceive:)`):
  switch on `body["action"]`:
  - `openMain`/`openApproval` → `NSApp.activate`; `window.makeKeyAndOrderFront`;
    if not `loaded`, `webView.load(DASH_URL)` then retry; for `openApproval`,
    `webView.evaluateJavaScript("window.hermesQuickAskResume&&
    hermesQuickAskResume('\(job)')")`; `popover.performClose(nil)`.
  - `close` → `popover.performClose(nil)`.
- **`SMAppService`** (import `ServiceManagement`): `@objc func toggleLoginItem()`
  → `try? SMAppService.mainApp.register()` / `.unregister()`; menu item checkmark
  reflects `SMAppService.mainApp.status == .enabled`.
- **`buildMenu()`** (line 188): add a "Quick Ask" item under Window/View with the
  hotkey as its `keyEquivalent` label (display only; the real global hook is
  Carbon).

`app/build-app.sh` — add `-framework Carbon -framework ServiceManagement` to the
`swiftc` line. Info.plist: `NSAllowsLocalNetworking` already present; no ATS
change. (Keep `.regular` activation so the dock icon + main window are unchanged;
the status item coexists.)

`dashboard/server.py` — **untouched.** Reused, by name: `_new_job`,
`_chat_worker`, `chat_path`/`load_chat`/`save_chat`, `agent_paused`,
`model_online`, the `/aux_*.js` static branch (lines 2126–2141), and the three
chat routes.

`dashboard/hermes_rpc.py` — **untouched.** Reused: `run_turn` (line 196) and its
`permissions.decide` seam (line 284).

`dashboard/index.html` — reused globals: `renderMd` (959), `setView` (996),
`setChatMode` (1014), `setAgentState` (1666), `let session` (1887), `addBubble`
(1917), `loadHistory` (1933), `streamJob` (1989) and its inline `.approval`
renderer (2003–2018). Plus the one `<script>` tag.

`dashboard/aux_quickask.js` — new file (popover UI + `hermesQuickAskResume`).
`dashboard/motion.min.js` — reused (served, `animate()`).

---

## Edge cases & failure modes

- **Dashboard down when popover opens** → `/aux_quickask.js` / `/api/health`
  fetch fails → "starting…" placeholder; `ensureServices()` already kickstarts on
  launch; retry on next open. Popover never shows a dead input as if ready.
- **Model offline / loading** → `/api/health` `model_online:false` → "Model is
  starting…" state, send disabled, health re-polled every 2 s.
- **Agent paused** → `/api/chat` returns `{ok:false}` no-job reply; popover shows
  it + resume hint; no phantom poll loop.
- **Job aged out mid-poll** (`/api/chat/poll` → `{gone:true}`, 404) → "job was
  lost (dashboard restarted?) — ask again."
- **Turn needs approval, user ignores it** → `run_turn` deadline is
  `TURN_TIMEOUT = 600 s` from submit (hermes_rpc.py:26). Hand-off has ≤10 min; if
  it lapses, the job returns a timeout reply and the ask is lost — the user
  re-asks in the main window. Popover surfaces the timeout text, not a hang.
- **Approval hand-off before main window has loaded** (app launched straight into
  the popover) → the message handler loads `DASH_URL` first and retries the
  `evaluateJavaScript` until `hermesQuickAskResume` is defined (bounded retry,
  then a fallback: just open the `menubar` session so the user can re-ask).
- **Long / multi-tool turns** → same streaming/status UI as the hub; the popover
  is transient, so if the user clicks away the turn keeps running server-side and
  the answer is waiting (in `menubar.json` + still-live job) on reopen.
- **Popover closes mid-stream** → poll loop is cancelled on `viewWillDisappear`;
  the server job is untouched. Reopen re-attaches by reloading `menubar` history
  (finished turns) — a still-running job is picked up only if the popover kept
  the job id; otherwise the finished reply lands in history.
- **Hotkey conflict** with another launcher → default `⌃⌥Space` is rarely bound;
  if `RegisterEventHotKey` returns non-`noErr`, log + skip (the status-item click
  still works). Configurable via `UserDefaults`.
- **Rapid re-triggers of the hotkey** → `toggleQuickAsk` is idempotent (show/
  close), guarded on `popover.isShown`.
- **Markdown injection** in a model reply → `qaMd` escapes HTML *before*
  formatting; links render as text-safe anchors that go through the browser via
  `decidePolicyFor` (never in-popover).
- **Very tall answer** → transcript strip scrolls inside the fixed 460 pt
  popover (`overflow:auto`); optional `resize` bridge grows the popover up to a
  cap (e.g. 620 pt) then scrolls.
- **Two surfaces on the `menubar` session at once** (popover + main window after
  hand-off) → both poll the same job (poll is a stateless read); only the main
  window shows Approve/Deny; harmless.

---

## Security & safety (invariant-by-invariant)

- **Proactive = NOTIFY-ONLY (never auto-act):** *not crossed.* A quick-ask is a
  **user-initiated** turn, not a Watchtower trigger. The popover has **no**
  Approve/Deny control at all — it is structurally read-only; any tool needing
  approval is either resolved by the user's *pre-set* `permissions.json` policy
  (`decide` → `auto`/`never`) inside `run_turn`, or handed to the main window for
  an explicit human click. The menu bar can never be the thing that approves.
- **Approvals stay `manual`:** unchanged. `run_turn` only ever sends
  `once`/`deny` (never `session`/`always`), so hermes's own allowlists never grow
  and `~/.hermes/permissions.json` stays the single source of graduated trust.
- **Graduated tiers upheld:** the popover adds no new decision point; floors in
  `CLASS_META` (credential-write / disk-device / privilege-escalation, and the
  ask-floored delete/exec/system classes) still clamp to ask.
- **Gmail read+draft only / Telegram locked / no SMTP:** untouched — quick-ask
  wires no new tool or transport; it only carries text to the same agent.
- **Local-first:** the popover talks solely to `127.0.0.1:7788` (same as the main
  window). `decidePolicyFor` sends any non-loopback navigation to the system
  browser. No new network egress.
- **No secrets handled:** prefs are a hotkey code + login-item flag; no tokens,
  no PII stored.
- **No new attack surface:** the dashboard already trusts any loopback client
  (no auth on `/api/*`); the popover is one more loopback client owned by the
  user's signed app. It widens nothing. (If loopback auth is ever added in a
  later phase, the popover inherits it since it shares the dashboard origin.)
- **What it refuses:** to Approve/Deny from the menu bar; to run tools silently
  outside policy; to open non-loopback URLs in-popover; to send when the agent is
  paused or the model is offline.

---

## Test plan (no user spam, no `--yolo`)

All against a running dashboard; none require the real user or dangerous acts.

1. **Static file serves:**
   `curl -s -o /dev/null -w '%{http_code} %{content_type}\n' localhost:7788/aux_quickask.js`
   → `200 application/javascript`.
2. **Round-trip a benign ask through the exact popover path** (proves the reused
   endpoints + `menubar` session, no UI needed):
   ```bash
   JOB=$(curl -s localhost:7788/api/chat -H 'Content-Type: application/json' \
     -d '{"message":"in one sentence, what is Hermes?","session":"menubar"}' \
     | python3 -c 'import sys,json;print(json.load(sys.stdin)["job"])')
   for i in $(seq 1 40); do
     curl -s "localhost:7788/api/chat/poll?job=$JOB" \
       | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["state"],repr(d["text"][:60]),d["done"])'
     sleep 1; done
   ```
   Expect `running…` with growing text, then `done True`. Confirm the transcript:
   `python3 -c 'import json;print(len(json.load(open("'"$HOME"'/.hermes/dashboard/chats/menubar.json"))["messages"]))'`
   → ≥ 2, and `serve_key` present (multi-turn memory).
3. **Multi-turn memory:** send a second `/api/chat` with a referential follow-up
   ("and who made it?"); poll; confirm the reply resolves the pronoun (same
   `serve_sid`).
4. **Approval surfaces, popover-safe (no real danger):** stage a class to *ask*
   and dry-run the decision without executing anything —
   `curl -s 'localhost:7788/api/permissions/test?pattern_key=git%20reset%20--hard%20(destroys%20uncommitted%20changes)'`
   → verdict `tier` is `ask` (floored) → in the live turn the popover would show
   the hand-off card, not buttons. (The genuine end-to-end approve/deny is P1.4's
   drill via the hub chat surface; quick-ask reuses that verified machinery.)
5. **Auto-approve is policy-driven, not popover-driven:** with a class the user
   set to `auto`, `/api/permissions/test` shows `tier:auto`; confirm `run_turn`
   would answer `once` — i.e. the menu bar never decides.
6. **Paused fast-path:** `POST /api/agent/pause`, then `/api/chat` with
   `session:"menubar"` → response has `ok:false` and **no** `job`; popover shows
   the paused reply. `POST /api/agent/resume` after.
7. **Swift build:** `app/build-app.sh` compiles clean with the added
   `-framework Carbon -framework ServiceManagement`; launch the rebuilt app →
   status item visible; `⌃⌥Space` opens the popover from a different frontmost
   app (no Accessibility prompt appears); type + Enter streams an answer;
   click a link → opens in the browser; Esc closes it.
8. **Login item:** toggle "Open at Login" → `SMAppService.mainApp.status ==
   .enabled`; reboot (or `sfltool resetbtm` sim) → status item present, main
   window not opened.

Verification harness note: exercise steps 1–6 headlessly (curl) so the model is
prompted with only benign strings — no messages sent to the user, no destructive
actions, no `--yolo`.

---

## Effort & sequencing + dependencies + open questions

**Effort: S/M (~1–1.5 days).** Split: `aux_quickask.js` popover UI + `qaMd`
(~½ day, the bulk), Swift status-item/popover/Carbon-hotkey/SMAppService (~½
day), hand-off wiring + build/manual test (~¼ day).

**Sequencing:**
1. `dashboard/aux_quickask.js` (popover UI + shim) — testable in a browser tab by
   setting `window.__HERMES_QUICKASK__=1` first; no app rebuild needed to iterate.
2. Add the one `index.html` `<script>` tag; verify `hermesQuickAskResume` resumes
   a hand-crafted job id in the main window.
3. `app/main.swift` status item + popover + `loadHTMLString(baseURL:DASH_URL)`.
4. Carbon hotkey + `SMAppService` toggle.
5. Message-handler hand-off; build; manual acceptance run.

**Dependencies (all satisfied today):** the three chat endpoints + `run_turn`
(shipped), `permissions.decide` (P1.3 shipped), `/aux_*.js` static serving
(shipped), `motion.min.js` (shipped), the WKWebView dialog/nav delegates in
`main.swift` (shipped), macOS 13+ for `SMAppService.mainApp` (Info.plist
`LSMinimumSystemVersion 13.0`). None blocked on the user.

**Open questions:**
- **Default hotkey** — `⌃⌥Space` proposed (rarely bound; avoids Spotlight ⌘Space
  and Alfred ⌥Space). Confirm, or expose a picker in a later pass.
- **Fresh vs. continued `menubar` session** — v1 keeps one rolling `menubar`
  session (best for quick follow-ups). Add a "New" affordance (POST
  `/api/sessions/delete {session:"menubar"}`) if context bleed becomes annoying.
- **`loadHTMLString(baseURL:http…)` same-origin fetch** — the trodden path; if any
  WebKit CSP/origin quirk appears, fallback is a 6-line `server.py` special-case
  serving a static `quickask.html` at `/quickask` (kept out of v1 to honor the
  no-`server.py`-edit constraint).
- **Popover auto-resize** — ship fixed 380×460 with internal scroll; the `resize`
  bridge is a stretch nicety, not v1-blocking.
- **Clipboard actions (P2 row #8)** — deliberately out of scope here; the popover
  is the natural future host (a "act on clipboard" chip), but that is its own
  workstream.
