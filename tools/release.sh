#!/usr/bin/env bash
# tools/release.sh — cut a release from a clean, verified tree.
#
#   tools/release.sh <version> "<commit title>" <<'MSG'
#   ...commit body...
#   MSG
#
# Steps, in order, each aborting the run on failure:
#   1. VERSION must equal <version> and CHANGELOG.md must have a "## [<version>]" section
#      (release.yml refuses a tag that does not match VERSION, and the release notes come
#      from that section).
#   2. Preflight: py_compile on dashboard/*.py, node --check on dashboard/*.js, bash -n on
#      the shell scripts, and the CI home-path grep (a personal /Users/<name> path fails CI).
#   3. Commit everything (or only what is staged when SHIP_STAGED_ONLY=1 — used to split a
#      tree with unfinished work into two releases).
#   4. Push main to origin AND public. A rejected public push (GitHub push protection,
#      network) stops here — nothing is tagged until both remotes have the commit.
#   5. Tag v<version> (annotated) and push it to both remotes.
#   6. Watch the public Release workflow and list the published assets.
#
# Never force-pushes. Never touches ~/.hermes.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
V="${1:-}"; TITLE="${2:-}"
[ -n "$V" ] && [ -n "$TITLE" ] || { echo "usage: tools/release.sh <version> \"<title>\" <<'MSG' ... MSG" >&2; exit 64; }
BODY="$(cat)"
PUBLIC_REPO="${HERMES_PUBLIC_REPO:-Emran05/hermes-assistant-local}"

die() { echo "release: $*" >&2; exit 1; }

# 1. version + changelog
[ "$(tr -d ' \t\n\r' < VERSION)" = "$V" ] || die "VERSION is $(cat VERSION), not $V"
grep -qE "^## \[$V\]" CHANGELOG.md || die "CHANGELOG.md has no ## [$V] section"

# 2. preflight
python3 -m py_compile dashboard/*.py || die "py_compile failed"
for f in dashboard/*.js; do node --check "$f" >/dev/null 2>&1 || die "node --check failed: $f"; done
for f in install.sh update.sh install-services.sh mlx-server.sh mlx-server-bg.sh tools/tests/run.sh; do
  [ -f "$f" ] && { bash -n "$f" || die "bash -n failed: $f"; }
done
hits="$(git grep -InE '/Users/[A-Za-z0-9._-]+' -- . ':!.github/workflows/ci.yml' 2>/dev/null \
        | grep -vE '/Users/(YOU|you|USER|username|me|<[^>]+>|\$[A-Za-z_{])' || true)"
[ -z "$hits" ] || { echo "$hits" | head -5 >&2; die "personal home path in tracked files (CI would fail)"; }
echo "preflight ok"

# 3. commit
[ -n "${SHIP_STAGED_ONLY:-}" ] || git add -A
git diff --cached --quiet && die "nothing to commit"
git commit -q -m "$TITLE" -m "$BODY" || die "commit failed"
echo "commit: $(git rev-parse --short HEAD) $TITLE"

# 4. push main to both remotes; a rejection stops the release before any tag exists
git push origin main 2>&1 | tail -1
git push origin main >/dev/null 2>&1 || die "origin push rejected — fix and re-run before tagging"
git push public main 2>&1 | tail -4
git push public main >/dev/null 2>&1 || die "PUBLIC push rejected (push protection / network) — fix and re-run before tagging"

# 5. tag both
git tag -a "v$V" -m "Hermes Assistant $V — see CHANGELOG.md" || die "tag exists — bump VERSION"
git push origin "v$V" 2>&1 | tail -1
git push public "v$V" 2>&1 | tail -1
git push public "v$V" >/dev/null 2>&1 || die "public tag push rejected"

# 6. watch the public release
sleep 20
id="$(gh run list -R "$PUBLIC_REPO" --workflow Release --limit 1 --json databaseId,headBranch \
      --jq ".[0] | select(.headBranch==\"v$V\") | .databaseId" 2>/dev/null || true)"
echo "release run: ${id:-not found yet}"
if [ -n "$id" ]; then
  gh run watch "$id" -R "$PUBLIC_REPO" --exit-status 2>&1 | tail -3 || true
fi
gh release view "v$V" -R "$PUBLIC_REPO" --json assets --jq '.assets[].name' 2>/dev/null || echo "(release not visible yet — check gh run list)"
