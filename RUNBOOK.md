# Personal Assistant Agent — Setup Runbook (M5 Mac, headless, always-on)

Your choices, baked in:
- **Reach:** Telegram bot, locked to your user ID only.
- **Autonomy:** draft-and-approve everything (nothing sends/modifies without `/approve`).
- **Phase 1 integrations:** Google Calendar, Gmail (drafts only), Discord.
- **Phase 2 (harder, do after phase 1 works):** iMessage (Photon), Microsoft Teams, Slack, Notion.

The golden rule while you build: get the reliable core working end-to-end
*before* touching the shaky integrations. A working small assistant beats a
broken ambitious one.

---

## 0. Prerequisites
- macOS 26.2+ recommended.
- Homebrew, Python 3.11, Node.js (for `npx`-based MCP servers).
- `brew install ffmpeg` (needed if you later want voice memo transcription).

## 1. Install the local model server
```bash
pip install --upgrade mlx-lm
chmod +x mlx-server.sh
./mlx-server.sh            # leave running; serves http://127.0.0.1:8080/v1
```
First run downloads the model (~18-20GB for Qwen 32B-A3B). Verify it's up:
```bash
curl http://127.0.0.1:8080/v1/models
```

## 2. Install Hermes
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

## 3. Drop in the config
```bash
mkdir -p ~/.hermes/secrets
cp config.yaml ~/.hermes/config.yaml
cp env.example ~/.hermes/.env      # then EDIT it
chmod 600 ~/.hermes/.env
```
Point Hermes at your local model (confirms the custom endpoint):
```bash
hermes model      # choose "custom", confirm base_url http://127.0.0.1:8080/v1
```

## 4. Telegram bot (your only reach-in channel)
1. In Telegram, message **@BotFather** -> `/newbot` -> copy the token.
2. Message **@userinfobot** -> copy your **numeric** user ID.
3. Put both in `~/.hermes/.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`).
4. `TELEGRAM_ALLOWED_USERS` must be **only your ID** — this is the lock that
   stops anyone who finds the bot from driving your agent.
```bash
hermes gateway setup      # pick Telegram, paste token + your id
```

## 5. Google Calendar + Gmail (read + draft only)
1. Google Cloud Console -> create OAuth **Desktop app** client -> download JSON
   to `~/.hermes/secrets/google_oauth.json`.
2. Set `GOOGLE_OAUTH_CREDENTIALS` in `.env` to that path.
3. Install the Google MCP server and run the one-time consent flow:
```bash
hermes mcp install google      # or: hermes mcp  (interactive picker)
```
   Verify the exact package name in the picker before installing.
4. Scopes are deliberately set to `calendar.readonly, gmail.readonly,
   gmail.compose` — the agent can READ your calendar/mail and DRAFT replies,
   but has **no send scope**. You send after reviewing. This is the whole
   point of draft-and-approve.

## 6. Discord (phase 1)
1. https://discord.com/developers -> New Application -> Bot -> copy token.
2. Put token in `.env`; set `DISCORD_ALLOWED_CHANNELS` to your own channel ids.
3. `hermes gateway setup` -> add Discord.

## 7. Start it as an always-on service
```bash
hermes gateway install     # installs background service (survives reboot)
hermes gateway start
hermes gateway status
```
Text your bot "what's on my calendar today?" from Telegram to smoke-test.

## 8. The daily agenda nudge
The `cron` block in config.yaml sends a 7am draft agenda (calendar + unread
mail summary) to Telegram. It only DRAFTS — you approve any follow-up sends.
Tune the time / prompt to taste, then `hermes gateway restart`.

---

## How draft-and-approve actually feels
- Read actions (list events, read email, read Discord) just happen.
- Any write/send action **pauses** and pings you; reply `/approve` or `/deny`
  in Telegram. Use `/verbose` to see exactly what it's about to do.
- Start here. Once you trust a specific low-risk action (e.g. posting your own
  agenda to a private Notion page), you can loosen that one later.

## Phase 2 — the harder integrations (attempt AFTER phase 1 is solid)
- **iMessage:** `hermes photon setup` (Photon = BlueBubbles successor, no Mac
  relay). Better odds than the old bridge, still fiddly across macOS updates.
- **Microsoft Teams:** `hermes gateway setup` -> Teams. Needs Azure/Graph app
  registration and often **org-admin consent** you may not control. If your
  Teams is a school/work tenant, expect to hit a permissions wall.
- **Slack:** create a Slack app + bot token, add to gateway. Workspace-admin
  install may be required.
- **Notion:** uncomment the `notion` MCP block in config.yaml, set `NOTION_TOKEN`,
  share the target pages/DB with the integration. This is the easiest phase-2
  item and the one that makes "maintain my agenda board" real.

## Security reminders (an always-on agent with your data is a real surface)
- Keep `TELEGRAM_ALLOWED_USERS` to only you.
- Keep Gmail at `compose` (draft), never `send`, until you truly trust it.
- `chmod 600 ~/.hermes/.env`; never commit it anywhere.
- Everything runs locally — model included — so your calendar/mail content
  isn't leaving the machine. That's the main privacy win of this whole setup.
- Watch Activity Monitor the first few days: if memory pressure goes yellow/red
  or swap kicks in, you're context/KV-bound — lower `context_window` or stay on
  the 32B model rather than GLM-Air.

## If something breaks
```bash
hermes doctor          # diagnoses common issues
hermes gateway status  # is the service up?
hermes dump            # paste into a GitHub issue for support
```
