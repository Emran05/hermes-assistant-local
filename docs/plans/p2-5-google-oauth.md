# Google Workspace OAuth (Gmail read+draft + Calendar) — design spec (P2)

Workstream P2.5. Wires Google OAuth so Hermes can **read Gmail + create drafts**
(never send) and **read/manage Calendar** (every write approval-gated). The
dashboard owns a minimal-scope OAuth flow, writes creds to the exact locations
the existing `google-workspace` skill already reads, and unblocks the `today`
widget with real Google Calendar data.

> Ground truth verified 2026-07-05 by reading the on-disk skill + dashboard. Two
> load-bearing surprises drove this design and are called out inline:
> 1. The shipped `setup.py` **hardcodes** `gmail.send` + `gmail.modify` + full
>    `calendar` + full `drive` scopes — it CANNOT be used as-is without violating
>    the safety invariant, and it has **no** `--services`/`--format` flags (the
>    SKILL.md documents flags that do not exist on disk — stale docs).
> 2. `google_api.py` has **no draft command** (only `send`/`reply`) and there is
>    **no Gmail OAuth scope that grants draft-create but forbids send**
>    (`drafts.create` requires `gmail.compose`, which also authorizes
>    `messages.send`). "Never send" is therefore enforced at the **tooling +
>    scope-minimization + approval** layers, documented honestly below — not by a
>    magic send-proof scope, which does not exist.

---

## Goal & acceptance criteria

Done means all of the following are true and demonstrable:

1. **Connect flow works end to end from the dashboard.** From a fresh state
   (no `~/.hermes/google_token.json`), the user completes: provide
   `client_secret.json` → get auth URL → consent in browser → paste redirect
   URL → `GET /api/google/status` returns `{"connected": true}` with the exact
   granted scope set.
2. **Minimal scopes only.** The stored token's `scopes` list is *exactly*
   `gmail.readonly`, `gmail.compose`, `calendar.events` — and the exchange step
   **refuses and auto-revokes** if Google ever returns a scope outside that set
   (verified by a unit test that feeds a forged callback with `gmail.send`).
3. **Gmail read works.** `python <venv> <skill>/scripts/google_api.py gmail
   search "is:unread" --max 5` returns JSON messages using the minimal token
   (no re-consent, no `invalid_scope` on refresh).
4. **Gmail draft works, send does not exist.** `google_draft.py` creates a real
   Gmail draft visible in the Gmail Drafts folder; there is **no reachable
   sanctioned command that calls `messages.send`** (the skill's `gmail send`/
   `gmail reply` are neutered + self-healing; `grep -R "messages().send"` over
   the *reachable* wrappers returns only the neuter stub).
5. **Calendar read unblocks the `today` widget.** With Google connected, the Hub
   `today` card and its pop-out render Google Calendar events (not icalBuddy);
   when disconnected they fall back to the existing icalBuddy path unchanged.
6. **Calendar writes are approval-gated, dashboard-initiated writes are zero.**
   The dashboard performs no Google writes itself; any event create/update/delete
   happens only through the agent chat/tool path and is confirmed per the skill's
   Rule 1. Proactive (Watchtower) code may read Calendar/Gmail but may only
   **draft/notify**, never send or auto-mutate (NOTIFY-ONLY boundary).
7. **Status is honest & recoverable.** The connection card shows connected/
   partial/expired/broken states, the exact scopes, the account email, and a
   Disconnect (revoke) that leaves the system in a clean fresh state.
8. **No secret ever enters the repo.** `client_secret.json` and `google_token.json`
   live only in `~/.hermes` at mode `600`; `.gitignore` covers them; the
   dashboard never logs token/secret bytes.

---

## Data model (files — exact shapes, all under `~/.hermes`, mode 600)

All paths match what the existing skill (`google_api.py`, `setup.py`) already
reads, so the skill's read path consumes our token with zero changes.

