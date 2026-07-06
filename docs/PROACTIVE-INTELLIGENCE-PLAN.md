# Hermes Proactive Intelligence Plan — the "Think For Me" layer

*Chief-architect synthesis. Grounds four research streams (proactive-agent-patterns,
opportunity-people-discovery, you-model-personalization, always-on-engineering) in the
live Hermes stack. Author: architecture pass, 2026-07-05.*

Every workstream below **extends a module that already exists** — no new runtime, no cloud,
no new framework. The through-line, borrowed from the always-on-engineering research:
**decouple loop frequency from interrupt frequency.** We run the *thinking* as often as the
Mac safely allows; a learned, decision-theoretic gate decides the tiny fraction worth the
user's attention.

Real modules this plan touches (verified in the checkout):
- **Watchtower + World Brief v2 + Hourly Intel** — all live in `dashboard/aux_watchtower.py`
  (rule engine `_evaluate`/`_wt_gate`/`_fire_rule`; brief `_brief_build_sections` over
  `_BRIEF_HEADERS`; intel `intel_loop`/`_intel_gather`/`_intel_curate`; midday `_midday_tick`;
  breaking `_breaking_pass`; reactions `_op_mark_reaction`).
- **Memory** — `dashboard/aux_memory.py` CRUD over `~/.hermes/memories/*.md` (`\n§\n`
  entry delimiter; `/api/memory/{list,file,create,save,delete,restore}`; snapshots + trash).
- **Google (soon-live)** — `dashboard/aux_google.py`, read-only `gmail.readonly` + `calendar`
  + People API, send is *architecturally impossible*.
- **Message Center (soon-live)** — `dashboard/aux_messages.py`, chat.db → `messages.json`.
- **Cron** — `hermes cron {create,run,tick,list,...}`, jobs in `~/.hermes/cron/`.
- **Safety rails** — `mlx_admission()` + `memory_guard_loop` (`MLX_SOFT_GB=50`/`MLX_HARD_GB=56`)
  in `server.py`; 17-class approval tiers in `dashboard/permissions.py` (aux_trust);
  Telegram gateway locked to user `8487169327`.

---

## 1. Vision & the leap: world-generic → you-personalized

Today Hermes is a superb *world* instrument. The 8am World Brief v2, the Midday Pulse, the
14-feed Hourly Intel loop and the Watchtower trigger engine all answer the question **"what is
happening?"** with real, cited, cross-checked items. What none of them answers is the question
the user actually asked for: **"what is happening *that matters to me*, and what should I do
about it?"** There is a model of the *world* (`intel.json`, market/RSS providers) but no model
of the *user* — no goals, no active projects, no people, no standing wants — and therefore no
reasoning that connects a world event to *this* life and turns it into a concrete move.

