#!/usr/bin/env bash
# tools/release.sh — cut a release from a clean, verified tree.
#
#   git add <exactly what ships>          # nothing is staged for you
#   tools/release.sh <version> "<commit title>" <<'MSG'
#   ...commit body...
#   MSG
#
# EVERY check runs before ANYTHING is committed or pushed (B30: it used to
# `git add -A` after the checks — shipping whatever untracked file was lying
# around, unchecked — and only found an existing tag after main was already on
# both remotes). Steps, in order, each aborting the run on failure:
#   1. The candidate is the INDEX: what you staged, and nothing else. Unstaged
#      edits and untracked files are listed and do not ship. HEAD does not move.
#   2. Preflight on a checkout of exactly the candidate: VERSION equals
#      <version> and is a semver, CHANGELOG.md has a "## [<version>]" section,
#      then the CI static checks (py_compile, bash -n, node --check, every
#      inline <script>, the home-path grep) and the unit tier in strict mode.
#   3. Tag v<version> must not exist locally, on origin or on public.
#   4. origin/main and public/main must both be ancestors of the candidate
#      (fast-forward only — this never force-pushes).
#   5. The public range public/main..candidate is what `git push public main`
#      publishes, history included. Refused when any commit in it adds a
#      personal home path (CI's regex, over added lines, file names and commit
#      messages) or a never-publish path (.env, keys, databases, logs, build
#      and scratch output). The commits are listed and you confirm their number
#      — typed at the terminal, or RELEASE_PUBLISH_COMMITS=<n> when there is
#      none — so nothing you did not mean to publish rides along.
#   6. Commit (the tree is re-checked against what passed), push main to
#      origin, then public, then tag v<version> and push the tag to both.
#   7. Watch the public Release workflow (which runs CI again as a gate before
#      it builds) and list the published assets.
#
# Never force-pushes. Never touches ~/.hermes. SHIP_STAGED_ONLY is no longer
# needed: only staged content ever ships.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
V="${1:-}"; TITLE="${2:-}"
[ -n "$V" ] && [ -n "$TITLE" ] || { echo "usage: git add <paths>; tools/release.sh <version> \"<title>\" <<'MSG' ... MSG" >&2; exit 64; }
BODY="$(cat)"
PUBLIC_REPO="${HERMES_PUBLIC_REPO:-Emran05/hermes-assistant-local}"

# The CI hygiene regex (.github/workflows/ci.yml, "No personal home paths") —
# tools/tests/unit/t_b30_release.py fails if the two ever drift apart.
HOME_RE='/Users/[A-Za-z0-9._-]+'
HOME_OK='/Users/(YOU|you|USER|username|me|<[^>]+>|\$[A-Za-z_{])'
# paths that must never reach a public remote, whatever .gitignore says today
NEVER_RE='(^|/)(\.env[^/]*|[^/]*\.env|[^/]*\.(p12|pem|key|mobileprovision|db|sqlite|sqlite3|log)|id_(rsa|dsa|ecdsa|ed25519)[^/]*|serve-token|messages-token)$|^(\.scratch|graphify-out|dist|app/build)/'

WT=""; LOG=""
cleanup() {
  if [ -n "$WT" ]; then git worktree remove --force "$WT" >/dev/null 2>&1; rm -rf "$WT"; git worktree prune >/dev/null 2>&1; fi
  [ -n "$LOG" ] && rm -f "$LOG"
}
trap cleanup EXIT
die() { echo "release: $*" >&2; exit 1; }
push() {   # one push, its real exit status, the last lines of its output
  local out rc
  out="$(git push "$@" 2>&1)"; rc=$?
  printf '%s\n' "$out" | tail -3
  return $rc
}

