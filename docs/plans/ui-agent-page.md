# UI Restructure — The Agent Page ("converse + watch it work")

Spec for the new flagship **Agent** view: conversation-first, with the agent's
actions rendered as living, inline "show-ins" — plus the always-on autonomous
heartbeat so the page is alive even when you aren't talking to it. The
fullscreen-chat mode, the Console (flight recorder), and the Desktop panel all
consolidate here. Mind's config cards move to Settings (separate spec:
`ui-settings-page.md`).

Design laws apply everywhere: zero emoji (bespoke two-tone SVG only), 12-hour
time, density, Liquid Glass (backdrop blur + hairline + specular + chromatic
rim), Motion One `animate()`/`SPRING`, per-category accent, aux-module pattern
(aux_*.py `register_get/post`, aux JS wraps existing hooks), WKWebView (⌘R).

---

## 1. What consolidates here

| Today | On the Agent page |
|---|---|
| Chat right-column + "full" chat mode (`deck[data-chat]`) | The centerpiece stream. The old full-chat sidebar's Tools quick-actions + conversations list + Local AI panel move to the Agent page's left rail. |
| **Console** tab (`#view-console`, `/api/console` 3s poll) + Flight Recorder card (`aux_recorder.js`) | The **Record** rail tab (right rail) — the "what it did" history — plus the turn scrubber (§7). The Console header tab is REMOVED. |
| **Desktop** tab (`aux_desktop.js`, `/api/desktop/*`) | The **Screen** rail tab (right rail) — watch computer-use live. The Desktop header tab is REMOVED. |
| Model pill agent-state (`setAgentState()`) | Stays in the header, but the Agent page adds the full **status hero** (§3). |
| Inline approval cards (`/api/chat/approve`) | Elevated to the marquee card class (§5.8), unchanged wire protocol. |

Resulting header tabs: **Hub · Agent · Settings** (Mind/Console/Desktop gone).
`setView()` map becomes `{hub:'view-hub', agent:'view-agent', settings:'view-settings'}`
with a localStorage migration: stored `mind`→`settings`, `console`/`desktop`→`agent`.

---

## 2. Layout

```
+------------------------------------------------------------------+
| header (unchanged: mark · greeting · seg[Hub|Agent|Settings] ·    |
|         model pill w/ live state · status pills)                  |
+------------------------------------------------------------------+
| #view-agent                                                       |
| +----------+  +------------------------------+  +--------------+ |
| | LEFT RAIL|  |   CONVERSATION COLUMN        |  | RIGHT RAIL   | |
| | 232px    |  |   flex:1, max 860px, center  |  | 320px        | |
| | sessions |  |  +------------------------+  |  | tabs:        | |
| | ......   |  |  | STATUS HERO (sticky)   |  |  |  Record      | |
| | tools    |  |  +------------------------+  |  |  Screen      | |
| | quick-   |  |  |                        |  |  |  Pulse       | |
| | actions  |  |  |  message stream +      |  |  |              | |
| | ......   |  |  |  tool show-in cards    |  |  | (collapsible |
| | Local AI |  |  |                        |  |  |  to a 44px   | |
| | tok/s    |  |  +------------------------+  |  |  icon strip) | |
| | TTFT mem |  |  | composer + brain badge |  |  |              | |
| +----------+  |  +------------------------+  |  +--------------+ |
|               +------------------------------+                   |
|  (autonomous ticker: full-width hairline strip pinned to bottom) |
+------------------------------------------------------------------+
```

- **Left rail** (glass, 232px; hides <1100px behind a ghost button): the three
  blocks lifted verbatim from `renderChatSide()` — conversations
  (`/api/sessions`), Tools quick-actions, Local AI live panel (tok/s, TTFT,
  memory from `/api/metrics` + `/api/models`). No new code; the render fn is
  re-pointed at `#agent-side`.
- **Conversation column**: the existing chat DOM (`#chat-log`, composer,
  upload) re-parented into `#agent-stream`. Max-width 860px, centered, so tool
  cards get room to breathe. All existing chat JS (jobs, poll loop, approve)
  keeps working — only the container moves.
