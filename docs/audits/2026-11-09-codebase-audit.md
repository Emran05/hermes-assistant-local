# Hermes Assistant codebase audit — 2026-11-09

Reviewed **1.3.2 plus `[Unreleased]`** at commit **dca0d3a** (clean tree). The
live dashboard (pid 8647, up since 2026-10-28) runs the same commit, so the
running code is the reviewed code.

Found **4 P1, 29 P2 and 89 P3** new defects (E-series below), plus 16 false
statements in the documentation. Priorities use the same definitions as
the last two audits:
- **P1** — ordinary use can lose data, silently hide something the owner
  needs, break an explicit privacy choice, or let the agent get past the
  human approval gate.
- **P2** — a real defect with a narrower trigger.
- **P3** — low impact, hardening, polish, or a false doc claim.

IDs continue the series: A01–A10 (2026-09-10), B01–B30 (2026-10-08),
C01–C135 (2026-10-21), now **E01 onward**. "D" is skipped because round 3
used D3–D14 for owner decisions. Numbers have gaps.

**None of the four P1s is live on this Mac today.** Each needs a state this
Mac is not in: an Undo click on an agent edit (the recorder has no write rows
yet), a connected GitHub repository, a Trust class set to Auto (the policy is
empty), or Full Disk Access for the Message Center (currently denied). They
are P1 because each becomes reachable through ordinary use of a shipped
feature.

Ratings where the orchestrator and a verifier disagree, with the reasoning
given at each finding:
- E03 and E04 were rated P2 by their verifiers and are kept at P1.
- E11 was rated P1 by its verifier and is filed at P2, alongside E10.
- Three more reviewer P1s are P2 on their verifiers' evidence: E10, E16, E29.

## Scope and verification

- **Tests.**
  - Unit tier, strict mode, at dca0d3a: **12,797 / 12,797 pass**, no skips on
    this Mac.
  - The three hermetic real-engine animation suites pass (55, 64 and 68
    checks), as do `tools/vendor-anim.sh --check` and the two vendor suites.
  - Not run: the live tier (it sends requests to the running dashboard), the
    browser tier through `run.sh`, `t_onboarding_real.js`, and the `ac` tier.
  - **CI cannot currently pass** on a machine without the mlx-vlm venv (E30),
    so "green locally" is the only signal there has been since 2026-10-26.
- **Method.** A third cut, different from both earlier audits:
  - **Seven lenses on round 3**, which had only ever been reviewed by its own
    implementers: the GitHub connector, the GitHub change pipeline, the
    GitHub review UI, what the repo ships into the agent (approval-gate,
    plugins, skills), the model advisor, the onboarding and advisor UI, and
    the motion runtime.
  - **Five lenses re-attacking earlier fixes** rather than re-reading their
    tests: the chat and approval pipeline, model lifecycle and the server
    lockdown, Needs-you and Watchtower, trust and data stores, the HTTP core.
  - **Six lenses on the gaps the last audit named**: two over the modules no
    audit had read in full, install/update/release, egress and MCP, the
    native Swift shell, and test quality (63 single-line mutants).
  - **Two cross-cutting lenses**: concurrency and long-run behaviour, and
    CLAUDE.md claims against the code (two halves).
  - **One status checker** over everything earlier audits left open.
  - Five lenses ran on Claude Fable 5 (the two GitHub server lenses, agent
    boundary, chat core, model lifecycle), fifteen on Opus, three on Sonnet.
  - About 155 raw findings, 138 after merging duplicates.
- **Verification.**
  - Every P1 candidate (seven raised by lenses, one escalated by the
    orchestrator) went to a Fable verifier that had to reproduce it
    independently and then try to refute it. None was refuted. Result: E01
    confirmed at P1; E02 confirmed as a P1 class; E03 and E04 confirmed in
    mechanism and rated P2 by the verifier; E10 confirmed and rated P2; E11
    confirmed and rated P1; E16 and E29 confirmed in part and downgraded to
    P2.
  - Twenty-six P2 claims went to Opus verifiers in ten batches. None was
    refuted outright. Several were corrected in scope, and five were rated P3
    by their verifier. Four of those are filed as P3 (E69, E73, E74, E112);
    the fifth, E09, is kept at P2 because three lenses each reported part of
    it.
  - The three test-gap P2s were not sent to a verifier: a surviving mutant is
    its own evidence. E30 was checked by hand against `ci.yml`.
  - P3s got a Sonnet sanity pass against the cited code.
