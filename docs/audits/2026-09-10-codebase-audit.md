# Hermes Assistant codebase audit — 2026-09-10

Reviewed version **1.3.0**, commit **98c29e0**. Found **10 reproducible defects: 3 P1 and 7 P2**. P1 means fix soon because ordinary use can lose conversation data or violate a privacy preference. P2 means a meaningful defect with a narrower trigger.

Source line numbers refer to that baseline commit. Concurrent edits appeared later in this shared checkout; all ten probes were rerun successfully against those edits, but the baseline unit-suite and Swift results do not certify that separate work.

The highest priorities are preserving explicit privacy choices and making concurrent writes transactional. Passing the existing tests does not cover the interleavings that expose these failures.

**Scope and verification**

- Inventoried the repository and checked syntax across 81 tracked Python files, 21 shell scripts, and 38 dashboard JavaScript files. No syntax errors; the vendored `motion.min.js` was excluded as in CI.
- Ran all 21 unit suites: **1,615 checks passed**. The first sandboxed run passed 1,396 checks; two HTTP harnesses could not bind their test ports. Reran just those two with loopback permission and the offline guard: another 119 and 100 checks passed.
- Both native entry points passed Swift type-checking. Dictation was checked against its declared macOS 14 deployment target. No app was rebuilt or installed.
- Examined chat/RPC, settings, permissions, recording/undo, memory/search, Google connection, updates/installers, the native shell, and dictation. Semantic review was focused on these workflows; this is not a claim of exhaustive line-by-line coverage of all approximately 63,000 application lines.
- Added [offline reproductions](reproduce_20260910.py) for all ten findings. They extract actual function definitions and use temporary files and fake services. They do not load a model, execute a tool action, contact a cloud provider, or change live settings.
- Live model interactions, real OAuth exchanges, GUI behavior, and actual update/restore operations were not exercised. Mocked integration boundaries and static traces are identified below.

Run the evidence script from the repository root:

```bash
python3 docs/audits/reproduce_20260910.py
```

These are audit probes: `CONFIRMED` means a defect is present. Convert each probe into a regression assertion for the correct behavior when implementing its fix.

**Findings**

| ID | Priority | Finding | Primary location |
|---|---|---|---|
| A01 | P1 | Background settings writes can turn Claude back on after opt-out | `dashboard/server.py:2129`, `dashboard/server.py:2150` |
| A02 | P1 | Cloud escalation defaults on and fails open | `dashboard/aux_claudebridge.py:92`, `dashboard/aux_claudebridge.py:331` |
| A03 | P1 | Concurrent chat appends silently overwrite each other | `dashboard/server.py:1961` |
| A04 | P2 | CLI fallback resumes the wrong session and retries after submission | `dashboard/server.py:969`, `dashboard/server.py:978` |
| A05 | P2 | WebSocket parsing loses data across ordinary read boundaries | `dashboard/hermes_rpc.py:74`, `dashboard/hermes_rpc.py:108` |
| A06 | P2 | Large-file undo skips conflict detection | `dashboard/aux_recorder.py:1178` |
| A07 | P2 | In-flight dictation can persist text after history is disabled | `dashboard/aux_dictation.py:763`, `dashboard/aux_dictation.py:804` |
| A08 | P2 | Concurrent requests launch overlapping updates | `dashboard/aux_update.py:636`, `dashboard/aux_update.py:696` |
| A09 | P2 | Fresh installs can start the model merely by opening the app | `app/main.swift:15`, `mlx-server.sh:15` |
| A10 | P2 | Config export discards valid world-clock settings | `dashboard/aux_config.py:152` |

**A01 — Background settings writes can turn Claude back on after opt-out.**

`weather()` captures the entire settings object before waiting for geocoding. It later writes that stale object under `_state_lock`. Locking only the final write does not protect the earlier read. If a user disables Claude while geocoding is in flight, weather's response restores the old `enabled: true`. Other settings can be overwritten the same way. `_cb_set_escalation()` also bypasses `_state_lock` entirely, and multiple writers use the same `.tmp` path.

The reproduction invokes the real weather and switch functions with a fake geocoding response. It checks that the switch is false after the user's action, then true after weather saves.

**Fix:** Introduce one settings-update helper that takes the shared lock, reads the current object, merges only the requested fields, and atomically writes it. Perform network requests outside that transaction. Convert all writers, including weather, the bridge, autorouting, and the update channel. Coordinate external writers through a process-safe lock or transactional store if they share this file.

