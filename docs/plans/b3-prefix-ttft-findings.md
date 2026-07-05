# B3 — Prefix-stable prompts & mlx prompt-cache TTFT findings

Date: 2026-07-05 · Model: `mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit` · mlx-lm 0.31.3
Status: analysis + patch proposal. **No shared-file edits applied** — diffs below are for the orchestrator.
Bench script (repeatable): `docs/plans/b3-ttft-bench.py`.

## 1. How the mlx cache actually matches (read from source)

`~/Library/Python/3.12/lib/python/site-packages/mlx_lm/models/cache.py` — `LRUPromptCache`
holds completed sequences in a per-model **token trie**. `fetch_nearest_cache()`
(called per request from `mlx_lm/server.py` line ~753) walks the new prompt's tokens
through the trie and reuses the **longest common token prefix**: exact match → full
reuse; a cached longer sequence → deep-copy + `trim_prompt_cache` down to the common
prefix; a cached shorter exact-prefix → reuse whole. **The first differing token ends
reuse — everything after it is re-prefilled.** Eviction: LRU, capped by
`--prompt-cache-size` (sequences) and `--prompt-cache-bytes`; the byte cap is also
re-enforced *before every request* (`trim_to(total - active)`).

## 2. Where the volatility actually is (token-sequence map of a hub turn)

