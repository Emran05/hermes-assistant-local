# Hermes Assistant

An always-on, **fully local** personal AI assistant for Apple Silicon Macs:
a [Hermes](https://github.com/NousResearch/hermes-agent) tool-calling agent + a
local MLX model + a custom Liquid-Glass dashboard. All LLM inference runs on your
machine; only Telegram transport and explicit agent tool calls (web search, etc.)
touch the internet.

> Local-first by design. Secrets live in `~/.hermes/.env` (never committed).
> Read-only where it matters (e.g. Gmail is draft-only, no send).

## What's inside

- **Model server** — `mlx-server.sh` runs `mlx_lm.server` (OpenAI-compatible) on
  `127.0.0.1:8080`. Default model: a Qwen3-30B-A3B MoE in 4-bit (~18 GB).
- **Dashboard** — `dashboard/server.py` (stdlib-only) on `127.0.0.1:7788`, with a
  modular widget hub, an Agent page, and Settings. Vanilla JS in a WKWebView.
- **World Brief** — an 8am daily brief + midday pulse + breaking alerts +
  end-of-day wrap, built from curated, lean-tagged news feeds (full-spread:
  factual / public / left / right / Mideast), 24h-recency-filtered.
- **News desk** — *framing comparison* ("one story, every lens" across outlets)
  and a *trend radar* (what's accelerating), both from headline analysis alone.
- **Idle-suspend** — the MLX server auto-sleeps after N minutes of no activity to
  free RAM, and transparently wakes on the next prompt.
- **Code knowledge graph** — optional [Graphify](https://github.com/Graphify-Labs/graphify)
  integration + Obsidian sync (`obsidian_sync.py`, `obsidian_daily.py`).
- **Native macOS app** — `app/main.swift` (AppKit/WebKit) with a menu-bar info
  dropdown (weather, model/plan usage, system meters) and a global-hotkey Quick Ask.

## Setup

1. Install the local model server deps and start it:
   ```bash
   pip install 'mlx-lm' 'transformers<5'      # transformers must stay <5
   ./mlx-server.sh                            # serves the model on :8080
   ```
2. Install the [Hermes agent](https://github.com/NousResearch/hermes-agent) and
   point its config at `http://127.0.0.1:8080/v1` (see `config.yaml`).
3. Copy `env.example` to `~/.hermes/.env` and fill in your own values
   (Telegram bot token, etc.). **Never commit `.env`.**
4. Start the services:
   ```bash
   ./install-services.sh                      # launchd: model + dashboard + serve
   ```
5. Open the dashboard at http://127.0.0.1:7788 (or build the native app with
   `app/build-app.sh`).

See `CLAUDE.md` for the full architecture notes and hard-won gotchas, and
`RUNBOOK.md` for operations.

## Notes

- Built and tuned for 64 GB Apple Silicon; adjust the model in `mlx-server.sh`.
- This is a personal project shared as-is. Placeholders like `123456789` /
  `@your_hermes_bot` / `/Users/you` are yours to fill in.

## License

MIT — see [LICENSE](LICENSE).
