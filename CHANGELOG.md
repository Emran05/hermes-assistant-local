# Changelog

All notable changes to Hermes Assistant are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.3] - 2026-09-04

Memory defaults that fit the Mac they run on, a reproducible model-server venv, and the
measured facts behind them (`docs/plans/post-v1-baseline.md`).

### Changed
- **RAM-aware defaults.** The memory-guard ceilings (`MLX_SOFT_GB` / `MLX_HARD_GB`), the
  prefix-cache entry count (`APC_EXACT_CACHE_ENTRIES`) and the mlx-lm prompt-cache size
  are now derived from physical RAM unless set explicitly; on a 64 GB+ machine they equal
  the previous constants (50 / 56 GB, 6 entries, 8 GB), smaller machines get proportionally
  smaller values so the guard actually engages before macOS starts swapping.
- **Per-model fit hint** in the model menu: "needs ~19 GB · this Mac has 64 GB", amber when
  tight, red (and neither switchable nor downloadable) when the model cannot fit.
- **Reproducible venv.** `install-mlx-vlm-venv.sh` now pins mlx, mlx-metal, transformers and
  huggingface_hub to the versions in production, not just mlx-vlm. A fresh install today
  was silently resolving newer mlx/transformers than the author's machine.

### Measured (no code change)
- mlx-vlm 0.6.16 / 0.6.17 corrupt output when two requests overlap with the MTP drafter
  loaded (2 of 6 and 1 of 3 clean probes vs 6 of 6 on 0.6.14); single-stream they are a
  wash (prose −8%). The venv stays on 0.6.14 until this is fixed upstream.
- MTP speculative decoding falls through to continuous batching (it is not disabled), but
  under two concurrent streams it yields 21.2 tok/s per stream (42.5 aggregate) versus
  59.8 aggregate without a drafter. It is a single-stream win — which is how the primary
  lane is used; background work stays on the 9B lane.
- Shims: the launcher's RNG-restore patch only matters for non-MTP drafters; the
  `os._exit(0)` teardown guard is still required on mlx 0.32.1 (fixed in 0.32.2).


## [1.0.2] - 2026-09-04

Conversation management and a readable "What's new" — backlog items #13 and #14.

### Added
- **Search across conversations.** A search field above the conversation list (full
  chat mode; a magnifier toggle in split mode) matches text in any past turn,
  shows a highlighted snippet, and Enter/click opens the conversation and flashes the
  matching bubble. `GET /api/sessions/search?q=` (message text only, hidden sessions
  excluded, 50 results, plain-text snippets with match offsets — the client escapes and
  highlights; the server never emits markup).
- **Pin and rename conversations.** Hover a sidebar row for pin / rename / delete
  (32 px targets); pinned rows sort first under a "Pinned" label; rename inline
  (Enter saves, Esc cancels). `POST /api/sessions/meta {session, pinned?, title?}`,
  titles capped at 80 characters with control characters stripped.
- **Export as Markdown.** From the chat toolbar: a Markdown transcript with
  `**You**` / `**Hermes**` turns, Claude answers as labelled quotes, tool and approval
  rows omitted, and session tokens or `~/.hermes` paths redacted.
  `GET /api/sessions/export?session=` serves it as an attachment; inside the app the
  WebView cannot download, so the text is copied to the clipboard with a toast.
- **What's new in the updater.** Settings › System & Data › Software update now renders
  release notes as Added / Changed / Fixed / Security groups (bold lead-ins and inline
  code preserved, everything else escaped, "Show all" past three items) and shows the
  installed version's notes when you are up to date (`GET /api/update/notes?version=`).

### Changed
- `GET /api/sessions` rows carry `pinned`; pinned conversations survive the 30-row cap.
- Aux modules can now return non-JSON responses (`RawResponse`) — used for the Markdown
  export's `Content-Disposition`.


## [1.0.1] - 2026-09-04

Small, measured follow-ups to 1.0.0 from the post-v1 backlog
(`docs/plans/post-v1-backlog.md`; baseline numbers in `docs/plans/post-v1-baseline.md`).

