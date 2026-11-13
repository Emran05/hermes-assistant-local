#!/usr/bin/env python3
"""Regression test for C61 (docs/audits/2026-10-21-codebase-audit.md).

live/verify_round2.sh captures the owner's Claude escalation switch and puts it
back from an EXIT trap.  The capture fell back to "true" when the read failed
(`.get('enabled', True)` and `|| echo true`), so a dashboard that was down, slow
or answering HTML made the trap switch escalation ON.  Its same-origin probes
also POSTed `{"enabled":true}` — turning the switch on mid-run — the
claude/think refusal check ran even when the disable had not taken (a real
`claude -p` call), and the marker restore deleted a pause the OWNER had set.

This runs the REAL script with `curl` and `pgrep` replaced on PATH by a fake
dashboard (a python shim that records every request and keeps the switch's
state in a file) and a throwaway HOME, so nothing here can reach a network, the
live dashboard or a model.  It asserts:
  * an unreadable / non-JSON / non-boolean switch => the script refuses, exits
    non-zero, and sends NO POST at all;
  * with the owner's switch off, it is never on at any point of the run;
  * with it on, it ends on, and claude/think only ever sees it off;
  * a disable that does not take => claude/think is never called;
  * an owner's own pause marker survives; an idle marker is put back.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SCRIPT = os.environ.get("T_C61_SCRIPT") or os.path.join(
    REPO, "tools", "tests", "live", "verify_round2.sh")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:].replace("\n", "\n    | ")))


# --------------------------------------------------------------------------
# the fake dashboard, as a `curl` on PATH
# --------------------------------------------------------------------------
CURL = r'''#!/usr/bin/env python3
import json, os, sys
st_path = os.environ["SHIM_STATE"]
log_path = os.environ["SHIM_LOG"]
mode = os.environ.get("SHIM_MODE", "")
home = os.environ["HOME"]
D = os.path.join(home, ".hermes", "dashboard")

a = sys.argv[1:]
method, url, data, headers, out, wfmt, fail = "GET", None, None, [], None, None, False
i = 0
while i < len(a):
    x = a[i]
    if x in ("-X", "--request"):
        method = a[i + 1]; i += 2; continue
    if x in ("-H", "--header"):
        headers.append(a[i + 1]); i += 2; continue
    if x in ("-d", "--data", "--data-raw"):
        data = a[i + 1]; i += 2; continue
    if x == "-o":
        out = a[i + 1]; i += 2; continue
    if x == "-w":
        wfmt = a[i + 1]; i += 2; continue
    if x == "-m":
        i += 2; continue
    if x.startswith("-") and not x.startswith("--"):
        if "f" in x[1:]:
            fail = True
        i += 1; continue
    if x.startswith("--"):
        i += 1; continue
    url = x; i += 1
if data is not None and method == "GET":
    method = "POST"
path = "/" + url.split("://", 1)[-1].split("/", 1)[-1] if url and "/" in url.split("://", 1)[-1] else "/"

with open(st_path) as f:
    st = json.load(f)
hdr = {h.split(":", 1)[0].strip().lower(): h.split(":", 1)[1].strip() for h in headers if ":" in h}

def save():
    with open(st_path, "w") as f:
        json.dump(st, f)

def write_settings():
    os.makedirs(D, exist_ok=True)
    with open(os.path.join(D, "settings.json"), "w") as f:
        json.dump({"claude_escalation": {"enabled": st["enabled"]}}, f)

rec = {"method": method, "path": path, "data": data, "headers": hdr,
       "state_before": st["enabled"]}
code, body = 200, ""

if mode == "down":
    rec["code"] = 0
    with open(log_path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    sys.exit(7)                       # curl: couldn't connect

refused = (hdr.get("host", "").startswith("evil")
           or ("origin" in hdr and not hdr["origin"].startswith(("http://127.0.0.1:", "http://localhost:")))
           or (method == "POST" and hdr.get("sec-fetch-site") == "cross-site"))
if refused:
    code, body = 403, json.dumps({"error": "forbidden"})
elif path == "/api/claude/escalate" and method == "GET":
    if mode == "html":
        body = "<html><body>Bad Gateway</body></html>"
    elif mode == "nonbool":
        body = json.dumps({"enabled": "yes"})
    elif mode == "http500":
        code, body = 500, json.dumps({"error": "boom"})
    else:
        body = json.dumps({"enabled": st["enabled"]})
elif path == "/api/claude/escalate":
    want = json.loads(data or "{}").get("enabled")
    if not (mode == "stuck" and want is False):
        st["enabled"] = bool(want)
        save()
        write_settings()
    body = json.dumps({"ok": True, "enabled": st["enabled"]})
elif path == "/api/claude/autoroute":
    body = json.dumps({"mode": "off", "claude_escalation": st["enabled"]})
elif path == "/api/claude/think":
    rec["think_while_enabled"] = st["enabled"]
    if st["enabled"]:
        body = json.dumps({"ok": True, "text": "hi there friend"})
    else:
        body = json.dumps({"ok": False, "refused": True, "reason": "escalation_off", "text": "off"})
elif path == "/api/models" or path == "/":
    body = json.dumps({"models": [{"label": "Fake", "download_error": None}]})
elif path == "/api/models/download":
    body = json.dumps({"ok": False, "error": "unknown model"})
elif path == "/api/agent/pause":
    os.makedirs(D, exist_ok=True)
    open(os.path.join(D, "agent-paused"), "w").close()
    try:
        os.unlink(os.path.join(D, "agent-idle-suspended"))
    except OSError:
        pass
    body = json.dumps({"ok": True})
else:
    code, body = 404, json.dumps({"error": "not found"})

rec["code"] = code
rec["state_after"] = st["enabled"]
with open(log_path, "a") as f:
    f.write(json.dumps(rec) + "\n")
if fail and code >= 400:
    sys.exit(22)
if out != "/dev/null":
    sys.stdout.write(body)
if wfmt:
    sys.stdout.write(wfmt.replace("%{http_code}", str(code)))
'''

PGREP = "#!/bin/sh\nexit 1\n"     # no model server, ever


def scenario(name, *, mode="", owner=False, markers=None):
    """Run the real script against the fake dashboard -> (rc, out, log, home)."""
    root = tempfile.mkdtemp(prefix="t-c61-%s-" % name)
    home = os.path.join(root, "home")
    d = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(d)
    shim = os.path.join(root, "bin")
    os.makedirs(shim)
    for fn, body in (("curl", CURL), ("pgrep", PGREP)):
        p = os.path.join(shim, fn)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)
    state = os.path.join(root, "state.json")
    with open(state, "w") as f:
        json.dump({"enabled": owner}, f)
    with open(os.path.join(d, "settings.json"), "w") as f:
        json.dump({"claude_escalation": {"enabled": owner}}, f)
    for m, content in (markers or {}).items():
        with open(os.path.join(d, m), "w") as f:
            f.write(content)
    log = os.path.join(root, "requests.jsonl")
    open(log, "w").close()
    env = dict(os.environ)
    env.update({
        "HOME": home,
        # the shims first, then the python running this suite (the script's
        # own `python3 -c` calls), then the system — no other curl can win
        "PATH": ":".join([shim, os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
        "SHIM_STATE": state, "SHIM_LOG": log, "SHIM_MODE": mode,
        "HERMES_DASH_BASE": "http://127.0.0.1:9",     # a closed port, like run.sh
    })
    env.pop("B", None)
    # the shim, and only the shim, is what the script will call
    which = subprocess.run(["bash", "-c", "command -v curl; command -v pgrep"],
                           env=env, capture_output=True, text=True).stdout.split()
    if which != [os.path.join(shim, "curl"), os.path.join(shim, "pgrep")]:
        print("  refusing to run: curl/pgrep do not resolve to the shims: %r" % which)
        sys.exit(1)
    p = subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True,
                       timeout=120, cwd=root)
    with open(log) as f:
        reqs = [json.loads(ln) for ln in f if ln.strip()]
    with open(state) as f:
        final = json.load(f)["enabled"]
    return p.returncode, p.stdout + p.stderr, reqs, d, final, root


def posts_to_escalate(reqs):
    return [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/claude/escalate"]


print("\n1. a switch that cannot be read is never guessed")
for mode in ("down", "html", "nonbool", "http500"):
    rc, out, reqs, d, final, root = scenario("unread-" + mode, mode=mode, owner=False)
    posts = [r for r in reqs if r["method"] == "POST"]
    check("[%s] the script refuses (non-zero exit)" % mode, rc != 0, out)
    check("[%s] not one POST is sent — nothing changed, nothing to restore" % mode,
          not posts, json.dumps(posts)[:600] + "\n" + out)
    check("[%s] the switch is still the owner's (off)" % mode, final is False, out)
    check("[%s] it still prints a failing summary the runner can read" % mode,
          "TESTS 0 passed 1 failed" in out, out)
    shutil.rmtree(root, ignore_errors=True)

print("\n2. owner has escalation OFF: it is never switched on")
rc, out, reqs, d, final, root = scenario("owner-off", owner=False)
check("the run itself passes against a well-behaved dashboard", rc == 0 and "FAIL" not in out, out)
on = [r for r in reqs if r.get("state_after") is True]
check("the switch is off after EVERY request (probes send the owner's value)",
      not on, json.dumps(on)[:600])
check("it ends off", final is False, out)
check("claude/think was called once, with the switch off",
      [r.get("think_while_enabled") for r in reqs if r["path"] == "/api/claude/think"] == [False],
      json.dumps(reqs)[:800])
check("the EXIT trap restored false", posts_to_escalate(reqs)[-1]["data"] == '{"enabled":false}',
      json.dumps(posts_to_escalate(reqs)[-1:]))
shutil.rmtree(root, ignore_errors=True)

print("\n3. owner has escalation ON: it ends on, think only sees it off")
rc, out, reqs, d, final, root = scenario("owner-on", owner=True)
check("the run passes", rc == 0 and "FAIL" not in out, out)
check("it ends on (the owner's value)", final is True, out)
check("claude/think never ran while the switch was on",
      all(r.get("think_while_enabled") is False for r in reqs if r["path"] == "/api/claude/think")
      and any(r["path"] == "/api/claude/think" for r in reqs), json.dumps(reqs)[:800])
shutil.rmtree(root, ignore_errors=True)

print("\n4. a disable that does not take never reaches Claude")
rc, out, reqs, d, final, root = scenario("stuck", mode="stuck", owner=True)
check("claude/think was NOT called", not any(r["path"] == "/api/claude/think" for r in reqs),
      json.dumps([r for r in reqs if r["path"] == "/api/claude/think"]))
check("the run fails (the disable did not work)", rc != 0, out)
check("it ends on the owner's value (on)", final is True, out)
shutil.rmtree(root, ignore_errors=True)

print("\n5. markers: the owner's pause survives, an idle marker is put back")
rc, out, reqs, d, final, root = scenario("paused", owner=False, markers={"agent-paused": ""})
check("the pause probe ran", any(r["path"] == "/api/agent/pause" for r in reqs), json.dumps(reqs)[-400:])
check("the owner's agent-paused marker is still there", os.path.exists(os.path.join(d, "agent-paused")), out)
check("the script's own marker check passes", "FAIL marker restore" not in out, out)
shutil.rmtree(root, ignore_errors=True)
rc, out, reqs, d, final, root = scenario("idle", owner=False, markers={"agent-idle-suspended": "1760000000"})
check("our pause marker is removed", not os.path.exists(os.path.join(d, "agent-paused")), out)
idle = os.path.join(d, "agent-idle-suspended")
check("the idle marker is back, byte for byte",
      os.path.exists(idle) and open(idle).read() == "1760000000", out)
shutil.rmtree(root, ignore_errors=True)
rc, out, reqs, d, final, root = scenario("nomarker", owner=False)
check("no marker before -> none after",
      not os.path.exists(os.path.join(d, "agent-paused"))
      and not os.path.exists(os.path.join(d, "agent-idle-suspended")), out)
shutil.rmtree(root, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