The leap is to add exactly two things on top of the machine that already exists: a **You-Model**
(a small, editable, local model of the user's goals / projects / people / "looking-for") and a
**reasoning loop** that joins every incoming signal to that model and emits a ranked *move*
("do X / meet Y / go to Z — because it advances your goal G / because person P just did Q").
Everything else — the feeds, the brief, the Telegram channel, the approval gates, the memory
ceiling — is reused. The World Brief stops leading with *"what happened"* and starts leading
with *"why this is for you."* The Hourly Intel loop stops curating a generic AI front-page and
starts tagging each item with the goal or person it touches. Watchtower stops firing on
*"AAPL moved 3%"* and starts firing on *"AAPL moved 3% and you hold a thesis on it / your
contact just tweeted about it."*

The strategic bet, confirmed across all four research streams, is that this is a *local-first*
product no incumbent can match. Dex/Clay/Nat do reconnect-only, in the cloud, on pull. Poppy
(the closest competitor, TechCrunch 2026-05) runs on cloud LLMs and only *aspires* to on-device.
Rewind/Limitless proved the local-first passive-memory model, then sold to Meta and shut the Mac
capture off (Dec 2025) — the cautionary tale. Hermes already sits where all of them wanted to be:
local Qwen reasoning over the user's *real* private context (iMessage/Gmail/Calendar), nothing
leaving the Mac, guarded by a hard memory ceiling. The un-copyable output is the **local join of
live public opportunity × the user's private network × their interest facets, pushed proactively.**
Every competitor ships one slice of that; none fuses it, and none can, on privacy grounds.

---

## 2. The You-Model

### 2.1 Where it lives (and why NOT in USER.md)

`~/.hermes/memories/USER.md` is capped at **1,375 chars** (`CORE_FILES` in `aux_memory.py`) —
by design it is the tiny, stable semantic core (identity, hard preferences, the news-verification
rule already there). The You-Model is far too big for it. Instead it becomes a set of **typed
topic files in the same `~/.hermes/memories/` directory**, which `aux_memory.py` already
renders, versions (snapshots + trash), char-meters, and edits from the Mind view for free — zero
new storage code. Each file uses the existing `\n§\n` entry delimiter so every entry is an
independently editable card.

| File | Cadence | Contents | Research basis |
|---|---|---|---|
| `USER.md` *(exists)* | ~never | identity, location (Hoboken/NYC), hard prefs | semantic core |
| `GOALS.md` *(new)* | quarterly | explicit objectives + time horizon | goals beyond profile facts (PersonaTree, 2510.07925) |
| `NOW.md` *(new)* | weekly | "currently working on" — active projects | highest-value freshest field for matching |
| `LOOKING-FOR.md` *(new)* | open loops | people to meet, roles to hire, things to buy, questions | each entry = a **standing subscription** (§3) |
| `INTERESTS.md` *(new)* | slow drift | topics, each with a weight + last-seen date | multi-interest facets (MIND / 2310.18608) |
| `PREFERENCES.md` *(new)* | rare | tone, when to interrupt, what counts as noise | feeds the interruptibility gate |

**People-graph.** One markdown card per person under `~/.hermes/memories/people/<slug>.md`
(mem-agent / PKG "spiderweb" convention: relationship, what they care about, last contact, open
threads, `[[wikilinks]]` to goals). A derived index `~/.hermes/dashboard/people-graph.json`
(strength scores, computed nightly — §4) is the machine-readable half. This requires a one-line
relaxation of `_mem_valid_name()` to permit the `people/` subdir; the CRUD, snapshot and editor
paths are otherwise unchanged. **Timestamp every fact and every edge** (Graphiti's one borrowable
idea) so stale facts decay and "what changed" is answerable.

### 2.2 How it's elicited

**Active — an adaptive onboarding interview the agent runs in chat.** Not a 20-question wizard.
Following Bayesian preference elicitation (2403.05534), the agent *seeds priors first* from
whatever context is already available (Gmail/Calendar/iMessage once granted; otherwise the
existing `state.db` session history + the Telegram conversation), then asks only the highest-
uncertainty questions — ~10 minutes, producing the initial `GOALS.md` / `LOOKING-FOR.md` / people
cards. Delivered as a normal Telegram/dashboard conversation; writes go through the same gated
memory path as everything else (§7). This is the single fastest lever to the first magic moment
(§6) because it needs *no account grant*.

**Passive — the Mem0 ADD/UPDATE/DELETE/NOOP loop (2504.19413).** A nightly reflection cron reads
the day's episodes (`state.db` messages/tool_calls), `messages.json`, and — once live — Gmail/
Calendar deltas, extracts salient facts, and proposes each as ADD/UPDATE/DELETE/NOOP against the
existing You-Model files. Mem0 reports ~90% token-cost and 91% p95-latency reduction vs. stuffing
full history — directly relevant to staying under the 50GB ceiling. Crucially, per the HEARTBEAT
paper (2603.23064), these autonomous writes are **proposed, not silently applied** (§7).

**Consolidation.** The same nightly cron does the Generative-Agents reflection move: merge
duplicate people cards, decay stale interests, re-derive `NOW.md` from the week's activity. This
is the "thinks while you sleep" engine, and its output *is* tomorrow's brief.

### 2.3 How it's edited in the dashboard

The Mind view already ships a "What it remembers" card backed by `/api/memory/*`. The new typed
files appear there automatically. We add one dedicated **"Your Model" card** (a thin view over the
same endpoints) that groups Goals / Now / Looking-For / Interests / People with the char-meter,
snapshot-restore and delete controls that already exist — delivering the *"you own and can delete
what's known about you"* surface that is Hermes's post-Rewind moat, for nearly free.

---

## 3. The reasoning loop: "world signal + You-Model → a ranked move"

This is the missing core. The shape is Generative Agents' retrieve→reflect→plan (2304.03442) —
model-agnostic, runs fine on the resident Qwen3-30B — implemented as a **two-tier funnel** so the
always-on part stays cheap and the expensive part stays rare (respects the MLX ceiling).

New module **`dashboard/aux_foryou.py`** (mirrors `aux_watchtower.py`'s structure: registers
`/api/foryou/*` via `register_get`/`register_post`, runs a daemon started in its handler-init,
reuses `_model_chat_url()`, `_wt_gate`, `_deliver`, `_op_mark_reaction`).

**Cadence — a 3-tier clock (always-on-engineering §1):**
- **Cheap event pass** (event-driven, near-free): on a new inbound iMessage, a Calendar change, or
  a Watchtower rule firing, run a *short* classification — "did anything change that touches the
  You-Model?" Tight prompt, not a 30B essay. This is where responsiveness lives.
- **Adaptive heartbeat**: fold into the existing `intel_loop` (`INTEL_INTERVAL=3600`), but back
  off when nothing changes (markets closed, quiet hours) and tighten in active windows.
- **Scheduled heavy synthesis, 2–3×/day** (`hermes cron` + the 8am `_brief_compose` / 3pm
  `_midday_tick`): the one place we spend the full context budget on deep "world→user→action"
  reasoning. Serialized (concurrency-1), admission-gated, queued — never continuous.

**The match, per item (two tiers, cheap→expensive):**
1. **Embedding pre-filter (no LLM, runs on everything).** Maintain a "preference center" — the
   normalized aggregate of `GOALS.md`/`INTERESTS.md`/`LOOKING-FOR.md` embeddings, cached locally,
   *one vector per interest facet* (multi-interest, not one blob — a single user vector destroys
   recall). Score each `intel.json` item / candidate by cosine to its *nearest* facet. Cheap,
   deterministic. (Treat each `LOOKING-FOR.md` entry as a subscription; each incoming item as a
   publication — semantic pub/sub, 2605.25701.)
2. **LLM match+reason on survivors only (~top-20/day).** For items over threshold, one Qwen pass
   (same chat-completion path `_intel_curate` already uses) answers *"why THIS user, which goal or
   person, and what is the one concrete action?"* — connecting the event to a specific
   `matched_goal` / `matched_person` and proposing **do X / meet Y / go Z**.

**Schema change (small):** `_intel_curate()` output and stored items gain
`{matched_goal, matched_person, suggested_action, tier, interrupt_score}`. Nothing else in the
pipeline changes shape.

**Scoring — "worth surfacing" (always-on-engineering §2, Horvitz):**
```
interrupt_score = P(user_acts)          # relevance to the modeled goals/network
                × value_if_true         # magnitude of the opportunity
                × time_decay(freshness) # perishability: a meetup tonight >> evergreen
                ÷ ECI(now)              # expected cost of interrupting right now (§5)
```
`P(user_acts)` starts as the embedding+LLM relevance and is progressively replaced by a learned
`P(useful)` classifier trained on the `useful/noise` reactions already collected by
`_op_mark_reaction` (the accept/reject label stream the ProactiveBench paper pays annotators for).

**Output shape (the contract):** three bands routed by score, mapped to LangChain's
notify/question/review tiers (which already map onto the 17-class approval system):
- **pierce now** → Telegram immediately (only high-score, low-ECI, perishable, corroborated).
- **defer to next breakpoint** → bounded deferral into the Midday Pulse / 8am brief.
- **digest only** → dashboard "For You" panel, never pings.

The canonical daily output, rendered in the brief and the panel:
> **3 things for you today**
> 1. **Do:** apply to the Grants.gov SBIR that opened today — matches your `NOW.md` "local-first AI" project. *[link]*
> 2. **Meet:** Jane K. is hosting a Thursday lu.ma in Manhattan on on-device inference (your `INTERESTS` facet); your contact Sam knows her — warm intro available. *[link]*
> 3. **Reconnect:** you told Alex you'd intro them 3 weeks ago and never did; they just shipped something relevant. *[thread]*

Every surfaced move logs its `useful/noise` reaction back into the memory stream, and a **nightly
reflection cron** re-derives the You-Model — the loop closes.

---

## 4. Sources: opportunities / people / events

### 4.1 Opportunity & people feeds — drop straight into `_intel_feeds()`

`_intel_feeds()` in `aux_watchtower.py` already fetches 14 keyless RSS feeds hourly. Add these
**keyless, no-account, RSS/JSON** sources today — no new capability required:

| Source | Endpoint | Key? | Value |
|---|---|---|---|
| HN "Who is Hiring" | `hnrss.org/whoishiring/jobs?q=<facet terms>` (+`/hired`,`/launches`,`/show`) | **keyless** | pre-filtered opportunity firehose; `?q=` narrows to the user's stack |
| HN Algolia | `hn.algolia.com/api/v1/search` | **keyless** | topic monitoring / "every HN mention of X" |
| Grants.gov | `v1/api/search2` (no-auth REST) + category RSS | **keyless** | funding matched to `GOALS.md` domains |
| GitHub activity | `GET /users/{u}/events/public` | **keyless** (60/hr; token → 5000/hr) | "someone in your network just shipped / went independent" |

### 4.2 Events — the one capability gap

The entire event category has **closed its discovery APIs** (Eventbrite search removed; Meetup
gated behind Pro/OAuth) or never had one (Luma, Gary's Guide). The moat is now *"read the public
web page,"* which is exactly the local-first advantage — a scraper on the user's Mac hitting
public HTML needs no key and breaks no per-seat pricing.

- **Blocker (flag to user — capability, not account):** `web_search` is live (ddgs) but
  **`web_extract` is NOT configured** (`_intel_web_ok()` currently probes False). Wiring a local
  HTML-fetch/parse capability is the **#1 unlock** for event discovery. Keyless, local, no ToS-risky
  third party. → *User step: none; engineering step in Phase 3.*
- Once extract exists: scrape `lu.ma/nyc` + `garysguide.com` (clean structured HTML: date, venue,
  host names, cost) into structured event candidates. **Keyless.**
- **Do NOT** depend on X/Twitter (reading is dead/paid, Nitter fragile) or LinkedIn scraping
  (bans). Substitute GitHub + public event RSVPs + the user's own graph.

### 4.3 The user's OWN network → warm-intro + reconnect engine (the crown jewel)

The warm-intro industry (Metal, Connect The Dots, 4Degrees, Dex, Clay) all converged on one
architecture that **does not need LinkedIn** — it reconstructs the relationship graph from the
user's own Gmail + Calendar metadata. Hermes has more: **iMessage via chat.db** (real conversation
content, not just metadata), which Dex/Clay legally cannot hold.

Build a **relationship layer** (new `aux_foryou.py` helpers + nightly cron) over the soon-live
`messages.json` + Gmail + Calendar:
1. **Nodes = people, edges = interactions.** One `people/<slug>.md` card each.
2. **Strength score per contact** = f(recency, frequency, depth, source) → `people-graph.json`.
3. Three products fall out of the same graph:
   - **Reconnect** (lost-touch): strength was high, recency decayed → "you haven't talked to X in
     N months" — but *context-triggered*, not just time-based (Dex's weakness): fire when a world
     signal is relevant to X.
   - **Dropped-thread detector**: chat.db content reveals "you said you'd intro them, never did."
     Cloud CRMs are blind to this.
   - **Warm-intro pathfinding** (≤3 hops, dual-strength scoring you→connector × connector→target).
4. **The killer join:** cross the *event* feed with the *graph* — "this Thursday lu.ma matches your
   `INTERESTS` facet and is hosted by someone your contact Jane knows; Jane can intro you." Public
   event × private network × interest facet, computed locally. **Nobody ships this.**

**User steps for §4.3:** the existing NEEDS-YOU items — Full Disk Access for the app (Message
Center) and the ~10-min Google read-only OAuth (Gmail/Calendar/People). Both already documented in
`docs/NEEDS-YOU.md`; no new asks.

---

## 5. Surfacing UX: earn the interrupt

**a) A daily "Opportunities / For You" brief section.** Add `("foryou", "For you — moves, people & rooms")`
to `_BRIEF_HEADERS` and a `_brief_foryou()` builder to the `_brief_build_sections()` dict. It slots
into the existing deterministic-then-synthesized brief pipeline (`_brief_render_text` →
`_brief_synthesize` → `_brief_compose`) with zero structural change, and — per the vision — becomes
the *lead* section, each item carrying its *why-you* and *suggested action*. The existing
"Underground signal" section becomes the deliberate **serendipity slot**: reserve a share of items
for high-unexpectedness-but-goal-relevant candidates (serendipity = relevance + unexpectedness +
novelty), with a tunable exploration dial so the agent never collapses into an echo chamber.