### `~/.hermes/google_client_secret.json` (user-provided; Desktop OAuth client)
Downloaded from Google Cloud Console; copied verbatim by us. Shape (Google's):
```json
{ "installed": { "client_id": "…apps.googleusercontent.com",
                 "project_id": "hermes-assistant-xxxx",
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token",
                 "client_secret": "…",
                 "redirect_uris": ["http://localhost"] } }
```
Validation: must contain `installed` (Desktop) or `web` key, else refuse.

### `~/.hermes/google_token.json` (we write; `authorized_user` format)
Byte-compatible with `Credentials.from_authorized_user_file` + the skill's
`_stored_token_scopes()`. The `scopes` field is the source of truth for what was
granted (the skill refreshes against exactly these):
```json
{ "type": "authorized_user",
  "client_id": "…apps.googleusercontent.com",
  "client_secret": "…",
  "refresh_token": "1//…",
  "token": "ya29.…",
  "token_uri": "https://oauth2.googleapis.com/token",
  "scopes": [ "https://www.googleapis.com/auth/gmail.readonly",
              "https://www.googleapis.com/auth/gmail.compose",
              "https://www.googleapis.com/auth/calendar.events" ] }
```

### `~/.hermes/google_oauth_pending.json` (transient PKCE state; deleted on exchange)
```json
{ "state": "…", "code_verifier": "…", "redirect_uri": "http://localhost:1",
  "created": 1751700000 }
```

### `~/.hermes/google_oauth_last_url.txt`
The exact auth URL, one line (parity with SKILL.md; convenience for the user).

### `~/.hermes/dashboard/google_status.json` (our cache; not sensitive — no tokens)
```json
{ "connected": true, "partial": false, "email": "you@example.com",
  "scopes": ["…gmail.readonly","…gmail.compose","…calendar.events"],
  "missing": [], "reason": "", "checked_at": 1751700123.4 }
```

### Constants (defined once, in `dashboard/google_oauth_driver.py`)
```python
SAFE_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
               "https://www.googleapis.com/auth/gmail.compose",
               "https://www.googleapis.com/auth/calendar.events"]
# Any granted scope NOT in SAFE_SCOPES ∪ {openid, userinfo.email, userinfo.profile}
# triggers immediate revoke + refuse. These are the hard-forbidden signals:
FORBIDDEN_SUBSTR = ["gmail.send", "gmail.modify", "gmail.insert",
                    "mail.google.com", "/auth/drive", "spreadsheets",
                    "documents", "contacts"]
REDIRECT_URI = "http://localhost:1"   # OOB replacement, matches setup.py
```

---

## Backend

New files (all committed, dashboard-owned; nothing edited in server.py):
- `dashboard/aux_google.py` — exec-loaded aux module; registers routes, wraps the
  `today` widget providers, runs the light status thread. Stdlib-only; shells
  into the venv for anything touching Google libs.
- `dashboard/google_oauth_driver.py` — run by the **venv** python
  (`~/.hermes/hermes-agent/venv/bin/python`, verified present + has
  `googleapiclient`/`google_auth_oauthlib`). One process per op, JSON on stdin →
  JSON on stdout. Mirrors the `aux_recorder._ckpt` subprocess pattern exactly.
- `dashboard/google_draft.py` — run by the venv python; the **only** sanctioned
  Gmail-write interface; drafts only (`users().drafts().create`); contains no
  `messages().send` call anywhere. Agent-invokable.
- `dashboard/aux_google.js` — connection card in the Mind view.

Venv shell helper (in aux_google.py, copied from aux_recorder's `_PY`/`_ckpt`):
```python
_VENV_PY = os.path.join(HOME, ".hermes","hermes-agent","venv","bin","python")
_PY = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable
_DRIVER = os.path.join(HERE, "google_oauth_driver.py")
def _drv(op, **kw):   # 60s timeout; returns {"error":…} on any failure, never raises
    p = subprocess.run([_PY, _DRIVER, op], input=json.dumps(kw),
                       capture_output=True, text=True, timeout=60)
    try: return json.loads(p.stdout or "{}")
    except Exception: return {"error": (p.stderr or "driver failed")[:200]}
```

### Endpoints (all via `register_get`/`register_post`; handlers take `ctx`)

**GET `/api/google/status`** — connection state for the card + widgets.
- req: none (optional `?fresh=1` bypasses cache).
- Fast path: if `google_token.json` absent → `{"connected": false,
  "has_client_secret": <bool>, "reason": "not_connected"}` with **no** venv call.
- Else `_cached("google_status", 60, lambda: _drv("check"))`; the driver refreshes
  the token if expired and returns `{connected, partial, email, scopes, missing}`.
- resp 200: `{ "ok": true, "connected": bool, "partial": bool, "email": str,
  "scopes": [str], "missing": [str], "has_client_secret": bool,
  "checked_at": float }`.
- errors: driver failure → `{"ok": true, "connected": false, "reason":
  "check_failed: …"}` (degrade to disconnected, never 500 the hub).

**POST `/api/google/client_secret`** — store the Desktop client (stdlib only, no venv).
- req: `{ "path": "/Users/…/client_secret_….json" }` **or**
  `{ "json": { "installed": {…} } }`.
- Validates JSON + presence of `installed`/`web` key; atomic-writes to
  `~/.hermes/google_client_secret.json` at `600` (reuse the memory module's
  temp+fchmod(600)+os.replace discipline).
- resp 200: `{ "ok": true, "stored": true }`.
- errors: `("bad_path", 400)` file missing / outside HOME; `("not_json", 422)`;
  `("not_client_secret", 400)` no installed/web key; `("too_big", 413)` > 64 KB.

**POST `/api/google/authurl`** — begin the flow.
- req: `{}`.
- 400 `{"ok": false, "error": "no_client_secret"}` if the client file is absent.
- Else `_drv("authurl")`: builds `Flow.from_client_secrets_file(client_secret,
  scopes=SAFE_SCOPES, redirect_uri=REDIRECT_URI, autogenerate_code_verifier=True)`,
  `authorization_url(access_type="offline", prompt="consent",
  include_granted_scopes="false")` (fresh consent — never incremental, so a
  previously-broad grant can't leak back in), writes `google_oauth_pending.json`
  + `google_oauth_last_url.txt`.
- resp 200: `{ "ok": true, "auth_url": str, "scopes": SAFE_SCOPES }`.

**POST `/api/google/exchange`** — finish the flow (the scope wall lives here).
- req: `{ "code": "http://localhost:1/?code=4/0A…&scope=…"  }` (full redirect URL
  or bare code both accepted; driver parses).
- `_drv("exchange", code=…)`: loads pending, verifies `state`, sets
  `OAUTHLIB_RELAX_TOKEN_SCOPE=1`, `flow.fetch_token(code=…)`, reads
  `creds.granted_scopes`. **Hard guard**: if `granted ⊄ (SAFE_SCOPES ∪ {openid,
  userinfo.email})` OR any `FORBIDDEN_SUBSTR` hit → POST-revoke the token with
  Google, delete it, return `{"error": "scope_violation", "granted": [...]}`.
  Only on a clean subset does it write `google_token.json` (`scopes` = granted).
- resp 200: `{ "ok": true, "email": str, "granted_scopes": [str] }`.
- errors: `("state_mismatch",400)`, `("expired",400,{"fresh_auth_url": …})`
  (driver re-issues a URL like the skill does), `("scope_violation",403)`,
  `("exchange_failed",400)`. On success busts `google_status` + `today_exp` +
  `calendar` caches and appends a recorder line (`recorder_record_local("google_connect",
  email, kind="net", reversible="no", source="dashboard")`).

**POST `/api/google/disconnect`** — revoke + wipe.
- req: `{}`. `_drv("revoke")` → POSTs to `https://oauth2.googleapis.com/revoke`,
  deletes `google_token.json` + pending. resp `{ "ok": true }`. Busts caches;
  recorder line `google_disconnect`.

**GET `/api/google/agenda?days=1`** — read-only calendar, feeds widgets/brief.
- `_cached("google_agenda_"+days, 120, lambda: _drv("agenda", days=n))`; driver
  runs `service.events().list(calendarId="primary", timeMin=<local midnight>,
  timeMax=<+n days>, singleEvents=True, orderBy="startTime")`.
- resp: `{ "ok": true, "connected": true, "days": [ { "date": "2026-07-05",
  "events": [ { "time": "9:00 AM", "end": "9:30 AM", "title": "Standup",
  "all_day": false, "location": "", "htmlLink": "…" } ] } ], "count": 3 }`
  (12-hour times, matching the existing `expand_today` shape). Disconnected →
  `{"ok": true, "connected": false}`.

### Widget unblock (no server.py edit; aux runs *after* inline defs + expanders_extra)
At module load, `aux_google.py` captures the existing providers and overrides the
registry entries so Google wins when connected, icalBuddy otherwise:
```python
_orig_card   = globals().get("macos_calendar")      # WIDGETS["today"]["provider"]
_orig_expand = globals().get("expand_today")         # EXPANDERS["today"]
def _today_card():
    d = _cached("google_cal_card", 120, lambda: _drv("agenda", days=1))
    if d.get("connected"):  return {"available": True, "events": _flatten_today(d)}
    return _orig_card() if _orig_card else {"available": False}
def _today_expand():
    d = _cached("google_cal_expand", 120, lambda: _drv("agenda", days=7))
    if d.get("connected"):  return {"available": True, "days": d["days"], "count": d["count"]}
    return _orig_expand() if _orig_expand else {"available": False}
try:
    if "WIDGETS" in globals(): WIDGETS["today"]["provider"] = _today_card
    if "EXPANDERS" in globals(): EXPANDERS["today"] = _today_expand
except Exception as e: print("[aux_google] widget wrap skipped: %r" % e, file=sys.stderr)
```
(These are *dict-entry* reassignments — not just redefining the global name —
because `WIDGETS`/`EXPANDERS` hold function-object references captured at build.)

### Background thread (guarded, like `aux_recorder._recorder_thread_started`)
```python
if not globals().get("_google_thread_started"):
    globals()["_google_thread_started"] = True
    threading.Thread(target=_google_status_loop, daemon=True).start()
```
`_google_status_loop`: every 30 min, if a token exists, `_drv("check")`, write
`google_status.json`, and on a **transition** into `expired`/`token_revoked`/
`refresh_failed` append ONE recorder line and (optionally) one `hermes send -t
telegram` notice — NOTIFY-ONLY, deduped on state change so it never spams. Never
mutates Google state. Skips entirely while `model_online()` is irrelevant (it is
independent of the model).

### How it respects `permissions.py`
- The dashboard performs **no** Google writes, so no dashboard action needs a
  tier. Calendar create/update/delete and any Gmail action happen only when the
  **agent** calls `google_api.py`/`google_draft.py` via the `terminal` tool
  inside a chat turn — that path already flows through
  `hermes_rpc.run_turn` → `_perm.decide(payload)` for any pattern hermes flags,
  and through the skill's Rule 1 ("confirm before create/delete events") for the
  rest. This spec adds nothing that bypasses that seam.
- The proactive/status thread is strictly read + notify; it never calls
  `approval.respond` and never emits a write. This upholds the Phase-2
  NOTIFY-ONLY law (DEVPLAN §Phase 2: "triggers may only notify, never act").

---

## Frontend

**View:** Mind view (integrations/connections belong with capabilities). One card
`#mind-extra-google` titled "Google Workspace", rendered by wrapping
`window.mindExtras` exactly as `aux_trust.js` wraps it:
```js
var prev = window.mindExtras;
window.mindExtras = async function(){ if(typeof prev==='function'){try{await prev();}catch(e){}} try{await googlePanel();}catch(e){} };
```
Reuses the global helpers `esc()`, `animate()` (Motion One), `revealStagger()`,
`REDUCE`, all `typeof`-guarded (headless-safe). Zero emoji — bespoke two-tone SVG
mark (a minimal envelope+calendar glyph, accent fill + `currentColor` stroke).
12-hour time on the "checked" timestamp.

**The ONE index.html edit** (applied by the orchestrator), alongside the other
aux tags near line 2055:
```html
<script src="/aux_google.js"></script>
```

**States** (driven by `GET /api/google/status`):
- `disconnected` (no token): headline "Not connected" + a **Connect Google**
  button that opens an inline multi-step stepper.
- `need_client_secret`: step 1 active — instructions + a path input (or paste-JSON
  textarea) → `POST /api/google/client_secret`.
- `awaiting_consent`: after `POST /api/google/authurl` — shows the auth URL (mono,
  copy button, and an "Open in browser" that the **user** clicks; it is a trusted
  `accounts.google.com` consent URL), the "the browser will fail at localhost:1 —
  that's expected, copy the whole address bar" note, and a paste box → `POST
  /api/google/exchange`.
- `connected`: green shield, account email, the three granted scopes as chips
  ("Read mail", "Draft mail", "Manage calendar"), a "Send is disabled" affordance
  (explicit, reassuring), last-checked time, and **Disconnect**.
- `partial` / `expired` / `error`: amber banner with the exact reason and a
  "Reconnect" button that restarts at the authurl step.

**Animations:** `revealStagger` the card in with the other Mind cards; the stepper
advances with a short Motion One `animate()` slide; all frozen under `REDUCE`.
The `today` widget needs **no** JS change — it already renders whatever the
provider returns; Google data flows through the same `RENDER['today']`.

---

## Integration points (verified names/paths)

- **Skill dir:** `~/.hermes/skills/productivity/google-workspace/scripts/` —
  `setup.py`, `google_api.py`, `gws_bridge.py`, `_hermes_home.py`,
  `references/gmail-search-syntax.md` (all confirmed on disk).
- **Creds locations (confirmed the skill reads these):** `google_api.py:41-43`
  `TOKEN_PATH = HERMES_HOME/"google_token.json"`, `CLIENT_SECRET_PATH =
  HERMES_HOME/"google_client_secret.json"`; `get_credentials()`
  (`google_api.py:181`) loads via `Credentials.from_authorized_user_file(
  TOKEN_PATH, _stored_token_scopes())` — **honors our minimal `scopes` list** and
  refreshes against exactly those (no `invalid_scope`).
- **Venv python:** `~/.hermes/hermes-agent/venv/bin/python` (→ cpython-3.11);
  `import googleapiclient, google_auth_oauthlib` **succeeds** there, **fails** in
  system `python3`. All Google-lib work must use the venv (dashboard is
  stdlib-only) — same split `aux_recorder.py` already relies on (`_VENV_PY`,
  `_ckpt`).
- **Dashboard aux plumbing (server.py, confirmed):** `register_get`/`register_post`
  (2043/2047), `RouteCtx.q1` (2060), the aux loader
  `_AUX_FILES=["expanders_extra.py"]+sorted(aux_*.py)` exec'd into globals
  (2071-2081), aux JS auto-served by the `path.startswith("/aux_") and endswith
  (".js")` branch (2127). Globals available to aux: `HERE, HOME, DATA, STATE_DB,
  read_json, write_json, _state_lock, _widget_cache, _cached, model_online`.
- **Widget registry:** `WIDGETS["today"]["provider"] = macos_calendar`
  (server.py:1728), `EXPANDERS["today"] = expand_today` (server.py:762);
  `macos_calendar` (1249) + `expand_today` (659) both shell `icalBuddy` and the
  former's own error text already says "Google Calendar can fill this widget once
  connected" — this spec makes that true.
- **Recorder:** `recorder_record_local(tool, target, kind=…, reversible=…,
  source="dashboard")` (`aux_recorder.py:533`) for connect/disconnect/expiry
  audit lines.
- **Frontend hook:** `window.mindExtras` chained at index.html:1828; existing tags
  at 2050-2055; wrapper pattern from `aux_trust.js:18-22`.
- **Telegram notify (optional, expiry only):** `hermes send -t telegram "…"`
  (confirmed subcommand; reuses `~/.hermes/.env` bot token, locked to user
  <YOUR_TELEGRAM_USER_ID>; no gateway/LLM needed).
- **`.gitignore`:** add `google_token.json`, `google_client_secret.json`,
  `google_oauth_pending.json`, `google_oauth_last_url.txt` guards (they live in
  `~/.hermes`, already outside the repo, but add belt-and-suspenders patterns if
  any `~/.hermes` subtree is ever tracked).

---

## Edge cases & failure modes (exhaustive)

- **No client_secret yet** → `authurl` 400 `no_client_secret`; card sits on step 1.
- **Malformed / wrong-type client_secret** (e.g., an API key, or a `web` client
  where the redirect isn't localhost) → `not_client_secret` 400; the flow will
  also fail at Google with `redirect_uri_mismatch` → surface that verbatim.
- **User pastes an expired / already-used / stale-tab code** → driver returns
  `expired` + a fresh `fresh_auth_url` (parity with setup.py behavior); card shows
  "that code expired — here's a fresh link".
- **State mismatch** (user pasted a URL from a different session) → `state_mismatch`
  400; restart at authurl.
- **User deselects scopes on the consent screen** (unchecks Calendar) →
  `OAUTHLIB_RELAX_TOKEN_SCOPE=1` lets the exchange succeed; token stores only the
  granted subset; status shows `partial` + `missing:[…]`; widgets that need the
  missing scope degrade gracefully (today widget falls back to icalBuddy).
- **Google returns a scope we did NOT ask for** (should be impossible with a fresh
  non-incremental consent, but defense-in-depth) → exchange **revokes + refuses**
  with `scope_violation`; nothing is written.
- **`Error 403: access_denied`** (app still in Testing, user not a test user) →
  the card's error text links to `https://console.cloud.google.com/auth/audience`
  (add-test-user), matching the skill's own guidance.
- **`disabled_client` / `invalid_client`** on refresh → status `error` with the
  reason; card offers Reconnect; the loop does not thrash (it only checks every
  30 min and only notifies on state transition).
- **Token revoked out-of-band** (user revoked at myaccount.google.com) → next
  `check`/read returns `token_revoked`/`invalid_grant`; status flips to `error`;
  one notify; widgets fall back.
- **Venv missing or google libs absent** → `_drv` returns `{"error": …}`; status
  degrades to `disconnected` with reason `"google libraries unavailable — run
  setup.py --install-deps"`; hub never 500s. (Libs verified present today; this
  guards a future venv rebuild.)
- **icalBuddy AND Google both unavailable** → today widget shows the existing
  `{"available": false, "reason": …}` — unchanged behavior, no regression.
- **Concurrent status checks** → `_cached` + the 60s TTL collapse them; the driver
  is idempotent and stateless per op.
- **Clock skew / all-day events** → agenda normalizes `date`-only events to
  `all_day:true` with empty `time` (same shape the widget already handles).
- **Partial write / crash mid-token-write** → atomic temp+rename at 600; a partial
  is never observed; a missing/corrupt token reads as disconnected.
- **Skill re-bundle restores `gmail send`** → the self-heal guard (below)
  re-neuters on next dashboard start; a window where send is reachable is bounded
  by the guard interval and still requires the agent to *choose* to send, which is
  itself recorder-logged.

---

## Security & safety (every invariant, and the honest send story)

**The send wall — three enforced layers + one honest caveat:**

1. **Scope minimization (necessary).** We request only `gmail.readonly`,
   `gmail.compose`, `calendar.events`. We never request `gmail.send`,
   `gmail.modify`, `mail.google.com`, or any Drive/Sheets/Docs/Contacts scope.
   The exchange **auto-revokes** any token that comes back with a forbidden scope.
   This is strictly stronger than the shipped `setup.py`, which requests
   `gmail.send` + `gmail.modify` + full `calendar` + full `drive`.
2. **Tooling / structural absence (primary).** The only sanctioned Gmail-write
   interface is `google_draft.py`, which calls `users().drafts().create` and
   contains **no** `messages().send`/`drafts().send` anywhere. The skill's
   `google_api.py gmail send` and `gmail reply` (the only reachable send paths on
   disk) are **neutered** by an idempotent, self-healing guard in `aux_google.py`:
   on connect and on each dashboard start it ensures those two functions raise
   `"sending is disabled by Hermes safety policy"` and their subparsers are
   removed, marked with a `# HERMES-NOSEND` sentinel so it patches once; the
   pre-image is snapshotted + recorder-logged (undoable). Result: no reachable
   sanctioned command can send mail.
3. **Approval + audit (defense-in-depth).** The residual truth, stated plainly:
   **there is no Gmail OAuth scope that permits draft-create yet forbids send** —
   `gmail.compose` technically authorizes `messages.send`. So a *misaligned agent
   could hand-write raw API calls* via the `terminal` tool to bypass the wrappers.
   That path is (a) arbitrary code execution → `permissions.py` classes
   `arbitrary-exec`/`execute-code`, both `floor:"ask"` → the user sees an approval
   card under `approvals.mode: manual`; and (b) recorded in the flight recorder.
   We do not claim the scope alone makes send impossible; we claim send is
   *structurally absent from every sanctioned path and gated everywhere else*.
   This is the honest reading of CLAUDE.md's "absence of send capability is the
   enforcement."

**Other invariants:**
- **Gmail = read + draft only.** Enforced as above. Drafts are the ideal
  proactive Gmail output: the agent may draft a reply, the **user** sends it — a
  natural NOTIFY-ONLY fit.
- **Calendar writes stay approval-gated & dashboard performs none.** `calendar.events`
  is requested because the workstream asks for "manage"; but every mutation goes
  through the agent's confirm-first path (skill Rule 1) — never the dashboard,
  never a proactive trigger. (If the user prefers read-only Calendar, flip
  `calendar.events` → `calendar.readonly` in `SAFE_SCOPES`; the today widget still
  works. Left as an open question below.)
- **Local-first / secrets.** All creds in `~/.hermes` at `600`; the dashboard
  binds loopback only; tokens/secrets are never logged, never sent to the model,
  never written to `state.db` or `google_status.json`. Refuses to store a
  client_secret located outside `$HOME`.
- **Telegram stays locked** to the one user (we only *reuse* `hermes send`; we add
  no new recipient).
- **NOTIFY-ONLY boundary** (Phase 2): the status thread and any downstream
  Watchtower use of this grant may read + draft + notify, never send or auto-mutate.

**What it refuses:** to request or retain send/modify/Drive scopes; to expose a
send command; to write Google state from the dashboard; to store a secret outside
`$HOME`; to run under a python without the Google libs (degrades, never guesses).

---

## Test plan (no spam, no `--yolo`, no real send — there is no send path)

All commands use the venv python `V=~/.hermes/hermes-agent/venv/bin/python`.

1. **Scope-guard unit test (offline, the critical one).** Drive
   `google_oauth_driver.py exchange` with a stubbed `Flow` whose
   `granted_scopes` includes `gmail.send`; assert it returns
   `{"error":"scope_violation"}` and writes **no** token. Repeat with exactly
   `SAFE_SCOPES` → asserts a token is written. `pytest`-style or a `-c` harness;
   no network.
2. **Client-secret validation.** `curl -s localhost:7788/api/google/client_secret
   -d '{"json":{"apikey":"x"}}'` → `not_client_secret` 400. Valid Desktop JSON →
   `{"ok":true}` and `stat -f '%Lp' ~/.hermes/google_client_secret.json` == `600`.
3. **Status fast path (disconnected).** With no token: `curl -s
   localhost:7788/api/google/status` → `connected:false`, `has_client_secret`
   reflects step 2, and it returns in <20ms (no venv spawn).
4. **Live connect (the one NEEDS-YOU manual pass).** Walk the real flow once with
   the user's account (below). After exchange:
   `$V ~/.hermes/skills/productivity/google-workspace/scripts/setup.py --check`
   → `AUTHENTICATED`; `python …/google_api.py gmail search "is:unread" --max 3`
   → JSON, no `invalid_scope`; `cat ~/.hermes/google_token.json | jq .scopes` ==
   the three SAFE_SCOPES exactly.
5. **Draft works, send refuses.** `$V dashboard/google_draft.py --to
   you@example.com --subject "Hermes test" --body "draft only"` → a draft
   appears in Gmail → Drafts (verify in the web UI); then
   `python …/google_api.py gmail send --to x --subject y --body z` → exits with
   "sending is disabled by Hermes safety policy" (neuter confirmed);
   `grep -rn "messages().send" dashboard/google_draft.py` → no matches.
6. **Calendar widget unblock.** `curl -s "localhost:7788/api/google/agenda?days=1"`
   → real events; open the Hub `today` card (⌘R in the app) → shows Google events;
   `mv ~/.hermes/google_token.json /tmp/` → card falls back to icalBuddy text
   without error; move it back.
7. **Disconnect.** `curl -s -XPOST localhost:7788/api/google/disconnect` →
   `{"ok":true}`; token gone; status `connected:false`; re-`--check` →
   `NOT_AUTHENTICATED`.
8. **Regression / restart.** `launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard`;
   `tail ~/.hermes/logs/dashboard.log` for `[aux_google]` clean load; hub renders;
   one chat turn still works.

---

## Effort & sequencing + dependencies + open questions

**Effort:** S–M. The heavy lifting (OAuth PKCE, token format, read commands)
already exists in the skill and the venv already has the libs; the net-new is the
minimal-scope driver, the scope guard, the draft wrapper, the neuter/self-heal,
one aux route module, one JS card, and the widget-provider wrap. ~1 focused day
of build + the user's ~10-minute browser setup.

**Sequencing:** (1) `google_oauth_driver.py` + scope guard + unit test →
(2) `aux_google.py` routes + status cache + widget wrap → (3) `google_draft.py` +
neuter/self-heal → (4) `aux_google.js` card + the one script tag → (5) live
connect with the user → (6) restart-verify + CLAUDE.md "Not yet done" flip.

**Dependencies:** none blocking. Google libs present in venv (verified). No other
P2 stream is a prerequisite. **Downstream unblocks:** P2.1 Watchtower can read
Calendar (gap triggers) + Gmail unread and *draft* replies; P2.1/P2.2 morning
brief can pull `GET /api/google/agenda`.

**NEEDS-YOU (every user step, for the runbook):**
1. Create/select a Google Cloud project:
   `https://console.cloud.google.com/projectselector2/home/dashboard`.
2. Enable **Gmail API** and **Google Calendar API** only (not Drive/Sheets/Docs —
   we don't request them): `https://console.cloud.google.com/apis/library`.
3. Configure the OAuth consent screen (External, app in Testing) and **add your
   own Google account as a Test user**:
   `https://console.cloud.google.com/auth/audience`.
4. Create credentials → **OAuth 2.0 Client ID** → application type **Desktop app**;
   download the JSON: `https://console.cloud.google.com/apis/credentials`.
5. In the dashboard Mind view → Google Workspace card → **Connect Google** → give
   it the downloaded JSON's path (or paste its contents).
6. Click the auth URL, sign in, approve **Read Gmail / Manage drafts / Manage
   Calendar** (leave everything else unchecked). The browser will fail to load
   `http://localhost:1` — **expected**; copy the entire address-bar URL.
7. Paste that URL back into the card. Status flips to **Connected**. Done — the
   token auto-refreshes from here; no further steps.
8. To revoke later: the card's **Disconnect** button (or
   `setup.py --revoke`).

**Open questions:**
- **Calendar write vs read-only.** Default `calendar.events` (honors "manage") vs
  the stricter `calendar.readonly` (widget-only). Recommend defaulting to
  `calendar.events` since writes are already approval-gated, but confirm the
  user's posture — it's a one-line `SAFE_SCOPES` change.
- **Neuter the skill vs ship-only-a-safe-wrapper.** This spec does both (neuter +
  self-heal *and* a separate draft wrapper). If editing a skill file is deemed
  too invasive against the curator, the fallback is wrapper-only + a memory fact
  directing the agent to `google_draft.py`; that weakens layer 2 to
  instruction-strength (still covered by layer 3). Confirm preference.
- **Account email source.** Driver `check` reads it via `gmail.users().getProfile`
  (authorized by `gmail.readonly`), avoiding an extra `userinfo.email` scope —
  confirm that's acceptable vs. adding the tiny profile scope for a nicer display.
