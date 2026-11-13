# Hermes Assistant codebase audit — 2026-10-08

Reviewed version **1.3.2**, commit **7b5bd76** (clean tree), with the dashboard
process that has been live since 2026-09-16 (no code commits since 2026-09-11, so
the running code is the reviewed code). Found **6 P1, 24 P2 and ~50 P3 defects**
(B-series, open) plus **11 UI defects** (U-series, fixed the same day — see
"UI findings").
P1 means fix soon: ordinary use can lose data, silently hide something the owner
needs, break an explicit privacy choice, or let the agent get past the human
approval gate. P2 is a real defect with a narrower trigger. P3 is low impact,
hardening, or polish.

IDs continue from the 2026-09-10 audit (A01–A10, all still fixed — nothing
below re-opens them) and start at **B01**.

## Scope and verification

- **Tests.** Unit tier `HERMES_TESTS_STRICT=1 tools/tests/run.sh unit`:
  **2,251 / 2,251 pass**. Live tier against the running dashboard:
  **311 / 311 pass** (`t_mcp.py` 293, `verify_round2.sh` 18). Browser tier
  **skipped** (Playwright not installed); the UI was driven by hand in a real
  WebKit-class browser instead (below). The `ac` tier was not run: the Mac was
  on battery and that tier refuses, and no test woke a model.
- **Live API sweep.** Every one of the 87 GET routes was called with default
  parameters (0 × 5xx, none slower than 3 s), then 144 hostile parameter cases
  (FTS operators, NUL, 5 kB tokens, path traversal in every id-taking route,
  `__proto__`, `NaN`/`-1`/`1e9` limits): **0 × 5xx, 0 leaks, 0 tracebacks**.
  The read surface is robust. No state-changing request was sent except two
  malformed POSTs chosen to fail before any write (B12).
- **Static review.** 11 parallel reviewers, one per subsystem (HTTP/chat core,
  model lifecycle + launchers, chat pipeline/RPC, watchtower/needs-you/for-you,
  data stores + memory, update/install/release, MCP + harness modules, core
  frontend, aux frontend, security cross-cut, Swift shell + remaining aux).
  The top 13 claims were then re-checked by an adversarial verifier against the
  code; verdicts are folded in below (two were downgraded to "partly").
  Reviewers ran offline reproductions (real functions, stubbed globals,
  scratch dirs, jsdom/node:vm for the UI) — they are cited as "repro".
- **UI.** Hub, Agent, model menu, Preferences, pop-outs, Customize, full-screen
  chat, light/dark, at the app's minimum (900×640) and default (1440×900)
  window sizes, with a programmatic overlap / clipping / hit-target / overflow
  / contrast sweep. See "UI findings".
- **Not exercised:** a live model turn, a real approval request, real OAuth,
  a real update/restore, the app rebuild (would drop Full Disk Access).

## Root cause behind most of the security findings

The same-origin guard (Host / Origin / Sec-Fetch-Site) is sound against
browsers — every bypass variant tried returned 403. But **the agent's own
terminal tool runs as the same uid**, can reach `127.0.0.1:7788` with no Origin
header (which the guard deliberately allows), and can read every `0600` file in
`~/.hermes`. So every endpoint whose job is to *ask the human* can be exercised
by the thing it is meant to restrain. B02 lists the concrete endpoints. A token
on disk does not fix this (same uid); an out-of-band secret held only by the
Swift app and injected into the WebView, or a native confirmation dialog, does.

## Findings