**b) A dashboard "For You" panel** — a new hub widget via the standard aux pattern
(`WIDGETS["foryou"]`, `EXPANDERS["foryou"]`, `RENDER["foryou"]`, `/api/hub` + `/api/expand`). This
is the **LangChain "Agent Inbox"** primitive: surfaced moves *queue* here instead of all pinging,
each with `useful/noise` buttons wired to the existing `_op_mark_reaction`, a *why-you* line, the
`suggested_action`, and (where consequential) an approval-gated "draft the intro" button.

**c) Telegram** — reserved for the `pierce now` band only; the disciplined channel, locked to
`8487169327`.

**Precision / anti-spam — "earn the interrupt" (the most-researched part):**
- **Bounded deferral is the default** (Horvitz MSR TR-2005-87): non-breaking items accrue and
  release at the next opportune breakpoint (calendar gap / Midday Pulse), rather than firing on
  discovery. Only corroborated, perishable, high-score items pierce. This is what makes *"run as
  often as possible"* compatible with *"never spam"* — loop frequency rises, **interrupts stay
  flat** because the gate is a scored threshold, not the loop cadence.
- **The four-layer filter** (CHI 2025, 2410.04596), gating every surface: relevance → importance
  → user-state (interruptible now?) → confidence. Surface only when all clear.
