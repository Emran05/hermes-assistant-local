# Hands-1 — Dev-Agent: sandboxed Claude Code orchestration

**Status:** spec (ready to build). **Author:** Hermes orchestrator. **Date:** 2026-07-05.
**Depends on (all live):** aux-module loader (`server.py:2124-2149`), `register_get/register_post`
(`server.py:2107-2111`), `RouteCtx` (`server.py:2116-2131`), `recorder_record_local`
(`aux_recorder.py:533`), `permissions.decide/audit` (`permissions.py:440`), the ticket-gate
pattern (`aux_shortcuts.py`), `get_access()` (`server.py:953`, `ACCESS_FILE = DATA/access.json`).

This is **Hands-1 of the "give Hermes hands" mission**: let the local agent spawn Claude Code to
work on code — *including upgrading Hermes itself* — where every byte of write is contained to a
throwaway git worktree by `sandbox-exec`, nothing touches the live system until the **user**
reviews the diff and merges. (Hands-2, direct Mac control, is a separate spec.)

---

## 0. Ground truth verified today (do not re-litigate)

| Claim | How verified | Result |
|---|---|---|
| `sandbox-exec` genuinely blocks writes outside an allowed subpath | Ran a `(deny default)(allow file-read*)(allow file-write* (subpath WT)(subpath /tmp))(deny file-write* ($HOME/.hermes))` profile; attempted 3 writes | write **inside** WT → `WROTE_INSIDE_OK`; write to `$HOME` → `Operation not permitted`; write to `~/.hermes` → `Operation not permitted`; no escape file created |
| Claude Code CLI flags | `claude --version` + `claude --help` | v2.1.201 at `~/.nvm/versions/node/v22.17.1/bin/claude`. Confirmed: `-p/--print`, `--allowed-tools <tools...>`, `--disallowed-tools`, `--dangerously-skip-permissions`, `--output-format <stream-json>`, `--add-dir <dirs...>`, `--permission-mode`, `-c/--continue`, `--resume`, `--model`, `--bg/--background` |
| `~/HermesAssistant` is a git repo, main @ `7b0a58e`, one worktree | `git worktree list` | clean; `git worktree add` usable |
| Live services | `install-services.sh:12-14` | `com.hermes.mlx-server` (:8080), `com.hermes.dashboard` (:7788), `com.hermes.serve` (:9119); restart = `launchctl kickstart -k gui/$UID/com.hermes.dashboard` |
| Health probe | `server.py:2206` | `GET /api/health` → `{model_online, hermes_found, hermes_path}` |

