# Deploying `audit-2026-11-09` — owner checklist

The branch fixes the 2026-11-09 audit (E-series). The per-finding status is
the last section of `docs/audits/2026-11-09-codebase-audit.md`.

Nothing in this checklist was run by the agents. None of them touched
`~/.hermes`, launchd, a model, GitHub or the network, and none of them
pushed. The main checkout at `~/HermesAssistant` is still on `main` at
`dca0d3a`, which is what the live dashboard and agent run. Steps are in
order. **Bold** steps are needed for a fix to take effect; the rest are
optional or are decisions.

## 0. Before merging

1. Review the branch. `git log --oneline main..audit-2026-11-09` shows one
   merge commit per cluster. Commits are named `E<NN>: <what>` (a fix),
   `review R4-<cluster>: <what>` (a reviewer's repair) and
   `seam S<n>-<m>: <what>` (a cross-cluster change).
2. The worktrees live under `~/HermesAssistant-worktrees/r4-*`. After
   merging, remove them with `git worktree remove <path>` and delete the
   `fix4/*` branches if you do not want them.
3. Merge when ready. The main checkout must be clean:
   ```bash
   git -C ~/HermesAssistant merge --no-ff audit-2026-11-09
   ```
   Do not push until you have done §1 and looked at §3.

## 1. Right after merging (code that runs in long-lived processes)

The approval-gate and tool-budget plugins are links into the checkout, so
their new code is on disk the moment you merge. But `hermes serve` and the
Telegram gateway keep the old module in memory until restarted.

4. **Restart the dashboard and reload the window:**
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard
   ```
   Then press ⌘R in the app and reopen Quick Ask.
5. **Deploy loop-breaker 1.1.0 and restart the agent's two processes.**
   loop-breaker is a plain copy, not a link (E66):
   ```bash
   cp ~/HermesAssistant/skills-snapshot/_plugins/loop-breaker/{__init__.py,plugin.yaml} ~/.hermes/plugins/loop-breaker/
   launchctl kickstart -k gui/$(id -u)/com.hermes.serve
   launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
   ```
   The restarts are what load the new approval-gate (E10–E14, E67, E68).
6. **Copy the corrected skills.** Nothing syncs `skills-snapshot/` to the live
   skills. Each goes to the same path under `~/.hermes/skills/`:
   - `apple/mac-control/SKILL.md`
   - `apple/screen-oracle/SKILL.md`
   - `autonomous-ai-agents/cron-conductor/SKILL.md`
   - `autonomous-ai-agents/github-sandbox/SKILL.md` and `scripts/gh-sandbox.sh`
   - `autonomous-ai-agents/hub-cartographer/SKILL.md`
   - `autonomous-ai-agents/mirror-check/SKILL.md`
   - `autonomous-ai-agents/skill-forge/SKILL.md`
   - `autonomous-ai-agents/think-with-claude/SKILL.md` and `scripts/think.sh`
   - `autonomous-ai-agents/watchtower-author/SKILL.md`
   - `autonomous-ai-agents/you-model-onboarding/SKILL.md` — this one was
     already stale before the round: the installed copy still says nothing
     ever leaves the Mac (E49).
7. Run doctor. It now has **23 checks**. The new one, "Trust patterns",
   compares Hermes' own dangerous-command list with the copy the Trust
   policy carries; it should say OK on Hermes 0.18.
8. Restart any open Claude Code session so the MCP server relaunches with
   the new code (E69, E81, E83, and the Needs-you "As of" line).
9. The model servers pick up the new launcher at their next start. A model
   that is running now keeps the old launcher until it next sleeps or
   restarts. The new launcher refuses any image, audio or video that is not
   inline data (E100).
10. You do not need to run `./install-services.sh`. If you do, it reloads a
    loaded model lane, as it always has.

## 2. Behaviour you will notice

**Approvals**
- The approval card shows a command line for line. Hidden and right-to-left
  characters appear as `⟦U+202E⟧`, long runs of blanks as `⟦260 spaces⟧`.
  A long command gets a line above the buttons telling you its length and
  to scroll up and read all of it (E15).
- The card says why it is asking when the Trust policy decided, for example
  "more than one command — asking" (E03).
- **Trust "Auto" approves less.** It is honoured only for one plain command
  of up to 512 characters that matches nothing stricter. A command with
  `;`, `&&`, a pipe, a substitution, brackets or braces, a quoted or
  expanded command word, or a redaction mark asks. Your policy is empty
  today, so nothing changes until you set a class to Auto.
- Cards for file writes show the head and the tail of long content with
  "N characters omitted" between, and show `replace_all` (E14). A payload
  in the middle of very long content is still not shown; the card says so.
- Writes to more shell-startup files now ask or are denied: `~/.cargo/env`,
  `~/.curlrc`, `~/.netrc`, `~/.oh-my-zsh/**`, `~/.config/zsh/**` and
  similar (E68). See §3.
- On Telegram, a default file search and ordinary relative writes no longer
  raise false blocks or cards (E13).

**Flight Recorder**
- Undo now refuses when Hermes has no earlier copy of the file, and says so
  before you click. Agent edits to `.env`, logs, files over 10 MB, `build/`
  and gitignored files show as not undoable (E01).
- Undo moves a file to the trash only when the agent provably created it,
  and the confirm text says that is what will happen. Undoing that puts the
  file back and never overwrites.
- An open detail pane no longer flickers or loses its scroll position (E33).

**GitHub**
- The review sheet wraps long lines, and needs a tick for every binary file
  and for any over-long or blank-padded line. Approve stays off until you
  have expanded a diff longer than 3,000 lines (E02).
- A description you edit past the limit is refused with a counter, not cut
  after approval (E64).
- Removing a repository deletes its local mirror; Disconnect and Revoke
  delete all mirrors. The agent's sandbox clones under `~/HermesSandbox`
  are left for you (E09).
- The Connections card warns when Hermes could not check whether the key
  reaches repositories you did not pick. On an existing connection it will
  now say "sampled", not the old unqualified claim (E08).
- A renamed repository shows as moved, with instructions, and no longer
  pauses everything (E07).
- If a mirror's git settings change while Hermes is using it, GitHub writes
  are held until you connect the key again. Reconnecting needs the token,
  and GitHub shows a fine-grained token only once, so keep it somewhere
  safe (E56).

**Needs-you**
- Texts from people you reply to are no longer filed as "never" because of
  a word like "newsletter". The pop-out has a collapsed "Never (n)" group
  where you can re-file a row (E04).
- More automated senders now reach Today: bank or courier numbers you
  replied to in the last 30 days, and businesses saved as contacts.
- One time only: a breaking alert from the last six hours that you had
  marked Done comes back once (E73).
- After the window has been closed for a while, the first look can show an
  older inbox for a moment while it rebuilds (E97).

**Models and setup**
- Pause now holds during a model drill, and a drill no longer reverts a
  model switch you made while it ran (E19).
- Resume and a model switch are no longer undone a minute later (E21).
- A setup pick whose download has only started is not made the assistant
  until it is fully on disk. A small watcher then activates it, only while
  the model server is down and only if you have not chosen another model
  since. See §3.
- A failed download in the setup sheet says so, with Retry (E22).
- "Free memory" refuses while paused or asleep.

**Privacy and data**
- "What leaves this Mac" has a 17th row, for the agent's own tools: its
  terminal, browser and web extract. It has no switch (E25).
- A credential pasted into a chat is no longer sent on to Claude by
  auto-routing; the Claude card says why (E80).
- The memory layer refuses more secret shapes, and refuses "my password is
  …" sentences (E24).
- Google sign-in accepts only Google's own addresses in the client file. If
  a stored client file names anything else, paste it again (E87).

**Dictation** (when the helper is next built)
- A self-correction that involves a negation is left exactly as spoken
  (E27).
- Verbatim style no longer strips "um" and "uh" (E107).

**Updates**
- Apply refuses while a chat turn is running unless you confirm (E18).
- The first update from 1.3.2 to this code links and enables approval-gate
  and restarts the gateway (E29).

## 3. Decisions

None of these blocks the merge. Each is a default the round chose that you
may want different.

- **The setup watcher.** After setup, a pending model pick is activated
  automatically once its download completes (reviewer's repair `3a98a92`).
  Without it, someone who closes the sheet before the download ends would
  wake on the default 27B. Keep it, or ask for the pick to stay pending
  until confirmed in the model menu.
- **How strict the shell-startup class is.** It now covers whole folders
  such as `~/.oh-my-zsh` and `~/.config/zsh`, in a class the dashboard
  ships at Never. A theme file there is denied, not asked. Keep it, or ask
  for those folders at Ask.
- **A new approval-gate class for other credential folders.** Hermes' own
  denials for `~/.aws`, `~/.config/gh`, `~/.gnupg` and reads of
  `~/.hermes/auth.json` can be bypassed with the same alias and letter-case
  spellings this round closed for the gate's own classes. Covering them
  needs a new class.
- **GitHub "remove" needs no proof of owner.** A removed repository is no
  longer checked, so a later permission change or drift there would go
  unseen. This belongs with F05.
- **GitHub notices ignore the notification masters** (E77). They are
  documented as security notices. Keep, or ask for a setting.
- **Dictation verbatim keeps fillers** (above).
- **E133** (three animation edge cases with no current caller) was left
  alone.
- Still open from earlier rounds: F05 (the app-held nonce), the public
  remote's history (B30), deleting Hermes' own copy of a deleted
  conversation (C58), pinning `softprops/action-gh-release` to a SHA.

## 4. Not verified — needs a live system

- **WebKit.** The review sheet and both approval cards were rendered in
  headless Chromium only. Open one of each in the app and check that long
  lines wrap and the markers show.
- **Real GitHub.** Three things are assumed from GitHub's documentation:
  that a renamed repository answers 301 (E07), the draft-PR fallback (E62),
  and how github.com shows a file that carries a NUL byte.
- **A real approval with a Trust class on Auto.** Set one low-risk class to
  Auto, ask for a compound command, and confirm the card appears with its
  reason line.
- **A real update hop.** The 1.3.2 → this-tree hop was run in scratch
  repositories with stubbed services, never against an install.
- **The Playwright suite for the review sheet**
  (`tools/tests/browser/t_gh_review.py`) has still never run: Python
  Playwright is not installed.
- On AC power: the drills and probes already listed in the 2026-10-21
  checklist §4 and §6.

## 5. Next Swift rebuild (one batch; a rebuild drops Full Disk Access)

In this order:
1. **E26** — query the live Messages database read-only instead of copying
   all of it to a temp file every 60 s. This also closes C89.
2. **E108** with the **B18** Swift half — the native bridges should accept
   only the dashboard's own origin, and the shell should open only http(s).
3. **E109** — reload after a WebContent crash.
4. **F05**, if it is ready.

`app/build-app.sh` no longer deletes the dictation helper when it rebuilds
the main app (E126).

## 6. Worth reporting to Hermes upstream

Found while testing against the installed Hermes 0.18. None is fixed by
this repository.

- Two of Hermes' own dangerous-command patterns backtrack exponentially. A
  crafted `hermes --a …` terminal command can hang `serve`.
- Hermes' sensitive-path checks (`is_write_denied` and friends) miss the
  `/System/Volumes/Data` spelling and letter-case variants.
- For a patch with a relative header, Hermes checks the raw relative path
  but writes relative to the terminal's current directory.
- `hermes config set` does not take `config.yaml.lock`, so it can race with
  another writer.
- Recording whether a file existed before `write_file` would let Undo close
  its last gap (below).

## 7. Still open after this round

- **Undo, one narrow case.** A file of yours can still be judged "created"
  if it already had mode 0600, is absent from the matched snapshot, and the
  agent never read it.
- **A reply lost to a dashboard restart** is flagged with a notice, but not
  fetched back from `serve` (E18).
- **An update interrupted after the code swap** still reads "already up to
  date" next time. `./update.sh --post-update` finishes it.
- **`config.yaml` editors** replace a symlinked `config.yaml` with a regular
  file, and `plugin_enable.py` never prunes its backups.
- **PR text.** Link-reference definitions inside a quote or list are not
  stripped, and a code span that pairs across lines can leave a live
  @mention (about 10 in 40,000 fuzzed bodies).
- **Secret shapes** are missed when glued to a preceding word character or
  written in upper case, as the older shapes always were.
- **A setup re-run** now shows a failed pending download, but only after
  the state is re-read.
- **The advisor's "lane can run" evidence** accepts a stale start token or
  a log from a refused start.
- **Interrupted GitHub push, one case.** If repository access is lost
  between the push and the check, a 404 is read as "the branch is absent"
  and the ledger records that nothing was pushed (E57).
- **A labelled secret that starts with a plain word** (`password: correct
  horse`) is redacted in exports but still passes the memory and GitHub
  checks.
- **Fifteen source lines contain a literal invisible or line-separator
  character** (U+FEFF, U+2028, U+2029, no-break space). Harmless today;
  worth cleaning in one commit.
- A failed pending setup download is polled every 30 seconds until you
  retry or pick another model.
