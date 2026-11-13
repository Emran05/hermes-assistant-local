# Deploying `audit-2026-10-21` — owner checklist

The branch fixes the 2026-10-21 audit (C-series) and the still-open
2026-10-08 audit (B-series), and adds research items F01, F03, F07 and F21.
The implementation status is the last section of
`docs/audits/2026-10-21-codebase-audit.md`. Round 3 (features: the motion
runtime, the model advisor, GitHub sandboxed changes, five opt-in models) has
its own section, §6.

Nothing in this checklist was run by the agents. None of them touched
`~/.hermes`, launchd, a model or the network, and none of them pushed. Steps
are in order. **Bold** steps are needed for a fix to take effect; the rest
are optional decisions.

## 0. Before merging

1. **Restore the file the audit's repro overwrote in the main checkout:**
   `git -C ~/HermesAssistant checkout -- docs/state-snapshot.json`. From this
   branch on, Export writes `~/.hermes/dashboard/state-snapshot.json`, never
   the install tree (C64).
2. Review the branch. `git log --oneline main..audit-2026-10-21` shows one
   merge commit per cluster, and each cluster's commits are named
   `<ID>: <what>`.
   - The worktrees live under `~/HermesAssistant-worktrees/`. Remove them
     after merging with `git worktree remove <path>`.
   - Your uncommitted 2026-10-08 UI work in the main checkout is the
     branch's first commit (`a8667b2`), byte-identical. Once you merge,
     discard the main checkout's uncommitted copy.
3. **Until you merge, stay on one model on `main`.** Switching to the
   uncensored 27B on 2026-10-27 crashed the model server twice. `main` lets
   the server load whatever model a request names, and Hermes keeps each
   conversation's model, so an older conversation (still on the stock 27B)
   and a new session (on the new model) made the server swap models in
   place, which segfaults in MLX 0.32.1 (see the crash note in CLAUDE.md).
   The branch fixes both halves: the server is pinned to its launch model
   (C130) and an idle conversation is moved to the selected model on its
   next turn. Until then, either stay on the stock Qwen3.8-27B, or use the
   new model only in a NEW conversation.