| ID | Pri | Finding | Primary location |
|---|---|---|---|
| B01 | P1 | A stale approval box reappears after you answer and can approve a later, unseen command | `dashboard/index.html:3170-3190` |
| B02 | P1 | The agent can answer its own approval / consent prompts over the Origin-less API | `server.py:4619`, `aux_shortcuts.py:466,575`, `aux_memlayer.py:1172-1260`, `permissions.py:669,726` |
| B03 | P1 | Any Hugging Face repo can be added and launched with `--trust-remote-code` (code execution outside approvals) | `server.py` `add_model`, `mlx-server.sh:165,195`, `mlx-server-bg.sh:76,84` |
| B04 | P1 | Needs-you "Done" on a message hides every future message from that contact | `aux_needsyou.py:376,1188,1202` |
| B05 | P1 | Scratchpad: editing in the pop-out then typing in the hub widget overwrites the edit | `index.html:1961-1963`, `expand.js:283-376` |
| B06 | P1 | MCP `needs_you` leaks message senders + previews while `messages_search` is off | `hermes_mcp.py:802-835` |
| B07 | P2 | Removing a widget doesn't stick across restarts; restarts can drop other widgets from the saved layout for good | `aux_claude_usage.py:447`, `aux_foryou.py:809`, `aux_messages.py:309`, `aux_needsyou.py:1752`, `server.py:2714` |
| B08 | P2 | Needs-you build overwrites Done/Snooze/acts written while it runs | `aux_needsyou.py:1134→1211` |
| B09 | P2 | Needs-you puts casual VIP chatter ("today", "tomorrow") and long-past deadlines in **Now** | `aux_needsyou.py:160-163,470-471` |
| B10 | P2 | A streaming reply (or its approval box) renders into whichever conversation is on screen | `index.html:3149-3207` |
| B11 | P2 | Enter bypasses the disabled Send button → overlapping turns; the server has no per-session in-flight check, so the first job hangs 600 s and appends a timeout bubble after the real reply | `index.html:3209`, `server.py:4584-4615`, `hermes_rpc.py:414` |
| B12 | P2 | Malformed POST bodies drop the connection (repro live); negative Content-Length hangs a thread; no body-size cap | `server.py:4144-4149`, `:4325`, `:4356` |
| B13 | P2 | Uploading any file whose name has a char > U+00FF (every macOS screenshot: U+202F) silently does nothing | `index.html:3095-3100` |
| B14 | P2 | Recorder undo-trash is purged by the file's *original* mtime — an "undone" file older than 14 days is deleted within the hour | `aux_recorder.py:1164`, `:791-800` |
| B15 | P2 | Memory trash: a just-deleted old file can be GC'd in the same request; `people/` trash entries can't be restored (400) | `aux_memory.py:221-249,642-654`, `:48,664` |
| B16 | P2 | Dictation's default "rules" cleanup deletes text before "I meant to…" and collapses "had had" | `aux_dictation.py:109,332-337` |
| B17 | P2 | Dashboard can be framed by any site (no `X-Frame-Options` / `frame-ancestors`) | `server.py` response headers |
| B18 | P2 | Links: hub RSS card, quicklinks and `wireLinks` pass any URL scheme to `NSWorkspace.open`; "Recent activity" rows *open/execute* files instead of revealing them | `index.html:1986`, `server.py:2748-2776`, `app/main.swift:197-200,236-253`, `expand.js:682-713` |
| B19 | P2 | Calendar event titles enter every prompt and the unattended briefing (full toolset) unfenced, newlines intact | `server.py:1310-1325`, `aux_google.py:647`, `aux_watchtower.py:2474` |
| B20 | P2 | Switching model or "Free memory" while idle-suspended leaves the asleep marker over a running model → memory guard and idle-suspend both off | `server.py:3741-3790`, `:1677` |
| B21 | P2 | Export / MCP / trace redaction misses JSON-quoted keys, `*_secret`/`*_token`/`OPENAI_API_KEY=` names, `Basic` auth, prose; export title + filename never redacted | `aux_convos.py:51-54,283,316`, mirrored `hermes_mcp.py:164-167` |
| B22 | P2 | Watchtower rule pass appends a "suppressed" log row every 60 s per still-true rule, flushing real history out of every bounded reader | `aux_watchtower.py:956-960` |
| B23 | P2 | A rule's dedupe signature never expires — the same move (e.g. NVDA −6 %) never alerts again | `aux_watchtower.py:~695,~890` |
| B24 | P2 | Watchtower switches (incl. the two "complete off-switch" masters) flip optimistically and never revert on a failed write | `aux_watchtower.js:465-591` |
| B25 | P2 | Claude-bridge child is denylist-only: no `--tools ""`/`--strict-mcp-config`, inherits `~/.claude` settings + user MCP servers (incl. `rlm-repl`) — latent while escalation is off | `aux_claudebridge.py:83,503-509` |
| B26 | P2 | Agent → Screen "Send to agent" task box is dead; the Vitals card is unreachable | `aux_agent.js:521-552,1068-1089`, `aux_desktop.js:178-189`, `aux_metrics.js:17-22` |
| B27 | P2 | Config import rewrites `models.json` with only `id/label/ram/note` — custom models lose `backend`/`template_args`/drafter, unlisted ones are deleted | `aux_config.py:231-247,538-544` |
| B28 | P2 | Compression card: a column-0 comment ends the YAML block early → duplicate key, the edit silently does nothing (aux_promptbudget already fixed this exact bug) | `aux_context.py:360-363,479` |
| B29 | P2 | Model-swap overlay is permanent if its POST rejects (e.g. dashboard restarting) — UI blocked until ⌘R | `index.html:2723-2762` |
| B30 | P2 | Release process: `git add -A` after preflight, tag check after pushing both remotes, no CI gate on the release workflow, and `git push public main` publishes full private history (6 commits contain the owner's home path) | `tools/release.sh:37-63`, `.github/workflows/release.yml` |

### P1 detail

**B01 — stale approval box.** Approve/Deny removes the box and nulls
`approvalBox`; `/api/chat/approve` only sets `pending_choice`; the worker pops it
at the top of a loop blocked up to 1 s in `next_event(timeout=1.0)`, so `state`
stays `approval` for 0–1 s. The 700 ms poll sees it and rebuilds an enabled box
(line 3170), which nothing removes until `done` (only Quick Ask has the
`state!=='approval'` cleanup). When the agent later asks for a *different*
approval, `!approvalBox` is false, so the UI never shows it — the visible box
still reads the old command, and clicking it answers the unseen one. Repro with
mocked polls (`approval2–4.js`). The server side compounds it:
`/api/chat/approve` never checks that an approval is actually pending. CLAUDE.md
notes this UI "has not yet been exercised by a real dangerous-command approval".
**Fix:** key the box on the approval's identity (command + request id), remove
it whenever `state !== 'approval'`, ignore the next poll after a click, and
server-side refuse a choice unless `job.state == 'approval'` and the request id
matches.

**B02 — the agent can answer its own prompts.** Each of these is a plain
Origin-less POST the agent's terminal can make (confirmed in code):
- `/api/chat/approve {job, choice:"approve"}` — the job id is published by
  `GET /api/needsyou?mark=0` (`aux_needsyou.py:776`, `"approval:<jobid>"`). A
  background poller started earlier with non-dangerous commands approves the
  dangerous one before the human sees the card. `serve-token` is also readable,
  so `approval.respond` on :9119 is reachable too.
- `/api/shortcuts/run` returns the single-use `ticket` to the requester and
  redeems `{ticket, approved:true}` from anyone; `/api/shortcuts/config`
  ("user-only") exposes any Shortcut. The reply text "cannot be approved by the
  requester" is not enforced. (R1 already accepts raw `shortcuts run` in the
  terminal; this adds the false guarantee and the allowlist bypass.)