- **Read-only, with these lapses** (none changed the repo or the live
  dashboard's state):
  - One lens ran `hermes --version` once.
  - One lens launched the full "Google Chrome for Testing" app twice, which
    raised a macOS Keychain prompt on the owner's screen. Later browser work
    used the headless shell only.
  - One lens left its hermetic test server running on port 17788; the
    orchestrator stopped it.
  - Loading repo modules refreshed gitignored `__pycache__` files.
  - `git status --short` was empty after every lens and at the end.
- **Not exercised:** a live model turn, a real approval, real GitHub, real
  OAuth, a real update, the WKWebView app, WebKit of any kind, and an app or
  helper rebuild. Findings that depend on one of these say so.

## What the findings have in common

1. **An approval is only as good as what the card shows, and three surfaces
   show less than what runs.**
   - The GitHub review sheet lets content through unseen in three ways (E02).
   - The approval-gate card cuts a patch at 600 characters and never shows
     `replace_all` (E14).
   - The main window's approval card flattens a multi-line command into one
     paragraph, so a comment line appears to swallow the code after it (E15).
   - The queue behind the cards held up: 80 randomised turns against a fake
     serve ran nothing without an accepted Approve on its own card.
2. **Gates that judge one spelling while the thing that runs sees another.**
   - The Trust policy is given only the first dangerous pattern in a command
     (E03).
   - The approval-gate classifies a path string; macOS resolves four alias
     spellings of the same file (E10), and Hermes parses a patch header the
     gate split differently (E11, E12).
   - The secret scan reads `.env` differently from how Hermes loads it (E06).
   - Reserved names and model ids are matched by exact letter case on a
     case-insensitive disk (two P3s).
3. **An explicit owner choice undone by recovery or background code.**
   - Undo deletes a file the agent only edited (E01).
   - A drill's restore un-pauses the agent (E19); a resume is booted out a
     minute later (E21).
   - The setup sheet writes defaults over real settings (E23); the config
     editors flip `compression.enabled` back on (E17); `obsidian_daily.py`
     replaces the owner's notes (E32).
4. **Round 3 is in better shape than the code around it.**
   - The motion runtime and the model advisor produced no P1 or P2.
   - In the GitHub feature, token custody, the git hardening, the
     `hermes/*`-only push rule, the owner-route gating and the scope wall's
     refusal paths all held under attack. Its P1 and P2s are in what the owner
     is shown and in ordering (a check before a lock, a snapshot before a size
     check).
   - The model-server lockdown held across all 49 upstream routes.
   - Most P1s and P2s are in code older than round 3 (recorder, Trust policy,
     Needs-you, dictation, the updater) or at seams between modules.
5. **The tests mostly have teeth, with gaps on two gates.** 45 of 63 mutants
   were killed, including every one on the scope wall, the canary scan, the
   approval-gate's fail-closed paths and the same-origin guard. The two gaps
   that matter are in E31.
6. **The documentation is largely accurate.** About 700 checkable claims in
   CLAUDE.md, README, RUNBOOK and SECURITY were verified; 16 are false
   (E139–E154), none of them a safety claim an agent would break a rule by
   trusting. Separately, six code findings make a CLAUDE.md sentence false
   (E05, E10, E28, E63, E77, E83).

## Measured (live, read-only)

- **Dashboard after 12 days:** about 58 MB resident, 28 threads, 50 file
  descriptors, one listening socket. No traceback in the logs comes from the
  running code. A 24,000-request run against a hermetic copy showed no
  growth.
- **Locks and threads:** the lock-order graph over every module has no
  cycle; all 19 background loops are wrapped in try/except; no lock is held
  during slow work on a path a request waits on.
- **Costs:** a cold hardware detection for the advisor takes 0.26 s and is
  then free. `needsyou.json` is rewritten every 60 s with identical content.
- **Live state while auditing:**
  - Trust policy empty (no class on Auto).
  - Full Disk Access denied; `messages.json` has no conversations.
  - Flight Recorder has no write rows.
  - No system HTTP proxy configured.
  - The dictation helper is not installed.
  - The Telegram gateway's working directory is `~/.hermes`.
  - The background model lane has never been started (`runs = 0`).
  - The installed `you-model-onboarding` skill is older than the repo's.

## Findings — P1 and P2

| ID | Pri | Finding | Location | Verification |
|---|---|---|---|---|
| E01 | P1 | Undo on an agent edit to a file the checkpoint store skips (`.env`, logs, >10 MB, `build/`, anything gitignored) moves the owner's file to trash, and trash cleanup deletes it after 14 days | `dashboard/aux_recorder.py:1517` | confirmed |
| E02 | P1 | The GitHub review sheet lets content be approved unseen: a line pushed past the edge of a scrollbar-less diff, a text file made "binary" by one NUL byte, and everything after line 3,000 | `dashboard/aux_github.js:569` | confirmed (class) |
| E03 | P1 | A Trust class set to Auto approves a whole compound command, because only the first dangerous pattern reaches the policy | `dashboard/permissions.py:506` | confirmed; verifier rated P2 |
| E04 | P1 | Needs-you files a text from someone you reply to as "never" when it contains words like "newsletter" or "out for delivery", and no screen lists those rows | `dashboard/aux_needsyou.py:790` | confirmed; verifier rated P2 |
| E05 | P2 | GitHub push budget, Pause and Trust tier are checked before a 90 s lock wait and never again, so queued approvals ignore all three | `dashboard/aux_ghchanges.py:2064` | confirmed |
| E06 | P2 | A `.env` line with a trailing comment turns the un-overridable secret block off for that secret | `dashboard/aux_ghchanges.py:937` | confirmed |
| E07 | P2 | Renaming a connected repository on GitHub pauses all GitHub writes under a false reason and leaves a state no button repairs | `dashboard/gh_api.py:1305` | confirmed (301 assumed) |
| E08 | P2 | The scope wall silently stores an "All repositories" token as `unchecked` when it has nothing to sample, and the card still says the key can only touch the repositories you picked | `dashboard/gh_api.py:813` | partly |
| E09 | P2 | GitHub mirrors keep every object ever snapshotted, including from refused requests, and are left on disk after Disconnect | `dashboard/gh_git.py:676` | partly |
| E10 | P2 | Four macOS alias spellings of a path pass the approval-gate's write and read checks with no card | `hermes-plugins/approval-gate/__init__.py:927` | confirmed |
| E11 | P2 | A carriage return in a patch header hides the target file from the approval-gate | `hermes-plugins/approval-gate/__init__.py:997` | confirmed; verifier rated P1 |
| E12 | P2 | After a `cd`, a patch with a relative header writes `~/.ssh/authorized_keys` with no card | `hermes-plugins/approval-gate/__init__.py:1099` | confirmed |
| E13 | P2 | On Telegram the approval-gate judges relative paths against `~/.hermes`, so a default file search is hard-blocked and ordinary writes raise a false card | `hermes-plugins/approval-gate/__init__.py:936` | confirmed |
| E14 | P2 | The approval-gate card cuts content at 600 characters, never shows `replace_all`, and passes bidi characters through raw | `hermes-plugins/approval-gate/__init__.py:240` | confirmed |
| E15 | P2 | The main window's approval card renders a multi-line command as one paragraph | `dashboard/index.html:1135` | confirmed |
| E16 | P2 | The `config.yaml` editors write unparsable YAML when a list is not indented under its key, and report success | `hermes-plugins/plugin_enable.py:210` | partly (was P1) |
| E17 | P2 | A comment on a section header line makes the `config.yaml` editors append a duplicate block that silently overrides the original | `dashboard/aux_context.py:479` | confirmed |
| E18 | P2 | A dashboard restart during a turn drops that reply from the conversation for good, while the agent still remembers it | `dashboard/server.py:1065` | partly |
| E19 | P2 | A model drill's restore step undoes a Pause or a model switch the owner made while it ran | `dashboard/aux_promotion.py:513` | confirmed |
| E20 | P2 | With a macOS web proxy configured, the dashboard sends its local-model calls (prompts and the model key) to the proxy | `dashboard/server.py:1510` | confirmed |
| E21 | P2 | Resume, or a model switch while asleep, loads the model and idle-suspend boots it out again within a minute | `dashboard/server.py:4291` | confirmed |
| E22 | P2 | A failed model download in the setup sheet shows "queued" forever, and the next wake pulls the weights past the disk check | `dashboard/aux_onboarding.js:1919` | confirmed |
| E23 | P2 | If the setup sheet's state read fails, Continue writes hard defaults over the owner's settings | `dashboard/aux_onboarding.js:2078` | confirmed (narrow) |
| E24 | P2 | The memory-fact secret check misses common key formats and plain "my password is …" sentences | `dashboard/aux_memlayer.py:424` | confirmed |
| E25 | P2 | The "what leaves this Mac" table says its 16 rows are everything, but has no row for the agent's own terminal, browser and web-extract traffic | `dashboard/aux_onboarding.js:350` | partly |
| E26 | P2 | The app copies the whole iMessage database to a predictable temp file every 60 s | `app/main.swift:778` | confirmed (latent) |
| E27 | P2 | Dictation cleanup reverses a spoken correction that takes back a negation | `dashboard/aux_dictation.py:420` | confirmed (latent) |
| E28 | P2 | Holding Space or Enter on the Claude-escalation switch or the Watchtower masters flips it on every key repeat | `dashboard/aux_settings_shell.js:749` | partly |
| E29 | P2 | A self-update finishes on the old `update.sh`, so the first hop from 1.3.2 never links approval-gate | `update.sh:319` | confirmed (was P1) |
| E30 | P2 | CI's unit job cannot pass: two round-3 suites print skips that `ci.yml` does not expect, which also blocks releases | `.github/workflows/ci.yml:141` | checked by hand |
| E31 | P2 | Two gate checks have no test: the worker's "answer only the card at the head" check and the decide-time push budget | `dashboard/hermes_rpc.py:757` | mutation survivors |
| E32 | P2 | `obsidian_daily.py` replaces the owner's own daily-note text when the end marker line is missing | `obsidian_daily.py:75` | confirmed |
| E33 | P2 | An open Flight Recorder detail pane is torn down and refetched every 3 s, with a subprocess per refetch | `dashboard/aux_recorder.js:653` | confirmed |

### P1 detail

**E01 — Undo destroys a file the agent only edited**
Location: `dashboard/aux_recorder.py:1517-1545` · also `:1532-1533` (classification by git's error text), `dashboard/aux_recorder.js:1179-1190` (confirm text), upstream `tools/checkpoint_manager.py:81-143` (exclusions) · lens: trust-stores · confirmed by an independent reproduction

- **What:** The recorder keeps no "before" state of its own; it relies on Hermes' checkpoint store. That store skips `.env`, `.env.*`, `*.log`, files over 10 MB, `build/`, `dist/`, `venv/` and anything in the project's `.gitignore`. When Undo asks git to restore such a file, git answers "pathspec did not match", and the handler reads that as "the agent created this file" and moves it to undo-trash. A `patch`, which by definition edits an existing file, is classed the same way.
- **Failure:** The agent edits your `.env` or a gitignored config; you click Undo expecting the previous version back. The confirm dialog promises a restore. Your file is moved to trash instead. Undoing the undo fails with a git error, and trash cleanup deletes the only copy at 14 days. Reproduced with the unmodified recorder and the real checkpoint manager for `.env`, `debug.log`, `build/out.js`, an 11 MB CSV and a gitignored file; a control file restored correctly.
- **Preconditions:** a write through serve or the CLI (Telegram writes get no Undo), and an Undo click. The live recorder has no write rows, so it has not fired here.
- **Fix:** Refuse Undo unless creation is proven (`git check-ignore` plus the size rule tell you a snapshot could never have existed) and mark such rows "cannot be undone". Longer term, record a before-hash at write time.

**E02 — The GitHub review sheet lets content be approved unseen**
Location: `dashboard/aux_github.js:565-571` (diff CSS), `dashboard/index.html:220-222` (all scrollbars hidden), `dashboard/aux_ghchanges.py:1455-1459, 1569` (binary flag; hidden-character scan skipped), `:1467-1666` (no long-line check) · lens: gh-ui, gh-changes · three shapes, each reproduced twice

- **What:** The sheet is the approval gate for anything Hermes pushes to GitHub. Three shapes get content through it without the owner seeing it:
  1. **Off-screen line.** Diff lines do not wrap and the app hides every scrollbar. A line padded with about 260 spaces shows as `+import sys`; the payload starts at x≈2010 px against a box edge of 1077 px. No check fires.
  2. **One NUL byte.** A NUL in the first 8,000 bytes makes git call a shell script binary. The diff is "Binary files … differ", the only check is informational, no tick is asked, and the hidden-character scan is skipped. The pushed file was byte-identical, hidden `curl` line included. The sheet does say "Binary file — not shown", but its headers still read "nothing blocks this change" and "the whole change".
  3. **Past line 3,000.** The rest sits behind a "Show the remaining" button at the very end; Approve is already enabled.
- **Failure:** A prompt-injected agent gets code or private text approved that the owner never read. The known-secret canaries and PEM checks still block listed secrets, so the exposure is arbitrary code and arbitrary private text, including to a public repository behind only the generic public-repo confirm.
- **Bounds:** the push goes only to `hermes/<slug>` on a connected repository as a draft PR, with a Telegram notice; nothing merges by itself.
- **Design note:** §3.4 of the design specifies the binary check as informational, so the code matches the design for shape 2. The design gives no safety reason and contradicts its own statements elsewhere that the owner sees everything pushed.
- **Not verified:** WebKit rendering (Chromium with the app's CSS only), and how github.com renders a NUL-carrying file.
- **Fix:** Wrap or flag long lines and warn on long whitespace runs; make a binary or NUL-carrying file a warn-level check that needs a tick, with a `--text` preview for files that are mostly text; keep Approve disabled until the remaining lines have been expanded.

**E03 — A Trust class on Auto approves a whole compound command**
Location: `dashboard/permissions.py:506-564` · also `dashboard/hermes_rpc.py:1170-1179`, upstream `tools/approval.py:1336-1348, 2436-2437` · lens: trust-stores · confirmed end to end; verifier rated P2

- **What:** `decide()` is written to take the most restrictive verdict across a list of pattern keys. Upstream Hermes reports only the first dangerous pattern it matches in a command and sends that one key. Nothing re-scans the command.
- **Failure:** With process-control and file-perms on Auto, `pkill -9 node; cp mykey.pub ~/.ssh/authorized_keys`, `…; sed -i … ~/.hermes/config.yaml` and `chmod 777 f && dd … of=/dev/disk4` each come back auto-approved. The safety floor on the second half is never consulted. The approval-gate plugin does not cover these terminal commands.
- **What still holds:** `rm -r` and `sudo` parts still ask, `/dev/sda` is hard-blocked, and Tirith catches an append to `~/.zshrc`.
- **Preconditions:** a floor-less class set to Auto. The shipped default is Ask for everything, and the live policy is empty.
- **Why P1 and not the verifier's P2:** the six floor-less classes are the ones the feature's own spec lists as intended for Auto. Using the Trust panel as designed turns one harmless-looking prefix into a bypass of the approval gate for nearly everything else. `permissions.py:24-26` describes floors that do not hold for compound commands.
- **Fix:** Downgrade Auto to Ask whenever the command contains a shell operator (upstream already has such a regex for its allowlist); better, scan the command for every pattern and resolve over all of them.

**E04 — Needs-you hides texts from people you reply to**
Location: `dashboard/aux_needsyou.py:790-792` · also `:212-218` (the word list), `:665-670`, `:1286-1288` (email collector, same order), `:1828` (payload carries only a count) · lens: attention-pipeline · confirmed; verifier rated P2

- **What:** The "automated sender" test runs over the message body before reply history is checked. A body containing "notification", "newsletter", "out for delivery", "verification code", "order shipped", "unsubscribe" or "verify your" is classed `never` at confidence 0.85, above the 0.6 guard that exists to stop silent misses. `never` rows travel only as a count; the widget, the pop-out and the model pass never see them, and the Never button only files rows in.
- **Failure:** "Did you see the school newsletter? Can you sign the slip?" from someone you replied to yesterday never appears in Needs-you and cannot be found or re-filed there. With the real module, 13 of 22 realistic texts from replied-to senders went to `never`.
- **Preconditions:** Full Disk Access for the Message Center. It is denied today, so nothing is affected now.
- **Why P1 and not the verifier's P2:** the message stays unread in Messages itself, which is the verifier's point. But the 2026-10-08 audit rated the same class (B04, Needs-you hiding a newer message) P1, and this contradicts the product's own rule in `docs/plans/purpose-and-direction.md:154`.
- **Origin:** release 1.1.2, not a later fix. No test covers it.
- **Fix:** Keep short-code and sender-handle patterns first, decide known-sender next, and apply body patterns only to senders with no history. Same order in the email collector. List `never` rows in the pop-out.

### P2 detail

**E05 — GitHub push budget, Pause and Trust are checked before the lock, not under it**
Location: `dashboard/aux_ghchanges.py:2064-2083` · lens: gh-changes · confirmed
- **What:** The decide path checks Pause and holds, the Trust tier and the daily budget once, then waits up to 90 s for the network lock. Under the lock it re-checks only the digest and remote heads.
- **Failure:** With the real rate limits, 14 queued approvals all pushed against a limit of 10. An approval already waiting still pushed after Pause had answered OK. Realistically one burst gets roughly 18 to 45 pushes through. CLAUDE.md lists "the push budget" among the bounds that hold against a same-user process forging approvals; against that adversary it does not.
- **Fix:** Repeat all three checks after the lock is taken. Add the missing test (E31).

**E06 — A trailing comment in `.env` defeats the known-secret block**
Location: `dashboard/aux_ghchanges.py:937-957` · lens: gh-changes · confirmed
- **What:** The scanner's parser keeps everything after `=`. For `KEY=secret # note` the value it searches for includes the comment. Hermes loads the same line through python-dotenv, which gives just `secret`.
- **Failure:** A file carrying the bare secret gets only an ackable warning, or nothing if the secret has no vendor shape. Multi-line and escaped-quote values miss too.
- **Preconditions:** an inline comment added by hand. The live `.env` has none.
- **Fix:** Parse values the way dotenv does and keep both forms as canaries.

**E07 — A renamed repository wedges the connector**
Location: `dashboard/gh_api.py:391-393, 618-623, 1305-1310`, `dashboard/aux_github.py:1684, 1703-1708` · lens: gh-connector · confirmed against the fake; GitHub's 301 is assumed from its docs
- **What:** The client never follows redirects. A renamed repository answers 301, which is neither "gone" nor success. The next check raises a false "reaches a repository you did not list" hold that pauses all GitHub writes.
- **Failure:** Unpause is refused; remove then add of the new name answers 503; no route ever deletes a repository row, so disconnect and reconnect leave it broken. The only exits are renaming back on GitHub or hand-editing `state.json`.
- **Fix:** Treat a 3xx on the repository read as "moved", and let remove actually drop the row.

**E08 — The scope wall passes an unchecked token silently**
Location: `dashboard/gh_api.py:790-840` (esp. `:813-816, :840`), `dashboard/aux_github.py:1127-1130, 1153` · lens: gh-connector · partly
- **What holds:** An "All repositories" token is stored with `write_reach: unchecked` and no warning when there is nothing to sample. That contradicts the code's own comment ("an unchecked wall is not a pass") and the card headline. The card never shows `write_reach`.
- **What does not:** A selected-repositories token with one extra public repository is within the documented, sample-based design; only its `contained` label overclaims.
- **Consequence:** The agent cannot push anywhere else through Hermes. The stored key is wider than the UI says, which matters if `credential.json` leaks.
- **Fix:** Show `unchecked` as a warning on the card and qualify the headline.

**E09 — GitHub mirrors grow without bound and outlive Disconnect**
Location: `dashboard/gh_git.py:149, 676-680`, `dashboard/aux_ghchanges.py:1766-1784` · lens: concurrency-longrun, gh-changes, gh-connector · partly; verifier rated P3 bordering P2
- **What:** A request snapshots the sandbox into the mirror before the size checks run. Garbage collection is off by design and nothing prunes. Reject, expiry, withdraw, repository removal and Disconnect all leave the objects. The design's "Delete mirrors and sandboxes" offer does not exist.
- **Measured:** an unchanged 20 MB file is stored once; a file that changes each time adds a full copy per refused request (16 KB → 82 MB in three). Worst case is tens of GB an hour with no free-disk stop.
- **Fix:** Refuse over-limit content before writing objects; prune after reject, withdraw and expiry; delete mirrors on Disconnect or say they remain.

**E10 — macOS alias paths bypass the approval-gate**
Location: `hermes-plugins/approval-gate/__init__.py:391-395, 927-947, 961-981, 558-564` · lens: agent-boundary · confirmed end to end; verifier rated P2
- **What:** The gate normalises with `realpath`, which leaves `/System/Volumes/Data/Users/…`, `/.nofollow/…`, `/.resolve/N/…` and `/.vol/<dev>/<inode>/<name>` unchanged. All four resolve to the same file on Darwin 25.5.
- **Failure:** With the real plugin, the real Hermes dispatcher and a live approval surface, the canonical path raised a card and was not written; every alias raised no card and was written. Reachable: shell rc files, LaunchAgents, `permissions.json`, `config.yaml`, `~/.claude`, the install directory, and reads of the GitHub credential folder. Hermes' own hard denials (`~/.ssh/authorized_keys`, `~/.hermes/.env`) use `realpath` too and are passed the same way.
- **Why P2:** it gives the agent no capability the terminal lacks (a plain `cp` into LaunchAgents already raises no card, which is the documented limit of a path denylist). It re-opens C116 and makes CLAUDE.md's "in every path form" false.
- **Fix:** Canonicalise through `F_GETPATH` on the deepest existing ancestor, strip `/System/Volumes/Data`, and refuse `/.vol`, `/.nofollow`, `/.resolve` and `/.file` outright.

**E11 — A carriage return in a patch header hides the target**
Location: `hermes-plugins/approval-gate/__init__.py:997-1005`, upstream `tools/patch_parser.py:81, 111-114` · lens: agent-boundary · confirmed end to end; verifier rated P1
- **What:** The gate splits a patch on `\r` as well as `\n`. Hermes' parser splits on `\n` only and then strips the header. So `*** Add File: ` + `\r` + path looks like an empty header to the gate and like a normal one to Hermes.
- **Failure:** With the real dispatcher and plugin, one `patch` call created `~/.bashrc`, `~/.bash_login` and `~/.profile` (scratch home) with no card. The write lands on the real file, not on a name containing a carriage return. `*** Add\rFile:` and `*** Move File: \r…` work the same way. CRLF and a trailing CR are handled correctly. No precondition.
- **Why P2 and not the verifier's P1:** same reasoning as E10. The terminal already has unasked routes to persistence that CLAUDE.md documents as outside the gate, so this adds a one-call route rather than a new capability. It is the easiest of the three to trigger and should be fixed with E10.
- **Not done:** the wider differential fuzz of header parsing; only CR variants and the relative-path split were verified.
- **Fix:** Have the gate call upstream's own patch parser, and answer Ask for any header containing a control character or one it cannot classify.

**E12 — A relative patch header after `cd` writes a protected file with no card**
Location: `hermes-plugins/approval-gate/__init__.py:1099-1124, 1335-1338`, upstream `tools/file_operations.py:1334-1338`, `agent/file_safety.py:101` · lens: agent-boundary · confirmed end to end
- **What:** The gate classifies the target correctly (`ssh`), then drops it because its helper says "Hermes will refuse this anyway". Hermes resolves the relative path for the write against the terminal's live directory, but runs its own deny check against the raw relative string.
- **Failure:** After `cd ~/.ssh`, a patch headed `*** Add File: authorized_keys` created the file with no card. `write_file` is not affected; only the patch format diverges.
- **Fix:** Never drop a classified patch target on the strength of Hermes' own refusal.

**E13 — On Telegram the gate judges relative paths against `~/.hermes`**
Location: `hermes-plugins/approval-gate/__init__.py:936-940, 558-560` · lens: agent-boundary · confirmed
- **What:** The gate always adds a form relative to the process's working directory. The Telegram gateway runs in `~/.hermes` by upstream design, while Hermes' file tools resolve relative paths against the home folder.
- **Failure:** A file search with no root is hard-blocked as "the GitHub key folder". A relative `write_file` or `patch` raises a false "Hermes config" card, and is blocked outright for cron jobs and subagents, which have no approval surface. It fails safe; it does not let anything through.
- **Fix:** When Hermes' own resolver answers, judge only that form and its real path.

**E14 — The approval-gate card can leave out the part that matters**
Location: `hermes-plugins/approval-gate/__init__.py:240, 255-261, 1146-1164` · lens: agent-boundary · confirmed
- **What:** Card text is cut at 600 characters. A patch whose `curl … | sh` sits at character 1,390 shows a card without it. The cut is signalled ("+823 more chars"), but no surface can show the rest. `replace_all` is never shown, so "replace every newline" reads as one edit. Bidi characters reach the card raw, on the dashboard and on Telegram.
- **Fix:** Show head and tail, or refuse to offer Approve for content too long to review; show `replace_all`; render hidden characters as `⟦U+XXXX⟧` as the GitHub sheet does.

**E15 — The main window's approval card flattens multi-line commands**
Location: `dashboard/index.html:1135-1136, 4261-4263`; compare `dashboard/aux_quickask.js:398-399` · lens: chat-core · confirmed in headless Chromium
- **What:** `.approval code` has no `white-space` rule. A five-line script renders as one wrapped paragraph: `# tidy the build folder shutil.rmtree(…)` reads as a single comment and indentation is lost. Every `execute_code` approval is multi-line. Quick Ask renders the same card correctly. WebKit's default for `<code>` is the same, so the app matches.
- **Fix:** `white-space: pre-wrap` with a bounded height on `.approval code`. One line of CSS; do this first.

**E16 — The `config.yaml` editors write unparsable YAML for non-indented lists**
Location: `hermes-plugins/plugin_enable.py:210-216, 274-285`, `dashboard/aux_promptbudget.py:442-571` · lens: unread-b, install-update-release, agent-boundary · partly; downgraded from P1
- **What holds:** For a list written at its key's own indent, `plugin_enable.py` and the prompt-budget editor produce output PyYAML rejects. Neither re-parses its result, and `plugin_enable.py` exits 0, so install and update log success.
- **What does not:** Hermes 0.18 writes indented lists and normalises on `hermes config set`, so the trigger is a hand edit or another tool. Hermes warns and falls back to defaults, after which every turn fails with "No inference provider configured", so nothing runs ungated. It does not repeat on each update, and doctor does warn.
- **Fix:** One change with E17: recognise items at the key's indent and re-parse before replacing the file.

**E17 — A comment on a section header makes the editors append a duplicate block**
Location: `dashboard/aux_context.py:479-487`, `dashboard/aux_promptbudget.py:461-465, 528-533`, `hermes-plugins/plugin_enable.py:164-168` · lens: unread-b · confirmed
- **What:** All three editors look for the header as `^key:\s*$`. `compression:  # note`, a quoted key or flow style is not found, so a second top-level block is appended and YAML keeps the last one.
- **Failure:** A hand-set `compression.enabled: false` is lost while the card shows it off. The Tool budget Install button drops approval-gate from `plugins.enabled`; `update.sh` keeps approval-gate but drops loop-breaker. B28 fixed comments inside the block only.
- **Fix:** Accept a trailing comment on the header, refuse other header forms, verify before replace.

**E18 — A dashboard restart mid-turn loses the reply**
Location: `dashboard/server.py:1065, 6399-6401`, `dashboard/index.html:4230`, `dashboard/aux_update.py:673` · lens: chat-core · partly
- **What holds:** Jobs live only in memory. After a dashboard-only restart, serve finishes the turn; the next turn resumes that session, so the agent remembers an answer and any tool actions the owner never saw. On reload the transcript ends at the user message with no notice.
- **What does not:** An Update Apply also stops serve, so that turn is killed rather than completed elsewhere. Apply still never checks for a turn in flight.
- **Fix:** Refuse or confirm Apply while a job is active; on loading a transcript that ends in a user turn, store a notice and offer to fetch the missing reply.

**E19 — A drill's restore undoes the owner's Pause or switch**
Location: `dashboard/aux_promotion.py:513-524`, `dashboard/server.py:5377-5382, 4291-4300` · lens: unread-a, model-lifecycle · confirmed
- **What:** The drill's `finally` always calls `switch_model(orig)`. On a paused agent that resumes it, deletes the pause marker and reloads the model. A switch the owner made mid-drill is reverted. Nothing blocks Pause or switch while a drill runs, and the UI never shows the drill's error or restore result.
- **Fix:** Skip the restore when the agent is paused or the active model is no longer the drilled one; surface the outcome.

**E20 — A system web proxy captures the dashboard's local-model calls**
Location: `dashboard/server.py:1510` · also `dashboard/doctor.py` · lens: model-lifecycle · confirmed
- **What:** The opener keeps urllib's default proxy handling. Python on macOS reads the system proxy setting and has no built-in loopback exemption; the default bypass list does not include `127.0.0.1`.
- **Failure:** With a manual Web Proxy (HTTP) set, the verifier's scratch proxy received the liveness probes and a chat POST carrying the bearer key and the full prompt, and `model_online()` read False. Exposed: clipboard transforms, the drill, evals, For-You, the Needs-you model pass, Watchtower, dictation cleanup and doctor. Not exposed: the main chat path, the MCP server and the GitHub client.
- **Preconditions:** a manual HTTP proxy in System Settings. None is set here. PAC files are not read.
- **Fix:** Build the opener, and doctor's, with `ProxyHandler({})`, as the MCP server already does.

**E21 — Resume is undone by idle-suspend**
Location: `dashboard/server.py:4291-4309, 5360-5403` · lens: model-lifecycle · confirmed
- **What:** Neither Resume nor a model switch while asleep counts as user activity. Once the model answers, idle-suspend measures from the last chat, with no grace period after a start.
- **Failure:** A resume after a three-hour pause was booted out 60 s later in the verifier's fake-clock run. "Wake now" is unaffected.
- **Fix:** Call `note_user_activity()` in both routes.

**E22 — A failed download shows "queued" forever in the setup sheet**
Location: `dashboard/aux_onboarding.js:1919-1940, 1036-1041, 1997-2001` · lens: onboarding-advisor-ui · confirmed
- **What:** The sheet reads only `downloaded` and `downloading`; it ignores `download_error`. The 2.5 s poll never stops and the Ready step says "Downloading".
- **Second effect (static):** apply has already made the model active. Wake never checks the weights are on disk, and `mlx-server.sh` keeps the online path for incomplete weights, so the next wake downloads inside the model server, past the disk check.
- **Fix:** Show the error and stop the poll; do not activate a model whose download has not finished.

**E23 — Continue writes defaults over real settings when the state read fails**
Location: `dashboard/aux_onboarding.js:2078-2097, 2119-2122, 1829-1862` · lens: onboarding-advisor-ui · confirmed, narrower than reported
- **What:** On a 500 `{ok:false}` no error appears and the hard-coded defaults are posted, turning News and briefings back on and resetting idle and prewarm. On a network error an error line does appear. It happens only on the first open in a page life; the realistic trigger is opening the sheet during a dashboard restart.
- **Fix:** Do not enable Continue until a real state has loaded.

**E24 — The memory-fact secret check misses common formats**
Location: `dashboard/aux_memlayer.py:424-490` · lens: trust-stores · confirmed
- **What:** Missed: `sk_live_`, `hf_`, `npm_`, `glpat-`, `ya29.`, `1//`, `GOCSPX-` and passwords inside URLs. The fact check also skips the prose rule the redactor has, so "my password is Summer2026!" is redacted in exports and MCP output but stored as a fact, replayed into prompts and not withheld from For-You. Short PINs and an AWS secret labelled only "key:" are not reasonably catchable.
- **Fix:** Add the fixed prefixes and the URL-userinfo rule to the shared block, and run the prose rule in the fact check.

**E25 — The network table omits the agent's own egress**
Location: `dashboard/aux_onboarding.js:285-289, 317-323, 350-357` · lens: egress-mcp-bridge · partly
- **What holds:** The card says its 16 destinations are "everything Hermes sends to the internet" and "Everything else runs on this Mac". No row covers the agent's terminal (`curl`), browser tools or web extract. Hermes' approval rules stop `curl | sh`-style piping, not a plain `curl`.
- **What does not:** The Claude switch is not shown broken; its row's wording over-promises.
- **Fix:** Add a row for agent tool traffic with its real control (the approval gate and toolsets), and reword the headline.

**E26 — The whole iMessage database is copied to a temp file**
Location: `app/main.swift:778-788` · lens: native-shell · confirmed; latent
- **What:** Every 60 s `MessagesSync` backs up all of `chat.db` to `$TMPDIR/hermes-msgsnap-<pid>.db` with a plain `sqlite3_open`. The copy is 0644 inside the user's temp folder, exists for about a second per tick, and survives a crash mid-tick. A planted symlink at that predictable path redirects the full copy to a location and mode of the planter's choosing; reproduced in C with the same calls.
- **New versus C89:** C89 covered the disk cost only. This is the privacy side: the whole history, readable by any process running as the owner without Full Disk Access, against the 14 previews the dashboard keeps.
- **Latent:** Full Disk Access is denied.
- **Fix:** Query the live database read-only. First item for the next `main.swift` rebuild.

**E27 — Dictation cleanup reverses a corrected negation**
Location: `dashboard/aux_dictation.py:420-432, 458-492` · lens: native-shell · confirmed; latent
- **What:** "Don't send the email, no wait, send the email" becomes "Don't send the email". Of the verifier's 20 realistic corrections, 11 came out with the opposite meaning; every case with a negation, a marker and a repeated verb was wrong. The result is pasted into the focused field with no review step.
- **Latent:** the dictation helper is not installed here.
- **Fix:** Leave the words as spoken when the kept prefix holds a negation the correction drops. Add negation cases to the B16/C124 suite.

**E28 — A held key flips two privacy switches repeatedly**
Location: `dashboard/aux_settings_shell.js:749-753, 708-719`, `dashboard/aux_watchtower.js:681-686` · lens: frontend-core · partly
- **What:** Neither handler ignores `KeyboardEvent.repeat`, and the escalation flip has no in-flight guard. One held key sent 12 POSTs; the end state depends on how many repeats land. The switch always showed the true server state. CLAUDE.md's keyboard rule is false for these controls.
- **Fix:** Ignore repeats; disable the control while a flip is in flight.

**E29 — A self-update finishes on the old script**
Location: `update.sh:319, 470`, `dashboard/aux_update.py:742-766` · lens: install-update-release · mechanism confirmed; downgraded from P1
- **What:** `git checkout` and `rsync` give `update.sh` a new inode, and bash finishes the old text. Running the published 1.3.2 script in a scratch repo: after the hop only tool-budget is linked, the gateway is not restarted, and a second plain update still links nothing. Only `--target` or `install.sh` enables approval-gate. `CHANGELOG.md:255` and the deploy checklist are false for this hop.
- **Why P2:** 1.3.2 had no gate, so nothing regresses. The `docs/state-snapshot.json` deletion on tarball installs is the old script's own behaviour and the known C64 tail.
- **Fix:** Move post-swap steps into a file invoked by path, or re-exec. The old script does run the new `install-services.sh` after the swap, so calling the post-update step from there reaches existing users on the first hop.

**E30 — CI's unit job cannot pass**
Location: `.github/workflows/ci.yml:141-148`, `tools/tests/unit/t_r3_roster_launcher.py:489, 561`, `tools/tests/unit/t_r3_roster_catalog.py:216` · lens: tests-quality · reproduced by the lens with CI's env; skip lines and list checked by hand
- **What:** Two round-3 suites print SKIP lines on a machine without the mlx-vlm venv. They are not in the expected-skips list, and CI fails on any unexpected skip. `release.yml` requires CI, so releases are blocked. Locally the tier is green only because this Mac has the venv.
- **Fix:** Add the three skip lines to the list. Give `run.sh` a per-suite timeout and CI a job timeout while there.

**E31 — Two gate checks have no test**
Location: `dashboard/hermes_rpc.py:757-758`, `dashboard/aux_ghchanges.py:2070-2073` · lens: tests-quality · mutation survivors
- **What:** Dropping the worker's "answer only the card at the head of the queue" check survives all eleven approval suites; they reach the stale case only through the route's 409. An off-by-one in the decide-time push budget survives all eight GitHub suites; only the request-time copy is tested.
- **Fix:** One test each, driving the real function.

**E32 — `obsidian_daily.py` can replace the owner's notes**
Location: `obsidian_daily.py:75-81` · lens: unread-b · confirmed in a scratch vault
- **What:** With the end marker deleted, the next run appends a second block; the run after replaces everything from the orphan start marker to the new block's end, the owner's own text included. No backup. The launch agent is installed on this Mac.
- **Preconditions:** the end marker line removed by hand.
- **Fix:** Never let a match span a second start marker; with unbalanced markers, append only.

**E33 — The recorder detail pane rebuilds every 3 s**
Location: `dashboard/aux_recorder.js:653-659, 685-688`, `dashboard/aux_recorder.py:1256` · lens: frontend-core · confirmed with a real mouse event
- **What:** The poll skips the rebuild only when keyboard focus is inside the list, and a mouse click on a row leaves focus on `<body>`. Each poll fetches the row again, replaces the diff and resets its scroll; each fetch runs an uncached checkpoint-diff subprocess.
- **Fix:** Keep the open pane across polls keyed on the row id; cache the diff.

## P3 — low impact, hardening, polish

"confirmed" means the reviewer reproduced it and the sanity pass found the
cited code as described. "plausible" means static reasoning only, with the
cited code matching. No row was marked doubtful.

### GitHub connector, change pipeline and review sheet

| ID | Finding | Location | Verification |
|---|---|---|---|
| E50 | The key check probes five repository permissions only, so a key that also carries an account permission such as "Git SSH keys" passes, yet the card says the key can only touch the repositories you picked. | `dashboard/gh_api.py:507` | plausible |
| E51 | Two write holds misbehave: a background check that cannot test write reach clears the "key too wide" hold with nothing fixed, and a check run while the Mac's clock was wrong leaves the "expired" hold stuck until you reconnect. | `dashboard/aux_github.py:1736` | confirmed |
| E52 | A check that fails on a network blip is not retried for six hours, and a rate-limited check wipes a repository's workflow verdict so writes there stay refused until the next good check. | `dashboard/aux_github.py:1751` | confirmed |
| E53 | Removing a repository only switches it off and never frees its slot, so the 10-repository limit counts every repository ever connected. | `dashboard/aux_github.py:1182` | confirmed |
| E54 | When someone other than Hermes has many `hermes/*` branches, the drift check sends one Telegram message per branch, resends the extras above 200 on every check, and does so while holding the GitHub lock. | `dashboard/aux_github.py:1835` | confirmed |
| E55 | The "this repository is public" confirmation relies on a visibility flag refreshed only by the six-hourly check, so a repository made public meanwhile is approved without the extra confirmation. | `dashboard/aux_github.py:1767` | plausible |
| E56 | The hardened git settings are rewritten before each call, but a program running as you that keeps rewriting the mirror's config can win the race and steer the approved push or turn every review diff into "binary". | `dashboard/gh_git.py:492` | confirmed |
| E57 | If the dashboard restarts after the push record is written but before the push lands, the ledger names a commit GitHub never received: a false drift alert, a blocked retry and a wasted daily push. | `dashboard/aux_ghchanges.py:2186` | confirmed |
| E58 | Retracting the later of two changes on one branch deletes the branch and closes the pull request, but the earlier change stays shown as an open pull request that can no longer be retracted. | `dashboard/aux_ghchanges.py:2363` | confirmed |
| E59 | Pull request descriptions and commit messages lose all leading indentation, so code blocks and nested lists arrive flattened on GitHub. | `dashboard/untrusted.py:68` | confirmed |
| E60 | Two patterns treated as "part of a web address" can leave a live @mention, and only two comment-style hidden link definitions are stripped. | `dashboard/aux_ghchanges.py:157` | plausible |
| E61 | A bare carriage return inside a diff line is drawn as a second row that can look like a deletion, and a file path containing " b/" is shown under the wrong name. | `dashboard/aux_github.js:1647` | confirmed |
| E62 | After approval the sheet says it opened a draft pull request even when GitHub refused drafts and Hermes opened a ready-for-review one that notifies reviewers. | `dashboard/aux_github.js:1785` | confirmed |
| E63 | When the "GitHub change waiting" toast is replaced by another message, it keeps swallowing clicks on its spot for about seven more seconds while invisible. CLAUDE.md says otherwise. | `dashboard/aux_github.js:2342` | confirmed |
| E64 | A pull request description you edit past 8 KB has its end, your own addition, silently cut after approval, although the sheet says it is sent exactly as written. | `dashboard/aux_github.js:1755` | plausible |

### Agent boundary, chat and attention

| ID | Finding | Location | Verification |
|---|---|---|---|
| E49 | The installed `you-model-onboarding` skill is older than the repo's and still tells the owner nothing ever leaves the Mac; the deploy checklist's copy list missed it. | `~/.hermes/skills/autonomous-ai-agents/you-model-onboarding/SKILL.md:144` | checked by hand |
| E65 | Several agent skills are wrong against today's code: the screen skill names a `screenshot` action that does not exist, the watchtower skill omits the News switch and the dry-run default, and others cite stale paths or archived skills. | `skills-snapshot/apple/screen-oracle/SKILL.md:34` | confirmed |
| E66 | The loop guard blocks the third identical read-only call in a turn even when the answers changed or the earlier calls failed, and its message wrongly says nothing could have changed. | `skills-snapshot/_plugins/loop-breaker/__init__.py:96` | confirmed |
| E67 | On the approval path any reply other than "deny", including "no", "cancel" or an empty one, counts as approval. Today only the dashboard's own "once" and "deny" are ever sent. | `hermes-plugins/approval-gate/__init__.py:1261` | confirmed |
| E68 | Files your shell startup files load, such as `~/.cargo/env` or `~/.curlrc`, are not in the protected "shell startup" class, so the agent can write them without a card. | `hermes-plugins/approval-gate/__init__.py:297` | confirmed |
| E69 | The hidden Needs-you drafts conversation can be read, branched or chatted in by naming it with different capital letters (`NEEDSYOU`), including through the MCP `chat_get` tool. What leaks is the model's draft replies, and only to a caller that deliberately tries the variant. | `dashboard/server.py:3027` | confirmed (verifier; was P2) |
| E70 | Continuing an old conversation that has messages but no stored agent session starts a blank agent with no notice, while the window shows the whole conversation. | `dashboard/hermes_rpc.py:937` | confirmed |
| E71 | When you click Approve after the agent had already dropped the request, the command does not run and the reply carries no note about it, unlike the timeout case. | `dashboard/hermes_rpc.py:792` | plausible |
| E72 | Quick Ask shows a reply as normal even when it could not be saved, and a refused send discards the text and attachments you had typed. | `dashboard/aux_quickask.js:1113` | plausible |
| E73 | Two breaking alerts sent in the same watchtower pass share one Needs-you id, so marking one Done also hides the other. Both already reached you on Telegram, and it has not happened in 159 live sends. | `dashboard/aux_needsyou.py:1053` | confirmed (verifier; was P2) |
| E74 | With the optional model pass on, it can promote to Now a message whose deadline is days past, an automated sender or a stranger. Off by default; nothing is hidden. | `dashboard/aux_needsyou.py:1588` | confirmed (verifier; was P2) |
| E75 | The text-fencing helpers leave a literal `<<<END …` in place when the input has four or more `<` in a row. A full closing marker still cannot be rebuilt. | `dashboard/untrusted.py:49` | confirmed |
| E76 | The morning brief's "Later today" list ignores AM/PM, so on a 12-hour clock afternoon and evening events are treated as past and dropped. | `dashboard/aux_watchtower.py:1659` | plausible |
| E77 | GitHub drift and push notices go out by Telegram with both notification masters off. CLAUDE.md's "both masters off = no background push from the dashboard" is false. | `dashboard/aux_github.py:597` | plausible |
| E78 | Needs-you's empty state promises a "next brief" even when Briefings are switched off or the watchtower config is corrupt. | `dashboard/aux_needsyou.py:1964` | plausible |
| E79 | After any Done or Snooze in the widget, every row in the reloaded list, collapsed ones included, is recorded as "shown", which skews the metrics. | `dashboard/aux_needsyou.js:261` | plausible |

### Egress, MCP and the Claude bridge

| ID | Finding | Location | Verification |
|---|---|---|---|
| E80 | The bridge gate checks wording, not values: credentials pasted into a chat are sent on to Claude by auto-routing when escalation is on, and the first 160 characters go into the usage log, which claims to hold no secrets. | `dashboard/aux_claudebridge.py:295` | confirmed |
| E81 | The MCP server exits instead of answering with a parse error when a client sends a very deeply nested JSON line. The line length is also unbounded. | `dashboard/hermes_mcp.py:2282` | confirmed |
| E82 | The shared redaction block misses unlabeled Google tokens (`ya29.`, `1//`, `GOCSPX-`), `hf_`, `sk_live_`, `glpat-` and `npm_` keys, and `user:password@` in web addresses, so they pass into exports and MCP results. | `dashboard/aux_convos.py:55` | confirmed |
| E83 | The structured part of an MCP result may reach 16 KB and list different items from its 8 KB text, although CLAUDE.md says both come from the same capped strings. | `dashboard/hermes_mcp.py:434` | confirmed |
| E84 | Watch rules poll Yahoo Finance and CoinGecko every five minutes around the clock while News is on, but the network table describes only the Telegram message. | `dashboard/aux_watchtower.py:3308` | plausible |
| E85 | The "last outbound" time for updates freezes when a check reaches GitHub but fails. | `dashboard/aux_update.py:518` | plausible |
| E86 | For-You's Claude step is refused for the whole batch when one headline such as "Show HN: Build a CLI…" trips the intent filter, silently falling back to the local model. | `dashboard/aux_foryou.py:601` | confirmed |
| E87 | The Google sign-in takes its authorization and token addresses from the pasted client file without checking they are Google's. | `dashboard/aux_google.py:540` | plausible |

### Frontend, advisor and long-run behaviour

| ID | Finding | Location | Verification |
|---|---|---|---|
| E88 | A recorder day group that is open only by default collapses on its own when the first action of a new day arrives, hiding its rows and detail pane and dropping keyboard focus. Regression from the Agent-page rework. | `dashboard/aux_recorder.js:287` | confirmed |
| E89 | The "Claude dialogues" sheet on the Agent page ignores Esc and lets Tab wander through the page behind it. | `dashboard/aux_agent.js:1473` | confirmed |
| E90 | Search highlights land on the wrong characters after an emoji, because the server counts code points and the page counts UTF-16 units. | `dashboard/index.html:1733` | confirmed |
| E91 | A saved view name the app does not know leaves the whole stage blank on every launch until you click a tab. | `dashboard/index.html:4388` | confirmed |
| E92 | Some polls keep running while the window is hidden: the header's weather and usage poll every minute, and the Settings shortcuts and memory polls every 10 and 15 seconds. | `dashboard/aux_topbar.js:155` | plausible |
| E93 | The Flight Recorder filter chips are plain spans that cannot be reached or used from the keyboard. | `dashboard/aux_recorder.js:573` | plausible |
| E94 | If one hardware command times out on the advisor's first check, that blank answer is kept until restart and "Re-check" does not retry it; a 64 GB Mac can then be advised to run only the smallest model. | `dashboard/aux_advisor_hw.py:1305` | confirmed |
| E95 | The advisor counts a background model lane as running and may recommend one, but nothing ever starts that lane, so fit verdicts are too pessimistic and extra downloads are suggested. | `dashboard/aux_advisor.py:1177` | confirmed |
| E96 | When a fetch raises and there is no recent good value, the widget cache never backs off, so every hub poll retries the network during an outage. | `dashboard/server.py:3352` | confirmed |
| E97 | Needs-you rebuilds and rewrites its store file every 60 seconds whether or not anyone is looking or anything changed. | `dashboard/aux_needsyou.py:2058` | confirmed |
| E98 | Idle-suspend's shutdown of the model is not coordinated with a chat starting, so a message sent at that exact moment can be killed mid-turn. | `dashboard/server.py:2731` | plausible |

### Model lifecycle, native shell, onboarding, stores

| ID | Finding | Location | Verification |
|---|---|---|---|
| E100 | The model server, sealed to one model, still opens any local image, audio or video path and fetches any web address a request names, so any local process can use it to probe files or make web requests. | `mlx-vlm-launch.py:494` | confirmed |
| E101 | Pressing Pause at the wrong moment (during a Thinking toggle, a model switch or a "free memory" restart) can leave the model loaded while the app says paused. | `dashboard/server.py:2196` | confirmed |
| E102 | On an install set to keep the model always on, a pause does not survive a re-login or an update. | `mlx-server.sh:29` | plausible |
| E103 | If the dashboard restarts in the middle of a model drill, the model under test stays as the assistant's model for good, including an uncensored one with no acknowledgement. | `dashboard/aux_promotion.py:513` | plausible |
| E104 | A model added under a built-in uncensored model's name in different letter case shows no Uncensored badge and switches in without the acknowledgement. | `dashboard/server.py:4421` | confirmed |
| E105 | The free-disk check for downloads ignores downloads already running, so two or three large models started together can fill the disk past the 5 GB margin. | `dashboard/server.py:5542` | plausible |
| E106 | A hand-edited sampling value holding an absurdly large whole number crashes the model list instead of being dropped, so the model menu cannot load. | `dashboard/server.py:4402` | confirmed |
| E107 | Dictation cleanup deletes real words that look like fillers ("ER", "AH-64") and turns "uh-huh" into "-huh", in every style including verbatim. | `dashboard/aux_dictation.py:316` | confirmed |
| E108 | Any page shown inside the app from a local address on any port, not only the dashboard's, could read and overwrite the clipboard through the native bridge. Nothing navigates there today. | `app/main.swift:677` | plausible |
| E109 | If the web engine inside the app crashes, the main window or Quick Ask popover stays blank until you press ⌘R. | `app/main.swift:295` | plausible |
| E110 | On the setup sheet, flipping a switch with the keyboard sends focus to Continue, so a second Space press advances the step instead of toggling back. | `dashboard/aux_onboarding.js:1494` | confirmed |
| E111 | When setup's download is refused for too little disk, the background lane has already been pointed at the model that was not downloaded, and the sheet reports only the refusal. | `dashboard/aux_onboarding.py:1206` | confirmed |
| E112 | Restoring a saved configuration signs the imported permissions policy without the re-trust review. The safety floor still stops Auto on risky classes, but your own Ask or Never on the six unfloored classes can revert to Auto, shown only as "policy differs". | `dashboard/aux_config.py:776` | confirmed (verifier; was P2) |
| E113 | Two changes to the model list made at the same moment can overwrite each other, silently losing one. | `dashboard/server.py:4874` | plausible |
| E114 | If the memory-index file is briefly unreadable or holds the wrong kind of data, it is replaced by an empty index with no saved copy. | `dashboard/aux_memory.py:335` | plausible |
| E115 | "Review and re-trust" signs whatever the permissions file contains at the moment of the click, not the version shown in the review. | `dashboard/permissions.py:1014` | plausible |
| E116 | Configuration exports and pre-restore backups are created readable by every account on the Mac (0644), unlike the other private stores. | `dashboard/aux_config.py:472` | confirmed |

### Never-read modules, HTTP core, install, motion, tests

| ID | Finding | Location | Verification |
|---|---|---|---|
| E117 | An eval run and a model drill can overlap, so one run may judge two different models under one model's name and use up the day's scheduled slot. | `dashboard/aux_evals.py:406` | confirmed |
| E118 | Desktop screenshots older than 24 hours are deleted only when someone lists or captures; otherwise they stay on disk and can still be fetched. | `dashboard/aux_desktop.py:97` | confirmed |
| E119 | If a drill fails, or restoring the assistant's model afterwards fails, the owner is never told. | `dashboard/aux_promotion.js:138` | plausible |
| E120 | Clipboard text goes to the model with no "untrusted data" fence, contrary to the stated rule. | `dashboard/aux_clip.py:210` | plausible |
| E121 | The memory-layer settings route switches the layer on plain truthiness, so the text "false" turns it ON. This is what remains of C59. | `dashboard/aux_memlayer.py:618` | plausible |
| E122 | A local client sending request headers one byte at a time keeps a thread and file handle open indefinitely, because the 20-second timeout restarts on every byte. C13 is only partly fixed. | `dashboard/server.py:6094` | confirmed |
| E123 | Wrong-typed or odd values give HTTP 500 instead of 400 on about 15 routes, and several of these failures are never logged. | `dashboard/server.py:6479` | confirmed |
| E124 | Failures in the aux route modules are logged as `<string>:line` instead of the file name, so the log cannot say which module failed. | `dashboard/server.py:5819` | confirmed |
| E125 | When extra allowed host names are configured, an https tunnel can never POST, while a name given without a port also has its port-80 origin trusted. Not set in the shipped plist. | `dashboard/server.py:5924` | confirmed |
| E126 | Rebuilding the main app, which the updater advises after any app change, deletes the dictation helper, and doctor then reports dictation as "not installed (optional)". | `app/build-app.sh:21` | plausible |
| E127 | Doctor can say "vX is available" right after vX was installed, because it trusts an old cached answer. | `dashboard/doctor.py:2006` | confirmed |
| E128 | The release checksum file comes from the same place as the download, so it catches corruption but not a tampered release; the script comments describe it as stronger. | `update.sh:411` | plausible |
| E129 | An update shuts down running model servers, and a background lane started by hand stays off afterwards, although the notes say they stay "exactly as they were". | `install-services.sh:236` | plausible |
| E130 | On the "main" update channel, a checkout with local commits ahead of origin is told an update is available forever. | `dashboard/aux_update.py:439` | plausible |
| E131 | Double-clicking the chat expand button within about half a second leaves a stray inline style on the chat column. No visible breakage found. | `dashboard/index.html:1599` | confirmed |
| E132 | In full chat mode the conversation list replays its entrance animation after every reply, rename, pin or delete, contrary to the "animate once" rule. | `dashboard/index.html:2086` | plausible |
| E133 | Hardening only, no current caller: an outside cancel can leave an element invisible, a layout move can leave leftover transform styles, and text swapped mid-animation reverts. | `dashboard/aux_anim.js:134` | plausible |
| E134 | No test checks that the change digest covers a change's body and its public-repository confirmation; dropping either passes every test. | `dashboard/aux_ghchanges.py:1672` | confirmed |
| E135 | Six narrow safeguards in the GitHub and approval-gate code can be removed with no test failing. | `dashboard/aux_ghchanges.py:1713` | confirmed |
| E136 | The model server's pin to a single model is tested only on the owner's Mac, because CI skips the test that needs the model environment. | `mlx-vlm-launch.py:641` | confirmed |
| E137 | The test runner and CI have no time limit, so one hung test blocks a local run forever and burns CI's six-hour default. | `tools/tests/run.sh:150` | plausible |
| E138 | Two test-hygiene claims are false: several suites still use hand-written copies of settings and escaping helpers, and the "no network" guard does not cover child processes or git. | `tools/tests/run.sh:9` | plausible |

## Documentation drift

Statements that are false against the code, user-facing first.

| ID | Document says | Code says | Where |
|---|---|---|---|
| E139 | README privacy: "a test search every 6 hours, plus an hourly news-research pass" run on their own | Both run only while agent web research is on (off by default) and News is on | `README.md:761-763` → `dashboard/aux_watchtower.py:2362` |
| E140 | "For-You's hourly Claude pass" | Every 2 hours; the in-app table already says so | `SECURITY.md:30`, `README.md:767` → `dashboard/aux_foryou.py:54` |
| E141 | RUNBOOK goals: "Gmail (drafts only)" | The grant is read-only (`gmail.readonly`) | `RUNBOOK.md:6` → `dashboard/aux_google.py:24` |
| E142 | README lists what the updater writes under `~/.hermes`: the tool-budget link only | It also links and enables approval-gate and restarts the Telegram gateway | `README.md:619-627` → `update.sh:526-552, 577-619` |
| E143 | README roadmap leaves open "Voice in and out (local STT/TTS)" | Local dictation shipped in 1.3.0 | `README.md:935` → `CHANGELOG.md:544` |
| E144 | "a 13-row 'What leaves this Mac' table", later plus "two" GitHub rows | The table has 16 rows | `CHANGELOG.md:264, 288` → `dashboard/aux_onboarding.js:231` |
| E145 | "update.sh never restarts the Telegram gateway" | It restarts `ai.hermes.gateway` whenever launchd has it loaded | `CLAUDE.md:753-754` → `update.sh:577-619` |
| E146 | "`set_master` still coerces with `bool()` (C59 open)" | Watchtower switches now reject non-booleans; the real remaining case is E121 | `CLAUDE.md:445-446, 475` → `dashboard/aux_watchtower.py:3707-3710` |
| E147 | "Doctor (`dashboard/doctor.py`, 21 checks)" | 22 checks; README and RUNBOOK already say 22 | `CLAUDE.md:789` → `dashboard/doctor.py:1396` |
| E148 | "Two views via a header segmented control: Hub … and Mind" | Three tabs: Hub, Settings (internal id `tab-mind`) and Console | `CLAUDE.md:28-29, 522-523` → `dashboard/index.html:1348-1352` |
| E149 | "`WIDGETS` catalog (20 widgets)" | 20 in the base dict; aux modules register five more, so 25 at runtime | `CLAUDE.md:499-500, 697` → `dashboard/server.py:4086` |
| E150 | "Harness: `scratchpad/t_mcp.py`, 293 checks" | The harness is `tools/tests/live/t_mcp.py` | `CLAUDE.md:1568` → `tools/tests/live/t_mcp.py` |
| E151 | "which is why there is no `aux_context.py`" | `dashboard/aux_context.py` exists (context and compaction) | `CLAUDE.md:1512-1513` → `dashboard/aux_context.py` |
| E152 | "(re)install + start the three services" | Four labels; the two model ones start on demand | `CLAUDE.md:1586` → `install-services.sh:12-15` |
| E153 | "The model-wake guard is a system-wide pgrep and can trip on another shell" | Since R2-61 `run.sh` parses `ps` and counts only real launcher processes; the tests README has the same stale text | `CLAUDE.md:1608-1609`, `tools/tests/README.md:87-97` → `tools/tests/run.sh:38-81` |
| E154 | Graphify "~2.8k nodes/5.1k edges" | The checked-in graph was built in July and predates round 3; a rebuild would be much larger (inferred, not rebuilt) | `CLAUDE.md:135-145` → `graphify-out/graph.json` |

Code findings that also make a CLAUDE.md sentence false: E05 ("the push
budget" bound), E10 ("in every path form"), E28 (the key-repeat rule), E63
(the toast's pointer events), E77 ("both masters off = no background push"),
E83 (structured output from the same capped strings).

## P3 fix notes

- E49 — Re-copy the skill; add it to the deploy checklist's copy list.
- E50 — Add read probes for account permissions that widen reach and reword the headline to what is observed.
- E51 — Record why a hold was set and clear it only when that cause is re-observed gone; never store `expired`.
- E52 — Retry a failed check on a short back-off; pass the rate-limit error out of the workflow fetch.
- E53 — Make remove clear the row (or delete it when nothing refers to it) and count only enabled rows.
- E54 — One summary notice per repository per check, sent after the network lock is released.
- E55 — Read visibility from GitHub at decide time and refuse as stale when it changed.
- E56 — Pin every transport in the hard flags; compare config bytes to the template after each call and treat a difference as tampering.
- E57 — When sweeping a change stuck in `pushing`, read GitHub's head and write the compensating ledger row if the commit is absent.
- E58 — On a successful retract mark every earlier pushed change on that branch retracted too.
- E59 — Keep leading indentation; the body is Markdown, not a prompt line.
- E60 — Exempt a `www.` run only at line start or after whitespace; strip every unused link-reference definition.
- E61 — Mark a stray `\r` like other hidden characters; take file names from the `+++`/`---` lines.
- E62 — Say "pull request" when `pr.draft === false` and mention the fallback.
- E63 — Tie the toast's pointer events to its visible class.
- E64 — Count bytes in the textarea against 8 KB, or refuse an over-long body with a 422.
- E65 — Correct the eight wrong places, re-copy, and extend the skills test to check action names.
- E66 — Count only repeats with an unchanged non-error result, as Hermes' own guardrail does.
- E67 — Approve only on `once`, `session`, `always` or `approve`.
- E68 — Add the common sourced files statically; optionally read the rc files for more.
- E69 — Compare case-folded ids in one shared helper used by server, branch, convos and MCP.
- E70 — Seed from the transcript whenever it is non-empty.
- E71 — Add a note in the "nothing pending" branch, as the timeout branch does.
- E72 — Mirror the main window's not-saved message; restore typed text on every send that did not start.
- E73 — Key the row on the story's signature.
- E74 — Require the deadline inside the past-due and upcoming windows and a known sender.
- E75 — Replace runs of three or more `<` or `>` with one regex in all four copies.
- E76 — Reuse the 12-hour-first clock parsing Needs-you already has.
- E77 — Document the security-notice exception or gate drift notices on a setting; fix the table row.
- E78 — Return no next brief when the master is off or the config has an error.
- E79 — Reload with `?mark=0` after an act.
- E80 — Run the shared credential test on task and context before calling Claude; log a redacted summary.
- E81 — Catch `RecursionError` and `MemoryError`; cap the line length.
- E82 — Add the anchored shapes and a URL-userinfo rule to all three copies of the block (with E24).
- E83 — Bound the structured result to 8 KB or document two caps.
- E84 — Add the polling to the alerts row.
- E85 — Record every attempt and have the card read that.
- E86 — Gate only the fixed task text; keep the harm scan on the fenced headlines.
- E87 — Require Google's own hosts for both addresses.
- E88 — Keep a group's default-open state for the page life; never collapse one holding focus or an open pane.
- E89 — Dialog role, focus on open, and an entry in the Esc/focus registry.
- E90 — Send UTF-16 offsets, or convert on the client.
- E91 — Whitelist the stored view name.
- E92 — Skip the three polls while hidden; catch up on `visibilitychange`.
- E93 — Render the chips as buttons with `aria-pressed`.
- E94 — Treat a missing fact like the failed Metal probe: retry on bust and every few minutes.
- E95 — Count the lane only when a marker or start token says it can run.
- E96 — Record the retry deadline before re-raising.
- E97 — Gate the loop on a recent client; skip the save when nothing changed.
- E98 — Take the lifecycle lock around the bootout and re-check activity inside it.
- E100 — Reject any message media reference that is not a `data:` URI before it reaches the route.
- E101 — Take the lifecycle lock around bootout and start in the Thinking toggle and model switch; refuse "free memory" while paused.
- E102 — Make `mlx-server.sh` exit when the pause marker exists, before the always-on branch.
- E103 — Write a restore-to marker before the swap and honour it at dashboard boot.
- E104 — Compare model ids case-insensitively; refuse case variants of curated ids.
- E105 — Subtract in-flight downloads from free space before the headroom check.
- E106 — Catch `OverflowError` in both copies of the sampling validator and drop the value.
- E107 — Match fillers case-sensitively, treat a hyphen as a word character, drop "mhm".
- E108 — Require main frame and the dashboard's exact origin in the bridge handler (next Swift batch).
- E109 — Implement the content-process-terminated delegate (next Swift batch).
- E110 — Patch switches in place, or restore the focused control across repaints.
- E111 — Write the background-lane model only once its download is complete or under way.
- E112 — Show per-class from→to rows before applying, and merge rather than replace.
- E113 — Add a locked models-update helper and use it in every roster writer.
- E114 — Read the memory-meta file with the quarantining read-for-update helper.
- E115 — Send the file hash the review was built from and refuse if it changed.
- E116 — Create exports and backups 0600.
- E117 — Make each runner refuse while the other runs; pass the captured model id.
- E118 — Prune at module load and in the shot handler; refuse frames past the limit.
- E119 — Toast the drill's error or failed restore when its poll ends.
- E120 — Fence clipboard text, or record this path as an accepted exception.
- E121 — Reject non-boolean `enabled` and `episodic` with a 400.
- E122 — Put a wall-clock deadline on the request line and headers.
- E123 — Validate field types; ASCII-only digit checks; log from self-catching handlers.
- E124 — Compile aux files with their real path so tracebacks name the file.
- E125 — Apply the port-less rule to every name; accept https for configured hosts or drop the tunnel wording.
- E126 — Make `build-app.sh` remove only its own bundle.
- E127 — Recompute availability against `VERSION`, as the Settings card does.
- E128 — Sign the checksum file, or reword the comments to "integrity, not authenticity".
- E129 — Skip bootout of a model job whose plist did not change.
- E130 — Report an update only when `origin/main` is not already an ancestor of HEAD.
- E131 — Clear identity end values when a spring is interrupted.
- E132 — Key the sidebar rows, or repaint inside the quiet wrapper.
- E133 — Settle on external cancels; clear static individual transforms before a flip.
- E134 — Alter each field of a stored change and assert the decision is refused as stale.
- E135 — One targeted assertion per branch.
- E136 — Drive the pin against a small fake server module in the unit tier.
- E137 — Per-suite timeout reported as FAIL; `timeout-minutes` on the CI job.
- E138 — Switch the five suites to the real helpers; document or guard git and child processes.

## Status of earlier findings

Nothing is stranded: every `fix/k…`, `fix2/w…` and `fix3/…` branch and
`audit-2026-10-21` has no commit beyond main.

| Item | Status today | Evidence |
|---|---|---|
| B02 / F05 / C131 (same-user processes can call owner-only routes) | open as documented; no nonce exists anywhere | `dashboard/server.py:5665, 5946` are comments only |
| B18, Swift half (the shell opens any URL scheme) | open as documented | `app/main.swift:197-199, 236-250` |
| C89 (chat.db copied every 60 s) | open, and worse than documented: see E26 | `app/main.swift:778-788` |
| C59 (switch routes coercing with `bool()`) | fixed everywhere except the memory layer (E121); CLAUDE.md names the wrong route (E146) | `dashboard/aux_memlayer.py:618` |
| C13 (idle connections) | partly: bodies are bounded, headers are not (E122) | `dashboard/server.py:6094` |
| C28 (route failures name the file) | false for aux routes (E124) | `dashboard/server.py:5819` |
| C116 (approval-gate path coverage) | re-opened by E10, E11, E12 | `hermes-plugins/approval-gate/__init__.py` |
| C58 (deleted chat stays in Hermes' state.db) | open; owner decision | no state.db delete in the delete route |
| C64 tail (tarball update deletes the old export) | open for the 1.3.2 hop only; cannot be fixed from the new release | see E29 |
| `softprops/action-gh-release` not pinned to a SHA | open | `.github/workflows/release.yml:183` |
| Temperature possibly ignored on the batch path | open; no probe built | deploy checklist §4 |
| aux_desktop.py (last audit's "not covered" item 5) | now audited: E118 | `dashboard/aux_desktop.py:97` |
| Skills contradicting code ("not covered" item 1) | the three named cases are fixed; eight new ones in E65; one stale installed copy (E49) | `skills-snapshot/` |
| loop-breaker vs Hermes' own loop guardrails ("not covered" item 2) | reconciled; one behavioural difference filed as E66 | `skills-snapshot/_plugins/loop-breaker/__init__.py:96` |
| C18, C39, C44, C47, C62, C94, C95, C99, C104, C114, F07+B19 (partly fixed) | not individually re-derived this round | — |

**Round 2 has no document.** Commit subjects carry R2-1…31, 33…44, 46…61 and
73. R2-32, R2-45 and R2-62…72 never appear, and neither do X-frontend-1,
X-model-lifecycle-3 and X-prompts-egress-2/3. They may be unfixed findings or
unused numbers; this cannot be settled from the repository.

## Checked and sound

The strongest negative results, one line each.

- *gh-connector* — The GitHub key never appears in any response, state file, recorder row or log; scrubbing covers raw, base64 and header forms.
- *gh-connector* — Redirects are never followed and the test-only address overrides work only with the offline flag plus a loopback address.
- *gh-connector* — The six-hourly check made exactly 13 GET requests and no write; every owner-only route answered 403 without proof of owner.
- *gh-changes* — No git call ever uses the sandbox's own `.git`; only `hermes/*` refs are pushable; 16 parallel approvals of 16 changes each pushed exactly once.
- *gh-changes* — The sandbox's `.gitattributes` could not change the review diff, and file names with newlines or leading `--` reached no command line.
- *gh-ui* — Markup payloads in every field of the card, sheet, checks, diff and ruleset created no element; `ghUrl` rejected every URL trick tried.
- *gh-ui* — Approve stays disabled until no block check remains, every tick is given and a title exists; a double click sent one request; the token is in neither the markup nor module state after a failed submit.
- *agent-boundary* — The gate failed closed in every probe: missing upstream helpers, a cron session, YOLO mode and an unknown computer action all ended blocked or asked.
- *agent-boundary* — The running agent uses the current plugin code: approval-gate and tool-budget are links into the checkout and loop-breaker is a byte-identical copy.
- *chat-core* — 80 randomised turns against a fake serve queue ran no command without an accepted Approve on its own card, outside the documented no-request-id residual.
- *chat-core* — The WebSocket client passed a fuzz over a real socket: 16- and 64-bit lengths, fragments with pings, a split at every byte, 70 kB frames.
- *model-lifecycle* — All 49 routes of mlx_vlm 0.6.14 plus the realtime socket were driven through the real guard and pin; any other model id is a 400 before loading.
- *model-lifecycle* — The two copies of the subfolder and sampling validators agreed on 300k fuzz inputs.
- *attention-pipeline* — The watchtower config fails closed: a corrupt file forces both masters off, and every scheduled, breaking and rule send re-reads it at send time.
- *attention-pipeline* — Every prompt input in these modules passes third-party text through the fencing helpers; search queries are quoted token by token.
- *trust-stores* — All 72 dangerous-command patterns in the installed Hermes map to a class; unknown keys can only resolve to Ask.
- *trust-stores* — Floors are clamped at read time even for a hand-written Auto; shortcut tickets are single-use and bound to their input.
- *egress-mcp-bridge* — `claude_think` is the only place `claude` is spawned; the master switch fails closed; every hardening flag exists in the installed CLI.
- *egress-mcp-bridge* — The vendored Motion, GSAP and anime files contain no network calls, and nothing in the page loads a remote asset.
- *egress-mcp-bridge* — The MCP allowlist behaves as documented: only JSON `true` enables a tool, and the restart, install and force flags are never forwarded.
- *http-core* — About 100 GET and 94 POST routes were swept with hostile input on a hermetic copy: no state-changing GET, and every body-length lie got the documented 400, 408 or 413.
- *http-core* — Uploads with `../`, absolute paths and RTL tricks all landed in the private inbox as 0600 files; reveal resisted symlink escapes.
- *advisor* — The plan limit is monotonic from 8 to 512 GB; 47,520 ranking runs never recommended an uncensored or undrilled build and never varied between identical calls.
- *advisor* — Every parser survived hostile input, and the serial number and UUID never leave the hardware reader.
- *onboarding-advisor-ui* — The uncensored gate on apply accepted only a literal `true` for primary and background picks and wrote nothing on a refusal.
- *motion* — Every interruption on the real libraries settled clean: enter during exit, an element removed mid-flight, flip during flip, hidden or reduced motion mid-flight, a failed library load.
- *motion* — The vendor route is an exact-key lookup; every served file re-hashed to its manifest value with licence banners intact.
- *frontend-core* — A fuzzer fed tag and quote payloads into every property of every widget and pop-out renderer; the only unescaped sinks were numeric.
- *frontend-core* — The greeting removal left no stale reference; all 46 theme tokens match across light, dark and the media block.
- *native-shell* — The Swift message decoder survived 300,000 random blobs with no trap; speech recognition is on-device only; secure fields are refused.
- *install-update-release* — The update lock held across the exec; `release.yml` has no `pull_request_target` and no untrusted expression in a `run:` step.
- *tests-quality* — 45 of 63 mutants were killed; thirteen suites passed under extreme time zones, locales, a home path with a space and three concurrent copies.
- *concurrency-longrun* — A lock-order graph over 47 locks found no cycle; all 19 background loops catch exceptions; 24,000 hermetic requests showed no growth.
- *docs-drift* — The Model advisor bullet (38 of 38 claims) and the GitHub bullet (45 of 45) were true against the code.

## What is still not covered

1. **WebKit.** Everything in the frontend was measured in Chromium or read
   statically. That includes the approval card (E15), the review sheet (E02)
   and all motion frame cost. A Playwright WebKit pass against fixture APIs
   would close most of it; the manual pass in the app remains on the owner's
   checklist.
2. **Anything live.** No model turn, no real approval round trip on the
   dashboard or Telegram, no real GitHub (so the 301 on rename in E07, the
   draft fallback in E62 and how github.com renders a NUL-carrying file are
   assumptions), no real `claude -p` child, no real update.
3. **The wider patch-header differential.** E11 verified carriage-return
   variants only. A full fuzz of the gate's header extraction against
   Hermes' own parser was planned and not finished.
4. **Tools outside the gate's file list.** Whether MCP read tools, media
   tools, `skill_manage`, `memory` or `cronjob` can reach protected paths, by
   alias or otherwise, was not tested.
5. **The eleven "partly fixed" rows** from the last audit were spot-checked,
   not re-derived.
6. **Round 2's missing ids** (above).
7. **Sampled, not read in full:** aux_trust.js, aux_memlayer.js, aux_prefs.js,
   aux_youmodel.js, aux_google.js, aux_shortcuts.js, aux_memory.js, and
   `doctor.py` check by check on a pristine install.
8. **Real launchd and a real model load** for the lifecycle findings (E21,
   E101, E102 and E98 rest partly on fake-clock or static evidence).

## Suggested order

Grouped so that each cluster touches its own files, as in the last two rounds.

1. **One-line fixes, do first:** E15 (CSS on the approval card), E20 (empty
   proxy handler), E21 (count resume as activity), E30 (CI skip list), E121
   (strict boolean).
2. **Approval surfaces:** E02 and its P3s (E61, E62, E64), E14, E03.
3. **approval-gate path handling:** E10, E11, E12, E13, E67, E68, with the
   unfinished differential fuzz as its test.
4. **Flight Recorder:** E01, E33, E88, E93.
5. **GitHub server side:** E05, E06, E07, E08, E09, then E50–E60 and the two
   test gaps (E31, E134, E135).
6. **Needs-you and Watchtower:** E04, E73–E79, E97.
7. **`config.yaml` editors:** E16 and E17 together.
8. **Model lifecycle and setup sheet:** E19, E22, E23, E101–E106, E110, E111,
   E117, E119.
9. **Privacy wording and secret detection:** E24, E25, E80, E82, E139–E144.
10. **Updater:** E29, E126–E130.
11. **Next Swift rebuild, one batch:** E26, E108, E109, the B18 Swift half,
    and F05 if it is ready. A rebuild drops Full Disk Access, so batch them.
12. **Dictation:** E27 and E107 before the helper is next built.
13. **CLAUDE.md:** E145–E154 and the six sentences the code findings falsify.

## Implementation status (branch `audit-2026-11-09`)

Implemented on 2026-11-10 in fifteen file-owned cluster branches
(`fix4/c01…c13`), each with an Opus implementer and an independent reviewer
that tried to break every fix and repaired what it broke. The four
highest-risk clusters (approval-gate, Flight Recorder, GitHub change
pipeline, Trust policy) were reviewed on Claude Fable 5; in each of them the
reviewer found the first fix still bypassable and closed it. Two seam passes
then made the changes clusters needed in each other's files, a docs pass
brought CLAUDE.md and the user docs into line, and a final cross-cluster
review on Fable 5 checked the seam work and the merged whole.

**129 fixed, 4 partly fixed, 4 not done, 1 skipped.** Every fix has a
regression test that drives the real code and was shown to fail on the
pre-fix code. Reviewers made 65 repair commits.

After the merge:
- Unit tier: **17,395 / 17,395** across 495 suites, strict, offline guards on (12,797 before).
- The same tier under CI's environment on a clean clone with an empty home:
  17,309 passed, 0 failed, 8 skips, all on the expected list.
- A hermetic smoke run on data written by the pre-fix build answers all 101
  GET and 94 POST routes with no 5xx and no traceback, sends the frame
  headers everywhere and refuses cross-origin requests.
- Six real-engine browser suites pass on headless Chromium.

Not done: the three Swift findings (the main app's source is frozen until the
next batched rebuild), and anything that needs a live model, real GitHub or
WebKit. See `docs/audits/2026-11-09-deploy-checklist.md` for what the owner
has to do before and after merging.

"Review" says what the cluster's reviewer concluded: *good* (attacked and
held) or *fixed by review* (the reviewer broke it and repaired it).

| ID | Status | Review | What changed |
|---|---|---|---|
| E01 | partly fixed | fixed by review | Undo reads the checkpoint store first. It restores only what the snapshot holds, and trashes a file only when creation is proven: a `write_file` (never a `patch`), not from Telegram, a path the store would have snapshotted, under the size cap, no earlier recorded touch, unchanged ignore files, mode 0600. Otherwise it refuses and the row shows as not undoable before any click. Undo of a trash move restores from trash and never overwrites. **Remaining:** a 0600 file absent from the matched snapshot that the agent never read can still be judged created; closing it needs a Hermes hook. |
| E02 | fixed | fixed by review | Sheet: diff lines wrap; runs of 40 blank characters or 80 columns, bare CR and 4+ stacked combining marks are drawn as markers; a binary file counts as shown only when its whole text preview is drawn, else it needs a tick; Approve stays off until a diff past 3,000 lines is expanded. Server: `C-binary` is a warn for every binary file, mostly-text files get the hidden-character scan and `text_preview`, and a new `C-longline` warn covers long or blank-padded lines. Checked end to end with a real server payload in the real sheet. |
| E03 | fixed | fixed by review | `decide()` re-scans the whole command against a copy of upstream's 73 patterns with upstream's path folding and takes the most restrictive verdict. Auto drops to Ask for any operator, substitution, bracket or brace, control character, quoted or expanded command word, redaction mark, missing command text or a command over 512 characters. Two patterns that backtracked exponentially were recompiled. 1.8 million fuzzed commands: no Auto where upstream sees something stricter. |
| E04 | fixed | fixed by review | Sender test order is handle, then reply history or contact, then body words (body only for senders with no history). The pop-out lists `never` rows with re-file buttons; the route serves them only for `?never=1`. |
| E05 | fixed | good | Pause, Trust tier and the push budget are checked again once `_GH_NET_LOCK` is held. 10 of 14 and 10 of 16 queued approvals pushed; the rest were refused and stayed pending. |
| E06 | fixed | fixed by review | `.env` is parsed the way python-dotenv parses it; both the parsed value and the raw text are canaries. A parser error no longer drops every canary. |
| E07 | fixed | fixed by review | A redirect on the repository read marks only that row moved, with a plain reason; no hold. Remove drops the row. Works for rows stored before the change, which have no id. |
| E08 | fixed | fixed by review | Status carries `write_reach` and a note; new connections store `sampled` or `unchecked`, and a stored `contained` is reported as `sampled`. The card warns unless reach was proven. |
| E09 | fixed | fixed by review | Over-limit content is refused before anything is written to the mirror; mirrors are pruned after reject, withdraw, expiry and decide; pending changes keep their objects through hold refs. Remove deletes that mirror; Disconnect and Revoke delete all. |
| E10 | fixed | good | The gate strips the firmlink prefix, asks the kernel for each path's real name, and refuses `/.vol`, `/.nofollow`, `/.resolve` and `/.file` outright, for writes, reads and searches. |
| E11 | fixed | fixed by review | Patch headers are extracted the way Hermes' parser extracts them; a header with a control or hidden character asks under a new key. A `~name` that is not a user (`~0/…`) is refused. A 496-patch differential fuzz guards it. |
| E12 | fixed | fixed by review | A classified patch target is never dropped on the strength of Hermes' own refusal. The gate asks Hermes' shell layer where a raw path lands and fails closed if it cannot. |
| E13 | fixed | fixed by review | Relative file paths are judged by Hermes' task resolver, not the gateway's working directory. Media and MCP tools keep the working-directory form. |
| E14 | fixed | fixed by review | Cards show head and tail with the omitted count, show `replace_all`, mark hidden characters, and stay under Telegram's cut. A payload in the middle of very long content is still hidden behind the count. |
| E15 | fixed | fixed by review | Both approval cards render commands line for line, mark hidden characters, bare CR, blank runs and stacked marks, and tell the owner the length of a long command above the buttons. |
| E16 | fixed | good | The editors read and write lists at the key's own indent, in the file's own style. `plugin_enable.py` exits non-zero on a refusal. |
| E17 | fixed | fixed by review | Header comments are accepted and kept. Quoted, flow-style, duplicated, merged or tab-indented sections are refused with the line number and nothing is written. Every write is verified before the replace. Zero refusals on real configs. |
| E18 | partly fixed | fixed by review | A user message is stored with `awaiting_reply`, cleared when the reply is saved. A message still carrying it from before a restart gets a notice; reads never write. Apply refuses during a turn unless confirmed. **Remaining:** the lost reply is not fetched back from serve. |
| E19 | fixed | good | A drill no longer resumes a paused agent or reverts the owner's switch; the outcome is recorded and shown. |
| E20 | fixed | good | The dashboard's opener sends loopback addresses direct and never attaches the model key to anything else. Doctor's probes use a proxy-free opener. |
| E21 | fixed | good | Resume, a model switch, the Thinking restart and Free memory count as owner activity. |
| E22 | fixed | fixed by review | A failed download shows as failed with Retry and the poll stops. Setup activates a pick only when it is complete on disk; a watcher finishes a pending pick while the server is down and the active model is unchanged. |
| E23 | fixed | good | Continue stays off on the welcome and prefs steps until a real state has loaded; Retry re-reads it. |
| E24 | fixed | good | The fact check catches Stripe, Hugging Face, npm, GitLab and Google OAuth tokens and URL passwords, and runs the prose rule. One false positive in 80 realistic facts, and that one is a real URL password. |
| E25 | fixed | fixed by review | A 17th row covers the agent's own terminal, browser and web-extract traffic; the headline claims only what the table covers. |
| E26 | not done | — | Swift. First item for the next batched rebuild. |
| E27 | fixed | fixed by review | Any negation in the part being kept leaves the sentence as spoken. |
| E28 | fixed | fixed by review | Both switches ignore key repeats and input while a flip is in flight; after a failed request the Settings switch re-reads the server. |
| E29 | fixed | fixed by review | After the code swap `update.sh` hands over to the incoming version's post-update step, which must pass a syntax check first. The new `install-services.sh` runs that step on the first hop from 1.3.2. |
| E30 | fixed | good | The expected-skips list matches what the suites print. Reproduced green under CI's environment three times. |
| E31 | fixed | good | Both missing tests exist; the mutants die. |
| E32 | fixed | fixed by review | Only a well-formed block whose markers are whole lines is replaced; otherwise a block is appended. Nothing is written unless the owner's text is byte-identical. |
| E33 | fixed | fixed by review | An open pane survives the poll; the diff cache holds only what the pane shows and is keyed on the file's size and time. |
| E49 | not done | — | Owner step: copy the skill (deploy checklist step 6). |
| E50 | fixed | good | Three account-level probes; the headline says what the wall observes. |
| E51 | fixed | good | Holds record their cause; one that a GET cannot test is cleared only by a reconnect or a passing add. |
| E52 | fixed | good | A failed check is retried after 10, 20, 40 minutes, capped at six hours. |
| E53 | fixed | good | Only active rows count toward the limit of ten. |
| E54 | fixed | good | One drift notice per repository per check, sent after the lock is released. A summary can repeat if a check overlaps a slow send. |
| E55 | fixed | good | Decide re-reads the repository's visibility. |
| E56 | fixed | good | Transports are pinned; a mirror config that changes during a git call fails the call and holds GitHub writes until a reconnect. |
| E57 | partly fixed | good | An interrupted push is reconciled with GitHub's head on the next request, decide, retract or periodic check. **Remaining:** a 404 is read as "branch absent", so lost repository access records "nothing pushed". |
| E58 | fixed | good | Retract marks earlier changes on the same branch retracted. |
| E59 | fixed | fixed by review | PR text keeps indentation; a fence inside a list item cannot leave a live mention. |
| E60 | partly fixed | fixed by review | A URL exempts a mention only where GitHub makes it a link. **Remaining:** reference definitions inside a quote or list are not stripped. |
| E61 | fixed | good | A bare CR is marked; file names come from the file list. |
| E62 | fixed | good | The result says "pull request" when it is not a draft. |
| E63 | fixed | good | The toast is inert the moment its text changes. |
| E64 | fixed | fixed by review | The sheet's byte count applies the server's cleaning; the server refuses an edited body it would cut. |
| E65 | fixed | good | Eleven skill sentences corrected and re-checked against the merged code; a test reads each fact from the code. |
| E66 | fixed | good | Only repeats with an unchanged successful answer count. Two cases fall back to the old count. |
| E67 | fixed | good | Only the documented approving answers approve. |
| E68 | fixed | good | More shell-startup files and folders are in the protected class. |
| E69 | fixed | fixed by review | Hidden and in-flight session checks, the delete tombstone, the index and MCP `chat_get` ignore letter case. |
| E70 | fixed | good | A conversation with no stored session is seeded from its transcript. |
| E71 | fixed | good | A late Approve leaves a note in the reply. |
| E72 | fixed | good | Quick Ask says when a reply was not saved and hands back the typed text. |
| E73 | fixed | good | The alert id is a hash of the row's signature. |
| E74 | fixed | good | Promotion needs a deadline in the documented window and a known sender. |
| E75 | fixed | good | Runs of three or more angle brackets are split in one pass, in all four copies and the clipboard path. |
| E76 | fixed | good | "Later today" uses the 12-hour-first clock parsing. |
| E77 | fixed | good | Not gated, by owner decision; the exception is named in code and stated in the table and CLAUDE.md. |
| E78 | fixed | good | No "next brief" when Briefings are off or the config is corrupt. |
| E79 | fixed | good | A reload after an act does not record sightings. |
| E80 | fixed | good | `claude_think` refuses a credential in the task or context before spawning anything; the log summary is redacted. |
| E81 | fixed | fixed by review | Over-long and too deeply nested lines get an error answer; the server keeps running. |
| E82 | fixed | good | The shapes are in all three copies of the block. |
| E83 | fixed | good | Text and structured output share one 8 KB bound and list the same items. |
| E84 | fixed | good | The alerts row names the five-minute price checks. |
| E85 | fixed | good | Every update check attempt is recorded and the card reads the newest. |
| E86 | fixed | good | A refused headline is dropped; the rest still go. |
| E87 | fixed | fixed by review | Only Google's hosts are accepted, including the older token address. |
| E88 | fixed | good | A group that is open stays open for the page life. |
| E89 | fixed | good | The sheet is a dialog in the Esc and focus registry. |
| E90 | fixed | good | Highlights are sliced by code point. |
| E91 | fixed | good | An unknown stored view falls back to the hub. |
| E92 | fixed | good | Three polls skip while hidden. |
| E93 | fixed | fixed by review | The chips are buttons, the same size as before. |
| E94 | fixed | good | A blank hardware fact is retried on Re-check and every ten minutes. |
| E95 | fixed | good | The lane is counted only on file evidence that it can run. A stale token still counts. |
| E96 | fixed | good | A raising provider is backed off. |
| E97 | fixed | good | Unchanged builds write nothing; the loop runs only while something read it in the last three minutes. MCP `needs_you` prints the inbox's age past five minutes. |
| E98 | fixed | good | Idle-suspend's shutdown runs under the lifecycle lock, and a chat arriving during it waits and then wakes the model. |
| E100 | fixed | fixed by review | The launcher refuses any media that is not inline data, for every body encoding the routes accept. |
| E101 | fixed | good | The restart paths run under the lifecycle lock; Free memory refuses while paused. |
| E102 | fixed | good | `mlx-server.sh` exits while the pause marker exists. |
| E103 | fixed | fixed by review | A restore point is written before a drill's swap and honoured at the next start; an owner switch removes it. |
| E104 | fixed | good | Ids are matched case-insensitively; a case variant of a curated id is refused. |
| E105 | fixed | good | The server and the menu subtract downloads in flight. |
| E106 | fixed | good | An overflowing value is dropped, in both validators and the import fallback. |
| E107 | fixed | good | Fillers never match capitals; verbatim keeps them. "Say ah" still loses the word in other styles. |
| E108 | not done | — | Swift. Next batched rebuild. |
| E109 | not done | — | Swift. Next batched rebuild. |
| E110 | fixed | good | Focus returns to the control that was used. |
| E111 | fixed | good | The lane is repointed only when its model is complete or really downloading. |
| E112 | fixed | good | An imported policy is merged, shown as from→to rows and lands untrusted. |
| E113 | fixed | good | Every roster write goes through `models_update()`. |
| E114 | fixed | good | The index is read through the quarantining helper. |
| E115 | fixed | good | Re-trust is bound to the hash of the file the review showed. |
| E116 | fixed | good | Exports and backups are 0600. |
| E117 | fixed | good | Evals and the drill refuse while the other runs. |
| E118 | fixed | good | Frames are pruned at start and refused past the limit. |
| E119 | fixed | fixed by review | The page reports an aborted drill or a failed or skipped restore, also after a restart. |
| E120 | fixed | fixed by review | Clipboard text goes inside the data fence and cannot close it. |
| E121 | fixed | good | Only JSON booleans are accepted. This closes C59. |
| E122 | fixed | good | The request line and headers share a 20-second budget. |
| E123 | fixed | fixed by review | Ten routes answer 400 on wrong types before any side effect; every returned 5xx is logged, escaped. |
| E124 | fixed | good | Aux failures name their file. |
| E125 | fixed | good | Configured host names follow the port-less rule; https only as the request's own Host. |
| E126 | fixed | good | The app build removes only its own bundle; doctor warns when the helper is missing. |
| E127 | fixed | good | Doctor compares against `VERSION`. |
| E128 | fixed | good | Wording only: detects corruption, not tampering. |
| E129 | fixed | fixed by review | A lane survives an update only if its service file and scripts are unchanged. |
| E130 | fixed | good | No update is offered when origin is already an ancestor. |
| E131 | fixed | good | The spring's leftovers are cleared on its own end event. |
| E132 | fixed | good | Sidebar rows animate once per page life. |
| E133 | skipped | — | Hardening with no current caller. |
| E134 | fixed | good | A test alters each field and expects the decision to be refused. |
| E135 | fixed | good | One assertion per surviving branch. |
| E136 | fixed | good | The pin runs against a fake server module in the unit tier. |
| E137 | fixed | good | A per-suite timeout, reported as a failure; job timeouts in CI. |
| E138 | fixed | fixed by review | Every suite uses the real settings helper and the real `esc()`. No unit suite reads the owner's data; the tier's guard refuses the installed `shortcuts`, `osascript` and `icalBuddy`. |
| E139–E144 | fixed | — | README, RUNBOOK, SECURITY and CHANGELOG corrected. |
| E145–E154 | fixed | — | CLAUDE.md and the tests README corrected. The doctor count is now 23. |

### Found during the fix round and not repaired

- 15 shipped source lines contain a literal U+FEFF, U+2028, U+2029 or
  no-break space.
- A labelled value that starts with a plain word (`password: correct horse`)
  is redacted in exports but passes the memory and GitHub checks.
- A failed pending download is polled every 30 seconds indefinitely.
- The four copies of the fence-splitting rule have no drift test.
- `t_gh_review.py` has never run: Python Playwright is not installed.
- Older behaviour the reviewers noted, not from this audit: Undo of a
  whole-folder undo row always refuses; snapshot matching can pick another
  turn's snapshot; Undo of a restore has no conflict check; the `config.yaml`
  editors replace a symlinked file with a regular one.
