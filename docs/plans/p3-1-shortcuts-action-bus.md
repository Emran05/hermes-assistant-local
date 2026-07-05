# P3.1 — Shortcuts Action-Bus (build-ready spec)

**One-liner:** expose curated macOS Shortcuts as agent actions that flow THROUGH the
P1.3 graduated-permission tiers, with every run landing in the P1.2 flight recorder.

DEVPLAN Phase 3 workstream #1 ("Hands & speed"): *"Curated macOS Shortcuts exposed as
tools with graduated permissions from Phase 1 (read-only auto; reversible remembered;
irreversible always-confirm). Every bus action lands in the flight recorder. Done means:
five useful Shortcuts callable from chat/Telegram; each shows tier-correct approval
behavior; each appears in the recorder timeline."*

Everything below was verified against the live tree on 2026-07-05 (file:line cited).

---

## 0. The core problem this design must solve

`shortcuts run <name>` is a **benign-looking command with arbitrary side effects**
(a Shortcut can send texts, delete files, post to the web). Two verified facts shape
the whole design:

1. **The P1.3 seam only fires on `approval.request` events.** `hermes_rpc.run_turn`
   consults `permissions.decide(payload)` only when serve emits
   `etype == "approval.request"` (dashboard/hermes_rpc.py:279–310). Hermes emits that
   event only for its `DANGEROUS_PATTERNS` / HARDLINE / `execute_code` /
   `mcp_elicitation` triggers (~/.hermes/hermes-agent/tools/approval.py:498–713).
   `shortcuts run X` matches **no** dangerous pattern, so if the agent types it into
   its terminal tool today it executes **silently** — completely bypassing the tiers.
2. **We do not patch hermes-agent source** for this (it is an upstream checkout;
   `hermes update` stashes local changes — config.yaml:118
   `non_interactive_local_changes: stash`). See Open Question #1 for the optional
   defense-in-depth patch.

**Therefore: the dashboard mediates.** The agent never runs `shortcuts` directly; it
calls a thin wrapper CLI that talks to a new aux module on the existing dashboard
(127.0.0.1:7788). The bus itself calls `permissions.decide()` — the *same* policy
engine, same `permissions.json`, same floors, same sidecar trust-clamp, same audit
log, same Trust panel — before any `subprocess` ever spawns.

