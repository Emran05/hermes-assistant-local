#!/usr/bin/env python3
"""1.1.3 "trust made visible" harness — download estimate + disk refusal.

Exec-loads dashboard/server.py into a throwaway HOME so nothing touches the
live ~/.hermes, nothing starts a download and nothing wakes the model
(download_model's threaded body is never reached on the refusal path, and on
the allow path _hf_python is stubbed to None so it returns before spawning).

Covers:
  1  _dir_size_gb follows HF's blob symlinks (a snapshot of links reports the
     blob bytes, not a few hundred bytes of link)
  2  _model_download_gb measures a COMPLETE fake snapshot
  3  _model_download_gb falls back to the onboarding catalog when not local,
     and adds a separate-repo drafter's size
  4  a PARTIAL snapshot is not measured (would understate the pull)
  5  _disk_short at the exact boundary (>=5 GB free after == allowed)
  6  download_model() refuses server-side with {"ok": false,
     "error": "not enough free disk"} and allows at the boundary
  7  models_payload() carries download_gb / disk_free_gb / disk_headroom_gb /
     ctx, and `fit` is byte-identical to 1.1.2 for every roster row
"""
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRV = os.path.join(REPO, "dashboard", "server.py")

FAILS = []
CHECKS = [0]


def check(name, got, want):
    CHECKS[0] += 1
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got={got!r}\n      want={want!r}")
    if not ok:
        FAILS.append(name)


def ok(name, cond, detail=""):
    CHECKS[0] += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- fake HOME
HOME = tempfile.mkdtemp(prefix="hermes-net-harness-")
os.environ["HOME"] = HOME
os.makedirs(os.path.join(HOME, ".hermes", "dashboard"), exist_ok=True)

ns = {"__name__": "server_harness", "__file__": SRV}
with open(SRV) as f:
    exec(compile(f.read(), SRV, "exec"), ns)
print(f"exec-loaded server.py on HOME={HOME}\n")
assert ns["HOME"] == HOME, ns["HOME"]


