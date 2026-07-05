# Capability Roadmap — agent skills (2026-07-05)

Built this run (in ~/.hermes/skills, author 'HermesAssistant (local)'): skill-forge,
hub-cartographer, mirror-check, cron-conductor, watchtower-author, osascript-cookbook,
screen-oracle, deep-dive. Full scored roadmap (incl. later tiers) below.

---

# Hermes Capability Roadmap

**Date:** 2026-07-05 · **Judge pass over 4 idea slates (33 raw ideas → 8 builds now, 24 deduped/tiered)**
**Mission:** "really give this agent the arms and legs to do some great work… If it doesn't have an ability, build a skill for it to enable it."

## Scoring method

Each idea scored 1–5 on **Wow** (does it feel like magic), **Buildability** (can we ship it today against the VERIFIED toolkit — no vaporware APIs; the original runbook taught us to distrust unverified values), **Safety-fit** (rides the invariants instead of widening them: manual approvals + tiers, notify-only watchtower, Gmail read-only, locked Telegram, local-first, no secrets in skills, no --yolo). Composite = W×B×S (max 125). A ★ marks **multipliers** — skills that make the other skills stronger; multipliers get priority over raw composite.

## Dedupe decisions

| Kept | Absorbed / merged | Why |
|---|---|---|
| **watchtower-author** | C3 + D7 (near-identical) | Same skill proposed twice — strongest sections of each merged |
| **cron-conductor** | D10 + A9 cron-gardener | Same job: good-cron template + fleet audit. One skill, two recipes |
| **mirror-check** | D3 + A3 mirror-mirror + D9 vitals-tuner | Post-task ritual, nightly journal, and vitals reading are one self-observability skill with three recipes |
| **deep-dive** | C5 fact-check *core* (adversarial confirm/disconfirm queries folded in as the Triangulate section) | fact-check remains a Tier-2 standalone for the forward-a-screenshot flow |
| **cua-app-pilot** (Tier 3) | A7 screen-shift-operator | Identical see-plan-click-verify protocol; merged, deferred (drills can't run unattended-safe) |
| **shortcuts-bus-driver** (Tier 2) | A8 shortcut-nightwatch (= bus-driver + cron-conductor) | Nightwatch is a composition, not a skill |
| **skill-forge** | D6 drill-sergeant | Adversarial delegate-testing becomes forge's mandatory self-test step, and every Tier-1 pack ships its own Drill section — the sergeant's doctrine lives everywhere instead of in one pack |
| **hub-cartographer** | C8 brief-composer's "cheapest source first" assembly order | Becomes a cartographer recipe |
| (superseded) | A6 morning-adjutant, C8 brief-composer | The dashboard already ships the 8am World Brief, Midday Pulse, and Breaking alerts — a skill duplicating them would drift. Revisit only as "brief-craft polish" |

## Tier 1 — build now (8 picks)

| # | Skill | From | W | B | S | Score | Why now |
|---|---|---|---|---|---|---|---|
| 1 | ★ hub-cartographer | D | 3 | 5 | 5 | 75 | The nervous-system atlas. Every other pick curls these endpoints; build it first and the rest get `hubctl` for free |
| 2 | ★ mirror-check | D+A | 4 | 5 | 5 | 100 | Turns "probably done" into "verified done." The trust ritual every other skill's recipes end with |
| 3 | ★ cron-conductor | D+A | 3 | 5 | 5 | 75 | Must exist BEFORE the fleet grows — deep-dive, watchtower-author and mirror-check all spawn crons; this keeps autonomy legible |
| 4 | watchtower-author | C+D | 5 | 5 | 5 | 125 | The "insane" move: research conclusions become standing tripwires. Zero new risk surface (notify-only by construction) |
| 5 | screen-oracle | B | 5 | 5 | 5 | 125 | "What am I looking at?" — instant daily magic, pure read path, exercises vision_analyze |
| 6 | deep-dive | C | 5 | 4 | 5 | 100 | Flagship analyst move: triangulated, citation-locked briefs; feeds watchtower-author and memory |
| 7 | osascript-cookbook | B | 4 | 4 | 4 | 64 | ★-adjacent: the substrate every future apple/ skill cites; turns computer-use sessions into one gated terminal call |
| 8 | ★ skill-forge | D | 5 | 4 | 4 | 80 | THE compounding multiplier — built last on purpose, so it has seven house-style exemplars to teach from |

**Slate coverage:** A → cron-conductor + mirror-check (merged in) · B → screen-oracle, osascript-cookbook · C → deep-dive, watchtower-author · D → hub-cartographer, skill-forge, mirror-check.

**The flywheel:** deep-dive produces conclusions → watchtower-author turns them into standing coverage → cron-conductor keeps the schedule honest → mirror-check verifies every run against the flight recorder → hub-cartographer is the substrate for all of it → skill-forge lets the agent mint the NEXT ability itself → osascript-cookbook + screen-oracle give the new abilities Mac hands and eyes. Each loop pass leaves the agent permanently more capable while riding existing rails.

## Build order

1. **hub-cartographer** (S) — foundation; ships `hubctl.sh`
2. **mirror-check** (S/M) — the verify ritual all later packs reference
3. **cron-conductor** (S) — before anyone creates a cron
4. **watchtower-author** (S) — first wow; uses hubctl + conductor's audit
5. **screen-oracle** (S) — second wow; independent, can parallelize
6. **deep-dive** (M) — flagship; hands off to watchtower-author
7. **osascript-cookbook** (M) — apple/ substrate
8. **skill-forge** (M) — caps the loop with 7 exemplars of house style to study

## Tier 2 — next wave (build when Tier 1 lands / blockers clear)

| Skill | From | W·B·S | Blocker / note |
|---|---|---|---|
| shortcuts-bus-driver | B2 | 4·4·5 | Bus is landing today — build the day `/api/shortcuts` is curl-verified live, not before (no specs against unverified APIs) |
| fact-check (standalone) | C5 | 4·5·5 | Core already in deep-dive; standalone adds the forwarded-screenshot → vision_analyze → verdict-ladder flow |
| night-shift-analyst | A1 | 5·4·5 | Pure composition: deep-dive + cron-conductor + a charter § in MEMORY.md. Nearly free after Tier 1 |
| paper-radar | C4 | 4·4·5 | Same composition pattern, site-scoped (arxiv/HN/GitHub) + dedupe ledger |
| intel-wield | C6 | 4·4·5 | Longitudinal /api/intel reads; partially covered by cartographer's cheapest-source-first rule |
| ticker-autopsy | C2 | 4·5·5 | Keyless Yahoo endpoints already trusted by the markets widget; explicit never-suggests-trades rule |
| memory-gardener | D4 | 3·5·5 | §-discipline pass; snapshots make it reversible; wait until memory has enough entries to garden |
| spotlight-librarian | B5 | 3·5·4 | mdfind/mdls recipes; trash-not-rm convention |
| safari-concierge | B9 | 4·4·4 | Depends on osascript-cookbook's tabs.jxa + one-time Safari TCC grant |
| skill-scout | D5 | 4·4·5 | The acquisition arm; needs skill-forge live first, and /api/mind_drill demand data to rank gaps |

## Tier 3 — bigger lifts / later

| Skill | From | W·B·S | Why deferred |
|---|---|---|---|
| cua-app-pilot (abs. screen-shift-operator) | B4+A7 | 5·3·3 | Highest trust payoff but drills require attended runs; build after screen-oracle proves the vision loop |
| shortcut-smith | D8 | 5·2·3 | Agent builds Shortcuts via computer-use — L effort, needs cua-app-pilot discipline first |
| competitor-dossier | C7 | 5·3·5 | L; wants deep-dive + watchtower-author + cron-conductor all mature |
| project-foreman | A4 | 5·3·4 | L; kanban-driven multi-week projects — needs cron-conductor + mirror-check habits proven |
| downloads-quartermaster | A2 | 4·4·5 | Good plan-then-approve exemplar; fold into spotlight-librarian's janitor patterns |
| follow-up-concierge | A5 | 4·4·5 | = watchtower-author + a followups § ledger + a daily cron; composition after Tier 1 |
| media-forge | B6 | 4·3·4 | ffmpeg brew-install gate; sips/textutil recipes could ship earlier as a slim v0 |
| mac-medic | B7 | 4·4·4 | Solid; overlaps the hub's live System widget — scope to diagnosis-and-interpretation only |
| window-butler | B8 | 4·3·4 | System Events geometry is fiddly; needs osascript-cookbook + attended TCC grants |
| morning-adjutant / brief-composer | A6/C8 | 3·4·5 | Superseded: 8am World Brief + Midday Pulse + Breaking alerts already built into the dashboard |

## Build rules (invariants, operationalized)

Every Tier-1 pack MUST contain these sections — this is the house style skill-forge will later enforce:
- **Safety & Approvals** — names which steps are READ (pass gates) vs MUTATE (approval-tiered); never teaches gate avoidance; cites the tier system by name so invariants are *taught*, not just enforced.
- **Drill** — a concrete pass/fail invocation safe to run unattended: no real sends, no destructive ops, no --yolo (hard-forbidden; classifier blocks it).
- **Gotchas** — at minimum: 12-hour time in ALL user-facing output (`%-I:%M %p` — established dashboard convention); §-memory format (`\n§\n`-joined entries, etag GET-before-PUT, byte-identical rewrites); escaping doctrine (heredocs over inline `-e` quoting, JXA JSON over AppleScript comma-soup, single-quoted curl `-d` bodies).
- **No secrets** — validate_skill.py secret-scan hard-fails on tokens/keys/home-paths before any pack ships.
- **Recorder rule** — never claim "done" without checking /api/recorder.
- **7-day review** — the curator prunes at 7d; every new pack gets a kanban review card at birth.

## Drill doctrine (how every build gets verified)

1. **Model check first:** Qwen3-30B-A3B must be active. Hermes-3-8B does NOT reliably tool-call — it deflects and fakes success (docs/FINDINGS.md), which would green-light broken skills.
2. **`hermes -z` is for read-only drills only.** Under manual approvals, oneshot runs fail closed on approval-needing writes AND can fake approval success — so -z drills must touch nothing gated.
3. **Hub chat is the approval surface.** Anything needing a grant runs via POST /api/chat → poll → /api/chat/approve (choice approve|deny) with the builder watching. Exact param names: verify against ~/HermesAssistant/dashboard/server.py before scripting.
4. **No real sends** in drills — no `hermes send`, no Telegram, no watchtower test_rule that fires a live alert without the user forewarned.
5. **Verify recipes against source, not memory** — endpoint shapes come from dashboard/server.py + aux_*.py, cron syntax from `hermes cron --help`. The original runbook's fabricated values are the standing lesson.