**A02 — Cloud escalation defaults on and fails open.**

`CB_ESC_DEFAULT = True`; missing, corrupt, or unreadable settings fall back to that value. Autorouting separately defaults to `auto`, and onboarding treats an absent choice as enabled. This contradicts the README's statement that Claude Bridge is off until enabled. On a Mac with a working authenticated Claude CLI and bridge prompt, qualifying chat prompts can therefore be sent to Claude without an explicit enable action. A settings read failure also does not preserve a prior opt-out.

The probe verifies the gate for both absent and corrupt settings and the router's default. It does not invoke Claude. Actual outbound transmission depends on the CLI, authentication, bridge prompt, and routing score.

**Fix:** Default outbound inference to false and require an explicit boolean true. Make read errors disable outbound calls and surface the configuration problem. Align onboarding, the router, install defaults, and documentation with the same policy. Treat this as a product privacy decision, not simply a label change.

**A03 — Concurrent chat appends silently overwrite each other.**

`save_chat()` detects only a reduction in message count. Two workers can read N messages, independently append a different answer, and each save N+1 messages. Both writes succeed; the second replaces the first answer. Equal-length stale snapshots can also overwrite metadata or branch registrations. The actual application has parallel local and Claude workers, and publishes `job.done` before `_finish_chat_job()` persists the local reply, so the race does not require an external caller.

The reproduction loads the same conversation twice, appends different replies, and saves both. Only the second reply survives, despite both saves succeeding.

**Fix:** Use a transaction or per-session lock around the complete read/modify/write operation. Give messages stable IDs and append explicitly; use a revision check for metadata edits. Mark the job complete only after its final reply is persisted. Keep branching/deletion within the same session consistency rules.

**A04 — CLI fallback resumes the wrong session and retries after submission.**

`_chat_worker()` passes the dashboard's conversation filename key to `run_agent()`, which turns it into `hermes --continue <key>`. The serve backend has a different durable ID, already stored in `chat['serve_key']`. The installed agent's `cmd_chat()` resolves `--continue` against agent IDs or titles and exits with an error when it cannot find one. Ordinary `chat-...` dashboard IDs therefore make the supposed recovery path fail even when a valid serve session exists.

The same exception handler also retries after `prompt.submit` and even after tool events. Once session handling is corrected, a blind retry could repeat work already performed. The current probe verifies the wrong key and the post-submission retry attempt. **It does not establish that the current normal UI path successfully executes a tool twice**: the invalid continuation ID commonly prevents that.

**Fix:** Track whether submission occurred. Allow CLI fallback only before submission, using the correct durable session or an explicitly new session. After submission, reconnect or query the original turn's state; otherwise report that its outcome is uncertain. Do not silently resubmit an action-bearing prompt. Add integration coverage for the installed CLI's continuation contract.

**A05 — WebSocket parsing loses data across ordinary read boundaries.**

Three related parser errors are reproducible:

- The HTTP upgrade can share a read with a WebSocket frame, but the constructor sets `_buf` to empty and discards the bytes following the header terminator.
- Fragment assembly lives in a local `message` variable. A timeout between fragments returns `None` and forgets the prefix; the next call returns only the continuation.
- Only the initial two-byte header read catches `socket.timeout`. A timeout while reading an extended length or payload escapes into the chat fallback after the header has already been consumed.

The probe supplies these legal byte arrangements through a fake socket. It observes a discarded first frame, a reply of `def` instead of `abcdef`, and an escaping payload timeout.

**Fix:** Preserve the upgrade remainder and maintain parser/fragment state across calls. Buffer an entire frame before consuming it, with bounded sizes and a monotonic deadline. If a dependency is acceptable, use a maintained WebSocket implementation; otherwise add byte-boundary, fragmentation, and timeout tests for the custom client.

**A06 — Large-file undo skips conflict detection.**

`_after_state()` intentionally omits a hash above 32 MiB, retaining size and modification time. The undo handler claims a size/mtime fallback in its comment but implements only the hash check. Editing a larger file after the agent wrote it therefore does not trigger a conflict, and the handler proceeds to restore with `force: false`. Hash-read failures and some file existence changes also bypass the current check.

