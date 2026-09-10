#!/usr/bin/env bash
# The owner's escalation switch is THEIR setting: capture it up front and put it
# back no matter how this script exits (an earlier version forced it to true).
E0=$(curl -s ${B:-http://127.0.0.1:7788}/api/claude/escalate | python3 -c "import sys,json;print(str(json.load(sys.stdin).get('enabled',True)).lower())" 2>/dev/null || echo true)
restore_esc(){ curl -s -X POST -H 'Content-Type: application/json' --data "{\"enabled\":$E0}" ${B:-http://127.0.0.1:7788}/api/claude/escalate >/dev/null 2>&1 || true; }
trap restore_esc EXIT
# Acceptance tests for round 2 (run AFTER the dashboard has been restarted).
B=http://localhost:7788; B2=http://127.0.0.1:7788
pass=0; fail=0
ok(){ printf 'PASS %s\n' "$1"; pass=$((pass+1)); }
ko(){ printf 'FAIL %s -- %s\n' "$1" "$2"; fail=$((fail+1)); }
code(){ curl -s -o /dev/null -w '%{http_code}' -m 10 "$@"; }
J(){ python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "== 1. same-origin guard =="
c=$(code $B/api/models);                                                        [ "$c" = 200 ] && ok "GET localhost host allowed" || ko "GET localhost" "$c"
c=$(code $B2/api/models);                                                       [ "$c" = 200 ] && ok "GET 127.0.0.1 host allowed" || ko "GET 127.0.0.1" "$c"
c=$(code -H 'Host: evil.example' $B2/api/models);                               [ "$c" = 403 ] && ok "GET bad Host refused (rebinding)" || ko "bad Host" "$c"
c=$(code -H 'Host: evil.example' $B2/);                                         [ "$c" = 403 ] && ok "app shell bad Host refused" || ko "shell bad Host" "$c"
c=$(code -X POST -H 'Origin: https://evil.example' -H 'Content-Type: text/plain' --data '{"enabled":false}' $B2/api/claude/escalate)
                                                                                [ "$c" = 403 ] && ok "POST cross-origin Origin refused" || ko "cross-origin POST" "$c"
c=$(code -X POST -H 'Sec-Fetch-Site: cross-site' -H 'Content-Type: application/json' --data '{"enabled":true}' $B2/api/claude/escalate)
                                                                                [ "$c" = 403 ] && ok "POST Sec-Fetch-Site cross-site refused" || ko "sec-fetch-site" "$c"
c=$(code -X POST -H 'Origin: http://127.0.0.1:7788' -H 'Content-Type: application/json' --data '{"enabled":true}' $B2/api/claude/escalate)
                                                                                [ "$c" = 200 ] && ok "POST same-origin Origin allowed" || ko "same-origin POST" "$c"
c=$(code -X POST -H 'Content-Type: application/json' --data '{"enabled":true}' $B/api/claude/escalate)
                                                                                [ "$c" = 200 ] && ok "POST without Origin (curl/launchd) allowed" || ko "no-origin POST" "$c"

echo "== 2. escalation toggle =="
S=~/.hermes/dashboard/settings.json
e0=$(curl -s $B/api/claude/escalate | J "d['enabled']");                        [ "$e0" = True ] || [ "$e0" = False ] && ok "GET escalate returns a boolean ($e0)" || ko "GET escalate" "$e0"
e1=$(curl -s -X POST -H 'Content-Type: application/json' --data '{"enabled":false}' $B/api/claude/escalate | J "d['enabled']")
                                                                                [ "$e1" = False ] && ok "POST disable" || ko "POST disable" "$e1"
p=$(python3 -c "import json;print(json.load(open('$S')).get('claude_escalation',{}).get('enabled'))"); [ "$p" = False ] && ok "persisted in settings.json" || ko "persist" "$p"
a=$(curl -s $B/api/claude/autoroute | J "d.get('claude_escalation')");          [ "$a" = False ] && ok "autoroute GET carries claude_escalation=false" || ko "autoroute flag" "$a"
t0=$(date +%s); r=$(curl -s -m 20 -X POST -H 'Content-Type: application/json' --data '{"task":"Say hi in three words.","depth":"quick"}' $B/api/claude/think); t1=$(date +%s)
rs=$(printf '%s' "$r" | J "str(d.get('refused'))+':'+str(d.get('reason'))" 2>/dev/null); [ "$rs" = "True:escalation_off" ] && [ $((t1-t0)) -le 3 ] && ok "claude_think refused at choke point in $((t1-t0))s" || ko "think refusal" "$rs ($((t1-t0))s) $r"
e2=$(curl -s -X POST -H 'Content-Type: application/json' --data "{\"enabled\":$E0}" $B/api/claude/escalate | J "str(d['enabled']).lower()"); [ "$e2" = "$E0" ] && ok "POST restored the owner's switch ($E0)" || ko "restore" "$e2"

echo "== 3. models payload / downloads =="
k=$(curl -s $B/api/models | J "all('download_error' in m for m in d['models'])"); [ "$k" = True ] && ok "download_error present on every roster row" || ko "download_error" "$k"
u=$(curl -s -X POST -H 'Content-Type: application/json' --data '{"id":"nobody/nothing"}' $B/api/models/download | J "d.get('ok'),d.get('error')"); case "$u" in *False*unknown*) ok "download of unknown id refused";; *) ko "unknown download" "$u";; esac
n=$(curl -s $B/api/models | J "[m['label'] for m in d['models']]"); echo "   roster: $n"

echo "== 4. pause while already down (regression check) =="
D=~/.hermes/dashboard; before=$(ls $D | grep -E '^agent-(paused|idle-suspended)$' | tr '\n' ' '); idle=$(cat $D/agent-idle-suspended 2>/dev/null)
echo "   markers before: [$before]"
if [ -z "$(pgrep -f 'mlx_lm server|mlx-vlm-launch')" ]; then
  r=$(curl -s -m 30 -X POST $B/api/agent/pause); okv=$(printf '%s' "$r" | J "d.get('ok')")
  [ "$okv" = True ] && ok "pause succeeds when the server is already down" || ko "pause while down" "$r"
  # restore exact prior state: drop the pause marker, put back the idle marker if it existed
  rm -f $D/agent-paused; [ -n "$idle" ] && printf '%s' "$idle" > $D/agent-idle-suspended
  after=$(ls $D | grep -E '^agent-(paused|idle-suspended)$' | tr '\n' ' '); [ "$after" = "$before" ] && ok "marker state restored [$after]" || ko "marker restore" "before=[$before] after=[$after]"
else
  echo "   skipped: model server is running"
fi
echo "TESTS $pass passed $fail failed"
exit $fail
