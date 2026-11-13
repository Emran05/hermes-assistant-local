#!/usr/bin/env bash
# Acceptance tests for round 2 (run AFTER the dashboard has been restarted).
#
# The owner's escalation switch and agent markers are THEIR settings. This
# script captures the switch up front and puts it back no matter how it exits,
# and it FAILS CLOSED (C61): if the switch cannot be read as a real boolean it
# refuses to run and changes nothing — it used to fall back to "true", so a
# failed read made the EXIT trap switch Claude escalation ON. Every probe POST
# sends the owner's own value, so a probe never flips the switch either, and
# the claude/think refusal check runs only once the switch is verifiably off
# (otherwise it would be a real `claude -p` call).
#
# HERMES_DASH_BASE overrides the dashboard URL (default http://localhost:7788);
# the same port is probed as both localhost and 127.0.0.1.
BASE="${HERMES_DASH_BASE:-http://localhost:7788}"
PORT="${BASE##*:}"; PORT="${PORT%%/*}"
B="http://localhost:$PORT"; B2="http://127.0.0.1:$PORT"
D=~/.hermes/dashboard

E0=$(curl -fsS -m 10 "$B/api/claude/escalate" 2>/dev/null | python3 -c "
import sys, json
v = json.load(sys.stdin).get('enabled')
if not isinstance(v, bool): sys.exit(1)
print(str(v).lower())" 2>/dev/null)
case "$E0" in
  true|false) ;;
  *) echo "verify_round2: could not read the owner's Claude escalation switch from $B/api/claude/escalate" >&2
     echo "verify_round2: refusing to run — nothing was changed" >&2
     echo "TESTS 0 passed 1 failed"
     exit 2 ;;
esac

PAUSED_BY_US=0; HAD_PAUSE=0; IDLE0=""; HAD_IDLE=0
restore(){
  if ! curl -fsS -m 10 -X POST -H 'Content-Type: application/json' --data "{\"enabled\":$E0}" "$B/api/claude/escalate" >/dev/null 2>&1; then
    echo "!! could not restore the owner's Claude escalation switch to $E0 — set it in Settings" >&2
  fi
  if [ "$PAUSED_BY_US" = 1 ]; then
    # exact prior state: drop OUR pause marker (never the owner's), put back
    # the idle marker if it existed
    [ "$HAD_PAUSE" = 1 ] || rm -f "$D/agent-paused"
    [ "$HAD_IDLE" = 1 ] && printf '%s' "$IDLE0" > "$D/agent-idle-suspended"
    PAUSED_BY_US=0
  fi
}
trap restore EXIT