The probe creates a sparse file just over the threshold, records its real after-state, changes its size, and verifies that the handler calls the mocked checkpoint restore. It does not perform a real restore. A pre-rollback checkpoint may aid recovery, but it does not replace the missing conflict warning.

**Fix:** Compare existence, type, size, and mtime when a hash is unavailable; treat inability to establish state as an unresolved conflict. Require explicit force before overwriting a changed target. Consider hash verification on demand for higher assurance.

**A07 — In-flight dictation can persist text after history is disabled.**

`_dct_finish()` snapshots settings before model cleanup. Turning history off scrubs existing rows, but the in-flight request still uses its old `keep_history: true` and appends a new row containing the transcript. `_dct_record()` never checks the current privacy setting. The UI may hide that row while the text remains on disk.

The reproduction changes the live setting to false and invokes the real scrub during a fake model cleanup. After completion, the real temporary JSON store again contains the transcript.

**Fix:** Make the privacy decision at persistence time. Recheck the current setting while serializing with the history scrub, and strip text under that lock when history is off. Define a consistent lock order to avoid introducing a deadlock between settings and history.

**A08 — Concurrent requests launch overlapping updates.**

`_upd_apply()` checks whether an updater is running and writes `running: true` only after spawning a subprocess. No lock covers the check/spawn/state sequence. Two browser windows, or closely overlapping requests, can both start an update. The shell updater has no overarching process lock either, so dashboard and terminal updates can compete over Git, installed files, services, logs, and result state.

The probe synchronizes two calls at the running check and verifies two successful fake spawns. It does not run `update.sh` or modify the checkout.

**Fix:** Acquire one lock for the whole update, held across the detached process lifetime. Use it for both CLI and dashboard invocations. Atomically reserve an update before spawning and clean up a failed reservation.

**A09 — Fresh installs can start the model merely by opening the app.**

The native app includes the primary model in `SERVICES` and calls `launchctl kickstart` during launch. `mlx-server.sh` refuses an unsolicited start only if `model-autostart-off` already exists. Neither installer creates that marker. `RunAtLoad: false` stops login autostart but does not prevent the native app's explicit kickstart. Existing machines with the marker behave differently from fresh installs; on a fresh install with downloaded weights, opening the hub can load the model without a chat request.

The probe executes only the actual startup gate with temporary paths and verifies it falls through without a marker. The installer and Swift call chain were traced statically. No launchctl or model command was executed.

**Fix:** Remove the model from unconditional app startup and let the dashboard's explicit wake path start it. Make on-demand behavior the default in the launcher/install process, with a deliberate setting for always-on behavior. Verify this against an empty installation state.

**A10 — Config export discards valid world-clock settings.**

Both world-clock providers consume `[label, timezone]` pairs. `_cfg_clean_settings()` accepts only strings in the `timezones` list, so an actual configuration such as `[["Paris", "Europe/Paris"]]` becomes `[]` during export. Importing the snapshot then restores defaults instead of the user's chosen clocks. Conversely, the sanitizer accepts strings the widget does not expect.

The probe passes valid pairs through the real sanitizer and observes an empty list.

**Fix:** Validate and preserve two-string pairs, including valid zone identifiers. Add an export/import round-trip test built from real widget settings shapes.

**Improvement paths**

1. **Centralize persistence first.** The repeated pattern is reading state, doing other work, and then replacing the entire object. Shared settings transactions and per-session chat operations address several findings at once. Preserve atomic replacement, but do not treat it as protection from lost updates.
2. **Give features explicit startup and dependencies.** `server.py:3630` executes auxiliary files into shared globals, and several auxiliary modules start threads during import. Move incrementally toward modules exposing route registration and a separate startup function. Report failed feature initialization through health checks. This will make the application easier to test and reduce global-name/load-order coupling.
3. **Test service contracts and failure timing.** Add fake-server coverage for RPC disconnects, multiwriter persistence, privacy changes during requests, update exclusion, fresh installation, and snapshot round trips. Add Swift type-check/build checks to CI. The existing broad suite is valuable, but these ten cases currently fall outside it.
4. **Make request validation consistent.** `Handler._body_json()` accepts arbitrary JSON shapes and has no general body-size limit; inline routes then assume dictionaries and particular value types. Validate objects and route inputs before mutation, bound request bodies, and return consistent errors. Apply the same discipline to settings accepted by the generic settings endpoint.
5. **Coalesce repeated background fetches.** `hub_data()` and `hub_prewarm_loop()` submit work to the same pool; `_cached()` has no per-key in-flight protection. Under slow feeds, repeated clients can duplicate expensive work. Share one in-flight result per provider, bound queued work, and keep serving a clearly marked last-good value. Measure latency before changing worker counts.
6. **Align installation templates and documentation with current behavior.** Besides the privacy and startup findings, the Google MCP stub in `config.yaml` and the README describe scopes/drafting differently from `aux_google.py`'s current OAuth flow. Test the shipped defaults and keep one source of truth for enabled integrations, consent, and model startup.