- **Right rail** (320px, glass): three tabs — **Record** (flight recorder,
  §6.1), **Screen** (desktop panel, §6.2), **Pulse** (autonomous activity
  feed, §6.3). Collapses to a 44px icon strip (three stacked two-tone SVG
  icons w/ unread-count dots); state in localStorage `hermes_agent_rail`.
- **Autonomous ticker**: 28px strip pinned above the composer's outer edge,
  full width of the conversation column (§4.2).

Chat modes: `deck[data-chat]` keeps working on Hub. On the Agent page the
old "full" mode is simply the page itself; the FAB/hide controls are hidden
under `#view-agent`.

---

## 3. Status hero + the two-brain indicator (signature moment)

A slim (56px) sticky glass strip at the top of the conversation column.
Left→right:

1. **The Sigil** — a 36px live agent glyph: the header `.mark` conic-gradient
   ring, but here it's an SVG with an orbiting particle (reuse the `.vk-orbit`
   spin pattern). States:
   - *idle*: slow 18s orbit, 40% opacity.
   - *thinking (local)*: orbit speeds to 3s, iris glow (`--iris` drop-shadow).
   - *thinking (Claude deep)*: **the moment** — the orbit splits into TWO
     counter-rotating particles, the ring's conic gradient warms toward
     Claude's clay/copper accent (`--claude:#D97757` light / `#E8926F` dark —
     new tokens), and a soft radial bloom pulses behind it at 2.4s.
   - *acting*: orbit pauses; the ring "ticks" 12° per tool event (Motion One
     spring rotate).
2. **State line** — 13px, two rows:
   - Row 1 (weight 640): `Thinking` / `Writing` / `Running terminal` /
     `Reading the screen` / `Waiting on you` / `Watching` (idle-autonomous).
   - Row 2 (11px muted): the live verb detail — current tool arg gist, or the
     autonomous heartbeat line (§4.2), or `Local · Qwen3-30B · 41 tok/s`.
3. **Brain badge** — a segmented pill showing which brain is engaged. Data:
   `/api/claude/bridge` (poll 5s while visible; it's a real state) + the chat
   stream's model field.
   - `LOCAL` half: quicksilver dot + `Qwen 30B`, live tok/s ticking.
   - `DEEP` half: clay dot + `Claude`. When the bridge engages, the pill's
     active half slides (Motion One spring, x-translate of an inner thumb),
     the clay half breathes (opacity .7→1, 2s alternate), and label reads
     `thinking with Claude — deep`. Tooltip: why routed (bridge payload
     `reason`), tokens this turn, today's Claude spend (from the Claude Usage
     aux data).
   - This pill is display-only. It is NOT a control (invariant: nothing on
     this page that computer_use could click to change privilege/routing).
4. **Right edge** — turn scrubber toggle (§7) + rail collapse toggle.

Under reduced-motion: all orbits frozen, brain transition is a plain
crossfade.

---

## 4. Alive-when-idle: the autonomous layer

The agent is always-on. Two mechanisms make that visible without stealing
focus. Both source from `/api/recorder` (tool.start/complete stream across
ALL surfaces — dashboard/Telegram/CLI/autonomous) diffed against the last
seen id, plus `/api/watchtower` (schedule/rules → upcoming + last-run) and
`/api/foryou` (proactive findings).

