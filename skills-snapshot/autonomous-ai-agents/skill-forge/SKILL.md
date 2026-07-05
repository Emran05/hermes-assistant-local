---
name: skill-forge
description: "Forge new SKILL.md packs for Hermes itself: study exemplars, draft to house style, get an explicit user yes, validate, delegate-drill, log the birth. Load when asked to create, author, write, or teach a new skill or ability."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [skills, meta, self-improvement, authoring, validation, drill, delegate]
    related_skills: [dogfood]
---

# Skill Forge

You can grow. When the user asks for an ability you don't have, you don't shrug — you
forge a new skill pack: a `SKILL.md` (plus optional `scripts/`) that teaches a future
you, from a cold start, how to do the thing. This file is the recipe for writing those
recipes. Follow it exactly; the loop has mandatory gates and they exist so the user can
trust you with more, not less.

A skill = teachable ability: step-by-step tool recipes + gotchas + optional helper
scripts. NOT a daemon, NOT a config change, NOT a place for secrets.

## When to Use

- User says "build a skill", "teach yourself", "make this repeatable", "you should
  remember how to do this"
- You just solved something hard/multi-step that will clearly recur
- A stock skill is wrong for this machine and needs a house replacement

## When NOT to Use

- One-off tasks (just do them)
- Anything needing embedded credentials — skills must never contain secrets;
  credentials belong in env/credential files the skill *references* by name only
- "Improve" edits to stock packs you didn't author — propose to the user instead

## 1. Study First (READ)

Never draft from memory. Before writing a single line:

1. `ls` the skills root to pick the right EXISTING category:
   `ls "${HERMES_HOME:-$HOME/.hermes}/skills/"`
   - NEVER invent a new top-level category without an explicit user OK.
   - NEVER nest under `computer-use/`, `dogfood/`, or `yuanbao/` — those are flat
     single-skill directories, not categories (they have a SKILL.md at top level).
2. `skill_view` 2–3 exemplars for format fidelity — always include
   `google-workspace` (canonical frontmatter) plus any house-authored pack
   (author "Hermes Assistant (local)") in a category near your target.
3. Note the anatomy: `skills/<category>/<name>/SKILL.md` + optional `scripts/`
   (helpers you run via terminal) + optional `references/`.

## 2. Frontmatter Contract (enforced by validator)

Match the observed stock format exactly — YAML between `---` fences, first bytes of
the file:

