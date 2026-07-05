---
name: cron-conductor
description: "Design patterns + fleet audit for safe scheduled autonomy. Every cron you create is quiet, deduped, ledgered, and auditable by default; weekly audit joins the roster, ledger, and flight recorder into a keep/kill table."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [cron, scheduling, autonomy, audit, ledger, quiet-hours, dedupe, safety, fleet]
    related_skills: [hermes-agent]
---

# Cron Conductor

You (Hermes) can schedule autonomous work with `hermes cron`. Unattended runs are
powerful and dangerous in a boring way: they accrete. Ten quiet little jobs become
a noisy, RAM-hungry, unexplainable fleet nobody remembers creating. This skill is
the discipline layer: **every cron follows the Good-Cron Template, every creation
is ledgered, every job is run once by hand before it's trusted, and the whole
fleet is audited weekly.** This is the skill that keeps every other skill honest.

Core safety fact you design around, never against: **an unattended cron run
inherits fail-closed approvals.** Nothing and nobody is present to click
"approve", so any approval-gated action (writes outside safe dirs, dangerous
terminal patterns, deletes) simply fails. Therefore a cron body must be
**read-tier + notify-tier ONLY**: read files, web_search/web_extract, curl to
loopback GETs, memory reads, and `hermes send --to telegram` for delivery.
Never write a cron that assumes a grant. If a scheduled task genuinely needs a
gated action, the cron's job is to *notify the user asking them to do it or to
approve it live* — not to attempt it.

## 1. THE GOOD-CRON TEMPLATE

Every cron job you create MUST satisfy all seven fields. If you cannot fill one
in, the job is not ready to exist.

1. **Narrow single purpose** — one question the run answers, stated in one
   sentence. "Check if any of my 3 watched repos cut a new release" — yes.
   "Keep an eye on tech news" — no (unbounded).
2. **Noteworthy threshold** — the explicit condition that makes a run worth the
   user's attention. Written INTO the prompt: "Only report if X changed / Y
   crossed Z / a new item appeared since the last run."
3. **Silent skip** — the scheduler has a built-in contract: if the agent's final
   response starts or ends with the line `[SILENT]` (bare `SILENT`, `NO_REPLY`
   also work), **delivery is suppressed** and the output is still saved locally
   for audit. Every prompt must end with an instruction like:
   *"If nothing meets the threshold, respond with exactly `[SILENT]` — never
   send a 'nothing happened' message."* Pinging on nothing is the cardinal sin
   (same discipline as the Midday Pulse brief).
4. **Cooldown / dedupe** — the prompt must tell the agent to compare against
   what was already reported. Recipe: keep a tiny state note in memory (e.g. a
   `cron-state-<id>` freeform memory file, or a line in the run prompt like
   "you last reported release v1.4 — only speak if newer"). If the same signal
   was reported in the previous run, `[SILENT]`.
5. **Quiet hours** — default **10:00 PM – 7:00 AM**: either schedule the job
   entirely inside waking hours, or (for high-frequency jobs) instruct: *"If
   the current local time is between 10:00 PM and 7:00 AM, respond `[SILENT]`
   unless the finding is urgent-critical."* Always phrase times to the user in
   12-hour form ("daily at 6:45 AM"), even though schedules are stored 24-hour
   internally.
6. **Notify only on signal** — delivery is `hermes send --to telegram` semantics:
   set `--deliver telegram` at creation, and rely on `[SILENT]` for all
   no-signal runs. One short message, headline first.
7. **Read/notify-tier body** — the prompt may only require: reads, web search,
   loopback GET curls, memory, and the delivery itself. No writes, no installs,
   no deletes, no computer_use, no assuming approvals (see the fail-closed fact
   above).

## 2. CREATE RECIPE

Syntax below was captured from `hermes cron create --help` on this machine
(v0.18) at skill-build time. If a flag errors, re-run `hermes cron create --help`
and trust THAT over this document — never trust remembered syntax.

```
hermes cron create [--name NAME] [--deliver DELIVER] [--repeat REPEAT]
                   [--skill SKILLS] [--script SCRIPT] [--no-agent]
                   [--workdir WORKDIR]
                   schedule [prompt]
```

- `schedule`: `'30m'`, `'every 2h'`, or 5-field cron like `'45 6 * * *'`
  (= daily 6:45 AM — echo it to the user in 12-hour form).
- `--deliver telegram` for user-facing signal jobs (Telegram is locked to the
  one user). `--deliver local` for jobs whose output only feeds the audit trail.
- `--skill <name>` attaches a skill's SKILL.md to the run (repeatable).
- `--script` + `--no-agent` = classic watchdog: the script IS the job, stdout is
  delivered verbatim, **empty stdout = silent**. Prefer this for pure threshold
  checks — no LLM, no drift.

Worked example (template-compliant):

```bash
hermes cron create --name "repo-release-watch" --deliver telegram '15 9 * * *' \
  'Single purpose: check whether nousresearch/hermes-agent published a new GitHub release since the last one you reported (keep the last-reported tag in the cron-state-repo-release-watch memory file; update it only when you report). Use web_extract on the releases page — read-only tools only. Noteworthy threshold: a release tag newer than the last reported one. If the threshold is not met, or the current local time is between 10:00 PM and 7:00 AM, respond with exactly [SILENT]. On signal: one short Telegram-ready message, headline first.'
```

**Ledger immediately — a cron without a ledger entry does not exist.** The
ledger is the freeform memory file `cron-ledger.md`, entries separated by `§`
lines, written through the dashboard's etag-safe memory API:

```bash
# 1. Read current ledger (note the etag). 404 means it doesn't exist yet.
curl -s "http://127.0.0.1:7788/api/memory/file?name=cron-ledger.md"

# 1b. First time only — create it:
curl -s -X POST http://127.0.0.1:7788/api/memory/create \
  -H 'Content-Type: application/json' \
  -d '{"name":"cron-ledger.md","content":"# Cron Ledger — one § entry per job\n"}'

# 2. Append your entry (old content + delimiter + entry) using the etag you read:
curl -s -X POST http://127.0.0.1:7788/api/memory/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"cron-ledger.md","base_etag":"<ETAG>","content":"<OLD CONTENT>\n§\ncron:<JOB_ID> | purpose: <one sentence> | schedule: daily 9:15 AM | mute: [SILENT] unless new release tag; quiet 10 PM-7 AM | created: 2026-07-05"}'
```

A 409 conflict means the file changed under you — re-read, re-append, re-save.
Entry format (all four fields mandatory): `cron:<id> | purpose: … | schedule: …
(12-hour) | mute: <silent-skip condition>`. Get the `<JOB_ID>` from the
`hermes cron create` output or `hermes cron list`.

## 3. RUN ONCE BEFORE TRUSTING (drill-sergeant doctrine)

Never let a schedule be a job's first execution. After creating:

1. `hermes cron run <job_id>` — queues it for the next scheduler tick.
2. Inspect the output: `ls ~/.hermes/cron/output/<job_id>/` and read the newest
   `.md` file. Did it obey the threshold? Did it `[SILENT]` correctly?
3. Confirm the run left a trace: `curl -s "http://127.0.0.1:7788/api/recorder?limit=50"`
   and check `hermes cron list` shows a fresh `last_run_at` with `last_status`
   ok.
4. Only THEN tell the user the job is live — echoing the schedule 12-hour.

If step 2 shows the agent chattering on nothing, tighten the prompt's threshold
and silent-skip wording and re-run. A cron that fails its manual run gets fixed
or removed the same day, never left to fail on schedule.

## 4. WEEKLY AUDIT

Run the bundled auditor (stdlib-only, read-only — it never modifies anything):

```bash
python3 ~/.hermes/skills/autonomous-ai-agents/cron-conductor/scripts/cron_audit.py
```

It joins `hermes cron list`'s source of truth (`~/.hermes/cron/jobs.json`) +
`cron-ledger.md` § entries + recent run outcomes (`~/.hermes/cron/output/`,
`/api/recorder`) + `/api/metrics` RAM context into a keep/kill/reschedule
markdown table. Verdicts it emits:

- **KEEP** — ledgered, recent runs healthy.
- **KILL?** — zombie (last runs erroring / 3+ consecutive fails), orphan
  (no ledger entry, or its purpose no longer appears in memory), or expired
  purpose.
- **RESCHEDULE?** — overlapping fire times with another job, or fires inside
  quiet hours.
- **UNKNOWN** — never run yet, or not enough data.

Then: **PROPOSE the table to the user and apply only what they approve.**
You may pause/edit/remove ONLY your own cron jobs, ONLY after the user approves
the audit plan — deleting a cron without an approved plan is forbidden.
Approved actions use: `hermes cron pause <id>`, `hermes cron edit <id>
--schedule '…'`, `hermes cron remove <id>`. After any removal, append a
`retired:` note to the job's ledger entry (same etag-safe save flow) so the
ledger stays a true history. Keep total autonomous load visible — the point of
the audit is that the fleet never silently accretes.

## 5. ANTI-PATTERNS CATALOG

- **Ping-on-nothing** — a job that messages "no updates today". Fix: `[SILENT]`
  contract in the prompt, always.
- **Approval-assuming cron** — a body that tries to write/delete/install
  unattended. It will fail closed every run and show up as a zombie. Fix:
  read/notify-tier body; gated work becomes a notification asking the user.
- **Overlapping schedules** — several jobs firing the same minute pile local-LLM
  load (RAM/TTFT). Fix: stagger minutes; check `/api/metrics` during audit.
- **Unbounded output** — "summarize the news" grows without a threshold. Fix:
  narrow purpose + noteworthy threshold + dedupe state.
- **Unledgered cron** — exists in `cron list` but nowhere in `cron-ledger.md`;
  three months later nobody knows why it runs. Fix: ledger at creation, audit
  flags orphans.
- **Quiet-hour buzzing** — 3:00 AM pings for non-urgent signals. Fix: schedule
  inside waking hours or add the quiet-hours `[SILENT]` clause.
- **Schedule-as-first-test** — trusting a job that has never been run manually.
  Fix: section 3, no exceptions.

## 6. SNAPSHOT

After any fleet change (create/edit/remove), fold the roster into the config
snapshot so the fleet is documented and restorable:

```bash
curl -s -X POST http://127.0.0.1:7788/api/config/export -H 'Content-Type: application/json' -d '{}'
```

and verify `~/.hermes/cron/jobs.json` is covered by the snapshot context. The
pair (jobs.json + cron-ledger.md) is the complete restorable description of
the fleet: jobs.json says *what runs*, the ledger says *why*.

## Gotchas

- Schedules are stored 24-hour internally but are ALWAYS echoed to the user in
  12-hour form ("daily 6:45 AM", never "06:45").
- `hermes -z` one-shots (and every unattended cron run) **cannot approve
  anything** — approval-gated calls fail closed. Design bodies read/notify-tier.
- Deleting or editing a cron requires the user's approval of an audit plan (or
  an explicit user request) first. Pausing a misfiring zombie mid-incident is
  acceptable; say so in the next message and put it in the audit.
- `[SILENT]` must be the whole response or its own first/last line — buried
  mid-sentence it will NOT suppress delivery.
- `--no-agent --script` jobs go silent via **empty stdout**, not `[SILENT]`.
- Do not embed secrets in cron prompts, scripts, or the ledger.
