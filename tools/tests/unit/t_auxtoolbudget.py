#!/usr/bin/env python3
"""aux_toolbudget.py exercised the way server.py execs it: into a globals dict
that already carries HOME/HERE/DATA/SETTINGS_FILE/_state_lock/register_*.
Nothing here touches the real ~/.hermes."""
import json
import os
import shutil
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_toolbudget.py")

SB = tempfile.mkdtemp(prefix="tb-aux-")
os.makedirs(os.path.join(SB, ".hermes", "dashboard"), exist_ok=True)
os.makedirs(os.path.join(SB, ".hermes", "plugins"), exist_ok=True)
DATA = os.path.join(SB, ".hermes", "dashboard")
SETTINGS = os.path.join(DATA, "settings.json")
CFG = os.path.join(SB, ".hermes", "config.yaml")
open(CFG, "w").write("model:\n  default: x\n  context_length: 65536\n")
json.dump({}, open(SETTINGS, "w"))

GET_ROUTES, POST_ROUTES = {}, {}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


_STATE_LOCK = threading.Lock()


def settings_update(mutate_fn):
    """Stand-in for server.py's settings_update() — ONE locked
    read-modify-write of settings.json, the only way an aux module is allowed
    to write it since the 2026-09-10 audit (A01)."""
    with _STATE_LOCK:
        s = read_json(SETTINGS, {})
        if not isinstance(s, dict):
            s = {}
        out = mutate_fn(s)
        if isinstance(out, dict):
            s = out
        write_json(SETTINGS, s)
        return json.loads(json.dumps(s))



G = {
    "__name__": "aux_toolbudget",
    "HOME": SB,
    "HERE": os.path.join(REPO, "dashboard"),
    "DATA": DATA,
    "SETTINGS_FILE": SETTINGS,
    "_state_lock": _STATE_LOCK,
    "settings_update": settings_update,
    "get_settings": lambda: read_json(SETTINGS, {}),
    "read_json": read_json,
    "write_json": write_json,
    "register_get": lambda p, f: GET_ROUTES.__setitem__(p, f),
    "register_post": lambda p, f: POST_ROUTES.__setitem__(p, f),
}
exec(compile(open(AUX).read(), AUX, "exec"), G)

PASS, FAIL = [], []

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (len(PASS), len(FAIL))))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + repr(detail)) if detail and not cond else ""))


apply_enable = G["_tb_apply_enable"]
read_enabled = G["_tb_read_enabled"]