# 1. the candidate: exactly the index
[ "$(git symbolic-ref -q --short HEAD)" = main ] || die "not on main — releases are cut from main"
git diff --cached --quiet && die "nothing is staged — stage exactly what ships (git add <paths>); nothing is added for you"
TREE="$(git write-tree)" || die "git write-tree failed (unmerged paths?)"
PARENT="$(git rev-parse --verify -q HEAD)" || die "no HEAD"
MSG="$(printf '%s\n\n%s\n' "$TITLE" "$BODY" | git stripspace)"
CAND="$(printf '%s\n' "$MSG" | git commit-tree "$TREE" -p "$PARENT")" || die "git commit-tree failed"
echo "candidate: $(git diff --cached --name-only | wc -l | tr -d ' ') staged file(s)"
left="$(git status --porcelain --untracked-files=all | grep -vE '^[MADRC] ' || true)"
[ -z "$left" ] || { echo "not shipping (unstaged or untracked):"; printf '%s\n' "$left" | head -20 | sed 's/^/  /'; }

# 2. preflight, on a checkout of exactly the candidate
WT="$(mktemp -d "${TMPDIR:-/tmp}/hermes-release.XXXXXX")" || die "mktemp failed"
LOG="$(mktemp "${TMPDIR:-/tmp}/hermes-release-tests.XXXXXX")" || die "mktemp failed"
git worktree add -q --detach "$WT" "$CAND" >/dev/null 2>&1 || die "could not check out the candidate"
preflight() {
  local rc=0 f hits out
  [ "$(tr -d ' \t\n\r' < VERSION)" = "$V" ] || { echo "VERSION is $(cat VERSION), not $V"; return 1; }
  printf '%s\n' "$V" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' || { echo "VERSION $V is not a semver"; return 1; }
  grep -qE "^## \[$V\]" CHANGELOG.md || { echo "CHANGELOG.md has no ## [$V] section"; return 1; }
  # the CI static checks, over the candidate's own tracked files
  git ls-files -z '*.py' | xargs -0 python3 -m py_compile || { echo "py_compile failed"; rc=1; }
  while IFS= read -r f; do bash -n "$f" || { echo "bash -n failed: $f"; rc=1; }; done < <(git ls-files '*.sh')
  while IFS= read -r f; do
    node --check "$f" >/dev/null 2>&1 || { echo "node --check failed: $f"; rc=1; }
  done < <(git ls-files 'dashboard/*.js')
  out="$(HERMES_REPO="$PWD" python3 tools/tests/lib/check_inline_scripts.py 2>&1)" \
    || { printf '%s\n' "$out" | grep -v '^ok'; echo "an inline <script> does not parse"; rc=1; }
  hits="$(git grep -InE "$HOME_RE" -- . ':!.github/workflows/ci.yml' | grep -vE "$HOME_OK" || true)"
  [ -z "$hits" ] || { printf '%s\n' "$hits" | head -5; echo "personal home path in tracked files (CI would fail)"; rc=1; }
  [ $rc -eq 0 ] || return 1
  echo "static checks ok — running the unit tier"
  if ! HERMES_REPO="$PWD" HERMES_TESTS_STRICT=1 tools/tests/run.sh unit >"$LOG" 2>&1; then
    grep -E '^FAILED:|TOTAL' "$LOG" | tail -3; tail -15 "$LOG"; return 1
  fi
  grep -E '^[[:space:]]+TOTAL' "$LOG" | tail -1
}
( cd "$WT" && preflight ) || die "preflight failed — nothing was committed or pushed"
echo "preflight ok"

# 3. the tag must be new everywhere, before anything moves
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && die "tag v$V already exists locally — bump VERSION"
for r in origin public; do
  t="$(git ls-remote --tags "$r" "refs/tags/v$V")" || die "cannot reach $r to check the tag"
  [ -z "$t" ] || die "tag v$V already exists on $r — bump VERSION; nothing was pushed"
done

# 4. fast-forward only, on both remotes
for r in origin public; do
  git fetch -q "$r" "+refs/heads/main:refs/remotes/$r/main" || die "cannot fetch $r/main"
  git merge-base --is-ancestor "refs/remotes/$r/main" "$CAND" \
    || die "$r/main is not an ancestor of this release — integrate it first (this never force-pushes)"
done