# ------------------------------------------------- fake HF snapshot builders
def blobify(mid, files, complete=True, sha="deadbeef"):
    """Build a hub-cache entry the way huggingface_hub does: real bytes in
    blobs/, symlinks in snapshots/<sha>/, and refs/main naming the sha."""
    base = os.path.join(HOME, ".cache", "huggingface", "hub",
                        "models--" + mid.replace("/", "--"))
    blobs, snap = os.path.join(base, "blobs"), os.path.join(base, "snapshots", sha)
    os.makedirs(blobs, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    os.makedirs(os.path.join(base, "refs"), exist_ok=True)
    with open(os.path.join(base, "refs", "main"), "w") as f:
        f.write(sha)
    names = list(files)
    for i, (name, nbytes) in enumerate(files.items()):
        b = os.path.join(blobs, f"blob{i}")
        with open(b, "wb") as f:
            f.write(b"\0" * nbytes)
        if not complete and name == names[-1]:
            continue                       # last shard never materialized
        os.symlink(b, os.path.join(snap, name))
    shards = [n for n in names if n.endswith(".safetensors")]
    if len(shards) > 1:
        with open(os.path.join(snap, "model.safetensors.index.json"), "w") as f:
            json.dump({"weight_map": {f"w{i}": n for i, n in enumerate(shards)}}, f)
    return snap


GB = 1024 ** 3
BIG = "harness/Big-4bit"
SMALL = "harness/Small-4bit"
DRAFT = "harness/Big-MTP-bf16"
CATONLY = "harness/CatalogOnly-4bit"

snap_big = blobify(BIG, {"a.safetensors": 3 * GB, "b.safetensors": 2 * GB,
                         "config.json": 1024})
snap_draft = blobify(DRAFT, {"d.safetensors": GB // 2})
blobify(SMALL, {"a.safetensors": 2 * GB, "b.safetensors": GB}, complete=False)

# the onboarding catalog, resolved BY NAME at call time by _catalog_dl_gb
ns["_ONB_BY_ID"] = {
    CATONLY: {"id": CATONLY, "size_gb": 16.1, "draft_size_gb": 0.9},
    SMALL: {"id": SMALL, "size_gb": 6.0},
}

# a roster the harness owns outright
ROSTER = [
    {"id": BIG, "label": "Big", "ram": 19, "ctx": 262144,
     "draft_model": DRAFT, "draft_kind": "mtp", "draft_block_size": 3,
     "backend": "mlx_vlm", "thinking": True},
    {"id": SMALL, "label": "Small", "ram": 7, "ctx": 262144, "backend": "mlx_vlm"},
    {"id": CATONLY, "label": "CatalogOnly", "ram": 19, "ctx": 262144,
     "backend": "mlx_vlm"},
]
ns["_model_registry"] = lambda: [dict(m) for m in ROSTER]
ns["_model_entry"] = lambda mid: next((dict(m) for m in ROSTER if m["id"] == mid), None)

print("=== 1. _dir_size_gb follows HF blob symlinks ===")
check("_dir_size_gb(big snapshot) == 5.0 GB", ns["_dir_size_gb"](snap_big), 5.0)
check("_dir_size_gb(drafter snapshot) == 0.5 GB", ns["_dir_size_gb"](snap_draft), 0.5)
ok("symlinks really are links (lstat << stat)",
   os.lstat(os.path.join(snap_big, "a.safetensors")).st_size < 4096,
   f"lstat={os.lstat(os.path.join(snap_big, 'a.safetensors')).st_size}B")

print("\n=== 2/3/4. _model_download_gb ===")
ns["_widget_cache"].clear()
check("complete repo + separate drafter measured on disk (5.0 + 0.5)",
      ns["_model_download_gb"](ROSTER[0]), 5.5)
ns["_widget_cache"].clear()
check("PARTIAL snapshot is not measured -> catalog 6.0",
      ns["_model_download_gb"](ROSTER[1]), 6.0)
ns["_widget_cache"].clear()
check("absent from disk -> catalog repo+drafter 16.1+0.9",
      ns["_model_download_gb"](ROSTER[2]), 17.0)
ns["_widget_cache"].clear()
ok("unknown id, no catalog entry -> None",
   ns["_model_download_gb"]({"id": "harness/Nope"}) is None)
ok("_model_downloaded agrees: BIG complete, SMALL partial",
   ns["_model_downloaded"](BIG) and not ns["_model_downloaded"](SMALL))

print("\n=== 5. _disk_short boundary (headroom 5.0 GB) ===")
check("headroom constant", ns["DISK_HEADROOM_GB"], 5.0)
check("17 GB pull, 22.0 GB free -> exactly 5.0 left -> allowed",
      ns["_disk_short"](17.0, 22.0), False)
check("17 GB pull, 21.9 GB free -> 4.9 left -> short",
      ns["_disk_short"](17.0, 21.9), True)
check("17 GB pull, 412 GB free -> allowed", ns["_disk_short"](17.0, 412.0), False)
check("unknown download size -> never refuse", ns["_disk_short"](None, 1.0), False)
check("unknown free space -> never refuse", ns["_disk_short"](17.0, None), False)

print("\n=== 6. download_model() server-side refusal ===")
ns["_hf_python"] = lambda: None          # nothing can spawn even if we got past
ns["_model_dl"].clear()
ns["_model_dl_err"].clear()

ns["_disk_free_gb"] = lambda path=None: 21.9        # 4.9 GB would be left
ns["_widget_cache"].clear()
r = ns["download_model"](CATONLY)
check("refused below the boundary", (r.get("ok"), r.get("error")),
      (False, "not enough free disk"))
check("refusal quotes the numbers", (r.get("download_gb"), r.get("disk_free_gb"),
                                     r.get("disk_headroom_gb")), (17.0, 21.9, 5.0))
ok("refusal started no download thread", ns["_model_dl"].get(CATONLY) is None,
   f"_model_dl={dict(ns['_model_dl'])}")

ns["_disk_free_gb"] = lambda path=None: 22.0        # exactly 5.0 GB left
ns["_widget_cache"].clear()
r = ns["download_model"](CATONLY)
check("allowed AT the boundary (falls through to the interpreter check)",
      (r.get("ok"), r.get("error")),
      (False, "no interpreter with huggingface_hub (venv missing?)"))
ok("still no download thread (stubbed _hf_python)",
   ns["_model_dl"].get(CATONLY) == "error")

ns["_model_dl"].clear()
ns["_model_dl_err"].clear()
r = ns["download_model"]("harness/NotOnTheRoster")
check("unknown id still refused first", (r.get("ok"), r.get("error")),
      (False, "unknown model"))

print("\n=== 7. models_payload() shape + fit unchanged ===")
ns["_disk_free_gb"] = lambda path=None: 412.0
ns["_widget_cache"].clear()
ns["active_model"] = lambda: BIG
ns["_machine_ram_gb"] = lambda: 64.0
ns["model_thinking_state"] = lambda mid: {"supported": True, "enabled": False}
ns["bg_model"] = lambda: SMALL
ns["bg_online"] = lambda: False
ns["agent_paused"] = lambda: False
ns["agent_idle_suspended"] = lambda: True      # keeps ram_gb None, no pgrep
ns["idle_suspend_enabled"] = lambda: True
ns["_idle_min"] = lambda: 10
ns["prewarm_payload"] = lambda: {"enabled": True}

p = ns["models_payload"]()
by = {m["id"]: m for m in p["models"]}
check("disk_free_gb on the payload", p.get("disk_free_gb"), 412.0)
check("disk_headroom_gb on the payload", p.get("disk_headroom_gb"), 5.0)
check("BIG download_gb", by[BIG].get("download_gb"), 5.5)
check("CATONLY download_gb", by[CATONLY].get("download_gb"), 17.0)
check("ctx rides through from the roster", by[BIG].get("ctx"), 262144)
ok("every row has a download_gb key",
   all("download_gb" in m for m in p["models"]))
# fit is the 1.0.3 contract, byte-identical: 19/64 -> ok, 7/64 -> ok
check("fit unchanged (19 GB on 64 GB)", by[BIG].get("fit"), "ok")
check("fit unchanged (7 GB on 64 GB)", by[SMALL].get("fit"), "ok")
check("fit unchanged (_model_fit 19 on 22 GB Mac)", ns["_model_fit"](19, 22), "no")
check("fit unchanged (_model_fit 19 on 30 GB Mac)", ns["_model_fit"](19, 30), "tight")
check("fit unchanged (_model_fit 19 on 64 GB Mac)", ns["_model_fit"](19, 64), "ok")
check("fit unchanged (no ram -> None)", ns["_model_fit"](None, 64), None)
check("mem.machine_gb still there", p["mem"].get("machine_gb"), 64)

print("\n=== real ~ statvfs sanity (read-only) ===")
free = ns["_disk_free_gb"].__wrapped__ if hasattr(ns["_disk_free_gb"], "__wrapped__") else None
# re-exec just the helper against the real HOME to prove statvfs works
import os as _os
st = _os.statvfs(_os.path.expanduser("~"))
real = round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
ok("statvfs(~) returns a plausible free-GB number", real > 0, f"{real} GB free")


import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (CHECKS[0] - len(FAILS), len(FAILS))))

shutil.rmtree(HOME, ignore_errors=True)
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
