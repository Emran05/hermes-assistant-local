# Clipboard Actions — design spec (P2)

**Workstream:** P2.3 (DEVPLAN Phase 2 #4, second half — "clipboard-aware actions").
**Owns:** `dashboard/aux_clip.py`, `dashboard/aux_clip.js`, one `<script>` line in `index.html`.
**Coordinates with:** P2.2 (menu-bar quick-ask) — shares the popover surface and the
Swift NSPasteboard read bridge. See **Integration points → P2.2 dependency**.

One-line: let the user run fast, purely-local text transforms (summarize / explain /
translate / rewrite / extract / proofread) on whatever is on the clipboard, show the
result inline, optionally copy it back — clipboard bytes never leave the machine and the
transform path structurally cannot call a tool, write a file, or send anything.

---

## Design decision (read this first): transforms are direct-to-model, NOT through the agent

The brief says "act on clipboard contents via the agent." Taken literally that means the
serve/hub chat path (`hermes_rpc.run_turn`). We deliberately do **not** route transforms
through it, for three grounded reasons:

1. **These are pure text-in / text-out transforms.** They need no tools, no filesystem,
   no calendar, no memory. The agent loop adds latency (session.create → prompt.submit →
   events) and, worse, *tool-call surface* for zero benefit.
2. **Safety by construction.** A direct `/v1/chat/completions` call to the local MLX server
   with **no `tools` field** cannot emit an `approval.request`, cannot touch
   `permissions.py`, cannot write, cannot send. The safety story is "the capability does
   not exist," not "we remembered to deny it" — the same posture as Gmail-no-send.
3. **Local-first is trivially provable.** The only socket the transform opens is
   `127.0.0.1:8080` (the MLX server, verified OpenAI-compatible below). Grep the module:
   one `urllib.request.urlopen` to loopback, nothing else.

The escape hatch to the *real* agent is one explicit click ("Open in chat"), which reuses
the existing `askAbout()` contextual-ask path — that, and only that, goes through the
approval-gated serve backend. So: **transforms = capability-free local inference;
escalation = existing agent path.** Nothing in between.

Verified live (2026-07-05) — the MLX server answers a no-tools chat completion:
```
$ curl -s http://127.0.0.1:8080/v1/chat/completions -d '{"model":"…Qwen3-30B-A3B…",
    "messages":[{"role":"system","content":"Reply with exactly: OK"},
                {"role":"user","content":"test"}],"max_tokens":8,"temperature":0}'
{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"OK"}}],
 "usage":{"prompt_tokens":19,"completion_tokens":2,…}}
```

---

## Goal & acceptance criteria (done means…)

1. **Transform works end-to-end, locally.** With text on the clipboard I open the clipboard
   sheet in the dashboard, pick "Summarize," and a summary streams/renders inline in < 4s
   (p50), produced by the active local model — with the network fully off except loopback.
2. **The six actions all run:** summarize, explain, translate (target language selectable),
   rewrite (tone/format selectable), extract (tasks / dates / emails / links / key-points),
   proofread. Each returns non-empty, on-task output for a representative input.
3. **Read is edge-only, three-tier, never server-side.** Clipboard text is read by (a) the
   Swift NSPasteboard bridge when running in the app, else (b) `navigator.clipboard.readText()`
   on localhost, else (c) a manual paste box — and reaches the backend only in the POST body.
   `grep -n "pbpaste\|clipboard\|NSPasteboard" dashboard/*.py` returns nothing (server never
   reads the clipboard).
4. **Copy-back is explicit and reversible.** The result is copied to the clipboard only when
   I click "Copy," via the frontend/Swift — never automatically, never server-side.
5. **Refuses cleanly:** empty clipboard → "Nothing to act on"; > `CLIP_MAX_CHARS` → "Selection
   too long, trim it"; model offline / paused → "The local model is offline — clipboard
   actions need it"; non-UTF-8 / binary → "That doesn't look like text." No stack traces reach
   the UI; a handler exception still returns a JSON `{ok:false,error}` (the aux dispatcher
   guarantees this).
6. **Capability-free is verifiable.** `POST /api/clip/run` never appears in the flight
   recorder (`recorder.db`), never appears in `permissions-log.jsonl`, and issues zero
   approvals — because it makes zero tool calls. A code read shows no `tools=` key, no
   `write_*`, no `hermes send`, no subprocess in the transform path.
7. **Escalation is one click and goes through the governed path.** "Open in chat" injects the
   clipboard text verbatim into the composer via `askAbout()` and the subsequent turn runs
   through `hermes_rpc.run_turn` with normal approvals — no bypass.
8. **Menu-bar parity contract exists.** The same `POST /api/clip/run` serves the P2.2 menu-bar
   popover; the request/response contract in this doc is what P2.2 codes against (no second
   endpoint, no divergence).

---

## Data model (files / JSON — exact shapes)

Clipboard content is **never persisted to disk** (privacy invariant — clipboards carry
passwords, tokens, PII). The feature is stateless except a tiny settings block.

### 1. Action catalog — static, in `aux_clip.py` (no file)

```python
CLIP_ACTIONS = {
  "summarize": {
    "label": "Summarize", "opts": [],
    "temperature": 0.4, "max_tokens": 512,
    "system": ("You are a precise summarizer. Summarize the user's text in tight, "
               "skimmable form: a one-line gist then 2–5 bullet points. Preserve names, "
               "numbers, and decisions. Do not add facts that are not in the text. Output "
               "only the summary, no preamble."),
  },
  "explain": {
    "label": "Explain", "opts": [],
    "temperature": 0.4, "max_tokens": 700,
    "system": ("Explain the user's text plainly for a smart non-expert. Define jargon, spell "
               "out what it means and why it matters, in a few short paragraphs. If it is code, "
               "explain what it does step by step. Only use information present in the text. "
               "Output only the explanation."),
  },
  "translate": {
    "label": "Translate",
    "opts": [{"id": "to", "label": "Into", "type": "lang", "default": "English"}],
    "temperature": 0.2, "max_tokens": 1500,
    "system": ("You are a faithful translator. Translate the user's text into {to}. Preserve "
               "meaning, tone, names, and formatting (lists, line breaks). Do not summarize, "
               "explain, or add notes. If the text is already in {to}, return it unchanged. "
               "Output only the translation."),
  },
  "rewrite": {
    "label": "Rewrite",
    "opts": [
      {"id": "tone", "label": "Tone", "type": "choice",
       "choices": ["clearer", "more concise", "more formal", "friendlier",
                   "more assertive", "simpler"], "default": "clearer"},
      {"id": "format", "label": "As", "type": "choice",
       "choices": ["prose", "bullet points", "an email", "a message"], "default": "prose"},
    ],
    "temperature": 0.5, "max_tokens": 1200,
    "system": ("Rewrite the user's text to be {tone}, formatted as {format}. Keep the original "
               "meaning and all facts; change wording, not substance. Do not invent details. "
               "Output only the rewritten text."),
  },
  "extract": {
    "label": "Extract",
    "opts": [{"id": "what", "label": "Pull out", "type": "choice",
              "choices": ["action items", "key points", "dates & times",
                          "names & entities", "emails & links", "numbers & figures"],
              "default": "action items"}],
    "temperature": 0.2, "max_tokens": 700,
    "system": ("Extract the {what} from the user's text as a clean list. Include only items "
               "that actually appear in the text — never guess or infer beyond it. If there "
               "are none, output exactly: (none found). Output only the list."),
  },
  "proofread": {
    "label": "Proofread", "opts": [],
    "temperature": 0.2, "max_tokens": 1400,
    "system": ("Correct spelling, grammar, and punctuation in the user's text. Preserve the "
               "author's voice, meaning, and formatting; do not rewrite for style or add "
               "content. Return the corrected text only — no list of changes, no commentary."),
  },
}
CLIP_ACTION_ORDER = ["summarize", "explain", "rewrite", "proofread", "translate", "extract"]
```

`{to}`/`{tone}`/`{format}`/`{what}` are filled from validated opts (whitelist for `choice`,
length-capped free string for `lang`). Unknown opt values fall back to the declared default.

### 2. Settings block — rides existing `~/.hermes/dashboard/settings.json` via `/api/settings`

```jsonc
// settings.json (only the clip sub-object shown; server.py get_settings()/POST /api/settings)
{
  "clip": {
    "enabled": true,             // false hides the launcher + disables the endpoint (503)
    "default_translate_to": "English",
    "last_action": "summarize",  // UX convenience, restored on open
    "copyback_confirm": false    // if true, "Copy" asks first (default off; copy is reversible)
  }
}
```

Frontend also mirrors `last_action`/last opts in `localStorage["hermes_clip"]` for instant
restore without a settings round-trip. No clipboard text is ever stored in either.

### 3. No new database, no recorder rows, no snapshot

Transforms are read-only inference — they are intentionally **absent** from `recorder.db`
(aux_recorder only logs tool calls) and from `permissions-log.jsonl`. This absence is an
acceptance check (#6), not an oversight.

---

## Backend

New file `dashboard/aux_clip.py`, exec-loaded into `server.py` globals by the aux loader
(after `aux_permissions.py` alphabetically — `aux_clip` sorts before it, so it loads before
permissions; that is fine, it uses none of permissions' names). It imports its own stdlib
(`json, time, os, sys, re, urllib.request, urllib.error`) and defines only `CLIP_*` / `_clip_*`
names. May use server globals: `HOME, DATA, MODEL_URL, model_online, active_model, get_settings,
_cached, read_json`.

### Endpoint 1 — `POST /api/clip/run`  (the transform)

Request body:
```jsonc
{
  "action": "summarize",              // required, must be a key of CLIP_ACTIONS
  "text":   "…clipboard contents…",   // required, the ONLY place clipboard text travels
  "opts":   { "to": "French" },       // optional, per-action (see catalog)
  "source": "dashboard"               // optional tag: "dashboard" | "menubar" (telemetry only)
}
```

Response 200:
```jsonc
{
  "ok": true,
  "action": "summarize",
  "result": "…model output…",
  "model": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
  "ms": 2841,                         // wall time of the inference
  "in_chars": 1203, "out_chars": 384,
  "truncated_input": false            // true if text was clamped to CLIP_MAX_CHARS
}
```

Errors (all as `(dict, status)` tuples — the aux dispatcher renders them):
| status | body `error` | when |
|--------|--------------|------|
| 400 | `bad_action` | action not in `CLIP_ACTIONS` |
| 400 | `empty` | `text` missing/blank after strip |
| 400 | `not_text` | `text` not a `str` / not decodable as UTF-8 |
| 413 | `too_long` (+`limit`,`got`) | `len(text) > CLIP_MAX_CHARS` |
| 503 | `model_offline` | `model_online()` is false (server booted-out / paused) |
| 503 | `disabled` | `settings.clip.enabled` is false |
| 502 | `model_error` (+`detail`) | MLX call non-200 / bad JSON / timeout |
| 500 | `internal: …` | anything else (dispatcher also backstops this) |

Handler logic (`_clip_run_handler(ctx)`):
```
1. s = get_settings().get("clip") or {}; if s.get("enabled") is False → (…,"disabled",503)
2. b = ctx.body or {}; action = b.get("action"); if action not in CLIP_ACTIONS → 400 bad_action
3. text = b.get("text"); if not isinstance(text,str) → 400 not_text
   text = text.replace("\x00","").strip(); if not text → 400 empty
   try text.encode("utf-8") else → 400 not_text
   truncated = len(text) > CLIP_MAX_CHARS; text = text[:CLIP_MAX_CHARS]
4. if not model_online() → 503 model_offline
5. spec = CLIP_ACTIONS[action]; sys_prompt = _clip_fill(spec, b.get("opts"))   # opts validated
6. body = {"model": active_model(),
           "messages": [{"role":"system","content":sys_prompt},
                        {"role":"user","content":text}],
           "max_tokens": spec["max_tokens"], "temperature": spec["temperature"],
           "stream": False}
   # NOTE: no "tools" key, ever — this is the structural no-tool guarantee.
7. t0=time.time(); out = _clip_complete(body)   # urllib POST to CLIP_URL, 60s timeout
   on urllib/timeout/JSON error → 502 model_error, detail truncated
8. result = out["choices"][0]["message"]["content"].strip()
   return {ok:True, action, result, model:body["model"], ms:int((time.time()-t0)*1000),
           in_chars:len(text), out_chars:len(result), truncated_input:truncated}
```

`CLIP_URL` = `MODEL_URL.replace("/v1/models","/v1/chat/completions")` (falls back to
`http://127.0.0.1:8080/v1/chat/completions`). `_clip_complete` uses `urllib.request` with
`Content-Type: application/json`, `timeout=CLIP_TIMEOUT` (60), and does **no** custom SSL
context (loopback, plain http — matches how `model_online()` hits `MODEL_URL`).

`_clip_fill(spec, opts)`: validate each declared opt; `choice` type must be in `choices` else
default; `lang` type coerced to `str`, stripped, `[:40]`, non-empty else default; then
`spec["system"].format(**validated)`. Never lets a user string reach `.format`'s field slots
(only the values are substituted; the template's `{…}` keys are fixed).

### Endpoint 2 — `GET /api/clip/actions`  (catalog for the UI)

Returns the render-safe catalog + current settings so the frontend needs no hardcoded copy:
```jsonc
{ "ok": true, "enabled": true, "order": ["summarize", …],
  "actions": { "summarize": {"label":"Summarize","opts":[]}, "translate": {"label":"Translate",
      "opts":[{"id":"to","label":"Into","type":"lang","default":"English"}]}, … },
  "defaults": { "default_translate_to": "English", "last_action": "summarize" } }
```
System prompts are **not** included (no need on the client; keeps templates server-owned).
Cached with `_cached("clip_actions", 10, …)` since it only changes when settings change.

### Background threads

**None.** Clipboard actions are request-scoped. No module-load thread, no timer, no
prewarm — nothing to guard with a `_thread_started` flag. (Contrast aux_recorder, which does.)

### How it respects `permissions.py`

It does not need to, and that is the point. `POST /api/clip/run` performs a **read-only local
inference with no `tools` array**, so the model cannot request a tool, so no
`approval.request` is ever produced, so `decide()` is never reached. The permission engine
governs tool calls on the serve path; this endpoint is off that path entirely. The single
governed action a user can take from the clipboard UI — "Open in chat" — flows through the
normal `/api/chat` → `run_turn` seam where `permissions.py` is enforced exactly as today. The
spec's **NOTIFY-ONLY** boundary is honored structurally: the clipboard feature shows results
and offers a manual copy; it never *acts* (no task creation, no draft, no send) without the
user crossing into the approval-gated agent path.

---

## Frontend

New file `dashboard/aux_clip.js`, auto-served at `/aux_clip.js`. **The one allowed index.html
edit** (applied by the orchestrator) is a single line after the other aux scripts:
```html
<script src="/aux_clip.js"></script>   <!-- after aux_config.js (line ~2055) -->
```
Everything else is self-injected DOM + wrapped hooks, per the aux_metrics.js precedent
(`typeof esc`, `typeof animate`, `REDUCE` all guarded so the headless render harness can eval
it with stubs).

### Surface & UX

A **Liquid Glass command sheet** (`#clip-sheet`, `position:fixed`, centered, backdrop scrim),
opened three ways, all wired by aux_clip.js at load:
- a compact launcher pill it injects into the composer toolbar (bespoke two-tone clipboard SVG,
  no emoji), and
- a global shortcut **⌘⇧V** (registered via `document.addEventListener('keydown', …)`, guarded
  so it never fires while typing in an input/textarea unless the sheet is open), and
- (in-app) the P2.2 menu-bar item, which opens the same sheet via a Swift→JS bridge call
  `window.hermesClip && hermesClip.open()` (aux_clip.js exposes `hermesClip.open/close`).

Sheet layout, top → bottom:
1. **Source line:** "From clipboard" + a live char count, or the manual-paste textarea when the
   read fell back (see read tiers). A small "re-read clipboard" refresh control.
2. **Action chips:** the six actions from `/api/clip/actions`, keyboard-navigable; the last-used
   one preselected. Selecting an action with opts reveals a compact opts row (a `<select>` for
   `choice`, a small combobox/text for `lang`).
3. **Result pane:** empty → hint; running → shimmer + "Thinking…"; done → the result in a
   readable mono/prose block. `animate()` (Motion One) fades/height-expands it in
   (REDUCE-motion: instant).
4. **Actions row:** **Copy** (writes result back — see copy tier), **Re-run**, **Open in chat**
   (escalation), **Close**. Copy shows a 1s "Copied" tick.

### Reading the clipboard — three tiers (never server-side)

`clipRead()` in aux_clip.js resolves the text, best source first:
1. **App bridge (most reliable, no prompt):** if `window.webkit?.messageHandlers?.hermesClip`
   exists, call it; Swift reads `NSPasteboard.general.string(forType:.string)` and returns it
   (via a completion or a `window.__clipInbox` assignment). This is the in-app path and needs
   the P2.2 Swift change (see dependency). No TCC prompt — the active signed app may read the
   pasteboard.
2. **Async Clipboard API:** `await navigator.clipboard.readText()`. `http://127.0.0.1` is a
   secure context, so this is available on user gesture in a browser tab and in WKWebView after
   activation. Wrapped in try/catch.
3. **Manual paste:** if both fail/deny, show a focused textarea with "Paste here (⌘V)"; its
   value is the text. Always works, zero permissions.

Whatever tier wins, the text goes only into the `POST /api/clip/run` body. It is held in a JS
variable for the sheet's lifetime and dropped on close (not stashed in localStorage).

### Copy-back — two tiers, explicit only

`clipWrite(text)` on the **Copy** click: prefer the Swift bridge
(`window.webkit.messageHandlers.hermesClipWrite`) → `NSPasteboard.general.setString`; else
`await navigator.clipboard.writeText(text)`; else select-the-result-and-⌘C fallback. If
`settings.clip.copyback_confirm` is true, `confirm()` first (works in-app via the existing
WKWebView confirm sheet). Never fires without the click.

### Escalation — reuse existing contextual ask

**Open in chat** calls the existing global `askAbout(clipText, /*verbatim*/ true)` (defined in
index.html, confirmed at line 1371) — but prefixed to preserve intent, e.g.
`askAbout('Regarding this clipboard text, ' + result_or_source, true)` isn't needed; simplest is
`askAbout(clipText, false)` which yields `Regarding "<text>" — ` in the composer, then the user
finishes the ask. This drops the sheet and focuses the composer; the subsequent send is a normal
governed turn. (No new backend for escalation — it is literally the current path.)

### States & animations
- **loading actions:** skeleton chips (10s cache means usually instant).
- **reading clipboard:** subtle spinner on the source line; on empty → inline "Nothing on the
  clipboard yet."
- **running:** action chip pulses; result pane shimmer; Copy/Re-run disabled.
- **error:** the endpoint's friendly `error` mapped to a one-line human message in the result
  pane (red hairline), never a raw trace.
- All motion via `animate()` with a `REDUCE` guard; glass follows the CLAUDE.md budget (one
  blurred layer for the sheet, `translateZ(0)`), 12-hour time if any timestamp is shown, zero
  emoji (bespoke SVG icons only).

---

## Integration points (verified names/files)

Confirmed present by grep on 2026-07-05:

| Name | File:loc | Use |
|------|----------|-----|
| `register_get` / `register_post` | server.py:2043 / 2047 | register `/api/clip/*` |
| `RouteCtx.q1` / `.body` | server.py:2060 / 2057 | handler arg |
| aux exec loader (`aux_*.py`, sorted) | server.py:2071–2083 | loads `aux_clip.py` |
| aux `.js` static serve (`/aux_*.js`) | server.py:2126–2141 | serves `/aux_clip.js` |
| `model_online()` | server.py:938 | offline guard |
| `active_model()` | server.py:1938 | `model` field of the completion |
| `MODEL_URL` (`…:8080/v1/models`) | server.py:54 | derive `CLIP_URL` |
| `_cached` | server.py:1169 | cache `/api/clip/actions` |
| `get_settings()` / `SETTINGS_FILE` / `POST /api/settings` | server.py:1158 / 1155 / 2320 | `clip` block |
| `DATA`, `HOME`, `HERE`, `read_json` | server.py:44–46,70 | paths |
| `askAbout(context, verbatim)` | index.html:1371 | escalation to agent |
| aux JS hook-wrap precedent | aux_metrics.js:18–22 (`window.loadConsole`) | injection pattern |
| aux script tags block | index.html:2050–2055 | where the one `<script>` line lands |
| `/api/chat` POST → `run_turn` | server.py:2394 / hermes_rpc | governed escalation path |
| `hermes send --to telegram` | ~/.hermes/hermes-agent/hermes_cli/send_cmd.py | NOT used here (clipboard never sends) |

### P2.2 dependency (menu-bar quick-ask) — the shared seam

- **Popover surface:** P2.2 adds the `NSStatusItem` + popover (a small WKWebView or native
  view). If it embeds a WebView on the same `127.0.0.1:7788` origin, it loads `/aux_clip.js`
  and calls `hermesClip.open()` — zero extra backend. This spec's `POST /api/clip/run` is the
  single shared endpoint; **P2.2 must not add a second one.**
- **Swift NSPasteboard bridge (this is the coordination point):** P2.2 is already editing
  `app/main.swift` (currently 241 lines, no `NSStatusItem`/`NSPasteboard` yet — confirmed).
  Add two `WKScriptMessageHandler`s to the shared `WKWebViewConfiguration.userContentController`:
  - `hermesClip` → replies with `NSPasteboard.general.string(forType: .string) ?? ""`
    (read tier 1);
  - `hermesClipWrite` → `NSPasteboard.general.clearContents(); setString(msg, forType:.string)`
    (copy tier 1).
  Both are ~15 lines total, no new entitlement, no TCC prompt. aux_clip.js already probes for
  their existence and degrades to `navigator.clipboard` when absent (so the dashboard works
  before the app change ships — the two workstreams are decoupled at runtime).
- **Ownership:** aux_clip.py/js and the endpoint contract are P2.3's; the NSStatusItem, the
  hotkey registration at the OS level, and the two message handlers are P2.2's Swift work,
  built against this contract.

---

## Edge cases & failure modes (exhaustive)

- **Empty clipboard / whitespace-only** → tier read yields ""; sheet shows "Nothing on the
  clipboard yet," endpoint never called.
- **Huge clipboard (e.g. a whole file copied)** → clamp to `CLIP_MAX_CHARS` (16000, ~5k tokens);
  backend sets `truncated_input:true`; UI shows "Acting on the first 16k characters." Never
  OOM the model with an unbounded prompt.
- **Binary / image / non-UTF-8 on the clipboard** → tier reads may return non-string or garbled
  text; `navigator.clipboard.readText()` yields "" for images; backend `not_text`/`empty`.
- **Model offline** (server booted-out via pause/resume, or mid model-switch) → `model_online()`
  false → 503 `model_offline`; UI: "The local model is offline — start it from the model menu."
  (Agent-paused specifically boots out the mlx-server, so this branch covers it too.)
- **Model errors / times out** (30B busy on another turn, 60s exceeded) → 502 `model_error`;
  UI: "The model is busy or slow — try again." Re-run enabled.
- **Concurrent transform + a chat turn** → both hit the same single MLX server; requests queue
  serially in mlx-lm. Acceptable; the 60s timeout bounds the wait. (Batching is a LATER perf
  item per DEVPLAN; not needed here.)
- **Model returns tool-call-shaped text anyway** (Qwen sometimes emits `<tool_call>` tokens
  unprompted) → we passed no `tools`, so it is just text; we return it verbatim. It cannot
  *execute* anything. (Optional nicety: strip a leading `<tool_call>…</tool_call>` block before
  returning; low priority.)
- **Opts tampering** (client sends `{"to": "<script>"}` or `tone:"evil"`) → `choice` validated
  against whitelist, `lang` coerced/capped; worst case the value lands as plain text inside the
  system prompt (no code path), and the template keys are fixed so `.format` cannot be abused.
- **`.format` KeyError** from a stray `{}` in a lang string → we only `.format(**validated)` a
  template with known keys; user values are the *arguments*, not the format string, so a `{` in
  the language name is inert. (If we ever interpolate user text into the template body, switch to
  explicit `.replace` — noted so a future edit doesn't regress this.)
- **WKWebView denies `navigator.clipboard`** → tier 3 manual paste; feature still works.
- **Browser tab (not the app) with clipboard permission denied** → same tier-3 fallback.
- **`settings.clip.enabled=false`** → launcher hidden, hotkey no-ops, endpoint 503 `disabled`.
- **aux_clip.py raises at import** → the server.py aux loader catches and logs
  `[aux_clip.py] failed to load` (server.py:2081) and the hub still comes up without the feature.
- **aux_clip.js throws at eval** → wrapped in an IIFE with try/catch around DOM injection so a
  throw cannot break the rest of index.html (aux_metrics.js precedent).
- **Result too long to copy comfortably** → Copy still copies the full string; UI truncates the
  *display* with a "show more," never the copied payload.

---

## Security & safety (every invariant)

- **Local-first / clipboard never leaves the machine.** The only outbound socket is
  `127.0.0.1:8080`. No `_ssl_context`, no external host, no `hermes send`, no telemetry. Grep
  proof is an acceptance check.
- **No persistence of clipboard content.** Nothing writes the text or result to disk; not to
  recorder.db, not to settings.json, not to localStorage. Sensitive-by-default handling.
- **No secrets handling.** We do not parse, detect, or special-case credentials — we simply
  never store or transmit clipboard bytes off-box, which covers the "user copied a password"
  case without us touching it.
- **Gmail/Telegram/send invariants untouched.** This feature has no send capability of any kind;
  "Draft an email" style outcomes are only reachable by escalating into the agent path, where the
  existing Gmail-read+draft-only / Telegram-locked / manual-approvals rules already hold.
- **Approvals & permission tiers.** The transform path emits no `approval.request` and never
  calls `decide()`; it cannot bypass approvals because it never needs one. Escalation uses the
  unchanged governed seam.
- **NOTIFY-ONLY boundary (proactive-action rule).** Clipboard actions are *reactive* (user
  invokes them) and *inert* (they show text, optionally copy on click). They never auto-act,
  never create tasks/drafts/sends on their own. Any state-changing outcome requires the user to
  cross into the approval-gated agent — the same boundary Watchtower (P2.1) honors.
- **What it refuses:** non-text, oversized, offline, disabled — all fail closed with a friendly
  message and no side effect. There is no `--yolo`, no tool enablement, no filesystem reach.
- **Input hardening:** opts whitelisted, lang capped, NUL stripped, size bounded, UTF-8 enforced,
  template keys fixed. `SESSION_RE` still guards the escalation path (unchanged `/api/chat`).

---

## Test plan (exact commands + expected; no user spam, no --yolo)

All against the running dashboard (`127.0.0.1:7788`) and local model (`:8080`). None of these
send a message, write a file, or hit the network beyond loopback.

1. **Catalog:**
   ```
   curl -s localhost:7788/api/clip/actions | python3 -m json.tool
   ```
   Expect `ok:true`, `order` length 6, each action has `label`, translate/rewrite/extract carry
   `opts`. No `system` field leaks.

2. **Summarize (happy path):**
   ```
   curl -s localhost:7788/api/clip/run -H 'Content-Type: application/json' -d '{"action":"summarize",
     "text":"Our Q3 review is Thursday 3pm. Alice owns the deck, Bob the budget. Ship by Friday."}'
   ```
   Expect `ok:true`, non-empty `result` mentioning Thursday/Alice/Bob, `model` = active model,
   `ms` present, `truncated_input:false`.

3. **Translate with opt:**
   ```
   curl -s localhost:7788/api/clip/run -d '{"action":"translate","opts":{"to":"French"},
     "text":"Good morning, the meeting is at noon."}'
   ```
   Expect French text in `result`, no English commentary.

4. **Extract:**
   ```
   curl -s localhost:7788/api/clip/run -d '{"action":"extract","opts":{"what":"dates & times"},
     "text":"Call Monday 9am, review 2026-07-10, dentist next Tuesday."}'
   ```
   Expect a list of the dates; nothing invented.

5. **Guards (each one command, expect the mapped error + status):**
   ```
   curl -s -o /dev/null -w '%{http_code}\n' localhost:7788/api/clip/run -d '{"action":"nope","text":"x"}'   # 400
   curl -s localhost:7788/api/clip/run -d '{"action":"summarize","text":"   "}'                              # 400 empty
   curl -s localhost:7788/api/clip/run -d "{\"action\":\"summarize\",\"text\":\"$(python3 -c 'print("a"*20000)')\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin))'   # 413 too_long OR ok+truncated per final policy
   ```
   (Policy note: > limit returns **413 too_long** for the raw endpoint; the *UI* truncates and
   sends `[:limit]` so a user never sees the 413 — pick one and keep the test aligned. Spec
   default: endpoint 413, UI pre-truncates → so drive this test with `limit+1` to see 413.)

6. **Model-offline branch (no user impact):** pause the model from the UI (or
   `launchctl bootout gui/$(id -u)/com.hermes.mlx-server`), then run #2 → expect `503
   model_offline`; resume after.

7. **Capability-free proof (the safety check):**
   ```
   grep -nE 'tools|write_file|hermes send|subprocess|smtp|approval' dashboard/aux_clip.py   # expect: nothing
   # run a transform, then:
   sqlite3 ~/.hermes/dashboard/recorder.db "select count(*) from actions where tool='clip' or source='menubar';"  # 0
   tail -5 ~/.hermes/dashboard/permissions-log.jsonl   # no clip entries
   ```

8. **Frontend headless render (no browser needed):** node-eval `aux_clip.js` with stubbed
   `esc/animate/REDUCE/document` (the harness CLAUDE.md prescribes for expand.js/aux JS) and a
   canned `/api/clip/actions` payload; assert the sheet builds and each chip renders without
   throwing (catches the esc-on-null / undefined-opt class).

9. **In-app manual pass (once, no external effect):** ⌘R the app, ⌘⇧V, pick Summarize on real
   clipboard text, verify inline result, click Copy, paste elsewhere to confirm copy-back.

Nothing here messages the user or the agent. The escalation path is exercised only by clicking
"Open in chat" manually (item 9), which stops at the composer — it does not auto-send.

---

## Effort & sequencing + dependencies + open questions

**Effort:** S (DEVPLAN scored it S, Value 4 / Diff 2). Backend `aux_clip.py` ~150 lines; frontend
`aux_clip.js` ~350 lines; one index.html script line. No new deps, no DB, no thread.

**Sequencing:**
1. `aux_clip.py` + the two endpoints + the six templates (self-contained, testable by curl — no
   frontend needed). Ship + verify with tests 1–7.
2. `aux_clip.js` sheet + tiers 2/3 read (navigator.clipboard + manual paste) + copy tier 2 +
   escalation. Ship as **dashboard-only**, fully working without any app change.
3. Coordinate the two Swift message handlers with P2.2 (read/write tier 1). Purely additive;
   aux_clip.js already feature-detects them.

**Dependencies:**
- **Runtime:** local MLX server up (`model_online()`); nothing else. Works today.
- **P2.2 (soft):** the menu-bar popover surface and the Swift NSPasteboard bridge. The feature is
  fully usable from the dashboard **without** P2.2; P2.2 only adds the menu-bar entry point and
  the no-prompt clipboard bridge. Decoupled at runtime by feature detection.
- **Orchestrator:** applies the single `<script src="/aux_clip.js"></script>` line.

**Open questions:**
1. **Streaming?** v1 is one-shot (aux dispatch returns a single JSON dict; the MLX server does
   support `stream:true` SSE). Outputs are short and TTFT is ~1.5s, so blocking is fine. If the
   result pane feels slow, a later `/api/clip/stream` SSE variant is the one upgrade — mirrors
   the "SSE is the one chat upgrade to consider" note in DEVPLAN §4. Recommend deferring.
2. **Oversized policy (413 vs. silent truncate):** spec default = endpoint 413, UI pre-truncates
   to `CLIP_MAX_CHARS` so the user sees "acting on first 16k" rather than an error. Confirm this
   is the desired UX (alternative: endpoint truncates too and always 200 with `truncated_input`).
3. **`CLIP_MAX_CHARS` value:** 16000 chosen for a comfortable prompt on the 30B with KV cap
   headroom. Revisit if the always-on model becomes an 8B with a smaller effective budget.
4. **Language input UX:** free-text `lang` (max 40 chars) vs. a short curated list. Free-text is
   more capable (any language) but a list is tidier; leaning free-text with a few suggested
   chips.
5. **Copy-back audit:** currently ephemeral (no log). If the trust story later wants "everything
   the assistant touched" to include clipboard writes, add a single-line jsonl (no content, just
   `{ts, action, out_chars}`). Out of scope for v1; flagged.
