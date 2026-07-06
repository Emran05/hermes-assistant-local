# UI Restructure — Settings Page (Mind re-home)

Spec for converting the Mind tab into a real Settings page: left settings-nav +
panels, search, deep links, and a migration path that keeps every existing aux
module working with near-zero rewrites. Companion spec: `ui-agent-page.md`
(conversation-focused Agent view). Design laws apply throughout: zero emoji
(bespoke two-tone SVG only), 12-hour time, density, Liquid Glass (backdrop blur
+ hairlines + specular), Motion One (`animate()`, `SPRING`), per-category
accent, aux-module pattern, WKWebView.

---

## 1. Principles

1. **Settings is where you configure the agent; Agent/Hub/Console are where you
   use it.** Anything with a toggle, a policy, a credential, or an editor lives
   here. Read-only *live* activity stays on Console/Agent.
2. **One nav, eleven panels, one search.** Not a pile of cards. Every setting is
   reachable in two clicks or one search.
3. **Minimal rewrite.** Existing aux cards keep mounting exactly as they do
   today (`window.mindExtras` chain into `#view-mind`); a new shell *relocates*
   them into their section panel after every render. Modules are converted to
   panel-native layouts later, one at a time, optionally.
4. **The approval invariant survives.** No control in Settings lets the agent
   widen its own permissions: permission-escalating POSTs are refused
   server-side while a computer-use session is live, and every policy change is
   flight-recorded.

---

## 2. Tab & routing changes

- **Rename, don't re-plumb.** The top tab keeps ids `tab-mind` / `view-mind`
  and the `setView` map key `mind` (index.html:1025-1038 untouched except
  label). The visible label becomes **Settings** with a bespoke gear glyph:

  ```html
  <b id="tab-mind" role="tab" aria-selected="false">
    <svg class="ic" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3.2"/>
      <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7"/>
    </svg>Settings</b>
  ```

  Tab order becomes: Hub · Agent (new) · Console · Desktop · **Settings**
  (settings moves to the far right of the segment, standard app convention).
- **Deep links.** Hash routing: `#settings/<sectionId>` (e.g.
  `#settings/permissions`). `hashchange` handler calls
  `setView('mind')` + `settingsShow(sectionId)`. Existing code that wants to
  send users to a setting (e.g. Claude-usage cap banner "Manage") just sets
  `location.hash`.
- **Per-row anchors.** `#settings/<sectionId>@<rowId>` scrolls to a row and
  flash-rings it (see §9).

---

## 3. Shell layout

New file `dashboard/aux_settings_shell.js` (served automatically as
`/aux_settings_shell.js`; add ONE `<script>` tag at the END of index.html's aux
list so it wraps the whole `mindExtras` chain last).

On first activation of the view it injects into `#view-mind`:

```
#view-mind                        (existing container; becomes the shell host)
└─ #set-shell                     display:grid; grid-template-columns:236px 1fr
   ├─ nav#set-rail  .glass        sticky left rail
   │  ├─ #set-search              input + result dropdown
   │  ├─ .set-group ("")          → Overview
   │  ├─ .set-group "Intelligence"→ Agent & Models · Claude Bridge · Memory & You-Model · Skills
   │  ├─ .set-group "Autonomy"    → Permissions & Trust · Proactive
   │  ├─ .set-group "World"       → Connections · Data & Sources
   │  ├─ .set-group "Dashboard"   → Appearance & Layout · Insights
   │  ├─ .set-group "System"      → System & Data
   │  └─ #set-kill                rail footer: master "Pause agent" switch
   └─ main#set-panels
      ├─ section.set-panel#sec-overview
      ├─ section.set-panel#sec-models        [hidden]
      ├─ section.set-panel#sec-bridge        [hidden]
      ├─ section.set-panel#sec-memory        [hidden]
      ├─ section.set-panel#sec-skills        [hidden]
      ├─ section.set-panel#sec-permissions   [hidden]
      ├─ section.set-panel#sec-proactive     [hidden]
      ├─ section.set-panel#sec-connections   [hidden]
      ├─ section.set-panel#sec-sources       [hidden]
      ├─ section.set-panel#sec-appearance    [hidden]
      ├─ section.set-panel#sec-insights      [hidden]
      └─ section.set-panel#sec-system        [hidden]
```

- **Rail**: 236px, Liquid Glass (`backdrop-filter:blur`, hairline right border,
  specular top edge). Items: 13px, glyph + label, per-category accent dot on
  hover; the active item gets a sliding glass pill indicator (a single absolute
  `.set-ind` element spring-animated to the active item's offset — one element,
  not per-item state).
