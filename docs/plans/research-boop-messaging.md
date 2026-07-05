# Research: "boop" for the Hermes Message Center

**Question asked:** the user said *"for messages and others look into the boop open
source project, it has integrations for agents for these kinds of things"* — floated
as an alternative to Hermes reading `~/Library/Messages/chat.db` directly.

**TL;DR identification:** "boop" is almost certainly **`raroque/boop-agent`** — a literal
name match: an *iMessage personal agent* (Claude Agent SDK **or** Codex) with memory,
sub-agents, automations, and integrations. It is **not** a cross-platform message
aggregator. The thing that actually aggregates iMessage/WhatsApp/Signal/Telegram is
**Beeper** (Matrix/mautrix) — a *different* project the user's description partly
conflates, and one Hermes's DEVPLAN already put on the **NEVER (this run)** list (#37).

**TL;DR recommendation:** **Stick with the current chat.db plan (DEVPLAN #9 / P2.4).**
Do **not** adopt boop-agent (its default iMessage path is the **Sendblue cloud** and its
memory lives in **Convex cloud** — both break Hermes's local-first identity; it is also an
*outbound bot*, not a read-only Message Center). Do **not** adopt Beeper (AGPL mautrix
bridges + a Matrix homeserver + cloud-leaning = the heavy path already vetoed). Notable
validation: **boop-agent's own opt-in local iMessage reader does exactly what Hermes
chose** — reads `~/Library/Messages/chat.db` read-only via `sqlite3` with Full Disk
Access — so Hermes's approach is independently corroborated by the very project the user
pointed at.

---

## 1. What it is

### boop-agent (the literal "boop" — most likely intended)
A personal **iMessage agent you text**. You send it a message; a dispatcher agent spawns
focused sub-agents that answer with context, memory, and tool use, and it replies back
over iMessage. Author (`raroque`) describes it as *"a starting point, not a finished
product"* — his personal architecture, open-sourced as a template after community
interest. It is a **conversational bot / proactive nudger**, categorically different from
Hermes's **read-only Message Center widget** that surfaces recent conversations.

### Beeper (the cross-platform aggregator the description actually points to)
A universal chat app built on **Matrix**, unifying ~11 networks (WhatsApp, Telegram,
Signal, Instagram/Facebook, Discord, Slack, Google Messages/Chat/Voice, X, LinkedIn,
Bluesky) via open-source **mautrix bridges**. This is the project that matches *"aggregates
messages across platforms (iMessage, WhatsApp, Signal, etc.)"* — but it is not called
"boop", and Hermes already rejected it.

**Disambiguation verdict:** name → boop-agent; *stated goal* (cross-platform aggregation)
→ Beeper. The user likely remembered "boop" (real, catchy, iMessage-focused) but attributed
Beeper-style cross-platform capabilities to it. Both are covered below because the honest
answer requires both.

---

## 2. How it works (architecture)

### boop-agent
```
iMessage ── Sendblue webhook ──▶ Interaction agent (dispatcher)
                                        │
                                        ▼
                                 Execution sub-agents
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
                  Memory store (Convex)        Integrations (Composio → MCP)
```
- **Runtime:** choose at setup — **Claude Agent SDK** (needs a Claude Code subscription)
  or **Codex app-server** (needs ChatGPT/Codex). Uses local subscription auth, no API keys
  for the runtime itself.
- **iMessage transport (primary):** **Sendblue** — a *cloud* iMessage API. Inbound arrives
  via a Sendblue **webhook** (setup auto-registers an ngrok/Cloudflare tunnel); replies go
  out through Sendblue's API. Messages transit Sendblue's servers.
- **Memory/persistence:** **Convex** — a *cloud* real-time database. All conversation
  history, memories, and extraction records live there (tiered short/long/permanent with
  daily consolidation).
- **Integrations:** **Composio** — a cloud tool-aggregator (~1,000 toolkits: Gmail, Slack,
  GitHub, Notion, Linear, HubSpot, Salesforce, …). The dispatcher calls `spawn_agent()`
  with a chosen toolkit set; `buildMcpServersForIntegrations()` wraps those tools as a
  **scoped MCP server** so each sub-agent sees only its tools (no 1000-tool context bloat).
- **Opt-in local Apple integration (the relevant part for Hermes):** off by default behind
  a two-layer switch; **read-only, on-device, Mac-only.** iMessage source *"Reads
  `~/Library/Messages/chat.db` locally through `/usr/bin/sqlite3`"* (requires Full Disk
  Access on the process running it); Notes/Reminders via `/usr/bin/osascript`. Toggle
  routes are **localhost-only** — the public tunnel cannot enable local Apple access.

