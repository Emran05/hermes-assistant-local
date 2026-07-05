---
name: deep-dive
description: "Turn any question into a triangulated, citation-locked research brief: decompose into sub-questions, fan out (local intel + memory BEFORE web), require 2+ independent sources per headline claim with a disconfirming query, output TL;DR/Findings/Contested/Sources/Confidence, then hand 'what to watch' off to watchtower rules. Use for 'research X', 'dig into Y', 'what's the real story on Z', due-diligence, comparisons, and any answer that must survive fact-checking."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Research, Triangulation, FactCheck, Citations, Brief, Watchtower, Memory, WebSearch]
    related_skills: [arxiv, blogwatcher, llm-wiki]
---

# Deep Dive — triangulated, citation-locked research briefs

The flagship analyst move. Any question becomes a brief where **every sentence carries a URL**, the headline claim survived an adversarial disconfirming query, and conclusions keep working after the conversation ends (watchtower hand-off).

**The one hard rule: no URL, no claim.** If you cannot cite it, it does not enter Findings. Never invent a URL — only cite URLs returned by `web_search`, `web_extract`, `/api/intel`, or memory files.

## Scripts

- `scripts/brief_template.py` — `--skeleton "topic"` renders the skeleton; pipe the finished brief to stdin to validate (exit 0 = passes the contract; it refuses briefs with uncited claims or a single-source headline).
- `scripts/linkcheck.py` — pipe the brief in (or pass URLs as args); HEAD/GET-checks every citation with a certifi SSL context. Dead links get cut or moved to Contested — never silently included.

```bash
SKILL_DIR="${HERMES_HOME:-$HOME/.hermes}/skills/research/deep-dive"
python3 "$SKILL_DIR/scripts/brief_template.py" --skeleton "my topic"   # start here
cat /tmp/brief.md | python3 "$SKILL_DIR/scripts/brief_template.py"     # gate before delivering
cat /tmp/brief.md | python3 "$SKILL_DIR/scripts/linkcheck.py"          # cull dead citations
```

## Step 1 — DECOMPOSE

Split the ask into **3–5 sub-questions** (lanes). For each lane write 2 search phrasings with *different vocabulary* (e.g. "mlx-lm latest release" vs "mlx-lm changelog 2026"). Varied phrasing beats repeated phrasing: ddgs rate-limits and near-duplicate queries return near-duplicate results.

Done when: lanes cover the ask with no overlap, each lane has 2 distinct phrasings.

## Step 2 — FAN OUT (cheapest source first)

Order is mandatory — local before web:

1. **Local intel store** (free, instant): `curl -s http://127.0.0.1:7788/api/intel` — hourly AI/social items with real URLs. Anything relevant here is a citable source.
2. **Memory topic files**: `curl -s "http://127.0.0.1:7788/api/memory/list"` then `curl -s "http://127.0.0.1:7788/api/memory/file?name=<file>.md"` for any research-* file on this topic. Prior briefs seed lanes and give you a diff baseline ("what changed since").
3. **Web**: per lane, `web_search` with each phrasing, then `web_extract` the **top 2–3 hits only**. Prefer primary sources (official repo/release page, vendor docs, the actual paper) over aggregators — `web_extract` returns boilerplate on JS-heavy aggregator pages.
4. **Wide topics** (4+ lanes or lanes needing 5+ extracts): `delegate` one sub-agent per lane with the lane question + the no-URL-no-claim rule + "return bullet findings, each with URL". Merge everything yourself in a final synthesis pass — sub-agents gather, you triangulate.

