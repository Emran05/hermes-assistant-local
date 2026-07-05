# EXECUTION PLAN — runway to 2026-07-06 night (judged synthesis, written 2026-07-05 ~2:45am)

Ground truth used: DEVPLAN.md, CHANGELOG-staging.md (11 unpushed commits; Jul 5 8am fire
suppressed, auto-resumes Jul 6), FINDINGS.md (Qwen3-30B default, answered), NEEDS-YOU.md,
CLAUDE.md, docs/plans/p2-4-message-center.md (cdhash rebuild DROPS the FDA grant — spec
lines ~375-376), docs/plans/p2-5-google-oauth.md.

## Verdict

Scores /10 on: deadline realism · quality-gate fidelity · user-step efficiency · felt value
by deadline · risk (10 = low risk).

**Plan A (finish-p2-first): 6 · 9 · 8 · 6 · 5 = 34/50.**
Best gate discipline and the only plan that remembers P2.6a multi-day drill-downs (the
forgotten half of DEVPLAN Phase-2 #6) and the DEVPLAN §7 tag/rollback rhythm. But it spends
~11h of a 40h runway on invisible plumbing (P3.2 router, P3.4 spec-decode), carries the two
riskiest shared-file touches (hermes_rpc.py, mlx-server.sh) into the deadline night, and
anchors its Brief gate on an 8am Jul 5 fire that the changelog says is suppressed.

**Plan B (unblock-user-parallel): 9 · 7 · 9 · 8 · 9 = 42/50. WINNER.**
Correct organizing insight twice over: (1) one ~25-min user sitting unblocks every
grant-gated surface, so build everything to grant-ready first; (2) the cdhash/FDA constraint
means main.swift must be final BEFORE the grant — freeze it after Wave A. Best buffer
(~25%), cuts exactly the highest-risk/lowest-felt items, and the "away-case floor" makes the
plan robust to the user never sitting. Weaknesses: drops drill-downs (honest Phase-2
completion) and skips P3.2 without recording the DEVPLAN reframe that legitimizes the skip.

**Plan C (demo-value-first): 8 · 4 · 7 · 8 · 6 = 33/50.**
Right about dress rehearsals and a demo-day hardening freeze. But it violates the user's
chosen quality-gated in-order policy (Phase-4 voice ahead of Phase 3; un-defers the brief
formatting the user explicitly deferred), cuts the Shortcuts action-bus (Value 5 in DEVPLAN
— the top felt-value P3 item), and gets ground truth wrong (claims 7 Phase-1 commits; there
are 11 unpushed including P2 work).

**Synthesis: Plan B's skeleton** + grafts from A (P2.6a drill-downs; Phase-2 close-out drill
+ v0.1/v0.2 tags; spec-first rigor for P3 builds; push-batch-1-as-rollback-anchor logic;
P2.5 open-question resolutions; bounded-research-loop gate; 8B-must-fail gate truth) + from
C (dress-rehearsal brief pushes through the identical code path; Jul 6 2pm code freeze +
demo-path rehearsal; risk-register framing). P3.2's skip becomes legitimate by recording the
reframe in DEVPLAN: the MoE-profile half is shipped-by-decision (Qwen3-30B is the drilled
default); the sub-2B router is deferred, not silently dropped.

## The plan

Policy throughout (unchanged): agents build self-contained aux modules; ORCHESTRATOR alone
touches shared files (index.html, main.swift, hermes_rpc.py, mlx-server.sh, permissions.py,
models.json, docs). Every workstream = 1 local commit + 1 CHANGELOG-staging line + NEEDS-YOU
update. Builds may parallelize; **verify gates close in DEVPLAN order** (2.4 → 2.5 → 2.6a →
3.1 → 3.3). No push without explicit go-ahead. --yolo never; Gmail send never; Telegram
locked; notify-only until P3.1's gated bus. ⌘R after every index.html change; datetime-alias
check on every new aux module.

### Wave 0 — land World Brief v2 (now → ~6am Jul 5) — in-flight agent, exclusive aux_watchtower.py owner
- Gate: dry-run compose shows links on every item, after-hours markets, populated AI & Labs
  section; hourly research loop is BOUNDED (cooldown + cache, no runaway web_search spend)
  and survives 3 consecutive cycles clean in dashboard.log; degrades deterministic with
  model paused; schema still refuses action/command/chat_id/target.