| Layer (in token order) | Size | Volatile bytes | Verdict |
|---|---|---|---|
| Hermes system prompt — stable tier (SOUL.md, tool/skill guidance) | bulk of ~19.6k tok | none | already prefix-stable |
| Hermes system prompt — context tier (context files from cwd=`~`) | small | none (fixed cwd) | stable |
| Hermes system prompt — volatile tier (memory block, USER.md, `Conversation started: <date>`) | tail of sys prompt | **date-only** (changes daily); memory block changes only on memory writes | acceptable — upstream already fixed this (`agent/system_prompt.py:446-461`, credit PR #20451); sits at the END so a change only busts the last few hundred tokens |
| Session history (serve resends full history) | grows | append-only | prefix-preserving by construction |
| **Our preamble** (`dashboard/server.py:957 access_preamble()`, prepended to the newest user msg at `server.py:2415`) | ~60–200 tok | **`[context] Local time: … %H:%M` is the FIRST line** — changes every minute; tasks + calendar lines mid-block | **the bug** — first byte of every fresh session's first message is volatile |
| Attachments line + user message | varies | inherently volatile | fine (it's the tail) |

Checked and clean: `pass_session_id` defaults False and nothing in the serve/dashboard
path enables it (no per-session ID bytes in the system prompt); `agent.system_prompt`
(ephemeral) is empty in `~/.hermes/config.yaml`; `prompt_caching.cache_ttl` only affects
hosted providers. **No hermes-side config change is needed** — its prompt is already
ordered stable→context→volatile with a date-precision timestamp at the end. The one
inherent cost: one cold prefill per day (date rollover) and after each model restart.

Within a session the preamble ordering doesn't matter (reuse ends where the new
message begins anyway). It matters for **fresh-session first turns** — menubar quick
asks after the 4am session reset, new hub chats, and every `hermes -z` briefing regen
(`server.py:1035`) — where consecutive requests share the whole system prompt via the
trie and then diverge at the preamble's first volatile byte.

## 3. Measurements

### 3.1 Controlled A/B (direct `/v1/chat/completions`, stream, temp 0, max_tokens 16, ~9.3k-token prompt, same total content both orderings)

| Case | Client TTFT | Server-side tokens prefilled |
|---|---|---|
| volatile line FIRST — cold | 3.386 s | 9,291 / 9,291 |
| volatile line FIRST — warm, minute changed | **2.916 s** | **9,266** (only ~14 leading tokens matched) |
| volatile line LAST — cold | 3.000 s | 9,288 / 9,288 |
| volatile line LAST — warm, minute changed | **0.260 s** | **15** (~9,273 reused) |
| exact repeat (ceiling) | 0.167 s | 1 |

Same content, same cache, same server — ordering alone is an **11× TTFT difference**
(2.92 s → 0.26 s) at 9.3k tokens. One changed minute at token ~14 threw away 9,252
cached tokens.

### 3.2 Real-world ground truth (`~/.hermes/logs/mlx-server.log`, live traffic today)

- Full agent prompt is **19,622 tokens**; cold prefill took **~8.2 s** (~2,390 tok/s).
- Warm turns in the same session prefilled only **27–290 tokens** → sub-second prefill.
  So hermes-level prefix reuse is *already working* when nothing busts it.
- Each cached ~20k-token sequence costs **1.93–2.15 GB** (~98 KB/token KV);
  my 9.3k bench sequences were ~0.92 GB each. Cache log peaked at 5.81 GB for 5 entries.
- Dashboard `/api/metrics`: serve-path TTFT p50 = 2,546 ms (n=1, warm-ish turn).

### 3.3 The sizing problem this exposes

`--prompt-cache-bytes 6000000000` holds only **~3 agent-sized sequences** — but at
least four producers insert them (hub chats, menubar session, briefing `-z` regens,
watchtower/intel runs). LRU churn evicts a still-hot 19.6k prefix, and the next turn
pays the full ~8.2 s cold prefill instead of ~0.3 s. `--prompt-cache-size 6` is
unreachable at 6 GB, so the byte cap is the binding (and thrashing) constraint.
Constraint the other way: `memory_guard_loop` (`server.py:1076`) hard-restarts the
model server above **32 GB** footprint (wiping the cache), and the model is ~18 GB —
so the cache budget must stay ≲ 10 GB with headroom. 8 GB ⇒ 4 sequences, ~26 GB
steady-state footprint, safe margin.

## 4. Patch proposal (diffs — NOT applied)

### 4.1 `dashboard/server.py` — reorder `access_preamble()`: stable first, volatile last

```diff
--- a/dashboard/server.py
+++ b/dashboard/server.py
@@ def access_preamble():
 def access_preamble():
     dirs = get_access()["dirs"]
     now = time.strftime("%A %Y-%m-%d %H:%M %Z")
+    # Prefix-stability (P3.B3): the mlx prompt cache reuses the longest
+    # common TOKEN PREFIX between requests — the first changed byte busts
+    # every token after it.  Stable lines therefore go FIRST and volatile
+    # lines LAST, ordered least→most volatile (grants, tasks, calendar,
+    # wall-clock minute).  Measured on the local Qwen3-30B server: with the
+    # time line first, a minute tick re-prefilled 9,266 of ~9,280 tokens
+    # (TTFT 2.92s); with it last, 15 tokens (TTFT 0.26s).  See
+    # docs/plans/b3-prefix-ttft-findings.md.
     lines = [
-        f"[context] Local time: {now}.",
         f"[context] Files the user drops in chat land in: {INBOX}",
     ]
     if dirs:
@@ def access_preamble():
             "[context] The user has not granted any folder access yet; don't "
             "browse their files. They can grant folders in the dashboard sidebar."
         )
+    lines.append(
+        "[context] Never invent facts, events, or files you did not actually "
+        "read or verify. If you don't know, say so plainly."
+    )
     tasks = [t for t in get_tasks()["tasks"] if not t.get("done")][:10]
     if tasks:
         lines.append("[context] The user's open tasks (from their dashboard task "
                      "list): " + "; ".join(t["text"] for t in tasks))
     cal = macos_calendar()
     if cal.get("available") and cal.get("events"):
         lines.append("[context] Today's events from the user's macOS Calendar: "
                      + "; ".join(f"{e['time']} {e['title']}".strip()
                                  for e in cal["events"]))
-    lines.append(
-        "[context] Never invent facts, events, or files you did not actually "
-        "read or verify. If you don't know, say so plainly."
-    )
+    lines.append(f"[context] Local time: {now}.")
     return "\n".join(lines) + "\n\n"
```

Semantics preserved: same lines, same wording; only order changes (grants → invariant
→ tasks → calendar → time). The model still gets the current time every turn.

### 4.2 `mlx-server.sh` — make the sequence cap reachable without tripping memory_guard

```diff
--- a/mlx-server.sh
+++ b/mlx-server.sh
@@
   --max-tokens 4096 \
   --prompt-cache-size 6 \
-  --prompt-cache-bytes 6000000000 \
+  --prompt-cache-bytes 8000000000 \
   --trust-remote-code
 # --prompt-cache-* CAPS the in-memory KV/prompt cache so it can't grow
 # unbounded and thrash RAM (root cause of the 49GB blowup; the dashboard's
-# memory_guard is now just a backstop). 6GB is plenty of prefix-reuse headroom
-# for a handful of active sessions. "Resume later" is handled by Hermes's own
+# memory_guard is now just a backstop). Measured (B3): each ~20k-token agent
+# sequence costs ~2GB of KV, so 6GB held only ~3 sequences while >=4 producers
+# (hub chats, menubar, briefing -z, watchtower) insert them — LRU churn was
+# re-paying the ~8s cold prefill. 8GB holds 4; footprint ~26GB stays under the
+# 32GB memory_guard restart line. Drop back to 6GB if you switch to
+# GLM-4.5-Air (the 106B model leaves no headroom). "Resume later" is Hermes's
 # message-history restore (state.db) — KV-cache-to-disk isn't worth it here.
```

### 4.3 Hermes-side config

**No change proposed.** Verified: system prompt already stable→volatile with date-only
timestamp at the tail; `pass_session_id` off; ephemeral system prompt empty. Nothing
config-pinnable would improve prefix stability further without upstream edits.

### 4.4 Optional follow-up (not in this patch)

`_generate_briefing()` sends `access_preamble() + BRIEFING_PROMPT`; the ~230-token
briefing prompt sits after the volatile lines, so every regen re-prefills it. Swapping
to `BRIEFING_PROMPT`-first (or a briefing-specific stable preamble) would save another
~250 tokens/regen (~0.1 s) — marginal; skip unless touching that code anyway.

## 5. Expected real-world win

- **Honest framing:** the community's "31s→3.4s" class of win comes from system-prompt
  prefix reuse, which hermes upstream already implements and which we confirmed live
  (19.6k cold = 8.2 s → warm turns prefill 27–290 tok, sub-second).
- **Preamble reorder (4.1):** extends the shared prefix of every fresh-session first
  turn / briefing regen past the preamble's stable lines instead of dying at token ~14
  of the message. Direct saving is modest (~100–250 tok ≈ 0.1 s/turn), but it removes
  the one volatile-first pattern in our stack and codifies the invariant — the A/B
  shows the failure mode costs 2.9 s at 9.3k tokens (≈8 s at real 19.6k size) if this
  layout ever migrates into a system-level block.
- **Cache-bytes bump (4.2):** the bigger lever. Every LRU eviction of a hot agent
  prefix costs a full ~8.2 s re-prefill on the next turn; 8 GB (4 sequences vs 3)
  covers the actual producer set, so fresh-session TTFT stays in the 0.3–2.5 s band
  instead of intermittently regressing to ~8–9 s.