Stagger web queries (don't burst 6 searches in one breath) and keep phrasings varied — ddgs rate limits.

Done when: every lane has ≥2 extracted sources or an explicit "nothing found" note.

## Step 3 — TRIANGULATE (the fact-check core)

- A claim enters Findings **only with 2+ independent sources** (different hosts; a syndicated copy of the same wire story is ONE source).
- For every **HEADLINE** claim, also run the disconfirming query: `<claim> false`, `<claim> debunked`, or `<claim> criticism`. You are looking for the strongest counter-evidence, not confirmation.
- Sources disagree? The claim goes to **Contested** with both links — never silently pick a side.
- Date-sensitive claims (versions, prices, headcounts, "current X") get an explicit staleness caveat with the source's publication date if visible.
- No URL, no claim. A plausible memory of a fact is not a source.

Done when: every Findings bullet has its URLs attached and the headline survived its disconfirming query.

## Step 4 — OUTPUT CONTRACT

Render with `brief_template.py --skeleton`, fill in, then **validate before delivering** (pipe the brief to `brief_template.py`; fix and re-run until exit 0). Run `linkcheck.py` on the final brief; drop or Contest any DEAD link.

- **TL;DR** — ≤7 lines, answer first.
- **Findings** — one claim per bullet, URL(s) on every bullet, main claim tagged `[HEADLINE]` with ≥2 URLs from ≥2 hosts.
- **Contested** — disagreements with both links (or "None").
- **Sources** — every URL with a one-line trust note; ≥2 distinct hosts.
- **Confidence** — High/Medium/Low + one sentence why (include staleness caveat).

**Persist** (skip in mini-dive/drill mode): append the brief as a `§` entry to a topic file via the memory API — etag discipline, writes ONLY through the API (never edit memory files with terminal):

```bash
H=http://127.0.0.1:7788
# read current content + etag (404 body means the file doesn't exist yet)
curl -s "$H/api/memory/file?name=research-<topic>.md"
# new file:
curl -s -X POST $H/api/memory/create -H 'Content-Type: application/json' \
  -d '{"name":"research-<topic>.md","content":"<brief markdown>"}'
# append an entry: new_content = old_content + "\n§\n" + brief  (the \n§\n bytes exactly; entries themselves must not contain a bare §)
curl -s -X POST $H/api/memory/save -H 'Content-Type: application/json' \
  -d '{"name":"research-<topic>.md","content":"<old + \n§\n + new>","base_etag":"<etag from the read>"}'
```
If save returns an etag conflict, re-read and re-append — never force.

**Telegram**: send a summary via `hermes send --to telegram` ONLY if the user explicitly asked to be pinged. Default is silent.

## Step 5 — WATCHTOWER HAND-OFF (the flywheel)

Every "what to watch" conclusion becomes a **proposed** notify-only rule. Dry-run first, then show the user the proposal — add only after they approve:

```bash
# dry-run (never sends, never mutates):
curl -s -X POST http://127.0.0.1:7788/api/watchtower -H 'Content-Type: application/json' \
  -d '{"op":"test_rule","rule":{"type":"rss_keyword","label":"mlx-lm releases","params":{"keywords":["mlx-lm","MLX release"]}}}'
# after user approval, same payload with "op":"add_rule"
```

Rule types: `rss_keyword` (params: `keywords` list ≤10), `ticker_move`/`index_move` (`symbol`, `threshold_pct`, `direction`: any/up/down), `crypto_move` (`coin`, ...), `system_metric` (`metric`: ram_pct/cpu_pct/disk_pct/battery_pct, `op`: >/<, `value`). Optional: `cooldown_min` (5–1440), `channels` (["hub"] and/or ["telegram"] — default both; use `["hub"]` unless the user wants pings).

Done when: each watch bullet has a test_rule dry-run result and an add_rule payload the user can approve.

## Step 6 — SELF-REPORT (mirror check)

End every dive with one honest block:

- tool-call count (searches / extracts / delegates)
- dead links found by linkcheck (and what you did with them)
- recorder window: `curl -s "http://127.0.0.1:7788/api/recorder"` — note the time span covering this dive so the user can audit it

## Mini-dive mode (drills / quick answers)

When asked for a MINI dive or given explicit budgets: obey the budgets exactly (e.g. 2 sub-questions, max 4 `web_extract` total), skip memory persistence and Telegram entirely, output the full brief inline in the same 5-section contract. All hard rules still apply — no URL no claim, ≥2 hosts on the headline, disconfirming check (one query is enough in mini mode).

## Gotchas

- **ddgs rate limits**: stagger queries, vary phrasings; if a search errors, wait a beat and rephrase rather than retrying verbatim.
- **web_extract on JS-heavy pages** returns nav boilerplate — prefer the primary source (GitHub releases, PyPI, official docs) over news aggregators.
- **Framework Python 3.12 lacks SSL root certs** — `linkcheck.py` already uses the certifi-bundle pattern (`ssl.create_default_context(cafile=certifi.where())`, same as dashboard `_ssl_context()`); reuse that pattern in any ad-hoc HTTPS you script here.
- **Memory byte discipline**: entries are joined by the exact bytes `\n§\n`; an entry must never contain a bare `§` line. Writes only via `/api/memory/*` with `base_etag`.
- **Safety envelope**: this skill is read-only on the web, notify-only on watchtower, and writes only through the etag/flock-safe memory API — it runs clean under manual approvals, including inside overnight crons. No destructive terminal ops, ever.
