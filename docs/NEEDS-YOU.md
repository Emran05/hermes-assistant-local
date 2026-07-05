# Needs-you checklist (batched user actions)
Items only you can do; grouped for 2-3 sittings. I keep this current.

## Batch 1 (whenever convenient)
- [ ] Calendar under launchd: System Settings → Privacy & Security → Calendars
      → enable for the framework Python (path in CLAUDE.md) — unblocks Today widget.
(nothing else yet — items append as workstreams land)

## Batch 1 — quick eyeball (optional, functionally verified already)
- [ ] Mind view → "What it remembers" card: confirm the memory editor looks right
      (edit a fact, add one, watch the char meter, create/delete/restore a topic file).
      Backend + rendering are curl- and harness-verified; this is pure visual QA.

## DECISION NEEDED — default model (see docs/FINDINGS.md)
- [ ] Keep **Qwen3-30B-A3B** as the default local model? (Recommended — the Hermes-8B
      won't reliably call tools, which breaks the "agent does things" vision. Qwen ~17GB,
      fits your 64GB fine, and is now loaded + proven.) Currently left ON Qwen. Say the
      word to revert to Hermes-8B.

## Trust panel eyeball (optional — functionally proven)
- [ ] Mind view → "Trust & Permissions" card: the 17 action-classes with Auto/Ask/Never
      controls + floor padlocks. All three tiers are live-drilled and working; visual QA only.
