# UI RESTRUCTURE — Master Plan (synthesis, build-ready)

One plan unifying the three design outputs:
- `plans/ui-agent-page.md` (Agent page spec — layout, show-ins, sigil, rails, phasing)
- `plans/ui-settings-page.md` (Settings spec — shell, relocator, 12 panels, phasing)
- `plans/ui-agent-patterns-research.md` (pattern research — 30+ sources, anti-patterns)

Design laws bind everything below: zero emoji (bespoke two-tone SVG, shared stroke
grammar), 12-hour time, density with progressive disclosure, Liquid Glass
(backdrop blur + hairlines + specular), Motion One `animate()`/`SPRING` only (no
ad-hoc CSS transitions), per-category accent, aux-module pattern, WKWebView (⌘R).

Invariants (non-negotiable in every section): approval gate visible and
un-bypassable — no approvable control anywhere the agent's computer-use surface
can click; local-first; nothing auto-sends, auto-approves, or auto-expires into
approval.

### Conflict-resolution ledger (which spec wins where)

| Conflict | Resolution |
|---|---|
| Settings spec kept 5 tabs (Hub·Agent·Console·Desktop·Settings); Agent spec folds Console+Desktop into the Agent right rail (3 tabs) | **Agent spec wins**: final tabs Hub · Agent · Settings. Settings spec was drafted before the fold-in decision. Transition safety below. |
| Routing keys: Agent spec renames setView map; Settings spec says "rename, don't re-plumb" (keep `mind`/`view-mind`) | **Settings spec wins on plumbing**: internal ids `tab-mind`/`view-mind` and key `mind` survive; aux_agent.js *wraps* `setView` adding `agent` + aliases (`settings`→`mind`, `console`/`desktop`→`agent`) + localStorage `hermes_view` migration. Nothing re-plumbed. |
| "Console stays the live activity surface" (settings/research) vs Console tab removed (agent) | Reconciled: the **Record rail IS the Console** — `aux_recorder.js`'s renderer re-mounted, same 3s poll, plus turn grouping. Endpoint `/api/console` kept; tab hidden only after rail parity is verified. |
| Card system naming: `SHOWIN_RENDER` map (agent) vs `registerBlock` registry (research) | One registry: **`SHOWIN_RENDER{tool→fn}`**, mirroring the existing `RENDER{}`/`EXPAND_RENDER{}` convention, adopting the research grammar: collapsed-by-default one-liners, click-to-expand per row, consecutive-call grouping ("N steps" cluster), density toggle in the page header. |
| Where analytics live | Both agree: **Insights stays in Settings** (Screen Time model); Agent page gets only the live strip (tok/s · which-brain · today's tokens). |
| Autonomous activity in chat? | Research law upheld everywhere: **never merged into the transcript** — ticker + Pulse rail + ephemeral popups only. |

---

## Final structure (tabs + what each holds)

Header segment becomes **Hub · Agent · Settings**.

| Tab | Holds | Fate of old surfaces |
|---|---|---|
| **Hub** | Unchanged: glanceable widget grid, split/hidden chat modes (`deck[data-chat]` keeps working). | — |
| **Agent** (new, flagship) | Conversation-first page: left rail (sessions · tools quick-actions · Local AI live panel, lifted from `renderChatSide`), centered 860px stream with tool show-in cards, status hero (Sigil + brain badge), 320px right rail with **Record / Screen / Pulse** tabs, 28px heartbeat ticker. | Fullscreen chat mode → *is* this page. **Console tab → Record rail** (recorder renderer re-mounted, turn-grouped). **Desktop tab → Screen rail** (aux_desktop panel re-mounted, auto-switches while computer_use runs). |
| **Settings** (Mind, re-homed) | 236px sticky glass nav rail (groups: Intelligence · Autonomy · World · Dashboard · System) + 12 panels + search + hash deep-links (`#settings/permissions@row`) + always-visible kill switch in the rail footer. | Every Mind card relocated by CARD_MAP (table below), zero aux rewrites in Phase A. Label changes; ids `tab-mind`/`view-mind` stay. |

Transition safety: Console/Desktop/Mind tab DOM is hidden (not deleted) for one
release; their `setView` ids alias to the new pages; `/api/console` endpoint kept.
Rollback for either page = remove its one script tag.

---

## The Agent page

Full detail in `plans/ui-agent-page.md`; this is the build contract.

### Layout
Left rail 232px (glass, hides <1100px) · conversation column flex/860px max ·
right rail 320px (collapsible to 44px icon strip w/ unread dots, state in
localStorage) · heartbeat ticker strip above the composer edge. Existing chat DOM
(`#chat-log` + composer) is **re-parented** into `#agent-stream` on view-enter and
returned on view-exit so Hub's split chat never breaks.

### Component build list
1. **`#view-agent` shell** — rails + stream + hero + ticker markup, injected by aux_agent.js; all new CSS in its `<style>` block.
2. **Status hero** (56px sticky): the **Sigil** + state line (labeled phases, never spinners: `Thinking / Writing / Running terminal / Reading the screen / Waiting on you / Watching`) + **brain badge** + scrubber/rail toggles.
3. **SHOWIN_RENDER dispatcher** — wraps the chat-poll consumer; tool status events create cards in *running* state; `/api/recorder` diff upgrades them with exit/reversibility (match: tool + start ts + arg hash). Unknown tool types fall through to the original plain status line (fail open).
4. **Show-in card kit** — shared anatomy `.showin` (head 36px: icon · gist · status chip · duration · chevron / body per-type / foot: reversibility chip + "view in Record"); spring enter + one icon tick; 2px accent shimmer on the top hairline while running; per-tool accent via `data-tool`→`--tac`. Collapsed by default (except terminal-while-running); consecutive calls group under an "N steps" header; density toggle persisted via `/api/settings`.
5. **Right rail tabs** — Record (recorder re-mount + turn grouping + row→card scroll-flash), Screen (desktop re-mount + auto-switch once per turn + red LIVE dot), Pulse (reverse-chron autonomous feed w/ unread count).
6. **Alive-when-idle layer** — ephemeral popups (max 2, 6s, overflow badges Pulse; **never used for approvals**) + heartbeat ticker (8s crossfade of live facts; 6px EKG dot blips on every recorder event).
7. **Approval card, elevated** (§5.8 of the spec) — in-stream + sticky duplicate above composer; body renders via the SAME show-in renderer as its type so you approve the verbatim command; Approve / Deny / Deny+tell-it-why (reason prefixes the next message, user still presses send).
8. **Turn scrubber** — 44px strip; per-turn tool events as accent dots on a real-duration time axis; playhead re-reveals terminal chunks, flashes cards, scrubs the Screen filmstrip; 4× replay. Pure client-side over data already held.
9. **`/api/agent/pulse`** — tiny `aux_agent.py` (`import datetime as _agent_datetime` — the gotcha) pre-joining recorder tail ∪ watchtower ∪ foryou for ticker/Pulse.

### Tool-call card taxonomy (9 types)
| Type | Accent | Render |
|---|---|---|
| **terminal** (marquee) | iris | Dark card in both themes (`#0B0E16`), SF Mono; streaming stdout pinned-to-bottom, 220px scroll cap, blinking block cursor; complete → `exit 0/1` chip, collapses to last 3 lines + "show all N" |
| web_search | quick | `Searched — "query"` + up to 5 result link chips (open default browser); `n results · 1.2s` foot |
| file read/write/patch | ok-green | mono middle-ellipsized path + verb chip; mini-diff ≤12 lines w/ `+18 −4` stat; **Undo** proxies existing `recUndo` confirm flow |
| computer_use | warm amber | screenshot thumbnail from `/api/desktop/shot` with click-point ring ping; 3-up filmstrip when batched; click → Screen rail scrubbed. **Never contains approval controls** |
| skill | violet | hexagonal two-tone badge plaque + one-time specular sweep; description from `/api/capabilities` |
| memory write | rose | fact as serif quote block (the one serif) + store; "edit in Settings → Memory" link |
| delegate | cyan-grey | nested mini-stream of sub-agent events (max 6 + "n more in Record"); collapses to `finished · 4 actions · 38s` |
| approval | iris flood | see #7 above — the un-bypassable marquee |
| claude-bridge | clay | slim divider cards bracketing the deep segment: `Escalated to Claude — <reason>` + token estimate / `Back on local · 2,140 tokens · 41s` |

### Two-brain treatment
- **The Sigil** (36px live glyph): idle 18s orbit → local-thinking 3s orbit + iris glow → **Claude-deep: orbit splits into two counter-rotating particles, ring warms to new clay tokens (`--claude:#D97757` / dark `#E8926F`), radial bloom** → acting: ring ticks 12° per tool event.
- **Brain badge**: LOCAL (quicksilver dot, live tok/s ticking) / DEEP (clay, breathing) segmented pill; thumb spring-slides on `/api/claude/bridge` engage (5s poll, 1s during a turn); tooltip = routing reason + tokens this turn + today's Claude spend. **Display-only — not a control** (invariant).
- Per-message brain stamp + bridge divider cards give rationale + cost at the moment of spend (research §6/§7).

### Signature "whoa" elements (ranked)
1. **The Sigil split** — the visible moment the second brain engages.
2. **The streaming dark terminal show-in** — watch commands run inside the conversation.
3. **The turn scrubber** — DVR-replay any turn, terminal output re-revealing, screen filmstrip scrubbing in sync.
4. **Honest velocity** — real local tok/s as the streaming indicator; only a local-first app can.
5. **Heartbeat ticker + EKG blip** — the page has a pulse even when you say nothing.

### States & data
All states from spec §10 (idle-autonomous, paused, serve-down `-z` fallback banner,
empty-session welcome + live ticker, reduced-motion: orbits frozen/shimmers off).
Polling budget per spec §8 — all feeds visibility-gated to the Agent view except
one 10s recorder tail for header badge + popups; no websockets; job-poll stays.

---

## The Settings page

Full detail in `plans/ui-settings-page.md`; this is the build contract.

### Shell & IA
`aux_settings_shell.js` (loaded LAST) injects `#set-shell` inside `#view-mind`:
236px sticky glass nav rail (sliding `.set-ind` spring indicator, kill switch in
footer) + `main#set-panels`. Search (`/` shortcut, auto-built index from panel
headings + `.set-row` labels, flash-ring on hit). Hash routing
`#settings/<sec>@<row>`. Component kit (~180 lines, `set-` prefix): row / toggle /
seg / field / tokens / kv / danger / savebar / search / flash; relocated legacy
cards reuse `.card.glass` unchanged.

**5 groups · 12 panels:** Overview · [Intelligence] Agent & Models, Claude
Bridge, Memory & You-Model, Skills · [Autonomy] Permissions & Trust, Proactive ·
[World] Connections, Data & Sources · [Dashboard] Appearance & Layout, Insights ·
[System] System & Data.

### Migration of every existing Mind card (CARD_MAP)
Mechanism (verified in code): every aux card appends to `#view-mind`, and
expand.js:1786 removes/recreates all `[id^="mind-extra-"]` per render — so the
shell re-relocates after every `mindExtras` run; MutationObserver as safety net;
**unknown card ids fall back to System — never lost**. Zero aux-module edits in
Phase A.

| Existing card | → Panel |
|---|---|
| `mind-base-hero` (greeting + 4 gstat counters) | Overview (masthead; stats become jump-links) |
| `mind-base-memory` (memory editor, richest card) | Memory & You-Model |
| `mind-extra-youmodel` (goals/now/looking-for/interests/people) | Memory & You-Model |
| aux_foryou onboarding (`#fy-onboard`) | Memory & You-Model (seeds the You-Model) |
| `mind-base-skills` (skill list + categories) | Skills |
| `mind-extra-trust` (17-class tiers + audit) | Permissions & Trust (present as Autonomy Dial per class, research §7) |
| `mind-extra-shortcuts` (action-bus allowlist) | Permissions & Trust (it's a permission surface) |
| `mind-extra-watchtower` (rules + brief schedule) | Proactive |
| aux_foryou tuning card | Proactive |
| `mind-extra-google` | Connections |
| aux_messages FDA status card | Connections |
| aux_promotion drill badges + attribution | Agent & Models |
| `#cu-cap` Claude-usage card | Claude Bridge |
| `mind-extra-skills` leaderboard · `mind-extra-fuel` tokens/day + 14/30/60d · `mind-extra-models` model mix · `mind-base-activity` | Insights (kept in Settings; aux_mind_drill still works — finds cards by id) |
| `mind-extra-config` (export/import) | System & Data |

Markup edits (orchestrator-owned): tab label→Settings + gear glyph, 4 ids on the
base sections (index.html:850-889), 2 script tags. That's all of index.html.

### New settings (consolidated)
Kill switch (rail footer, always visible) · quiet hours + daily notify cap +
delivery channel (Proactive) · network-guard scrape-domain allowlist
(Permissions) · per-skill enable/disable (`skills_disabled` key) · Claude budget
warn % (Bridge) · data retention steppers + scheduled config backup + services
status/restart/logs via new `aux_system.py` (System) · theme/density/reduce-motion
(Appearance; 12-hour renders as a *locked* row — honesty over fake choice) ·
launch-at-login + menu-bar hotkey display + approval sound.

### Settings-side invariants
Permission-widening POSTs (Ask→Auto, floor unlock, allowlist/network adds):
native `confirm()` first, **server 409 while `/api/desktop` reports an active
computer-use session** (~10 lines in aux_permissions.py/aux_shortcuts.py POST
handlers), and flight-recorded; Overview shows the last 5 policy mutations from
the recorder. Locked rows (harmful-refused, floors) get lock glyph + spring
wiggle. Danger zone all behind native `confirm()`; factory reset requires typed
`RESET`.

---

## Build plan (ordered — fastest path to an alive Agent page, then Settings)

Two tracks after B0; they touch disjoint files and can run in parallel builders.
Load order at end of index.html's aux list: `aux_settings_shell.js` then
`aux_agent.js` (settings shell wraps the `mindExtras` chain; aux_agent wraps
`setView`/chat-poll/`loadRecorder` — different hooks, agent last so its retab
sees the final tab DOM).

| # | Step | Contents | Why this order |
|---|---|---|---|
| **B0** | Orchestrator, ~30 min | index.html only: Mind label→Settings + gear glyph, 4 base-section ids, 2 script tags. | Unblocks both tracks; the ONLY shared-file edit until B7. |
| **B1** | Agent shell (1 session) = P-A1 | `aux_agent.js` v1: inject `#view-agent`, retab to Hub·Agent·Settings (wrap `setView`, alias old keys, migrate `hermes_view`), re-parent chat on enter/exit, re-point `renderChatSide` at `#agent-side`. | Cheapest structural win: the page exists, chat works fullscreen, Hub untouched. |
| **B2** | **The alive moment** (1 session) = P-A2 core | Poll-wrap + `SHOWIN_RENDER` with the **terminal card first**; status hero with Sigil (idle/local/deep) + brain badge (bridge poll); heartbeat ticker v1 (client-side join of watchtower/foryou/recorder — no new backend yet). | Highest impact per line: streaming terminal + splitting Sigil + pulsing ticker is the demo. Ticker pulled forward from P-A3 because it's ~50 lines over existing polls. |
| **B3** | Settings Phase A (1 session, **parallel with B2**) | `aux_settings_shell.js`: shell CSS+HTML, CARD_MAP relocator + MutationObserver, nav + hash routing, search auto-index, kill-switch mirror. | Whole Mind→Settings migration ships with zero aux rewrites. Rollback = remove tag. |
| **B4** | Show-in completion (1 session) | Remaining renderers (web/file/skill/memory), recorder-completion matching, ephemeral popups, Pulse rail, `aux_agent.py` `/api/agent/pulse` (ticker v2). | Depends on B2's dispatcher. |
| **B5** | Rails fold-in (1 session) = P-A3 | Record re-mount (`renderRecorderRows` → rail, turn grouping), Screen re-mount (export aux_desktop mount fn — the one permitted edit there), auto-switch + LIVE dot; then hide Console/Desktop tab buttons (DOM kept). | Only after rail parity — this is what licenses removing two tabs. |
| **B6** | Approval elevation (1 session, **gated**) = P-A4 | §5.8: sticky duplicate, show-in-rendered approval body, `event.isTrusted` guard, computer_use-running lockout (+500ms), never-ephemeral/never-Enter rules. **Gate: verify with a real dangerous-command approval** (open item in CLAUDE.md) before B5's tab-hiding ships to "stable". | Touches the security-critical path; isolated so it gets its own verification pass. |
| **B7** | Settings Phase B (1-2 sessions) | Native blocks (Data & Sources token editors, Appearance keys, Active-model + memory-ceiling), `aux_system.py`, extend accepted-keys tuple (server.py:2393 — **orchestrator-owned edit**) + `GET /api/settings` if absent, escalation 409 guard in aux_permissions.py/aux_shortcuts.py. | First server-side change; everything before is JS-only. |
| **B8** | Polish (ongoing) = P-A5 + Settings C | Turn scrubber, delegate nesting, filmstrips, specular sweeps; per-module native `.set-row` conversions (each drops a CARD_MAP entry); retention enforcement, scheduled backup, downloads. | Pure upside, no dependencies. |

**Files changed, by owner** — New: `aux_agent.js`, `aux_agent.py`,
`aux_settings_shell.js`, `aux_system.py` (B7). Small edits: `aux_desktop.js`
(export mount fn, B5), `aux_permissions.py`/`aux_shortcuts.py` (409 guard, B7).
**Orchestrator-owned shared edits**: `index.html` (B0), `server.py:2393`
accepted-keys (B7). **Never edited**: `expand.js`, `aux_recorder.js`,
`aux_memory.js`, `aux_trust.js`, and every other existing aux module — all
wrapped, not modified. Every phase ends with ⌘R in the app + a headless render
check (node + stubbed helpers + live JSON, as done for expand.js).

---

## Risks

1. **Chat re-parent breaks Hub split chat.** Mitigation: DOM moves only on view
   enter/exit; `deck[data-chat]` path untouched; B1 acceptance test = send a
   message from Hub-split AND Agent, plus an approval round-trip, before B2.
2. **Poll-wrap fragility (monkey-patching the chat consumer).** Wrap, never
   replace; unknown/malformed tool events pass through to the original plain
   status renderer; a top-level try/catch around SHOWIN dispatch means a renderer
   bug degrades to today's UI, not a dead stream.
3. **Approval invariant regressions — the big one.** Guards, all required
   together: `event.isTrusted` on approve/deny; ALL approval buttons disabled
   while any computer_use call is running (`paused — Hermes is controlling the
   screen`, re-enable on complete+500ms); approvals never in ephemeral popups,
   never auto-dismiss, no Enter default; brain pill and everything in the hero is
   display-only; computer_use cards contain no approval controls; Settings-side
   409 while a computer-use session is live; wire protocol (`/api/chat/approve`)
   untouched. **B6 does not ship without the real dangerous-command approval
   test** (still-open CLAUDE.md item).
4. **Recorder↔show-in matching is heuristic** (tool + start ts + arg hash).
   Fail open: unmatched cards still settle to a terminal state on job completion;
   Record rail remains ground truth; no card ever blocks the transcript.
5. **Removing Console/Desktop tabs strands muscle memory or a missed dependency.**
   Tabs hidden not deleted for one release; `setView('console'/'desktop')` alias
   to Agent (+ auto-open the matching rail tab); `/api/console` endpoint kept;
   rollback = remove `aux_agent.js` tag.
6. **Settings relocator races async late-mounting cards.** The relocator re-runs
   after every `mindExtras` chain AND on MutationObserver childList; unknowns go
   to System, never dropped; rollback = remove `aux_settings_shell.js` tag (cards
   render flat in `#view-mind` exactly as today).
7. **Polling/perf budget.** New polls (bridge 5s/1s, desktop 2s-while-running,
   pulse) gate on `visibilityState` AND view===agent; only the 10s recorder tail
   runs globally; long transcripts rely on the collapsed-by-default grammar and
   220px terminal scroll caps to bound DOM size.
8. **aux-module gotchas.** `datetime` alias in `aux_agent.py`/`aux_system.py`;
   script load order (settings shell before agent, both last); WKWebView dialog
   quirks — keep native `confirm()`/`prompt()` flows for undo/danger/escalation
   exactly as implemented.
9. **Design-law drift under speed.** No emoji anywhere (all new glyphs bespoke
   two-tone SVG in the `recGLY` style), 12-hour via `recClock`, all motion
   through global `animate()`+SPRING with REDUCE fallbacks (orbits frozen,
   shimmers off, fades only) — checked at every phase gate.