- `/api/memory/facts/update {approve_all:true}` approves every held row; a text
  edit without `origin:"card"` keeps the row approved; and `origin:"card"` is
  just a body field, so `_ml_facts_post` stores agent text as `source=user,
  review=0` → persistent injection into every turn.
- `permissions.py` `set_class` (med/low classes → auto: force-push, kill,
  chmod, container lifecycle) and `retrust` (re-blesses an out-of-band policy
  edit, defeating the tamper sidecar); `/api/config/import` restores both.
- `/api/claude/escalate`, `/api/onboarding/apply`, `/api/access`,
  `/api/prompt/budget` (`full` widens the agent's own toolset),
  `/api/update/apply|channel`, `/api/models/add|download|switch` (B03).
**Fix:** a per-launch nonce minted by the Swift app and injected into the
WebView (never written under `~/.hermes`), required on every approve / consent /
policy route; or route approvals through a native `NSAlert` the agent cannot
script. Fix the misleading Shortcuts message regardless.

**B03 — remote code via the model roster.** `add_model` accepts any string and
both launchers always pass `--trust-remote-code` (for mlx_vlm that sets
`MLX_TRUST_REMOTE_CODE=true`). add → download → switch with a repo whose
`tokenizer_config.json` carries an `auto_map` runs that repo's Python in the
model-server process, with no Hermes approval. `download_model` also fails open
when the size is unknown. **Fix:** make `trust_remote_code` an opt-in roster
field (off for the shipped Qwen entries unless measured to need it) and refuse
`/api/models/add` outside a curated list or behind the B02 nonce.

**B04 — Done hides the next message.** Message items are one id per
conversation (`message:<handle>`); `_ny_build` hides any item with `done_at`
set and never compares it with `received_at`; nothing clears `done_at`; and an
active conversation keeps refreshing `last_seen`, so the record is never pruned.
Mark Monday's "can you send it by 5pm?" Done and Tuesday's urgent message from
the same person never appears. Same for Snooze-past and Reclassify→never.
**Fix:** hide only when `done_at >= received_at` (and the same rule for snooze /
reclassify on message items).

**B05 — Scratchpad overwrite.** The hub textarea keeps its old `d.text` until
the 60 s `refreshHub`, and its input handler POSTs the *whole* textarea. Add a
paragraph in the pop-out (saved), close it, type one character in the hub box
within 60 s → the hub POSTs the old text + that character. Repro `notes.js`.
**Fix:** refresh the hub notes widget in `closePop`, and send a base revision
with each save so a stale editor is refused instead of winning.

**B06 — MCP privacy gate bypass.** `messages_search` is off by default and
`hermes_search` filters `message` rows for exactly this reason, but
`t_needs_you` prints `sender` + `summary` for every item, including message
items whose summary is the preview. Latent today only because Full Disk Access
is currently denied (Message Center empty); it leaks the moment FDA is
re-granted. Related: `hermes_search` still prints the `message N` match count,
and the allowlist parser uses `bool(value)` so `"false"`/`"off"` strings enable
a tool or write (`hermes_mcp.py:351,359`). **Fix:** drop `source=="message"`
items (and their counts) unless `messages_search` is enabled; accept only
`value is True`.

### P2 notes worth reading before fixing

- **B07** is reachable with one Remove + any restart. `get_layout()` filters
  `order` to ids already in `WIDGETS`, and aux files load sorted, so the
  re-inject save in `aux_claude_usage` runs before `foryou`/`needsyou`/
  `framing`/`trends` exist and drops them; foryou/needsyou come back at index 0,
  `framing`/`trends` (no re-inject) are gone. Fix: store a `seen` set in
  layout.json and inject only never-seen ids; never save from the filtered view
  at module load.
- **B12** was flagged as "improvement path 4" in the 2026-09-10 audit and is
  still open; now reproduced live: `[1]` to `/api/layout` and
  `Content-Length: abc` to an aux route both return no response
  (`curl` → `000`) with a `ValueError` traceback in `dashboard.log`.
- **B20**'s simplest fix is to remove the idle-suspend marker inside
  `_mlx_start()` whenever it returns True.
- **B22/B23** together mean a quiet rule log is full of `dedupe` rows while the
  real alert never fires again.

## UI findings (measured)

| ID | Pri | Finding |
|---|---|---|
| U01 | P2 | First load can show **"Widget unavailable."** on Needs-you / For-you until the 60 s refresh: `renderHub()` (`index.html:2846`) runs before the aux scripts that register those renderers, and `fillWidget` swallows the error silently. Observed live once; timing-dependent. |
| U02 | P2 | Markets pop-out prints **"+-0.01 · +0%"**: the sign comes from the rounded `pct` (−0.0 ⇒ "+") but prefixes the unrounded `chg` (`expand.js:1259`, same pattern `:71`). |
| U03 | P3 | Hub Markets card overflows by 8 px at the 900 px minimum window (`.quoterow` scrollWidth 191 > 183); % column touches the card edge. Percent decimals inconsistent ("−3%" next to "−3.26%"). |
| U04 | P3 | Hub header greeting wraps to three lines at 900–1000 px. |
| U05 | P3 | Agent view at 900×640: chat card stops ~130 px short of the bottom (dead band). |
| U06 | P3 | Preferences popover rows start at four different x offsets (17 / 27 / 41 / 43 px). |
| U07 | P3 | Model menu: "64 / GB" line break, 3–4-line rows in a 316 px menu, ~20 px Drill buttons. |
| U08 | P3 | Customize mode: 20×20 remove button collides with header meta (Markets "at close · Oct 8" wraps). |
| U09 | P3 | Hit targets below 28 px: For-you Useful/Noise (21 px), chat chips (27 px), `#cs-new` (26 px), remove (20 px). |
| U10 | P3 | Header Claude-Code pill reads only "· off" when narrow; 2.75:1 contrast in light theme. |

| U11 | P2 | Feed headlines lose every HTML entity: `_intel_clean` replaced `&…;` with a space, so PBS's "Nicol&aacute;s" rendered "Nicol s", `&#8217;` → "don t", `&amp;` → "AT T" (`aux_watchtower.py:1500`). Found live during the UI pass. |

No interactive controls overlap at either window size, no text overlaps, and no
horizontal page scroll at ≥ 900 px. The light/dark token sets are complete in all
four declaration blocks (the CLAUDE.md theme trap holds).

### Resolved in the working tree (uncommitted, same day)

**U01–U11 are fixed** and re-measured against the live dashboard at 900×640 and
1440×900: 0 overlapping controls, 0 interactive targets under 28 px (chips,
Useful/Noise, `#cs-new`, `.rm`, Drill now expand to 32–37 px hit areas without
overlapping), no header text wraps, Markets rows 0 px overflow with 2-decimal
percentages, Agent view fills the height (chat ends 592/640), all Preferences
labels share x = 41, the markets pop-out has no "+-" (sign helpers checked on
−0.01 / −0.004 / ±0 / 0.004 / null / NaN), no "Widget unavailable" on first
paint, no console errors. U11's fix (`html.unescape` after the tag strip) was
tested offline; it applies after the next dashboard restart + feed gather.
`tools/tests/run.sh unit` stays 2,251 / 2,251.

The same change set adds the **Liquid Glass motion** pass — new
`dashboard/aux_glass.js` + CSS in `index.html`: a pointer-tracked specular light
and rim on glass surfaces (one passive rAF-throttled listener, one layout read
per surface entered), gel press + spring release on controls and cards with a
2 px hover lift, a spring/stretch sliding thumb on the Hub/Agent switch, menus
and pop-outs that grow out of their anchor, and a scroll-edge header that
densifies when content passes under it. All of it is off under both
`prefers-reduced-motion` and the app's own Reduce-motion pref (verified: no
light, no popover animation). `aux_window.js`'s header observer now ignores the
light's own attribute writes, so the native drag band is not re-measured every
frame. Engine notes for the WKWebView check: the popover blur keyframe and the
`mask-composite` rim are the most WebKit-sensitive pieces (each is one CSS line
to drop if they misrender).

