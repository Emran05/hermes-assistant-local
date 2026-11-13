#!/usr/bin/env python3
"""doctor.py judges a roster row's `model_subfolder`, not the snapshot root.

Round 3, a-roster merge note (3): PocketAiHub/Qwen3.8-27B-Abliterated-MLX keeps
its weights in 4bit/, so a doctor that only looked at the snapshot root reported
a fully downloaded model as PARTIAL, and as FAIL when it was the active model.
Runs the shipped doctor.py against a throwaway HOME with a fake hub cache; no
dashboard, no network, nothing under the real ~/.hermes.
"""
import importlib.util, json, os, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-r3-doctor-sub-")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["DASH_PORT"] = "9"
os.makedirs(os.path.join(os.environ["HOME"], ".hermes", "dashboard"))
spec = importlib.util.spec_from_file_location("doctor_r3sub", os.path.join(REPO, "dashboard", "doctor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def snapshot(sub):
    snap = tempfile.mkdtemp(dir=TMP)
    w = os.path.join(snap, sub) if sub else snap
    os.makedirs(w, exist_ok=True)
    json.dump({"weight_map": {"a": "model-1.safetensors"}},
              open(os.path.join(w, "model.safetensors.index.json"), "w"))
    open(os.path.join(w, "model-1.safetensors"), "wb").close()
    return snap


print("doctor.py: model_subfolder")
check("a clean subfolder is kept", d._subfolder("4bit") == "4bit" and d._subfolder("/4bit/") == "4bit")
check("absent is the snapshot root", d._subfolder(None) == "" and d._subfolder("") == "")
for bad in ("..", "../x", "a/../b", "/", "a\\b", 7, "x" * 65, "a/b/c/d/e"):
    check("an invalid subfolder is refused, never joined: %r" % (bad,), d._subfolder(bad) is None)
snap = snapshot("4bit")
check("weights in 4bit/ are complete for a row naming 4bit", d._snapshot_complete(snap, "4bit"))
check("...and incomplete for a row naming the root (the old false PARTIAL)", not d._snapshot_complete(snap, ""))
check("an invalid subfolder is never complete", not d._snapshot_complete(snap, None))
check("no snapshot is never complete", not d._snapshot_complete(None, ""))
root = snapshot("")
check("a root snapshot still reads complete", d._snapshot_complete(root, ""))
check("_draft_state refuses an invalid draft_subfolder",
      d._draft_state({"id": "x/y", "draft_subfolder": "../mtp"}) == "missing")

print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