### Beeper
Each network is a **puppeting bridge** (mautrix) translating between Matrix and the native
protocol. Bridges can be **self-hosted** against a Matrix homeserver (`beeper/bridge-manager`)
**or** run on **Beeper Cloud** / "On-Device Connections." Consuming clients talk Matrix
(SDKs: `mautrix/go`, `beeper/pickle` TS); community examples include MCP servers and a
Raycast extension. Architecture = **homeserver + one long-running bridge daemon per network**.

---

## 3. Platforms / integrations

| | Personal messaging | Third-party services |
|---|---|---|
| **boop-agent** | **iMessage only** (Sendblue cloud, or opt-in local chat.db read). **No WhatsApp/Signal/Telegram.** | ~1,000 via **Composio** (Gmail, Slack, GitHub, Notion, Linear, HubSpot, Salesforce, Discord, Drive/Calendar/Sheets/Docs, Jira, Asana, Airtable, Figma, Dropbox, …) + built-in local browser automation |
| **Beeper** | **iMessage, WhatsApp, Telegram, Signal, Instagram/Facebook, Discord, Slack, Google Messages/Chat/Voice, X, LinkedIn, Bluesky** (~11 native; more community bridges) | n/a (messaging-focused) |

Crucial mismatch: boop-agent's "integrations for these kinds of things" are **productivity
SaaS**, **not** the cross-platform *personal messaging* (WhatsApp/Signal) the Message Center
wants. Only Beeper delivers that — and only via heavy bridges.

---

## 4. License / maturity

- **boop-agent:** **MIT** (permissive — no copyleft concern). **~869 stars, ~200 forks**;
  actively maintained (debug dashboard, agent-CLI self-upgrade, a native iOS app teased).
  Self-described as a template, not a product.
- **Beeper / mautrix bridges:** **AGPL-3.0-or-later** (verified for `mautrix/whatsapp` and
  `mautrix/signal`). Beeper is a funded, mature product with a large open-source footprint.
  ⚠️ **AGPL matters for Hermes:** DEVPLAN #38 already flags AGPL/GPL as *"architecture ideas
  only, no code lifting."* Running the bridge binaries is fine; vendoring/deriving from their
  code inside Hermes is a legal no-go.

---

## 5. Privacy / local-first assessment

**boop-agent — fails Hermes's local-first bar as-shipped.** Its default data flow is
**device → Sendblue (cloud) → your server → Convex (cloud)**: message *content* and all
memory leave the machine. Ironically this is **less private than Hermes's own chat.db plan**.
The only local-first piece is the opt-in `chat.db`/osascript reader — which Hermes already
does natively. Composio integrations also proxy OAuth + tool calls through Composio's cloud.

**Beeper — cloud-leaning; local-first only via serious self-hosting effort.** The convenient
path is Beeper Cloud (message data transits Beeper). Fully self-hosted mautrix bridges keep
data on your infra, but that means running a Matrix homeserver + a persistent bridge process
per network + linking each account (QR/session) — operationally heavy and a standing attack
surface. This is precisely why DEVPLAN #37 rejected it: *"already rejected as heavy;
local-first chat.db read (#9) is the answer."*

**Hermes's current P2.4 plan — strongest privacy posture of the three.** FDA-holding signed
app reads a read-only SQLite snapshot, decodes only ~14 recent conversation previews, POSTs
them over **loopback** to a `0600` store; the full multi-GB `chat.db` never leaves TCC
protection; no network egress; read-only (no send). Nothing leaves the machine.

---

## 6. Fit vs. chat.db (the current plan)

- **Does it preserve local-first?** boop-agent: **No** (Sendblue + Convex + Composio clouds).
  Beeper: only with heavy self-hosting, and even then it's more surface than a single SQLite
  read. chat.db plan: **Yes**, by construction.
- **Complexity vs chat.db?** Both **add** complexity. boop-agent drags in a cloud iMessage
  bridge, a cloud DB, and a tool-aggregator to replace what Hermes does with one local SQLite
  query it *already wrote and proved* (`expand_messages()`, `dashboard/expanders_extra.py:553`).
  Beeper adds a homeserver + N bridge daemons. chat.db is the lowest-complexity path for
  iMessage.
- **What does it uniquely unlock?** Only **Beeper** unlocks platforms chat.db physically
  cannot reach — **WhatsApp, Signal, Telegram, Instagram, etc.** boop-agent unlocks *nothing*
  chat.db can't already do for messaging (its extra value is SaaS tool-calling, which is a
  different feature from a Message Center). This is the one genuine capability gap: chat.db is
  **iMessage/SMS-only**.
