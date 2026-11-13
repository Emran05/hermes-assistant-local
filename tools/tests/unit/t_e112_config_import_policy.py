#!/usr/bin/env python3
"""E112 (audit 2026-11-09): a config restore no longer signs the imported
permissions policy, no longer drops the owner's entries, and the dry-run plan
names every tier it changes.

Before: snapshot_apply() called permissions._save() — REPLACE + SIGN in one
step — so an imported Auto was live at once (the sidecar review an
out-of-band edit gets never ran), every Never the owner set after the export
vanished, and the plan the owner approved said only "policy differs".

Execs the REAL dashboard/aux_config.py (as server.py's aux loader does) with
the REAL permissions.py and server.py's real read_json/write_json/
settings_update against a throwaway HOME, then renders the plan with the REAL
aux_config.js cfgPlanRows() and index.html's real esc() in node.

Run:  python3 t_e112_config_import_policy.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %r" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-e112-")
os.environ["HOME"] = HOME
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA, exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "dashboard"))
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import permissions as P                    # noqa: E402
import server_real                          # noqa: E402

FORCE = "git force push (rewrites remote history)"
PKILL = "force kill processes"


def load_cfg():
    lock = threading.Lock()
    g = {"__name__": "aux_config_e112", "HOME": HOME, "DATA": DATA,
         "HERE": os.path.join(REPO, "dashboard"),
         "HERMES": os.path.join(HOME, "bin", "hermes"), "_hermes_env": lambda: {},
         "get_layout": lambda: {"order": []}, "save_layout": lambda o: None,
         "SETTINGS_FILE": os.path.join(DATA, "settings.json"),
         "LAYOUT_FILE": os.path.join(DATA, "layout.json"),
         "MODELS_FILE": os.path.join(DATA, "models.json"), "WIDGETS": {},
         "_model_registry": lambda: [], "active_model": lambda: "",
         "switch_model": lambda m: {"ok": False}, "_model_downloaded": lambda m: False,
         "_widget_cache": {},
         "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    g.update(server_real.settings_api(g["SETTINGS_FILE"], lock))
    src = open(os.path.join(REPO, "dashboard", "aux_config.py"), encoding="utf-8").read()
    exec(compile(src, "aux_config.py", "exec"), g)
    return g


def set_policy(classes, patterns=None):
    assert P.permissions_set({"op": "reset"}).get("ok")
    for c, t in classes.items():
        assert P.permissions_set({"op": "set_class", "class": c, "tier": t}).get("ok")
    for k, t in (patterns or {}).items():
        assert P.permissions_set({"op": "set_pattern", "pattern": k, "tier": t}).get("ok")


def snap(classes, patterns=None):
    return {"schema": 1, "kind": "hermes-state-snapshot",
            "permissions": {"classes": classes, "patterns": patterns or {}}}


try:
    G = load_cfg()
    post = G["snapshot_post"]

    # The owner, AFTER exporting: process-control Never, a force-push Never,
    # git-destructive Ask (explicit).
    set_policy({"process-control": "never", "git-destructive": "ask"},
               {FORCE: "never"})
    check("starting policy is trusted", P._load()[1] is True)
    old_sidecar = open(P.SIDECAR).read()

    # A snapshot (old export, shared one, or an edited file) that loosens:
    S = snap({"git-destructive": "auto", "file-perms": "auto",
              "credential-write": "auto"},
             {"git reset --hard (destroys uncommitted changes)": "auto"})
    r = post("/api/config/import", {"dry_run": True, "snapshot": S,
                                    "sections": ["permissions"]})
    plan = (r.get("plan") or {}).get("permissions") or {}
    rows = {x["id"]: x for x in plan.get("rows") or []}
    check("the plan lists per-class from→to rows",
          rows.get("git-destructive", {}).get("from") == "ask"
          and rows.get("git-destructive", {}).get("to") == "auto", plan)
    check("…and flags the loosened ones",
          rows.get("git-destructive", {}).get("loosened") is True
          and rows.get("file-perms", {}).get("loosened") is True, rows)
    check("…and per-pattern rows",
          rows.get("git reset --hard (destroys uncommitted changes)", {}).get("to") == "auto", rows)
    check("the owner's Nevers the snapshot does not name are not in the plan as changes",
          "process-control" not in rows and FORCE not in rows, sorted(rows))
    check("a floor-forbidden Auto is ignored and said so",
          "credential-write" not in rows
          and any("credential-write" in w for w in plan.get("ignored") or []), plan)
    check("the plan says the policy lands untrusted", plan.get("lands_untrusted") is True, plan)
    check("dry run wrote nothing", open(P.SIDECAR).read() == old_sidecar
          and P._load()[1] is True)

    # --- apply -------------------------------------------------------------
    r = post("/api/config/import", {"dry_run": False, "snapshot": S,
                                    "sections": ["permissions"]})
    check("apply ok", r.get("ok") is True and r.get("applied", {}).get("permissions") is True, r)
    pol, trusted, _e = P._load()
    check("THE FIX: the imported policy is NOT signed (untrusted)", trusted is False, trusted)
    check("the sidecar still holds the last signed policy",
          open(P.SIDECAR).read() == old_sidecar)
    check("the owner's Never entries survive (merge, not replace)",
          pol["classes"].get("process-control") == "never"
          and pol["patterns"].get(FORCE) == "never", pol)
    check("the floor-forbidden Auto was not written",
          "credential-write" not in pol["classes"], pol)
    v = P.decide({"pattern_key": "world/other-writable permissions",
                  "command": "chmod 777 out.txt"})
    check("an imported Auto is suspended (Ask) until re-trust", v["tier"] == "ask", v)
    v = P.decide({"pattern_key": PKILL, "command": "pkill -9 node"})
    check("the owner's process-control Never still auto-denies", v["tier"] == "never", v)
    check("the apply warns that Auto is suspended",
          any("UNTRUSTED" in w for w in r.get("warnings") or []), r.get("warnings"))
    pay = P.permissions_payload()
    check("the Trust panel offers the review (untrusted, with the signed diff)",
          pay["trusted"] is False and isinstance(pay.get("signed"), dict), pay.get("trusted"))
    check("the signed diff base is the pre-import policy",
          pay["signed"]["classes"]["git-destructive"] == "ask", pay.get("signed"))
    check("the import is in the audit log",
          any(e.get("op") == "import" for e in P._tail_entries(50)))

    # After re-trust the imported Auto works.
    P.permissions_set({"op": "retrust", "sha": P.permissions_payload().get("sha")})
    v = P.decide({"pattern_key": "world/other-writable permissions",
                  "command": "chmod 777 out.txt"})
    check("after Review & re-trust the imported Auto applies", v["tier"] == "auto", v)

    # An import that changes nothing writes nothing and keeps trust.
    before = open(P.PERM_FILE, "rb").read()
    r = post("/api/config/import", {"dry_run": False, "snapshot": snap({"file-perms": "auto"}),
                                    "sections": ["permissions"]})
    check("a no-op import leaves the file and the trust alone",
          open(P.PERM_FILE, "rb").read() == before and P._load()[1] is True
          and r.get("applied", {}).get("permissions") == "unchanged", r)
    st = G["_cfg_drift"](G["snapshot_build"](), {"permissions": {"classes": {"file-perms": "auto"},
                                                                 "patterns": {}}})
    check("drift: a snapshot whose entries all hold is in sync", st["permissions"] == "in_sync", st)

    # A tightening import: not loosened, still lands untrusted (it is unsigned)
    r = post("/api/config/import", {"dry_run": True, "snapshot": snap({"file-perms": "never"}),
                                    "sections": ["permissions"]})
    rr = r["plan"]["permissions"]["rows"]
    check("a tightening row is not flagged loosened",
          rr and rr[0]["id"] == "file-perms" and rr[0]["loosened"] is False, rr)

    # A pattern a live Never covers via its class: from is the effective tier
    set_policy({"git-destructive": "never"})
    r = post("/api/config/import", {"dry_run": True,
                                    "snapshot": snap({}, {FORCE: "auto"}),
                                    "sections": ["permissions"]})
    rr = {x["id"]: x for x in r["plan"]["permissions"]["rows"]}
    check("a pattern Auto over a class Never shows Never → Auto, loosened",
          rr.get(FORCE, {}).get("from") == "never" and rr[FORCE]["to"] == "auto"
          and rr[FORCE]["loosened"] is True, rr)

    # --- the UI renders the rows ------------------------------------------
    plan_js = {"permissions": {"changed": True, "lands_untrusted": True,
                               "ignored": ["credential-write: Auto is below its safety floor"],
                               "rows": [{"kind": "class", "id": "git-destructive",
                                         "label": "Git <history>", "from": "ask", "to": "auto",
                                         "loosened": True},
                                        {"kind": "pattern", "id": "x", "label": "x",
                                         "from": "ask", "to": "never", "loosened": False}]}}
    js = r"""