### 4.1 Ephemeral show-in popups
When a recorder event arrives that does NOT belong to the current chat job
(match: recorder action's session/source vs the active `serve_sid`), a compact
popup slides in bottom-right of the conversation column (not OS-level; in-page):

- 300px glass chip: two-tone tool icon + one-line gist
  (`Ran web_search — "openai devday 2026 dates"`) + relative time.
- Enters with Motion One spring (y: 12→0, opacity 0→1), holds 6s, exits
  (opacity→0, y→-6). Max 2 stacked; overflow increments the Pulse tab's
  unread dot instead.
- Click → opens the Record rail focused on that action (detail expanded).
- Approval-needed autonomous events do NOT use this ephemeral form — they
  render a persistent approval card pinned above the composer (§5.8) and
  badge the header. Nothing auto-sends, nothing auto-expires into approval.

### 4.2 The heartbeat ticker
The 28px strip under the stream. A single line that crossfades every 8s
between the most interesting live facts, composed client-side:

- `Watching · next Watchtower sweep 2:30 PM` (from `/api/watchtower`)
- `Scanned 14 feeds this hour · 3 worth your time` (from `/api/foryou` meta)
- `Last action 4m ago — wrote memory: "prefers window seats"` (recorder tail)
- `Local model resident · 18.2 GB · 41 tok/s idle` (`/api/metrics`)

Left edge: a 6px "EKG" dot that blips (scale 1→1.4→1, 300ms) whenever any
recorder event lands — the page's pulse. Ticker text is plain muted 11.5px;
this is ambience, not chrome. Clicking the ticker opens the Pulse rail tab.

---

## 5. The tool show-in cards (taxonomy + exact renders)

Show-ins are rich cards woven INTO the message stream at the position the
tool fired, replacing today's plain "running tool…" status line. Sources:
the chat poll (`/api/chat/poll` tool status events) create the card
immediately in *running* state; the recorder (`/api/recorder`) upgrades it
with result/exit/detail on `tool.complete` (match by tool + start ts + arg
hash). Every card shares an anatomy:

```
.showin                      — glass card, radius-sm, accent left rail 2px
  .showin-head (36px)        — two-tone tool icon · title gist · status chip
                               (running spinner→ok/err) · duration · chevron
  .showin-body               — per-type (below); collapsed by default except
                               terminal-while-running
  .showin-foot (optional)    — reversibility chip (from recorder) · "view in
                               Record" ghost link
```

Enter animation: height 0→auto + opacity via Motion One spring, then the
head icon does one 360° tick. Running state: a 2px accent progress shimmer
sweeps the top hairline (CSS gradient translate, paused under reduced-motion).
Per-type accent (via `data-tool` → `--tac`): terminal=iris, web=quick,
file=ok-green, computer=warm amber, skill=violet `--iris-2`, memory=rose,
delegate=cyan-grey, claude-bridge=clay.

### 5.1 TERMINAL — the marquee show-in
A dark card regardless of theme (terminal is terminal):
`background:#0B0E16; border:1px solid rgba(150,160,220,.18)` + inner specular.
- Head: shell icon (reuse `recGLY.shell`) · the command in SF Mono 12.5px,
  single line, ellipsized · status chip.
- Body (auto-expanded while running): `$ <command>` prompt line, then
  **streaming stdout/stderr** — SF Mono 11.5px, `#C9D2F0` on the dark ground,
  stderr tinted `--bad`, max-height 220px with `overflow-y:auto` pinned to
  bottom while streaming (unpin on user scroll). A blinking block cursor
  (steps() animation) sits at the tail while running.
- Complete: chip becomes `exit 0` (ok) / `exit 1` (bad, card's left rail goes
  red), body collapses to the last 3 lines with a "show all N lines" ghost
  row. Long output never grows the card past 220px — it scrolls inside.
- Foot: reversibility chip from the recorder + copy-command ghost button.
- Data: command + streaming chunks from `/api/chat/poll`; exit code +
  reversibility from the matched recorder action.

### 5.2 WEB_SEARCH
- Head: globe icon (`recGLY.net`) · `Searched — "<query>"`.
- Body: result link chips — up to 5 rows: favicon-less two-tone link glyph,
  title (13px 600), domain (11px muted). Whole row is an `<a>`
  (target=_blank → app opens default browser). If the tool result payload
  lacks structured results, fall back to a mono excerpt block.
- A tiny "n results · 1.2s" foot line.

### 5.3 FILE read / write / patch
- Head: `recGLY.read` / `recGLY.write` icon · the path, mono, middle-
  ellipsized (`~/…/dashboard/server.py`) · verb chip (`read`/`wrote`/`patched`).
- Body (write/patch): a mini-diff — up to 12 lines, mono 11.5px, additions on
  `color-mix(--ok 12%)`, deletions on `color-mix(--bad 10%)`, with a
  `+18 −4` stat in the head. (Read): first-8-lines preview, muted.
- Foot: reversibility (`reversible — checkpointed`) + Undo ghost button that
  proxies the existing recorder undo (`recUndo`) with its confirm() flow.

### 5.4 COMPUTER_USE
- Head: `recGLY.computer` icon · action gist (`Clicked "Submit" in Safari`).
- Body: a 16:10 **screenshot thumbnail** from `/api/desktop/shot?id=` (the
  desktop timeline correlates by ts), radius-xs, hairline border, with the
  click-point marked by an iris ring ping (two expanding circles, 1 loop).
  Click thumbnail → opens the Screen rail tab scrubbed to that moment.
- If multiple actions batch in one tool call, thumbnails become a 3-up
  filmstrip with a count chip.
- **Invariant rendering rule**: computer_use cards NEVER contain approval
  controls, and approval cards render outside any region a computer_use
  session screenshot could be "clicking into" — see §5.8.

### 5.5 SKILL invocation
- Head: a **skill badge** — hexagonal two-tone SVG plaque with the skill's
  category accent + the skill name in 12px 640 (`brief-writer`,
  `market-scan`). Sub-line: category · `from ~/.hermes/skills/…`.
- Body: the skill's one-line description (from `/api/capabilities` skill
  index, cached) + args gist. Skills are the "it learned this" moment — the
  badge gets a one-time specular sweep on enter (a 45° white gradient
  translating across, 700ms).