4. If the model will not come back after a crash on `main` ("launchctl would
   not start the model server" in the dashboard log), the crashed job is
   still loaded; unload it, then press Wake now (fixed on the branch,
   `607bdff`):
   ```bash
   launchctl bootout gui/$(id -u)/com.hermes.mlx-server
   ```

## 1. Right after merging (code that runs in long-lived processes)

3. **Restart the dashboard and reload the window:**
   `launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard`, then press ⌘R
   in the app and reopen Quick Ask.
4. **Re-render the services: `./install-services.sh`** (or let the next
   `update.sh` do it). That changes `com.hermes.serve` in three ways:
   - It runs without Hermes' web UI (C117). The UI's HTML handed the session
     token to any loopback page. The new launch uses `HERMES_WEB_DIST` at an
     impossible path and drops `--skip-build`. The hub only uses `/api/ws`,
     so nothing in the dashboard changes. `http://127.0.0.1:9119` stops
     serving Hermes' own UI.
   - It sets `HERMES_TUI_CHECKPOINTS=1` (C114), so the Flight Recorder can
     undo dashboard-chat writes. The snapshots go to
     `~/.hermes/checkpoints/store`. Telegram turns still cannot be undone;
     that is upstream, in `gateway/run.py`.
   - The plist is written 0600 from its first byte.
5. **Enable the approval gate (C111/C116).** Without it, `computer_use`
   keystrokes and writes to rc files or LaunchAgents still run unapproved
   from the dashboard, Telegram and `-z`:
   ```bash
   ln -sfn ~/HermesAssistant/hermes-plugins/approval-gate ~/.hermes/plugins/approval-gate
   python3 ~/HermesAssistant/hermes-plugins/plugin_enable.py approval-gate
   cp ~/HermesAssistant/skills-snapshot/_plugins/loop-breaker/{__init__.py,plugin.yaml} ~/.hermes/plugins/loop-breaker/
   launchctl kickstart -k gui/$(id -u)/com.hermes.serve
   launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
   ```
   The `cp` deploys the loop-breaker fix (C134/C135); the live copy is a
   plain copy, not a symlink. `update.sh` now links and enables
   `approval-gate` itself. Doctor's new `hermes_drift` check reports whether
   the gate is active.
6. Copy the corrected skills, because nothing syncs `skills-snapshot/` to the
   live skills:
   - `apple/mac-control/SKILL.md`
   - `apple/screen-oracle/scripts/grab.sh`
   - `autonomous-ai-agents/{cron-conductor,hub-cartographer,mirror-check,skill-forge}/SKILL.md`

   Each goes to the same path under `~/.hermes/skills/`.
   `skills-snapshot/hermes-search` has never been installed live; copy it if
   you want the agent to have it.
7. Restart any open Claude Code session so the MCP server relaunches with
   the new code (B06, B21, F21).

## 2. Behaviour you will notice

- **Chat with serve down** now refuses honestly instead of running a
  full-toolset `hermes -z` (C36). `hermes -z` auto-approves everything.
  Every unattended run now carries an explicit reduced `--toolsets`.
- **The model servers answer only for their launch model** (C130). They have
  no CORS and no `--trust-remote-code` unless a roster entry opts in. They
  run offline when the snapshot is complete (C49).
  - Look for `[mlx-vlm-launch] pinned to …; CORS off` in
    `~/.hermes/logs/mlx-server.log` on the next wake.
  - A model id that drifts (for example a hand-edited `model.default`) now
    gets a 400 instead of a silent hot-swap. Switch models through the menu.
- **Watchtower.**
  - The hourly agent web-research pass is now **off**; it had been running
    (C50). Turn it on in Mind › Watchtower if you want it.
  - With "News updates" off, no feed is fetched (C48) and no watch rule
    fires (C54).
  - The CNN world feed is gone: it has no https and has not published since
    2023.
- **For-You's Claude picks have their own switch**, default off, in
  Settings › Connections › Data & Network (C35). Escalation must be on as
  well.
- **The Google scope is now `calendar.readonly`** (C51). Google is not
  connected on this Mac. An older, wider grant would show "Connected · wider
  than read-only"; reconnect to narrow it.
- **Needs-you** says "Messages is not being read" while Full Disk Access is
  missing (C18). Re-grant Full Disk Access to the app to fix that.
- **The Scratchpad** refuses a stale or over-long save instead of truncating
  it (C19/B05).
- **Quick Links** with a non-http(s) scheme are hidden from the hub card
  (B18). They stay in the editor.
- **The Claude Code Usage widget** drops by roughly half. That is the
  corrected dedupe, not a regression; re-check any soft cap.
- **Agent › Screen** shows a live "Give the agent a Mac task" box again
  (B26).

## 3. Config decisions the new doctor check raises (nothing is auto-edited)

- **Text-to-speech** defaults to Microsoft Edge's online voices. Either run
  `hermes config set tts.provider piper` (local; needs `piper-tts` in the
  agent venv) or `hermes tools disable tts --platform telegram`.
- **Model-server API key** (C130, optional, defence in depth on top of the
  pin):
  1. Write a random key to `~/.hermes/dashboard/model-api-key` (mode 0600).
  2. Put the same value as `model.api_key` and as the `custom_providers: bg`
     `api_key`, replacing `local-anything`.
  3. Restart serve, the gateway and the model lanes.
- **Other approvals.** Set `memory.write_approval: true` and
  `skills.write_approval: true`; the approval gate covers
  `write_file`/`patch`, not the memory or skill tools (C40/C116).
- **Too many approval cards.** Add specific keys such as
  `computer_use:scroll` to `command_allowlist`. Note that this also allows
  them unattended.
- Optionally tighten old stores now rather than on their next write:
  `chmod 600 ~/.hermes/dashboard/*.json ~/.hermes/dashboard/chats/*.json`
  (C110).
- Delete `~/.hermes/dashboard/chats/needsyou.json`, which holds old draft
  bodies. Every route already hides it (C38).

## 4. On AC power, when convenient (loads a model)

- Run one load drill of each lane **without `--trust-remote-code`** (F01).
  The offline evidence is that none of the four cached snapshots has
  `auto_map` or `*.py`.
  - The 27B: wake it and run the model-menu Drill.
  - The 9B: `touch ~/.hermes/dashboard/model-start-ok-bg`, kickstart
    `com.hermes.mlx-bg`, then send one request.
- The first real dangerous-command approval in the hub: check the card
  appears once, Approve runs that command, and the card does not come back
  (B01).
- Not built because they need a model: F02 (primary-lane admission guard),
  F12 (mlx canary matrix) and F13 (temperature probe). See
  `docs/plans/feature-research-2026-10-21.md`.

## 5. Rebuilds (each resets macOS grants)

- **Dictation helper:** `bash app/build-dictation.sh`, then
  `open "app/build/Hermes Dictation.app"` and re-grant Microphone and
  Accessibility. That activates C122, C123, C125 and C56, which were only
  typechecked here. Then smoke-test:
  - A quick tap inserts nothing.
  - Releasing during engine start leaves no "Listening…" state.
  - Text typed after an emoji is not corrupted.
- **Main app (frozen; batch these into one deliberate rebuild, then
  re-grant Full Disk Access):**
  - The Swift half of B18: open only http/https from `createWebViewWith`,
    and use an explicit scheme allowlist in `decidePolicyFor`.
  - C89: MessagesSync copies the 201 MB `chat.db` every 60 s.
  - F05, the per-launch approval nonce that closes B02 and C131. Until then,
    any same-uid process and any OrbStack/Docker container can use the whole
    dashboard API.

## 6. Round 3: motion, model advisor, GitHub sandbox, five opt-in models

Round 3 adds features, not audit fixes. The same rule held: no agent sent a
GitHub request, loaded a model, opened the app or touched `~/.hermes` or
launchd. Steps continue the numbering of §0–§1.

8. **Restart the dashboard and reload the window** (the step 3 command, then
   ⌘R). That loads the motion runtime and the `/vendor/` routes, the model
   advisor, the GitHub connector, change pipeline and screens, the five new roster
   seeds, the Needs-you GitHub source and the agent's GitHub `[context]` line.
   Check the vendored files with GET, not `curl -I` (the server has no HEAD
   handler and answers 501):
   ```bash
   curl -s -o /dev/null -D - http://127.0.0.1:7788/vendor/gsap/gsap-3.15.0.min.js
   #   expect Cache-Control: public, max-age=31536000, immutable, and an ETag
   curl -s http://127.0.0.1:7788/motion.min.js | shasum -a 256
   #   expect 5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf
   ```
   Quick Ask may keep its cached Motion 11 copy for up to a day (the old
   `max-age=86400`). That is harmless: the calls are the same.
9. Services: nothing to re-render for round 3. `install-services.sh` and
   `update.sh` did not change in it. The model launch scripts did
   (`mlx-server.sh`, `mlx-server-bg.sh`, `mlx-vlm-launch.py`: `model_subfolder`
   and `sampling`), but launchd runs them from the checkout, so the next wake or
   switch uses them. Step 4 still applies if you have not done it.
10. **Restart serve and the Telegram gateway** so they load the approval-gate
    additions (the GitHub tripwires, the `secret` read block, the `sandbox-git`
    class):
    ```bash
    launchctl kickstart -k gui/$(id -u)/com.hermes.serve
    launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
    ```
    `~/.hermes/plugins/approval-gate` is a symlink to the checkout's
    `hermes-plugins/approval-gate` (step 5, or install.sh/update.sh), so the
    merged code is already in place; a running serve or gateway only reads it
    at start. `ls -l ~/.hermes/plugins/approval-gate` shows the link.
11. **Before you connect GitHub, copy the `github-sandbox` skill.** Nothing
    installs it: install.sh links only plugins, and `.gitattributes` marks
    `skills-snapshot/` export-ignore, so a release tarball lacks it too.
    ```bash
    mkdir -p ~/.hermes/skills/autonomous-ai-agents/github-sandbox
    cp -R ~/HermesAssistant/skills-snapshot/autonomous-ai-agents/github-sandbox/. \
      ~/.hermes/skills/autonomous-ai-agents/github-sandbox/
    ```
    Without it, once a repository is connected and a sandbox prepared, the
    `[context]` line points the agent at a skill it cannot load.

### Behaviour you will notice

- **Motion.** One runtime drives the hub, pop-outs, the model menu, the
  model-swap overlay, Needs-you, the Agent page, Settings and first-run setup.
  Either Reduce motion switch (Preferences or macOS) lands everything at once,
  and nothing moves on a background refresh. Customize no longer rebuilds the
  grid.
- **The header logo tile is gone** (your request). The header's own left
  padding now keeps the greeting clear of the window buttons.
- **Settings › Agent & Models** opens with the Model advisor card. Its figures
  are labelled estimates. The 2B, 4B, 35B-A3B and uncensored-27B speeds stay
  assumptions (±25–40%) until the calibration below.
- **The model menu has five new rows**, none downloaded: Qwen3.6-35B-A3B,
  Qwen3.8-27B Abliterated (PocketAI), Qwen3.8-27B Abliterated (Huihui,
  text-only), Qwen3.6-35B-A3B Heretic and Qwen3.5-9B Abliterated. Seeds cannot
  be removed from the menu.
- **Uncensored rows** carry a badge and the line "no refusals — the approval
  gate is the only brake". Switching to one asks for the acknowledgement once.
  First-run setup refuses an uncensored pick without it as well. To store it
  for every later uncensored switch, tick it in the advisor card, or:
  ```bash
  curl -s -X POST localhost:7788/api/advisor/answers -H 'Content-Type: application/json' -d '{"uncensored_ack": true}'
  ```
  `{"uncensored_ack": false}` withdraws it.
- **An 8 GB Mac's memory ceiling** is now (6, 8) GB instead of (8, 10) (D9).
  Nothing changes from 10 GB up, so not on this Mac.
- **Settings › Connections** has a GitHub card, **Trust** lists a 19th class,
  "GitHub pushes & pull requests" (Ask; never Auto), and **Data & Network** has
  two GitHub rows. None of them does anything until you connect GitHub: no
  request goes to GitHub before you press Check token. First-run setup (on a
  re-run) has an optional GitHub step.
- **GitHub mirrors are never garbage-collected** (`gc.auto=0`). If
  `~/.hermes/dashboard/github/mirrors/<owner>__<repo>.git` grows large, delete
  that folder; the next prepare recreates it (a change still pending on it then
  fails cleanly). The agent's sandbox, `~/HermesSandbox/github/`, is separate
  and never touched.
- Settings Overview prints its growth numbers with thousands separators, and
  the Memory and You-Model meter fills end square.

### GitHub acceptance against real GitHub (optional, a throwaway private repository)

Nothing here has talked to GitHub yet. Run it once, in the app, before relying
on it. Everything goes through Settings › Connections › GitHub; curl appears
only where the app has no button.

1. Create a throwaway private repository. On the GitHub card, enter
   `<you>/<repo>` under "Which repositories?" and press **Open GitHub to create
   the token**. GitHub's prefilled fine-grained token form must open in your
   **default browser**, and the app window must not navigate. A very fast
   first press can need a second one; the card says so. On GitHub choose
   "Only select repositories", pick the repository, press "Generate token" and
   copy it. Only fine-grained `github_pat_` tokens are accepted.
2. Paste it into Token and press **Check token**. A refusal stores nothing and
   lists its reasons, and the field is empty again after every attempt. Check
   that `GET /api/github/status` never contains the token and that
   `~/.hermes/dashboard/github/credential.json` is 0600 in a 0700 folder.
3. Enable the repository on the card. Copy the recommended rule (the card's
   **Copy**) and import it on GitHub (Settings › Rules › Rulesets › New
   ruleset › Import a ruleset). Then press **Check now** and see the
   repository's protection chip change.
4. Check three GitHub behaviours the scope check assumes:
   - the card shows the token's expiry. GitHub sends
     `GitHub-Authentication-Token-Expiration` on `/user`; without it you get a
     "never expires" warning, not a refusal;
   - Check token did not refuse falsely. A refusal would mean
     `GET /user/repos?visibility=private` listed repositories outside the
     token's selection. That is safe, but needs fixing;
   - Revoke now (step 9 of this list) shows its result as a success (not a
     warning), so `POST /credentials/revoke` accepted the key without other
     authentication.
5. Round trip: in a hub chat, ask the agent to change one line in that
   repository. It should prepare a sandbox, request the change and stop. Then
   a toast says "Hermes has a GitHub change for you" (within about a minute,
   while the window is visible), Needs-you shows "Hermes has a GitHub change
   for you: …", and Open lands on the review sheet. Also ask the agent to run
   `gh auth token` (refused, key `github:gh-token`) and to read
   `~/.hermes/dashboard/github/credential.json` with `read_file` (refused as
   class `secret`).
6. **In the review sheet, tick every warning and press "Push and open draft
   PR".** This is the WKWebView check the speed bump depends on. The fetch POST
   must carry `Origin: http://127.0.0.1:7788` and `Sec-Fetch-Site:
   same-origin`. If either is missing, the sheet shows the owner_only
   explanation ("Only you can do this, from the Hermes window itself…") and
   nothing is pushed. Reject still works, and the log shows nothing, because a
   403 is a normal answer. Then check:
   - only `hermes/<slug>` appears on GitHub, and the pull request is a draft;
   - @mentions are neutralised and the footer is there (it names this Mac's
     host name);
   - Telegram reports the push;
   - "Open the pull request" opens github.com in your default browser, and
     the app window does not navigate.

   Where a plan has no draft pull requests, GitHub should answer 422 with a
   message containing "draft", and Hermes retries once as a ready pull request
   titled `[Hermes] …`. If the message differs, the push ends with no PR.
7. **Retract** it from the same sheet (it asks first). The branch must be
   deleted (a `--force-with-lease` deletion over HTTPS with the fine-grained
   token) and the pull request closed. Like Approve, Retract answers
   owner_only if the window sends the wrong headers.
8. Drift: push to a `hermes/` branch by hand, outside Hermes, then press
   **Check now** (or wait up to 6 hours). Exactly one Telegram drift notice
   should arrive, and the card shows the alert. Clear it with **That was me**.
9. Press **Revoke now** (it asks first). Then GitHub's `/user` must answer 401
   to the old token. The app has no button for this check:
   `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <old token>" https://api.github.com/user`.
10. Keychain: with the dashboard running under launchd, press **Check this
    Mac** (or **Check again**) under "Other GitHub access on this Mac". It runs
    `security find-internet-password -s github.com` without `-g`/`-w`; confirm
    that no Keychain prompt appears. Then review what it lists (gh login, git
    credential helpers, SSH keys, token variables) and run the commands it
    shows yourself, or accept that access knowingly. Hermes changes nothing.

### AC drills for the five new models (on AC power; loads a model)

None of them has run on this Mac. Before trusting one:

1. Download it from the model menu (6–25 GB; the disk check quotes each size).
2. Run the 6-case tool drill (the model menu's Drill, or
   `POST /api/models/drill`). A pass of 5 of 6 or better is what the advisor
   counts as verified; a fail removes the verification.
3. Run the two-stream concurrency probe on mlx-vlm 0.6.14 and require 6/6 clean
   rounds: `tools/bench/concurrency_probe.py --model <id> --block none --rounds 6`.
   None of the five ships an MTP drafter, so there is no acceptance rate to
   measure unless you add one.
4. For the four uncensored builds, also run the benign over-refusal check and
   the check that refusals do not return under Hermes' long system prompt
   (`docs/plans/abliterated-models-2026-10.md`).
5. On a pass, set `"verified": true` on that row in
   `~/.hermes/dashboard/models.json`. That only clears the "unverified until
   drilled" badge; the advisor never reads the field, because a config import
   can set it.

Notes:
- AutisticAF's build is text-only. Do not make it the assistant if you rely
  on computer-use screenshot turns.
- To try the vanch007 9B as the background lane, write its id to
  `~/.hermes/dashboard/bg-model` while the lane is down.
- Once the stock Qwen3.6-35B-A3B passes a local drill, the advisor may
  recommend 35B-A3B + 9B on this Mac instead of 27B + 9B.
- Optional during a drill: try a sampling default on one row, for example
  `"sampling": {"repetition_penalty": 1.15}`. It takes effect on the next
  switch or restart; deleting the key brings back the seed's value (none).
- Advisor calibration (D2): follow `tools/tests/ac/advisor-calibration.md` for
  the Qwen3.5-2B, the Qwen3.5-4B and the Qwen3.6-35B-A3B. Copy the fitted
  numbers into `_ADV_FACTS`, run
  `python3 tools/tests/unit/t_advisor_estimate.py --write-golden` and review the
  diff.

### Manual pass in the app (WKWebView)

Only headless Chromium ran any of this. After step 8:

1. **Customize** with 15 or more widgets: remove one, add one from the Widget
   Center, drag a card across the grid and press Alt+Arrow on a title. The
   cards slide, none is left tilted when you leave Customize, the press "gel"
   still works on every card and the remove buttons do not blink. Watch the
   frame rate of a full-hub slide over the glass cards. The optional
   solid-glass-during-flight mode (`.anim-lite`) is not built; ask for it only
   if this stutters.
2. **Switch models** once: a check mark draws in with "Ready", and the overlay
   goes after about 1.3 s. Trigger one failure (for example during a dashboard
   restart): a short shake, then gone after about 2.6 s. Minimise the window
   during a switch: the overlay is gone when you come back.
3. **Settings › Overview**: on the first open the growth numbers count up and
   the activity line draws in; reopening with the same numbers moves nothing.
   **Settings › Insights › Vitals**: the numbers count once on first view and
   on the 24h/7d switch, and nothing pulses every 15 s.
4. **Needs-you**: Done on a Now row slides it right and fades it, the rows
   below glide up and the count ticks down (for example "3 now" to "2 now"),
   with no scrollbar flash. Snooze and a re-file lift the row away instead.
5. **Agent page**: the heartbeat ticker fades out and back every 8 s and is
   never blank; a tool event ticks the sigil ring, which stays centred; a
   show-in card rises and its icon turns once; the EKG dot blips on a new
   recorder event; with the Record rail open, its 3 s repaint does not re-fade
   rows; an Undo flashes its row iris for about 0.15 s.
6. **Claude Usage pop-out**: the 5-hour, today and 7-day totals count up.
7. **Model advisor**: Settings › Agent & Models shows the card first, with
   "Last analysed" in 12-hour time. Tick "Unfiltered research (no refusals)":
   the acknowledgement copy appears, and the use turns on only after its
   checkbox is ticked; untick to withdraw. Click Download, then decline the
   confirm: nothing starts. In the model menu, click a downloaded uncensored
   row: one confirm names the switch and quotes the acknowledgement. Cancel it.
8. **Quick Ask** opens and fades in.
9. Repeat a remove, an add, a model switch, a Needs-you act and an advisor
   answer change with Reduce motion on in Preferences, then again with the
   macOS setting. Everything lands at once with no flash, and the swap's
   reading time still applies.

Optional: the day rows in the weather and calendar pop-outs now collapse
visibly, in about 0.24 s, before they hide. JavaScriptCore frame budgets stay
unmeasured. `playwright install webkit` (a download) would let a later WebKit
probe measure them.

### Tests on this Mac

- Python Playwright is not installed. So `tools/tests/run.sh browser` skips the
  whole tier (it also needs the dashboard to answer), and
  `tools/tests/browser/t_onboarding_ui.py` and `t_gh_review.py` (the GitHub
  review sheet, which has never run anywhere) skip when run by hand. To enable
  them (a download), use the venv the runner suggests:
  ```bash
  python3 -m venv /tmp/pwenv && /tmp/pwenv/bin/pip install playwright \
    && /tmp/pwenv/bin/playwright install chromium
  PWENV=/tmp/pwenv tools/tests/run.sh browser                    # the dashboard must be up
  /tmp/pwenv/bin/python tools/tests/browser/t_onboarding_ui.py   # hermetic, no dashboard
  /tmp/pwenv/bin/python tools/tests/browser/t_gh_review.py       # hermetic; shots in $HERMES_TEST_SHOTS
  ```
- The real-library suites need no Playwright and use the Chromium already in
  `~/Library/Caches/ms-playwright`: `node tools/tests/browser/t_anim_real.js`,
  `t_anim_hub_real.js`, `t_anim_surfaces_real.js` and `t_onboarding_real.js`.
  `tools/tests/browser/t_advisor_ui.py` falls back to a stdlib DevTools driver
  when Playwright is missing.

### GSAP licence: re-read, done

The live page (`https://gsap.com/standard-license`) was re-read on 2026-10-26.
It is unchanged: effective 2025-04-30, last modified 2025-05-30. It is free,
commercial use included, for all of GSAP including the plugins (Flip;
CustomEase is not named, but "all of GSAP including the plugins" covers it).
It prohibits no-code visual animation builders that compete with Webflow, and
removing or altering GSAP's proprietary notices, so the `@license` banners
stay. It says nothing more about redistribution or notice files.
`dashboard/vendor/gsap/LICENSE.md` already says exactly this, so nothing needs
updating. This is an engineering reading, not legal advice.

## 7. Owner decisions

- The public remote `Emran05/hermes-assistant-local` already carries the
  full private history, including six commits with a home path (B30).
  `tools/release.sh` now refuses to publish such commits again. Whether to
  rewrite that published history is yours to decide.
- **Releasing has changed:** stage what ships yourself (the script never
  runs `git add`), then run `tools/release.sh <ver> "<title>"`.
  - It runs the strict unit tier first.
  - It lists the commits public would receive and asks you to confirm their
    count.
  - `release.yml` now gates on CI.
  - Still to do: pin `softprops/action-gh-release@v2` to a commit SHA.
- Whether deleting a conversation should also delete Hermes' own copy in
  `~/.hermes/state.db`. `session_search` can still find it (C58 is a partial
  fix).

### Round 3

- **D9, the memory-ceiling floor** (`_mem_ceilings`): applied, so 8 GB gives
  (6, 8) and nothing changes from 10 GB up. Keep it, or revert `cef8ea9` and
  `8834aa1` together, along with the CLAUDE.md sentence.
- **D10, CLAUDE.md figures**: CLAUDE.md now says the 9B lane's ~88 tok/s is its
  short-prompt speed (32–42 tok/s at the 24–40k contexts it runs), that it is
  an estimated ~9.3–9.6 GiB resident rather than ~7 GB, and that 63 tok/s is
  block 4 (block 3, which ships, is 58.7). Re-measure the lane's footprint to
  replace the estimate.
- **D14, the advisor's judgement constants**: review the quality ordinals in
  `_ADV_FACTS` (`dashboard/aux_advisor_hw.py`) and the ranking goldens
  (`tools/tests/unit/t_advisor_rank.py`, 11 machines × 6 profiles). Two cells
  are borderline:
  - on the M5 Max, "Faster" recommends 27B + 9B over 9B + 4B by about 0.01
    score;
  - on the M4 Pro 48 GB, the 27B's 181 s cold read sits against the 180 s cut,
    so it is excluded at S = 18,500 and kept at S = 18,000.

  Regenerate deliberately with
  `python3 tools/tests/unit/t_advisor_rank.py --print-golden`.
- **The vanch007 Qwen3.5-9B abliterated build**: its repository states no
  licence, and the converter is anonymous. Keep it, or ask for a roster flag
  that hides a seed (seeds cannot be removed). The same flag would take the
  uncensored builds out of the menu if you want that.
- **D4(b), the uncensored MoE**: the curated one is Youssofal's Heretic
  (24.6 GB). froggeric's 20.4 GB build has advisor facts but is not curated.
- **The pull request footer** names this Mac's host name ("approved it on
  <host>"). On a public repository that is public. Keep it, or change
  `GHC_PR_FOOTER` in `dashboard/aux_ghchanges.py`.
- **Shipping the `github-sandbox` skill**: a release install gets the
  approval-gate additions and the `[context]` line, but not the skill (step
  11). Either move the skill to a shipped folder and install it, or make the
  `[context]` line name the routes.
- **The stored uncensored acknowledgement** is global and one-time, and any
  process running as you can POST it, the agent's terminal included. That is
  the existing threat model, with the approval gate as the only brake.
- **F05 is still the real fix for B02.** Until the app-held nonce ships (§5),
  the GitHub approve and retract speed bump, and the connector's owner-only
  routes (connect, add a repository, check, drift ack, widening settings,
  unpausing), can be reached by any process running as you.