- **Validation signal:** boop-agent's local Apple integration independently reproduces
  Hermes's exact technique (read-only `chat.db` via `sqlite3` + FDA). The project the user
  cited *endorses* the path Hermes already chose.

---

## 7. Integration path (if ever pursued)

- **boop-agent as a dependency:** not recommended. If anything is borrowed, it's an
  **architecture idea** (MIT permits code reuse, but the shape is wrong for Hermes): the
  **"scoped MCP server per sub-agent"** pattern (`buildMcpServersForIntegrations`) is a clean
  way to hand a sub-agent only the tools it needs — worth mirroring with a **local** MCP
  registry instead of cloud Composio. No adoption of Sendblue/Convex/Composio.
- **Beeper, if cross-platform is ever a hard requirement:** the only local-first route is
  **self-hosted mautrix bridges** feeding a local Matrix homeserver, then a small reader that
  pulls recent rooms via the Matrix client-server API (or `beeper/pickle`) and POSTs previews
  to the *same* `POST /api/messages/ingest` endpoint P2.4 already defines. That reuses Hermes's
  ingest contract — but pulls in AGPL binaries (run-only, never vendored) and per-network
  bridge daemons. Strictly a **Phase-3+, opt-in, per-platform** add-on, not now.
- **chat.db (current):** already specced end-to-end in `p2-4-message-center.md`; no new
  external moving parts.

---

## 8. Recommendation

**Stick with chat.db for iMessage (DEVPLAN #9 / P2.4). Ship it as planned.** It is the most
private, lowest-complexity option and is now independently validated by boop-agent's own local
reader.

**Do not adopt boop-agent.** It doesn't solve the stated goal (no WhatsApp/Signal; iMessage
only), its default architecture (Sendblue + Convex + Composio) is *cloud-dependent and less
private than what Hermes already has*, and it's an outbound bot rather than a read-only
Message Center. Salvage at most one **idea** — scoped per-agent MCP tool exposure — reimplemented
locally. MIT license keeps that option clean.

**Do not adopt Beeper this run.** It's the only thing that unlocks WhatsApp/Signal/Telegram,
but at the cost of a Matrix homeserver + AGPL bridge daemons + cloud-leaning defaults — exactly
the heaviness DEVPLAN #37 already rejected. The prior "NEVER (this run)" call holds.

**Hybrid, only if the user later insists on non-iMessage platforms:** keep chat.db as the
iMessage source of truth, and add **self-hosted mautrix bridges** for *specific* extra
platforms as an opt-in Phase-3+ feature, feeding the existing `/api/messages/ingest` contract
— run-only (never vendor AGPL code), fully local, user-initiated. Until then, the cross-platform
gap is a known, accepted limitation, not a reason to import a cloud stack.

---

## Sources (verified by fetch unless noted)

- boop-agent repo (fetched): https://github.com/raroque/boop-agent
- boop-agent INTEGRATIONS.md (fetched): https://github.com/raroque/boop-agent/blob/main/INTEGRATIONS.md
- boop-agent README.md (fetched): https://github.com/raroque/boop-agent/blob/main/README.md
- boop-agent open-source announcement (search result, not fetched): https://x.com/raroque/status/2048799820433756236
- Beeper open-source docs (fetched): https://developers.beeper.com/open-source
- Beeper bridges & self-hosting (search result): https://developers.beeper.com/bridges
- Beeper bridge-manager (search result): https://github.com/beeper/bridge-manager
- How Beeper Android Works (search result): https://blog.beeper.com/2024/04/09/how-beeper-android-works/
- mautrix/whatsapp — AGPL-3.0-or-later (search result): https://github.com/mautrix/whatsapp
- mautrix Signal — AGPL-3.0-or-later (matrix.org, search result): https://matrix.org/ecosystem/bridges/signal/
- Referenced clouds (not fetched): Sendblue (iMessage API), Convex (DB), Composio (tools)

_Written 2026-07-05. Cross-refs: `docs/DEVPLAN.md` #9 (chat.db), #37 (Beeper/Matrix — NEVER),
#38 (AGPL — ideas only); `docs/plans/p2-4-message-center.md`._