- **Panels**: each panel starts with a `.set-head` (28px title, one-line
  subtitle, right-aligned contextual actions) then stacked `.set-block` cards
  (existing `.card.glass` styling reused — relocated legacy cards drop in
  visually unchanged).
- **Narrow (<880px)**: rail collapses to a horizontal scrollable chip row above
  the panel (`.set-rail` becomes `position:static; display:flex; overflow-x:auto`).
- **Rail footer kill switch**: "Pause agent" — the same pause/resume the model
  pill exposes, always visible. Red-tinted when paused, with "Paused 2:14 PM"
  caption (12-hour).

### Component inventory (new CSS, `set-` prefix, ~180 lines)

| Component | Purpose |
|---|---|
| `.set-rail` `.set-group` `.set-item` `.set-ind` | nav rail, group headers, items, sliding indicator |
| `.set-panel` `.set-head` `.set-block` | panel scaffold; blocks are `.card.glass` variants with full-width layout |
| `.set-row` | label + description (left) / control (right); the standard settings row |
| `.set-toggle` | glass toggle switch (thumb spring-animates; already have `.pill` styles to derive from) |
| `.set-seg` | small segmented control (reuse `.seg` styles at 12px) |
| `.set-field` `.set-ta` | text input / textarea with hairline focus ring |
| `.set-tokens` | token/chip list editor (tickers, feeds, domains): chips with x-glyph, inline add field |
| `.set-kv` | read-only key-value strip (status rows: service up/down, connected accounts) |
| `.set-danger` | red-accent block for destructive actions; every action goes through native `confirm()` |
| `.set-savebar` | sticky bottom bar inside a dirty panel: "Unsaved changes · Revert / Save" |
| `.set-search` `.set-hit` | search input + dropdown hits |
| `.set-flash` | 1.2s flash ring applied to a row navigated to via search/anchor |

---

## 4. Section-by-section IA

Every block lists its controls and the endpoint it reads/writes. "Legacy card"
means the existing aux card is relocated here as-is in Phase A (§7) and
optionally rebuilt native later.

### 4.0 Overview (`sec-overview`) — landing panel
The current Mind hero becomes the Settings masthead.
- **Masthead**: relocated `mind-base-hero` (the `#mind-line` greeting + the four
  `.gstat` counters: Skills / Memories / Sessions / Tool calls). Sub-line links
  each stat to its section (`#settings/skills`, `#settings/memory`, …).
- **Status glance** `.set-kv`: active local model + tok/s (`/api/models`,
  `/api/metrics`), Claude Bridge state + % of cap (`/api/claude/bridge`),
  Google connected? (`/api/google/status`), Watchtower next brief time
  (`/api/watchtower`), services health (`/api/settings` system block, §4.11).
  Each row is a jump-link into its section.
- **Recent changes**: last 5 settings/policy mutations pulled from
  `/api/recorder?filter=policy` — reinforces "everything here is recorded".

### 4.1 Agent & Models (`sec-models`) — accent: violet
- **Active model** block (native, Phase B): the model-pill menu's content
  (`/api/models` GET) promoted to a full block — model rows with RAM badge,
  "lighter = less RAM" hint, active radio, Switch action (POST
  `/api/models {model}`); pause/resume mirrors the rail kill switch.
