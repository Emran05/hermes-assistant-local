---
name: mirror-check
description: "Post-task self-QA ritual + nightly self-review journal: verify your own work against the flight recorder, read your vitals, write yourself lessons."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [self-qa, verification, flight-recorder, journal, vitals, metrics, memory, autonomy, trust]
    related_skills: [hermes-agent]
---

# Mirror Check

Never trust your own success claim. This skill teaches you to verify what you
actually did (not what you believe you did) against the flight recorder, to
read your own vitals, and to leave yourself dated lessons so tomorrow's you is
better than today's.

Everything here is read-only against the world. The ONLY thing this skill ever
writes is your own memory, through the dashboard's etag-guarded memory API.

## Scripts

- `scripts/task_audit.py` — time window → flight-recorder events → readable audit report; flags irreversible rows. Run it, don't reimplement it.
- `scripts/vitals_snapshot.py` — /api/metrics + /api/mind_drill week-over-week deltas as one JSON blob.

Both are python3 stdlib-only and talk to the local dashboard at
`http://127.0.0.1:7788` (override with env `HERMES_DASH_URL`).

```bash
MC="${HERMES_HOME:-$HOME/.hermes}/skills/autonomous-ai-agents/mirror-check/scripts"
python3 "$MC/task_audit.py" --minutes 60          # audit the last hour
python3 "$MC/task_audit.py" --minutes 30 --json   # same, machine-readable
python3 "$MC/vitals_snapshot.py"                  # vitals JSON
```

## 1. WHEN

- **After ANY multi-step task, ALWAYS before reporting done.** If you used 3+
  tool calls, touched a file, ran a shell command, or drove the screen — run
  THE RITUAL below before you tell the user it worked.
- **Nightly journal** — a cron job (section 3) runs the self-review at ~3 AM.
- **Not needed** for pure Q&A / single web lookups with no side effects.

## 2. THE RITUAL (five steps, in order)

**Step 1 — Re-read your outputs.** Never cite your own earlier message as
evidence. If you wrote or edited a file, `read_file` it back and check the
actual content matches the intent. If the task was computer-use (clicking,
typing on screen), take a fresh screenshot and run `vision_analyze` on it —
confirm the end state visually. If you called an API, curl the resource back.

**Step 2 — Pull the flight recorder for the task window.**

```bash
python3 "$MC/task_audit.py" --minutes 30
```

Pick `--minutes` to cover the whole task. This lists every recorded action
(id, 12-hour timestamp, source, tool, target, status) and flags
`[IRREVERSIBLE]` and `[undoable]` rows. Note: `/api/recorder` itself has NO
time filter — the script paginates with `before=<id>` and filters by `ts`
client-side. Do the same if you must query it raw.

**Step 3 — Check every `[IRREVERSIBLE]` row against stated intent.** For each
irreversible-marked action ask: did the user actually ask for this, and did it
target exactly what was intended (right path, right command, right app)? Any
mismatch is an incident — say so plainly.

**Step 4 — If a step went wrong:** undo is whitelist-bound. ONLY actions with
kind `write` or `shell` that have a snapshot (`reversible` not `"no"`) can be
undone:

```bash
curl -s -X POST http://127.0.0.1:7788/api/undo \
  -H 'Content-Type: application/json' -d '{"id": <action_id>}'
```

If it returns `"error": "irreversible"`, or the action's kind is anything
else, do NOT retry and NEVER promise undo — instead PROPOSE a concrete manual
fix to the user and wait. Never claim something was rolled back unless
/api/undo returned `"ok": true` and you re-read the target to confirm.

**Step 5 — Emit the 3-line verdict.** Exactly this shape, timestamps in
12-hour form:

```
did: <one line — what actually happened, past tense, no spin>
verified-by: <evidence — recorder ids + times, file re-read, screenshot; e.g. "recorder #247–#248 (9:06 PM), re-read shortcuts.json">
residual risk: <what could still be wrong, or "none identified">
```

Deliver the verdict to the current chat. Send it via
`hermes send --to telegram` ONLY if BOTH are true: the task was cron-initiated
(no live chat to answer in) AND the user has opted into pings. Otherwise never
ping.

## 3. NIGHTLY JOURNAL (cron variant)

Create once (this registers in the standard hermes cron registry — confirm
with `hermes cron list`):

```bash
hermes cron create "0 3 * * *" \
  "Load the mirror-check skill (skill_view mirror-check) and run the NIGHTLY JOURNAL procedure from section 3: audit the last 24h via scripts/task_audit.py --minutes 1440, pull scripts/vitals_snapshot.py, bucket into incidents vs successes, and append dated lessons to mirror-journal.md ONLY via the /api/memory endpoints. If nothing noteworthy, do nothing and reply exactly: nothing noteworthy." \
  --name mirror-journal --skill mirror-check --deliver local
```

