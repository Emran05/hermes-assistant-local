# Security

Hermes Assistant runs entirely on your Mac. There is no server to attack and no
account to compromise — but it is an always-on agent with access to your files,
calendar and messages, so the local surface matters.

## The posture

- **Loopback only.** The dashboard binds `127.0.0.1:7788` and the agent backend
  `127.0.0.1:9119`. Neither listens on your network.
- **Same-origin guard.** The dashboard API has no auth token, so every request
  is checked before dispatch: the `Host` header must be a loopback name we
  serve on (this closes DNS rebinding), a present `Origin` must match it, and
  `Sec-Fetch-Site: cross-site` is refused on state-changing verbs. No CORS
  headers are ever sent.
- **Secrets stay in `~/.hermes/.env`** (mode 600) and are never read into the
  dashboard's config export — the exporter hard-refuses to write a snapshot
  that contains anything token-shaped.
- **Read-only Google, approve-before-act everywhere else.** The Google grant
  is `gmail.readonly`, `calendar.readonly` and `contacts.readonly`; a grant
  that comes back wider is revoked and not stored. Google has no
  draft-without-send scope (`gmail.compose` can send), so a reply Hermes
  drafts is text you send yourself. Dangerous tool calls pause for approval
  unless you have explicitly graduated that action class to auto — and Auto
  approves only one simple command it has fully checked: a chained, very long,
  partly redacted or obscured command still asks, and every part of a command
  is held to the strictest class it matches. Writes to shell startup files,
  LaunchAgents, `~/.ssh`, `~/.hermes`, `~/.claude` and the install directory
  ask for approval whichever spelling of the path the agent uses (macOS alias
  paths such as `/.vol/…` are refused outright).
- **Telegram is locked to an allowlist.** `TELEGRAM_ALLOWED_USERS` in
  `~/.hermes/.env` must contain only your own numeric user id — that allowlist
  is the entire lock between a stranger who finds your bot and your agent.
- **Inference is local.** Prompts and model output do not leave the machine.
  The optional Claude Bridge is the one exception, and it is a switch you turn
  on (Settings › Claude Bridge); it refuses to send a question or context that
  looks like it holds a password, key or token. For-You's Claude pass (about
  every 2 hours), which would send
  your You-Model profile, needs a second switch of its own ("For-You via
  Claude"). The `claude -p` child runs with no tools, no MCP servers and none
  of your `~/.claude` settings. Settings › Connections › Data & Network lists
  what can reach the internet, including the agent's own tools: a command in
  its terminal, its browser tools or a page fetch can reach any address, with
  no switch other than the agent's toolsets and the approval prompts.
- **GitHub only through a sandbox, if you connect it.** Hermes stores a
  fine-grained token in `~/.hermes/dashboard/github/credential.json` (mode 600)
  and never gives it to the agent: the agent edits a local sandbox and requests
  a change, and the dashboard pushes only a change you approved, only to a
  `hermes/` branch, as a draft pull request, with a Telegram message per push
  (a security notice: it is sent even with the notification switches off).
  Hermes refuses a token that can change settings, read secrets, add webhooks,
  run Actions or manage your account's SSH or GPG keys, and one its check
  finds can write to repositories you did not pick; that check is a sample,
  not a proof, and the GitHub card says so. The review sheet approves only
  what it has shown: binary files, very long lines and long runs of blank
  space need their own tick. Disconnecting deletes Hermes' local mirrors.

## Reporting a vulnerability

Open an issue at
<https://github.com/Emran05/hermes-assistant-local/issues>. If the issue would expose
someone's data by being public, use GitHub's **Report a vulnerability** button
on the Security tab instead, which opens a private advisory.

Please include the version (Settings › System & Data, or `curl -s
localhost:7788/api/version`), what you did, and what happened. There is no
bounty and no SLA — this is one person's project.

## Things that are not vulnerabilities

- Anything that can open a connection to this Mac's loopback can reach
  `127.0.0.1:7788`: every login on the Mac (the Guest account included), and
  processes in OrbStack or Docker Desktop containers and OrbStack Linux
  machines via `host.docker.internal` / `host.orb.internal`, whose host-side
  proxy connects to loopback on their behalf. All of them arrive from
  `127.0.0.1` and can send any `Host` header, so neither the same-origin
  guard nor the peer-address check can tell them from you. Closing that needs
  a secret the requester must present, which the dashboard does not have yet.
  Until it does, the trust boundary is the machine: disable the Guest
  account, and do not run untrusted containers while Hermes is running. The
  peer check does refuse any non-loopback address if the dashboard is ever
  bound to one (`DASH_HOST`), even with a forged `Host` header;
  `HERMES_DASH_ALLOWED_PEERS` (comma-separated IPs or CIDRs) opts specific
  peers back in.
- The app is ad-hoc signed unless a release was built with Apple signing
  secrets, so first launch needs right-click › Open.
- GitHub approvals have the same gap. On this Mac the agent runs as your
  account, so the key file's mode 600 does not stop its terminal from reading
  it, and approving or retracting a change (`/api/github/changes/decide`,
  `/api/github/changes/retract`) is guarded only by a speed bump: a
  same-origin `Origin` header plus `Sec-Fetch-Site: same-origin`, which any
  process running as you can forge. The connector's widening routes (connect,
  add a repository, check, acknowledge drift, widen settings, unpause) have no
  proof of owner yet; removing a repository needs none by design (it reduces
  what Hermes can do), so such a process could also remove one, after which
  Hermes no longer checks it for drift. The same per-launch secret is the fix. Until then the
  bounds are the token itself (only the repositories you picked, no settings,
  secrets, webhooks or Actions, an expiry date), `hermes/` branches only, draft
  pull requests, at most 10 pushes a day (checked again at the moment of the
  push, together with Pause), a Telegram message per push, and a
  6-hourly check that reports any `hermes/` branch Hermes did not push. Branch
  rules on your default branch still matter: a stolen token can push wherever
  GitHub's rules do not stop it. A signed-in `gh`, a git password in the
  Keychain or a loaded SSH key bypasses Hermes entirely; Hermes detects them
  (`GET /api/github/ambient`) and never changes them.
- The optional "Uncensored" roster models have had their refusal behaviour
  removed by their authors. That is what they are for. They are never the
  default, switching to one needs your acknowledgement, and what they write is
  your responsibility.