- **useful/noise feedback attributes to the model, not globally** (2502.09869): a "noise" reaction
  down-weights the *specific* matched goal/interest/source that fired, not global sensitivity.
  Explicit reactions weighted far above silence (no-reaction ≠ dislike).

---

## 6. Build plan — ordered workstreams

Design rule throughout: **extend a named module, keep every invariant, ship the cheapest thing that
produces the "whoa" first.** Phase 1 needs *no account grant* — it runs on the intel loop + brief +
memory + local Qwen that already exist.

### Phase 1 — The You-Model + the reasoning tag (fastest magic; no grants needed)
| WS | Extends | Effort | Acceptance | Deps | Safety |
|---|---|---|---|---|---|
| **1.1 Typed You-Model files** | `aux_memory.py` (+`people/` subdir in `_mem_valid_name`) | S | `GOALS/NOW/LOOKING-FOR/INTERESTS/PREFERENCES.md` exist, editable in Mind, char-metered | none | gated writes (§7) |
| **1.2 Onboarding interview** | agent conversation + gated memory writes | S | ~10-min chat produces initial Goals/Looking-For/people cards | none | writes proposed, user-confirmed |
| **1.3 Reasoning tag on intel** | `_intel_curate()` + item schema | M | items carry `matched_goal`/`matched_person`/`suggested_action`; embedding pre-filter + Qwen why-you pass | 1.1 | two-tier funnel ≤20 items/day → under ceiling |
| **1.4 "For you" brief section** | `_BRIEF_HEADERS` + `_brief_build_sections` | S | 8am brief leads with "3 things for you… because" | 1.3 | notify-only; deterministic fallback if model down |
| **1.5 "For You" dashboard panel** | hub widget (aux pattern) + `_op_mark_reaction` | M | Agent-Inbox queue with useful/noise + why-you | 1.3 | dashboard-only, no ping |

