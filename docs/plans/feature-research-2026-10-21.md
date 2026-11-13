# Feature research — what to build next (2026-10-21)

Written against **1.3.2** (commit 7b5bd76 + the uncommitted Liquid Glass / U-fix
working tree), alongside the same-day codebase audit
(`docs/audits/2026-10-21-codebase-audit.md`). Research only: nothing here has
been built, no model was loaded, and the Hermes checkout was read but not
fetched or upgraded.

**How it was made.** Eight parallel web researchers (Hermes upstream, MLX
models/runtimes, peer products, macOS 26 platform, MCP and agent protocols,
agent-harness research, agent security, community demand) returned 130 dated,
sourced facts and 61 raw ideas. One inventory pass over this repo's CHANGELOG
and `docs/plans/` listed what is shipped, parked and rejected, so nothing below
re-proposes a shipped or rejected item without saying what changed. A merge
step reduced the ideas to **23 candidates** (25 dropped, reasons at the end).
A code scout then checked each candidate against the real code for
insertion points, invariant conflicts, effort and prerequisites. Finally,
56 load-bearing external claims went to adversarial verification: **37
confirmed, 16 partly right (corrected below), 1 refuted, 2 unverifiable**.
Another 24 lower-priority claims were not verified because of the cap. The
ranking and sequencing are this document's judgement, not the agents'.

## 1. What changed in the world since the September plans

- **Hermes Agent is four minor versions ahead.** Installed: v0.18.0
  (2026.7.1). Upstream: **v0.22.4 (2026-10-21)**. Along the way:
  - **v0.19** turned LLM-reviewed "smart approvals" on by default.
  - **v0.20** raised the default tool-iteration cap from 90 to 500 and added
    streaming voice.
  - **v0.21** added cron agents with persistent memory, no-LLM monitor-mode
    jobs and live subagent steering. Its session-store rewrite made state.db
    fragile; 0.21.2/0.21.3 fixed that.
  - **v0.21.6** carries the first listed security fixes.
  - **v0.22** added lazy tool loading, and it deletes sessions inactive for
    90 days at startup unless `sessions.auto_prune: false`. Skip **v0.22.1**:
    it was built from the wrong commit.
  - **Breaking for this dashboard:** after `gateway.ready`, a WebSocket
    client must send `client.capabilities {server_requests:true}`.
    Otherwise every approval request is *withdrawn*, not denied.
    `hermes_rpc.py` does not send it today.