### Rejected alternatives
- **Hermes skill that teaches raw `shortcuts run`** — zero permission gating (fact #1).
  Rejected outright; the skill we DO ship explicitly forbids the raw CLI.
- **Local MCP server** (config.yaml `mcp_servers:` exists, line 150) — a second
  process to babysit, and MCP tool calls don't route through `permissions.decide()`
  either (only `mcp_elicitation` events do). More moving parts, same gap.
- **Patching `DANGEROUS_PATTERNS`** — makes the RAW CLI ask, but gives only
  binary ask/allow, not per-shortcut classes, and creates a merge liability on every
  `hermes update`. Kept as an optional add-on (Open Question #1), not the mechanism.

---

## 1. Goal & acceptance criteria

**Goal:** a governed action bus where each enrolled Shortcut has a user-assigned risk
class, runs resolve through the P1.3 engine (AUTO / ASK / NEVER + floors + trust
clamp), ASK surfaces a real approval the user answers, and every executed run is a
flight-recorder row and a metrics event.

Acceptance (maps to DEVPLAN "done means"):
- [ ] ≥5 user-chosen Shortcuts enrolled and callable from hub chat AND Telegram
      (Telegram v1 = run request queues, approval happens in the dashboard).
- [ ] Tier matrix proven live: a `shortcut-read` shortcut set AUTO runs silently with
      an `auto-approved` audit row; the same shortcut at shipped default ASKs; a
      `shortcut-irreversible` shortcut can NEVER be set to auto (403 from
      `permissions_set`, clamped by `decide()` even if permissions.json is hand-edited).
- [ ] Every executed run appears in `/api/recorder` (tool `shortcut_run`) with
      duration, outcome, capped input/output; `/api/undo` refuses it.
- [ ] Unenrolled shortcuts are un-runnable through the bus (hard deny, audited).
- [ ] All bus decisions appear in the Trust panel's recent-audit feed and per-class
      weekly stats without any Trust-panel code changes.
- [ ] Zero regressions: dashboard boots with the new aux module even if
      `shortcuts` CLI is missing (module degrades to "unavailable", never throws —
      aux loader contract, server.py:2074–2083).

---

## 2. Ground truth (verified integration points)

| Thing | Verified location |
|---|---|
| `shortcuts` CLI | `/usr/bin/shortcuts`; `run` supports `-i/--input-path`, `-o/--output-path`, `--output-type <UTI>`; `list --show-identifiers` prints `Name (UUID)`; 9 shortcuts currently installed (e.g. `Skip Forward (BF36463C-41C5-456E-9D19-BDE047BCE11E)`) |
| Aux-module loader | server.py:2071–2083 — `_AUX_FILES = ["expanders_extra.py"] + sorted(aux_*.py)`, exec'd into server globals; a failing module logs and is skipped. Sorted order puts `aux_shortcuts.py` AFTER `aux_metrics.py`, `aux_permissions.py`, `aux_recorder.py`, so their globals exist at our load time |
| Route registry | `register_get(path, fn)` / `register_post(path, fn)` (server.py:2043–2048); handlers get `RouteCtx` with `.query`, `.body`, `.q1(key)` (server.py:2051–2063); return dict or `(obj, status)` |
| Aux JS serving | any `/aux_*.js` served with `no-store` (server.py:2126–2128) |
| Permission engine | `dashboard/permissions.py` — REAL sibling module (`import permissions`), NOT exec-included (permissions.py:3–7). `decide(payload)` → `{tier, class, classes, pattern_key, reason, clamped}`; never raises; fail-safe ASK (433–496). `CLASS_META` (17 classes, 65–134), `CLASS_ORDER` (137–144), floors clamped at write (669, 689) AND read (464), sidecar trust-clamp (474–476), `audit(job, payload, verdict, action, choice)` (517–546), `permissions_payload()` iterates `CLASS_ORDER` → **new classes appear in the Trust panel with zero JS changes** (600–625; aux_trust.js:176–226 renders `data.classes` dynamically, grouped by risk band) |
| Pattern-key overrides | `permissions.json` `patterns{key→tier}` beats class tier (`_pattern_tier`, 415–421); `set_pattern` classifies unknown keys via `_heuristic` (688) — we extend `_heuristic` with bus prefixes so per-shortcut overrides floor-check correctly |
| Enforcement seam precedent | hermes_rpc.py:279–310 — sends only `once`/`deny` upstream, audits `auto-approved` / `auto-denied` / `asked` / `user-approve` / `user-deny`. The bus mirrors this action vocabulary so Trust-panel stats (`_recent_stats`, permissions.py:578–594) aggregate bus runs for free |
| Flight recorder | `recorder_record_local(tool, target, kind="write", reversible="yes", source="dashboard", **kw)` (aux_recorder.py:533–571) — kw: `args`, `session`, `status`, `summary`, `after_state`, `ts`, `tool_call_id`; INSERT OR IGNORE into `actions`; `UNDO_WHITELIST = {"write","shell"}` so kind `"other"` is undo-refused by design (aux_recorder.py:74) |
| Metrics | `metrics_record(kind, **fields)` / `metrics_count(name, n)` (aux_metrics.py:128, 140) — best-effort JSONL in `~/.hermes/metrics/`, never raises |
| Hub widget contract | CLAUDE.md:73–76 — mutate `WIDGETS[id]`, `EXPANDERS[id]`, append to layout; JS sets `RENDER[id]`, `EXPAND_RENDER[id]`, `WICONS[id]` (working example: aux_claude_usage.py:429–432 + aux_claude_usage.js) |
| Skill format | `~/.hermes/skills/<category>/<name>/SKILL.md` with YAML frontmatter incl. `prerequisites.commands` (verified: skills/apple/apple-reminders/SKILL.md); `apple/` category exists |
| Aux gotcha | **never `from datetime import datetime` in an aux module** — alias imports only (CLAUDE.md:66–72) |
| Approvals config | `approvals.mode: manual` (config.yaml:110–111); `hermes -z` fails closed on approvals (CLAUDE.md:164) — cron/Telegram surfaces resolve via the gateway queue, not the hub seam |

---

## 3. Data model

### 3.1 Registry — `~/.hermes/dashboard/shortcuts.json` (mode 600, atomic writes)
```json
{
  "version": 1,
  "updated": 1751700000.0,
  "shortcuts": [
    {
      "id": "BF36463C-41C5-456E-9D19-BDE047BCE11E",   // UUID from `shortcuts list --show-identifiers` — the ONLY key ever passed to subprocess
      "name": "Hermes Ping",                          // display; refreshed from `shortcuts list` (renames don't dodge policy: UUID is stable)
      "class": "shortcut-read",                       // shortcut-read | shortcut-reversible | shortcut-irreversible
      "confirmed": true,                              // user clicked class confirmation in the UI; false ⇒ treated as shortcut-irreversible (§7)
      "enabled": true,
      "timeout_s": 60,                                // subprocess kill deadline, clamp 5–300
      "accepts_input": false,                         // if false, `input` on run requests is rejected
      "note": "returns 'pong'",
      "added": 1751700000.0
    }
  ]
}
```
Corrupt/missing file ⇒ empty registry ⇒ every run denies (fail closed).

### 3.2 New permission classes — small patch to `dashboard/permissions.py`
(shared file ⇒ **orchestrator integrates**, per the aux-module rule)

Add to `CLASS_META` (mirroring existing entry shape, permissions.py:65–134):
```python
"shortcut-read": {
    "label": "Shortcuts — read-only", "risk": "low",
    "default": "ask", "floor": "",
    "desc": "Enrolled Shortcuts the user marked read-only (return data, change nothing)."},
"shortcut-reversible": {
    "label": "Shortcuts — reversible", "risk": "med",
    "default": "ask", "floor": "",
    "desc": "Enrolled Shortcuts whose effect the user can undo by hand (toggle, timer)."},
"shortcut-irreversible": {
    "label": "Shortcuts — irreversible", "risk": "high",
    "default": "ask", "floor": "ask",
    "desc": "Enrolled Shortcuts that send, post, delete, or spend. Can never run silently."},
```
- `CLASS_ORDER`: insert `"shortcut-irreversible"` after `"unknown"` (high band) and
  `"shortcut-reversible"`, `"shortcut-read"` after `"mcp-consent"` / before
  `"read-only"` — Trust panel bands render by position (aux_trust.js:206–226).
- `_heuristic(key)` (permissions.py:285–299): add, ABOVE existing rules,
  ```python
  if k.startswith("shortcut-irreversible:"): return "shortcut-irreversible"
  if k.startswith("shortcut-reversible:"):   return "shortcut-reversible"
  if k.startswith("shortcut-read:"):         return "shortcut-read"
  ```
  This makes both `decide()` and `set_pattern`'s floor check (permissions.py:688–690)
  resolve bus keys to the right class with **no change to `decide()` itself** — so we
  inherit floors, sidecar trust-clamp, fail-safe ASK, and the audit format untouched.
- **Deliberately consistent with P1.3's shipped posture:** `default: "ask"` for all
  three (`SHIPPED_DEFAULT`, permissions.py:54). Installing the bus changes nothing
  until the user flips a class to AUTO in the Trust panel. DEVPLAN's "read-only auto"
  is achieved by the *user* opting `shortcut-read` → AUTO, exactly like every other
  auto-capable class.

### 3.3 Pattern keys (per-shortcut overrides for free)
The bus computes `pattern_key = "<class>:<uuid>"` from the **registry** (the agent
never supplies it) and calls `permissions.decide({"pattern_key": pk})`. Because the
key embeds the class, `permissions.json`'s existing `patterns` map + the existing
`POST /api/permissions {op:"set_pattern"}` API give per-shortcut tier overrides with
correct floor enforcement, zero new code. Re-classing a shortcut changes its key, so
stale overrides expire automatically.

### 3.4 Pending-approval queue (in-memory, module-level)
```python
_SB_PENDING = {}   # token -> {id, name, class, input, requested_ts, expires_ts,
                   #           source, state: pending|approved|denied|expired,
                   #           result: None|{...}}
```
- token = `uuid.uuid4().hex`; TTL 600s, sweeper expires → auto-deny + audit.
- Deliberately NOT persisted: a dashboard restart voids consent-in-flight
  (fail closed; wrapper surfaces "bus restarted — ask again").

### 3.5 Run records
No new DB. Executed runs → recorder rows (§5.4); decisions → the P1.3 audit JSONL
(`~/.hermes/dashboard/permissions-log.jsonl`) via `permissions.audit()`; latency →
metrics JSONL via `metrics_record`.

---

## 4. Backend — `dashboard/aux_shortcuts.py` (new aux module, agent-buildable)

Namespace prefix `_sb_*` / `SB_*` / `shortcuts_*` (collision-free with server.py
globals — same discipline aux_metrics documents at its header). Imports aliased per
the datetime gotcha. `import permissions as _sb_perm` (real module, same
`sys.modules` instance as the chat seam ⇒ one lock, one policy cache).

### 4.1 Endpoints
| Route | Verb | Purpose |
|---|---|---|
| `/api/shortcuts` | GET | registry (enrolled, with class/tier resolved via `_sb_perm.decide` dry-run) + `installed` (parsed `shortcuts list --show-identifiers`, 30s cache) + `pending` count + last 20 runs (from recorder, `tool='shortcut_run'`) |
| `/api/shortcuts/enroll` | POST | `{id, class, timeout_s?, accepts_input?, note?}` — id must exist in the installed list; class must be one of the 3; writes registry with `confirmed:false` (§7-I3) |
| `/api/shortcuts/confirm` | POST | `{id}` — sets `confirmed:true` (the UI's explicit "I understand this class" click) |
| `/api/shortcuts/remove` | POST | `{id}` — deletes entry |
| `/api/shortcuts/run` | POST | `{name_or_id, input?, source?}` — THE agent entrypoint (also the UI test-run). Returns `{status:"done", output, duration_ms}` \| `{status:"pending", token, message}` \| `{status:"denied", reason}` \| `{status:"error", error}` |
| `/api/shortcuts/wait` | GET | `?token=` — long-poll ≤55s; returns pending/approved+result/denied/expired/unknown |
| `/api/shortcuts/pending` | GET | live queue for the hub approval card |
| `/api/shortcuts/approve` | POST | `{token, choice: "approve"\|"deny"}` — resolves a pending entry; approve ⇒ executes inline (caller is the UI thread; run in a worker thread, `wait` picks up the result) |

### 4.2 Run pipeline (`/api/shortcuts/run`)
```
resolve name_or_id against registry (by UUID first, then unique enabled name)
  ├─ not enrolled/disabled → audit(action="auto-denied", class="shortcut-irreversible"?
  │    no — use a synthetic verdict {tier:"never", class:"unknown"}); return denied     [never spawns]
  ├─ input given but accepts_input false → error (no audit needed; nothing decided)
effective_class = entry.class if entry.confirmed else "shortcut-irreversible"
pk = effective_class + ":" + entry.id
v = _sb_perm.decide({"pattern_key": pk, "command": "shortcuts run " + entry.name})
  ├─ tier auto  → audit("auto-approved") → execute → record+metrics → return done
  ├─ tier never → audit("auto-denied")  → return denied(reason=v.reason)
  └─ tier ask   → audit("asked") → enqueue _SB_PENDING → return pending(token)
        └─ /api/shortcuts/approve:
              approve → audit("user-approve") → execute → record → result on token
              deny    → audit("user-deny")   → record denied? NO — recorder holds
                        executed runs only; denials live in the audit log (§5.4)
```
`permissions.audit()` is called with `job="shortcut-bus:"+source` (it accepts a plain
string — permissions.py:527) and the same action vocabulary as hermes_rpc.py:289–310,
so Trust-panel weekly stats and the recent feed aggregate bus activity unmodified.

### 4.3 Execution (the only place a subprocess spawns)
```python
argv = [SB_BIN, "run", entry_id]          # SB_BIN = os.environ.get("HERMES_SHORTCUTS_BIN", "/usr/bin/shortcuts")
# input:  write to NamedTemporaryFile under ~/.hermes/dashboard/shortcut-io/ (dir 700, file 600) → ["-i", path]
# output: ["-o", out_path, "--output-type", "public.utf8-plain-text"]
subprocess.run(argv, capture_output=True, timeout=entry.timeout_s, text=True, stdin=DEVNULL)
```
- **argv list, UUID only, no shell** — a shortcut named `; rm -rf ~` is inert.
- `SB_BIN` env override exists ONLY for the stub-runner test harness (§9).
- On `TimeoutExpired`: process killed by subprocess, outcome `error:timeout`
  (a Shortcut that opens a UI prompt hangs headless — the timeout is the backstop).
- Output = `-o` file if non-empty else stdout; capped 64KB returned / 2KB into
  recorder `after_state`; temp files deleted in `finally`.
- Concurrency: per-UUID `threading.Lock` (no double-fire) + global semaphore(2).

### 4.4 Recorder + metrics glue (only for EXECUTED runs)
```python
recorder_record_local(
    tool="shortcut_run", target=entry.name, kind="other", reversible="no",
    source="shortcut-bus", args={"id": entry.id, "class": effective_class,
                                 "tier": v["tier"], "input": input_capped_2k},
    status="done" if rc == 0 else "error",
    summary="%s · %s · %.1fs · %s" % (entry.name, v["tier"], dur, outcome),
    after_state={"exit_code": rc, "output_head": out[:2048], "duration_s": dur})
```
- `kind="other"`, `reversible="no"` ⇒ `/api/undo` refuses (UNDO_WHITELIST,
  aux_recorder.py:74) — correct: side effects of a Shortcut are not file-restorable,
  same rationale as the recorder's `computer` kind (aux_recorder.py:69).
- `recorder_record_local` and `metrics_record` are exec-shared globals loaded before
  us (sorted `_AUX_FILES`); still wrap both in `try/except NameError` so a failed
  sibling module can't take the bus down (loader tolerance contract).
```python
metrics_record("shortcut_run", name=entry.name, tier=v["tier"], outcome=outcome, ms=int(dur*1000))
metrics_count("shortcut_runs")
```

---

## 5. Agent surface — wrapper CLI + skill (no hermes-agent changes)

### 5.1 `~/.hermes/bin/hermes-shortcut` (new, chmod 755; stdlib python3)
`~/.hermes/bin` already holds user tools (`tirith`, `uv`, `uvx`).
```
hermes-shortcut list                      → GET /api/shortcuts  (prints enrolled: name · class · current tier)
hermes-shortcut run <name> [--input TEXT|-]  → POST /api/shortcuts/run
    done    → prints output, exit 0
    pending → prints "Approval needed — check the Hermes dashboard", then polls
              GET /api/shortcuts/wait?token= in a loop up to 120s total:
                approved → prints output, exit 0
                denied   → exit 2 ("Denied: <reason>")
                expired/unknown → exit 3 ("Approval timed out / bus restarted")
    denied  → exit 2;  error → exit 4;  unenrolled → exit 5 with the enrolled list
```
Talks only to `http://127.0.0.1:7788`; ~80 lines; urllib only.
Because the wrapper is a plain fast terminal command matching no dangerous pattern,
hermes's terminal tool runs it without its own approval — **the consent happens
inside the bus**, which is the design: one governed choke point instead of a regex
race.

### 5.2 Skill — `~/.hermes/skills/apple/shortcuts-bus/SKILL.md`
Frontmatter mirrors apple-reminders (name/description/version/platforms/
`prerequisites.commands: [hermes-shortcut]`, tags `[Shortcuts, automation, macOS]`).
Body teaches:
- ALWAYS `hermes-shortcut list` first; run only enrolled names.
- **NEVER call `shortcuts run` / `shortcuts` directly — it bypasses the user's
  permission tiers.** (Stated as a hard rule; residual enforcement gap in §7-R1.)
- `pending` means the user is being asked — report that and stop; do not retry in a
  loop; exit codes 2/3 mean denied/timed out — respect the answer, never re-ask
  in the same turn.
- Telegram turns: tell the user approval waits in the dashboard.

### 5.3 Why not a hermes custom tool?
A first-class tool needs hermes-agent source edits (tools/registry, toolset wiring)
— upstream checkout, update-stashed, and quality-gate risk for zero governance gain:
the wrapper already gives one-call ergonomics and the bus owns consent. Revisit only
if the restraint-router work (P3.2) wants schema-typed tool calls.

---

## 6. Frontend — `dashboard/aux_shortcuts.js` + widget

- **Widget** `WIDGETS["shortcuts"] = {title:"Shortcuts", icon:"zap", size:"s",
  cat:"system", provider:w_shortcuts}` + `EXPANDERS["shortcuts"]`; JS registers
  `RENDER`, `EXPAND_RENDER`, `WICONS` (two-tone SVG, no emoji — CLAUDE.md rules).
  Body: enrolled count, last run (name · outcome · ago), **pending badge**.
- **Pending approval card**: widget body polls `/api/shortcuts/pending` on the
  existing hub cadence; when non-empty renders name, class chip (risk accent, reuse
  aux_trust.js `RISK_ACCENT` colors), capped input preview, Approve/Deny → POST
  `/api/shortcuts/approve`. `confirm()` is safe in the WKWebView (dialogs
  implemented — CLAUDE.md:77–81).
- **Pop-out (`EXPAND_RENDER`)**: enroll UI — installed-but-unenrolled list (from
  `installed`), class picker with the three descriptions + explicit red copy on
  irreversible ("sends/posts/deletes — every run will ask"), the `confirmed`
  checkbox flow, per-shortcut tier display with a deep-link hint to the Trust panel,
  recent-runs table, test-run button.
- **Trust panel: zero changes** — the three classes render automatically
  (aux_trust.js:176/206 iterates `data.classes`).
- After editing, reload the WebView (⌘R in the app — CLAUDE.md:172–174). Verify the
  expander headless by evaling aux_shortcuts.js in node with stubbed helpers
  (established pattern, CLAUDE.md:109–112).

---

## 7. Security & safety — invariants

- **I1 — No ungoverned spawn.** The only `subprocess` call sits behind
  `permissions.decide()`; unenrolled/disabled ⇒ deny before decide; registry
  unreadable ⇒ deny. Fail closed everywhere (mirrors permissions.py's
  "degrade toward ASK, never AUTO").
- **I2 — Irreversible can never auto.** `floor: "ask"` is enforced at write time
  (`permissions_set` 403) AND read time (`decide()` clamp) — hand-editing
  `permissions.json` to `"shortcut-irreversible":"auto"` still ASKs, and the sidecar
  hash-mismatch additionally suspends ALL auto until Review & re-trust
  (permissions.py:27–28, 474–476). Inherited, not re-implemented.
- **I3 — Rogue self-enrollment is contained.** The dashboard is unauthenticated on
  loopback, so the agent *could* curl `/api/shortcuts/enroll`. Containment:
  enrollment always lands `confirmed:false`, and unconfirmed entries are treated as
  `shortcut-irreversible` (floor ask) regardless of requested class — a self-enrolled
  shortcut still asks the user on every single run, showing name + class. `confirm`
  is UI-only by convention; even if the agent curls it, the class ceiling still
  applies and every ASK card names the shortcut. Net: the agent can at most create
  more questions, never silent runs.
- **I4 — No injection surface.** subprocess argv by UUID only; UUID validated
  against `shortcuts list --show-identifiers` at enroll; names never reach the shell.
- **I5 — Shipped default changes nothing.** All three classes default ASK; AUTO
  requires two explicit user acts (confirm class + flip tier in Trust panel).
- **I6 — Sends stay governed.** Gmail-send-never and Telegram-lock are untouched
  (different subsystems). A Shortcut that sends *anything* belongs in
  `shortcut-irreversible` — enroll-UI copy states this rule; it is user-enforced by
  class choice, machine-enforced as always-confirm.
- **I7 — Privacy caps.** Recorder `args.input` capped at 2KB, `output_head` 2KB,
  wrapper stdout 64KB; recorder.db is already chmod 600 (aux_recorder.py:145);
  temp IO files 600 under a 700 dir, deleted in `finally`.
- **I8 — Consent does not survive restarts.** Pending queue is memory-only; TTL
  600s; expiry audits `auto-denied`.
- **R1 — Residual risk (documented, not hidden):** the agent can still type raw
  `shortcuts run` into its terminal tool and hermes will not ask (fact §0.1). Layers:
  the skill forbids it; the Console timeline (`/api/console`) shows every terminal
  call for spot-audit; optional hardening = Open Question #1. This gap exists TODAY
  without the bus — the bus strictly reduces it by giving the agent a sanctioned,
  cheaper path (list+run+output in one call vs. blind CLI).

---

## 8. Edge cases

| Case | Behavior |
|---|---|
| Duplicate shortcut names | enroll/run resolve by UUID; run-by-name requires a unique enabled match, else `error:ambiguous` listing candidates |
| Shortcut renamed in Shortcuts.app | UUID stable ⇒ policy sticks; GET refreshes display name from `shortcuts list`; recorder rows keep name-at-run-time |
| Shortcut deleted after enroll | subprocess exits non-zero → `error`, recorder row, widget flags entry "missing" |
| Shortcut opens a UI / prompts for input | hangs → killed at `timeout_s` → `error:timeout` (documented in enroll UI: "must run headless") |
| `accepts_input:false` but input sent | rejected pre-decide (`error:no-input-allowed`) |
| Binary/huge output | `--output-type public.utf8-plain-text`; undecodable → `errors="replace"`; caps per I7 |
| Two runs of the same shortcut | per-UUID lock: second waits ≤5s then `error:busy` |
| Approval while turn already timed out | run still executes on approve; result lands in recorder + `/api/shortcuts` recent list; wrapper long-poll (120s) < pending TTL (600s), so late approvals are visible even if the agent stopped waiting |
| Dashboard restart with pending run | queue lost; `wait` → `unknown` → wrapper exit 3; nothing executed |
| permissions.py patch missing (partial deploy) | `_heuristic` maps `shortcut-*:` keys to `unknown` (floor ask) ⇒ every run ASKs; nothing silent. Degradation is safe by construction |
| `shortcuts` CLI absent (non-macOS dev box) | `installed:[]`, runs `error:unavailable`, module loads fine |
| Registry hand-edited to invalid class | normalize on load: invalid class ⇒ treated as `shortcut-irreversible` + `confirmed:false` |

---

## 9. Test plan (no `--yolo` — forbidden; no real sends; no live Shortcut needed until the final drill)

**Layer 1 — engine (pure, no subprocess):**
- `permissions.decide` matrix via the existing dry-run endpoint
  `GET /api/permissions/test?pattern_key=shortcut-read:<uuid>` (never mutates —
  permissions.py:638–641): shipped default ask for all 3 classes; class set AUTO ⇒
  auto for read/reversible; irreversible+auto ⇒ 403 on `set_class`, and a hand-written
  `"shortcut-irreversible:X":"auto"` pattern still resolves ask (floor clamp);
  sidecar tamper (append a byte to permissions.json) ⇒ `trusted:false` ⇒ auto→ask
  everywhere; `retrust` restores. Restore policy file after.

**Layer 2 — bus with a stub runner (zero real Shortcuts executions):**
- `HERMES_SHORTCUTS_BIN=/tmp/sb-stub.sh` (stub: `list --show-identifiers` prints two
  fake `Name (UUID)` lines; `run` echoes/sleeps/exits per UUID). Restart dashboard
  with the env var; drive with curl:
  enroll→confirm→run happy path (auto + ask + deny); unenrolled deny; unconfirmed
  entry forced to irreversible; timeout kill (stub sleeps 10 > timeout_s 5);
  concurrent `busy`; pending expiry auto-deny; approve-executes / deny-skips;
  `wait` long-poll semantics; audit rows land in
  `~/.hermes/dashboard/permissions-log.jsonl` with the exact action vocabulary;
  recorder rows appear in `/api/recorder` and `/api/undo` refuses them; metrics JSONL
  gains `shortcut_run` records. Then unset env, restart, confirm real `shortcuts list`
  parse against the 9 installed shortcuts (list-only — nothing runs).

**Layer 3 — wrapper + skill:**
- `hermes-shortcut list/run` against the stub bus: exit codes 0/2/3/4/5; pending →
  approve in hub → output arrives in-poll. Headless-eval aux_shortcuts.js in node
  (stubbed helpers + live `/api/expand` JSON) per the established renderer check.

**Layer 4 — live drill (single benign shortcut; the P1.4-style proof):**
- NEEDS-YOU: user creates "Hermes Ping" (Text "pong" → Stop and Output) — 60s in
  Shortcuts.app; enrolls via the widget as `shortcut-read`, confirms.
- Drill script (mirrors the P1.4 approval drill): (1) hub chat "run the Hermes Ping
  shortcut" → skill → wrapper → ASK card → Approve → "pong" in-turn; recorder row;
  audit `asked`+`user-approve`. (2) Trust panel: shortcut-read → AUTO → rerun →
  silent, `auto-approved` audit, status chip "auto-approved · …". (3) Deny path once.
  (4) Telegram: same ask → confirm reply says approval waits in dashboard; approve
  there; result visible in widget recent-runs. (5) Flip class to irreversible in
  registry → verify AUTO no longer honored (floor). Reset tiers to ASK afterward.
- Nothing in the drill sends, posts, deletes, or spends.

---

## 10. Effort & sequencing

| # | Work | Size | Who |
|---|---|---|---|
| 1 | permissions.py patch (3 CLASS_META entries, CLASS_ORDER, 3 `_heuristic` prefix lines) + Layer-1 tests | S (~35 lines) | **orchestrator** (shared file) |
| 2 | aux_shortcuts.py — registry, decide pipeline, executor, pending queue, 8 routes | M (~450 lines) | agent (aux module) |
| 3 | recorder/metrics glue + stub-runner Layer-2 suite | S/M | same agent |
| 4 | aux_shortcuts.js — widget, pending card, enroll pop-out; WKWebView reload + node eval | M (~250 lines) | agent |
| 5 | `hermes-shortcut` wrapper + SKILL.md | S (~120 lines total) | agent |
| 6 | Live drill + NEEDS-YOU entries + CHANGELOG | S | orchestrator + user |

Sequencing 1→2/5 in parallel→3→4→6. Roughly one focused day of agent time; the only
user-blocking step is #6 (batch into NEEDS-YOU.md: create "Hermes Ping", pick the
five real Shortcuts to enroll, choose their classes).

---

## 11. Open questions (decisions for the user / later)

1. **Defense-in-depth for raw `shortcuts run`:** add one DANGEROUS_PATTERNS entry in
   the local hermes-agent checkout so the raw CLI at least ASKs (then map its
   pattern_key into `PATTERN_CLASS`). Cost: local-source divergence that
   `hermes update` will stash (config.yaml `non_interactive_local_changes: stash`) —
   needs a re-apply note in CLAUDE.md. Worth it? (Recommended: yes, post-v1.)
2. **Telegram inline approval** for pending runs (buttons in the bot chat) — touches
   the locked gateway; v2 at earliest, and only with explicit user sign-off.
3. **Unify the pending card with the chat approval UI** when a run originates from a
   hub turn (single approval surface). Needs job-id plumbing through the wrapper;
   v1 keeps the widget card to avoid touching hermes_rpc.
4. **Recorder input redaction** — per-shortcut `redact_input:true` flag for shortcuts
   fed sensitive text? (Cheap to add to the registry schema now, default false.)
5. **`--output-type`** — is `public.utf8-plain-text` the right default, or fall back
   to raw stdout when the shortcut outputs files/images? (v1: text only; image
   output is a natural P4 widget tie-in.)
6. **Which five Shortcuts** ship the DEVPLAN "done means" — user picks; the current 9
   installed (`Skip Forward`, `Take a Break`, `Text Last Image`, …) include obvious
   candidates plus at least one (`Spam Text`) that MUST be irreversible if enrolled
   at all.