### Phase 2 — The relationship engine (needs Messages + Google grants)
| WS | Extends | Effort | Acceptance | Deps | Safety |
|---|---|---|---|---|---|
| **2.1 Relationship graph** | `aux_messages.py`+`aux_google.py` → `people-graph.json` | M | per-contact strength from recency/freq/depth | FDA + Google OAuth (NEEDS-YOU) | local only, read-only scopes |
| **2.2 Reconnect + dropped-thread** | nightly cron | S | context-triggered "reach out to X now" | 2.1 | notify-only |
| **2.3 Warm-intro pathfinding** | `aux_foryou.py` | M | ≤3-hop dual-strength paths surfaced with intro handle | 2.1 | drafting an intro = approval-gated |
| **2.4 Nightly reflection cron** | `hermes cron` + Mem0 loop | M | You-Model refreshed nightly from messages/cal/intel | 2.1 | ADD/UPDATE/DELETE proposed, not applied (§7) |

### Phase 3 — Event discovery (needs `web_extract`)
| WS | Extends | Effort | Acceptance | Deps | Safety |
|---|---|---|---|---|---|
| **3.1 Wire `web_extract`** | intel capability (`_intel_web_ok`) | M | local HTML fetch/parse works | none (eng) | local, public pages only |
| **3.2 Luma/Gary's scrapers** | `_intel_feeds()` | S | structured NYC/Hoboken events in intel | 3.1 | keyless |
| **3.3 The event×network join** | `aux_foryou.py` | M | "go to Z, hosted by Jane, Sam can intro" | 2.1+3.2 | notify-only |
| **3.4 Keyless opportunity feeds** | `_intel_feeds()` | S | HN whoishiring/Algolia/Grants.gov/GitHub in intel | none | keyless |

