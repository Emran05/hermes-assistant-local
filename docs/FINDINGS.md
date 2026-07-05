# Findings — things discovered during the build that need your call

## ★ MODEL: Hermes-3-8B can't reliably use tools; Qwen3-30B can (2026-07-05)

While live-drilling the approval loop (P1.4) I discovered the local **Hermes-3-Llama-3.1-8B-4bit
does NOT reliably call tools** in hub chat. Asked to run a shell command it *deflects*
(hallucinates a fake "the user said don't" message) or *predicts the output* instead of
actually invoking the terminal tool — so it never really does anything. This breaks the core
"agent does things while I'm away" vision: an assistant that won't call tools is just a chatbot.

**Qwen3-30B-A3B-Instruct-2507-4bit (already on disk) tool-calls reliably.** Switched to it and
every drill worked first try — it ran `git reset --hard`, triggered the real approval, respected
the policy tiers. It's a MoE (~3.3B active params) so it's fast, and it uses ~17GB — comfortable
in your 64 GB (the 8B was actually using *more* once its KV cache grew).

Notably, `mlx-server.sh` already documents Qwen3-30B as the intended default ("the right default
for an assistant whose job is tool-calling") — the active-model pointer had just drifted to the 8B.

**What I did:** left the active model on **Qwen3-30B-A3B** (it's loaded and working) and reset the
permission policy to safe all-ask.
**Your call:** keep Qwen3-30B as the default (recommended — it's the difference between a working
and non-working agent), or tell me to go back to the 8B. The model menu switches either way now
(I also fixed the switch bug — see below).

## Switch button — deeper bug found & fixed (2026-07-05)

Earlier we fixed the switch button's dead `confirm()` dialog (WKWebView). But it had a *second*
bug underneath: `switch_model` used `launchctl kickstart -k`, which does NOT reliably reload the
KeepAlive model service — it kept serving the OLD model even after the active-model file changed.
Fixed to `bootout`→`bootstrap` (same reliable pattern as pause/resume), which also cleanly frees
the old model's RAM before loading the new one. Switching models works properly now.

## Approvals require the model to actually call a tool (by design)

Under `approvals.mode: manual`, a headless `hermes -z` session can't approve, so tool calls that
need approval don't execute (the model narrates fake success). This is expected. It's why the
P1.4 live drills run through the *hub chat* surface (where the dashboard/Telegram can approve),
not `-z`. Also why `--yolo` is refused: it would blanket-disable the approval gate (a safety
invariant), so it's never used.
