#!/bin/bash
# hubctl.sh — thin, stdlib-only helper for the Hermes dashboard loopback API.
# Deps: curl + python3 (both stock on macOS). No secrets, no external hosts.
#
#   hubctl.sh get <path>                 # GET any endpoint, pretty-printed
#   hubctl.sh memory-get <file.md>       # fetch one memory file (content+etag)
#   hubctl.sh memory-put <file.md>       # save; new content on stdin.
#                                        #   core files (USER.md/MEMORY.md):
#                                        #   stdin = entries separated by a
#                                        #   lone '§' line. Automatic etag
#                                        #   round-trip; refuses on conflict.
#   hubctl.sh recorder-tail <n>          # last n flight-recorder actions
#   hubctl.sh watchtower <op> '<json>'   # POST {op, ...json} to /api/watchtower
set -euo pipefail

HUB="${HUB_URL:-http://127.0.0.1:7788}"

die() { echo "hubctl: $*" >&2; exit 1; }

cmd="${1:-}"; shift || true

case "$cmd" in
  get)
    path="${1:-}"; [ -n "$path" ] || die "usage: get /api/..."
    curl -s -m 20 "$HUB$path" | python3 -m json.tool
    ;;

  memory-get)
    f="${1:-}"; [ -n "$f" ] || die "usage: memory-get <file.md>"
    curl -s -m 20 "$HUB/api/memory/file?name=$f" | python3 -m json.tool
    ;;

  memory-put)
    f="${1:-}"; [ -n "$f" ] || die "usage: memory-put <file.md>  (content on stdin)"
    # stdin -> temp file -> python: GET etag/kind, build payload, POST save.
    # (temp file, not stdin: python reads its program from stdin here)
    tmp="$(mktemp /tmp/hubctl.XXXXXX)"
    trap 'rm -f "$tmp"' EXIT
    cat > "$tmp"
    python3 - "$HUB" "$f" "$tmp" <<'PYEOF'
import json, sys, urllib.request

hub, name = sys.argv[1], sys.argv[2]
with open(sys.argv[3], "r", encoding="utf-8") as fh:
    new_text = fh.read()

def call(path, body=None):
    if body is None:
        req = urllib.request.Request(hub + path)
    else:
        req = urllib.request.Request(
            hub + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

cur = call("/api/memory/file?name=" + name)
if not cur.get("ok"):
    sys.exit("hubctl: read failed: " + json.dumps(cur))

body = {"name": name, "base_etag": cur["etag"]}
if cur.get("core"):
    entries = [s.strip() for s in new_text.split("\n§\n") if s.strip()]
    bad = [e for e in entries if "§" in e]
    if bad:
        sys.exit("hubctl: an entry may not contain the § delimiter")
    body["entries"] = entries
else:
    body["content"] = new_text

out = call("/api/memory/save", body)
if out.get("ok"):
    print(json.dumps({"ok": True, "name": name, "etag": out.get("etag")}))
else:
    # etag conflict: server returns current content — surface it, never force
    sys.exit("hubctl: save refused (likely etag conflict — re-read and merge): "
             + json.dumps(out)[:400])
PYEOF
    ;;

  recorder-tail)
    n="${1:-10}"
    curl -sf -m 20 "$HUB/api/recorder?limit=$n" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(json.dumps(d.get("counts", {})))
for a in d.get("actions", []):
    print(json.dumps({k: a.get(k) for k in
        ("id", "ts", "kind", "tool", "target", "reversible", "status")}))
'
    ;;

  watchtower)
    op="${1:-}"; extra="${2:-}"
    [ -n "$extra" ] || extra='{}'
    [ -n "$op" ] || die "usage: watchtower <op> '<json>'"
    printf '%s' "$extra" | python3 -c "
import json, sys, urllib.request
extra = json.load(sys.stdin)
extra['op'] = '$op'
req = urllib.request.Request(
    '$HUB/api/watchtower', data=json.dumps(extra).encode('utf-8'),
    headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=20) as r:
    print(json.dumps(json.loads(r.read().decode('utf-8')), indent=2))
"
    ;;

  *)
    die "unknown subcommand '$cmd' — use: get | memory-get | memory-put | recorder-tail | watchtower"
    ;;
esac