### 5.6 MEMORY write
- Head: `recGLY.memory` icon · `Remembered`.
- Body: the fact in a quote block (13px, italic serif New York for warmth —
  the one place serif appears) + which store (USER.md / topic file).
- Foot: `edit in Settings → Memory` ghost link (routes to the settings page
  memory editor) + recorder undo if reversible.

### 5.7 DELEGATE / sub-agent
- Head: `recGLY.agent` doubled (a second smaller head offset behind) ·
  `Delegated — <task gist>`.
- Body: a nested mini-stream — the sub-agent's own tool events render as
  14px-indented micro-rows (icon + gist + status dot), max 6 visible,
  "n more in Record" link. The nested region has its own faint left rail.
- Complete: collapses to `finished · 4 actions · 38s`.

### 5.8 APPROVAL — elevated, un-bypassable
The existing inline approval card, promoted:
- Renders in-stream AND pins a duplicate above the composer if scrolled out
  of view (position:sticky sentinel + IntersectionObserver).
- Iris-flooded glass (`--user-bubble` ground), 2px iris border, the requested
  action rendered via the SAME show-in body as its type (a terminal approval
  shows the exact command in the dark mono block) so you approve what you can
  read, verbatim.
- Buttons: `Approve` (primary) / `Deny` (ghost) / `Deny + tell it why`
  (opens a one-line reason input that prefixes the next user message —
  still requires the user to press send; nothing auto-sends).
- **Security invariants** (unchanged wire: POST `/api/chat/approve`):
  - Buttons require a real user gesture; `click()` via JS is ignored — the
    handler checks `event.isTrusted`.
  - While ANY computer_use tool call is in `running` state, all approval
    buttons on the page are disabled with the note `paused — Hermes is
    controlling the screen` (prevents the agent's own cursor from approving
    itself). Re-enabled on tool.complete + 500ms.
  - Approvals never appear in ephemeral popups, never auto-dismiss, never
    have a default-on-Enter binding.
- Timeout display: `waiting 3m` counts up; no auto-deny, no auto-approve.

### 5.9 CLAUDE BRIDGE hand-off (bonus card)
When `/api/claude/bridge` reports a deep-thinking hand-off mid-turn, a slim
clay-accented divider card slots into the stream: a two-tone "bridge" glyph
(two arcs meeting) + `Escalated to Claude — <reason>` + token estimate.
On return: `Back on local · 2,140 tokens · 41s`. These bracket the deep
segment so you can see exactly which part of the answer cost Claude tokens.

---

## 6. Right rail tabs