- `name`: kebab-case, MUST equal the directory name
- `description`: imperative summary + when-to-load trigger words (this is how future
  you decides to load it — put the user's likely phrasing in it)
- `version`: semver string, start at `1.0.0`
- `platforms`: `[macos]` (this machine)
- `metadata.hermes.tags`: non-empty list; `metadata.hermes.related_skills` when relevant
- `author` / `license` optional per the stock anatomy; house packs use
  author `Hermes Assistant (local)`, license `MIT`
- Optional extras only if true: `prerequisites.commands`, `required_environment_variables`
  (names only — NEVER values)

## 3. House Style (every forged pack MUST contain)

1. **A `## Safety & Approvals` section** naming which steps are READ vs MUTATE and
   which gates apply (terminal approval tiers, skills-dir write tickets, send locks).
2. **A `## Drill` section**: one concrete pass/fail invocation that is
   unattended-safe — no sends, no destructive ops, no approval-gated writes, never
   `--yolo`. A fresh agent must be able to run it and self-grade from the output.
3. **A `## Gotchas` section**: the sharp edges (12-hour time preference, §-delimiter
   memory discipline, shell-escaping doctrine — whichever apply, plus anything you
   hit while building).
4. **Every recipe labeled READ or MUTATE** right in its heading or first line.

## 4. The Forge Loop (the mandatory order)

1. **Draft** the full SKILL.md in your head/scratch — do not write to the skills
   directory yet.
2. **Present a summary in chat**: name, category, what it teaches, what its scripts
   do, what its Drill proves.
3. **Explicit user yes.** (MUTATE gate — this step is part of the recipe, not
   optional. "Silence" or "they probably want it" is a no.)
4. **Write the files** (MUTATE): the skills directory is outside safe dirs, so this
   raises an approval ticket — expected and correct. Forge ONLY in hub chat where the
   user can tap approve. Never forge from cron or a `-z` oneshot (approvals fail
   closed there, and oneshot mode can fake approval success).
5. **Validate** (READ) — must PASS BEFORE the delegate test, not after:
   ```bash
   python3 "${HERMES_HOME:-$HOME/.hermes}/skills/autonomous-ai-agents/skill-forge/scripts/validate_skill.py" \
     "${HERMES_HOME:-$HOME/.hermes}/skills/<category>/<name>/SKILL.md"
   ```
   Exit 0 = frontmatter lint + secret-scan clean + house-style sections present.
6. **Delegate self-test** (section 5 below).
7. **Birth record** (MUTATE): append a §-delimited memory entry ("forged <name> on
   <date>, drill passed in N iterations") AND create a kanban card "review skill
   <name>" due in 7 days — the curator prunes at 7d; the review decides keep/kill and
   flags packs that were never used.

## 5. Delegate Self-Test (drill-sergeant doctrine)

Fresh context is the point: if a sub-agent fails the Drill using ONLY the SKILL.md,
the DOC is bad, not the model.

1. Spawn `delegate` with a clean minimal prompt:
   `Using only the skill <category>/<name> via skill_view, perform its Drill section
   exactly and report PASS or FAIL with evidence.`
   Give it no other context — no summary of what you built, no hints.
2. The tester inherits full manual approvals and CANNOT approve its own gated
   actions. That is an invariant and it is correct — which is exactly why Drills must
   stay in un-gated territory (reads, web, memory, loopback GETs).
3. Log every attempt:
   ```bash
   bash "${HERMES_HOME:-$HOME/.hermes}/skills/autonomous-ai-agents/skill-forge/scripts/drill_log.sh" <name> pass|fail <iteration> "note"
   ```
4. On FAIL: revise the SKILL.md wording (the doc, not the drill grade), re-validate,
   re-delegate. Max 3 iterations, then STOP and escalate to the user with the failing
   transcript.

## Scripts

- `scripts/validate_skill.py` — stdlib-only linter. Path mode
  (`validate_skill.py <SKILL.md|skill-dir>`) checks: required frontmatter keys,
  kebab `name` == dirname, category exists on disk and is a real category (not a
  flat skill), description non-empty, house-style sections present, secret-scan
  (API-key shapes, key=value credential pairs, auth-header values, hardcoded
  home-directory paths — hard fail). `--stdin --name X --category Y` mode lints a
  draft before any file exists (use it for dry-run drills).
- `scripts/drill_log.sh` — appends structured JSONL pass/fail records per forged
  skill to `logs/skill-drills.jsonl` under the Hermes home; `--summary` prints
  per-skill trends for reviews.

## Safety & Approvals

- Sections 1–3 and step 5 of the loop are READ — no gates.
- Loop steps 4 and 7 are MUTATE: writing into the skills directory rides the
  approval tiers (ticket-gated); memory/kanban writes ride their normal tools.
- The explicit user yes (loop step 3) is a hard gate IN ADDITION to the file-write
  ticket. Both must happen.
- Forged packs must never contain secrets — the validator enforces this
  mechanically and a hit is a hard fail, not a warning.
- Never forge under cron or oneshot contexts; never use `--yolo` (forbidden,
  always).

## Drill

Unattended-safe, zero writes. A fresh agent loaded with only this skill must:

1. WITHOUT writing any files, draft the complete SKILL.md for a hypothetical skill
   `productivity/unit-convert` (converts units via terminal arithmetic): full
   frontmatter per the contract PLUS `## Safety & Approvals`, `## Drill`, and
   `## Gotchas` sections per the house style, recipes labeled READ/MUTATE.
2. Print the draft in full.
3. Print the exact `validate_skill.py` command you would run next (the `--stdin`
   form, since no file exists yet).

PASS = the printed draft, piped to
`python3 scripts/validate_skill.py --stdin --name unit-convert --category productivity`,
exits 0. FAIL = validator non-zero, or the agent wrote files, or it skipped any of
the three house-style sections.

## Gotchas

- The skills directory is OUTSIDE the safe-dir set: every file write there raises an
  approval ticket. In hub chat the user taps approve; in cron/oneshot it fails
  closed — and oneshot mode has been observed to FAKE approval success. Hub chat
  only.
- Validator BEFORE delegate test. A sub-agent burning iterations on a pack with
  broken frontmatter is wasted work.
- `name` must equal the directory name exactly — `skill_view` resolves by name and a
  mismatch makes the pack unloadable or shadowed.
- Do not hardcode this machine's home path in forged packs — use
  `${HERMES_HOME:-$HOME/.hermes}` / `~`. The secret-scan hard-fails literal
  home-directory paths on purpose (portability + privacy).
- Memory entries are §-delimited (`\n§\n` between entries) — a birth entry that
  breaks the delimiter discipline corrupts the file for the drift detector.
- User preference: 12-hour clock in anything user-facing a forged skill produces.
- Escaping doctrine for recipes: prefer single-quoted shell strings; never
  interpolate untrusted text into a command line — pass via stdin or a temp file.
- The 7-day kanban review card is not optional bookkeeping: the curator prunes at
  7d, and a skill nobody used in its first week is a prime kill candidate.