print("\n=== A. plugins.enabled editor (pure) ===")
CASES = [
    ("no plugins block",
     "model:\n  default: x\n",
     "model:\n  default: x\nplugins:\n  enabled:\n    - tool-budget\n"),
    ("no trailing newline",
     "model:\n  default: x",
     "model:\n  default: x\nplugins:\n  enabled:\n    - tool-budget\n"),
    ("plugins block, no enabled key",
     "plugins:\n  disabled: []\n",
     "plugins:\n  enabled:\n    - tool-budget\n  disabled: []\n"),
    ("existing block list (the real shape)",
     "plugins:\n  enabled:\n    - loop-breaker\n  disabled: []\n",
     "plugins:\n  enabled:\n    - loop-breaker\n    - tool-budget\n  disabled: []\n"),
    ("empty inline list",
     "plugins:\n  enabled: []\n  disabled: []\n",
     "plugins:\n  enabled:\n    - tool-budget\n  disabled: []\n"),
    ("populated inline list",
     "plugins:\n  enabled: [loop-breaker, spotify]\n",
     "plugins:\n  enabled:\n    - loop-breaker\n    - spotify\n    - tool-budget\n"),
    ("four-space item indent is preserved",
     "plugins:\n  enabled:\n      - loop-breaker\n",
     "plugins:\n  enabled:\n      - loop-breaker\n      - tool-budget\n"),
    ("a later top-level key is not disturbed",
     "plugins:\n  enabled:\n    - loop-breaker\nmodel:\n  default: x\n",
     "plugins:\n  enabled:\n    - loop-breaker\n    - tool-budget\nmodel:\n  default: x\n"),
    # --- review fix 2: block scoping -------------------------------------
    ("a column-0 comment does not end the block",
     "plugins:\n  enabled:\n    - loop-breaker\n# a note\nmodel:\n  default: x\n",
     "plugins:\n  enabled:\n    - loop-breaker\n    - tool-budget\n# a note\nmodel:\n  default: x\n"),
    ("a blank line does not end the block",
     "plugins:\n  enabled:\n    - loop-breaker\n\nmodel:\n  default: x\n",
     "plugins:\n  enabled:\n    - loop-breaker\n    - tool-budget\n\nmodel:\n  default: x\n"),
    ("a DEEPER enabled: is not plugins.enabled",
     "plugins:\n  options:\n    loop-breaker:\n      enabled: true\n  enabled:\n    - loop-breaker\n",
     "plugins:\n  options:\n    loop-breaker:\n      enabled: true\n  enabled:\n    - loop-breaker\n    - tool-budget\n"),
    ("a deeper enabled: alone is not the list either",
     "plugins:\n  options:\n    loop-breaker:\n      enabled: true\n",
     "plugins:\n  enabled:\n    - tool-budget\n  options:\n    loop-breaker:\n      enabled: true\n"),
    ("a comment between the key and its items",
     "plugins:\n  enabled:\n    # the ones we run\n    - loop-breaker\n",
     "plugins:\n  enabled:\n    # the ones we run\n    - loop-breaker\n    - tool-budget\n"),
]
for name, src, want in CASES:
    got = apply_enable(src, "tool-budget")
    check(name, got == want, got)

print("\n=== B. idempotency — a second enable is byte-identical ===")
for name, src, _want in CASES:
    once = apply_enable(src, "tool-budget")
    twice = apply_enable(once, "tool-budget")
    check("idempotent: " + name, once == twice, twice)
check("already present -> unchanged",
      apply_enable("plugins:\n  enabled:\n    - tool-budget\n", "tool-budget")
      == "plugins:\n  enabled:\n    - tool-budget\n")
check("quoted entry counts as present",
      apply_enable("plugins:\n  enabled:\n    - \"tool-budget\"\n", "tool-budget")
      == "plugins:\n  enabled:\n    - \"tool-budget\"\n")

print("\n=== C. reader ===")
check("reads a block list",
      read_enabled("plugins:\n  enabled:\n    - a\n    - b\n  disabled: []\n") == ["a", "b"])
check("reads an inline list",
      read_enabled("plugins:\n  enabled: [a, b]\n") == ["a", "b"])
check("empty inline list", read_enabled("plugins:\n  enabled: []\n") == [])
check("absent block", read_enabled("model:\n  default: x\n") == [])
check("stops at the next top-level key",
      read_enabled("plugins:\n  enabled:\n    - a\nmodel:\n  x: 1\n") == ["a"])
# The installed config is a bonus signal (it is the only hand-written YAML
# around); absent — a fresh checkout, CI — the reader is already covered by
# the synthetic cases above.
_real_cfg = os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")
if os.path.exists(_real_cfg):
    _txt = open(_real_cfg).read()
    _declares = "plugins:" in _txt and "enabled:" in _txt
    check("the installed config's plugin list parses",
          bool(read_enabled(_txt)) == _declares, read_enabled(_txt))
else:
    print("  skip the installed ~/.hermes/config.yaml is not present")