### 6.1 Record (the flight recorder, folded in)
`aux_recorder.js`'s existing renderer (`renderRecorderRows`, filters, undo,
detail, reversibility chips) is re-mounted into `#agent-rail-record` instead
of the Console view. Additions:
- Grouped by **turn**: recorder actions cluster under a turn header (user
  message gist + time, 12-hour) — the raw flat list remains via the existing
  `all` filter.
- Clicking a row scrolls-and-flashes the matching in-stream show-in card
  (iris outline pulse) if that turn is in the current session; otherwise
  opens the row's detail as today.
- The old 3s `loadConsole` poll wrap continues to drive it; poll only while
  `#view-agent` is visible or the rail has unread.

### 6.2 Screen (the desktop panel, folded in)
`aux_desktop.js`'s panel mounts into `#agent-rail-screen`: live shot
(`/api/desktop/shot`), timeline filmstrip (`/api/desktop/timeline`), manual
capture (`/api/desktop/capture`). Added: when a computer_use show-in card is
running in-stream, the rail auto-switches to Screen (once per turn; user can
switch away and it won't fight). A red `LIVE` recording dot appears on the
rail tab while computer_use runs.

### 6.3 Pulse (autonomous feed)
The full log behind the ticker + popups: a reverse-chron list of
non-chat recorder actions + Watchtower runs + For-You findings, each a
micro-row (icon · gist · relative time). Unread dot count clears on view.
Rows deep-link: recorder rows→Record detail; For-You rows→the Hub widget
pop-out (`openPop('foryou')`).

---

## 7. Turn scrubber — replay what it did

Toggle in the status hero. A horizontal strip (44px) slides down under the
hero: the current (or any selected) turn's actions as evenly-spaced dots on a
time axis — dot glyph = tool icon, dot color = tool accent, width between
dots ∝ real duration. Drag/arrow-key a playhead:
- The stream auto-scrolls to the show-in active at that instant and flashes it.
- Terminal cards re-reveal their output up to that timestamp (we retain the
  chunk timing from the poll stream in-memory per session).
- If the turn had computer_use, the Screen rail scrubs its filmstrip to match.
Playback button replays the turn at 4× (Motion One timeline over the dots).
This is pure client-side theater over data we already hold — no new backend.

---

## 8. Data sources + polling budget

| Feed | Endpoint | Cadence | Drives |
|---|---|---|---|
| Chat turn | POST `/api/chat` → GET `/api/chat/poll?job=` | existing loop | messages, show-in create/stream, approvals |
| Approve | POST `/api/chat/approve` | on gesture | §5.8 |
| Actions history | GET `/api/recorder` | 3s while page visible (reuse existing wrap) | show-in completion, Record rail, popups, ticker EKG |
| Console fallback | GET `/api/console` | (superseded; keep endpoint) | — |
| Brain state | GET `/api/claude/bridge` | 5s while visible; 1s while a turn is running | brain badge, bridge cards, sigil |
| Screen | `/api/desktop/shots·shot·timeline`, POST `/api/desktop/capture` | rail-visible only, 2s while computer_use running | §5.4, §6.2 |
| Local AI panel | `/api/metrics`, `/api/models` | existing 5s | left rail, ticker |
| Watchtower | GET `/api/watchtower` | 60s | ticker |
| Proactive | GET `/api/foryou` | 60s | ticker, Pulse |
| Sessions | `/api/sessions`, `/api/history` | existing | left rail |
| Skills index | `/api/capabilities` | once per page-enter, cached | §5.5 |

All polls gate on `document.visibilityState==='visible'` AND current view
=== agent (except a single lightweight recorder tail poll at 10s that feeds
the header badge + popups from any view). No websockets needed; stay on the
job-poll architecture.

---

## 9. Aux wiring (build plan)

New files (aux-module pattern, no server.py surgery):

**`dashboard/aux_agent.py`** — tiny: `register_get("/api/agent/pulse", …)`
returning a pre-joined feed for the ticker/Pulse tab (recorder tail ∪
watchtower last/next ∪ foryou meta, one call instead of three). Import
datetime ONLY as `import datetime as _agent_datetime` (aux-module gotcha).
Everything else reuses existing endpoints.

**`dashboard/aux_agent.js`** (served `/aux_agent.js`, loaded LAST after
expand.js + aux_recorder.js + aux_desktop.js so it can re-mount their DOM):
1. Inject `#view-agent` markup (rails + stream shell + hero + ticker) after
   `#view-hub`; inject a `<style>` block with the `.showin` system, the dark
   terminal card, sigil animations, rail layout (~all new CSS lives here).
2. Retab: rename/rewire the segmented control to Hub·Agent·Settings; patch
   `setView`'s map (wrap, don't replace: keep old ids resolving for safety);
   migrate localStorage `hermes_view`.