pass=0; fail=0
ok(){ printf 'PASS %s\n' "$1"; pass=$((pass+1)); }
ko(){ printf 'FAIL %s -- %s\n' "$1" "$2"; fail=$((fail+1)); }
code(){ curl -s -o /dev/null -w '%{http_code}' -m 10 "$@"; }
J(){ python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
SAME="{\"enabled\":$E0}"     # every probe body: a broken guard would change nothing

echo "== 1. same-origin guard =="
c=$(code $B/api/models);                                                        [ "$c" = 200 ] && ok "GET localhost host allowed" || ko "GET localhost" "$c"
c=$(code $B2/api/models);                                                       [ "$c" = 200 ] && ok "GET 127.0.0.1 host allowed" || ko "GET 127.0.0.1" "$c"
c=$(code -H 'Host: evil.example' $B2/api/models);                               [ "$c" = 403 ] && ok "GET bad Host refused (rebinding)" || ko "bad Host" "$c"
c=$(code -H 'Host: evil.example' $B2/);                                         [ "$c" = 403 ] && ok "app shell bad Host refused" || ko "shell bad Host" "$c"
c=$(code -X POST -H 'Origin: https://evil.example' -H 'Content-Type: text/plain' --data "$SAME" $B2/api/claude/escalate)
                                                                                [ "$c" = 403 ] && ok "POST cross-origin Origin refused" || ko "cross-origin POST" "$c"
c=$(code -X POST -H 'Sec-Fetch-Site: cross-site' -H 'Content-Type: application/json' --data "$SAME" $B2/api/claude/escalate)
                                                                                [ "$c" = 403 ] && ok "POST Sec-Fetch-Site cross-site refused" || ko "sec-fetch-site" "$c"
c=$(code -X POST -H "Origin: $B2" -H 'Content-Type: application/json' --data "$SAME" $B2/api/claude/escalate)
                                                                                [ "$c" = 200 ] && ok "POST same-origin Origin allowed" || ko "same-origin POST" "$c"
c=$(code -X POST -H 'Content-Type: application/json' --data "$SAME" $B/api/claude/escalate)
                                                                                [ "$c" = 200 ] && ok "POST without Origin (curl/launchd) allowed" || ko "no-origin POST" "$c"

echo "== 2. escalation toggle =="
S=~/.hermes/dashboard/settings.json
e0=$(curl -s $B/api/claude/escalate | J "d['enabled']");                        [ "$e0" = True ] || [ "$e0" = False ] && ok "GET escalate returns a boolean ($e0)" || ko "GET escalate" "$e0"
e1=$(curl -s -X POST -H 'Content-Type: application/json' --data '{"enabled":false}' $B/api/claude/escalate | J "d['enabled']")
                                                                                [ "$e1" = False ] && ok "POST disable" || ko "POST disable" "$e1"
p=$(python3 -c "import json;print(json.load(open('$S')).get('claude_escalation',{}).get('enabled'))"); [ "$p" = False ] && ok "persisted in settings.json" || ko "persist" "$p"
a=$(curl -s $B/api/claude/autoroute | J "d.get('claude_escalation')");          [ "$a" = False ] && ok "autoroute GET carries claude_escalation=false" || ko "autoroute flag" "$a"
if [ "$e1" = False ] && [ "$p" = False ] && [ "$a" = False ]; then
  t0=$(date +%s); r=$(curl -s -m 20 -X POST -H 'Content-Type: application/json' --data '{"task":"Say hi in three words.","depth":"quick"}' $B/api/claude/think); t1=$(date +%s)
  rs=$(printf '%s' "$r" | J "str(d.get('refused'))+':'+str(d.get('reason'))" 2>/dev/null); [ "$rs" = "True:escalation_off" ] && [ $((t1-t0)) -le 3 ] && ok "claude_think refused at choke point in $((t1-t0))s" || ko "think refusal" "$rs ($((t1-t0))s) $r"
else
  ko "think refusal" "not attempted: the switch is not verifiably off, so the call could reach Claude"
fi
e2=$(curl -s -X POST -H 'Content-Type: application/json' --data "{\"enabled\":$E0}" $B/api/claude/escalate | J "str(d['enabled']).lower()"); [ "$e2" = "$E0" ] && ok "POST restored the owner's switch ($E0)" || ko "restore" "$e2"

echo "== 3. models payload / downloads =="
k=$(curl -s $B/api/models | J "all('download_error' in m for m in d['models'])"); [ "$k" = True ] && ok "download_error present on every roster row" || ko "download_error" "$k"
u=$(curl -s -X POST -H 'Content-Type: application/json' --data '{"id":"nobody/nothing"}' $B/api/models/download | J "d.get('ok'),d.get('error')"); case "$u" in *False*unknown*) ok "download of unknown id refused";; *) ko "unknown download" "$u";; esac
n=$(curl -s $B/api/models | J "[m['label'] for m in d['models']]"); echo "   roster: $n"

echo "== 4. pause while already down (regression check) =="
before=$(ls $D | grep -E '^agent-(paused|idle-suspended)$' | tr '\n' ' ')
[ -e "$D/agent-paused" ] && HAD_PAUSE=1
[ -e "$D/agent-idle-suspended" ] && { HAD_IDLE=1; IDLE0=$(cat "$D/agent-idle-suspended" 2>/dev/null); }
echo "   markers before: [$before]"
if [ -z "$(pgrep -f 'mlx_lm server|mlx-vlm-launch')" ]; then
  PAUSED_BY_US=1
  r=$(curl -s -m 30 -X POST $B/api/agent/pause); okv=$(printf '%s' "$r" | J "d.get('ok')")
  [ "$okv" = True ] && ok "pause succeeds when the server is already down" || ko "pause while down" "$r"
  restore
  after=$(ls $D | grep -E '^agent-(paused|idle-suspended)$' | tr '\n' ' '); [ "$after" = "$before" ] && ok "marker state restored [$after]" || ko "marker restore" "before=[$before] after=[$after]"
else
  echo "   SKIP pause-while-down: a model server is running"
fi
echo "TESTS $pass passed $fail failed"
exit $fail