### Phase 4 — Earn-the-interrupt hardening
| WS | Extends | Effort | Acceptance | Deps | Safety |
|---|---|---|---|---|---|
| **4.1 ECI / bounded deferral** | `_wt_gate` + Calendar/iMessage state | M | non-breaking items defer to breakpoints; in-meeting = hold | 2.1 | replaces static quiet-hours |
| **4.2 Learned interruptibility + P(useful)** | `_op_mark_reaction` history → small classifier | M | gate self-calibrates per source/hour | reaction history | BusyBody-style, offline train |
| **4.3 Serendipity dial + eval loop** | brief "underground" + `aux_metrics` | S | exploration slider; weekly usefulness report | 1.5 | §8 metrics |

**Build FIRST (fastest "it's thinking for me"):** WS **1.1 → 1.2 → 1.3 → 1.4**. That chain needs
zero account grants and produces, at *tomorrow's 8am brief*, a "For you" section that reads
"3 things for you today, because [your goal]." Everything after deepens it.

---

## 7. Risks & how the invariants hold

- **Privacy / staying local.** All inference stays on the resident Qwen at `127.0.0.1:8080`;
  the You-Model is plain markdown under `~/.hermes/memories/` the user can read, edit and delete;
  Google scopes are read-only and *send is architecturally impossible* (`aux_google.py`); chat.db
  never leaves the Mac. Nothing new touches the network except the same keyless RSS/JSON fetches the
  intel loop already makes. The "you own and can delete your model" Mind card makes this legible —
  the post-Rewind moat.
