#!/usr/bin/env bash
# seed_priors.sh — read-only prior dump for the You-Model onboarding interview.
# Prints: current You-Model state, USER.md core facts, and recent session titles.
# NEVER writes anything.
set -u

HUB="http://127.0.0.1:7788"

echo "== You-Model state (what already exists) =="
curl -s --max-time 5 "$HUB/api/youmodel" || echo "(hub unreachable — STOP: do not write memory without the hub)"
echo
echo
echo "== USER.md (semantic core) =="
cat "$HOME/.hermes/memories/USER.md" 2>/dev/null || echo "(no USER.md yet)"
echo
echo
echo "== Recent sessions (what they've been asking about) =="
sqlite3 "file:$HOME/.hermes/state.db?mode=ro" \
  "SELECT source || '  ' || title FROM sessions
   WHERE title IS NOT NULL AND archived=0
   ORDER BY started_at DESC LIMIT 15;" 2>/dev/null || echo "(state.db unreachable)"
