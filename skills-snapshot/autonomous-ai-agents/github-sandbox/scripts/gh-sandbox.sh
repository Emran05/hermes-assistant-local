#!/bin/bash
# gh-sandbox.sh — the agent's GitHub sandbox helper (github-sandbox skill).
# Loopback only, keyless, stdlib only (curl + python3, both stock on macOS).
#
#   gh-sandbox.sh status                           GET  /api/github/status
#   gh-sandbox.sh prepare <owner/repo>             POST /api/github/sandbox/prepare
#   gh-sandbox.sh refresh <owner/repo>             POST /api/github/sandbox/refresh
#   gh-sandbox.sh request <owner/repo> <slug> <title> [body|-] [new|append]
#                                                  POST /api/github/changes
#                                                  (body "-" = read it from stdin)
#   gh-sandbox.sh changes [<id>]                   GET  /api/github/changes[?id=]
#   gh-sandbox.sh withdraw <id>                    POST /api/github/changes/withdraw
#
# These six are the ONLY routes this script knows. Deciding, retracting,
# connecting, un-pausing and every other GitHub setting are the owner's, in the
# dashboard: this file never names them, and a request ends with "tell the
# owner it is waiting for their review, and stop".
#
# Every JSON body is built by python3's json.dumps from environment values and
# sent on stdin (--data-binary @-): nothing the agent passes is ever spliced
# into JSON, a URL or a command line by the shell. No Origin header is sent.
set -u

HUB="http://127.0.0.1:7788"

die() { echo "gh-sandbox: $*" >&2; exit 2; }

# grep anchors match per LINE, so "good<newline>anything" would pass an anchored
# pattern: refuse a line break up front, before any value reaches a URL.
oneline() {
  case "$1" in *$'\n'*|*$'\r'*) return 1 ;; esac
}
valid_repo() {
  oneline "$1" && printf '%s' "$1" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$'
}
valid_slug() {
  oneline "$1" && printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9-]{1,48}$'
}
valid_id() {
  oneline "$1" && printf '%s' "$1" | grep -Eq '^ghc-[0-9a-f]{12}$'
}

# The pretty-printer. The response goes in on STDIN: `changes <id>` can carry a
# diff of up to 1 MiB, and an environment variable that big makes exec fail with
# "Argument list too long" (macOS ARG_MAX is 1 MiB for argv + environment).
IFS= read -r -d '' SHOW_PY <<'PY' || true
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.stderr.write("gh-sandbox: the dashboard sent something that is not JSON\n")
    sys.exit(1)
# `changes <id>` answers {ok, change: {..., diff}}. The agent wrote this diff and
# the owner reads all of it on the review card; a megabyte of it in the agent's
# context helps nobody.
for holder in ((d, d.get("change")) if isinstance(d, dict) else ()):
    if isinstance(holder, dict) and isinstance(holder.get("diff"), str) \
            and len(holder["diff"]) > 4000:
        holder["diff"] = holder["diff"][:4000] + \
            "\n... (%d more characters; the owner sees the full diff)" % (len(holder["diff"]) - 4000)
    # a binary-flagged text file carries a text preview (up to the whole diff
    # budget); the agent wrote it, so 1,000 characters of each is plenty
    for f in ((holder.get("files") or []) if isinstance(holder, dict) else []):
        if isinstance(f, dict) and isinstance(f.get("text_preview"), str) \
                and len(f["text_preview"]) > 1000:
            f["text_preview"] = f["text_preview"][:1000] + \
                "\n... (%d more characters)" % (len(f["text_preview"]) - 1000)
print(json.dumps(d, indent=2, ensure_ascii=False))
if isinstance(d, dict):
    for k in ("message", "hint"):
        if d.get(k):
            sys.stderr.write("%s: %s\n" % (k, d[k]))
    if d.get("review_url"):
        sys.stderr.write("review: %s (the owner decides there; stop and tell them)\n"
                         % d["review_url"])
    sys.exit(0 if d.get("ok") else 1)
sys.exit(1)
PY

# show <curl exit> <response>: pretty-print; exit 1 when ok is false
show() {
  local rc="$1" resp="$2"
  if [ "$rc" -ne 0 ] || [ -z "$resp" ]; then
    echo "gh-sandbox: the dashboard did not answer at $HUB (is it running?)" >&2
    exit 1
  fi
  printf '%s' "$resp" | python3 -c "$SHOW_PY"
  exit $?
}

get() {    # get <path+query>
  local out rc
  out="$(curl -s -m "${2:-30}" "$HUB$1")"; rc=$?
  show "$rc" "$out"
}

post() {   # post <path> <json> [timeout]
  local out rc
  out="$(printf '%s' "$2" | curl -s -m "${3:-60}" -X POST "$HUB$1" \
           -H 'Content-Type: application/json' --data-binary @-)"; rc=$?
  show "$rc" "$out"
}

json_repo() {   # {"repo": $1}
  GHS_REPO="$1" python3 -c 'import json, os; print(json.dumps({"repo": os.environ["GHS_REPO"]}))'
}

cmd="${1:-}"; shift || true

case "$cmd" in
  status)
    get "/api/github/status" 20
    ;;

  prepare)
    repo="${1:-}"
    valid_repo "$repo" || die "usage: prepare <owner/repo>"
    post "/api/github/sandbox/prepare" "$(json_repo "$repo")" 300
    ;;

  refresh)
    repo="${1:-}"
    valid_repo "$repo" || die "usage: refresh <owner/repo>"
    post "/api/github/sandbox/refresh" "$(json_repo "$repo")" 300
    ;;

  request)
    repo="${1:-}"; slug="${2:-}"; title="${3:-}"; body="${4:-}"; mode="${5:-new}"
    valid_repo "$repo" || die "usage: request <owner/repo> <slug> <title> [body|-] [new|append]"
    valid_slug "$slug" || die "slug must match ^[a-z0-9][a-z0-9-]{1,48}$ (the branch is hermes/<slug>)"
    [ -n "$title" ] || die "a title is required"
    case "$mode" in new|append) ;; *) die "mode is new or append" ;; esac
    if [ "$body" = "-" ]; then body="$(cat)"; fi
    json="$(GHS_REPO="$repo" GHS_SLUG="$slug" GHS_TITLE="$title" GHS_BODY="$body" \
            GHS_MODE="$mode" python3 - <<'PY'
import json, os
print(json.dumps({"repo": os.environ["GHS_REPO"], "slug": os.environ["GHS_SLUG"],
                  "title": os.environ["GHS_TITLE"], "body": os.environ["GHS_BODY"],
                  "mode": os.environ["GHS_MODE"], "source": "api"}))
PY
)" || die "could not build the request body"
    post "/api/github/changes" "$json" 240
    ;;

  changes)
    id="${1:-}"
    if [ -z "$id" ]; then
      get "/api/github/changes" 30
    else
      valid_id "$id" || die "a change id looks like ghc-0123456789ab"
      get "/api/github/changes?id=$id" 30
    fi
    ;;

  withdraw)
    id="${1:-}"
    valid_id "$id" || die "usage: withdraw <ghc-id>"
    post "/api/github/changes/withdraw" \
         "$(GHS_ID="$id" python3 -c 'import json, os; print(json.dumps({"id": os.environ["GHS_ID"]}))')" 30
    ;;

  *)
    die "unknown command '$cmd' — use: status | prepare | refresh | request | changes | withdraw"
    ;;
esac