### Added
- **Prewarm after wake.** After an idle-suspend wake the dashboard now runs one
  throwaway turn through the serve backend so the ~18k-token system prompt is in the
  prefix cache before your first real message. Measured through the real path on the
  M5 Max (idle-suspend wake → first token of the next turn): **29.1 s before, 1.7 s
  after**; the warm-up itself takes ~27 s in the background right after the wake. It
  fires after a wake with no message pending ("Wake now", the popover's status strip,
  a Telegram-triggered wake); if you type into a sleeping dashboard your own message
  already does the prefill, so no second one is started. Off switch:
  `POST /api/agent/prewarm {"enabled": false}`; state in `/api/models.prewarm`. The
  warm-up session never appears in the conversation list and never counts as user
  activity for the idle clock.

### Changed
- Release source tarballs no longer include `.claude/`, `skills-snapshot/`,
  `graphify-out/` or `docs/state-snapshot.json` (`.gitattributes` export-ignore).
- Efficiency baseline recorded: MTP draft block 3 stays the default (best prose,
  second on code); block 4 wins code by ~8% but loses prose by ~7%; no drafter is
  ~1.5-2x slower on decode.

### Fixed
- **Free Memory reports the truth**: the model-menu action polls the real restart
  outcome (`GET /api/model/mem_free/status`) instead of a blind 4 s timer and shows the
  error when the restart fails.
- Toggling Thinking no longer claims `restarted: true` when the server restart failed.
- The clipboard sheet closes on Esc.
- Agent Desktop screenshots are recorded as read-only captures (no "irreversible"
  badge in the Flight Recorder).


## [1.0.0] - 2026-09-03

The first release meant to be installed by someone other than its author:
a bootstrap script, a self-update path, and a security pass on the local API.

### Added
- **Menu-bar Quick Ask, rebuilt.** The popover (⌃⌥Space) is now a control
  surface: a status strip that wakes or pauses the model and toggles Claude
  escalation with one tap, an ask field that is never locked by model state (a
  send while asleep wakes it), one-tap clipboard actions (Summarize / Explain /
  Rewrite), Plan my day and Ask Claude, `/` to filter actions, streaming answers
  with inline Approve / Deny, Claude answers shown as their own card (they were
  silently dropped before), Copy / Continue-in-main on every answer, and a
  height that follows the content (320–620 px).
- **Release + update system.** `VERSION` at the repo root is the single source
  of truth; `app/build-app.sh` stamps it (plus the short git sha) into the app
  bundle's `CFBundleShortVersionString` / `CFBundleVersion`. A new dashboard
  module (`dashboard/aux_update.py` + `aux_update.js`) adds **Settings › System
  & Data › Software update**: current version, a `stable` / `main` channel
  selector, "Check for updates", release notes, a live log while updating, and
  a dot on the header gear when something is waiting. Endpoints:
  `GET /api/version`, `GET /api/update/check`, `GET /api/update/status`,
  `POST /api/update/apply`, `POST /api/update/channel`.
- **`update.sh`** — one idempotent script for both install shapes: a git
  checkout (fetch tags, check out the release tag, or fast-forward
  `origin/main` on the `main` channel) and a tarball install (download the
  release source tarball, verify it against `SHA256SUMS`, rsync it in). It
  re-runs `install-services.sh`, leaves the on-demand model servers asleep,
  never touches `~/.hermes` data, and logs everything to
  `~/.hermes/logs/update.log`.
- **`install.sh`** — fresh-Mac bootstrap: preflight (macOS 14+, Apple Silicon,
  Xcode command line tools, Python 3.12+) with an exact remediation line per
  failure and no silent system installs, `~/.hermes` scaffolding, first-run
  seeds of `.env` and `config.yaml` (never overwriting existing ones), a
  pointer to the Hermes Agent CLI installer, the optional mlx-vlm venv, an
  opt-in `--app` build, and finally the launchd services. `--dry-run` prints
  the plan.
- **Continuous integration** (`.github/workflows/ci.yml`): every Python file
  compiled, every shell script parsed, every dashboard JS file `node --check`ed,
  and a hygiene gate that fails the build on a committed home-directory path.