- Live-fire decision: Telegram delivery is already verified, so IF the gate passes by
  7:30am, un-suppress today's 8am fire and let Jul 5 8am deliver v2 live (a free full dress
  rehearsal at the normal hour). If not gated by 7:30am, leave Jul 5 suppressed — never risk
  the daily delivery on an unverified merge. Final gate either way = the armed 8am Jul 6 fire.
- Stays deferred inside it: prettier structured formatting (user's call stands).

### Wave A — grant-ready everything (now → ~10am Jul 5) — 4-5 agents parallel, disjoint files
- **A1  P2.4 dashboard half** (agent, 3-4h): aux_messages.py + aux_messages.js per spec
  option (a). Gate: full curl matrix (never-synced / synthetic ingest round-trip incl. (555)
  prettify / 403 wrong-token store-untouched / fda:false→grant card / stale badge / 400+413
  clamps); token file 0600; headless node eval of all degradation states.
- **A2  P2.4 Swift half** (agent, 3-4h, EXCLUSIVE main.swift owner): MessagesSync (SQLite
  backup-API snapshot, ported queries, attributedBody byte-scan, Apple-epoch convert, 60s
  POST) + external-scheme opener; rebuild via build-app.sh. Gate: compiles, installs,
  launches clean; WITHOUT FDA it POSTs {fda:false} and the grant card renders (proves the
  whole pipe pre-grant); tmp snapshot unlinked; zero chat.db opens from python in
  dashboard.log. **Then FREEZE main.swift for the rest of the runway** (cdhash rebuild drops
  the FDA grant; any later Swift change costs a second user sitting).
- **A3  P2.5 Google stack** (1-2 agents, 4-6h): google_oauth_driver.py + SAFE_SCOPES /
  FORBIDDEN_SUBSTR scope wall + offline scope-guard unit test (forged gmail.send grant →
  revoke + refuse, NO token written; clean scopes → token written — non-negotiable gate);
  aux_google.py routes + status cache + today-widget provider wrap; google_draft.py
  (drafts.create only); send-neuter + self-heal (HERMES-NOSEND sentinel, pre-image
  snapshotted + recorder-logged); aux_google.js Mind card. Orchestrator resolves spec open
  questions as: calendar.events scope (writes already approval-gated), neuter+wrapper both,
  email via gmail.getProfile. Gate (all offline): scope-guard green; client_secret 400/422/413
  matrix; status fast-path <20ms; grep proves no reachable messages().send outside the
  neuter stub; neuter idempotent across two restarts; icalBuddy fallback untouched when
  disconnected; clean [aux_google] load on restart.
- **A4  P2.6a multi-day drill-downs** (agent, 2h): small aux module, 30-day views over
  existing tokens_by_day/skill_usage via /api/mind_extra. Gate: 30-day view renders headless
  with live data; no new 500 paths. (Without this, Phase 2 isn't honestly complete.)
- **Orchestrator integration checkpoint (~9-10am):** all index.html tags in ONE edit,
  restart dashboard, ⌘R, full smoke (all aux modules load clean, hub renders, one chat turn,
  one approval card), one commit per workstream, CHANGELOG lines. Gates close in order
  2.4 → 2.5 → 2.6a.

### Phase-2 close-out (~10am → noon Jul 5) — orchestrator only, ~1.5h
- Full drill: 3 launchd services + gateway bootout/bootstrap restart-verified; hub renders;
  one real chat turn; one approval round-trip; ⌘R in app.
- CLAUDE.md updated (Message Center pivot; Google moves to "consent-pending"); NEEDS-YOU
  rewritten as the single-sitting script (below); retro-tag **v0.1** at the Phase-1 boundary
  commit, tag **v0.2** "Phase 2 complete — Initiative" locally.

### USER SITTING — once, ~25-30 min, whenever the user wakes (scripted in NEEDS-YOU.md)
1. FDA grant to Hermes Assistant.app (AFTER the A2 freeze — order matters) → Message Center
   live. 2. Google Cloud console + browser consent (~12-15 min) → Gmail read/draft + Calendar
   live. 3. Calendar TCC for framework python (1 min, keeps icalBuddy fallback honest;
   optional once Google connects). 4. Menu-bar / ⌃⌥Space / ⌘⇧V / Trust panel / memory-editor
   eyeball QA (3 min). 5. **Push batch 1 go/no-go.** 6. If Wave B is ready: approve one live
   bus action (+ import .shortcut files only if the machine has <5 useful ones).
Nothing in the build queue blocks on this sitting; it only converts grant-ready → live.

### Wave B — pure-code Phase 3, spec-first (Jul 5 ~noon → ~8pm) — 2-3 agents
- **B1  P3.1 Shortcuts action-bus** (spec 1h → build 4-5h): write
  docs/plans/p3-1-shortcuts-bus.md first (same rigor as p2-4/p2-5). Curate from the user's
  existing `shortcuts list` FIRST (avoids a user import step); ship .shortcut files + a
  NEEDS-YOU line only if <5 useful exist. aux_shortcuts.py enumerates/runs via `shortcuts`
  CLI riding the terminal-tool seam the permission engine already intercepts —
  hermes_rpc.py NOT touched; new action-classes only if needed (orchestrator merges
  permissions.py policy). Every invocation → recorder.db. Gate: tier-correct behavior
  live-drilled through real Qwen3-30B turns exactly like P1.4 (ASK→card→deny blocked;
  AUTO runs; NEVER auto-denied); recorder rows for every bus action; refusal on unknown
  shortcut; command_allowlist interop unchanged.
- **B2  P3.3 per-model parsers + promotion gate** (3-4h, after B1's gate closes): fallback
  parsers, canned tool-calling eval wired into the switcher, license + per-model
  KV-precision fields in models.json (orchestrator edit). Gate has built-in ground truth:
  **Hermes-3-8B must FAIL** (known deflector per FINDINGS.md — if the gate passes it, the
  gate is wrong); Qwen3-30B passes; "Built with Llama" attribution lands in the Mind/About
  surface (DEVPLAN risk table).
- **B3  Prefix-stable system-prompt audit** (1.5-2h, filler, DEVPLAN §4 continuous track):
  move volatile bytes to the prompt tail; TTFT before/after recorded in metrics.jsonl; no
  drift-detector trips; one chat turn + one approval still work.
- **DEVPLAN reframe commit:** P3.2's MoE-profile half recorded as shipped-by-decision
  (Qwen3-30B drilled default, user-confirmed); sub-2B restraint router explicitly deferred
  with reasons. This keeps the in-order policy honest while skipping the router.

### Wave C — grant conversion + hardening (post-sitting → Jul 6 evening)
- **C1** Live verification of P2.4 + P2.5 (1-2h): real conversations render ≤60s post-grant,
  zero chat.db opens from python; Gmail search returns JSON on the minimal token; draft
  visible in Gmail Drafts; `gmail send` refuses; today widget shows real Google events. Do
  NOT drill disconnect/reconnect — don't burn the grants.
- **C2** Google agenda → Brief "your day" section + calendar-gap trigger stub → live
  evaluator (2-3h, same aux_watchtower.py owner, after Wave 0 landed). Gate: dry-run brief
  with real events; gap trigger fires in test-preview; notify-only schema still refuses
  actions. Then a **manual dress-rehearsal Telegram push Jul 5 evening** through the
  identical code path — the exact artifact that fires at 8am Jul 6, read on the phone,
  fix what feels wrong.
- **C3** Jul 6, 8am: observe the armed fire (final Brief gate). Fix-forward window all
  morning.
- **C4** Jul 6 hardening (3-4h): full restart-verify (3 services + gateway), one undo drill,
  config-as-code export → fresh state-snapshot.json committed, metrics review vs DEVPLAN §6
  (TTFT p50, approvals, zero irreversible incidents), CLAUDE.md + DEVPLAN + NEEDS-YOU
  updated to reality, tag **v0.3** locally. **Code freeze Jul 6 ~2pm** — nothing new ships
  after; the afternoon rehearses the full demo path twice and absorbs overruns.

Load: Wave A ≈ 14-16 agent-hours across 4-5 agents (done by ~10am); Wave B ≈ 10-12h across
2-3; Wave C ≈ 6-8h. Roughly 20-25% of the runway stays buffer.

### Away-case floor (if the user never sits)
Still delivered by Jul 6 night: Brief v2 live at 8am with research loop + links; action-bus
live-drilled on existing shortcuts; promotion gate with the 8B failure as proof; TTFT prefix
win; P2.4/P2.5 fully demo-able via synthetic ingest + all degradation states. The sitting,
whenever it happens, is pure grant-clicking with zero build wait. NEEDS-YOU.md is the
deliverable in that world.

## Cut list

1. **P3.2 sub-2B restraint router** — the valuable half (MoE default) is already live and
   drilled; the router is M-effort where routing bugs look like model bugs and needs eval
   soak the runway lacks; zero felt delta at TTFT p50 965ms. Highest regression-risk-per-hour
   remaining. Reframe committed to DEVPLAN so the skip is recorded, not silent.
2. **P3.4 speculative decoding** — touches mlx-server.sh, the one file whose breakage kills
   the always-on assistant overnight; marginal win vs an already-green 965ms p50; B3 buys
   latency at near-zero risk. First candidate for the next runway: on a short-lived branch,
   only after push batch 1 exists as a remote rollback anchor.
3. **P3.5 screenshot-grounded computer-use** — evidence polish, not felt value in 36h;
   third-party MCP supply-chain vetting cannot be rushed (DEVPLAN risk table).
4. **ALL Phase 4** — voice (despite pre-approved downloads: latency tuning is fiddly and it
   jumps the phase order), agent-authored widgets, /learn, corpus RAG. Each M/L with no
   spec; starting any guarantees a half-done stream. Next runway's Block A, spec-first.
5. **Prettier brief formatting** — user deferred; stays deferred (Plan C's un-defer rejected).
6. **Research-loop expansion beyond v2's gate** — scope freezes at the gate.
7. **P2.4 reboot-survival drill** — verify across app relaunch instead; a mid-runway reboot
   risks the whole service stack for one checkbox. Reboot check rides the next natural reboot.
8. **Opportunistic refactors** (dead w_messages bodies, bare-datetime imports in
   aux_config/aux_recorder — CLAUDE.md says tolerate) and everything DEVPLAN marks
   LATER/NEVER, specifically --prompt-concurrency, kv4 quant, native vibrancy,
   verification-gated tasks, Discord.

## Push batches (executed only on explicit go-ahead)

- **Batch 1 — ask at the sitting (first waking contact Jul 5):** the 11 existing commits +
  Wave 0/A + close-out, with tags v0.1 (retro, Phase-1 boundary) and v0.2. Pre-push gate:
  aux_config secret-scan on the diff; .gitignore covers messages-token / store.json /
  google_* / serve-token paths; services restart clean. Rationale: (a) cleanest rollback
  point we'll ever have (DEVPLAN §7: a release = a rollback point); (b) 15+ unpushed commits
  on the machine that IS the product is single-disk risk we shouldn't carry another 24h.
- **Batch 2 — Jul 6 evening at the deadline:** Wave B + C commits after live-grant
  verification and the real 8am fire, tag v0.3. If any workstream's gate is red or
  mid-flight when asked, push through the last green gate only — per-workstream commits
  make that surgical. Never push mid-workstream; never push a red gate; tags ride with
  their batch.

## Risks

1. **8am Jul 6 fire mis-fires** (the demo moment) → up to two prior fires through the
   identical path (Jul 5 8am if Wave 0 gates by 7:30am; Jul 5 evening dress rehearsal with
   real calendar), plus the fix-forward morning window.
2. **App rebuild after FDA grant silently kills Message Center** → main.swift frozen at the
   A2 gate; nothing in Waves B/C touches Swift; if an emergency rebuild is unavoidable, the
   re-grant is a 30-second NEEDS-YOU line, not a crisis.
3. **User never sits** → away-case floor above; no build blocks on grants.
4. **Action-bus half-drilled = trust liability** → the drill IS the gate; if drills can't
   finish before the Jul 6 2pm freeze, the bus is held from push batch 2 and ships next
   runway (never a half-drilled action surface).
5. **Google consent friction** (403 access_denied / testing-mode) → card surfaces exact fix
   links per spec; icalBuddy fallback degrades gracefully, not visibly.
6. **Parallel-agent integration friction** → aux-module pattern keeps builds disjoint;
   orchestrator applies index.html tags in one edit; datetime-alias check + ⌘R every time;
   gates still close serialized in DEVPLAN order.
7. **Single-disk exposure until batch 1 lands** → ask for the push at first waking contact,
   not at the deadline.