3. Re-parent: move `#chat-log` + composer into `#agent-stream` on
   view-enter (and back to the deck column on view-exit so Hub's split chat
   still works); re-point `renderChatSide()` at `#agent-side`.
4. Wrap the chat poll handler (monkey-patch the existing poll-consumer fn):
   intercept tool status events → create/stream/settle show-in cards via a
   `SHOWIN_RENDER{tool→fn}` map (mirrors the `RENDER{}`/`EXPAND_RENDER{}`
   convention); pass through to the original for plain text/approvals, then
   upgrade approval DOM to §5.8.
5. Wrap `loadRecorder`'s poll: diff for (a) show-in completion matching,
   (b) popups + ticker EKG, (c) Record rail render (call
   `renderRecorderRows` into the rail).
6. Mount desktop panel: call `aux_desktop.js`'s existing init against
   `#agent-rail-screen` (export its mount fn if currently anonymous — the
   one permitted edit inside aux_desktop.js).
7. Sigil + brain badge: 5s/1s bridge poll, `setAgentState` wrap so the
   header pill and the sigil never disagree.
8. Scrubber: per-turn in-memory event log (`window.agentTurnLog`) captured
   in step 4.
9. Trusted-gesture approval guard + computer_use-running lockout (§5.8).

**Removals** (after Agent + Settings both land): Console/Desktop/Mind tab
buttons hidden (DOM kept one release for rollback), their `setView` ids
alias to the new pages.

Icon rule: every new glyph is bespoke two-tone SVG (accent fill @ .14–.16 +
currentColor stroke), added alongside `recGLY` style; NO emoji anywhere.
All times 12-hour via the existing `recClock` pattern. Verify renderers
headless (node + stubbed helpers + live JSON) as done for expand.js.

---

## 10. States checklist

- **Idle-autonomous** (no chat running): sigil slow-orbit, ticker cycling,
  popups on background actions, composer placeholder `Ask, or just watch —
  Hermes is on watch.`
- **Thinking local / deep**: §3 sigil + brain badge states.
- **Tool running**: show-in in running shimmer; header pill mirrors.
- **Approval pending**: pinned card, header badge, sigil holds amber ring.
- **Agent paused** (`/api/models .paused`): hero swaps to a flat `Paused`
  strip with the existing resume affordance; ticker shows `paused — model
  server offline`; popups suppressed.
- **Serve down** (chat falls back to `-z`): banner chip in hero
  `one-shot mode — approvals unavailable`, approval-needing tools will fail
  closed (existing behavior), show-ins still render from recorder.
- **Empty session**: a centered welcome block with three suggested asks +
  the live ticker still running underneath (never a dead page).
- **Reduced motion**: orbits frozen, shimmers off, popups fade only.

## 11. Phasing

1. **P-A1**: `#view-agent` shell + retab + chat re-parent + left rail. (Hub
   split-chat untouched.)
2. **P-A2**: show-in system — terminal card first (marquee), then web/file/
   skill/memory; recorder-completion matching.
3. **P-A3**: rails (Record re-mount, Screen re-mount, Pulse) + popups +
   ticker + `/api/agent/pulse`.
4. **P-A4**: sigil + brain badge + bridge cards; approval elevation + the
   computer_use lockout (verify with a real dangerous-command approval —
   still an open item in CLAUDE.md).
5. **P-A5**: scrubber + delegate nesting + polish (specular sweeps, filmstrip).

Each phase ends with ⌘R in the app + a headless render check.
