# Routing v2 (cheap turns → the 9B lane) — deferred, with reasons (2026-09-07)

The 1.2 brief listed "model routing v2: classify/route cheap tasks to the 9B lane
automatically (measured)". After mapping the code (`scratchpad/research/124-routing-map.md`,
Sonnet code-scout, read-only) it is deferred rather than shipped. The facts:

1. **A chat turn cannot be steered to the 9B through the agent.** Dashboard chat drives
   `hermes serve` over one WebSocket (`dashboard/hermes_rpc.py:196-334`); `session.create` and
   `prompt.submit` carry no model or lane field, so every agent turn runs on `model.default`
   (the primary lane, :8080). The only lane-aware path is the one-shot `hermes -z --provider
   custom:bg` used by briefings (`server.py:975-1005`), not the streaming chat path.
2. **The alternative is a bypass, not routing.** Everything that talks to the 9B today posts
   raw completions to `:8081/v1/chat/completions` (For-You, watchtower synthesis, needs-you) —
   no tools, no approvals, no memory tool, no Flight Recorder rows. Sending "cheap" chat turns
   that way would silently change what a turn *is*; that is a product decision for the owner,
   not an optimisation.
3. **The saving is narrow on this Mac.** `mlx_admission()` budgets the SUM of both lanes
   (27B ≈ 18–19 GB + 9B ≈ 7 GB), and only the primary lane has idle-suspend/auto-wake — the
   bg lane's start token (`model-start-ok-bg`) is never minted by dashboard code, so an asleep
   9B falls back to the primary. Routing saves time only when the 9B is already resident and
   the 27B would need a cold wake (~30–50 s + ~25 s prefill before the 1.2.0 diet).
4. **No comparison data.** `promotion.json` has no drill entry for Qwen3.5-9B; per-turn
   metrics and the context meter parse only `mlx-server.log`, never `mlx-bg.log`; there is no
   same-prompt A/B harness over the owner's real messages.

## What would make it worth doing (prerequisites, each measurable)
- Upstream: a per-turn model/lane override in `hermes serve` (`prompt.submit` field or a
  second serve bound to the bg lane) — check the next Hermes Agent release notes.
- Lane lifecycle: auto-wake/idle-suspend for the bg lane mirroring the primary, plus a memory
  policy for "both resident" on 32/64 GB Macs.
- Data: a 9B row in the evals history (run the 1.2.3 suite with the 9B active, on AC), bg-lane
  rows in the context meter (`mlx-bg.log`), and a 50–100 message A/B set from the owner's own
  history judged for "was the 9B answer acceptable".
- Then: a shadow-mode router at the `_chat_worker` seam `aux_autoroute.py` already wraps,
  logging `would_route` per turn for two weeks before any active mode.

Owner decision needed: is a tools-free fast lane for simple questions wanted at all? If yes,
the honest v1 is a visible "quick answer (9B, no tools)" choice in the composer, not a silent
router.
