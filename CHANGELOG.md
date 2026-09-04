# Changelog

All notable changes to Hermes Assistant are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-09-03

The first release meant to be installed by someone other than its author:
a bootstrap script, a self-update path, and a security pass on the local API.

### Added
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