- **Silent memory pollution (HEARTBEAT, 2603.23064).** Background loops writing to USER-model files
  is a real corruption vector (bad web/RSS/email content, or the agent's own hallucinations, merging
  into durable "facts"). Mitigations, built in: **autonomous memory writes are proposed to a
  quarantined `*.pending` section and require approval**, never silently applied; every background
  change is **audit-logged with source provenance** (reuse the `_mem_record`/snapshot machinery);
  ingested content is **untrusted until corroborated** (extend the Breaking-alert 2-source rule to
  memory writes).
- **Notification fatigue / over-reach.** The whole of §5 is the mitigation: bounded deferral as
  default, the four-layer filter, pierce/defer/digest bands, and feedback attributed to the specific
  matched model element. Interrupts stay flat as loop frequency rises. Consequential actions
  (drafting an intro, anything outbound) remain behind the 17-class approval tiers; proactivity is
  **notify-only** by construction.
- **Memory ceiling / "run as often as possible" safety.** The two-tier funnel means the expensive
  Qwen pass runs on only ~20 pre-filtered items/day, serialized (concurrency-1), context-capped, and
  admission-gated by `mlx_admission()` (refuse ≥50GB) with the `memory_guard_loop` 56GB watchdog as
  backstop. Heavy synthesis is queued to the 2–3 scheduled windows, never continuous — exactly the
  guardrail the always-on category is documented to lack.
- **Telegram-locked.** The `pierce now` channel remains the gateway locked to user `8487169327`.

---

## 8. Metrics — is it actually useful?

The `useful/noise` reactions already collected by `_op_mark_reaction` **are the gold accept/reject
labels** the proactive-agent literature pays annotators for. Turn them into a live eval loop
(extend `aux_metrics.py` + `_rule_stats`):

- **Precision, per source-type AND per interrupt-band** (north star): "of items I *pierced* with,
  what % were marked useful?" If Grants runs 80% and ticker runs 30%, raise the ticker threshold.
- **Acted-on rate:** of surfaced moves, how many led to a click / draft-approved / calendar add.
  This is the truest usefulness signal (do X / meet Y / go Z actually taken).
- **Recall via held-back sampling:** log suppressed items; periodically surface a sample in the
  digest ("I held these back — any matter?") so false-negatives are visible. Pure precision
  optimization silently collapses into saying nothing.
- **Moments of delight:** count of items the user explicitly reacts positively to or forwards; track
  the *first* magic moment and its cadence thereafter.
- **Weekly usefulness report** (to the user, via the brief): "42 candidates considered, 6 surfaced,
  5 marked useful (83% precision), 2 held back you later flagged as wanted." Makes the system's value
  legible and tunable — and is itself a retention hook.

---

*Net: the world-generic half already exists. The gap closes with two additions the research
unanimously points to — a **typed You-Model** in the memory files you already edit, and a
**relationship graph from the user's own data** — plus a **reasoning loop** that joins world signal
to that model and a **decision-theoretic gate** that earns the interrupt. First magic moment ships
in Phase 1 with no new account grant. The un-copyable output is the local join of public opportunity
× private network × interest facet, pushed proactively, on a Mac, with nothing leaving it.*
