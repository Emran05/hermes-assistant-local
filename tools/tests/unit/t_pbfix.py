#!/usr/bin/env python3
"""Review fixes 1-4 for dashboard/aux_promptbudget.py (+ the shared lock in
hermes-plugins/plugin_enable.py).

aux_promptbudget.py is exercised the way server.py execs it: into a globals
dict that already carries HOME / HERMES / _hermes_env / _state_lock /
register_*. Everything runs against a sandbox HOME. No model, no probe, no
subprocess except the two deliberate concurrency workers, no real ~/.hermes.

Sections
  A  block-scoped parsing: comments, nesting, odd indents
  B  _pb_apply_text: the duplicate-`cli:` bug and comment survival
  C  _pb_write_toolsets: lock + unique temp file + the stderr change line
  D  TWO PROCESSES race the same config through both editors
  E  single-flight / coalescing of the subprocess-backed build
  F  a failed probe is reported, not swallowed
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_promptbudget.py")
PE = os.path.join(REPO, "hermes-plugins", "plugin_enable.py")

SB = tempfile.mkdtemp(prefix="pb-fix-")
os.makedirs(os.path.join(SB, ".hermes", "dashboard"), exist_ok=True)
CFG = os.path.join(SB, ".hermes", "config.yaml")

BASE_CFG = (
    "model:\n"
    "  default: x\n"
    "  context_length: 65536\n"
    "platform_toolsets:\n"
    "  cli:\n"
    "    - hermes-cli\n"
    "  telegram:\n"
    "    - web\n"
    "plugins:\n"
    "  enabled:\n"
    "    - loop-breaker\n"
)


def write_cfg(text=BASE_CFG):
    with open(CFG, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(CFG, 0o600)


write_cfg()

G = {
    "__name__": "aux_promptbudget",
    "HOME": SB,
    "HERMES": os.path.join(SB, "bin", "hermes"),
    "_hermes_env": lambda: dict(os.environ),
    "_state_lock": threading.Lock(),
    "register_get": lambda p, f: None,
    "register_post": lambda p, f: None,
}
exec(compile(open(AUX).read(), AUX, "exec"), G)

spec = importlib.util.spec_from_file_location("pe_test", PE)
PEM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PEM)

PASS, FAIL = [], []

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (len(PASS), len(FAIL))))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + repr(detail)) if detail and not cond else ""))


@contextlib.contextmanager
def capture_stdout():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old


parse = G["_pb_parse_toolsets"]
apply_text = G["_pb_apply_text"]

# ===========================================================================
print("\n=== A. block-scoped parsing (review fix 2) ===")
# ===========================================================================
check("plain block", parse(BASE_CFG) == {"cli": ["hermes-cli"], "telegram": ["web"]},
      parse(BASE_CFG))

WITH_COMMENT = (
    "platform_toolsets:\n"
    "# a note in column 0\n"
    "  cli:\n"
    "    - web\n"
    "    - terminal\n"
    "model:\n"
    "  default: x\n"
)
check("a column-0 comment does not end the block",
      parse(WITH_COMMENT) == {"cli": ["web", "terminal"]}, parse(WITH_COMMENT))

BLANKS = "platform_toolsets:\n\n  cli:\n\n    - web\n\nmodel:\n  default: x\n"
check("blank lines do not end the block", parse(BLANKS) == {"cli": ["web"]},
      parse(BLANKS))

DEEP = (
    "platform_toolsets:\n"
    "    cli:\n"
    "      - web\n"
    "    telegram:\n"
    "      - terminal\n"
)
check("a 4-space block is read at its own indent",
      parse(DEEP) == {"cli": ["web"], "telegram": ["terminal"]}, parse(DEEP))

INLINE = "platform_toolsets:\n  cli: something\n  telegram:\n    - web\n"
check("an inline scalar records the key but no list",
      parse(INLINE) == {"cli": [], "telegram": ["web"]}, parse(INLINE))

NESTED = (
    "platform_toolsets:\n"
    "  cli:\n"
    "    - web\n"
    "other:\n"
    "  platform_toolsets:\n"
    "    cli:\n"
    "      - nothing\n"
)
check("a nested platform_toolsets is not read as the top-level one",
      parse(NESTED) == {"cli": ["web"]}, parse(NESTED))
check("no block at all -> {}", parse("model:\n  default: x\n") == {})
check("an empty block -> {}", parse("platform_toolsets:\nmodel:\n  x: 1\n") == {})

# ===========================================================================
print("\n=== B. _pb_apply_text (review fix 2: no duplicate key) ===")
# ===========================================================================
out = apply_text(WITH_COMMENT, "cli", ["web"])
check("the comment case rewrites in place, no second cli:",
      out.count("cli:") == 1, out)
check("the comment survives", "# a note in column 0" in out, out)
check("re-parses to the new value", parse(out) == {"cli": ["web"]}, parse(out))
check("no stray keys after the rewrite", "model:\n  default: x\n" in out, out)

out = apply_text(BASE_CFG, "cli", ["web", "terminal"])
check("a plain rewrite keeps one cli:", out.count("  cli:\n") == 1, out)
check("telegram is untouched", parse(out)["telegram"] == ["web"], parse(out))
check("plugins block untouched", "    - loop-breaker\n" in out)
check("model block untouched", "  context_length: 65536\n" in out)

check("a no-op is byte-identical",
      apply_text(BASE_CFG, "cli", ["hermes-cli"]) == BASE_CFG)
check("removal drops the key",
      "cli" not in parse(apply_text(BASE_CFG, "cli", None)),
      parse(apply_text(BASE_CFG, "cli", None)))
check("removal keeps telegram",
      parse(apply_text(BASE_CFG, "cli", None)) == {"telegram": ["web"]})

TRAILING = (
    "platform_toolsets:\n"
    "  cli:\n"
    "    - hermes-cli\n"
    "# why cli and not tui: see aux_promptbudget.py\n"
    "model:\n"
    "  default: x\n"
)
out = apply_text(TRAILING, "cli", ["web"])
check("a trailing comment after the last item survives",
      "# why cli and not tui" in out, out)
check("and the rewrite is still single", out.count("cli:") == 1, out)

out = apply_text("model:\n  default: x\n", "cli", ["web"])
check("no block -> the block is created", parse(out) == {"cli": ["web"]}, out)
check("no block + None -> unchanged",
      apply_text("model:\n  default: x\n", "cli", None) == "model:\n  default: x\n")

DEEP_OUT = apply_text(DEEP, "cli", ["web", "todo"])
check("a 4-space block is rewritten at ITS indent",
      parse(DEEP_OUT) == {"cli": ["web", "todo"], "telegram": ["terminal"]},
      DEEP_OUT)
check("the 4-space rewrite really used 4 spaces", "    cli:\n" in DEEP_OUT, DEEP_OUT)

# ===========================================================================
print("\n=== C. _pb_write_toolsets: lock, temp file, log line ===")
# ===========================================================================
write_cfg()
before_sha = open(CFG, "rb").read()
with capture_stdout() as buf:
    changed, bak = G["_pb_write_toolsets"]("cli", ["hermes-cli"])
check("a no-op write changes nothing", changed is False and bak is None)
check("a no-op write leaves the bytes alone", open(CFG, "rb").read() == before_sha)
check("a no-op write logs nothing", buf.getvalue().strip() == "", buf.getvalue())

with capture_stdout() as buf:
    changed, bak = G["_pb_write_toolsets"]("cli", ["web", "terminal"])
line = buf.getvalue()
check("a real write reports changed", changed is True)
check("a real write makes a backup", bak and os.path.exists(bak), bak)
check("the backup is 0600", oct(os.stat(bak).st_mode & 0o777) == "0o600")
check("the change is logged to stderr/stdout (review fix 3)",
      "[aux_promptbudget] platform_toolsets.cli" in line, line)
check("the log line names before AND after",
      "hermes-cli -> web,terminal" in line, line)
check("config mode preserved", oct(os.stat(CFG).st_mode & 0o777) == "0o600")
check("the value landed", parse(open(CFG).read())["cli"] == ["web", "terminal"])

with capture_stdout() as buf:
    G["_pb_write_toolsets"]("cli", None)
check("a removal logs '(inherit)'", "-> (inherit)" in buf.getvalue(), buf.getvalue())

leftovers = [f for f in os.listdir(os.path.dirname(CFG)) if ".tmp" in f]
check("no temp file is left behind", leftovers == [], leftovers)
# The old bug was one FIXED temp name shared by every writer. Record the paths
# _pb_atomic_write actually replaces from and prove they are unique per call.
class _OsSpy:
    seen = []

    def __getattr__(self, name):
        return getattr(os, name)

    def replace(self, src, dst):
        _OsSpy.seen.append(src)
        return os.replace(src, dst)


real_os = G["_pb_os"]
G["_pb_os"] = _OsSpy()
try:
    G["_pb_write_toolsets"]("cli", ["web"])
    G["_pb_write_toolsets"]("cli", ["web", "todo"])
    G["_pb_write_toolsets"]("cli", ["web", "todo", "memory"])
finally:
    G["_pb_os"] = real_os
names = [os.path.basename(x) for x in _OsSpy.seen]
check("every write used its own temp file", len(set(names)) == 3, names)
check("no fixed '.promptbudget.tmp' name",
      not any(n.endswith(".promptbudget.tmp") for n in names), names)
check("the temp name carries the pid",
      all((".tmp-%d-" % os.getpid()) in n for n in names), names)
check("the temp file lived beside the config",
      all(os.path.dirname(x) == os.path.dirname(CFG) for x in _OsSpy.seen))

# the lock file is the cross-process contract: both editors must use one path
check("both editors lock the SAME path",
      G["_PB_LOCK_FILE"] == PEM.lock_path(CFG), (G["_PB_LOCK_FILE"], PEM.lock_path(CFG)))

# ===========================================================================
print("\n=== D. two PROCESSES race the same config (review fix 1) ===")
# ===========================================================================
# Worker A goes through aux_promptbudget (platform_toolsets.cli); worker B goes
# through plugin_enable (plugins.enabled) — the real dashboard-vs-update.sh
# race. Each holds the lock for ~0.5s between its read and its write, and they
# start 0.1s apart, so B is guaranteed to be inside A's critical section. With
# the flock B waits and re-reads A's result; without it B's write would land on
# the text it read before A wrote and A's edit would vanish.
WORKER = os.path.join(SB, "worker.py")
with open(WORKER, "w", encoding="utf-8") as fh:
    fh.write('''
import importlib.util, os, sys, threading, time
which, sb, repo, hold = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
cfg = os.path.join(sb, ".hermes", "config.yaml")
if which == "pb":
    G = {"__name__": "aux_promptbudget", "HOME": sb,
         "HERMES": os.path.join(sb, "bin", "hermes"),
         "_hermes_env": lambda: dict(os.environ),
         "_state_lock": threading.Lock(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    path = os.path.join(repo, "dashboard", "aux_promptbudget.py")
    exec(compile(open(path).read(), path, "exec"), G)
    real = G["_pb_apply_text"]
    def slow(src, platform, items):
        time.sleep(hold)                 # still holding the lock
        return real(src, platform, items)
    G["_pb_apply_text"] = slow
    G["_pb_write_toolsets"]("cli", ["web", "terminal", "file"])
else:
    spec = importlib.util.spec_from_file_location("pe", os.path.join(repo, "hermes-plugins", "plugin_enable.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    real = m.apply_enable
    def slow(src, name):
        time.sleep(hold)
        return real(src, name)
    m.apply_enable = slow
    m.enable(cfg, "tool-budget", tag="race")
''')

write_cfg()
t0 = time.time()
pa = subprocess.Popen([sys.executable, WORKER, "pb", SB, REPO, "0.5"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(0.1)
pb = subprocess.Popen([sys.executable, WORKER, "pe", SB, REPO, "0.5"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
oa, ea = pa.communicate(timeout=60)
ob, eb = pb.communicate(timeout=60)
elapsed = time.time() - t0
final = open(CFG, encoding="utf-8").read()

check("both workers exited 0", pa.returncode == 0 and pb.returncode == 0,
      (pa.returncode, pb.returncode, ea[-400:], eb[-400:]))
check("they SERIALISED (the second waited for the first)", elapsed > 0.9, elapsed)
check("edit A survived (platform_toolsets.cli)",
      parse(final).get("cli") == ["web", "terminal", "file"], parse(final))
check("edit B survived (plugins.enabled)",
      "tool-budget" in PEM.read_enabled(final), PEM.read_enabled(final))
check("edit B did not lose the pre-existing plugin",
      "loop-breaker" in PEM.read_enabled(final), PEM.read_enabled(final))
check("telegram was never touched", parse(final).get("telegram") == ["web"])
check("the file is not doubled or truncated",
      final.count("platform_toolsets:") == 1 and final.count("plugins:") == 1, final)
check("the file still has the model block", "  context_length: 65536\n" in final)
check("no temp file survived the race",
      [f for f in os.listdir(os.path.dirname(CFG)) if ".tmp-" in f] == [],
      os.listdir(os.path.dirname(CFG)))
check("config mode survived the race",
      oct(os.stat(CFG).st_mode & 0o777) == "0o600")

# the other order, and with plugin_enable first
write_cfg()
t0 = time.time()
pb = subprocess.Popen([sys.executable, WORKER, "pe", SB, REPO, "0.5"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(0.1)
pa = subprocess.Popen([sys.executable, WORKER, "pb", SB, REPO, "0.5"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
pb.communicate(timeout=60)
pa.communicate(timeout=60)
final = open(CFG, encoding="utf-8").read()
check("reversed order: both edits survive too",
      parse(final).get("cli") == ["web", "terminal", "file"]
      and "tool-budget" in PEM.read_enabled(final), final)

# two writers on the SAME key: the second must see the first's text
write_cfg()
pa = subprocess.Popen([sys.executable, WORKER, "pb", SB, REPO, "0.4"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
p2 = subprocess.Popen([sys.executable, WORKER, "pb", SB, REPO, "0.4"],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
pa.communicate(timeout=60)
p2.communicate(timeout=60)
final = open(CFG, encoding="utf-8").read()
check("same-key writers leave ONE cli: block", final.count("  cli:\n") == 1, final)
check("same-key writers leave a valid file",
      parse(final).get("cli") == ["web", "terminal", "file"], parse(final))

# ===========================================================================
print("\n=== E. the build is single-flighted (review fix 3) ===")
# ===========================================================================
write_cfg()
G["_pb_invalidate"]()
builds = []


def fake_build(fresh=False, selection_override=None):
    builds.append(1)
    time.sleep(0.4)                       # a real build is seconds
    return {"ok": True, "n": len(builds)}


G["_pb_build"] = fake_build
threads = [threading.Thread(target=lambda: G["_pb_payload"](True)) for _ in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("6 concurrent ?fresh=1 calls cost ONE build", len(builds) == 1, len(builds))

builds[:] = []
G["_pb_payload"](False)
check("a warm cache builds nothing", len(builds) == 0, len(builds))
G["_pb_invalidate"]()
G["_pb_payload"](True)
check("an invalidated cache does build", len(builds) == 1, len(builds))
builds[:] = []
time.sleep(0.01)
G["_pb_payload"](True)
check("a LATER fresh call still rebuilds", len(builds) == 1, len(builds))

# ===========================================================================
print("\n=== F. a failed probe is reported, not swallowed (review fix 4) ===")
# ===========================================================================
del G["_pb_build"]
exec(compile(open(AUX).read(), AUX, "exec"), G)      # restore the real _pb_build
G["_pb_invalidate"]()


def boom_ps():
    raise RuntimeError("hermes prompt-size exited 127")


def boom_probe(scen):
    raise RuntimeError("the Hermes venv interpreter was not found")


G["_pb_prompt_size"] = boom_ps
G["_pb_probe"] = boom_probe
pay = G["_pb_payload"](True)
check("both failures still answer a payload", pay["ok"] is True)
check("prompt_size_ok is False", pay["prompt_size_ok"] is False)
check("probe_ok is False", pay["probe_ok"] is False)
check("degraded is True", pay["degraded"] is True)
check("tokens_are_tools_only is True", pay["tokens_are_tools_only"] is True)
check("prompt_size_error is carried",
      "prompt-size exited 127" in (pay["prompt_size_error"] or ""), pay["prompt_size_error"])
check("probe_error is carried",
      "venv interpreter" in (pay["probe_error"] or ""), pay["probe_error"])
check("the toolset rows fall back rather than vanish",
      len(pay["toolsets"]) == len(G["_PB_KEYS_FALLBACK"]), len(pay["toolsets"]))

G["_pb_prompt_size"] = lambda: {"system_prompt": {"bytes": 20700},
                                "skills_index": {"bytes": 8988, "chars": 8988}}
G["_pb_invalidate"]()
pay = G["_pb_payload"](True)
check("prompt_size alone recovering is reported",
      pay["prompt_size_ok"] is True and pay["probe_ok"] is False, pay["probe_ok"])
check("still degraded while the probe is down", pay["degraded"] is True)
check("tokens are no longer tools-only", pay["tokens_are_tools_only"] is False)

G["_pb_probe"] = lambda scen: {
    "keys": ["web", "terminal"], "labels": {}, "descs": {},
    "per_toolset": {"web": {"count": 2, "json_bytes": 900, "names": ["a", "b"]},
                    "terminal": {"count": 1, "json_bytes": 400, "names": ["t"]}},
    "scenarios": {k: {"toolsets": ["web"], "count": 2, "json_bytes": 900,
                      "names": ["a", "b"]} for k in
                  ("current", "full", "lean", "focused")},
    "skills": {"error": "not measured"},
}
G["_pb_invalidate"]()
pay = G["_pb_payload"](True)
check("a clean measurement is not degraded", pay["degraded"] is False, pay)
check("prompt_size_ok and probe_ok both True",
      pay["prompt_size_ok"] is True and pay["probe_ok"] is True)
check("tokens_are_tools_only is False", pay["tokens_are_tools_only"] is False)

shutil.rmtree(SB, ignore_errors=True)
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
raise SystemExit(1 if FAIL else 0)