# 5. what the public push would publish
RANGE="refs/remotes/public/main..$CAND"
n="$(git rev-list --count "$RANGE")"
echo "public would receive $n commit(s):"
git log --format='  %h %s' "$RANGE"
git diff --shortstat "refs/remotes/public/main" "$CAND" | sed 's/^/ /'
# --cc: `git log -p`/`--name-only` print NOTHING for a merge commit, so a line
# or file that only a merge introduces (a conflict resolution) would ride along
# unseen; the dense combined diff shows exactly what the merge itself added
hits="$( { git log -p --cc --format= "$RANGE" -- . ':!.github/workflows/ci.yml' \
             | grep -E '^\+' | grep -vE '^\+\+\+ (b/|/dev/null)'
           git log --format=%B "$RANGE"
           git log --cc --name-only --format= "$RANGE"; } \
         | grep -E "$HOME_RE" | grep -vE "$HOME_OK" || true)"
[ -z "$hits" ] || { printf '%s\n' "$hits" | head -5 >&2
  die "a personal home path is in the history this would publish (public/main..release) — fix those local commits first; nothing was pushed"; }
bad="$(git log --cc --name-only --format= --diff-filter=ACMR "$RANGE" | sort -u | grep -E "$NEVER_RE" || true)"
[ -z "$bad" ] || { printf '%s\n' "$bad" | head -10 >&2
  die "the history this would publish adds never-publish paths — fix those local commits first; nothing was pushed"; }
if [ -n "${RELEASE_PUBLISH_COMMITS:-}" ]; then
  [ "$RELEASE_PUBLISH_COMMITS" = "$n" ] \
    || die "RELEASE_PUBLISH_COMMITS=$RELEASE_PUBLISH_COMMITS but public would receive $n commit(s) — check what else would be published; nothing was pushed"
elif { exec 3</dev/tty; } 2>/dev/null; then
  printf 'Type %s to publish exactly these commits: ' "$n" >&2
  ans=""; read -r ans <&3; exec 3<&-
  [ "$ans" = "$n" ] || die "not confirmed — nothing was pushed"
else
  die "no terminal to confirm the $n commit(s) above — check them, then re-run with RELEASE_PUBLISH_COMMITS=$n"
fi

# 6. commit what passed, then push: main to both remotes, then the tag
[ "$(git rev-parse HEAD)" = "$PARENT" ] || die "HEAD moved while the checks ran — re-run"
[ "$(git write-tree)" = "$TREE" ] || die "the staged content changed while the checks ran — re-run"
args=(-q -m "$TITLE"); [ -n "$BODY" ] && args+=(-m "$BODY")
git commit "${args[@]}" || die "commit failed"
[ "$(git rev-parse 'HEAD^{tree}')" = "$TREE" ] && [ "$(git rev-parse HEAD^)" = "$PARENT" ] \
  || die "the commit is not what passed the checks (a hook changed it?) — inspect HEAD; nothing was pushed"
echo "commit: $(git rev-parse --short HEAD) $TITLE"
push origin main || die "origin push rejected — nothing was tagged; fix it, then push main to both remotes and tag by hand"
push public main || die "PUBLIC push rejected (push protection / network) — main is on origin only and nothing is tagged; fix it, then: git push public main, and tag by hand"
git tag -a "v$V" -m "Hermes Assistant $V — see CHANGELOG.md" || die "could not create tag v$V"
push origin "v$V" || die "origin tag push rejected"
push public "v$V" || die "public tag push rejected"

# 7. watch the public release
sleep 20
id="$(gh run list -R "$PUBLIC_REPO" --workflow Release --limit 1 --json databaseId,headBranch \
      --jq ".[0] | select(.headBranch==\"v$V\") | .databaseId" 2>/dev/null || true)"
echo "release run: ${id:-not found yet}"
if [ -n "$id" ]; then
  gh run watch "$id" -R "$PUBLIC_REPO" --exit-status 2>&1 | tail -3 || true
fi
gh release view "v$V" -R "$PUBLIC_REPO" --json assets --jq '.assets[].name' 2>/dev/null || echo "(release not visible yet — check gh run list)"