print("\n=== D. settings read/write validation ===")
S = G["_tb_settings"]
W = G["_tb_write_settings"]
check("defaults when the key is absent", S() == {"enabled": True, "max_chars": 24000, "spill": True}, S())
W({"max_chars": 8000})
check("valid write lands", S()["max_chars"] == 8000, S())
check("other keys keep their defaults", S()["enabled"] is True and S()["spill"] is True)
W({"enabled": False, "spill": False})
check("partial write merges", S() == {"enabled": False, "max_chars": 8000, "spill": False}, S())
check("settings.json keeps its other keys", set(read_json(SETTINGS, {})) >= {"tool_budget"})
json.dump({"tool_budget": {"max_chars": 3}, "weather_city": "x"}, open(SETTINGS, "w"))
check("out-of-range max_chars falls back", S()["max_chars"] == 24000, S())
json.dump({"tool_budget": "nope"}, open(SETTINGS, "w"))
check("garbage tool_budget -> defaults", S() == {"enabled": True, "max_chars": 24000, "spill": True}, S())
json.dump({"weather_city": "x"}, open(SETTINGS, "w"))
W({"max_chars": 16000})
check("write preserves unrelated settings", read_json(SETTINGS, {}).get("weather_city") == "x")

print("\n=== E. POST validation ===")


class Ctx:
    def __init__(self, body):
        self.body = body
        self.query = {}


post = POST_ROUTES["/api/tool/budget"]
get = GET_ROUTES["/api/tool/budget"]
for body, why in (({}, "empty body"),
                  ({"max_chars": 100}, "below the floor"),
                  ({"max_chars": 999999}, "above the ceiling"),
                  ({"max_chars": "8000"}, "string max_chars"),
                  ({"max_chars": True}, "bool max_chars"),
                  ({"enabled": "yes"}, "string enabled"),
                  ({"spill": 1}, "int spill")):
    r = post(Ctx(body))
    ok = isinstance(r, tuple) and r[1] == 400 and r[0].get("ok") is False
    check("400 on %s" % why, ok, r)
for body in ({"max_chars": 4000}, {"max_chars": 200000}, {"enabled": True}, {"spill": False}):
    r = post(Ctx(body))
    check("accepts %s" % body, isinstance(r, dict) and r.get("ok") is True, r)

print("\n=== F. GET payload shape ===")
p = get(Ctx({}))
check("is a dict", isinstance(p, dict))
for k in ("installed", "enabled_in_config", "settings", "stats", "choices",
          "context_length", "restart_required", "restart_note", "limits"):
    check("payload has %s" % k, k in p, sorted(p))
check("stats.today shape",
      set(("calls", "omitted_chars", "est_tokens_saved", "spills")) <= set(p["stats"]["today"]),
      p["stats"]["today"])
check("stats.last is None with no log", p["stats"]["last"] is None)
check("context_length read off the sandbox config", p["context_length"] == 65536, p["context_length"])
check("four choices offered", len(p["choices"]) == 4)
check("24k choice is ~10% of the window",
      [c for c in p["choices"] if c["chars"] == 24000][0]["pct_window"] == 10.2,
      p["choices"])
check("not installed in the sandbox", p["installed"] is False)
check("not enabled in the sandbox config", p["enabled_in_config"] is False)

print("\n=== G. stats read back off a real-looking log ===")
today = __import__("datetime").date.today().isoformat()
with open(os.path.join(DATA, "tool-budget.jsonl"), "w") as fh:
    fh.write('{"ts":"2020-01-01T00:00:00","tool":"terminal","kept_chars":10,"omitted_chars":90,"est_tokens_saved":25,"spill_path":"/x","session":"old"}\n')
    fh.write('{ this line is corrupt\n')
    fh.write('{"ts":"%sT09:00:00","tool":"read_file","kept_chars":6000,"omitted_chars":35000,"est_tokens_saved":9722,"spill_path":"/a","session":"s1"}\n' % today)
    fh.write('{"ts":"%sT09:01:00","tool":"terminal","kept_chars":5900,"omitted_chars":40000,"est_tokens_saved":11111,"spill_path":"","session":"s1"}\n' % today)
    fh.write('{"ts":"%sT09:02:00","tool":"web_extract","kept_chars":6000,"omitted_chars":12000,"est_tokens_saved":3333,"spill_path":"/c","session":"s2"}\n' % today)
