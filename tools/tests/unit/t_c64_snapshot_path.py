#!/usr/bin/env python3
"""C64 — the config snapshot is written under ~/.hermes/dashboard, not into
the install tree.

POST /api/config/export used to write <install dir>/docs/state-snapshot.json:
anchored to the CODE, so a tarball update's `rsync --delete` deleted the
owner's export (the file is export-ignored), on a git checkout every Export
dirtied a tracked file and the updater refused to run, and any harness that
exercised Export with a scratch HOME still overwrote the repo's copy.  It now
writes DATA/state-snapshot.json, and the old path is only READ — by status and
a file import, while no new export exists — so an export made before the move
can still be restored.

The REAL dashboard/aux_config.py is exec'd with stub server globals, HOME in a
temp dir and HERE pointed at a fake install tree, so "the install tree" is a
directory this test can watch.  No dashboard, no network, nothing under the
real ~/.hermes or the real checkout.
"""
import io, json, os, shutil, sys, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = os.path.join(REPO, "dashboard", "aux_config.py")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def load(home, install):
    """aux_config.py exec'd the way server.py's aux loader does it."""
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    os.makedirs(os.path.join(install, "dashboard"), exist_ok=True)
    routes = {}
    g = {
        "__name__": "aux_config_c64", "HOME": home, "HERE": os.path.join(install, "dashboard"),
        "DATA": data, "read_json": read_json, "write_json": lambda p, o: None,
        "get_layout": lambda: {"order": ["clock"]}, "save_layout": lambda o: None,
        "get_settings": lambda: {"tickers": ["AAPL"]}, "settings_update": lambda fn: None,
        "SETTINGS_FILE": os.path.join(data, "settings.json"),
        "LAYOUT_FILE": os.path.join(data, "layout.json"),
        "MODELS_FILE": os.path.join(data, "models.json"),
        "WIDGETS": {"clock": {}}, "_model_registry": lambda: [], "active_model": lambda: "",
        "switch_model": lambda m: {"ok": True}, "_model_downloaded": lambda m: False,
        "_state_lock": threading.Lock(), "_widget_cache": {}, "HERMES": "/nonexistent/hermes",
        "_hermes_env": lambda: {},
        "register_get": lambda path, fn: routes.__setitem__(("GET", path), fn),
        "register_post": lambda path, fn: routes.__setitem__(("POST", path), fn),
    }
    exec(compile(io.open(SRC, encoding="utf-8").read(), SRC, "exec"), g)
    return g, data


def tree(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in fn:
            p = os.path.join(dp, n)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


def main(tmp):
    home, install = os.path.join(tmp, "home"), os.path.join(tmp, "install")
    g, data = load(home, install)
    newp = os.path.join(data, "state-snapshot.json")
    legacy = os.path.join(install, "docs", "state-snapshot.json")

    print("\n1. Export writes under ~/.hermes/dashboard and leaves the install tree alone")
    check("CFG_SNAPSHOT_PATH is under DATA", os.path.dirname(g["CFG_SNAPSHOT_PATH"]) == data, g["CFG_SNAPSHOT_PATH"])
    before = tree(install)
    r = g["snapshot_post"]("/api/config/export", {"note": "c64"})
    check("export ok", isinstance(r, dict) and r.get("ok") is True, r)
    check("the file is at ~/.hermes/dashboard/state-snapshot.json", os.path.isfile(newp))
    check("nothing was written into the install tree", tree(install) == before,
          sorted(set(tree(install)) - set(before)))
    check("no docs/state-snapshot.json appeared", not os.path.exists(legacy))
    path = (r or {}).get("path", "")
    check("the reported path is the new one, with no absolute path in it",
          path == "~/.hermes/dashboard/state-snapshot.json" and home not in path, path)
    snap = read_json(newp)
    check("it is a real snapshot", isinstance(snap, dict) and snap.get("note") == "c64"
          and snap.get("dashboard", {}).get("settings", {}).get("tickers") == ["AAPL"], snap)
    st = g["snapshot_get"]({})
    check("status reads the new file", st.get("file", {}).get("exists") is True
          and st["file"].get("path") == "~/.hermes/dashboard/state-snapshot.json"
          and not st["file"].get("legacy"), st.get("file"))

    print("\n2. an export from before the move is still found and restorable")
    home2 = os.path.join(tmp, "home2")
    g2, data2 = load(home2, install)
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    shutil.copyfile(newp, legacy)
    lb = os.stat(legacy)
    st = g2["snapshot_get"]({})
    f = st.get("file", {})
    check("status falls back to docs/state-snapshot.json", f.get("exists") is True
          and f.get("path") == "docs/state-snapshot.json" and f.get("legacy") is True, f)
    check("and it validates", f.get("valid") is True, f)
    plan = g2["snapshot_post"]("/api/config/import", {"dry_run": True})
    check("a file import (dry run) reads it", isinstance(plan, dict) and plan.get("ok") is True
          and plan.get("dry_run") is True, plan)

    print("\n3. the next export goes to the new place, and the old file is never written")
    r = g2["snapshot_post"]("/api/config/export", {"note": "after the move"})
    check("export ok", isinstance(r, dict) and r.get("ok") is True, r)
    check("written under the new HOME's DATA", os.path.isfile(os.path.join(data2, "state-snapshot.json")))
    la = os.stat(legacy)
    check("the legacy file is untouched", (la.st_size, la.st_mtime_ns) == (lb.st_size, lb.st_mtime_ns))
    st = g2["snapshot_get"]({})
    check("status now reads the new file", st["file"].get("path") == "~/.hermes/dashboard/state-snapshot.json"
          and not st["file"].get("legacy"), st["file"])

    print("\n4. with neither file, it says so")
    home3 = os.path.join(tmp, "home3")
    g3, _ = load(home3, os.path.join(tmp, "install3"))
    st = g3["snapshot_get"]({})
    check("no snapshot -> exists false, the new path offered", st["file"].get("exists") is False
          and st["file"].get("path") == "~/.hermes/dashboard/state-snapshot.json", st["file"])
    r = g3["snapshot_post"]("/api/config/import", {"dry_run": True})
    check("import from file -> 'no snapshot file'", isinstance(r, tuple) and r[1] == 400
          and "no snapshot file" in r[0].get("error", ""), r)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="t-c64-")
    try:
        main(tmp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