## P3 — low impact, hardening, polish (grouped)

**HTTP / persistence:** `/api/tasks`, `/api/layout`, `/api/access` still do
unlocked read-modify-write (the A01/A03 pattern), and task ids collide after a
same-second delete+add (`server.py:4333-4449`) · `/api/upload` check-then-open
race and "ok" on a truncated upload (`:4362-4376`) · `_save_chat_locked` moves a
healthy chat aside as `.corrupt-*` on any `OSError` (EMFILE), not just bad JSON
(`:2164-2191`) · deleting a conversation mid-turn resurrects it with only the bot
reply (`:4552`) · one malformed `chats/*.json` fails the whole sidebar, and every
call parses every chat (`:2289-2338`) · `layout.json` that isn't a dict breaks
`/api/hub` permanently (`:2714`) · Origin check accepts port-less
`http://localhost` (`:4059-4072`) · state-changing GETs reachable cross-site:
`/api/intel?gather=1`, `/api/google/auth_url` (overwrites the PKCE verifier),
`/api/foryou` · permissions audit log stores commands unredacted, mode 0644
(`permissions.py:509-548`).

**Model lifecycle / launchers:** `mlx-vlm-launch.py:143` exits 0 on every failure
and swallows import-level tracebacks (uvicorn still logs lifespan/port errors) ·
`_mlx_start` treats only "already…" as already-loaded; `Bootstrap failed: 5`
on a loaded-but-dead job may leave the model unwakeable (unverified on this
macOS) · `memory_guard_loop`'s pre-check can resurrect a model paused mid-tick;
`idle_suspend_loop` can write its marker after a concurrent pause (the two
markers coexist) · self-heal never fires while the bg lane is up
(`_mlx_proc_alive` matches both lanes) · idle-suspend can boot out a model
under a long Telegram tool run · download double-click spawns two
`snapshot_download`s; the headroom check doesn't credit bytes already on disk;
7200 s cap · `memory_guard_loop` body unguarded; `IDLE_SUSPEND_MIN=abc` stops
the dashboard starting; `idle_config` accepts `inf` · "Wake now" is a no-op
when the model is down without a marker · `launch-dashboard.sh` waits ~6 min for
a model the A09 gate refuses to start (and `tools/tests/run.sh:193` recommends
that script).