The nightly procedure:

1. `python3 "$MC/task_audit.py" --minutes 1440 --json` and
   `python3 "$MC/vitals_snapshot.py"`.
2. Bucket: **incidents** (failed/undone actions, denied approvals, loop
   warnings, irreversible actions that didn't match intent, vitals breaches)
   vs **successes** (multi-step tasks that verified clean).
3. **Noteworthy test:** if there are zero incidents and no vitals breach and
   nothing new was learned — STOP silently. An empty journal night is a good
   night. Do not write filler.
4. Append lessons to the journal file through the memory API (never raw file
   writes — see Gotchas):

```bash
# read (returns {content, etag}; 404 = create it first)
curl -s "http://127.0.0.1:7788/api/memory/file?name=mirror-journal.md"
# first time only:
curl -s -X POST http://127.0.0.1:7788/api/memory/create \
  -H 'Content-Type: application/json' \
  -d '{"name":"mirror-journal.md","content":"# Mirror journal\n"}'
# save = full new content + the etag you read (etag mismatch => re-read, merge, retry once)
curl -s -X POST http://127.0.0.1:7788/api/memory/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"mirror-journal.md","base_etag":"<etag>","content":"<old content>\n§\n2026-07-05 — <lesson>"}'
```

Journal entry format — one dated `§`-separated entry per lesson, each ≤ 2
sentences: what happened, what to do differently. Newest at the bottom.

## 4. VITALS READING

`python3 "$MC/vitals_snapshot.py"` returns current vitals + week-over-week
deltas + `breaches` vs targets. How to read it:

- **TTFT p50/p95** (`turns.ttft_ms`): p50 target < 1500 ms. p50 drifting up
  across days with the same model = context/prompt bloat — trim verbosity,
  batch tool calls. p95 >> p50 = cold-start reloads or memory pressure.
- **RAM**: the metrics API already uses **footprint semantics**. If you ever
  measure by hand use `footprint -p <mlx_pid>`, NEVER `ps` RSS — ps
  under-reports MLX/Metal unified memory badly (documented finding: ps showed
  a fraction of a real 49 GB footprint). MoE idle target ≤ 20 GB
  (`targets.moe_idle_gb`); small models ≤ 6 GB.
- **est_tok_per_sec** falling + RAM rising = the model server is degrading;
  worth flagging.

**Adaptations you may take alone** (behavioral, reversible, no config):
batch/parallelize tool calls; delegate long research to a sub-agent; trim your
own verbosity and context; prefer the audit script over hand-paginating APIs.

**Must only PROPOSE, never do** (anything touching services or config): model
switches, mlx-server flags, launchd changes, config.yaml edits, cron changes,
dashboard settings. Before proposing a config change, snapshot current state
so the proposal is diffable:

```bash
curl -s "http://127.0.0.1:7788/api/config/snapshot"
```

Present the proposal + evidence to the user and stop.

## 5. FAILURE-PATTERN MEMORY

- **Dedupe before appending.** Read `mirror-journal.md` first; if an existing
  `§` entry already captures the same failure pattern, do not append a
  near-duplicate — refine the existing entry's wording in the same save
  instead.
- **Monthly compression** (1st of the month, during the nightly run):
  `delegate` a sub-agent to read the whole journal and return 3–7 durable
  principles. Replace the journal's oldest month with a one-line digest, and
  promote truly durable principles into core memory `MEMORY.md` via the same
  API — note MEMORY.md is entry-structured and capped at 2200 chars total:

```bash
curl -s "http://127.0.0.1:7788/api/memory/file?name=MEMORY.md"   # {entries:[...], etag}
curl -s -X POST http://127.0.0.1:7788/api/memory/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"MEMORY.md","base_etag":"<etag>","entries":["<existing...>","<new principle>"]}'
```

Entries must not contain the `§` character; the API joins them itself.

## Gotchas (hard rules)

- **Unattended cron runs fail closed on approval-needing writes.** The
  journal therefore writes ONLY via the `/api/memory/*` endpoints (the
  dashboard's own etag/flock-guarded CRUD) — never `write_file`, never shell
  redirection into `~/.hermes/memories/`. If even the API call is blocked in
  an unattended run, skip and journal next attended session.
- **Verdict timestamps are 12-hour** (e.g. `9:06 PM`), matching the rest of
  the system.
- **/api/undo is whitelist-bound** (kinds `write`/`shell` with snapshot
  only). Never promise undo for anything else — computer-use actions,
  shortcuts, API calls, sends are not undoable.
- **This skill is read-only outside your own memory.** Screenshots,
  recorder/metrics GETs, file re-reads: all read-only. It exercises the
  flight-recorder invariant; it must never add risk.
- Telegram pings: cron-initiated + user opted in, both required. Default is
  silence.