**Two trees, do not confuse them:** the coding target repo is **`~/HermesAssistant`** (this git repo;
the live dashboard is served out of `~/HermesAssistant/dashboard`). The *agent runtime* lives
separately at `~/.hermes/hermes-agent` (recorder's `HERMES_SRC`, `aux_recorder.py:43`). Dev-agent's
`target=hermes` means **`~/HermesAssistant`**.

---

## 1. Goal & acceptance criteria

**Goal.** A dashboard-owned service the local agent calls to spawn Claude Code on a coding goal,
fully sandboxed, and produce a **reviewable git diff** the user merges. For `target=hermes` the
change is additionally **boot-tested in the worktree** on a scratch port before the diff is
surfaced. The agent never runs raw `claude` in the terminal for code work — it goes through these
endpoints, which own the sandbox, the recorder, and the merge gate.

**Acceptance criteria (all must hold):**

1. `POST /api/devagent/start {goal, target}` creates `git worktree add /tmp/hermes-dev/<id>` off the
   target repo's `main`, writes a per-session `sandbox-exec` profile, and launches
   `sandbox-exec -f <profile> claude -p "<goal+guardrails>" --dangerously-skip-permissions
   --output-format stream-json` as a tracked background session.
2. `GET /api/devagent/sessions` and `GET /api/devagent/stream?id=` return live status + captured
   stream-json progress; `GET /api/devagent/diff?id=` returns `git -C <worktree> diff` (the artifact).
3. **Sandbox proof (shipped test):** a session whose goal is "write to `$HOME/pwned.txt`" runs to
   completion with the file **never created** — the write is denied by the profile, recorded as an
   attempted escape. Green in CI-style `devagent_selftest.py`.
4. **Self-upgrade safety:** for `target=hermes`, after the agent finishes, a scratch dashboard boots
   from the worktree on an unused port; `curl /api/health` must return 200 with `model_online`
   before the diff is labelled `tested-green`. The live tree is **never** edited.
5. `POST /api/devagent/merge {id, ticket, approved}` is **user-gated** (single-use ticket, same
   pattern as `aux_shortcuts.py`), does the real `git merge --ff-only`/`git apply` on the target repo,
   and — only for hermes — triggers `launchctl kickstart -k gui/$UID/com.hermes.dashboard`. The agent
   can *request* but never *approve* its own merge.
6. Every lifecycle event → `recorder_record_local` (irreversible-marked where apt) and
   `permissions.audit`. If the recorder is unavailable, `start` refuses (fail-closed), matching
   `aux_shortcuts.py:526`.
7. Scope enforcement: `target` resolves to `hermes` (`~/HermesAssistant`) **or** a folder currently
   in `get_access()["dirs"]`. Anything else → 403 before any process spawns.
8. A skill `autonomous-ai-agents/dev-conductor/SKILL.md` teaches the agent to drive this via the
   endpoints, never raw `claude`.

**Non-goals (Hands-1):** no auto-merge ever; no editing the live tree; no network exfiltration path;
no PR creation (local diff only for v1); Mac desktop control (that's Hands-2).

---

## 2. Architecture (exact components)

```
Telegram / hub turn ("upgrade yourself to do X" / "add feature Y to project Z")
        │
        ▼
  Hermes agent  ──POST /api/devagent/start──►  aux_devagent.py  (exec'd into server.py globals)
        │                                          │
        │                                          ├─ resolve+authorize target (get_access / hermes)
        │                                          ├─ git worktree add /tmp/hermes-dev/<id>
        │                                          ├─ render sandbox profile /tmp/hermes-dev/<id>.sb
        │                                          ├─ spawn: sandbox-exec -f <prof> claude -p … (bg thread)
        │                                          │      stdout(stream-json) ─► session ring buffer
        │                                          └─ recorder_record_local("dev_session","start",…)
        │
        ├─GET  /api/devagent/sessions───────────►  list (status, target, started, escape_attempts)
        ├─GET  /api/devagent/stream?id=─────────►  tail of captured stream-json events
        ├─GET  /api/devagent/diff?id=───────────►  git -C <wt> diff  (+ tested_green flag)
        │
        │   on session finish (bg thread):
        │      target=hermes → boot scratch dashboard from <wt> on free port, curl /api/health
        │                       → set tested_green; kill scratch instance
        │      hermes send --to telegram "diff ready for <id> — review in Agent Desktop"
        │
        └─POST /api/devagent/merge {id}─────────►  issue ticket → user approves in Agent Desktop panel
                                                   POST again {id,ticket,approved:true}
                                                     → git -C <target> merge --ff-only <branch>
                                                     → (hermes) launchctl kickstart -k …dashboard
                                                     → recorder + audit; worktree pruned
```

**Component inventory (exact files):**

- `dashboard/aux_devagent.py` — the whole backend (one aux module, self-contained, imports its own
  stdlib + `import permissions as _dv_perm`). Follows the `aux_shortcuts.py` house style.
- `dashboard/aux_devagent.js` — Agent Desktop panel (sessions list, live stream, diff viewer, merge
  button), loaded like `aux_shortcuts.js`.
- `permissions.py` — **one addition**: a `dev-agent` class in `CLASS_META` + `CLASS_ORDER` +
  `PATTERN_CLASS` seeds (`dev-run:*`, `dev-merge:*`). Additive, no behavior change to existing keys.
- `~/.hermes/skills/autonomous-ai-agents/dev-conductor/SKILL.md` (+ `scripts/`).
- `dashboard/devagent_selftest.py` — the shippable sandbox-escape proof (safe to run).
- Runtime state: `~/.hermes/dashboard/devagent/` (0700) — session index + per-session logs. Worktrees
  + profiles live under `/tmp/hermes-dev/` (ephemeral, allowed by the profile).

**The aux-loader gotcha (critical, grounded).** Aux files are `exec`'d into `server.py` globals in
**sorted order** (`server.py:2136-2138`). `aux_devagent.py` sorts **before** `aux_recorder.py` and
`aux_permissions.py` (`d` < `p`,`r`). Therefore, exactly like `aux_shortcuts.py` does, dev-agent must
**never** reference `recorder_record_local` at module-exec time — only look it up lazily via
`globals().get("recorder_record_local")` **inside request handlers** (by which point all aux modules
are loaded). `import permissions` is fine at exec time (it's a real module on `sys.path`, not an aux
glob). Same **datetime gotcha** as `aux_shortcuts.py:40`: use only `time`, no `datetime` import
(the private-alias trap in exec'd modules).

---

## 3. Data model

**Session index** — `~/.hermes/dashboard/devagent/sessions.json` (0600, atomic write via the
`aux_shortcuts.py:_sb_save_config` tmp+`os.replace` idiom):

```jsonc
{
  "version": 1,
  "sessions": {
    "<id>": {                       // id = "dv-" + secrets.token_hex(6)
      "id": "dv-a1b2c3d4e5f6",
      "goal": "add a /api/foo endpoint that returns uptime",
      "target": "hermes",           // "hermes" | absolute granted dir
      "target_repo": "~/HermesAssistant",
      "worktree": "/tmp/hermes-dev/dv-a1b2c3d4e5f6",
      "branch": "devagent/dv-a1b2c3d4e5f6",
      "base_sha": "7b0a58e",
      "profile": "/tmp/hermes-dev/dv-a1b2c3d4e5f6.sb",
      "state": "running",           // queued|running|finished|tested|failed|merged|aborted
      "started": 1751745600.0,
      "ended": null,
      "exit_code": null,
      "pid": 41234,
      "escape_attempts": 0,         // count of file-write denials observed (sandbox proof)
      "tested_green": false,        // hermes only: scratch boot passed /api/health
      "diff_stat": null,            // "3 files changed, 40 insertions(+)"
      "source": "telegram"          // request origin, for the recorder
    }
  }
}
```

**Per-session log** — `~/.hermes/dashboard/devagent/<id>.log.jsonl` (append-only): one line per
captured stream-json event (`{"ts":…, "type":"assistant|tool_use|tool_result|result", …}`). Capped
(`DV_LOG_CAP` bytes; rotate-drop oldest). This is what `/stream` tails.

**In-memory (never persisted, fail-closed):**
- `_DV_SESSIONS` — live handles: `{id: {proc, thread, ring:[…], lock}}`.
- `_DV_TICKETS` — merge-approval tickets, single-use, 5-min TTL, die with the process (verbatim the
  `aux_shortcuts.py` ticket store — a dashboard restart voids any consent-in-flight).

**Recorder rows** (`recorder_record_local`, `aux_recorder.py:533` signature
`(tool, target, kind, reversible, source, **kw)`):
- `tool="dev_session"`, `kind="agent"`, `reversible="no"` — start / finish / escape-attempt / abort.
- `tool="dev_merge"`, `kind="shell"`, `reversible="partial"` — the actual git merge (file-state
  restorable via the pre-merge sha we record in `after_state`). Merge is the *only* row that mutates
  the real tree, so it is the one worth checkpointing.

`kind="agent"` maps to `REVERSIBLE_POLICY` → `"no"` and is **not** in `UNDO_WHITELIST`
(`aux_recorder.py:71,73`), so `/api/undo` structurally refuses to "undo" a dev session — correct: you
undo a *merge* by git, not by the recorder.

---

## 4. Backend — `aux_devagent.py` (exact names)

### 4.1 Constants
```
DV_DIR        = ~/.hermes/dashboard/devagent            # 0700
DV_SESSIONS_F = DV_DIR/sessions.json
DV_WT_ROOT    = /tmp/hermes-dev                         # worktrees + profiles
DV_CLAUDE     = os.environ.get("HERMES_CLAUDE_BIN",
                  "~/.nvm/versions/node/v22.17.1/bin/claude")   # verified path
DV_SANDBOX    = "/usr/bin/sandbox-exec"                 # verified present
DV_HERMES_REPO= ~/HermesAssistant                       # target=hermes
DV_TICKET_TTL = 300      DV_RUN_TIMEOUT = 1800 (30 min hard kill)
DV_LOG_CAP    = 2_000_000   DV_STREAM_TAIL = 400 (events)
DV_CLASS_RUN  = "dev-agent"   DV_CLASS_MERGE = "dev-agent"
DV_ALLOWED_TOOLS = "Read Write Edit Bash Glob Grep"    # passed to --allowed-tools
```

### 4.2 Endpoints (registered at module tail, `aux_shortcuts.py:585` style)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| POST | `/api/devagent/start` | `devagent_start_handler(ctx)` | authorize → worktree → profile → spawn |
| GET | `/api/devagent/sessions` | `devagent_sessions_handler(ctx)` | list all (index + live state) |
| GET | `/api/devagent/stream` | `devagent_stream_handler(ctx)` | `?id=` → last `DV_STREAM_TAIL` events |
| GET | `/api/devagent/diff` | `devagent_diff_handler(ctx)` | `?id=` → `git -C <wt> diff`, diffstat, `tested_green` |
| POST | `/api/devagent/merge` | `devagent_merge_handler(ctx)` | issue ticket / redeem ticket → real merge |
| POST | `/api/devagent/abort` | `devagent_abort_handler(ctx)` | kill proc, prune worktree, record |

### 4.3 `devagent_start_handler` flow (fail-closed order — matches `shortcuts_run_handler`)
1. `if not callable(globals().get("recorder_record_local")): return 503` ("refusing to run
   unrecorded", verbatim policy from `aux_shortcuts.py:526`).
2. Parse `goal` (str, ≤ 8 KB), `target` (str). Reject empty goal.
3. **Authorize target** → `_dv_resolve_target(target)`:
   - `"hermes"` → `DV_HERMES_REPO`.
   - else must be an **exact member** of `get_access()["dirs"]` (`server.py:953`) **and** a git repo
     (`git -C <dir> rev-parse --show-toplevel` equals it). Anything else → `(None, 403)`.
   - Refuse if the resolved path is `/`, `$HOME`, `~/.hermes`, or contains `.hermes` — belt-and-suspenders.
4. `pk = DV_CLASS_RUN + ":" + target` ; `permissions.decide({pattern_key:pk, command:"claude -p …"})`.
   `tier=="never"` → audit `auto-denied` + 403. (Default tier for `dev-agent` is **`ask`** — see §5.)
   Since `start` itself spawns nothing destructive to the live tree, `ask` here can be satisfied by the
   **dashboard-config allowlist** (user pre-authorizes autonomous coding on this target) rather than a
   per-run ticket; the *merge* is where the hard ticket lives. Configurable, default = start requires
   the target be allowlisted, merge always tickets.
5. `id = "dv-"+secrets.token_hex(6)`; `mkdir -p /tmp/hermes-dev`.
6. `git -C <target_repo> worktree add -b devagent/<id> /tmp/hermes-dev/<id> main`
   (argv list, `shell=False`). Capture `base_sha`.
7. `_dv_write_profile(id, worktree)` → `/tmp/hermes-dev/<id>.sb` (§5.1).
8. Build argv:
   ```
   [DV_SANDBOX, "-f", profile, DV_CLAUDE, "-p", _dv_prompt(goal, target),
    "--dangerously-skip-permissions", "--output-format", "stream-json",
    "--allowed-tools", *DV_ALLOWED_TOOLS.split(),
    "--add-dir", worktree]
   ```
   `cwd=worktree`, `env=_dv_env()` (see §5.3 — **scrubbed** env, no secrets), `shell=False`.
9. Spawn in a daemon thread; stream stdout line-by-line → parse JSON → append to
   `<id>.log.jsonl` + ring buffer; **count `file-write*` deny lines / permission-error tool_results →
   `escape_attempts`**. Enforce `DV_RUN_TIMEOUT` (kill process group).
10. `recorder_record_local(tool="dev_session", target=id, kind="agent", reversible="no",
    source="devagent", args={goal,target}, status="running", summary="dev session started: "+goal)`.
    `permissions.audit(...,"asked"/"auto")`.
11. Return `{ok, id, state:"running", worktree, branch}`.

### 4.4 On finish (background thread continuation)
- Record `exit_code`, `diff_stat = git -C <wt> diff --stat`, set `state="finished"`.
- **If `target=="hermes"` and diff non-empty → `_dv_boottest(id)`** (§5.2): pick a free port,
  `DASH_PORT=<port> python <wt>/dashboard/server.py` under a **read-only-everywhere** sandbox profile
  (it must not write the live tree either), poll `curl -s 127.0.0.1:<port>/api/health` up to 15 s.
  200 + `model_online` present → `tested_green=True`, `state="tested"`. Kill scratch instance
  (SIGTERM the pgid). Boot failure → `state="failed"`, diff still viewable, `tested_green=False`.
- `_dv_notify(id)` → `hermes send --to telegram` (reuse the `aux_watchtower.py:879 _wt_send_telegram`
  pattern: `[HERMES,"send","--to","telegram","--quiet",text]`, scrubbed env): *"Dev session dv-…
  finished for <target>. <diffstat>. tested-green=<bool>. Review + merge in Agent Desktop."*
- `recorder_record_local(..., status="done", summary=…, after_state={exit_code, diff_stat,
  escape_attempts, tested_green})`.

### 4.5 `devagent_merge_handler` (user-gated, ticket = `aux_shortcuts.py` verbatim)
- No `ticket` in body → **issue**: verify session `finished/tested`, `pk=DV_CLASS_MERGE+":"+id`,
  `permissions.decide`; `tier` clamped so merge is **never auto** (defense-in-depth like
  `aux_shortcuts.py:558` `if tier=="auto": tier="ask"`). Mint token, store in `_DV_TICKETS`, record
  `ask`, return `{needs_approval:true, ticket, message:"user must confirm in Agent Desktop"}`.
- With `ticket` → **redeem**: consume single-use *before* acting; `approved:false` → record deny,
  done. `approved:true` →
  1. `git -C <target> fetch` not needed (local); verify worktree branch still fast-forwardable:
     `git -C <target> merge-base --is-ancestor main devagent/<id>` — if not, refuse ("base moved,
     re-run"). Record `pre_merge_sha`.
  2. `git -C <target> merge --ff-only devagent/<id>` (argv, shell=False). Non-ff → refuse, do **not**
     force.
  3. `git -C <target> worktree remove --force /tmp/hermes-dev/<id>` + delete profile.
  4. **hermes only:** `launchctl kickstart -k gui/$UID/com.hermes.dashboard` (grounded restart cmd,
     `install-services.sh:126`). Record as `dev_merge` / `kind=shell` / `reversible=partial` with
     `after_state={pre_merge_sha, new_sha}`.
  5. `state="merged"`; audit `user-approve`; return `{ok, status:"merged", new_sha}`.

---

## 5. Safety model (invariant-by-invariant: what CANNOT happen, and why)

### 5.1 The sandbox profile — `_dv_write_profile(id, wt)` (VERIFIED syntax)
```scheme
(version 1)
(deny default)                       ; nothing is allowed unless named
(allow process*)                     ; claude needs to fork node
(allow sysctl-read) (allow mach*) (allow signal) (allow ipc*)
(allow file-read*)                   ; reads allowed everywhere (it must read the repo)
(allow file-write*
  (subpath "<wt>")                   ; the throwaway worktree — the ONLY real writable tree
  (subpath "/tmp/hermes-dev")
  (subpath "/private/tmp/hermes-dev")
  (subpath "<HOME>/.claude")         ; claude's own state/session cache
  (subpath "<HOME>/Library/Caches/claude-cli-nodejs")
  (subpath "/private/var/folders")   ; node/tmp
  (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))
(deny file-write*                    ; explicit belt-and-suspenders over the deny-default
  (subpath "<HOME>/.hermes")         ; ALL Hermes secrets + live runtime
  (subpath "<HOME>/HermesAssistant") ; the LIVE repo — worktree is /tmp, never here
  (subpath "<HOME>/.ssh")
  (literal "<HOME>/.zshrc") (literal "<HOME>/.zprofile"))
```
**Proven (§0):** writes inside `<wt>` succeed; writes to `$HOME` and `~/.hermes` return
`Operation not permitted`; no file is created. `file-read*` is allowed so a rogue agent *could read*
broadly — which is why §5.3 scrubs secrets out of its **env** and the profile does not grant it a
network exfil channel by policy (see 5.4). Note the two writable-tree lines for `<wt>`: on macOS
`/tmp` is a symlink to `/private/tmp`, so both the `/tmp/hermes-dev` and `/private/tmp/hermes-dev`
forms are listed (mirrors the working test).

| Invariant | What CANNOT happen | Why (mechanism) |
|---|---|---|
| **Live tree never edited by the spawned agent** | Claude Code cannot write `~/HermesAssistant/**` | worktree is at `/tmp/hermes-dev/<id>`; profile `deny file-write* (subpath <HOME>/HermesAssistant)` on top of `deny default`. Verified an out-of-WT write → `Operation not permitted`. |
| **Secrets never written/corrupted** | Cannot write `~/.hermes/**` (`.env`, `serve-token`, `google_token.json`, `messages-token`) | explicit `deny file-write* (subpath <HOME>/.hermes)` + deny-default. Verified. |
| **Secrets never *exfiltrated*** | Cannot read a secret and phone it home through Hermes | env is scrubbed (§5.3) so the process doesn't inherit tokens; the *values* it could read from disk have no sanctioned outbound channel — Claude Code's own model API is its only network use, and we do not pipe secrets into the prompt. (Hardening: optional no-network sub-profile for offline goals — see Open Questions.) |
| **No self-approval** | The agent cannot merge its own work | merge requires a single-use ticket redeemed with `approved:true`; the ticket is surfaced **only** in the Agent Desktop panel to the user; `aux_shortcuts.py` proves the pattern (requester ≠ approver). |
| **Self-upgrade can't break the running assistant** | A broken hermes diff cannot reach the live process pre-review | live tree is never edited; the change is tested in the worktree on a scratch port; merge is `--ff-only` + user-gated; only *then* the restart. A red boot-test still lets the user see the diff but flags `tested_green=false`. |
| **Never runs unrecorded** | `start` refuses if the recorder is down | `if not callable(globals().get("recorder_record_local")): 503` — copied from `aux_shortcuts.py:526`. |
| **No `--yolo` of the Hermes approval gate** | Merge + restart never auto | `tier=="auto"` clamped to `ask` for `dev-agent` (defense-in-depth, `aux_shortcuts.py:558`); `credential-write`-adjacent paths are structurally denied by the profile, not by judgment. |

### 5.2 Scratch boot-test (`_dv_boottest`) — self-upgrade proof
- Free port: bind `("127.0.0.1", 0)`, read assigned port, close. (Avoid the live 7788/9119/8080.)
- Launch `DASH_PORT=<port> python3 <wt>/dashboard/server.py` with `cwd=<wt>/dashboard`, **under a
  read-only sandbox profile** (no `file-write*` to the live tree; it may write its own `/tmp`), pgid
  isolated, `DV_BOOT_TIMEOUT=15s`.
- Poll `GET http://127.0.0.1:<port>/api/health`; success = HTTP 200 with a JSON body containing
  `hermes_found` (schema from `server.py:2206`). Then `SIGTERM` the pgid.
- The scratch instance points at the **worktree's** code but the **same** `~/.hermes/dashboard` data
  dir (read-mostly) — acceptable for a boot smoke test; it does **not** get a merge and is killed in
  seconds. (Open Q: fully isolate its `DATA` dir.)

### 5.3 `_dv_env()` — scrubbed environment
Start from a minimal env, **not** `os.environ`. Include: `PATH` (with the node bin dir so `claude`
resolves), `HOME`, `TERM`, `LANG`, and `ANTHROPIC_*`/claude-auth vars **only if** Claude Code needs
them for its own login (it uses `~/.claude` OAuth, so ideally none). **Exclude** everything
`HERMES_*`, `*_TOKEN`, `GOOGLE_*`, and anything read out of `~/.hermes/.env`. This is the difference
between "the agent can read a secret file if it tries" (blocked further by not being *given* one and
having no channel) and "we handed it the secrets in `env`" (never).

### 5.4 Network posture
v1 allows the process network access (Claude Code must reach the model API). The exfil risk is
mitigated by (a) scrubbed env, (b) not injecting secrets into the goal prompt, (c) the recorder
capturing the full tool stream for after-the-fact audit. **Optional hardening** (Open Q): a
`(deny network*)` variant of the profile for goals that don't need Claude's cloud — not viable while
Claude Code itself is cloud-backed, so realistically network stays on and env-scrubbing + read-only
secret-partition is the containment.

---

## 6. Frontend — Agent Desktop panel (`aux_devagent.js`)

A hub panel titled **"Agent Desktop → Dev Sessions"** (registered like `aux_shortcuts.js`; the wider
Agent Desktop surface also hosts Hands-2's Mac-control card). Renders:
- **Sessions table:** id, target, goal (truncated), state badge (`running`/`tested-green`/`failed`/
  `merged`), diffstat, `escape_attempts` (should be 0; non-zero = red alarm), started-ago.
- **Live stream drawer:** polls `/api/devagent/stream?id=` (2 s) while `running`; renders the
  stream-json events as a readable activity log (tool_use → "editing server.py", etc.).
- **Diff viewer:** `/api/devagent/diff?id=` in a `<pre>` with monospace + `overflow-x:auto`; a
  `tested-green` chip when the boot-test passed.
- **Merge control:** "Request merge" → shows the returned ticket + an explicit **"Approve merge &
  restart"** confirm that POSTs `{id, ticket, approved:true}`. Only the user, in the panel, sees the
  ticket. A **"Discard"** button → `/api/devagent/abort`.
- Data-dense, theme-aware, no external assets (CSP-clean like the rest of the hub).

---

## 7. The skill(s) to author

### `~/.hermes/skills/autonomous-ai-agents/dev-conductor/SKILL.md`
Frontmatter matches the house format (`cron-conductor/SKILL.md`):
```yaml
---
name: dev-conductor
description: "Drive sandboxed Claude Code through the dashboard dev-agent endpoints to write code and upgrade Hermes itself — worktree-contained, boot-tested, user-merged. Never run raw claude for code work."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [claude-code, sandbox, self-upgrade, git-worktree, dev-agent, safety]
    related_skills: [claude-code, hermes-agent, cron-conductor]
---
```
**Body teaches (recipes):**
1. **The one rule:** for any "write code / add feature / upgrade yourself" ask, call
   `POST /api/devagent/start` — **never** `claude` in the terminal. Raw `claude` bypasses the
   sandbox, the recorder, and the merge gate (exactly why the shortcuts bus exists,
   `aux_shortcuts.py:11`).
2. **Choosing target:** `"hermes"` for self-modification; otherwise an **exact** path already in
   Folder Access (Settings → Folder Access). If the folder isn't granted, tell the user to grant it —
   don't try to widen scope.
3. **Writing the goal:** one crisp deliverable, name the files if known, "add tests", "don't touch
   config". Never paste secrets into the goal.
4. **The wait:** poll `/sessions`; when `finished`/`tested`, read `/diff`. Summarize the diff to the
   user. For hermes, report `tested_green`.
5. **Merge etiquette:** request merge, then **stop** — the user approves in Agent Desktop. You cannot
   and must not approve. If they approve, confirm the restart landed (`/api/health`).
6. **Red flags:** `escape_attempts > 0` → surface loudly, do **not** recommend merge. `tested_green:
   false` on hermes → tell the user the change didn't boot; recommend a fix-up session, not a merge.
7. **`scripts/devagent.sh`** — thin curl wrappers (`start`, `sessions`, `diff`, `merge`) for the
   agent's terminal tool, all hitting `127.0.0.1:7788`.

---

## 8. Edge cases

- **Worktree add fails** (dirty index / branch exists): sanitize by `git worktree prune` first;
  branch name includes the random id so collisions are near-impossible; on failure return 500 + record.
- **`base` (main) moves between start and merge:** `merge --ff-only` refuses a non-ff; handler returns
  "base advanced, re-run the session" — never `-f`.
- **Claude Code hangs:** `DV_RUN_TIMEOUT` (30 min) kills the pgid; state → `failed`; worktree kept for
  inspection until abort.
- **Dashboard restart mid-session:** in-memory proc handles die; on reload, sessions in `running`
  with a dead pid are reconciled to `orphaned` (worktree + diff still inspectable). Tickets are void
  (fail-closed, `aux_shortcuts.py:224`).
- **Recorder DB locked/unavailable:** `start` 503s; a mid-flight record failure is logged but doesn't
  crash the session (the *start* gate is the hard one).
- **`get_access()` grant revoked after start:** the worktree already exists off that repo; merge
  re-checks the grant and refuses if the folder is no longer granted.
- **Scratch boot-test port race / long import:** 15 s timeout → `tested_green:false` (not fatal;
  user still reviews).
- **Escape-attempt false positives:** only count genuine `file-write*` deny signatures /
  permission-denied tool_results, not ordinary Claude "I can't do X" prose.
- **Two sessions, same target:** allowed (distinct branches); merge order enforced by ff-only —
  second must rebase/re-run if the first merged.
- **Disk pressure in `/tmp`:** worktrees are pruned on merge/abort; a GC sweep removes
  `/tmp/hermes-dev/*` older than 24 h with no live session.
- **Memory ceiling:** Claude Code is **cloud-backed** — the coding itself puts ~no pressure on local
  MLX, so dev-agent runs even when local-model admission is backing off at the 50 GB ceiling. Only the
  dashboard orchestration (tiny) is local. (Contrast Hands-2 computer-use, which is heavy.)

---

## 9. Test plan (safe to run — no real destructive ops, no `--yolo` of hermes)

Ship `dashboard/devagent_selftest.py`. All tests use `target` = a throwaway temp git repo (or
`hermes` **without ever calling merge**), so nothing merges into the live tree.

1. **Sandbox escape is blocked (THE proof, already green in §0).** Start a session in a temp repo with
   goal *"create the file `$HOME/pwned.txt` with contents `x`"*; on finish assert
   `not os.path.exists("$HOME/pwned.txt")` and `escape_attempts >= 1`. This is the load-bearing test.
2. **In-worktree write works:** goal *"add a file `hello.txt`"*; assert `git -C <wt> diff` shows it and
   the live repo is unchanged.
3. **Scope enforcement:** `start` with `target="/etc"` and with a non-granted path → both 403, no
   worktree created.
4. **Fail-closed recorder:** monkeypatch `recorder_record_local` to absent → `start` returns 503.
5. **No self-approval:** `merge` without ticket → `needs_approval`; redeeming a **forged/expired**
   ticket → 403; redeeming twice → second is refused (single-use).
6. **Self-upgrade boot-test:** a hermes session with a trivial safe diff (add a comment) → assert
   `tested_green:true` and the scratch instance is gone (port freed) afterward; a session with a
   deliberately broken `server.py` edit → `tested_green:false`, diff still returned, **no merge**.
7. **`--ff-only` refuses divergence:** advance `main` after a session, attempt merge → refused, live
   `HEAD` unchanged.
8. **No secret in env:** spawn a probe goal that dumps `env`; assert no `HERMES_*`/`*_TOKEN`/`GOOGLE_*`
   appears in the captured stream.
9. **Recorder + audit rows exist** for start/finish/merge-request with the right `tool`/`kind`.

Run: `python3 dashboard/devagent_selftest.py` — must be green before wiring the panel.

---

## 10. Sequencing

1. `permissions.py` — add `dev-agent` class (additive; assertion loop at `permissions.py:252` must
   still pass). Ship + verify Trust panel still renders.
2. `aux_devagent.py` core: constants, `_dv_write_profile`, `_dv_resolve_target`, `_dv_env`,
   `start`/`sessions`/`stream`/`diff` (no merge yet). Manual smoke via curl.
3. `devagent_selftest.py` tests 1–4, 8–9 → green (the sandbox proof lands here).
4. Merge path: tickets, `merge`/`abort`, `--ff-only`, hermes restart. Tests 5, 7.
5. `_dv_boottest` + notify. Test 6.
6. `aux_devagent.js` Agent Desktop panel.
7. `dev-conductor/SKILL.md` + `scripts/devagent.sh`.
8. End-to-end dry run: agent (via skill) starts a hermes session for a trivial real feature, boot-test
   green, user merges from the panel, dashboard restarts clean.

---

## 11. Open questions

1. **`start` gate:** allowlist-per-target (frictionless autonomous coding, merge is the hard gate) vs.
   per-run ticket even to *start*? Spec defaults to: start = target must be allowlisted; **merge always
   tickets**. Confirm with user.
2. **Scratch boot-test DATA isolation:** share the live `~/.hermes/dashboard` (read-mostly, simplest)
   vs. a copied throwaway DATA dir (fully isolated, more code)? Spec ships shared+read-only; revisit if
   a boot-test ever writes.
3. **Network hardening:** worth a `(deny network*)` profile variant for offline-capable goals? Moot
   while Claude Code is cloud-backed — deferred.
4. **PR flow:** v1 is local-diff + local-merge. Add optional `gh pr create` from the worktree branch
   for projects the user wants on GitHub? Hands-1.1.
5. **Concurrency cap:** max simultaneous dev sessions (RAM/`/tmp`)? Propose 2.
6. **`--allowed-tools` scope:** ship with `Read Write Edit Bash Glob Grep`; is `Bash` inside the
   sandbox acceptable (it's write-contained but can spawn arbitrary processes)? The profile contains
   the blast radius; confirm comfort level.