**Chat pipeline:** Approve is sent as `"approve"`, which Hermes' MCP-elicitation
path reads as decline (normal command approvals work) — map to `"once"` · a
definitive `prompt.submit` RPC error is treated as "already submitted", hiding the
real error · two pending approvals → the second is swallowed · `_cb_gate` refuses
benign questions ("Should I get a password manager…") and misses others
("Can you write me a full implementation…") · `_AR_DEEP` escalates any message
containing "opus" to the 600 s Opus call · deep answer persisted before the local
reply after a 10-minute wait · `_cb_recent_add` shared `.tmp` path race.

**Watchtower / needs-you / for-you:** evening wrap at ≥ 22:00 never sends under the
default 22:00 quiet hours and logs nothing · a failed Telegram send loses the
brief for the day with no log row (documented tradeoff; consider 2–3 bounded
retries) · For-You react handler rewrites the store from a stale read · reclassify
skips the Now cap of 5 · intel fetch reads unbounded bodies · `Infinity`/`NaN`
accepted by `_clamp_int` (500), snooze `until`, `idle_config`, evals `at_hour`,
trace `since` · draft reply: double click = two model jobs, a failed job's error
text is shown as the draft, an approval inside the draft job is never surfaced.

**Stores:** FTS `unicode61` doesn't segment CJK and `_ix_locate` can't highlight
underscore words · one slow icalBuddy call prunes the ±30-day calendar index to
today · non-atomic memlayer `review` migration · recorder cursor skips rows with
equal timestamps; same-named calls without ids collapse · pin ceiling race (13
pins; injection still caps at 12) · `/api/messages/ingest` returns 500 (not 403)
for a non-ASCII token and on `inf` timestamps.