- **Memory ceiling** block (native): MLX soft/hard ceiling sliders + numeric
  fields, "Override for this session" toggle, **Free memory now** button
  (existing endpoints under `/api/models` / `/api/metrics`; wire to whichever
  the model menu already calls — reuse, don't duplicate). Live gauge: current
  MLX RSS vs ceilings as a two-marker bar, polls `/api/metrics` every 5s while
  the panel is visible only.
- **Downloads**: available-to-download model list with size + license note,
  progress bar during pull (`/api/models` op:download if present; otherwise
  Phase C).
- **Promotion drills** (legacy: aux_promotion's badges + "Built with Llama"
  attribution relocate here): drill badges, last-drill result, license notes.

### 4.2 Claude Bridge (`sec-bridge`) — accent: coral
- **Enable bridge** `.set-toggle` (`/api/claude/bridge` GET/POST).
- **Budget policy** `.set-seg` per lane: *Thinking* (unlimited) / *Code*
  (gated — each use asks) / *Harmful* (refused, locked, display-only). Locked
  rows render the lock glyph and are not interactive.
- **Tier routing**: Sonnet vs Opus rows — "Deep thinking → Opus, everything
  else → Sonnet" with per-lane pickers.
- **Usage-cap governor**: cap %, current burn (relocated `#cu-cap` Claude-usage
  card from aux_claude_usage), warn-at threshold (new, §8), and a "View usage
  stats" link → `#settings/insights@tokens`.

### 4.3 Memory & You-Model (`sec-memory`) — accent: mind blue
- **What it remembers** (legacy: `mind-base-memory`, the aux_memory editor —
  the richest existing card; relocate untouched: rows, add/edit/delete, trash,
  ETag conflict banners all keep working; `/api/memory/*`).
- **You-Model** (legacy: `mind-extra-youmodel`): GOALS / NOW / LOOKING-FOR /
  INTERESTS / PREFERENCES editors + people cards (`/api/youmodel`).
- **Raw files**: snapshots list + trash (already inside aux_memory's card;
  surface as a sub-block).
- **Run onboarding** button (the `#fy-onboard` flow from aux_foryou moves here —
  it seeds the You-Model, that's a memory concern).

### 4.4 Skills (`sec-skills`) — accent: gold
- **Learned skills** (legacy: `mind-base-skills` card — `#skill-list`,
  `#skill-cats` category bar, count).
- **Enable/disable** per skill: `.set-toggle` per row (new; POST
  `/api/settings {skills_disabled:[...]}` — additive key, server already
  accepts list-valued keys pattern; add `skills_disabled` to the accepted-keys
  tuple in server.py:2393).
- **Skill-forge** entry: "Teach a new skill" button → opens the existing forge
  flow (chat handoff to Agent page with a prefilled composer).

### 4.5 Permissions & Trust (`sec-permissions`) — accent: red/amber
- **Trust tiers** (legacy: `mind-extra-trust`): the 17/18-class Auto/Ask/Never
  matrix, floor locks, untrusted-policy banner, recent decisions
  (`/api/permissions`).
- **Shortcuts action bus** (legacy: `mind-extra-shortcuts`): the allowlist is a
  permission surface, so it lives here, not in Connections.
- **Network guard** (new, §8): `.set-tokens` allowed-domain list for autonomous
  scraping.
- **Audit log**: last 20 policy decisions (already in the trust card) + "Full
  audit in Console" link.
- **Escalation guard (invariant)**: any POST that *widens* a permission
  (Ask→Auto, floor unlock, allowlist add, network-guard add) is (a) preceded by
  native `confirm()`, (b) rejected by the server with 409 while
  `/api/desktop` reports an active computer-use session, (c) written to the
  flight recorder. (a) is shell JS; (b) is a ~10-line check in
  `aux_permissions.py`/`aux_shortcuts.py` POST handlers reading the desktop
  session flag.

### 4.6 Proactive Intelligence (`sec-proactive`) — accent: teal
- **Watchtower rules** (legacy: `mind-extra-watchtower`): rule list + editor
  (`/api/watchtower`).
- **Brief schedule**: morning brief time (12-hour picker), midday toggle,
  breaking-news toggle (all inside the watchtower card today; Phase B splits
  them into `.set-row`s).
- **Quiet hours** (new, §8): start/end 12-hour pickers + "no notifications
  except breaking" policy seg.
- **Daily notification cap** (new): numeric stepper.
- **Delivery channel** (new): seg — Dashboard / Telegram / Messages.
- **For-You feedback** (legacy: aux_foryou card minus onboarding): tuning of
  the For-You stream (`/api/foryou`).

### 4.7 Connections (`sec-connections`) — accent: green
- **Google** (legacy: `mind-extra-google`): Gmail/Calendar status dots +
  connect wizard (`/api/google/*`).
- **Messages & Full Disk Access** (legacy: aux_messages status card): FDA
  granted? test-read button, setup instructions.
- **Telegram**: bot status, chat-id, test-ping (`/api/settings` keys).
- **Folder access**: `.set-tokens` list of granted folders + "Grant…" (opens
  native folder picker via the existing prompt bridge).
Every connection row is a `.set-kv` with status dot → expands to its wizard.

### 4.8 Data & Sources (`sec-sources`) — accent: sky
All native Phase B — these have `/api/settings` keys but no UI today beyond
scattered widget affordances:
- **Tickers** + starred, **Coins**: `.set-tokens` (`tickers`, `starred_tickers`,
  `coins`).
- **News/RSS/Intel feeds**: `.set-tokens` with URL validation (`rss_feeds`,
  `news_feeds`, intel feeds key).
- **Quicklinks**: token editor with title+URL rows (`quicklinks`).
- **World clocks**: timezone token list (`timezones`).
- **Weather city**: `.set-field` (`weather_city`).
One sticky `.set-savebar` for the whole panel; single POST `/api/settings` with
changed keys; server already caps lists at 20 and clears `_widget_cache`.

### 4.9 Appearance & Layout (`sec-appearance`) — accent: purple
- **Theme**: Auto/Dark/Light seg (new `theme` key in settings.json + a
  `data-theme` root attribute; today's palette becomes the dark theme).
- **Density**: Comfortable/Dense seg (new `density` key → root class scaling
  paddings; the app is already dense — this adds a *comfortable* variant).
- **Reduce motion**: seg Auto (system) / Always — feeds the existing global
  `REDUCE` flag so every aux module inherits it for free.
- **Accent seed**: per-category accents stay fixed by design law; expose only a
  global tint hue slider (optional, Phase C).
- **Widgets & Hub layout**: embeds the existing Widget Center (add/remove via
  `/api/layout` op add/remove); drag-order editor list mirrored from Hub.
- **Clock**: 12-hour is a design law — render a locked row stating "12-hour
  time · fixed" with the lock glyph rather than a toggle (honesty > fake
  choice).

### 4.10 Insights (`sec-insights`) — accent: slate
**Recommendation: Insights stays in Settings** (like macOS Screen Time), and
the Agent page gets only a compact live strip (tok/s, which-brain, today's
tokens). Rationale: drill-downs are retrospective configuration-adjacent
analysis, not conversation; keeping them here avoids cluttering the Agent
page's focus. Console remains the live activity surface.
- Relocated legacy cards: `mind-extra-skills` (skills-in-action leaderboard),
  `mind-extra-fuel` (tokens/day + 14/30/60d drills), `mind-extra-models`
  (model-mix chart) — aux_mind_drill's enhancement pass keeps working because
  it re-runs after `mindExtras` and finds cards by id regardless of location.
- Relocated `mind-base-activity` (spark, messages, tokens in/out, platform
  list).
- **Claude usage** deep stats link-through from §4.2.
- Data: `/api/mind_extra`, `/api/metrics`.

### 4.11 System & Data (`sec-system`) — accent: graphite
- **Services**: `.set-kv` rows for dashboard server, MLX server, watchtower
  daemon — status dot, uptime, **Restart** button each (needs one new endpoint:
  `register_get("/api/system/services")` + `register_post("/api/system/restart")`
  in a small `aux_system.py`; restart shells out to the existing
  `install-services.sh` targets).
- **Logs**: tail viewer (last 200 lines, monospace, `.set-block` with
  `overflow:auto`) with source seg (server / mlx / watchtower).
- **Config as code** (legacy: `mind-extra-config`): export/import snapshot
  (`aux_config` endpoints), plus **scheduled backup** toggle (new, §8).
- **Data retention** (new, §8): recorder history days, memory snapshot
  keep-count, chat transcript retention — steppers; POST `/api/settings`.
- **Danger zone** `.set-danger`: wipe memory (trash-first), reset permissions
  to floors, factory reset — each behind native `confirm()` with typed
  confirmation for factory reset (`prompt()` must equal "RESET").

---

## 5. Search

- Registry: `window.settingsRegistry = []` of
  `{sec, secTitle, row, title, keywords, el()}`.
  - **Phase A auto-index**: after each relocation pass, walk every
    `.set-panel`, index `h2` texts and `.set-row` labels — zero per-module
    work.
  - Modules may push richer entries later
    (`settingsRegistry.push({sec:'permissions', title:'Terminal commands', keywords:'shell bash exec'})`).
- UX: focus `#set-search` (also global shortcut `/` when Settings view is
  active), type ≥2 chars, dropdown of up to 8 hits grouped by section
  (`section — row`), arrow keys + Enter. Selecting: switch panel, scroll row
  into view, apply `.set-flash`.
- Match: case-insensitive substring across title+keywords; rank exact-prefix
  first. No fuzzy library — 30 lines of JS.

---

## 6. Aux-module wiring — the relocator (near-zero rewrite)

**Key mechanics found in code, which the design exploits:**
- Every aux JS chains `window.mindExtras` and appends its card to
  `document.getElementById('view-mind')` (aux_trust.js:145, expand.js:1783).
- `mindExtras()` in expand.js re-entry-guards by removing all
  `[id^="mind-extra-"]` under `#view-mind` and re-appending (expand.js:1786) —
  so cards are *recreated at the container root on every render*.

**Therefore:** panels live *inside* `#view-mind`, and the shell re-relocates
after every chain run. No aux module changes at all.

```js
// aux_settings_shell.js — loaded LAST, wraps the whole chain
var CARD_MAP = {
  'mind-base-hero':      'sec-overview',
  'mind-extra-skills':   'sec-insights',
  'mind-extra-fuel':     'sec-insights',
  'mind-extra-models':   'sec-insights',
  'mind-base-activity':  'sec-insights',
  'mind-extra-trust':    'sec-permissions',
  'mind-extra-shortcuts':'sec-permissions',
  'mind-extra-youmodel': 'sec-memory',
  'mind-base-memory':    'sec-memory',
  'mind-base-skills':    'sec-skills',
  'mind-extra-google':   'sec-connections',
  'mind-extra-watchtower':'sec-proactive',
  'mind-extra-config':   'sec-system'
  // + aux_promotion badges → sec-models, cu-cap card → sec-bridge,
  //   foryou card → sec-proactive, messages card → sec-connections
  //   (add their root ids after confirming; unknowns are safe — see below)
};
var prev = window.mindExtras;
window.mindExtras = async function(){
  if (typeof prev === 'function') { try { await prev(); } catch(e){} }
  relocate();
};
function relocate(){
  var host = document.getElementById('view-mind');
  ensureShell(host);                          // idempotent shell injection
  // any direct child of #view-mind that is not the shell is a legacy card
  Array.prototype.slice.call(host.children).forEach(function(n){
    if (n.id === 'set-shell') return;
    var dest = CARD_MAP[n.id] || 'sec-system';        // unknowns → System, never lost
    var slot = document.getElementById(dest);
    var anchor = slot.querySelector('[data-legacy-slot]') || null;
    slot.insertBefore(n, anchor);                     // preserves per-panel order via slot markers
    n.classList.add('set-legacy');                    // width/margin normalization only
  });
  buildSearchIndex();
}
new MutationObserver(function(){ scheduleRelocate(); })   // safety net for async late mounts
  .observe(document.getElementById('view-mind'), {childList:true});
```

- **Base cards need ids**: add four ids in index.html (the only markup edit
  besides tab label/order): `mind-base-hero`, `mind-base-skills`,
  `mind-base-memory`, `mind-base-activity` on the four static `<section>`s at
  index.html:850-889.
- **Panel switching**: `settingsShow(id)` toggles `[hidden]` on panels, moves
  `.set-ind`, updates `location.hash`, and lazily kicks per-panel pollers
  (metrics gauge etc.). `revealStagger` runs on the shown panel's blocks —
  same entrance feel Mind has today (expand.js:1896).
- **View activation**: existing code already calls `mindExtras()` when the
  mind view renders; nothing changes. `setView('mind')` remains the entry.
- **Server side**: no changes for Phase A. Phase B adds `aux_system.py`
  (services/restart/logs endpoints via `register_get`/`register_post`) and
  extends the accepted-keys tuple in the `/api/settings` POST
  (server.py:2393) with: `skills_disabled`, `quiet_hours`, `notify_cap`,
  `brief_channel`, `theme`, `density`, `reduce_motion`, `net_allowlist`,
  `retention`, `backup_schedule`, `budget_warn_pct`. Add a matching
  `GET /api/settings` (register_get) returning the sanitized settings dict if
  one doesn't exist.

### Per-panel legacy slots
Each panel's scaffold includes `<div data-legacy-slot></div>` where relocated
cards should sit relative to native blocks (usually after the native rows), so
Phase B native blocks and legacy cards interleave deterministically.

---

## 7. Migration path (phased, shippable at each step)

- **Phase A — re-home (1 session).** Tab relabel + reorder; 4 base-card ids;
  `aux_settings_shell.js` (shell CSS+HTML, relocator, nav, hash routing,
  search auto-index, kill-switch mirror). Every existing card works unchanged,
  now organized under 12 panels. Ship.
- **Phase B — native blocks (1-2 sessions).** Data & Sources panel (token
  editors over `/api/settings`), Appearance panel (theme/density/reduce-motion
  keys + root attributes), Active-model + memory-ceiling blocks (promote model
  menu), `aux_system.py` (services/logs/restart), accepted-keys extension,
  escalation guard (server 409 during live computer-use). Ship.
- **Phase C — polish (ongoing, per-module).** Convert relocated cards to
  panel-native `.set-row` layouts one module at a time (each conversion:
  change that module's mount target to its panel slot and drop CARD_MAP
  entry); brief-schedule split; downloads; retention enforcement; scheduled
  config backup; accent tint slider.

Rollback at any phase = remove the shell script tag; cards render into
`#view-mind` as a flat grid exactly as today.

---

## 8. New settings worth adding (inventoried above, consolidated)

| Setting | Section | Why |
|---|---|---|
| Master kill switch in rail footer | shell | always-on agent needs an always-visible off |
| Quiet hours + daily notification cap + delivery channel | Proactive | notify-only agent must be tunable or it gets muted forever |
| Network guard (scrape-domain allowlist) | Permissions | autonomous scraping needs a visible boundary |
| Skills enable/disable | Skills | today skills are all-on |
| Budget warn threshold (% of Claude cap) | Claude Bridge | soft warning before the governor hard-gates |
| Data retention (recorder days, snapshots, transcripts) | System | local-first means the user owns growth on their own disk |
| Scheduled config-as-code backup | System | the export exists; automate it |
| Theme / density / reduce-motion | Appearance | table stakes; REDUCE already plumbed |
| Launch-at-login + menu-bar quick-ask toggle + hotkey display | System | surfaces what install-services.sh already manages |
| Approval sound toggle | Proactive | audible cue for approval-needed while app is backgrounded |

---

## 9. States & motion (Motion One)

- **Panel switch**: outgoing panel none (instant hide); incoming
  `animate(panel, {opacity:[0,1], transform:['translateY(8px)','none']}, {duration:.28, easing:[.22,1,.36,1]})`
  then `revealStagger(panel.querySelectorAll('.set-block,.set-legacy'), 60)`.
  Skipped under `REDUCE`.
- **Nav indicator**: `animate(ind, {top: y+'px'}, SPRING)` — glass pill glides.
- **Toggles**: thumb `animate(thumb,{transform:'translateX(...)'} , SPRING)`;
  track tint crossfades.
- **Search flash**: `.set-flash` = box-shadow ring keyframe 0→accent→0 over
  1.2s.
- **Savebar**: slides up `translateY(100%)→0` spring on first dirty change;
  Save button shows inline progress hairline; success collapses bar with a
  check glyph beat.
- **Loading**: reuse `.skel` skeleton rows per block; blocks render heads
  immediately, bodies skeleton until fetch resolves.
- **Errors**: per-block inline banner (hairline red card) with Retry — same
  idiom as aux_memory's `#mem-banner`.
- **Empty**: every list editor has a one-line empty state with the section's
  glyph at 20% opacity and an inline add affordance — no blank boxes.
- **Locked rows** (harmful-refused, permission floors, 12-hour law): lock glyph
  + muted text + `cursor:not-allowed`; clicking wiggles ±2px (spring) instead
  of doing nothing silently.

---

## 10. Data-source map (all existing unless marked NEW)

| Panel | Endpoints |
|---|---|
| Overview | `/api/models`, `/api/metrics`, `/api/claude/bridge`, `/api/google/status`, `/api/watchtower`, `/api/recorder` |
| Agent & Models | `/api/models` (GET/POST), `/api/metrics` |
| Claude Bridge | `/api/claude/bridge` |
| Memory & You-Model | `/api/memory/*`, `/api/youmodel`, `/api/foryou` (onboarding) |
| Skills | `/api/mind_extra`, `/api/settings` (`skills_disabled` NEW key) |
| Permissions & Trust | `/api/permissions`, aux_shortcuts endpoints, `/api/desktop/*` (escalation guard) |
| Proactive | `/api/watchtower`, `/api/foryou`, `/api/settings` (quiet-hours keys NEW) |
| Connections | `/api/google/*`, aux_messages endpoints, `/api/settings` (telegram/folders) |
| Data & Sources | `/api/settings` (tickers, coins, rss_feeds, news_feeds, quicklinks, timezones, starred_tickers, weather_city) |
| Appearance | `/api/settings` (theme/density NEW), `/api/layout` |
| Insights | `/api/mind_extra`, `/api/metrics` |
| System | `/api/system/*` (NEW aux_system.py), aux_config endpoints, `/api/settings` (retention NEW) |