- **The mlx-vlm pin is still right, but the reason has moved.** mlx-vlm is
  at 0.7.10. The concurrent-MTP corruption (issue #2033) is still open.
  Someone reproduced it on 0.7.0 + mlx 0.32.2 on an M5 Max, with the
  orcarouter Qwen3.8-27B *Uncensored* 6-bit and its `mtp/` drafter. That is
  the original reporter's issue, but this repro was a separate commenter's.
  The 0.7.x line has fixed things this product wants:
  - 0.7.4: low-temperature and top-p sampling, three tool-parser bugs.
  - 0.7.8: stable tool-schema key order for prefix caching.
  - 0.7.9: honours checkpoint sampling defaults.
  - 0.7.0: an APC redesign (#1960) whose PR describes snapshot/restore *and
    disk persistence for dense and hybrid caches*. The research first said
    disk APC was Flash-Next-only; the verifier corrected that.

  So serialising the primary lane (F02) is what would make 0.7.x safe to
  take for its single-stream benefits.
- **mlx core** 0.32.2 fixed the teardown segfault the launcher shim works
  around. 0.32.3 added Metal gated-delta-net kernels, which apply to the
  Qwen3.5/3.8 hybrid layers.
- **No better model at this RAM.** No open-weight model released Jul–Oct
  2026 clearly beats Qwen3.8-27B for tool calling at ~18–20 GB with an MLX
  build. Gemma 4's numbers are vendor-reported and its MLX tool parser had
  bugs. The roster stays.
- **MCP 2026-07-28 is final and breaking**: stateless, no `initialize`,
  sampling/roots/logging deprecated. `hermes_mcp.py` negotiates up to
  2025-06-18, and the posts do not say how stdio changes. Tool annotations
  (`readOnlyHint` …) and structured output have been in the spec since 2025
  and the server uses neither.
- **macOS.** This Mac is on 26.5.2. 26.6 (2026-07-27) was mostly a security
  release. macOS 27 was reported shipped on 2026-09-14; its new hooks (`fm`
  CLI, Shortcuts "Use Model") need the OS upgrade, and some of them leave the
  device. Apple open-sourced a Python SDK for the on-device Foundation Model
  (`apple_fm_sdk`, macOS 26+, 8,192-token context). No Mac latency figure has
  been published.
- **Security landscape.** Several 2026 CVEs were exactly B02's class: a
  same-user local agent reaching its own control surface (OpenCode
  CVE-2026-22812; CodeWhale CVE-2026-75857).
  - Hugging Face: `trust_remote_code=False` turned out not to be a fence
    (Transformers CVE-2026-4372, fixed in 5.3.0; Diffusers "FaceHugger").
  - Prompt injection: adaptive attacks beat most published defences.
    Spotlighting went from ~1 % attack success with static attacks to >95 %
    under search-based ones. The defensible half is capability limiting
    (Meta's "Rule of Two": at most two of untrusted input / sensitive access
    / state change-or-external-comms).
  - Upstream: Hermes's own `SECURITY.md` treats approval-gate regex bypasses
    as out of scope. The dashboard therefore cannot lean on upstream
    heuristics to hold the approval line.
- **Demand signals.** In 2026 HN threads, the most-cited real use of
  personal agents is scheduled briefings and change monitoring (Hermes ships
  this). Reliability is the loudest complaint; "did my job run?" is the most
  praised small thing. Prompt injection via email and web content is the
  loudest worry. Local meeting transcription is a visible Show HN cluster
  (37 posts). Email help ranks high, though the "third most-cited" figure
  could not be verified; a keyword count puts it second.

## 2. Recommendation

Make the next line (**1.4**) about closing the open trust gaps and making
unattended work visible, *before* adding new inbound sources. Every new
source (email, meetings, files, selected text) is untrusted text flowing into
an agent that has tools. Until B19 (fencing) and B03 (model supply chain) are
closed, and B02 is at least contained, each new source widens the exact holes
the audits found.

### Wave 0 — measure before building (no product code; needs AC power)

| Item | Why first |
|---|---|
| **F13** temperature probe on the MTP batch path | Decides whether the Thinking/temperature copy is honest and whether 0.7.x's sampling fixes matter. One run, ~2 min per arm. |
| **F09** read-only check: does v0.22 still exempt core tools from lazy loading? | The scout found that in 0.18 `tools/tool_search.py` never defers `_HERMES_CORE_TOOLS`, which is where the heavy schemas are. If 0.22 keeps that rule, lazy loading buys little over the Focused profile and F09 drops. |
| **F16** count unsolicited pushes per day from the watchtower log | Daily caps already exist (rules 20, breaking 5). Only build a unified budget if the data shows a problem. |

### Wave 1 — 1.4.0 "close the trust gaps" (no app rebuild)

| # | Item | Effort | Closes / enables |
|---|---|---|---|
| 1 | **F07** fence untrusted text + reduced toolset for every unattended run | M | B19; prerequisite for F10, F11, F19, F22, F23 |
| 2 | **F01** model-add lockdown (curated roster, `trust_remote_code` per entry, default off, no `.py/.pkl/.bin` pulls) | M | B03 (+ the B27 config-import strip on the same path) |
| 3 | **F03** Hermes config drift check in Doctor | S | Pins `approvals.mode`, `sessions.auto_prune`, iteration cap, MCP sampling, loopback-only provider URLs and **the TTS provider** (see below) before and after any upgrade |
| 4 | **F02** primary-lane admission guard (one decode at a time while the MTP drafter is loaded) | M | Turns the "DO NOT UPGRADE" note into an enforced control; makes F12's 0.7.x cell viable |
| 5 | **F21** MCP tool annotations + structured output + version check | S | Clients use read-only hints to cut prompts; costs nothing |

**Found while scoping F03 (audit-relevant).** `~/.hermes/config.yaml` has no
`tts:` section, so the agent's core `text_to_speech` tool resolves to
`DEFAULT_PROVIDER = "edge"` (`~/.hermes/hermes-agent/tools/tts_tool.py:169`),
which is Microsoft Edge's cloud voices; `edge_tts` 7.2.7 is installed in the
Hermes venv. state.db shows zero calls to date, so this is latent. It is
still an egress path that NETWORK_FACTS does not list. Pin a local provider,
or drop the tool from the toolsets, in the same change as F03.

### Wave 2 — 1.4.x everyday value

| # | Item | Effort | Note |
|---|---|---|---|
| 6 | **F20** Stop / steer a running turn | M | `session.interrupt` and `session.steer` **already exist in 0.18** (scout-verified), so this needs no upgrade. It is the biggest daily-UX win when a 27B turn runs at 30–60 tok/s. |
| 7 | **F15** "Did it run?" ops strip + counts-only Telegram status | M | Answers the #1 complaint about personal agents from logs the dashboard already keeps |
| 8 | **F18** phase 0 "Read aloud" via the macOS synthesizer | S–M | Completes the dictation loop with zero RAM; ship it with the TTS-provider pin above |
| 9 | **F04** owner eval families (memory updates/abstention, FTS recall golden set, tool-call scoreboard) | M | This harness is the gate for F08, F12 and F14, and the only evidence that could ever justify embeddings |

### Wave 3 — platform moves (each behind its own canary)

| # | Item | Effort | Gate |
|---|---|---|---|
| 10 | **F08** Hermes 0.18 → 0.22.x in a scratch checkout with a serve-protocol canary | L | F03 + F04 + the `client.capabilities` change in `hermes_rpc.py`. Consider waiting for 0.23.0, where the curated notes for the ~620-PR 0.22.3 rollup are due. |
| 11 | **F12** mlx canary matrix: (a) mlx 0.32.3 under the 0.6.14 pin; (b) mlx-vlm 0.7.10 | M | (a) is independent. (b) only after F02 and F13. Also measure whether #1960's disk APC applies to dense `qwen3_5`, which would revive backlog #24 (wake latency). |
| 12 | **F10** Routines panel over `hermes cron` (monitor-mode first, explicit wake policy) | L | F07. Cron runs inside the gateway and delivers through its own adapters, which **bypass `_wt_send_telegram`** and therefore quiet hours and both masters. Route routine output through the choke point, or the "complete off-switch" claim in CLAUDE.md stops being true the day the first job exists. |

### Wave 4 — bigger bets

- **F05** out-of-band approval nonce, the real B02 fix. It needs the app
  rebuild. Batch it with F23's "capture the frontmost app *before*
  `NSApp.activate`" change: the scout found the popover activates Hermes
  first, so the front window is already gone by the time JS runs.
- **F06** Seatbelt profile around the terminal tool. It contains secret
  reads. It only contains B02 if loopback 7788/9119 is denied to the sandbox
  too (unix-socket shim for skills), and it never covers the in-process file
  tools.
- Later, each after F07:
  - **F11** Gmail triage into Needs-you. Local drafts only, because no
    draft-without-send Gmail scope exists. Blocked on the owner connecting
    OAuth.
  - **F14** memory v2 (bi-temporal facts, AC-only consolidation behind the
    existing approval queue).
  - **F19** meeting capture via the dictation helper. Needs a *helper*
    rebuild, a dashboard→helper channel, 0600 retention-limited transcripts,
    and must stay off the MCP surface.
  - **F22** files_search, opt-in per folder; launchd python lacks Full Disk
    Access for ~/Documents.
  - **F23** selection chip for Quick Ask.
  - **F17** Apple on-device model lane, as a one-hour spike first: SDK
    wheels for Python 3.14, the `xcode-select` CommandLineTools path, real
    RAM.

### Not now

F16 beyond the measurement (caps already exist) and F09 if the 0.22 core-tool
exemption holds. Also, everything in the dropped list (§5).

## 3. Candidate summary

| ID | Candidate | Feasibility | Effort | Value | Wave |
|---|---|---|---|---|---|
| F01 | Model-add lockdown (B03) | fits | M | high | 1 |
| F02 | Primary-lane concurrency guard | fits | M | high | 1 |
| F03 | Hermes config drift guard | fits | S | high | 1 |
| F04 | Owner eval families | fits | M | high | 2 |
| F05 | Out-of-band approval nonce (B02) | app rebuild | L | high | 4 |
| F06 | Seatbelt profile for the terminal tool | fits | L | high | 4 |
| F07 | Fence untrusted text + reduced unattended toolset (B19) | fits | M | high | 1 |
| F08 | Hermes 0.22.x upgrade with protocol canary | fits | L | high | 3 |
| F09 | Lazy tool loading measured against the stable prefix | fits | S | low* | 0 |
| F10 | Routines panel over hermes cron | fits | L | high | 3 |
| F11 | Gmail triage into Needs-you, local drafts | fits | M | high | 4 |
| F12 | mlx canary matrix | fits | M | medium | 3 |
| F13 | Temperature honesty probe | fits | S | medium | 0 |
| F14 | Memory v2: supersession + consolidation | fits | M | medium | 4 |
| F15 | Ops "did it run?" strip + Telegram status | fits | M | medium | 2 |
| F16 | Daily notification budget | fits | S | low* | 0 |
| F17 | Apple on-device model lane | new sidecar | M | medium | 4 (spike) |
| F18 | Read aloud (macOS first, Kokoro later) | fits | M | medium | 2 |
| F19 | Meeting capture | fits (helper rebuild) | L | medium | 4 |
| F20 | Stop / steer a running turn | fits | M | medium→high | 2 |
| F21 | MCP annotations + structured output | fits | S | medium | 1 |
| F22 | files_search over granted folders | fits | M | medium | 4 |
| F23 | Selection chip for Quick Ask | fits | M | medium | 4 |

\* Value revised down by the feasibility pass: F09 because of the core-tool
exemption, F16 because two daily caps already exist.

## 4. Corrections from verification

These change how the candidates should be read; the cards below keep the
scouts' original wording.

- **APC disk persistence** is not Flash-Next-only. 0.7.0's #1960 redesigns
  APC for dense and hybrid caches with disk persistence, according to its PR
  body. That still needs a measurement on `qwen3_5`.
- **#2033** was reproduced on 0.7.0 by a separate commenter (Uncensored 27B
  6-bit + `mtp/`), not by the original reporter. The patch they tested and
  found ineffective is the still-unmerged #2197. Nobody has reported on
  0.7.1–0.7.10.
- **#2532** (stale M-RoPE deltas in batched MTP) matters mainly for mixed
  image/text batches, and a changed output has not been shown.
- **Rule of Two** has three properties, not four. State change and external
  comms are one property.
- **Adaptive attacks** beat >90 % "for most" of the 12 defences, and those
  12 mix jailbreak and injection defences. "CaMeL is not adopted by
  mainstream harnesses" has no source.
- **Hermes SECURITY.md** says host execution is the default. It explicitly
  excludes approval-gate regex bypasses from scope.
- **sandbox-exec** works on 26.2 and was run successfully on this Mac
  (26.5.2). It is marked DEPRECATED in its man page, with no removal
  announced.
- **Demand figures.** "Email is the third most-cited use" is unverifiable;
  a keyword count puts it second. "Users praise morning status reports"
  rests on one comment. The 3–5/day notification budget comes from an
  opinion post.
- **F09's two token figures do not conflict.** One is the halved desktop
  tool schema, the other a separate ~2.1k-token fixed-prompt cut.

## 5. Dropped during synthesis

Each was dropped as already shipped, previously rejected with no new evidence, against a hard constraint, or too weakly evidenced.

- Hermes Agent memory 'Topics' editor (research candidate from peer-products) - already shipped: dashboard/aux_memlayer.js lists facts with edit, pin, add and approve of imported facts since 1.2.2; only the supersession and consolidation parts survive as F14.
- SpeechAnalyzer / Parakeet dictation engine bake-off (macos-platform, local-models-mlx, community-demand candidates) - already shipped: app/dictation/main.swift already uses SpeechAnalyzer + SpeechTranscriber with an on-device SFSpeechRecognizer fallback; benchmarks cited are vendor or anecdotal. Meeting capture (F19) reuses it.
- Hermes upstream streaming voice, barge-in and on-device wake words (0.20 Herald) - always-on microphone costs battery and privacy, mic TCC must live in the separate signed helper rather than launchd python, and wake words need an opt-in design nobody asked for; TTS is covered by F18 without it.
- Webhook receiver for upstream HMAC-signed lifecycle events as an audit feed - marginal over existing tracing and state.db reads, and the agent shares the uid so it can read the HMAC secret and forge audit events, which would weaken the Flight Recorder.
- Apple container micro-VM backend for the terminal tool (XL) - heavier than Seatbelt, loses native Mac tools and computer use, adds RAM and cold start, and apple/container 1.3.1/1.4.1 carried multiple Containerization security advisories (1.6.0 patches some); revisit only if the Seatbelt profile in F06 cannot bound loopback reach.
- OTel GenAI semantic-convention trace refresh - the conventions are still in 'Development' (sources conflict on stabilisation) and moved repos, the export is opt-in with no user demand; revisit when the spec stabilises.
- Expose Hermes as a local provider/MCP hub for Raycast and LM Studio style clients - low value, and it invites third-party clients to wake the 27B or reach :8080 (which the same-origin guard does not protect); the stdio MCP server already covers agent clients.
- A2A v1.0 and AG-UI adoption - networked or vendor-ecosystem protocols with no local single-user use case, a new listener conflicts with the same-origin stance, and AG-UI is vendor-sourced; decision recorded: do not adopt unless the owner runs several local agents that must call each other.
- MCP Apps (ui:// resources) card for needs_you - low value for a single-owner product that has its own Hub, secondary-source evidence only, and message previews are sensitive.
- Approval-gated MCP write tools via elicitation - the static launch-time allowlist is the real control and elicitation is client-controlled, so it adds no enforcement; it also does not address B02 and must never become a path to sending anything.
- Scoped masked credential prompts (OpenClaw 2.0 style) - evidence is low-confidence secondary reporting; Hermes 0.19 already ships pluggable SecretSource and F06 denies secret reads, and no current tool needs a runtime secret.
- Embeddings / sqlite-vec with Qwen3-Embedding-0.6B - rejected before (no vector RAG until FTS proves insufficient) and no new evidence beyond small MLX embedder availability and no retrieval benchmark or MLX reranker; the FTS recall cases in F04 are now the measurement that would justify reviving it.
- Grammar-constrained tool arguments (XGrammar-metal in the mlx-vlm venv) and a tool-call repair layer - heavy dependency, low-confidence evidence, interaction risk with MTP and the concurrency bug, and no measured failure rate; the tool-call scoreboard in F04 decides whether it is ever needed.
- EventKit via PyObjC replacing icalBuddy - modest gain, needs a new Calendar TCC grant on the fragile launchd python, icalBuddy works; revisit only if parsing problems appear.
- On-device Translation framework sidecar - niche for one owner, language-pack downloads need a UI session, needs a Swift helper.
- macOS 27 fm CLI, Shortcuts 'Use Model' and agentic Siri hooks as features - require the macOS 27 upgrade (owner's call, may reset TCC and break the pinned MLX stack), fm --model pcc and AFM 3 Cloud leave the machine, and App Intents MCP support is unverified; handled as a platform note instead.
- Alternative primary or lane models (Gemma 4 31B and 26B A4B, Nemotron 3.5 Lightning, GLM-5.3-Flash, Muse Spark Open, Qwen3.8-Flash-Next) as roster changes - no independent tool-calling evidence of an upgrade over Qwen3.8-27B, the biggest are too large for 64 GB, and Gemma 4 would forgo the native MTP win; kept only as optional inputs to the F04 gate.
- APC disk cache for dense Qwen3.8-27B wake latency - disk persistence (#2059) is scoped to Qwen3.8-Flash-Next and blocked on the mlx-vlm concurrency bug; prewarm already hides most of the cost; watch item only.
- Hermes 0.22 Connectors for hosted apps, free nous/welcome guided-start model, one-click llama.cpp local models, Bot Mode, A2A plugin and the ~600-plugin catalog - cloud-routed or GGUF-only (not MLX), or an unreviewed plugin supply chain (the ClawHub malicious-skills record is the cautionary tale); incompatible with local-first and the trust posture.
- Upstream memory provider plugins (Honcho, Mem0, Supermemory, Hindsight, OpenViking, Holographic) - third-party or cloud-leaning stores that duplicate the owner's memory.db layer; OpenViking is AGPL (ideas only).
- Continuous Rewind-style screen memory - conflicts with battery, privacy and the per-invocation capture principle; Rewind and Limitless shutting down shows a gap, not that continuous capture is acceptable here.
- Superhuman-style auto-drafts saved into Gmail - impossible under the owner's scope policy (no draft-without-send scope exists; compose and modify permit send); drafts stay local text in F11.
- Cloud assistants' features (ChatGPT Pulse and Workspace Agents, Gemini Personal Intelligence, Perplexity Comet and Computer, Raycast Pro BYOM gating) - cloud-synced or paid cloud products; the local equivalents already shipped (World Brief, For You, Needs-you).
- Hands-1 sandboxed dev-agent as written - still DO NOT BUILD; the Seatbelt work in F06 supplies the shared P0 controls, nothing in the research changes the reviewer's verdict.
- Open WebUI / AnythingLLM agent flows and approval gates - versions and features come from low-confidence comparison sites and nothing in them fits the product's single-owner local design.

## 6. Candidate cards (feasibility detail)

Generated from the per-candidate code-scout reports and lightly trimmed. File:line references are to the working tree on 2026-10-21. "Effort" is the scout's estimate after reading the code. "Prerequisites" names audit findings by ID: B-series in `docs/audits/2026-10-08-codebase-audit.md`, C-series in `docs/audits/2026-10-21-codebase-audit.md`. Section 4 corrects these cards where they disagree.

### F01 — Model-add lockdown: no remote code from the roster (close B03)

Feasibility **fits** · effort **M** · value high · main-app rebuild: no

**What.** Make add_model/download accept only safetensors repos, pinned to a revision SHA, with no .py/.pkl/.bin payloads, and drop --trust-remote-code from mlx-server.sh and mlx-server-bg.sh wherever the shipped roster does not need it (carry it as a per-roster-entry flag, default off). Anything outside the curated list needs an explicit owner confirmation (the B02 nonce once F05 exists). Small, no new deps, no Swift.

**Why now.** 2026 showed trust_remote_code=False is not a reliable fence (Transformers CVE-2026-4372, Diffusers FaceHugger CVEs) and pickle scanners are evadable, and the 2026-10-08 audit lists B03 as an open P1 where any HF repo string is launched with --trust-remote-code outside approvals.

**Where it goes.**
- mlx-server.sh:165 (mlx_vlm branch): replace the hard-coded --trust-remote-code in VLM_ARGS with a conditional append driven by a new server-backend JSON field trust_remote_code (default false)
- mlx-server.sh:195 (mlx-lm exec): same conditional. The flag is the last argument after --chat-template-args, so the exec needs an array or a variable ($TRC_FLAG) to make it optional
- mlx-server-bg.sh:76 and :84: same conditional. The bg lane reads bg-model only, so it needs its own source for the flag (the roster entry for the bg model, or a bg-trust file). Default off

**Constraints.** The launchers are shell, not Swift or the frozen app, so no rebuild or FDA loss. The claim 'needs app rebuild: false' is correct · stdlib-only dashboard: HfApi().model_info for SHA resolution must run in the _hf_python() subprocess (the venv has huggingface_hub 1.28.0), not in server.py

**Prerequisites.** B02 (approval self-answer nonce): needed for the 'explicit owner confirmation' to be a genuine control. Without it the agent's terminal can POST the confirm. · Run the flag-off load drill for all four shipped entries on mlx-vlm 0.6.14 (and the mlx-lm path for any entry without backend) before removing the flag from the launchers

**Risks.** Qwen3.5-9B's tokenizer_class is TokenizersBackend (transformers 5). It resolves in-library, so it should not need remote code. Loading without the flag is NOT yet verified on mlx-vlm 0.6.14. · No cached snapshot contains .py files and no config.json/tokenizer_config.json has auto_map (checked: grep -l auto_map matched nothing in any of the four snapshots), so the shipped roster very likely needs no remote code…

**Resources.** RAM: none. Disk: none for the shipped roster (the ignore patterns only skip files). Fewer stray .bin/.py pulls for user-added repos. Battery: none.

**Test plan.** Unit (offline, tools/tests/unit/t_f01_model_lockdown.py in the t_a09 style): (1) add_model rejects malformed ids ('../x', 'a b', no slash, >120 chars) and any non-curated id without the confirm; accepts a curated id; never persists a trust_remote_code key from the request;

**Sources.** <https://labs.cloudsecurityalliance.org/research/csa-research-note-huggingface-diffusers-facehugger-rce-20260/> · <https://www.rapid7.com/blog/post/from-pth-to-p0wned-abuse-of-pickle-files-in-ai-model-supply-chains/>


### F02 — Primary-lane concurrency guard (fail closed on overlapping MTP streams)

Feasibility **fits** · effort **M** · value high · main-app rebuild: no

**What.** Enforce a one-request-at-a-time admission rule on the primary model server itself (in mlx-vlm-launch.py or the server flags, not the dashboard) so a Telegram turn, a dashboard turn, a prewarm turn and any delegated subagent can never be decoded concurrently with the MTP drafter loaded. Overlapping callers queue and the UI shows 'queued behind another turn'. Add a doctor check that flags any overlap that would have occurred.

**Why now.** mlx-vlm issue #2033 still reproduces the '!'-flood / early-stop corruption on 0.7.0 with mlx 0.32.2 on an M5 Max with dense Qwen3.8-27B at concurrency 2 and 4, so the 0.6.14 pin is not an exit and silent garbage on overlap is a live risk. The repo's own numbers show serialising is also faster in aggregate: solo MTP is ~47.8 tok/s vs 42.5 aggregate for two concurrent MTP streams.

**Where it goes.**
- mlx-vlm-launch.py:138-140 (the __main__ block): add _patch_admission_guard() beside _patch_rng_restore/_patch_local_snapshot_resolution/_patch_default_reasoning_effort.
- mlx-vlm-launch.py new patch body: gate only when a drafter is loaded (self.draft_model is not None and self.draft_kind == 'mtp'; the same attributes _run_impl checks).
- mlx-vlm-launch.py same patch: counters (in_flight, queued, max_queue_depth, overlaps_prevented, last_wait_ms) written to a small JSON file such as ~/.hermes/dashboard/primary-admission.json (0600, atomic tmp+replace, throttled).

**Constraints.** Mostly compatible. It enforces, not contradicts, the 'DO NOT UPGRADE mlx-vlm past 0.6.14' policy. · Idle-suspend and prewarm: prewarm is a real serve turn (CLAUDE.md prewarm note), so it will queue behind a user turn or block one. A prewarm that holds the slot for ~25s cold prefill delays the first real turn.

**Prerequisites.** No audit finding strictly blocks it. B02 (approval self-answer) and B19 (unfenced prompts) are about the dashboard API and prompt injection, which are orthogonal to the model-server layer. · Confirm the Hermes client timeout and retry behaviour for a request that stays silent (config.yaml timeout 180, inactivity_timeout 120).

**Risks.** A leaked slot (exception path, GC'd iterator, client disconnect mid-prefill) wedges the whole primary lane. Needs once-only release, a watchdog timeout (e.g. · Queue wait vs client timeouts (see conflicts). An unconfirmed assumption: whether the Hermes 0.18 client retries after the 120s inactivity timeout, which would double-submit and double-queue the same prompt.

**Resources.** RAM: none beyond a few KB of counters. Battery: neutral to slightly positive (serialising avoids the 29% aggregate loss). Disk: one tiny JSON file. Network: none.

**Test plan.** Unit (offline, tools/tests/unit): a harness with fake iterators proving the semaphore is released exactly once on normal end, on exception, on close() mid-stream and on GC. Cover FIFO ordering, the queue-wait cap and the counters file shape (0600, atomic).

**Sources.** <https://github.com/Blaizzy/mlx-vlm/issues/2033> · <https://github.com/Blaizzy/mlx-vlm/issues/2532>


### F03 — Hermes config drift guard in Doctor (safety and egress keys)

Feasibility **fits** · effort **S** · value high · main-app rebuild: no

**What.** Add a Doctor panel and MCP doctor check that pins the Hermes config keys whose defaults upstream has changed or that could silently break the local-only claim: approvals.mode manual (0.19 made LLM-reviewed 'smart approvals' default), sessions.auto_prune false (0.22 deletes 90-day-old sessions), the tool-iteration cap (0.20 raised the default 90 to 500), any enabled remote/cloud mcp_servers entry plus MCP sampling and elicitation settings (sampling is on by default upstream and wakes local inference), auxiliary/comp…

**Why now.** The upgrade path crosses four minor versions that each changed a safety-relevant default, and the Data & Network card promises 'everything else runs on this Mac'; a drift guard keeps that sentence true before and after the upgrade and costs almost nothing.

**Where it goes.**
- dashboard/doctor.py:562-571 (CHECKS list + @check decorator): add `@check("hermes_drift", "Hermes config drift")` right after _chk_config (doctor.py:906-937);
- dashboard/doctor.py near _yaml_sane (doctor.py:523): add a tiny pure stdlib reader `_hcfg_load(path)` (indent-based subset parser for scalars, nested maps and `- item` lists, used for approvals/sessions/agent/command_allowlist/mcp…
- dashboard/aux_doctor.py:79-101 needs NO change: /api/doctor serves run_checks output, and ?format=text goes through mod.report; the new check appears automatically in both

**Constraints.** None with invariants: read-only, stdlib-only, no model wake, no network (doctor never wakes a model per its own docstring), no app rebuild · doctor.py is also a standalone CLI that must work with the dashboard down, so the parser must live in doctor.py, not aux_doctor.py or server.py

**Prerequisites.** None blocking. B02 (agent answers own approval prompts, docs/audits/2026-10-08-codebase-audit.md:61) is orthogonal but makes this check weaker as a guarantee: the same-uid terminal tool can edit ~/.he… · B19 unrelated

**Risks.** Hand-rolled YAML subset parser could misread flow-style, anchors, multiline or quoted values; mitigate by WARN on any unparseable region and by preferring PyYAML or running `~/.hermes/hermes-agent` venv python (`hermes c… · Absent keys: after an upgrade a missing key means the NEW upstream default applies (e.g. approvals.mode absent -> smart in 0.19+, auto_prune absent -> true in 0.22).

**Resources.** Negligible: one file read (~6 KB config) plus an in-process parse per doctor run, within the 10 s cache (aux_doctor.py:_DOC_CACHE_TTL). No RAM/battery/network/disk cost; no model wake.

**Test plan.** unit (offline, tools/tests/unit/t_f03_hermes_drift.py, run via tools/tests/run.sh): feed fixture YAML strings to the pure parser/evaluator and assert status per rule: approvals.mode manual=PASS, smart/off/absent=WARN/FAIL; sessions.auto_prune absent/false=PASS, true=WARN; agent.max_turns >cap=WARN; mcp_servers with enabled:true and non-loopback url=WARN while enabled:false (current higgsfield entry) = PASS;

**Sources.** <https://newreleases.io/project/github/NousResearch/hermes-agent/release/v2026.7.20> · <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp> · <https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.0>


### F04 — Personal eval expansion: memory updates, recall, notification precision, upgrade and model gates

Feasibility **fits** · effort **M** · value high · main-app rebuild: no

**What.** Extend the 9-case scheduled eval suite with owner-shaped families built on synthetic facts: knowledge-update and abstention cases for the memory layer, an FTS recall golden set over the owner's own index (the measurement that decides whether embeddings are ever justified), a tool-call reliability scoreboard from state.db (decides whether grammar or repair layers are worth building), and a needs-you precision tracker.

**Why now.** Public memory and tool-calling benchmarks are vendor-reported and retrieval-centric, with no MLX numbers for Qwen3.8-27B or Qwen3.5-9B, so the only trustworthy gate for the upgrade, an mlx bump or a model swap is one the owner runs on their own stack. Several other candidates here (F08, F12, F14, F17) need it as their pass/fail test.

**Where it goes.**
- dashboard/aux_evals.py:602-621 (EV_DRILL_IDS/EV_LOCAL_CASES) and :623-652 evals_cases(): add a third registry EV_OWNER_CASES (memory_update, memory_abstain, recall_golden) appended after the local three;
- dashboard/aux_evals.py:754-835 evals_run(): runs everything evals_cases() returns against the active model; memory/recall cases that need NO model (pure FTS/memory calls) should be flagged model-free and run before the online/wake…
- dashboard/aux_evals.py:709 _ev_store_run + :270 _EV_SCHEMA: cases stored per run; a family/score column (or recall@k in detail JSON) goes here; _ev_migrate at :333 handles it

**Constraints.** No env override for the DB paths: ML_DB (aux_memlayer.py:90) and IX_DB (aux_index.py:92) are module constants derived from DATA; there is no HERMES_MEMORY_DB. · Anything that seeds into real memory.db/index.db would pollute the owner's data and trigger index_touch wrappers; must never write the real stores

**Prerequisites.** None blocking for the first slice (memory + recall are read-only/synthetic) · B02 should land before any case or MCP write path that calls memory facts add or evals run, since aux_memlayer.py:1172-1260 is on the B02 list and the agent can self-answer consent prompts

**Risks.** Global-swap isolation bug could write synthetic rows into the owner's memory or index · Scoreboard heuristics over state.db misclassify tool errors, producing a misleading go/no-go for grammar/repair layers

**Resources.** RAM: none for memory/recall/scoreboard slices if model-free (sqlite only, tmp files in scratch dir). Battery/disk: negligible. Model-judged cases reuse the existing AC-gated daily run (~150s cap per case).

**Test plan.** unit (offline): extend tools/tests/unit/t_evals.py with case-registry tests (new families present, model-free cases run without model_online, missing case still fails not vanishes); test_memlayer.py-style throwaway HOME seeds synthetic facts, asserts update supersedes old fact, abstain case yields empty block, and real memory.db mtime/hash unchanged;

**Sources.** <https://mem0.ai/blog/ai-memory-benchmarks-in-2026> · <https://arxiv.org/pdf/2606.15903> · <https://www.betterclaw.io/blog/gemma-4-vs-qwen-3-8> · <https://artificialanalysis.ai/articles/nemotron-3-5-lightning-launch>


### F05 — Out-of-band approval secret held only by the app (close B02 at the root)

Feasibility **fits-with-app-rebuild** · effort **L** · value high · main-app rebuild: yes

**What.** Sensitive routes (approve, consent, permission-policy, memory-fact approval, model-add, config import, shortcuts run) require a per-launch nonce that the Swift app injects into its WebView and Quick Ask popover and that never touches disk, so a same-uid agent shell cannot answer its own prompts over the Origin-less API. Optionally surface dangerous approvals as a native NSAlert. Ship it inside one deliberate, batched rebuild.

**Why now.** The audit's own conclusion is that a token on disk does not fix same-uid self-approval; Claude Code's sandbox documentation treats the same problem (a sandboxed command granting itself permissions) as a design requirement, and 2026 produced real CVEs of exactly this class in local agents.

**Where it goes.**
- dashboard/server.py:4099-4120 (_request_allowed, pure) and :4123 (_guard): add a pure `_route_gated(method, path)` plus `_nonce_ok(headers)`.
- dashboard/server.py:4619 /api/chat/approve (inline). Also inline: /api/models/add (:4547) and /api/access (:4333, only the grant/revoke ops).
- dashboard/aux_shortcuts.py:586-587 (/api/shortcuts/config, /api/shortcuts/run, including ticket redemption with approved:true).

**Constraints.** Same-uid limits. If the nonce stays only in app memory plus WebView JS, the agent can't read it from disk. But the server must learn it, and the dashboard is launchd-owned, so there is no inheritable fd from the app. · CLAUDE.md invariant: the same-origin guard intentionally allows Origin-less requests, and curl, the launchd scripts, the MCP server and MessagesSync depend on that.

**Prerequisites.** B01 (stale approval box / server-side refuse unless job.state=='approval' and request id matches, audit lines ~100-107). · B03 (trust_remote_code opt-in and a curated roster for /api/models/add). The nonce makes add owner-only, but B03's fix is independent and cheap, and it should not wait on a Swift rebuild.

**Risks.** First-writer-wins race and server restart. Mitigation A (recommended): the handshake is accepted only if the server can map the client TCP source port to a PID (`lsof -nP -iTCP:<port>` or libproc) and that PID's executab… · Residual same-uid attacks remain: ptrace/lldb of the running app if the user has Developer Tools authorization, or AppleScript/accessibility UI scripting that clicks Approve in the WebView.

**Resources.** Negligible. One 32-byte nonce in memory, an hmac.compare_digest per gated POST (microseconds), and a ~2 s status poll from the app only if boot_id tracking is not piggy-backed on the existing /api/health retry timer.

**Test plan.** unit (offline, tools/tests/unit/t_f05_appnonce.py): the pure `_nonce_ok` / `_route_gated` decision table. Cases: gated path with no header, a wrong header or the right header, in armed and un-armed states (un-armed means gated routes refuse); non-gated paths unaffected; op-conditional gating (/api/access list vs grant; /api/memory/facts add vs approve_all; /api/permissions set_class); constant-time compare;

**Sources.** <https://security-tracker.debian.org/tracker/CVE-2026-22812> · <https://corgea.com/advisories/vulnerabilities/CVE-2026-75857> · <https://code.claude.com/docs/en/sandboxing> · <https://hn.algolia.com/api/v1/items/47831437>


### F06 — Seatbelt (sandbox-exec) profile around the agent's terminal and code-exec tools

Feasibility **fits** · effort **L** · value high · main-app rebuild: no

**What.** Run the Hermes terminal tool's child processes under a deny-by-default Seatbelt profile that denies reads of ~/.hermes/.env, serve-token, messages-token, mcp-allow.json and chats, denies writes to ~/.hermes/config.yaml, the dashboard directory, LaunchAgents and the update lock, and limits network to what tools legitimately need. Read-only access to the dashboard API for skills goes through a narrow unix-socket shim rather than open loopback.

**Why now.** Claude Code and Codex ship exactly this pattern on macOS and sandbox-exec still works on macOS 26.2 with no removal announced; Anthropic documents it as the way to cut approval prompts while bounding blast radius. It is the only B02 mitigation that needs no rebuild.

**Where it goes.**
- ~/.hermes/hermes-agent/tools/environments/local.py:513-520 `_find_bash()` returns `shutil.which("bash")` first, and `_run_bash` (local.py:989-1060) is the single Popen site for terminal commands as `[bash,"-c",cmd]`.
- Alternative seam (hermes_cli/plugins.py:1126-1141 `register_middleware`, VALID_MIDDLEWARE in hermes_cli/middleware.py:29 incl. TOOL_REQUEST_MIDDLEWARE): a new plugin hermes-plugins/sandbox/{plugin.yaml,__init__.py} (same layout as…
- NEW dashboard/sandbox_profile.py (or aux_sandbox.py, aliased imports per the datetime gotcha): renders the .sb profile from live state (granted folders from get_access()['dirs'], server.py:1255) into ~/.hermes/sandbox/profile.sb (…

**Constraints.** Stdlib-only dashboard and no Swift change: satisfied (shim is shell+Python stdlib; sandbox-exec is /usr/bin/sandbox-exec, verified present). · Only covers children of terminal/process tools. Hermes file tools (tools/file_tools.py, read_file/write_file/patch), memory_tool, skills tools, browser, send_message and computer_use run IN the serve process, unsandboxed, so ~/.he…

**Prerequisites.** B02 itself is partly what this mitigates, but the server-side halves should land regardless: /api/chat/approve refusing unless job.state=='approval' with matching request id (B01), and a per-launch no… · B19 (unfenced calendar/feed text in unattended prompts, server.py:1310-1325, aux_watchtower.py:2474) should land first or alongside: the sandbox bounds what an injected prompt can do via terminal, but…

**Risks.** False sense of security: unsandboxed in-process tools (file tools, execute_code approval path, computer_use) bypass it; must ship with the file-tool denylist and be described as 'defence in depth for B02', not a fix. · Silent bypass on hermes upgrade: if a newer Hermes resolves bash differently (absolute /bin/bash, HERMES_GIT_BASH_PATH-like override, new terminal backend, or a PTY/process_registry spawn path that does not call _find_ba…

**Resources.** RAM: none (sandbox-exec is a kernel policy, no daemon; unix-socket shim is one dashboard thread). Battery: negligible (policy compile per exec ~ms; shim idle). Disk: a few KB (profile, shim script). Network: none added;

**Test plan.** UNIT (tools/tests/unit, offline, no sandbox-exec needed for logic): profile renderer is pure `render_profile(grants, home, env)`: realpath handling (/etc,/tmp,/var -> /private), grants become subpath allows, secrets list (.env, serve-token, messages-token, mcp-allow.json, chats, config.yaml, dashboard dir, LaunchAgents, update.lock) always denied even if a grant covers them (grant of ~ or ~/.hermes must NOT lift deni…

**Sources.** <https://code.claude.com/docs/en/sandboxing> · <https://github.com/anthropics/claude-code/issues/26095> · <https://gastown.dev/docs/research/macos-sandbox-exec>


### F07 — Fence untrusted text and run unattended jobs under a reduced toolset (close B19)

Feasibility **fits** · effort **M** · value high · main-app rebuild: no

**What.** Calendar titles, feed items, message previews and later email text are datamarked and wrapped as inert quoted data (control characters and newlines stripped, length-capped, delimiter-escaped) before entering any briefing, watchtower, For-You or routine prompt. Unattended runs use a reduced toolset profile (no terminal, no web egress with parameters, no file write, no shortcuts run) so they never hold all three legs of untrusted input, sensitive access and state change.

**Why now.** Prompt injection through email and web content is the most repeated technical worry in the 2026 community threads, adaptive attacks beat spotlighting-style defences on their own, and the evidence supports the capability-limiting half (Rule of Two). Routines (F10) and email triage (F11) both ingest more untrusted text and must not ship before this.

**Where it goes.**
- dashboard/server.py:1136-1144 run_agent(): add a toolsets/unattended parameter that appends '-t <reduced list>' before '-z' (hermes -z accepts -t/--toolsets, hermes_cli/main.py:12221, oneshot.py:33-112).
- dashboard/server.py:1393 _generate_briefing(): `run_agent(access_preamble() + BRIEFING_PROMPT, lane='bg')` is the one unattended full-toolset call. Switch it to the reduced profile.
- dashboard/server.py:1247-1330 access_preamble(): the calendar line (~1310-1325) joins `e['time'] e['title']` raw. Fence the titles there.

**Constraints.** Stdlib-only: no conflict. The helper is regex and string work. · AUX MODULE GOTCHA: new code must not use `from datetime import datetime`. Not needed here.

**Prerequisites.** B19 is this feature. No hard blockers. · B02 (approval self-answer): not needed for Phase 1, because unattended `-z` runs fail closed on approvals and the reduced toolset removes the terminal anyway.

**Risks.** Spotlighting alone is defeated by adaptive attacks, so the capability cut (-t) is the real control. Without it, fencing is only cosmetic. Sell and test the two halves separately. · The `-t` allowlist semantics must be verified against v0.18. oneshot.py:81 mentions `--toolsets all`, and unknown entries are only warned about and ignored (:112).

**Resources.** Negligible. The fencing is a few regex passes per prompt build. A reduced toolset shrinks the tool schema in the unattended prompts, a mild token and prefill saving of up to several thousand tokens on the bg lane (cf.

**Test plan.** Unit tier (offline): (1) A pure `fence_untrusted` test table: newlines, CR, control characters, a fence-marker injection, length cap, unicode, empty and None input, and idempotence (fencing twice does not grow without bound). (2) Drift test that the mirrored regex blocks match aux_convos and hermes_mcp.

**Sources.** <https://arxiv.org/abs/2510.09023v1> · <https://ai.meta.com/blog/practical-ai-agent-security/> · <https://www.sophos.com/blog/inside-the-lethal-trifecta-blast-radius-reduction-in-ai-agent-deployments> · <https://hn.algolia.com/api/v1/items/46893970>


### F08 — Staged Hermes upgrade 0.18.0 to 0.22.x with a serve-protocol canary

Feasibility **fits** · effort **L** · value high · main-app rebuild: no

**What.** Check out a tagged 0.22.x release into a scratch location (never in place, never against the live serve) and run a protocol canary: session.create/resume, prompt.submit, the approval round trip as hermes_rpc.py performs it, state.db read-only queries the dashboard issues, hermes send --json, hermes -z --continue, the custom_providers bg lane, plugin hooks used by tool-budget and loop-breaker, and the existing 10-case agent drill plus the eval suite.

**Why now.** Upstream is at v0.22.4 (2026-10-21), four minor versions ahead, with the first listed security fixes (0.21.6: dashboard auth hardening, credential scoping, SSRF pinning), cron memory and monitor mode, lazy tool loading, and state.db reliability fixes.

**Where it goes.**
- tools/bench/hermes_canary.py (new): standalone driver that imports dashboard/hermes_rpc.py WSClient and runs session.create/resume, prompt.submit, approval.respond against a scratch serve on :9129.
- dashboard/hermes_rpc.py:367 (session.resume), :398 (session.create), :414 (prompt.submit), :425/:473/:483 (approval.respond), :465 (approval.request event): the exact RPC surface the canary has to replay.
- tools/tests/unit/t_canary_protocol.py (new): offline unit test that pins the list of RPC methods and event names in hermes_rpc.py, so the canary and the client cannot drift apart.

**Constraints.** The live serve process, the model ports :8080/:8081 and `hermes serve --stop` are off-limits, because --stop scans the process table and would kill the live serve (1.3-feasibility.md:59). The canary must kill only its own PID. · mlx-vlm stays pinned to 0.6.14. The Hermes upgrade must not move the mlx or transformers pins, and it uses its own venv, never the framework Python or the mlx-vlm venv.

**Prerequisites.** B02 (approval self-answer over the Origin-less API; audit table, docs/audits/2026-10-08-codebase-audit.md:61) should land first or alongside. · F03 (drift guard, pinning approvals mode, auto-prune and iteration caps): the pitch already treats it as a gate. It must land before cutover, though not before the scratch canary.

**Risks.** A WebSocket breaking change is documented upstream (per the pitch; I did not verify it). approval.request/approval.respond could become server-to-client requests, or a client.capabilities handshake could be required. · state.db schema drift. Today messages has columns id, session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp, token_count, finish_reason, reasoning, observed, active, compacted and others.

**Resources.** Disk: a source clone of about 300 MB plus a venv of about 0.5 to 1 GB, and node_modules if web_dist is built, about 300 MB. This is temporary, and the scratch tree is deleted after.

**Test plan.** unit tier (offline, CI): t_canary_protocol.py asserts that the set of RPC methods and event names hermes_rpc.py uses matches a pinned constant. It also checks that the canary driver refuses ports 9119/8080/8081 and refuses `serve --stop`, and checks the TOKEN_FILE override logic. A schema-expectation test builds a throwaway state.db with the expected columns and runs the dashboard readers against it.

**Sources.** <https://github.com/NousResearch/hermes-agent/releases> · <https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration> · <https://www.gradually.ai/en/changelogs/hermes-agent/> · <https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.0>


### F09 — Tool-schema diet via upstream lazy tool loading, measured against the byte-stable prefix

Feasibility **fits** · effort **S** · value high · main-app rebuild: no

**What.** After F08, adopt the upstream on-demand tool loading (tool_search for ~19 rarely used tools) instead of building the planned tool-schema-diet llm_request plugin, and measure the real prefix and cold-prefill change with the prompt-budget tooling. The acceptance test is that the system and tool prefix stays byte-identical across a whole conversation (so APC exact-prefix caching keeps hitting) and that prewarm still produces a ~0.2-0.4 s second turn.

**Why now.** v0.22.0 ships lazy loading, and mlx-vlm 0.7.8 separately made tool-schema key order stable for prefix caching, which signals this exact interaction matters. Prefill of the ~14.5-21k token prefix (~25 s cold at ~630-690 tok/s) is the single biggest wake-latency cost left after prewarm.

**Where it goes.**
- ~/.hermes/config.yaml `tools.tool_search.enabled` (default 'auto', threshold_pct 10; hermes_cli/config.py:2656) - the only switch needed to activate native lazy loading; set 'on' for a measurement arm.
- dashboard/aux_promptbudget.py:156-200 (_PB_PROFILES) and :740 (_pb_build) - add a 'tool_search' arm/field to the payload so the card reports visible-tools bytes vs deferred bytes;
- docs/plans/b3-ttft-bench.py and b3-prefix-ttft-findings.md - extend the prefix-stability probe to hash the serialized system+tools prefix across N turns (including one turn that calls tool_call) and assert the hash never changes.

**Constraints.** The core finding collides with the pitch: tool_search.py:9-11 and :163-183 say core tools in toolsets._HERMES_CORE_TOOLS (toolsets.py:31) are NEVER deferred, 'no exceptions'. · The pitch's '~19 rarely-used tools' is likely the same set the Focused profile already removes via toolsets config (33->19 tools).

**Prerequisites.** Read-only check first: diff v0.22 tools/tool_search.py and toolsets.py against the installed 0.18 to confirm whether core tools became deferrable and whether _HERMES_CORE_TOOLS still pins the set (can… · Run `hermes prompt-size --platform tui --json` from ~ with tools.tool_search.enabled=on vs off (config-only, reversible) to get the true visible-vs-deferred byte split before any build.

**Risks.** Prefix stability is by design: tool_search.py header says the catalog is stateless and rebuilt each assembly, the model-visible array is visible-core + 3 fixed bridge schemas (bridge_tool_schemas(deferred_count) at :426… · Schemas are never appended to the conversation or the array; tool_call executes the underlying tool by name through handle_function_call.

**Resources.** RAM: none. Battery: none beyond measurement runs. Disk: none. Network: none. Prefill: potential savings only for deferrable (non-core) schemas;

**Test plan.** unit (offline): pure test of _pb_est/_pb_shape with a tool_search arm; a hash-stability test that serializes system+tools for N synthetic registry states and asserts equality when only deferred tools change; config-key writer test (enabled on/off/auto, invalid value rejected). live (read-only dashboard): GET /api/prompt/budget carries visible/deferred bytes and the active tool_search mode; no POST.

**Sources.** <https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.0> · <https://standardcompute.com/hermes-agent-v0-22> · <https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.7.8> · <https://claude.com/blog/context-management>


### F10 — Routines panel over hermes cron with no-LLM monitor mode and a wake policy

Feasibility **fits** · effort **L** · value high · main-app rebuild: no

**What.** A Routines card (inside an existing Settings panel or the Mind view) listing recurring and one-shot agent tasks with next run, last result, run now, dry run and 'turn this conversation into a routine'. Each routine declares a lane: no-LLM monitor (upstream monitor mode skips the model when nothing changed), bg lane, or primary with an explicit 'may wake the model' opt-in; otherwise it queues until the model is already awake. Results land in the Needs-you inbox and the ops strip (F15), not a new notification stream.

**Why now.** Scheduled and unattended tasks are table stakes (Claude Cowork scheduled tasks since Feb 25 and device-offline scheduling since Jul 7; OpenClaw's cron/hooks core) and the most-cited real use in agent threads is a daily check that notifies on change. Hermes 0.21 added cron persistent memory, continuity and no-LLM monitor mode, and 0.22 added retry 5/15/30 min with one alert per incident.

**Where it goes.**
- NEW dashboard/aux_routines.py (+ aux_routines.js, one <script> tag in index.html after aux_network.js). Aux files exec SORTED, so it loads before aux_watchtower.
- Routes: GET /api/routines, POST /api/routines/{create|edit|pause|resume|run|remove|dryrun|from_chat}. They shell out to `hermes cron ...` (list/create/edit/pause/resume/run/remove).
- Result harvest: a sweep thread (aux_index's 30 min sweep pattern, but ~60 s) reads ~/.hermes/cron/output/<job_id>/*.md and each job's last_run_at/last_status/last_error from jobs.json.

**Constraints.** Cron jobs run inside the gateway process (cron/scheduler.py:2253 run_job) and call the resolved provider endpoint directly. They do not go through serve or the dashboard. · Cron delivery (--deliver telegram|origin|local) goes through the gateway adapters (scheduler.py:1220 _deliver_result). That skips _wt_send_telegram, so quiet hours, the briefings/news masters and the delivery-error parsing would n…

**Prerequisites.** B19 (fence untrusted calendar/feed text in unattended prompts, audit table row at docs/audits/2026-10-08-codebase-audit.md:78). Routines are exactly the unattended full-toolset case. · B02 (agent can answer its own approvals over the Origin-less API, audit line 61). A routine that executes tools unattended makes the self-approval hole worse, and `POST /api/routines/*` would be anoth…

**Risks.** The bg lane (:8081) is on-demand/off at times. Nothing mints the bg token, which the user has to touch by hand. A bg-lane job with :8081 down will fail, so the dashboard gate must check bg_online() and queue. · Unverified: whether provider 'custom:bg' resolves in resolve_runtime_provider for cron. Probe it with a dry-run job first.

**Resources.** RAM: none for monitor-mode routines, because a script runs and no model loads. A primary-lane routine loads the 27B (~26 GB) only on the explicit opt-in.

**Test plan.** unit (offline, tools/tests/run.sh): (1) a pure `_rt_lane_decision(lane, model_up, bg_up, opt_in, paused, suspended)` truth table covering monitor, bg and primary with and without the opt-in, plus paused and idle-suspended. (2) A pure parser for jobs.json records into the routine view, with fixtures of last_status ok/error/silent and a one-shot schedule.

**Sources.** <https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31> · <https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.0> · <https://support.claude.com/en/articles/12138966-release-notes> · <https://docs.openclaw.ai>


### F11 — Gmail read-only triage feeding Needs-you, with local-only drafts

Feasibility **fits** · effort **M** · value high · main-app rebuild: no

**What.** Implement the empty email collector in Needs-you over the existing gmail.readonly OAuth: list recent threads with metadata and snippet only (bodies fetched on demand), classify with the existing rules-first buckets and VIP history signal, and offer reply suggestions as local draft text shown in the dashboard for the owner to send themselves. Optional 'email' source in the FTS index, off by default. Rules first; the model pass stays off by default, today-bucket only, already-online lane only.

**Why now.** Email and admin help is the third most-cited real use of personal agents, the collector slot is already specified and empty, and reviewers say Superhuman-style auto-drafts are rarely send-ready, so a transparent rules-first triage with review-only drafts is a defensible differentiator. It also matches the owner's explicit read-and-draft-only posture.

**Where it goes.**
- dashboard/aux_needsyou.py:126 _NY_MAIL_PROVIDERS = (needsyou_email_provider, gmail_needs_reply, ...): a function named needsyou_email_provider, defined by a new aux module, is picked up with no edit to aux_needsyou.py.
- dashboard/aux_needsyou.py:889-941 _ny_collect_email already consumes {available, messages:[{id, from, subject, snippet, ts, unread}]}.
- dashboard/aux_needsyou.py:464 and :1115 already treat source 'email' like 'message' in _ny_classify and _ny_suggested. The classifier needs no change.

**Constraints.** No send path exists. aux_google.py:24-35 and :88 forbid gmail.send, gmail.compose, gmail.modify and gmail.insert, and the scope wall revokes anything wider. gmail.readonly makes sending impossible at Google's side. · The pitch says Needs-you should surface 'reply suggestions as local draft text', but the existing draft action (_ny_start_draft) runs a full agent chat job with tools.

**Prerequisites.** Owner connects Google OAuth on this Mac (Settings › Connections). Today connected is false. · B02 (approval self-answer) should land first, because email text will reach agent turns.

**Risks.** The owner has not connected Google. Live check of /api/google/status returned connected:false, has_client_secret:false, scopes:[], reason not_connected. · Prompt injection through snippets (B19, B02), see conflicts.

**Resources.** RAM: negligible (about 25 thread records cached, a few tens of KB). Battery: one HTTPS burst every 5-10 min for roughly 26 small requests, which is trivial but should pause in quiet hours and while idle-suspended.

**Test plan.** Unit tier (offline, tools/tests/unit): stub urllib so Gmail JSON fixtures drive the new provider. Cover metadata-only requests (assert no format=full and no body fetch), snippet entity decoding, a cache TTL, a failed token refresh giving available:false, and sources.email present or absent.

**Sources.** <https://hn.algolia.com/api/v1/items/47783940> · <https://www.capterra.com/p/199278/Superhuman/> · <https://apps.apple.com/us/app/superhuman-the-fastest-email/id1120837655>


### F12 — Isolated mlx canary matrix: mlx core bump under the 0.6.14 pin, and 0.7.x with a longer probe

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** Build throwaway venvs (rename-aside rollback rule, never the live ports, AC power, primary paused) for two cells: (a) mlx-vlm 0.6.14 with mlx 0.32.3 or 0.32.4, to test whether the new Metal gated-delta-net kernels speed the Qwen3.5/3.8 hybrid layers and whether the teardown-segfault shim can go; (b) mlx-vlm 0.7.10 with mlx 0.32.4. Gate each cell on single-stream correctness, tool-call drill, prefix-cache behaviour, a temperature test (F13) and an extended concurrency probe.

**Why now.** Since the 0.6.14 pin, mlx-vlm shipped 0.7.0 to 0.7.10 (temperature and top-p fixes, three tool-parser fixes, stable tool-schema key order, release of finished speculative-batch caches) and mlx added GDN kernels in 0.32.3. The corruption bug is still open, so the experiment is cheap and could capture the upside without taking the risk.

**Where it goes.**
- install-mlx-vlm-venv.sh:48-49 - VENV is hardcoded to ~/.hermes/mlx-vlm-venv. Add `VENV="${VENV:-$HOME/.hermes/mlx-vlm-venv}"` (or a new sibling install-mlx-vlm-canary.sh) so a cell builds at ~/.hermes/mlx-vlm-venv.canary-<cell>.
- tools/bench/concurrency_probe.py:241-277 (run_rounds) - hardcoded 2-way (threads ta/tb with PA/PB). Add a --streams N option (2|4) and a third and fourth prompt.
- tools/bench/concurrency_probe.py:309-329 (argparse) - --rounds default is 3. Add --streams, --sampled-probe (exists), a tool-drill hook and a prefix-cache check.

**Constraints.** Frozen app: not affected. Everything is Python and shell in tools/ and install scripts, so there is no Swift rebuild and no FDA loss. The 'needs app rebuild: false' claim holds. · Memory-guard and idle-suspend: the probe is safe only because the spawned server hides behind vlm-bench-launch.py. mlx-server.sh/dashboard `_mlx_footprint_gb` use pgrep on 'mlx-vlm-launch|mlx_lm server', so the canary does not app…

**Prerequisites.** None of the open audit findings block this: B02 (approval self-answer) and B19 (unfenced prompts) concern the dashboard's approval/prompt surface, not the model server. · F13 (temperature test) should land first or in the same change, since the cells are gated on it, and the current temperature behaviour is documented as an open question.

**Risks.** Statistical power: a 6/6-clean gate has little power against a low-rate fault. At p=0.10 per round, P(6/6 clean) = 0.9^6 = 53%. At p=0.65 (0.6.17) it is 0.35^6 = 0.2%. · The probe is 2-way only (run_rounds spawns exactly ta and tb with PA/PB, concurrency_probe.py:250-253). 4-way must be added.

**Resources.** RAM: one extra model server resident at a time. The Qwen3.8-27B 4-bit is ~16 GB weights, with a 0.9 GB drafter, and APC entries at 6 x ~1.2 GB (about 26 GB total footprint for the live primary, per CLAUDE.md).

**Test plan.** unit (offline, CI): tools/tests/unit/test_canary_matrix.py covers only the pure parts. (1) The clean-round scorer on synthetic round dicts, including the flood, early stop, error and non-interleaved cases. (2) The upper-confidence-bound helper: 0 failures in 6 gives ~39% (95% one-sided), and 0 in 30 gives ~9.5%, so the gate can print a number.

**Sources.** <https://github.com/Blaizzy/mlx-vlm/releases> · <https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.7.9> · <https://github.com/ml-explore/mlx/releases> · <https://github.com/Blaizzy/mlx-vlm/issues/2033>


### F13 — Temperature honesty probe on the MTP batch path

Feasibility **fits** · effort **S** · value medium · main-app rebuild: no

**What.** One measured run on the current pin: three identical prompts at temperature 1.0 and top_p 0.95 (and one at 0.0), with and without the drafter, comparing outputs and reading the sampler construction in mlx_vlm/server/generation.py. If sampling is effectively greedy on the batch/MTP path, make the Thinking and temperature controls say so, and record whether the server honours checkpoint sampling defaults (changed in 0.7.9) so recommended Qwen settings are actually in effect.

**Why now.** The observation of byte-identical completions at temperature 1.0 on both 0.6.14 and 0.6.17 is documented but uninvestigated (backlog #27). Fixes for small top-p and very low temperature (0.7.4) and checkpoint sampling defaults (0.7.9) landed upstream, so the answer can also settle whether the canary (F12) is worth taking.

**Where it goes.**
- tools/bench/temperature_probe.py (new, modelled on tools/bench/concurrency_probe.py): spawn a server on 8090 from a renamed copy of mlx-vlm-launch.py, send N identical prompts at T=1.0/top_p=0.95 and one at T=0, run it with --bloc…
- tools/tests/unit/t_f13_temp_probe.py (new): offline tests of the probe's pure parts (distinct-completion counter, verdict function, request-body builder). The server must not start.
- tools/bench/README.md: document the new probe. docs/plans/post-v1-backlog.md row 27: record the verdict. CLAUDE.md 'Open question — temperature may be ignored' paragraph: replace it with the answer.

**Constraints.** No conflict with stdlib-only: the probe is stdlib urllib against an OpenAI-compatible server. · Battery/RAM: it loads the 27B plus drafter (~17 GB) on 8090. That is compatible with 'nothing in tools/tests wakes a model' only if it lives in the ac tier or tools/bench.

**Prerequisites.** None blocking. Audit B02 (approval self-answer) and B19 (unfenced prompts) are unrelated: the probe issues no agent turns, no tool calls and no prompt-injection surface. · Run it on AC with the primary idle-suspended or paused. Otherwise use the --url mode against an already-up server so no second 27B loads.

**Risks.** The answer is already largely visible in the code, so a live run mainly confirms it. generation.py:1507-1531 `_make_sampler` returns None for T==0 (greedy). · A false 'greedy' conclusion is possible if only identical prompts are compared. Include one T=0 vs T=1 comparison on a prompt whose output differs, and one run with different seeds.

**Resources.** RAM: ~17-19 GB for the 27B plus 0.9 GB drafter while the probe runs, on 8090 with the primary asleep. Run it twice (with and without drafter), each ~2 min. Disk: none (models already cached).

**Test plan.** unit tier (offline, in run.sh unit): t_f13_temp_probe.py checks the pure functions. Cases: 5 identical strings give distinct=1 and verdict 'deterministic'; 5 differing strings give 'sampling live'; T=0 gives identical by design; the request builder emits temperature, top_p and optionally seed; the probe refuses to run off AC. No server starts, and the pgrep guard stays clean.

**Sources.** <https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.7.4> · <https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.7.9>


### F14 — Memory v2: temporal supersession and approval-gated nightly consolidation

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** Give memory facts valid_from, invalid_at and superseded_by, so a changed preference or schedule retires the old fact instead of contradicting it, with 'as of' queries and history in the Mind view. A consolidation pass runs only on AC and only when the bg lane is genuinely up (never waking the primary): it proposes merges, supersessions and a short 'tomorrow context' digest for the morning brief, and every proposed change waits in the existing approval queue for the owner.

**Why now.** Stale or contradicting facts are the documented weak point of long-lived agent memory (knowledge-update and forgetting categories), the sleep-time compute literature shows precomputing over predictable context pays off, and v0.22 made Hermes memory pluggable, so the dashboard's own layer should be the place where the truth is kept consistent.

**Where it goes.**
- dashboard/aux_memlayer.py:158-196 _ML_SCHEMA + :244-276 _ml_migrate: add facts.valid_from/invalid_at/superseded_by via the ALTER-TABLE-if-missing pattern (note the early-return on 'review' in cols at :257 means a second migration…
- dashboard/aux_memlayer.py:524-575 _ml_candidates: both SELECTs (pinned :541-545 and BM25 :556-560) need 'AND invalid_at IS NULL'. Exclude in SQL like archived/review.
- dashboard/aux_memlayer.py:447-451 _ML_COLS/_ML_QCOLS + :424 _ml_row: add the new columns (row tuple is positionally unpacked at :427, so change both together).

**Constraints.** Two sources of truth: MEMORY.md/USER.md rows are imported source=agent in_snapshot=1 and never injected (agent's frozen prompt carries them verbatim, aux_memlayer.py header/:994-998). · CLAUDE.md invariant: aux modules must not 'from datetime import datetime'; use an alias. The agent's file tool can reach people/ and youmodel files, so those sources stay review=1 untrusted.

**Prerequisites.** B02 (approval self-answer over the Origin-less API; the audit lists aux_memlayer.py:1172-1260 explicitly): the memory approve/approve_all endpoints are exactly what the agent's terminal can curl to ap… · B19 (unfenced untrusted text in unattended prompts): the consolidation prompt and the digest ingest calendar/feed/agent-written text; fence before shipping.

**Risks.** Wrong supersession by a 9B model silently hiding a true fact; mitigated only if proposals are never auto-applied and the old row is archived/invalid not deleted (reversible, restore exists). · Divergence between memory.db and USER.md (above); importer may resurrect or overwrite retired text if origin_key text changes.

**Resources.** RAM: none beyond existing bg lane (7GB, only if already up); never loads or wakes the primary. Battery: AC-only gate, so zero on battery; one short bg-lane call batch per night (a few thousand tokens).

**Test plan.** unit (tools/tests/unit, offline, tmp ML_DB): migration adds columns idempotently on a v1 store and on a fresh one; superseded/invalid rows excluded from _ml_candidates for both pinned and BM25 paths; as_of query returns the old fact for a past T and the new one now; supersede refuses/unpins pinned facts and keeps the 12-pin cap; proposals never alter facts until approved;

**Sources.** <https://arxiv.org/abs/2504.13171> · <https://letta.com/blog/sleep-time-compute> · <https://arxiv.org/html/2501.13956v1> · <https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.0>


### F15 — Ops visibility strip ('did it run?') plus a read-only Telegram status command

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** One panel shows each scheduled brief, watchtower pass, eval run, index sweep and delivery with last run, result, and a one-tap retry, built from logs the dashboard already keeps. A read-only status reply over Telegram (counts of Needs-you buckets, running jobs, last failures) answers 'what's going on' without opening the app. Telegram output is restricted to counts and status lines and passes the MCP redaction rules; message and email previews never go through it.

**Why now.** Reliability and flaky integrations are the top complaint in personal-agent threads, while the thing users praise most is a small unattended report that confirms the backup or job ran. A companion view of backlog and finished work was an explicit unmet ask, and the existing Telegram gateway can carry it without any Swift change.

**Where it goes.**
- dashboard/aux_ops.py (NEW, sorted after aux_needsyou, before/after aux_watchtower; resolve foreign globals by name at call time): GET /api/ops -> normalized rows from the existing stores
- dashboard/aux_ops.js (NEW) + one <script> tag in dashboard/index.html after aux_network.js: Ops card (Settings > System, aux_update.js relocator pattern) or Hub widget via WIDGETS/EXPANDERS/RENDER/WICONS (aux_claude_usage pattern)
- dashboard/aux_ops.py: GET /api/ops/telegram -> plain-text, counts-only status line(s); the sole thing a Telegram quick command calls

**Constraints.** No conflict with stdlib-only, no Swift, no cloud: purely reads local stores and uses an existing gateway feature. · Telegram egress invariant: every Telegram push must go through _wt_send_telegram and the Data & Network NETWORK_FACTS table; a pull-reply via quick command is a reply on the existing bot channel, but NETWORK_FACTS/onboarding table…

**Prerequisites.** B19 (unfenced calendar/feed text in unattended prompts) is not a blocker for the panel itself but the status path must not carry that text; land fencing independently. · B02 (agent can answer its own approvals / use Origin-less API): should land BEFORE adding a retry POST and before relying on a config-resident exec quick command, since the agent's terminal tool could…

**Risks.** No common run-outcome schema exists; each store has a different shape (jsonl fire log, single JSON, sqlite ix_meta, eval history, trace JSONL, update-state). · Briefing/idle-suspend and watchtower stores record success of generation more than failure; some 'did it run' answers (e.g.

**Resources.** RAM/battery: negligible. Reads small local files on request (cache result ~10-30 s); no new thread needed, no polling, no model wake. Disk: none new (optionally a tiny ops-retry log).

**Test plan.** unit (offline, tools/tests/unit/t_ops.py): fixture HOME with synthetic watchtower-log.jsonl, briefing.json, evals history, ix_meta, update-state; assert normalizer yields {id,label,last_ts,result in ok|failed|overdue|never|absent,detail_code} per source and degrades per missing/corrupt store (None vs list discipline);

**Sources.** <https://hn.algolia.com/api/v1/items/47783940> · <https://hn.algolia.com/api/v1/items/48419000> · <https://hn.algolia.com/api/v1/items/46993587>


### F16 — Daily notification budget with digest overflow in the attention router

Feasibility **fits** · effort **S** · value medium · main-app rebuild: no

**What.** A rolling, configurable cap on unsolicited pushes (briefs, pulse, breaking alerts, Needs-you nudges) enforced at the single _wt_send_telegram choke point, with overflow folded into the next brief or pulse as a digest and breaking alerts allowed to override like they override quiet hours. Start by measuring pushes per day from the watchtower log; default the cap only if the data shows a problem.

**Why now.** Practitioner and HCI work in 2026 converges on budgeted, breakpoint-aware interruption, the owner already has per-source masters, quiet hours and a precision metric for Needs-you, and the choke point makes it a small change. Evidence for the specific numbers (3 to 5 a day) is thin and unmeasured, hence measure first.

**Where it goes.**
- dashboard/aux_watchtower.py:907 (_wt_send_telegram). It is the single choke point, but it takes only text and has no 'kind', so a global budget check would need a keyword argument such as kind/bypass.
- dashboard/aux_watchtower.py:880-900 (_wt_gate): rules already pass through 'daily_cap' (cfg daily_cap, default 20, clamped 1-200 at :307). A rolling global budget would replace or extend this block.
- dashboard/aux_watchtower.py:2450 (_breaking_pass): per-day breaking cap (breaking.daily_cap default 5, clamp 1-10, :332). This is the existing analogue of the proposal, and cap overflow is logged as suppressed='daily_cap' and the…

**Constraints.** A daily cap already exists in two places (global daily_cap default 20 for rules; breaking.daily_cap default 5). F16 is therefore not 'not previously planned' in substance. It is a unification and a change from drop to digest. · The log (:2216-2225 etc.) shows briefs are logged, but the morning brief path (:2554) was not verified to write a log row (a grep for the morning type found no log row in the data: only breaking_ai, breaking_news, midday_brief and…

**Prerequisites.** Do the measurement first (see the test plan) and decide on the data. No audit finding strictly blocks the work. · B19 (unfenced untrusted text): only if the digest is routed through LLM synthesis. Keep the digest deterministic and append it after the synthesis, then B19 is not a prerequisite.

**Risks.** Likely a solution without a problem. The log has no row after 2026-09-01 (mtime Sep 1 15:00; the newest row is the midday brief of 2026-09-01), so there is no data for the roughly 7 weeks since. · Over that window, delivered pushes per active day run 1 to 7, typically 3 to 6. Breaking alerts are the bulk (153 delivered, 73 suppressed by daily_cap, 6 breaking_news), at roughly 3 a day on average with a cap of 5.

**Resources.** Negligible. There are no new processes, and a tiny JSON state addition. A rolling count over the log is a read of up to ~250 KB (log max rotates to .1), which is cheap, or a counter in the existing state.

**Test plan.** Measure first (read-only, no code change): a script in the scratchpad or a tools/ one-off that reads watchtower-log.jsonl(.1), groups delivered rows by local date and type, and prints pushes per day and the percentage of days at 5 or more, over the last 30 days.

**Sources.** <https://tianpan.co/blog/2026/05/13/background-agents-notification-budget-attention-economy> · <https://arxiv.org/pdf/2605.06717>


### F17 — Apple on-device model as a zero-RAM utility lane (apple_fm_sdk sidecar)

Feasibility **needs-new-sidecar** · effort **M** · value medium · main-app rebuild: no

**What.** A tiny isolated-venv sidecar using Apple's open-source Python SDK for the macOS 26 on-device model, for short structured jobs that currently either wake the 27B or are skipped: the Needs-you today-bucket model pass, conversation titling, search query rewriting, and schema-guided field extraction (title, time, location) as an additional layer for fenced calendar and feed text. Starts with a one-hour spike measuring latency, RAM and whether guided generation works on this Mac.

**Why now.** Apple open-sourced the Python SDK (macOS 26.0+, Apache-2.0) and the WWDC26 session says the on-device model was rebuilt with better tool calling and image input. It runs outside the MLX memory budget and would satisfy the rule that background work never wakes the model.

**Where it goes.**
- NEW dashboard/apple_fm_sidecar.py (isolated venv ~/.hermes/applefm-venv, loopback socket, on-demand) - separate process, not exec'd into server.py
- NEW dashboard/aux_applefm.py - exec'd sorted; registers GET /api/applefm/status, probe cache, helper applefm_json(schema, prompt, timeout); all foreign globals resolved by name at call time
- dashboard/aux_needsyou.py:1022-1075 _ny_model_pass - add the sidecar as a first lane BEFORE the bg_online()/bg_lane() probe (aux_needsyou.py:1038-1050); keep the settings gate _ny_model_enabled (aux_needsyou.py:997) first

**Constraints.** Python version: the dashboard/framework python is Homebrew 3.14.6 (/opt/homebrew/bin/python3), /usr/bin/python3 is 3.9.6; the SDK's minimum Python and whether it ships wheels for 3.14 is unverified - the venv should pin an explici… · xcode-select -p points at /Library/Developer/CommandLineTools although /Applications/Xcode.app exists - if the SDK needs the Xcode 26 toolchain/Foundation Models framework to build a native extension, xcode-select would need switc…

**Prerequisites.** B19 (docs/audits/2026-10-08-codebase-audit.md:78) should land first or alongside if F17 is to serve as a fenced-extraction layer under F07; otherwise it is explicitly not a security control · B02 (audit line 61) is not blocked by F17 (sidecar never touches approvals) but the sidecar's loopback socket must not become a second Origin-less approval-answering surface - keep it a pure text-in/J…

**Risks.** Spike may fail outright: Apple Intelligence may be disabled/region-limited, model assets not downloaded, or the SDK may require entitlements/Xcode toolchain a launchd python process lacks. · Quality: Apple's on-device model is small; Needs-you today-bucket judgement from 4-shot prompts may be worse than the 9B. Value is mostly in format-guided extraction, which is the weakest-use-case for the stated pitch.

**Resources.** RAM: nominally outside the MLX budget (mlx_admission/_mlx_footprint_gb do not see it), but the system model lives in unified memory under Apple's daemons - unmeasured;

**Test plan.** unit (offline, tools/tests/unit/t_f17_applefm.py): fake sidecar on a loopback port + absent-sidecar case; assert _ny_model_pass returns {} when disabled (sidecar not even probed), when absent (falls to bg lane or {}), on malformed JSON, unknown ids, and that promotion to 'now' still requires confidence>=0.8 plus a deadline; assert sidecar never started by a background tick when the setting is off;

**Sources.** <https://github.com/apple/python-apple-fm-sdk> · <https://developer.apple.com/videos/play/wwdc2026/241> · <https://rits.shanghai.nyu.edu/ai/apple-foundation-models-macos-27/>


### F18 — Read-aloud (TTS): macOS speech first, on-demand Kokoro sidecar second

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** Phase 0: a 'Read aloud' button on bot replies, the morning brief and Needs-you digest using the macOS speech synthesizer (zero RAM, no dependencies). Phase 1: an optional on-demand sidecar in its own venv running Kokoro-82M via mlx-audio that loads on request and exits when idle, played back by the page. Check first that the upgraded Hermes agent's built-in TTS tool is local, not a cloud engine, and add that to the drift guard.

**Why now.** Dictation (input) shipped in 1.3.0, so output is the missing half of a hands-free loop. mlx-audio is actively maintained (v0.6.1 on 2026-10-20) with Kokoro and newer small TTS models, and a user-gesture playback button avoids the autoplay and TCC questions that upstream's streaming voice stack raises.

**Where it goes.**
- dashboard/aux_tts.py (new, ~/aux_dictation.py pattern; register_get/register_post as at aux_dictation.py:949-952): POST /api/tts/say {text, voice?, rate?} runs /usr/bin/say -o <tmp>.aiff then afconvert to m4a/wav, returns bytes or…
- dashboard/aux_tts.js (new, one <script> tag in index.html after aux_network.js): adds a 'Read aloud' button to bot bubbles, the brief widget and the Needs-you widget/pop-out;
- dashboard/aux_onboarding.js:80 NETWORK_FACTS: phase 0 needs no row (all local); phase 1 weights download adds one row (HF download, 'only when you press Install voice') so aux_network.js and the first-run sheet both update

**Constraints.** DRIFT RISK, confirmed: Hermes's built-in text_to_speech tool is in the core toolset (toolsets.py:52) and DEFAULT_PROVIDER = 'edge' (tts_tool.py:169, config.py:1956) which is Microsoft Edge neural voices over the network (cloud). · Phase 1 venv: mlx_audio 0.5.0 already sits in mlx-vlm-venv and requires transformers>=5.14 (matching the venv's 5.15.0), so there is no conflict with mlx-vlm, but mlx-audio pulls mlx>=0.31.1 with a loose pin;

**Prerequisites.** Not blocked by B02 or B19 in principle: the feature adds no agent-reachable mutating route if POST /api/tts only synthesizes and plays; · Set tts provider to a local engine or drop text_to_speech from the toolset and add the drift-guard check (should land first or with phase 0).

**Risks.** Cloud TTS in the agent toolset (edge default) is a pre-existing privacy leak path; fix is a config/drift item, not a feature item · WKWebView audio: AIFF from say may not play in all WebKit builds; convert with afconvert to m4a/wav (both ship in macOS) and test in the real app, not only Playwright

**Resources.** Phase 0: zero RAM, negligible battery, no network, no disk beyond a transient ~0.2-1 MB temp file per utterance. Phase 1: Kokoro-82M weights ~0.3-0.4 GB disk (downloaded from Hugging Face once, user-triggered), RAM rough…

**Test plan.** unit (offline): tools/tests/unit/tts_harness.py - text sanitiser (markdown/URL/secret stripping, length cap), command builder for say (no shell, argv list, voice allowlist), settings clamp, drift guard parses a config.yaml fixture and fails on provider edge/elevenlabs/openai/etc and on auto_tts true, NETWORK_FACTS row present for the weights download, disk-headroom refusal using the pure rule.

**Sources.** <https://github.com/Blaizzy/mlx-audio/releases> · <https://simple-repository.app.cern.ch/project/mlx-audio> · <https://hermesagent.org.cn/docs/releases/v0-20-0>


### F19 — Meeting capture: record, transcribe on device, summarise after, index and follow up

Feasibility **fits** · effort **L** · value medium · main-app rebuild: no

**What.** Extend the separate dictation helper app (which already runs SpeechAnalyzer on device, with its own TCC identity) to capture mic plus system audio for a calendar-linked meeting, transcribe locally, store the transcript, and afterward (on AC, when the owner or a routine wakes the model) produce notes and action items into Notes, the FTS index and Needs-you follow-ups. Explicit start and stop, a visible recording indicator, retention window, and a consent reminder for multi-party calls.

**Why now.** Local meeting transcription is a visible 2026 Show HN cluster (37 posts), Granola made botless capture the most-praised single-purpose assistant, and Rewind's shutdown left the screen and audio memory gap without a trusted incumbent. Most of the hard part (on-device STT, helper identity, calendar, search index) already ships.

**Where it goes.**
- app/dictation/main.swift:140-215 (enum Dash: post/get are synchronous loopback-only HTTP, no Origin). Add new routes here: poll GET /api/meetings/command and POST /api/meetings/segment|status.
- app/dictation/main.swift:1145 (30 s heartbeat Timer) and :1340-1359 (status POST). There is NO dashboard->helper channel today. The helper only POSTs and GETs /api/dictation/settings (:1365).
- app/dictation/main.swift:386-460 (AnalyzerLive). It is built for a short utterance (feeder.finish, then finalizeAndFinishThroughEndOfInput).

**Constraints.** Pitch assumes the dashboard can command the helper. It cannot today (helper is POST/GET client only, no listener). A poll channel must be added, and that is a helper rebuild plus a grant reset (ad-hoc cdhash changes, build-dictati… · 'needs app rebuild: false' is only true for Hermes Assistant.app. The helper rebuild is required.

**Prerequisites.** B02 (approval self-answer over the Origin-less API) should land first, or the new Origin-less start/stop/segment/forget routes need a helper token like /api/messages/ingest's. · B19 (unfenced untrusted text in unattended prompts): the summariser must be built on the fenced, tool-less path. Landing the fence helper first lets F19 reuse it.

**Risks.** System-audio capture TCC: macOS 26 offers Core Audio process/system taps (AudioHardwareCreateProcessTap, needs NSAudioCaptureUsageDescription, 'System Audio Recording Only' category, no screen-recording banner) and Scree… · SpeechAnalyzer on hour-long streams: not evidenced in repo. The helper only uses short push-to-talk utterances (main.swift AnalyzerLive finalizes at the end).

**Resources.** RAM: helper adds roughly 100-300 MB while capturing (two analyzer sessions plus buffers; unverified, measure). Battery: sustained on-device STT for the whole meeting, the highest new load of any shipped feature, so gate…

**Test plan.** Unit (offline, tools/tests/unit): pure functions with a scratch dir, in the style of t_a07_dictation_privacy.py. Cover: segment merge and dedupe of mic vs system echo; transcript chunker for map-reduce; retention prune (the days boundary, the 0600 mode on a fresh store); forget cascades to index_touch and the adapter returns None for an absent store;

**Sources.** <https://hn.algolia.com/api/v1/search?query=local%20meeting%20transcription&tags=story&numericFilters=created_at_i%3E1767225600&hitsPerPage=12> · <https://skillscouter.com/granola-review/> · <https://9to5mac.com/2025/12/05/rewind-limitless-meta-acquisition/>


### F20 — Steer, interrupt and branch controls for a running turn in the Agent view

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** Expose the serve RPCs for interrupting or steering a running turn and for watching delegated subagents, so the owner can redirect a long local turn (27B at 30-60 tok/s) instead of waiting or restarting it. Feature-detect each method at runtime and hide controls the connected Hermes does not support.

**Why now.** Upstream added live subagent steering in 0.21.0 and the current programmatic-integration doc lists session.steer, session.interrupt, subagent.steer and delegation status; the dashboard already uses session.branch for conversation branching, and long local turns are the main waiting cost.

**Where it goes.**
- dashboard/hermes_rpc.py:~403 (top of the `while time.time() < deadline` loop, next to `choice = job.pop("pending_choice", None)`): add `job.pop("pending_steer")` -> srv.call("session.steer",{session_id,text}) and `job.pop("pending…
- dashboard/hermes_rpc.py:~472 (`elif etype == "message.complete"`): read payload.status ("interrupted"|"complete"|"error", set by tui_gateway/server.py:8706) and stamp job["interrupted"]=True so the persisted bot message can be mar…
- dashboard/hermes_rpc.py:~472 event switch: handle subagent.start/tool/complete events (tui_gateway/server.py:3464-3609) into job["subagents"] for the watch panel; poll delegation.status only if the method probe says available.

**Constraints.** A04 fallback rule: interrupt/steer happen only after submit_sent, so the one-shot `hermes -z` fallback must never trigger; an interrupted job must finish via message.complete, not by raising (a WSError after interrupt must not rou… · A03: do not set job['done'] from the stop route; only _finish_chat_job may. Stop route must only set a mailbox field.

**Prerequisites.** None hard. Soft: B02 (approval self-answer via Origin-less API) should land first or at least be considered, since /api/chat/stop and /steer add two more state-changing routes the agent's terminal cou… · F08 (upgrade to newer Hermes) is NOT required: session.interrupt, session.steer, delegation.status, delegation.pause, subagent.interrupt all exist in installed 0.18 (tui_gateway/server.py:7835, 8104,…

**Risks.** Steer only lands at the next tool-batch boundary (run_agent.py:2719 docstring: appended to last tool result). On a pure-prose generation with no tool calls it is NOT applied during that generation; · Interrupt may not abort the in-flight mlx-vlm HTTP generation (agent.interrupt is cooperative; whether the streaming request is closed was not verified).

**Resources.** Negligible. No new threads, no polling when idle; one extra tiny RPC per control action; optional delegation.status poll (every few s) only while a turn runs and the method exists.

**Test plan.** unit (offline): extend the hermes_rpc fake-ServeSession test so a job with pending_interrupt triggers session.interrupt and a scripted message.complete{status:'interrupted'} ends run_turn with state=done, and that submit_sent=True prevents the _chat_worker fallback; pending_steer sends session.steer with exact text and a -32601/4010 reply sets can_steer=false without failing the turn;

**Sources.** <https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31> · <https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration>


### F21 — MCP server modernisation: tool annotations, structured output, spec-version check

Feasibility **fits** · effort **S** · value medium · main-app rebuild: no

**What.** Add readOnlyHint, idempotentHint and openWorldHint=false to every personal-context tool (and destructiveHint where a write key exists), titles, and outputSchema plus structuredContent for search, calendar_next and needs_you while keeping the text content as the fallback. In the same pass, verify what protocolVersion current Claude Code and other clients actually send and decide whether the 2026-07-28 stateless shape needs dual support now (the official posts leave stdio unaddressed).

**Why now.** Annotations and structured output have been in the spec since 2025 but the server uses none of them, and clients now use read-only hints to cut permission prompts. The 2026-07-28 revision is final and breaking (initialize and Mcp-Session-Id removed), with client adoption unverified.

**Where it goes.**
- dashboard/hermes_mcp.py:87 PROTOCOL_VERSIONS tuple: add 2025-11-25 at the front only after checking the spec text; LATEST_PROTOCOL derives from [0] (line 89).
- hermes_mcp.py:1380 TOOLS list: add an annotations dict per entry, or compute it once in tool_descriptors() from the tool name plus _writable() (avoids editing 16 literals).
- hermes_mcp.py:1592-1595 tool_descriptors(): currently emits only name/title/description/inputSchema. Add annotations, plus outputSchema for hermes_search, calendar_next, needs_you.

**Constraints.** No invariant collision found: read-only, local, no network/dependency/dashboard route/Swift change; the proxy rule is kept. · The 8 KB bound (_bound) works on text only. A structured dict has no bound today, so cap by item count and per-field length; truncating serialized JSON mid-string would give invalid structuredContent.

**Prerequisites.** None blocking. B02 (agent can answer its own approval prompts over the Origin-less API) touches the same surface (the MCP proxy sends no Origin by design). Do not add any write tool before B02 lands; · B19 (unfenced calendar text) is related: calendar_next structured output returns raw event titles. _plain() strips markup and control characters but does not mark the text as untrusted for a model.

**Risks.** outputSchema is a contract: a client validating structuredContent rejects a mismatch. Every error path must return isError:true with no structuredContent, and the needs_you 'building' branch (line 807) must be an error o… · A schema bug turns a working tool into a failing one in strict clients. Mitigate with a tiny stdlib schema check in the test (jsonschema is not available).

**Resources.** Negligible: a few hundred bytes per tools/list and a small dict per structured call. No RAM, battery, disk or network change. The process is spawned per client and idle between calls.

**Test plan.** unit tier (offline, tools/tests/unit): note the current MCP harness is live/t_mcp.py, not unit. Add a pure unit test that imports hermes_mcp with HERMES_MCP_ALLOW pointing at a temp file and checks: (a) every tool_descriptors() entry has annotations with openWorldHint false;

**Sources.** <https://modelcontextprotocol.io/specification/2025-11-25/changelog> · <https://blog.modelcontextprotocol.io/posts/2026-07-28/> · <https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/>


### F22 — files_search over granted folders, exposed via the index and MCP resource links

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** A Files source in the unified FTS index limited to folders the owner granted under /api/access (filenames plus a bounded text slice, with optional markitdown conversion for PDF/Office in an isolated venv), a Files filter in Search everything, and an MCP files_search tool that returns resource_link entries instead of inline blobs so the 8 KB cap stops truncating. Off by default in the MCP allowlist like messages_search; snippets only, never whole files; secret-looking files excluded.

**Why now.** files_search was the one missing tool in the MCP server and other agents most often ask for local files; the 2026-07-28 spec names resource URIs as the replacement for roots and adds read cache hints, which gives a clean shape. The pieces (granted folders, FTS adapters, redaction) all exist.

**Where it goes.**
- dashboard/aux_index.py:93 IX_SOURCES tuple: add 'file'
- dashboard/aux_index.py:625 _IX_ADAPTERS: add 'file': _ix_src_file, returning None when access.json is absent or no granted dir is readable, a list otherwise
- dashboard/aux_index.py ~584 (before _ix_src_watchtower): new _ix_src_file(known) plus a bounded walker; resolve get_access() via _ix_g by name at sweep time (aux_index loads before other aux files;

**Constraints.** Granted folders (access.json) were granted so the AGENT can read them; indexing them makes content searchable by the dashboard UI and any MCP client, which is a wider audience. · TCC: the dashboard runs under launchd python, which has no Full Disk Access; ~/Documents and ~/Desktop reads can fail with EPERM (CLAUDE.md 'Not yet done').

**Prerequisites.** B02 (agent can self-answer approval/consent prompts over the Origin-less API, audit table row B02): the new opt-in toggle and any /api/access change are consent surfaces the agent could flip itself; · B19 (unfenced untrusted text in prompts): file snippets returned by the MCP tool and any future in-agent search are the same injection class;

**Risks.** Secret leakage: _redact in hermes_mcp is only three regexes aimed at chat export (API-key shapes); it is not a file-content scrubber. · A granted folder that is a home directory or contains a repo would index node_modules, .git, browser profiles; walker must hard-exclude dotdirs and known app-data dirs (recent_files only skips dot-prefixed and 3 names)

**Resources.** RAM: negligible if streamed (read at most ~64 KB per file, store at most ~20-50 KB body per file). Disk: index.db grows roughly the stored text, e.g. 2000 files x 20 KB = ~40 MB plus FTS inverted index ~ similar;

**Test plan.** unit (offline, tools/tests/unit): build a temp HERMES data dir with an access.json pointing at a tmp tree; assert (1) _ix_src_file returns None when access.json is missing and when every root raises PermissionError, and a list otherwise; (2) a root that raises EPERM keeps its prior rows (no prune) while a deleted file in a readable root is pruned;

**Sources.** <https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/>


### F23 — Selection and front-window context for Quick Ask (opt-in per invocation, no screenshots)

Feasibility **fits** · effort **M** · value medium · main-app rebuild: no

**What.** When Quick Ask opens, offer a visible, removable chip with the frontmost app, window title and any selected text, read through the already-authorised cua-driver (Accessibility) rather than the frozen Swift app. The owner sends or removes the chip; nothing is captured continuously. A screenshot-region option stays a stretch goal gated on confirming the primary build exposes vision.

**Why now.** Apple's new Siri and Google's Gemini Mac app both centre on 'ask about what is on screen' with a global shortcut, and the owner already has a global hotkey, clipboard transforms and cua-driver with Accessibility and Screen Recording. A selection chip delivers most of the value without the vision model and app rebuild that blocked screenshot-to-answer before.

**Where it goes.**
- dashboard/aux_quickask.js:1018 send(text): prepend/attach the chip's fenced block before j('/api/chat', {message,...}) at ~:1033; chip element rendered in the POP composer and removable
- dashboard/aux_quickask.js:1267 and :1316 W.__qaFocus: the hook Swift calls on show (app/main.swift:570-573) is where the chip fetch (GET /api/context/front) should be kicked off, once per invocation
- NEW dashboard/aux_frontctx.py (aux files exec sorted, loaded via the existing loader): register_get('/api/context/front') using the same register_get pattern as dashboard/aux_clip.py:250;

**Constraints.** No violation of stdlib-only, local-first, no telemetry, or frozen-app rules: no Swift change needed. The claim needs_app_rebuild=false holds. · The invariant to respect: selected text is third-party, possibly attacker-authored web text entering a prompt on a full-toolset agent.

**Prerequisites.** B02 (agent answers its own approval prompts over the Origin-less API; audit table line 61): should land first or at least be acknowledged, because this feature feeds attacker-controlled text into a to… · B19 (unfenced untrusted text in prompts, line 78) and the F07 fence: build the shared fence helper first, then F23 consumes it rather than adding another copy.

**Risks.** Frontmost-app recovery: because Hermes activates first, 'previous app' is inferred from z-order, which can be wrong with multiple Spaces/full-screen apps or a hotkey pressed while Hermes was already frontmost (then there… · Selected text via AX is not guaranteed: get_window_state returns element value/label, and I did not verify it exposes AXSelectedText.

**Resources.** RAM: negligible (one subprocess, short-lived; cua daemon already running at pid 40026). Battery: one AX walk per Quick Ask open only, no polling, nothing continuous. Disk: none unless chips are saved into chats.

**Test plan.** unit (offline): pure function that builds the chip payload from canned cua-driver JSON (z-order list with Hermes pid at top, secure-field element, oversize selection, selection containing '>>>' / '<<<' / control chars / 'ignore previous instructions');

**Sources.** <https://techcabal.com/2026/06/08/wwdc-2026-announcements/> · <https://9to5google.com/2026/04/15/gemini-app-mac/>