**MCP / harness:** lone surrogates crash the MCP server (`_send`) or permanently
break `chat_get` on that chat (`_bound`) · `context_recent` is labelled
"primary lane" but merges both lanes · `HTTP_PROXY` is honoured for the loopback
URL · notifications (no id) still execute tools · `pinned="false"` pins ·
`config.yaml.bak-*` never pruned · one malformed tool-budget log row 500s the
whole payload.

**Update / install / release:** `update.sh` tarball path exits 0 on failure
(`trap 'cleanup; on_exit'` — `$?` is cleanup's), sources unquoted release-JSON
values before the checksum check, passes the bearer token in `curl` argv, and
`rsync --delete`s with no dirty check or rollback · main-channel
`update_available` stays stale for 6 h after applying · `install-services.sh`
bootstraps the model before the dashboard, so one "Bootstrap failed: 5" leaves the
dashboard down; plist written 0644 then chmod; paths not XML-escaped ·
`release.yml` interpolates step outputs into `run:` and uses a mutable
`softprops/action-gh-release@v2` in the signing job · on this Mac the checker
uses the private `origin` slug (documented), so the public repo's Releases are
never consulted here.

**Swift shell:** Quick Ask never retries a failed first load · any `localhost`
*port* stays in-app with the clipboard/window bridges · MessagesSync copies the
whole `chat.db` every 60 s and reports every open failure as "Full Disk Access
needed" · Swift / Python attributedBody decoders have drifted (truncated length
prefix, U+FFFD stripping, 200 vs 140 cap) despite the CHANGE-BOTH rule ·
`expand_messages` uses UTC midnight for "today".