Suggested order: **A01–A03 first; A04–A05 together; A06–A09 next; A10 as a small independent fix.** Build the persistence and regression-test improvements alongside those fixes, then undertake the module cleanup in small steps.

Only this report and the reproduction script were authored for the audit. This audit did not change application code or live settings. Concurrent edits to three existing test fixtures, `dashboard/server.py`, `dashboard/aux_google.py`, and `app/dictation/main.swift` were left untouched.

---

## Resolution (2026-09-10)

All ten findings are fixed, each with a regression suite under `tools/tests/unit/`.
Those suites assert the CORRECT behaviour, so they fail if the defect returns —
unlike `reproduce_20260910.py`, which asserts the defect and is now expected to
stop reproducing. That script is kept as the historical evidence; it is not
maintained against the fixed code (its `store_namespace()` predates
`_save_chat_locked`/`save_chat_update`/`settings_update`, so `main()` stops at
its first probe — call the individual probe functions with those definitions
added if you want to re-run one).

| ID | Priority | Finding | Status | Regression test |
|---|---|---|---|---|
| A01 | P1 | Background settings writes can turn Claude back on after opt-out | Fixed | `tools/tests/unit/t_a01_settings_transaction.py` (51 checks) |
| A02 | P1 | Cloud escalation defaults on and fails open | Fixed | `tools/tests/unit/t_a02_escalation_default.py` (21 checks) |
| A03 | P1 | Concurrent chat appends silently overwrite each other | Fixed | `tools/tests/unit/t_a03_chat_concurrency.py` (38 checks) |
| A04 | P2 | CLI fallback resumes the wrong session and retries after submission | Fixed | `tools/tests/unit/t_a04_cli_fallback.py` (34 checks) |
| A05 | P2 | WebSocket parsing loses data across ordinary read boundaries | Fixed | `tools/tests/unit/t_a05_ws_frames.py` (39 checks) |
| A06 | P2 | Large-file undo skips conflict detection | Fixed | `tools/tests/unit/t_a06_undo_conflict.py` (20 checks) |
| A07 | P2 | In-flight dictation can persist text after history is disabled | Fixed | `tools/tests/unit/t_a07_dictation_privacy.py` (18 checks) |
| A08 | P2 | Concurrent requests launch overlapping updates | Fixed | `tools/tests/unit/t_a08_update_lock.py` (16 checks) |
| A09 | P2 | Fresh installs can start the model merely by opening the app | Fixed | `tools/tests/unit/t_a09_model_autostart.py` (21 checks) |
| A10 | P2 | Config export discards valid world-clock settings | Fixed | `tools/tests/unit/t_a10_timezone_roundtrip.py` (9 checks) |

**What A01/A03 changed structurally**, since it is the "centralize persistence
first" improvement path and the next writer needs to know: `settings_update(
mutate_fn)` / `settings_set(**fields)` in `server.py` are now the ONLY way to
write `settings.json` (read + mutate + atomic write under `_state_lock`, no slow
work inside), and `save_chat_update(session, mutate_fn)` is the only way to
read-modify-write a conversation. Conversations carry a `rev` that every locked
save bumps, and `save_chat()` refuses a snapshot behind the file on disk.
`write_json` writes through a unique `O_EXCL` temp file rather than a shared
`<path>.tmp`. A chat job's `done` is published by `_finish_chat_job` only after
the reply is persisted, with `persisted: true/false` beside it.

Verified after the fixes: `tools/tests/run.sh unit` — 40 suites, 2,253 checks,
0 failures; the dashboard restarted clean, `GET /api/claude/escalate` still
`false`, and a weather widget + pop-out refresh left `settings.json`
byte-identical (its coordinates were already cached, so no geocode ran).
