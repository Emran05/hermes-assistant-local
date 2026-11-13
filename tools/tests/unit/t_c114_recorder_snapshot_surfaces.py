#!/usr/bin/env python3
"""C114 (audit 2026-10-21): the Flight Recorder says WHY a dashboard or
Telegram write has no snapshot, instead of a green "snapshots on" that only
ever applied to the terminal CLI.

Upstream reads config.yaml's `checkpoints.enabled` in the classic CLI alone.
`hermes serve` (every dashboard chat) builds agents with
checkpoints_enabled = $HERMES_TUI_CHECKPOINTS, from its LaunchAgent
environment, and the messaging gateway (Telegram) never passes the flag. The
recorder keyed its banner on the config key, so with the key on it showed no
warning while no dashboard/Telegram write could ever be undone, and the banner's
remedy changed nothing for those surfaces.

Exec's the REAL aux_recorder.py (throwaway HOME with a fake serve LaunchAgent
and config.yaml), reads /api/recorder and /api/undo through the real handlers,
then feeds that payload to the REAL aux_recorder.js in node. Checks:
  1. the payload reports per-surface snapshot ability (serve from the plist,
     cli from the config key, per-source coverage, the gateway sources seen);
  2. a hub row and a Telegram row each carry their own reason, and the Undo
     refusal repeats it;
  3. the banner names the dashboard (with the env var and the installer) and
     Telegram, and drops the dashboard line once the plist sets the variable;
  4. install-services.sh writes HERMES_TUI_CHECKPOINTS=1 into the serve plist.

Run:  python3 t_c114_recorder_snapshot_surfaces.py
"""
import io
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return os.environ.get("HERMES_REPO") or os.path.dirname(
        os.path.dirname(os.path.dirname(_HERE)))


REPO = _find_repo()
AUX = os.path.join(REPO, "dashboard", "aux_recorder.py")
AUXJS = os.path.join(REPO, "dashboard", "aux_recorder.js")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


class Ctx:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body or {}

    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_c114", "HOME": home,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(home, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),        # no caching under test
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def write_plist(home, env):
    d = os.path.join(home, "Library", "LaunchAgents")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "com.hermes.serve.plist"), "wb") as f:
        plistlib.dump({"Label": "com.hermes.serve",
                       "EnvironmentVariables": env}, f)


HARNESS = r"""
const fs = require("fs");
// esc() is the REAL one, cut out of index.html's inline script (E138: a stand-in
// tests a different escaping from the one the dashboard ships)
globalThis.esc = require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
globalThis.relTime = () => "just now";
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
for (const [k, d] of Object.entries(cases)) {
  try {
    out[k] = {banner: typeof recBannerLines === "function" ? recBannerLines(d) : null,
              rows: renderRecorderRows(d.actions || [])};
  } catch (e) { out[k] = {error: String(e)}; }
}
process.stdout.write(JSON.stringify(out));
"""