**Other aux:** Claude-usage tokens aren't de-duplicated by message/request id
(likely inflated) · Google token used with zero expiry skew; DST-day range ·
onboarding apply: list/str `quiet_hours` 500s after partial apply, `idle_min:true`
= 1 min, bg model not size-checked · promotion drill doesn't wake or mark
activity · system sampler spawns 3 processes every 5 s with no UI open ·
`obsidian_sync` link/filename truncation mismatch.

**Frontend:** dictation toggle discards unsaved dictionary edits · You-model,
tool-budget, shortcuts and recorder error messages are wiped by the immediate
re-render · "Add person" stays disabled after one failed validation · Claude
dialogue overlay collapses every 12 s · stale-response races in history load,
trace range and memory preview · duplicate Clipboard sheet on double open ·
System pop-out keeps polling every 5 s after close · markdown: links with `)`
break, placeholder restore isn't recursive, ~8k nested `>` throws out of
`renderMd` (uncaught — aborts history render) · `cvWhen` shows "in 1d" for 2 h ·
search Enter opens the stale first result · `delchat` ignores failure · forced
scroll-to-bottom during streaming · Enter during IME composition sends ·
`#settings/__proto__` blanks Settings · Data & Network card renders escalation
**On** when its read fails (display only; backend enforces off).

## Checked and sound

Guard variants (trailing-dot / IPv4-mapped / short-form / `0.0.0.0` hosts,
`7788.evil.com` / userinfo / `null` / https origins, duplicate Host, absolute-form
URIs, HTTP/1.0) all refuse correctly · WebSocket codec (fragments, 64-bit lengths,
UTF-8 across frames, resumable timeouts) · JSON-RPC id matching and per-session
event filtering · FTS external-content sync and `_ix_tokens` sanitisation · every
osascript body is constant; every subprocess uses argv and a timeout · memory /
desktop / chat-id / static-JS path validation · stores 0600 · ingest token
`compare_digest`, fails closed · no XSS found in any widget, pop-out, Mind,
Console, model menu, sidebar or markdown (string-taint fuzz of every real payload
+ 26 hostile markdown cases) · no timer stacking · theme tokens complete · the
`datetime` aux gotcha is not currently breaking anything · escalation switch is
fail-closed on every backend path.

## Suggested order

1. **B01 + B02 together** (the approval surface): fix the stale box, make the
   server check approval state + identity, then add the launch nonce and gate
   approve / consent / policy routes on it. B03 rides on the same nonce.
2. **B04–B06** (silent misses and the privacy leak) — each is a few lines.
3. **B07, B12, B13, B14–B16** (layout loss, request validation, uploads, trash and
   dictation data loss).
4. The remaining P2s, then P3s in groups.
