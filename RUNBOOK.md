# Personal Assistant Agent — Setup Runbook (M5 Mac, headless, always-on)

Your choices, baked in:
- **Reach:** Telegram bot, locked to your user ID only.
- **Autonomy:** draft-and-approve everything (nothing sends/modifies without `/approve`).
- **Phase 1 integrations:** Google Calendar and Gmail (read-only: Hermes writes reply text, you send it), Discord.
- **Phase 2 (harder, do after phase 1 works):** iMessage (Photon), Microsoft Teams, Slack, Notion.

The golden rule while you build: get the reliable core working end-to-end
*before* touching the shaky integrations. A working small assistant beats a
broken ambitious one.

---

## 0. Prerequisites
- Apple Silicon, macOS 14 (Sonoma) or newer.
- Homebrew and **Python 3.12+** (`./install.sh` checks both). Node.js only if
  you later add an `npx`-based MCP server.
- `brew install ical-buddy` if you want calendar events: the Today widget, the
  briefs, Needs-you and search read the macOS Calendar through it.
- `brew install ffmpeg` (needed if you later want voice memo transcription).

## 1. Install the local model server
Don't `pip install --upgrade mlx-lm` (or mlx-vlm) into the system Python: the
primary model runs from an isolated venv with pinned versions, and mlx-vlm
0.6.15–0.6.17 corrupt output when two requests overlap. `./install.sh` offers to
build that venv; on its own:
```bash
./install-mlx-vlm-venv.sh    # ~/.hermes/mlx-vlm-venv, mlx-vlm 0.6.14 + pins
```
The model server is **on demand**. `./mlx-server.sh` run by hand prints
"on-demand mode: no fresh start token — not loading the model" and exits; the
dashboard starts it (with a one-off start token) when a chat or Telegram turn
needs it, or when you press "Wake now" in the model menu. To keep it running
anyway, install the services with `HERMES_MODEL_ALWAYS_ON=1 ./install-services.sh`.
The first start downloads the model you picked (~17 GB for Qwen3.8-27B). While
it is awake:
```bash
curl http://127.0.0.1:8080/v1/models
```

## 2. Install Hermes
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

## 3. Drop in the config
`./install.sh` seeds both files when they are missing (it never overwrites
one). By hand:
```bash
cp config.yaml ~/.hermes/config.yaml   # already points Hermes at 127.0.0.1:8080
cp env.example ~/.hermes/.env          # then EDIT it
chmod 600 ~/.hermes/.env
```
The seed pins `approvals.mode: manual` and a local TTS voice, and adds no MCP
servers. `python3 dashboard/doctor.py` checks the file for drift later.

## 4. Telegram bot (your only reach-in channel)
1. In Telegram, message **@BotFather** -> `/newbot` -> copy the token.
2. Message **@userinfobot** -> copy your **numeric** user ID.
3. Put both in `~/.hermes/.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`).
4. `TELEGRAM_ALLOWED_USERS` must be **only your ID** — this is the lock that
   stops anyone who finds the bot from driving your agent.
```bash
hermes gateway setup      # pick Telegram, paste token + your id
```

## 5. Google Calendar + Gmail (read-only)
Connect Google from the dashboard, not through an MCP server: **Settings ›
Connections › Google** walks you through creating an OAuth **Desktop app**
client in Google Cloud Console and pasting it in. The dashboard builds the
consent request itself and asks for exactly `gmail.readonly`,
`calendar.readonly` and `contacts.readonly` (`GOOG_SCOPES` in
`dashboard/aux_google.py`). It refuses and revokes any grant that includes a
send-capable Gmail scope: Google has no draft-only scope, and `gmail.compose`
can send. The agent reads your calendar and mail and writes reply text; you
send it yourself.

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
The dashboard sends the morning brief, the midday pulse and the evening wrap to
Telegram — set the times and the master switches in **Settings › Proactive**.
(Hermes does not read a `cron:` block in config.yaml; its own scheduler keeps
jobs in `~/.hermes/cron/jobs.json` — see `hermes cron`.)

---

## How draft-and-approve actually feels
- Read actions (list events, read email, read Discord) just happen.
- Any write/send action **pauses** and pings you; reply `/approve` or `/deny`
  in Telegram. Use `/verbose` to see exactly what it's about to do.
- Start here. Once you trust a specific low-risk action (e.g. posting your own
  agenda to a private Notion page), you can loosen that one later. Setting a
  class to Auto in the dashboard's Trust panel approves only a single, simple
  command of that class: a chained or very long command still asks.

## Phase 2 — the harder integrations (attempt AFTER phase 1 is solid)
- **iMessage:** `hermes photon setup` (Photon = BlueBubbles successor, no Mac
  relay). Better odds than the old bridge, still fiddly across macOS updates.
- **Microsoft Teams:** `hermes gateway setup` -> Teams. Needs Azure/Graph app
  registration and often **org-admin consent** you may not control. If your
  Teams is a school/work tenant, expect to hit a permissions wall.
- **Slack:** create a Slack app + bot token, add to gateway. Workspace-admin
  install may be required.
- **Notion:** add a `notion` entry under `mcp_servers:` in
  `~/.hermes/config.yaml` (see `hermes mcp`), set `NOTION_TOKEN`, and share the
  target pages/DB with the integration. An MCP server is a new process and a
  new network path; doctor's drift check lists it while it is enabled.

## Security reminders (an always-on agent with your data is a real surface)
- Keep `TELEGRAM_ALLOWED_USERS` to only you.
- Keep Gmail at `gmail.readonly`. `compose`, `modify` and `send` can all send
  mail; the dashboard refuses and revokes a grant that includes any of them.
  Don't add an MCP server or skill that can send.
- `chmod 600 ~/.hermes/.env`; never commit it anywhere.
- Everything runs locally — model included — so your calendar/mail content
  isn't sent to a hosted model unless you switch on Claude escalation (see
  Settings › Connections › Data & Network for everything that can go out).
  That's the main privacy win of this whole setup.
- Watch Activity Monitor the first few days: if memory pressure goes yellow/red
  or swap kicks in, you're context/KV-bound — pick a smaller model in the model
  menu (`model.context_length` cannot go much lower: Hermes refuses less than
  64k). The dashboard also refuses new model work above its memory ceiling and
  restarts the server past a hard one.

## If something breaks
```bash
python3 dashboard/doctor.py   # START HERE — 23 read-only checks, one screen,
                              # exit 1 if anything FAILed. Never wakes the model.
hermes doctor          # diagnoses common issues
hermes gateway status  # is the service up?
hermes dump            # paste into a GitHub issue for support
```