p = get(Ctx({}))
t = p["stats"]["today"]
check("corrupt line skipped, yesterday excluded", t["calls"] == 3, t)
check("omitted_chars summed", t["omitted_chars"] == 87000, t)
check("est_tokens_saved summed", t["est_tokens_saved"] == 24166, t)
check("spills counted (empty path not counted)", t["spills"] == 2, t)
check("per-tool breakdown", t["tools"] == {"read_file": 1, "terminal": 1, "web_extract": 1}, t["tools"])
check("orig_chars = kept + omitted", t["orig_chars"] == t["kept_chars"] + t["omitted_chars"])
check("last row is the newest", p["stats"]["last"]["tool"] == "web_extract", p["stats"]["last"])

print("\n=== H. install into a sandbox ~/.hermes/plugins ===")
open(CFG, "w").write("plugins:\n  enabled:\n    - loop-breaker\n  disabled: []\nmodel:\n  context_length: 65536\n")
r = post(Ctx({"install": True}))
check("install ok", isinstance(r, dict) and r.get("ok") is True, r)
dst = os.path.join(SB, ".hermes", "plugins", "tool-budget")
check("plugin present", os.path.isfile(os.path.join(dst, "__init__.py")))
check("installed via symlink", os.path.islink(dst), os.path.realpath(dst))
check("link points at the repo",
      os.path.realpath(dst) == os.path.realpath(os.path.join(REPO, "hermes-plugins", "tool-budget")))
cfg_now = open(CFG).read()
check("config gained the entry", "- tool-budget" in cfg_now, cfg_now)
check("loop-breaker survived", "- loop-breaker" in cfg_now)
check("config_changed reported", r["config_changed"] is True)
check("a backup was made", r["backup"] and os.path.exists(r["backup"]), r.get("backup"))
check("backup is 0600", oct(os.stat(r["backup"]).st_mode & 0o777) == "0o600")
check("enabled_at stamped", os.path.exists(os.path.join(DATA, "tool-budget-enabled-at")))
check("restart_required now true", r["restart_required"] is True, r["restart_required"])
before = open(CFG).read()
n_baks = len([f for f in os.listdir(os.path.dirname(CFG)) if ".bak-toolbudget-" in f])
r2 = post(Ctx({"install": True}))
check("second install is a no-op on the config", open(CFG).read() == before)
check("second install writes no backup",
      len([f for f in os.listdir(os.path.dirname(CFG)) if ".bak-toolbudget-" in f]) == n_baks)
check("second install reports config_changed False", r2["config_changed"] is False)
check("installed is now true", r2["installed"] is True)
check("enabled_in_config is now true", r2["enabled_in_config"] is True)

print("\n=== I. restart is opt-in only ===")
calls = []
G["_tb_restart_serve"] = lambda: (calls.append(1), (True, ""))[1]
post(Ctx({"max_chars": 24000}))
check("a settings write does not restart anything", calls == [], calls)
r = post(Ctx({"restart": True}))
check("restart only on an explicit flag", calls == [1], calls)
check("restarted reported", r["restarted"] is True)


check("read_enabled ignores a nested enabled:",
      read_enabled("plugins:\n  options:\n    lb:\n      enabled: true\n") == [],
      read_enabled("plugins:\n  options:\n    lb:\n      enabled: true\n"))
check("read_enabled reads past a column-0 comment",
      read_enabled("plugins:\n# note\n  enabled:\n    - a\n    - b\n") == ["a", "b"],
      read_enabled("plugins:\n# note\n  enabled:\n    - a\n    - b\n"))