- **Release automation** (`.github/workflows/release.yml`): pushing a `vX.Y.Z`
  tag verifies it against `VERSION`, builds and signs the app (Developer ID +
  notarisation when the Apple secrets are configured, ad-hoc otherwise),
  publishes the app zip, a source tarball and `SHA256SUMS`, and takes the
  release notes from this file.
- **Uncensored model option.** `orcarouter/Qwen3.8-27B-Uncensored-MLX` — the
  abliterated build of the same 27B primary, same tokenizer, template and
  mlx-vlm backend — is now a roster entry. It is **opt-in and never the
  default**: you pick it in the model menu. New roster fields came with it
  (`ignore_patterns`/`allow_patterns` so a 95 GB repo pulls only the ~17 GB you
  need, `draft_subfolder` for a drafter that lives inside the model repo, and
  `hf_offline` for fully offline loads).
- **README, LICENSE (MIT), SECURITY.md** and this changelog.

### Changed
- **UI consistency pass** across the Hub · Agent · Settings restructure: the
  twelve settings panels behind one nav rail, legacy Mind cards relocated
  automatically, Agent rails absorbing the old Console and Desktop views, and
  the header reduced to a segmented switch plus status chips.
- Documentation scrubbed of personal identifiers (bot handle, Telegram user id,
  email address, home paths) so the tree works on any Mac.

### Fixed
- **Model downloads.** The dashboard's Python has no `huggingface_hub`, so every
  download started from the model menu had been failing silently; downloads now
  run through an interpreter that actually has it, and report a reason when they
  cannot. "Downloaded" now means *every shard in
  `model.safetensors.index.json` is present* — the old "any `.safetensors`"
  check offered a switch at 0-of-3 shards.
- **Model lifecycle hardening.** A pause now verifies the launchd bootout before
  writing its marker (a false "paused" made the memory guard and idle-suspend
  stand down over a live model); the chat worker prints why it fell back instead
  of failing silently; snapshot resolution matches the loader's; and every
  start/switch path propagates a real error instead of reporting "loading".

### Security
- **Same-origin guard on the whole API.** The dashboard binds `127.0.0.1:7788`
  with no token, cookie or CSRF defence, and nearly every route changes real
  state — so any web page you happened to visit could `fetch()` it (a
  `text/plain` POST is a "simple request": no preflight, and the attacker never
  needs to read the response), and DNS rebinding exposed every GET. One
  pre-dispatch check now runs on every verb: the `Host` header must be a
  loopback name we serve on, a present `Origin` must match it, and
  `Sec-Fetch-Site: cross-site` is refused on state-changing verbs. Requests with
  no `Origin` (curl, the launchd scripts, the app's own POST) still work, and no
  CORS headers are ever added.
- **One master switch for the second brain.** `claude_escalation.enabled`
  gates the only function that shells out to `claude -p`, so the auto-router,
  the manual Escalate button and the For-You producer are all covered by a
  single setting.

## [0.2] - 2026-08

### Added
- **Message Center.** The native app (which holds Full Disk Access; a launchd
  Python process cannot) snapshots `~/Library/Messages/chat.db` with SQLite's
  online backup and posts it to a token-guarded ingest endpoint, so recent
  iMessage threads appear as a hub widget without the agent ever getting
  database access.
- Proactive intelligence: the You-Model, the "For you" brief, and the watchtower
  rules engine with Telegram delivery.
- The Claude Bridge — an optional deep-reasoning escalation path — plus the
  auto-router that decides when a turn deserves it.
- Rich pop-outs for every hub widget, and the Agent page (rails, dialogue,
  desktop view).

## [0.1] - 2026-07

### Added
- First working assistant: the local MLX model server, the Hermes Agent
  backend, and the Liquid Glass dashboard on `127.0.0.1:7788`.
- Phase 1 "earn trust": editable memory, the flight recorder with undo,
  graduated permission tiers with safety floors, a metrics baseline, and
  config-as-code export/import.
- launchd services for the model server, dashboard and agent backend.

[Unreleased]: https://github.com/Emran05/hermes-assistant-local/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Emran05/hermes-assistant-local/releases/tag/v1.0.0
[0.2]: https://github.com/Emran05/hermes-assistant-local/releases/tag/v0.2
[0.1]: https://github.com/Emran05/hermes-assistant-local/releases/tag/v0.1