const { indexFunction } = require(process.argv[1]);
globalThis.esc = indexFunction('esc');
globalThis.window = globalThis;
require(process.argv[2]);
process.stdout.write(globalThis.cfgPlanRows(JSON.parse(process.argv[3])));
"""
    out = subprocess.run(["node", "-e", js, os.path.join(REPO, "tools", "tests", "lib", "index_helpers.js"),
                          os.path.join(REPO, "dashboard", "aux_config.js"), json.dumps(plan_js)],
                         capture_output=True, text=True, timeout=60)
    html = out.stdout
    check("UI: renders a row per change (not 'policy differs')",
          "policy differs" not in html and "Git &lt;history&gt;" in html, html or out.stderr)
    check("UI: the label is escaped", "<history>" not in html, html)
    check("UI: from → to shown as tier names", "Ask</span> → " in html and "Auto</span>" in html, html)
    check("UI: the loosened row is highlighted", html.count("cf-loose") >= 1
          and "less strict" in html and html.count("less strict") == 1, html)
    check("UI: ignored entries and the untrusted note are shown",
          "credential-write" in html and "untrusted" in html, html)
    plan_old = {"permissions": {"changed": True}}
    out = subprocess.run(["node", "-e", js, os.path.join(REPO, "tools", "tests", "lib", "index_helpers.js"),
                          os.path.join(REPO, "dashboard", "aux_config.js"), json.dumps(plan_old)],
                         capture_output=True, text=True, timeout=60)
    check("UI: an older server's plan still renders", "policy differs" in out.stdout, out.stdout)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