check("read_enabled skips a comment inside the list",
      read_enabled("plugins:\n  enabled:\n    - a\n    # why\n    - b\n") == ["a", "b"],
      read_enabled("plugins:\n  enabled:\n    - a\n    # why\n    - b\n"))
check("read_enabled stops at a sibling key",
      read_enabled("plugins:\n  enabled:\n    - a\n  disabled:\n    - z\n") == ["a"],
      read_enabled("plugins:\n  enabled:\n    - a\n  disabled:\n    - z\n"))

print("\n=== J. review fix 10: a restart is refused while a turn is running ===")
calls = []
G["_tb_restart_serve"] = lambda: (calls.append(1), (True, ""))[1]
G["CHAT_JOBS"] = {"j1": {"done": False}}
r = post(Ctx({"restart": True}))
code = r[1] if isinstance(r, tuple) else 200
body_r = r[0] if isinstance(r, tuple) else r
check("busy -> refused", body_r.get("ok") is False, body_r)
check("busy -> 409", code == 409, code)
check("busy -> the flag is in the payload", body_r.get("busy") is True, body_r)
check("busy -> nothing was kickstarted", calls == [], calls)
cfg_before = open(CFG).read()
r = post(Ctx({"install": True, "restart": True}))
body_r = r[0] if isinstance(r, tuple) else r
check("a busy refusal happens BEFORE any write", body_r.get("ok") is False)
check("a busy refusal changes no config", open(CFG).read() == cfg_before)
G["CHAT_JOBS"] = {"j1": {"done": True}}
r = post(Ctx({"restart": True}))
check("a finished job does not block", r.get("ok") is True, r)
check("and it did restart", calls == [1], calls)
G["CHAT_JOBS"] = {}
check("no CHAT_JOBS at all is not busy", G["_tb_busy_jobs"]() is False)
del G["CHAT_JOBS"]
check("an absent CHAT_JOBS global is not busy", G["_tb_busy_jobs"]() is False)

print("\n=== K. review fix 8: an unreadable helper is UNKNOWN, not off ===")
saved_helper = G["_TB_HELPER"]
saved_err = G["_TB_HELPER_ERR"]
G["_TB_HELPER"] = None
G["_TB_HELPER_ERR"] = "plugin_enable.py could not be loaded (ImportError)"
try:
    pay = G["_tb_payload"]()
    check("enabled_in_config is None (unknown)", pay["enabled_in_config"] is None, pay["enabled_in_config"])
    check("config_state_known is False", pay["config_state_known"] is False)
    check("active is False, not None", pay["active"] is False, pay["active"])
    check("restart_required stays False on an unknown state", pay["restart_required"] is False)
    check("helper_error rides in the payload", "could not be loaded" in pay["helper_error"])
    check("ok is still True (the card renders)", pay["ok"] is True)
    r = post(Ctx({"install": True}))
    body_r = r[0] if isinstance(r, tuple) else r
    check("install refuses without the helper", body_r.get("ok") is False, body_r)
finally:
    G["_TB_HELPER"] = saved_helper
    G["_TB_HELPER_ERR"] = saved_err
pay = G["_tb_payload"]()
check("with the helper back it is a real boolean",
      isinstance(pay["enabled_in_config"], bool), pay["enabled_in_config"])
check("and known", pay["config_state_known"] is True)

print("\n=== L. an unreadable config is unknown; a missing one is False ===")
os.rename(CFG, CFG + ".away")
try:
    check("no config at all -> False (certainly not enabled)",
          G["_tb_enabled_in_config"]() is False)
finally:
    os.rename(CFG + ".away", CFG)
orig_read = G["_tb_read_enabled"]
G["_tb_read_enabled"] = lambda src: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    check("an unparseable config -> None (unknown)",
          G["_tb_enabled_in_config"]() is None)
finally:
    G["_tb_read_enabled"] = orig_read

shutil.rmtree(SB, ignore_errors=True)
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
raise SystemExit(1 if FAIL else 0)
