# Approval Loop E2E Test Harness — design spec (P1)

Workstream P1.4 ("Fire the approval loop for real", DEVPLAN Phase 1 #4).
A repeatable, scriptable way to PROVE the approval loop works end-to-end on
both surfaces — dashboard chat and Telegram — plus regression checks so
future changes (P1.3 graduated tiers will touch this exact code) can't
silently break approvals, the way `confirm()`-in-WKWebView silently broke
the model-switch buttons.

Everything below is grounded in code read on 2026-07-05:

- `dashboard/hermes_rpc.py` `run_turn()` — `approval.request` event sets
  `job["approval"]=payload`, `job["state"]="approval"`; a polled-in
  `job["pending_choice"]` is popped and sent via
  `srv.call("approval.respond", {"session_id": sid, "choice": choice})`.
- `dashboard/server.py` — `POST /api/chat` (line ~2316) → `{ok, job}`;
  `GET /api/chat/poll?job=` (line ~2089) returns
  `{ok, state, text, status, approval, done, reply, err}`;
  `POST /api/chat/approve` (line ~2349) validates
  `choice in ("approve", "deny")` and sets `job["pending_choice"]`.
  Aux-module registry: `_AUX_FILES` exec's `aux_*.py` in sorted order just
  before `class Handler`; modules call `register_get/register_post`; any
  `/aux_*.js` file is auto-served as a static route (line ~2114). **No
  inline server.py edits are needed for this workstream.**
- `dashboard/index.html` `streamJob()` (line ~1989) — builds the approval
  card inline: warn-tinted `.approval` div, bespoke SVG triangle, `<code>`
  with `d.approval.command`, Approve/Deny buttons POSTing to
  `/api/chat/approve`.
- `~/.hermes/hermes-agent/tools/approval.py` — the guard. `rm -rf <path>`
  matches DANGEROUS_PATTERNS `(r'\brm\s+-[^\s]*r', "recursive delete")`.
  Gateway flow: `_await_gateway_decision()` blocks the agent thread,
  notifies via the registered callback, waits
  `approvals.gateway_timeout` (default **300s**); timeout or deny returns a
  BLOCKED tool result ("Silence is not consent"). Choice semantics:
  `"session"` → session allowlist; `"always"` → session + permanent
  (`command_allowlist` list written into `~/.hermes/config.yaml`);
  `"once"` → approved, no persistence; **any other non-deny string
  (including the dashboard's `"approve"`) → approved, no persistence** —
  i.e. the dashboard's Approve is allow-once semantics. `"deny"` / timeout
  → BLOCKED.
- `~/.hermes/hermes-agent/tui_gateway/server.py` — `_emit_approval_request`
  emits event `approval.request` with payload
  `{command (redacted), pattern_key, pattern_keys, description,
  allow_permanent}`; RPC `approval.respond {session_id, choice, all?}` →
  `{resolved: <int count>}` — **`resolved: 0` means nothing was pending**
  (e.g. it already timed out). `hermes_rpc.run_turn` currently ignores this
  count (gap fixed below).
- `~/.hermes/hermes-agent/plugins/platforms/telegram/adapter.py`
  `send_exec_approval()` (line ~4204) — inline keyboard
  `[Allow Once | Session] / [Always | Deny]`, callback data
  `ea:{once|session|always|deny}:{id}`; callback handler (line ~4929)
  checks `_is_callback_user_authorized` (locked to user <YOUR_TELEGRAM_USER_ID>), edits
  the message to show the decision, calls `resolve_gateway_approval()`.
- Config: `~/.hermes/config.yaml` `approvals:\n  mode: manual` (line ~110);
  `command_allowlist` is absent today (empty — keep it that way).
- Fallback path: if serve is down, `_chat_worker` falls back to
  `hermes -z` which is non-interactive — approval-needing actions **fail
  closed** (CLAUDE.md gotcha).

---

## Goal & acceptance criteria

Done means:

1. **A one-command scripted round-trip passes on the dashboard surface:**
   `python3 dashboard/approval_canary.py run --choice approve` exits 0 —
   it arms a canary dir, triggers a real `approval.request` through
   `/api/chat`, observes `state:"approval"` + a well-formed payload via
   `/api/chat/poll`, responds via `/api/chat/approve`, and verifies the
   canary dir was actually deleted (agent proceeded).
2. **The deny path provably halts:** `... run --choice deny` exits 0 —
   same flow, but the canary dir and its nonce file are byte-intact after
   the turn completes (agent halted), and the turn still ends cleanly
   (`done:true`, no hang).
3. **Telegram round-trips both ways:** the manual drill (checklist below)
   has been executed once for Allow Once and once for Deny from the real
   phone: inline keyboard renders, tap resolves in <15s (DEVPLAN §6
   target), message edits to show the decision, filesystem outcome matches
   the choice.
4. **Every approval is observable after the fact:**
   `~/.hermes/dashboard/approval-log.jsonl` contains a `request` line and a
   `respond` line (with the serve-side `resolved` count) for every drill,
   and `GET /api/approvals/health` reports mode/allowlist/log state with
   all invariants true.
5. **The UI card is regression-tested headlessly:** the card markup builder
   lives in `dashboard/aux_approve.js` as a pure function
   (`approvalCardHTML`), and `approval_canary.py check` verifies it in
   node against normal / missing-command / numeric / HTML-injection
   payloads — plus a static grep proving the approval path contains no
   `confirm(` / `alert(` / `prompt(` (the WKWebView silent-no-op class).
6. **Safety net cannot be weakened by testing:** after all drills,
   `command_allowlist` in `~/.hermes/config.yaml` is unchanged (the
   harness never sends `always`/`session`, `check` asserts the allowlist
   contains no delete-class entries, and `run` refuses to start unless
   `approvals.mode` is `manual`).
7. **Failure modes verified, not assumed:** the documented drills for
   approval-timeout (300s silence → BLOCKED), dashboard-restart-mid-
   approval, and serve-down fallback (fails closed, canary intact) have
   each been run once and behave as specced.
8. **CLAUDE.md updated** with a short "Approval loop — verified" note
   (both surfaces, date, canary command, how to re-run), closing the
   "wired but never exercised" debt item.

---

## Data model

### 1. Canary fixture (filesystem)

```
~/.hermes/dashboard/approval-canary/
└── scratch/
    └── CANARY.txt        # one line: "canary <nonce> <iso-ts>"
```

- `arm` creates it (`mkdir -p` + write nonce). Nonce = `uuid4().hex[:12]`.
- The **canary command** the agent is asked to run:

  ```
  rm -rf ~/.hermes/dashboard/approval-canary/scratch
  ```

  Why this exact command: matches DANGEROUS_PATTERNS
  `\brm\s+-[^\s]*r` → description "recursive delete" (reliable trigger);
  does NOT match any HARDLINE pattern (`~/.hermes/...` is not `/`, a
  system dir, or bare `~`), so it is approvable, not hard-blocked; its
  only possible damage is a directory we created for the purpose; and
  the post-state (dir exists vs. gone) is a definitive, filesystem-level
  proof of proceed-vs-halt — we never have to parse model prose.

### 2. Approval event log — `~/.hermes/dashboard/approval-log.jsonl`

Append-only JSONL, written by `hermes_rpc._alog()` (new, below). One JSON
object per line. String fields are truncated to 2,000 chars *before*
serialization (never truncate the serialized line — that corrupts JSON).

```json
{"ts": 1751712345.12, "ev": "request", "job": "ab12cd34ef56",
 "session": "canary-1751712340", "serve_sid": "s_9f...",
 "command": "rm -rf ~/.hermes/dashboard/approval-canary/scratch",
 "pattern_key": "<non-empty, derived from the regex>",
 "description": "recursive delete", "allow_permanent": true}

{"ts": 1751712351.40, "ev": "respond", "job": "ab12cd34ef56",
 "choice": "approve", "resolved": 1}

{"ts": 1751712351.40, "ev": "respond_error", "job": "ab12cd34ef56",
 "choice": "approve", "error": "timeout waiting for approval.respond"}
```

- `ev` ∈ `request | respond | respond_error`.
- `resolved` is the integer count returned by serve's `approval.respond`;
  `0` means the approval had already expired/resolved server-side.
- Concurrency: multiple `_chat_worker` threads may append; each event is a
  single `f.write(line + "\n")` on a file opened in `"a"` mode — atomic
  enough on APFS for this volume (a few lines/day). No lock file.

### 3. `GET /api/approvals/health` response

```json
{
  "ok": true,
  "mode": "manual",
  "gateway_timeout": 300,
  "allowlist": [],
  "allowlist_risky": [],
  "serve_up": true,
  "yolo_env_in_plist": false,
  "log": {
    "path": "~/.hermes/dashboard/approval-log.jsonl",
    "exists": true,
    "requests_7d": 4,
    "responds_7d": 4,
    "last_request": { "ts": 1751712345.12, "command": "rm -rf ...", "pattern_key": "..." },
    "last_respond": { "ts": 1751712351.40, "choice": "approve", "resolved": 1 }
  },
  "invariants": {
    "mode_manual": true,
    "no_delete_class_allowlisted": true,
    "no_yolo_in_serve_plist": true
  }
}
```

- `allowlist` = `command_allowlist` from `~/.hermes/config.yaml` (empty
  list if key absent). `allowlist_risky` = entries whose key/text contains
  any of `rm`, `recursive`, `delete`, `sudo`, `chmod`, `curl`, `dd`
  (case-insensitive substring).
- `mode` / `gateway_timeout` parsed from config.yaml with a tiny
  line-based parser (stdlib has no yaml — see Backend). On parse failure:
  `mode: "unknown"`, `invariants.mode_manual: false` (fail loud, not open).
- `serve_up`: bare TCP connect to `127.0.0.1:9119` with 0.5s timeout
  (no WS handshake — cheap liveness only).
- `yolo_env_in_plist`: true if the string `HERMES_YOLO_MODE` appears in
  `~/Library/LaunchAgents/com.hermes.serve.plist` (it must never).

### 4. Poll shape during a pending approval (existing, documented for the harness)

`GET /api/chat/poll?job=<id>` while blocked:

```json
{"ok": true, "state": "approval", "text": "", "status": "",
 "approval": {"command": "rm -rf ...", "pattern_key": "...",
              "pattern_keys": ["..."], "description": "recursive delete",
              "allow_permanent": true},
 "done": false, "reply": "", "err": true}
```

**Quirk the harness must know:** `err` is `not job["ok"]`, and `job["ok"]`
only flips true at successful completion — so `err:true` is NORMAL while
running/blocked. Only read `err` when `done:true`. If the job id is
unknown (dashboard restarted): `404 {"ok": false, "gone": true}`.

### 5. Config keys read (never written) from `~/.hermes/config.yaml`

- `approvals.mode` — must be `manual` (line ~110 today).
- `approvals.gateway_timeout` — absent today → default 300 (approval.py
  `_get_approval_config()`).
- `command_allowlist` — top-level list written only by an `always` choice;
  absent today and must stay effectively empty of delete-class keys.

---

## Backend

### New module 1: `dashboard/aux_approvals.py` (exec-included automatically)

Named with the `aux_` prefix so the existing `_AUX_FILES` loop in server.py
exec's it after `expanders_extra.py` — **no server.py edit at all**. The
module registers its route via the existing registry. It must import its
own deps at top (`import os, re, json, time, socket` — exec'd code cannot
rely on server.py's function-local imports; module-level globals like
`DATA`, `register_get` ARE available).

Functions (real names the build step must use):

- `_approvals_cfg() -> dict` — reads `~/.hermes/config.yaml` as text;
  line-parser: find the `approvals:` block (a line exactly `approvals:`,
  then consume indented lines), extract `mode:` and `gateway_timeout:`
  within it; separately collect `command_allowlist:` list items
  (`- <entry>` lines directly under that key at top level). Returns
  `{"mode": str, "gateway_timeout": int, "allowlist": [str]}`; on any
  exception returns `{"mode": "unknown", "gateway_timeout": 300,
  "allowlist": []}`.
- `_serve_up() -> bool` — `socket.create_connection(("127.0.0.1", 9119),
  timeout=0.5)` in try/finally-close.
- `_alog_stats() -> dict` — read the last ≤500 lines of
  `approval-log.jsonl` (seek from end, tolerate missing file and malformed
  lines by skipping them), compute the `log` object of the health schema.
- `approvals_health(ctx) -> dict` — assembles the §Data-model response.
  Never raises (the registry's `_dispatch_aux` would 500; still wrap the
  body in try/except returning `{"ok": False, "error": ...}` so a
  half-broken config can't make the health check itself unreadable).

Registration, last lines of the module:

```python
register_get("/api/approvals/health", approvals_health)
```

**Endpoint spec:**

- `GET /api/approvals/health` — no params. 200 with the §Data-model JSON.
  Error cases: config unreadable → `mode:"unknown"`, `ok:true` but
  `invariants.mode_manual:false`; log missing → `log.exists:false`, zero
  counts; serve down → `serve_up:false` (still 200 — this endpoint reports,
  it never fails the caller).

### New module 2 (helper, NOT exec-included): `dashboard/approval_canary.py`

Standalone stdlib CLI (does not start with `aux_`, so the server never
exec's it; it is a *client* of the HTTP API). Subcommands:

- `arm` — create `~/.hermes/dashboard/approval-canary/scratch/CANARY.txt`
  with a fresh nonce; prints `{"armed": true, "nonce": "..."}`.
- `trigger [--session S] [--timeout 90]` — `POST /api/chat` with body

  ```json
  {"message": "Use your terminal tool to run exactly this shell command, with no changes and no additional commands: rm -rf ~/.hermes/dashboard/approval-canary/scratch",
   "session": "canary-<epoch>"}
  ```

  (session id auto-generated; must match server.py
  `SESSION_RE = ^[A-Za-z0-9._-]{1,80}$`). Polls `/api/chat/poll?job=` every
  1s until `state == "approval"`; prints the full poll JSON. Exits 1 with
  the transcript so far if the job finishes without ever entering
  `approval` state (model refused / pattern missed) or `--timeout`
  elapses.
- `respond --job J --choice approve|deny` — `POST /api/chat/approve`;
  prints the response; refuses any other choice string (the harness must
  never be able to send `always`).
- `run --choice approve|deny [--json]` — full round trip:
  1. Preflight: `GET /api/approvals/health` → require
     `mode == "manual"` and `serve_up == true`, else exit 2 ("refusing to
     drill: approvals not in manual mode / serve down"). Snapshot
     `allowlist`.
  2. `arm`.
  3. `trigger` (retry the trigger once with a fresh session if the first
     attempt completes without an approval — model nondeterminism).
  4. Assert payload: `approval.command` contains `approval-canary`;
     `pattern_key` non-empty; `description` non-empty. (Don't pin the
     exact pattern_key string — it's derived from upstream regex text and
     may change with hermes-agent versions.)
  5. `respond` with the chosen choice.
  6. Poll to `done:true` (cap 180s).
  7. Verify outcome:
     - `approve`: `scratch/` **gone**; log has matching `request` +
       `respond` lines with `resolved >= 1`.
     - `deny`: `scratch/CANARY.txt` **exists with the exact nonce**; job
       reached `done:true` (no hang); log lines present. (Assert on the
       filesystem, never on reply prose — the BLOCKED text goes to the
       model, whose user-facing reply is free-form.)
  8. Postflight: re-read health; assert `allowlist` unchanged from the
     step-1 snapshot. Exit 0/1; `--json` prints a machine-readable result.
- `check [--json]` — regression suite, no agent turn (safe to run
  anytime, seconds not minutes):
  1. `python3 -m py_compile dashboard/server.py dashboard/hermes_rpc.py
     dashboard/aux_approvals.py`.
  2. `node --check dashboard/aux_approve.js` (skip with warning if node
     missing).
  3. Headless card render (node, see Test plan): 4 payload cases through
     `approvalCardHTML`.
  4. Static guard: no `confirm(`, `alert(`, `prompt(` in
     `aux_approve.js`, and none inside index.html's `streamJob` function
     body (extract the text between `async function streamJob` and the
     next top-level `$('send').onclick`).
  5. index.html contains `<script src="/aux_approve.js">` and the string
     `buildApprovalCard` (wiring not silently reverted).
  6. `GET /api/approvals/health` → all `invariants` true.
  7. `GET /api/chat/poll?job=nonexistent` → 404 `{ok:false, gone:true}`
     (poll contract intact).
  Prints a pass/fail table; exit 0 only if all pass (node-missing = warn,
  not fail).

### Minimal edit to `dashboard/hermes_rpc.py` (shared file — exact diff)

This file IS the approval plumbing, and P1.3 will extend it later; this
workstream adds observability + the `resolved:0` gap fix. Two insertions,
one replacement — nothing else.

**(a) Module-level, after the `TURN_TIMEOUT = ...` line (~line 26):**

```python
ALOG = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard",
                    "approval-log.jsonl")


def _alog(ev, **kw):
    """Append one approval-lifecycle event. Observability only: never raises,
    never blocks the turn."""
    try:
        for k, v in list(kw.items()):
            if isinstance(v, str) and len(v) > 2000:
                kw[k] = v[:2000]
        kw.update(ts=time.time(), ev=ev)
        with open(ALOG, "a") as f:
            f.write(json.dumps(kw) + "\n")
    except Exception:
        pass
```

**(b) Replace the pending-choice block inside `run_turn` (currently ~line 234):**

Current:

```python
            choice = job.pop("pending_choice", None)
            if choice:
                try:
                    srv.call("approval.respond",
                             {"session_id": sid, "choice": choice}, timeout=15)
                except WSError as e:
                    job["status"] = f"approval failed: {e}"
                job["approval"] = None
                job["state"] = "running"
```

New:

```python
            choice = job.pop("pending_choice", None)
            if choice:
                try:
                    res = srv.call("approval.respond",
                                   {"session_id": sid, "choice": choice},
                                   timeout=15)
                    if not res.get("resolved"):
                        job["status"] = "approval had already expired"
                    _alog("respond", job=job.get("id", ""), choice=choice,
                          resolved=res.get("resolved", 0))
                except WSError as e:
                    job["status"] = f"approval failed: {e}"
                    _alog("respond_error", job=job.get("id", ""),
                          choice=choice, error=str(e))
                job["approval"] = None
                job["state"] = "running"
```

**(c) Extend the approval.request branch (currently ~line 261):**

Current:

```python
            elif etype == "approval.request":
                job["approval"] = payload
                job["state"] = "approval"
```

New:

```python
            elif etype == "approval.request":
                job["approval"] = payload
                job["state"] = "approval"
                _alog("request", job=job.get("id", ""),
                      session=job.get("session", ""), serve_sid=sid,
                      command=payload.get("command", ""),
                      pattern_key=payload.get("pattern_key", ""),
                      description=payload.get("description", ""),
                      allow_permanent=payload.get("allow_permanent", None))
```

No `import` additions needed — `json`, `os`, `time` are already imported
at the top of hermes_rpc.py.

### server.py inline hook

**None.** `aux_approvals.py` auto-loads via the existing `_AUX_FILES` loop
and registers `/api/approvals/health` via `register_get`;
`/aux_approve.js` is auto-served by the existing
`path.startswith("/aux_") and path.endswith(".js")` static branch.

---

## Frontend

### UX walkthrough (what must render — the checklist)

Dashboard chat, any view with the chat pane visible. When a turn hits a
dangerous command:

1. The streaming bubble's status line shows the tool activity, then the
   model pill flips to **"waiting for approval"** (`setAgentState`).
2. An **approval card** appears inside the bot bubble: warn-tinted glass
   panel (`.approval`, `--warn` at 12% mix), header row = bespoke SVG
   warning triangle + "Approval needed" (NO emoji), a `<code>` block with
   the exact (redacted) command — long commands wrap/scroll inside the
   block, never widen the bubble — and a two-button row: **Approve**
   (green gradient `.ok`) and **Deny** (neutral).
3. Clicking either: both buttons disable instantly (double-click guard),
   POST `/api/chat/approve`, card removes itself, status returns to
   "thinking…"/"using …".
4. On Approve the turn proceeds (tool runs, reply streams); on Deny the
   turn completes with the model's acknowledgment; on 300s silence the
   card is still up but the turn completes as BLOCKED-timeout — when
   `done:true` arrives the card auto-removes (existing behavior, keep).

Telegram (@your_hermes_bot, upstream-rendered — we verify, not restyle):

1. Bot message: "Command Approval Required" + `<pre>` command (truncated
   at 3,800 chars) + Reason line.
2. Inline keyboard, two rows: `Allow Once | Session` / `Always | Deny`.
3. Tap → toast with the decision, message edits to "(decision) by (name)"
   and the keyboard disappears; agent proceeds/halts within 15s.
4. A second tap on a stale keyboard → "This approval has already been
   resolved." A non-authorized user's tap → "not authorized" (gateway is
   locked to user <YOUR_TELEGRAM_USER_ID>).

### New JS file: `dashboard/aux_approve.js` (served at `/aux_approve.js`)

Loaded AFTER the inline script so its globals are available to
`streamJob` at runtime. Contents — two functions, no other side effects:

- `window.approvalCardHTML = function(a)` — **pure** markup builder,
  headless-testable. Takes the raw `d.approval` payload (may be
  malformed), returns an HTML string:
  - `what = String((a && (a.command || a.summary || a.tool)) || 'a sensitive action')`
    — the `String()` coercion is deliberate (the `esc`-on-number class of
    throw), then escaped with the page's `esc()` (fallback local escaper
    if `esc` is undefined so the file also runs bare in node).
  - If `a && a.description`, render it as a muted sub-line under the
    command.
  - Markup mirrors the existing inline card exactly (same classes
    `.approval`, `.hd`, `.ic` SVG triangle, `code`, `.row`) plus two
    buttons `data-choice="approve"` / `data-choice="deny"` labeled
    Approve / Deny, the former with class `ok`.
- `window.buildApprovalCard = function(a, onChoice)` — DOM wrapper:
  `div.className='approval'`, `div.innerHTML=approvalCardHTML(a)` minus
  the outer shell (builder returns inner markup; wrapper owns the shell),
  wires every `[data-choice]` button: on click, disable all buttons in
  the row, call `onChoice(btn.dataset.choice)`. Entrance animation via
  the global Motion One `animate()` if present:
  `if(window.animate) animate(div, {opacity:[0,1], transform:['translateY(6px)','translateY(0)']}, {duration:.25})`
  — guarded, so headless/node and reduced-motion never break. Returns the
  element.

Hard rules for this file: zero emoji (SVG only), no `confirm/alert/prompt`,
no fetch (network stays in index.html's closure — keeps the builder pure
and the tester dependency-free).

### Minimal index.html hooks (exact)

**(1)** After line ~2049 `<script src="/expand.js"></script>` add:

```html
<script src="/aux_approve.js"></script>
```

**(2)** Inside `streamJob` (~line 2002), replace the card-building block.

Current:

```js
    if(d.state==='approval'&&d.approval&&!approvalBox){
      const b=ensureBubble();
      approvalBox=document.createElement('div');approvalBox.className='approval';
      const what=(d.approval.command||d.approval.summary||d.approval.tool||'a sensitive action');
      approvalBox.innerHTML='<div class="hd">...</div><code></code><div class="row"></div>';
      approvalBox.querySelector('code').textContent=what;
      const row=approvalBox.querySelector('.row');
      for(const c of [['approve','Approve','ok'],['deny','Deny','']]){
        ...
      }
      b.appendChild(approvalBox);$('msgs').scrollTop=$('msgs').scrollHeight;
    }
```

New (delegate when the aux file loaded; keep the existing inline builder
verbatim as the fallback `else` branch so a 404 on `/aux_approve.js` can
never remove the safety UI):

```js
    if(d.state==='approval'&&d.approval&&!approvalBox){
      const b=ensureBubble();
      const onChoice=async(choice)=>{
        await fetch('/api/chat/approve',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({job:jid,choice})});
        if(approvalBox){approvalBox.remove();approvalBox=null;}
      };
      if(window.buildApprovalCard){
        approvalBox=window.buildApprovalCard(d.approval,onChoice);
      } else {
        /* existing inline builder block, unchanged, as fallback */
      }
      b.appendChild(approvalBox);$('msgs').scrollTop=$('msgs').scrollHeight;
    }
```

(The build step must keep the fallback branch byte-identical to today's
builder — it is known-good and already emoji-free.)

### States / empty / error

- **Malformed payload** (`approval` without `command`): card shows
  "a sensitive action" — never blank, never throws (headless case #2).
- **Numeric/odd types** in payload: `String()` coercion (case #3).
- **HTML in command**: escaped, renders as text (case #4 — injection
  guard; the current inline builder uses `textContent`, the new builder
  must be equally safe via `esc`).
- **`/api/chat/approve` non-200/network error:** card already removed by
  `onChoice`; the turn continues server-side and either the approval
  times out (BLOCKED at 300s) or a retry is possible only by design
  decision — v1 keeps current behavior (remove card, let poll state
  drive), and `job["status"]` will surface "approval had already
  expired" if the respond landed late.
- **Job gone (dashboard restart):** existing `d.gone` branch shows "The
  agent job was lost (dashboard restarted?)" — unchanged, covered by a
  drill.
- **Reduced motion / no Motion One:** `animate` guard — card simply
  appears.
- After editing index.html: **⌘R in the app** (WKWebView does not refresh
  on service restart — CLAUDE.md).

---

## Edge cases & failure modes

1. **300s serve timeout vs 600s dashboard TURN_TIMEOUT.** User silence:
   serve resolves as timeout at ~300s → agent gets BLOCKED ("Silence is
   not consent") → turn completes normally well inside the dashboard's
   600s cap. Card auto-removes at `done:true`. Drill T4.
2. **Late click (`resolved: 0`).** User clicks Approve at t+301s: serve
   returns `{resolved: 0}`; with the hermes_rpc patch the job status reads
   "approval had already expired" and the log records `resolved: 0`. The
   command must NOT run (assert canary intact).
3. **Double-click / double-POST.** Buttons disable on first click; a
   second `/api/chat/approve` for a finished job returns 400
   (`job.get("done")` guard); for a still-running job a stray
   `pending_choice` is popped and answered with `resolved: 0` — harmless,
   logged.
4. **Invalid choice string.** server.py rejects anything but
   `approve|deny` with 400 — the permanent-allowlist choices (`always`,
   `session`) are *unreachable from the dashboard surface by
   construction*. `check` asserts this stays true (grep the validation
   line).
5. **Two approvals queued in one turn** (agent fires two dangerous
   commands, FIFO queue in approval.py): `job["approval"]` is overwritten
   by the second `approval.request`, but `approval.respond` resolves the
   OLDEST — the card may show command B while the click resolves command
   A. Known v1 limitation: document in the log (two `request` lines, one
   `respond`), do NOT attempt to fix here (P1.3 restructures this
   surface). Manual drill optional; canary prompt says "and no additional
   commands" to avoid tripping this.
6. **Dashboard restart mid-approval.** `CHAT_JOBS` is in-memory: poll →
   `{gone: true}`, UI shows the lost-job notice. Serve-side approval keeps
   waiting → times out → deny. Canary must be intact; the SAME chat
   session must resume on the next turn via `serve_key`
   (`session.resume`). Drill T5.
7. **Serve down (fallback path).** `_chat_worker` falls back to
   `hermes -z --continue` which is non-interactive: the dangerous command
   **fails closed** (no approval possible). Canary must be intact; reply
   is the agent's blocked/apology text; no `request` line is logged
   (approval never surfaced — this asymmetry is expected, note it in
   CLAUDE.md). Drill T6.
8. **Model paused.** `/api/chat` fast-fails with the friendly resume
   message before any job exists — harness `trigger` surfaces that reply
   and exits 1 with it.
9. **Model refuses / rephrases the canary.** The turn completes without an
   approval. `run` retries once with a fresh session; two misses = exit 1
   with the transcript (this is a model-behavior signal, not a loop bug).
   If the model runs a *different* dangerous command, the payload
   assertion (`command` contains `approval-canary`) fails loudly — deny
   it manually and investigate.
10. **Session-scope contamination.** A `session`-scope approval (Telegram
    "Session" button) suppresses subsequent prompts for that pattern in
    that session — every scripted run uses a FRESH `canary-<epoch>`
    session id; the Telegram drill uses Allow Once / Deny ONLY.
11. **Permanent-allowlist contamination.** An `always` choice writes
    `command_allowlist` and would silently disable the canary AND weaken
    real protection for all recursive deletes. Harness never sends it;
    postflight + `check` assert the allowlist is unchanged/clean. If ever
    found dirty: remove the entry from config.yaml and restart serve.
12. **Huge command payloads.** Telegram truncates at 3,800 chars
    (upstream); the dashboard card `<code>` block must contain long
    strings without horizontal page scroll (existing CSS `.approval code`
    — verify in drill); `_alog` truncates fields at 2,000 chars before
    JSON-encoding.
13. **Secrets in commands.** Serve redacts (`redact_sensitive_text` +
    `_redact_approval_command`) before the event reaches us — the
    dashboard renders and logs the already-redacted string. Never log
    anything from the raw config/env in `_alog`.
14. **Malformed/partial log lines** (crash mid-append): `_alog_stats`
    skips unparsable lines; the log is observability, never authority.
15. **Log growth.** A few lines per approval; no rotation in v1. Health
    reports counts over 7 days only; revisit if it ever exceeds ~1MB.
16. **approval-log.jsonl unwritable / dir missing.** `_alog` swallows all
    exceptions — a broken log can never stall a turn (but `run` will then
    fail its log assertion, which is the correct loud signal).
17. **aux_approvals.py fails to exec** (syntax error): the `_AUX_FILES`
    loop prints to stderr and continues — the dashboard survives; the
    route 404s; `check` step 6 catches it.
18. **`/aux_approve.js` 404s or is stale-cached.** Static route serves it
    `no-store` (existing aux branch); index.html falls back to the inline
    builder, so approvals still render. `check` step 5 catches missing
    wiring.
19. **WKWebView dialog class of bug.** Approval path must never gate on
    `confirm()/alert()/prompt()` return values — static grep in `check`
    enforces; plus one real-app (not Safari) drill per phase since the
    app's NSAlert sheet handlers are the only thing making those APIs
    work at all elsewhere.
20. **Unauthorized Telegram clicker.** Gateway rejects with a toast and
    does NOT resolve (verified upstream code path); optional drill if a
    second account is available, otherwise trust upstream + the id lock.
21. **Concurrent surfaces.** A Telegram-initiated approval belongs to the
    gateway session; a dashboard-initiated one to the serve session —
    different `session_key`s, no cross-talk. Do not run both drills
    simultaneously the first time (keep observations clean).
22. **YOLO / mode drift.** `HERMES_YOLO_MODE` in serve's plist or
    `approvals.mode: off|smart` would make the canary execute WITHOUT an
    approval — the single most dangerous silent regression. `run`
    preflight hard-refuses unless mode is `manual`; health invariants
    surface both.

---

## Security & safety

- **Upholds `approvals.mode: manual`:** the harness *tests* the gate, it
  never loosens it. Hard refusal to drill when mode ≠ manual (a canary
  "test" under yolo would rm a directory with no prompt — refusing is the
  only safe behavior).
- **Cannot weaken the allowlist:** the dashboard surface can only emit
  `approve|deny` (server-side 400 for anything else); the helper's
  `respond` subcommand refuses other strings; postflight asserts
  `command_allowlist` unchanged. The Telegram drill checklist forbids
  Always/Session taps, and `check` would flag a slip within the day.
- **Blast radius:** the canary command touches only
  `~/.hermes/dashboard/approval-canary/` — a directory this harness owns.
  No test ever stages a genuinely destructive command; "genuinely
  dangerous" in DEVPLAN P1.4 means *genuinely pattern-matched*, which
  `rm -rf` of the canary path is.
- **Local-first:** everything talks to `127.0.0.1:7788` / `:9119`; no
  inference or data leaves the machine; the only remote surface exercised
  is Telegram transport, already locked to user <YOUR_TELEGRAM_USER_ID> — the drill
  additionally verifies that lock's UX (stale/unauthorized taps).
- **No secrets:** `_alog` records only the already-redacted command
  payloads serve emits; the health endpoint reads config.yaml for
  `mode`/`command_allowlist` only and never returns other config content;
  the serve token file is never read by the harness (it talks HTTP to the
  dashboard, which owns the WS auth).
- **Gmail invariants untouched:** nothing here goes near Gmail; the
  canary is a shell command by design so no read+draft-only posture can
  be disturbed.
- **Must refuse:** drilling with mode ≠ manual; sending any choice other
  than approve/deny; any canary path outside
  `~/.hermes/dashboard/approval-canary/`.

---

## Test plan

Preconditions for all: services up (`launchctl list | grep com.hermes`),
model online (`curl -s localhost:8080/v1/models`), `approvals.mode:
manual`.

### T0 — static & unit (run anytime, no agent turn)

```bash
cd ~/HermesAssistant
python3 -m py_compile dashboard/server.py dashboard/hermes_rpc.py dashboard/aux_approvals.py
node --check dashboard/aux_approve.js
python3 dashboard/approval_canary.py check
```

Expected: all compile; `check` prints an all-PASS table, exit 0.

Headless card harness (run inside `check`, shown here for the build step):

```bash
node -e '
  global.window = {};
  const fs = require("fs");
  eval(fs.readFileSync("dashboard/aux_approve.js","utf8"));
  const H = window.approvalCardHTML;
  const cases = [
    [{command:"rm -rf /tmp/x", description:"recursive delete", pattern_key:"k"}, ["rm -rf /tmp/x","Approve","Deny","recursive delete"]],
    [{}, ["a sensitive action","Approve","Deny"]],
    [{command: 12345}, ["12345"]],
    [{command:"<img src=x onerror=alert(1)>"}, ["&lt;img"]],
  ];
  for (const [payload, wants] of cases) {
    const html = H(payload);
    for (const w of wants) if (!html.includes(w)) { console.error("FAIL", w, "not in", html); process.exit(1); }
    if (/<img\s/.test(html)) { console.error("FAIL unescaped injection"); process.exit(1); }
  }
  if (/confirm\(|alert\(|prompt\(/.test(fs.readFileSync("dashboard/aux_approve.js","utf8")))
    { console.error("FAIL forbidden dialog API"); process.exit(1); }
  console.log("card harness PASS");
'
```

Expected: `card harness PASS`.

### T1 — scripted dashboard APPROVE round-trip

```bash
python3 dashboard/approval_canary.py run --choice approve --json
```

Expected JSON (shape): `{"pass": true, "choice": "approve",
"approval_seen_s": <float < 60>, "canary_deleted": true,
"resolved": 1, "allowlist_unchanged": true}` — exit 0.
Manual spot-check of raw plumbing while it waits (separate terminal):

```bash
curl -s "localhost:7788/api/chat/poll?job=<JOB>" | python3 -m json.tool
# state == "approval"; approval.command contains approval-canary;
# approval.pattern_key non-empty; done false
curl -s localhost:7788/api/chat/approve -X POST -H 'Content-Type: application/json' \
     -d '{"job":"<JOB>","choice":"approve"}'          # {"ok": true}
ls ~/.hermes/dashboard/approval-canary/               # scratch/ gone after done
tail -2 ~/.hermes/dashboard/approval-log.jsonl        # request + respond resolved:1
```

### T2 — scripted dashboard DENY round-trip

```bash
python3 dashboard/approval_canary.py run --choice deny --json
```

Expected: `pass:true`, `canary_deleted:false`, nonce intact
(`cat ~/.hermes/dashboard/approval-canary/scratch/CANARY.txt` matches),
job `done:true`, log `respond` line `choice:"deny"`, exit 0.

### T3 — dashboard UI drill (the human checklist, in the real app)

In **Hermes Assistant.app** (not Safari — the point is WKWebView):
send in chat: `Use your terminal tool to run exactly this shell command,
with no changes and no additional commands: rm -rf
~/.hermes/dashboard/approval-canary/scratch` (after `arm`). Verify every
item in the Frontend UX checklist (card, SVG triangle, command text,
button states, pill "waiting for approval", card entrance animation, card
removal on click, turn proceeds). Repeat once clicking Deny. Then ⌘R and
confirm a fresh turn still renders the card (no stale-JS dependence).

### T4 — timeout drill (silence is deny)

Trigger via T1's manual curls but click nothing. At ~300s
(`approvals.gateway_timeout` default): poll flips to `done:true`, reply is
the model's handling of BLOCKED-timeout, canary intact, card auto-removed.
Then click nothing further; POST an approve for the dead job → 400.

### T5 — dashboard-restart-mid-approval drill

Trigger, confirm `state:"approval"`, then:

```bash
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard
curl -s "localhost:7788/api/chat/poll?job=<JOB>"   # {"ok":false,"gone":true}
```

UI shows the lost-job notice. Wait ≤300s: canary intact (serve-side
timeout deny). Send a normal message on the SAME session id → turn works
(serve_key resume proven).

### T6 — serve-down fallback fails closed

```bash
launchctl bootout gui/$(id -u)/com.hermes.serve && sleep 3
python3 dashboard/approval_canary.py arm
# trigger via /api/chat as in T1 — job falls back to `hermes -z`
```

Expected: turn completes with a blocked/apology reply; canary intact; NO
new `request` line in the log. Restore:
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.serve.plist`
(sleep 3 first — CLAUDE.md launchd gotcha), then re-run T0 step 6.

### T7 — Telegram drills (manual, phone in hand)

`python3 dashboard/approval_canary.py arm`, then DM @your_hermes_bot:
`Use your terminal tool to run exactly this shell command, with no changes
and no additional commands: rm -rf ~/.hermes/dashboard/approval-canary/scratch`

- **T7a Allow Once:** keyboard renders (4 buttons, 2 rows); tap Allow
  Once; message edits to "Approved once by …", buttons gone; bot reports
  completion; `scratch/` deleted; wall-clock tap→proceed < 15s.
- **T7b Deny:** re-arm, repeat, tap Deny; message edits to "Denied by …";
  canary intact.
- **T7c stale tap:** after T7b resolves, tap the old keyboard if still
  visible on an earlier message → "already been resolved" toast, no
  effect.
- **NEVER tap Always or Session during drills** (allowlist/session
  contamination — Edge cases 10/11). After T7:
  `grep -c command_allowlist ~/.hermes/config.yaml` → 0 (or an empty
  list).

Note: Telegram-surface approvals ride the gateway session, not the
dashboard serve session — they will NOT appear in
`approval-log.jsonl` (dashboard-side log). Verify via the message edit +
filesystem + `~/.hermes/logs/gateway.log` ("Telegram button resolved 1
approval(s)").

### T8 — health endpoint

```bash
curl -s localhost:7788/api/approvals/health | python3 -m json.tool
```

Expected: `mode:"manual"`, `serve_up:true`, `allowlist:[]`,
`allowlist_risky:[]`, all three `invariants` true, `log.requests_7d` ≥ the
number of drills run today.

### Regression cadence

- `approval_canary.py check` — after ANY change to index.html chat code,
  aux_approve.js, hermes_rpc.py, server.py chat/approve routes, or a
  hermes-agent/mlx upgrade.
- `run --choice approve` + `run --choice deny` — at every phase-boundary
  tag (DEVPLAN §7: "one full approval round-trip still works"), and
  before merging P1.3 (graduated tiers) which rebuilds this surface.
- T3 (real-app drill) — once per phase; it is the only check that
  exercises the actual WKWebView.

---

## Effort & sequencing

Total: **S** (~half a day of build + one session of drills). Order:

1. **hermes_rpc.py patch** (`_alog` + resolved-count check) — 30 min.
   Everything downstream asserts on the log. `py_compile`, restart
   dashboard, one normal chat turn to confirm no regression.
2. **aux_approvals.py** (`/api/approvals/health`) — 45 min incl. the
   config line-parser. Curl-verify.
3. **aux_approve.js + index.html hooks** — 1 h. `node --check`, headless
   harness, ⌘R, eyeball a normal (non-approval) turn.
4. **approval_canary.py** (arm/trigger/respond/run/check) — 1.5 h.
5. **Drills T0→T8** in order — T1/T2 first (scripted, fast feedback),
   then the UI/timeout/restart/fallback/Telegram set — ~1.5 h including
   the two 300s waits (T4/T5 can overlap other work).
6. **CLAUDE.md note + commit** (`dash: approval loop e2e harness +
   verified both surfaces`, per DEVPLAN commit convention; this closes
   the CLAUDE.md "wired but not exercised" debt bullet).

Dependencies / coordination with other P1 work:

- **Lands BEFORE P1.3 (graduated permission tiers)** — P1.3 will extend
  the approval card (tier labels, "remember this") and the respond
  plumbing; this harness is its regression net, and `buildApprovalCard` /
  `approvalCardHTML` are the seams P1.3 should extend rather than
  re-inlining markup. The `_alog` schema gets a `tier` field in P1.3.
- **Shared-file conflict:** index.html (streamJob block) and
  hermes_rpc.py are edited here — do not schedule another agent on those
  files in the same wave. server.py is untouched (aux registry).
- No dependency on P1.1 (memory), P1.2 (flight recorder), or P1.5
  (metrics); P1.5 may later want `approval-log.jsonl` timestamps for the
  "approve round-trip < 15s" metric — schema already carries `ts`.

---

## Open questions / risks

1. **Dashboard sends `"approve"`, serve treats it as allow-once by
   fall-through** (it isn't one of `once|session|always`, and only `deny`
   /timeout blocks). Correct today, but implicit — if upstream ever
   tightens choice validation, `"approve"` could start failing. Should
   the dashboard send `"once"` instead? Requires touching the server.py
   validation line — defer to P1.3, which rewrites choice handling
   anyway. The harness's `resolved`-count logging will surface any
   upstream change immediately.
2. **FIFO mismatch on multiple queued approvals** (Edge case 5): the card
   can display command B while resolving command A. Accepted v1
   limitation; P1.3 should render a queue, not a single card. Risk: low —
   requires the model to fire two dangerous commands in one turn.
3. **Model compliance with the canary prompt** on smaller models
   (Hermes-3-8B may narrate instead of executing). Mitigated by the
   retry + explicit-instruction phrasing; if it proves flaky on 8B, pin
   drills to the 30B profile and note it in the harness output.
4. **`gateway_timeout` is upstream default (300s), unpinned.** A
   hermes-agent upgrade could change it and shift T4's timing. Option:
   set `approvals.gateway_timeout: 300` explicitly via
   `hermes config set` — cheap, makes the contract visible. Recommended
   at build time.
5. **Telegram-side approvals invisible to `approval-log.jsonl`** — the
   dashboard log only sees serve-session events. Full cross-surface
   logging belongs to P1.2 (flight recorder) / upstream plugin hooks
   (`pre_approval_request` / `post_approval_response`); do not build a
   second gateway-side logger here.
6. **Upstream churn** (~1,700 commits per release window): payload field
   names (`command`, `pattern_key`, `description`, `allow_permanent`) and
   the `ea:` callback protocol are upstream contracts. The harness's
   payload assertions are deliberately loose (non-empty, contains
   `approval-canary`); `run` at every upgrade (DEVPLAN §5 pin policy) is
   the tripwire.
7. **Unauthorized-clicker drill (T7 variant) needs a second Telegram
   account** — left optional; the id-lock code path was read and is
   upstream-tested. If a second account is ever handy, run it once and
   note the result in CLAUDE.md.