HOME = tempfile.mkdtemp(prefix="hermes-c114-")
try:
    os.makedirs(os.path.join(HOME, ".hermes"), exist_ok=True)
    with open(os.path.join(HOME, ".hermes", "config.yaml"), "w") as f:
        f.write("model:\n  default: x\ncheckpoints:\n  enabled: true\n")
    write_plist(HOME, {"PATH": "/usr/bin", "HOME": HOME,
                       "HERMES_DASHBOARD_SESSION_TOKEN": "t"})
    G = build(HOME)
    con = sqlite3.connect(G["REC_DB"])
    for tcid, src, snap in (("h1", "hub", ""), ("t1", "telegram", ""),
                            ("c1", "cli", json.dumps({"workdir": "/w", "commit": "c"}))):
        con.execute(
            "INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
            " reversible, status, snapshot_ref, undo_note, origin)"
            " VALUES(?, 1, 's', ?, 'write_file', '/tmp/x', 'write', ?, 'done', ?, ?, 'statedb')",
            (tcid, src, "yes" if snap else "no", snap,
             "" if snap else "no snapshot captured"))
    con.commit()
    ids = dict(con.execute("SELECT tool_call_id, id FROM actions"))
    con.close()

    print("\n== the payload says what each surface can snapshot ==")
    off = G["recorder_api_handler"](Ctx())
    snaps = off.get("snapshots") or {}
    check("serve read from its LaunchAgent: off", snaps.get("serve") is False, snaps)
    check("cli from config.yaml: on", snaps.get("cli") is True, snaps)
    by = snaps.get("by_source") or {}
    check("per-source coverage counted",
          by.get("hub") == {"rows": 1, "with_snapshot": 0}
          and by.get("cli") == {"rows": 1, "with_snapshot": 1}, by)
    check("Telegram listed as a gateway source", snaps.get("gateway") == ["telegram"],
          snaps.get("gateway"))
    rows = {a["source"]: a for a in off.get("actions", [])}
    check("the hub row names the serve environment variable",
          "HERMES_TUI_CHECKPOINTS" in (rows.get("hub", {}).get("no_snapshot_why") or ""),
          rows.get("hub"))
    check("the Telegram row says Telegram turns are never snapshotted",
          (rows.get("telegram", {}).get("no_snapshot_why") or "").startswith(
              "Telegram turns are never snapshotted"), rows.get("telegram"))
    check("a row with a snapshot carries no reason",
          "no_snapshot_why" not in rows.get("cli", {}), rows.get("cli"))
    und = G["recorder_undo_handler"](Ctx(body={"id": ids["t1"]}))
    check("the Undo refusal repeats the reason",
          und.get("error") == "irreversible" and "Telegram" in (und.get("detail") or ""),
          und)

    write_plist(HOME, {"PATH": "/usr/bin", "HERMES_TUI_CHECKPOINTS": "1"})
    on = G["recorder_api_handler"](Ctx())
    check("with the variable in the plist, serve reads on",
          (on.get("snapshots") or {}).get("serve") is True, on.get("snapshots"))

    print("\n== the banner (real aux_recorder.js in node) ==")
    tmp = tempfile.mkdtemp(prefix="hermes-c114-js-")
    hp, cp = os.path.join(tmp, "h.js"), os.path.join(tmp, "c.json")
    io.open(hp, "w").write(HARNESS)
    io.open(cp, "w").write(json.dumps({"off": off, "on": on,
                                       "legacy": {"checkpoints_enabled": False,
                                                  "actions": []}}))
    p = subprocess.run(["node", hp, AUXJS, cp], capture_output=True, text=True,
                       timeout=60)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        js = json.loads(p.stdout)
    except Exception:
        js = {}
        check("node harness ran", False, p.stderr[-400:])
    b_off = " ".join((js.get("off") or {}).get("banner") or [])
    b_on = " ".join((js.get("on") or {}).get("banner") or [])
    check("banner: dashboard chats are not snapshotted, with the env var + installer",
          "Dashboard chats are not snapshotted" in b_off
          and "HERMES_TUI_CHECKPOINTS=1" in b_off and "install-services.sh" in b_off,
          b_off)
    check("banner: Undo is unavailable for Telegram turns",
          "Undo is unavailable for Telegram turns" in b_off, b_off)
    check("with the config key on, no CLI line", "Terminal sessions" not in b_off, b_off)
    check("once serve has the variable the dashboard line goes",
          "Dashboard chats" not in b_on and "Telegram" in b_on, b_on)
    check("an older payload still gets the old single line",
          " ".join((js.get("legacy") or {}).get("banner") or []).startswith(
              "Snapshots are off"), js.get("legacy"))
    check("the irreversible chip carries the reason as its tooltip",
          'title="Telegram turns are never snapshotted' in ((js.get("off") or {}).get("rows") or ""))

    print("\n== the installer sets it ==")
    inst = io.open(os.path.join(REPO, "install-services.sh"), encoding="utf-8").read()
    seg = inst[inst.find('cat > "$SERVE_PLIST"'):]
    seg = seg[:seg.find("\nEOF")]
    check("install-services.sh writes HERMES_TUI_CHECKPOINTS=1 into the serve plist",
          "<key>HERMES_TUI_CHECKPOINTS</key><string>1</string>" in seg)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
