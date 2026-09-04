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
- **Draft-and-approve by default.** Gmail is granted `compose`, never `send`;
  dangerous tool calls pause for approval unless you have explicitly graduated
  that action class to auto.
- **Telegram is locked to an allowlist.** `TELEGRAM_ALLOWED_USERS` in
  `~/.hermes/.env` must contain only your own numeric user id — that allowlist
  is the entire lock between a stranger who finds your bot and your agent.
- **Inference is local.** Prompts and model output do not leave the machine.
  The optional Claude Bridge is the one exception, and it is a switch you turn
  on (Settings › Claude Bridge).

## Reporting a vulnerability

Open an issue at
<https://github.com/Emran05/hermes-assistant-local/issues>. If the issue would expose
someone's data by being public, use GitHub's **Report a vulnerability** button
on the Security tab instead, which opens a private advisory.

Please include the version (Settings › System & Data, or `curl -s
localhost:7788/api/version`), what you did, and what happened. There is no
bounty and no SLA — this is one person's project.

## Things that are not vulnerabilities

- Anyone with a login on your Mac can reach `127.0.0.1:7788`. That is by
  design; the trust boundary is your user account.
- The app is ad-hoc signed unless a release was built with Apple signing
  secrets, so first launch needs right-click › Open.
- The optional "Uncensored" roster model has had its refusal behaviour removed
  by its authors. That is what it is for. It is never the default, and what it
  writes is your responsibility.
