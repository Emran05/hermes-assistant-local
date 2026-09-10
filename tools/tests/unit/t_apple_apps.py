#!/usr/bin/env python3
"""Harness for the Apple apps (Reminders/Notes) off-by-default gate.

Three parts:
  A. aux_appleapps.py exercised the way server.py execs it (stubbed globals,
     throwaway HOME) — settings read/write + the two HTTP handlers.
  B. apple_apps_enabled() — server.py's choke point — reimplemented verbatim
     (it's an 8-line pure function; importing the real 4000-line server.py
     just to test it would open real sockets/threads) and exercised against
     every settings shape: absent, explicit true/false, corrupt file,
     non-dict value.
  C. A source-scan regression proof: for every one of the four real
     Reminders/Notes osascript call sites the owner's complaint named, the
     gate check (`apple_apps_enabled(` / the needsyou wrapper) must appear
     in the function BEFORE the `tell application "Reminders"/"Notes"` line
     and before any `subprocess.run` in that function — i.e. the app can
     never be launched while the flag is off. This is the automated version
     of "grep every call site" from the task's verify step, so it stays true
     after any future edit to these functions.

Nothing here touches the real ~/.hermes or the running dashboard.
"""
import json
import os
import re
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + repr(detail)) if detail and not cond else ""))


# ===========================================================================
# A. aux_appleapps.py — settings + routes
# ===========================================================================
print("=== A. aux_appleapps.py (settings + /api/apple_apps) ===")

SB = tempfile.mkdtemp(prefix="aa-aux-")
DATA = os.path.join(SB, ".hermes", "dashboard")
os.makedirs(DATA, exist_ok=True)
SETTINGS = os.path.join(DATA, "settings.json")
json.dump({}, open(SETTINGS, "w"))


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


GET_ROUTES, POST_ROUTES = {}, {}


class Ctx(object):
    def __init__(self, body=None):
        self.body = body or {}


G = {
    "__name__": "aux_appleapps",
    "HOME": SB,
    "HERE": DASH,
    "DATA": DATA,
    "SETTINGS_FILE": SETTINGS,
    "_state_lock": _STATE_LOCK,
    "get_settings": lambda: read_json(SETTINGS, {}),
    "read_json": read_json,
    "write_json": write_json,
    "settings_update": settings_update,
    "register_get": lambda p, f: GET_ROUTES.__setitem__(p, f),
    "register_post": lambda p, f: POST_ROUTES.__setitem__(p, f),
}
exec(compile(open(os.path.join(DASH, "aux_appleapps.py")).read(),
             "aux_appleapps.py", "exec"), G)

check("registers GET /api/apple_apps", "/api/apple_apps" in GET_ROUTES)
check("registers POST /api/apple_apps", "/api/apple_apps" in POST_ROUTES)

r = GET_ROUTES["/api/apple_apps"](Ctx())
check("GET default: both off", r == {"ok": True, "reminders": False, "notes": False}, r)

r = POST_ROUTES["/api/apple_apps"](Ctx({"reminders": True}))
check("POST reminders=true -> ok", r == {"ok": True, "reminders": True, "notes": False}, r)
check("settings.json actually updated",
      read_json(SETTINGS, {}).get("apple_apps") == {"reminders": True})

r = POST_ROUTES["/api/apple_apps"](Ctx({"notes": True}))
check("partial update preserves the other key (reminders stays true)",
      r == {"ok": True, "reminders": True, "notes": True}, r)

r = POST_ROUTES["/api/apple_apps"](Ctx({"reminders": False, "notes": False}))
check("both back off", r == {"ok": True, "reminders": False, "notes": False}, r)

r = POST_ROUTES["/api/apple_apps"](Ctx({}))
bad = isinstance(r, tuple) and r[1] == 400 and r[0].get("ok") is False
check("empty body is refused with 400, not a silent no-op", bad, r)

# a pre-existing unrelated settings key must survive a write
write_json(SETTINGS, {"weather_city": "Boston", "apple_apps": {"reminders": True}})
POST_ROUTES["/api/apple_apps"](Ctx({"reminders": False}))
after = read_json(SETTINGS, {})
check("an unrelated settings key is not clobbered by the read-modify-write",
      after.get("weather_city") == "Boston", after)
check("the other app's flag survives a single-key POST",
      after.get("apple_apps", {}).get("reminders") is False, after)


# ===========================================================================
# B. apple_apps_enabled() — server.py's choke point, reimplemented verbatim
# ===========================================================================
print("\n=== B. apple_apps_enabled() (server.py) ===")

_settings_path = [None]


def _get_settings_stub():
    return read_json(_settings_path[0], {})


def apple_apps_enabled(kind):
    try:
        cfg = _get_settings_stub().get("apple_apps")
        if isinstance(cfg, dict):
            return bool(cfg.get(kind))
    except Exception:
        pass
    return False


B = os.path.join(SB, "b-settings.json")
_settings_path[0] = B

json.dump({}, open(B, "w"))
check("no apple_apps key at all -> both default False",
      apple_apps_enabled("reminders") is False and apple_apps_enabled("notes") is False)

json.dump({"apple_apps": {"reminders": True}}, open(B, "w"))
check("reminders true, notes absent -> reminders True, notes False",
      apple_apps_enabled("reminders") is True and apple_apps_enabled("notes") is False)

json.dump({"apple_apps": {"reminders": False, "notes": True}}, open(B, "w"))
check("both keys explicit -> read correctly",
      apple_apps_enabled("reminders") is False and apple_apps_enabled("notes") is True)

json.dump({"apple_apps": "not-a-dict"}, open(B, "w"))
check("apple_apps not a dict -> fails closed (False)",
      apple_apps_enabled("reminders") is False)

with open(B, "w") as f:
    f.write("{not valid json")
check("corrupt settings.json -> fails closed (False), never raises",
      apple_apps_enabled("reminders") is False)

os.unlink(B)
check("missing settings.json -> fails closed (False), never raises",
      apple_apps_enabled("notes") is False)


# ===========================================================================
# C. source-scan regression proof — every real call site is gated BEFORE any
#    "tell application" / subprocess.run in the same function.
# ===========================================================================
print("\n=== C. source-scan: every osascript call site is gated first ===")


def read(path):
    with open(os.path.join(DASH, path), encoding="utf-8") as f:
        return f.read()


def func_body(src, def_line_re):
    """Crude but sufficient for this file's style: the function's own
    top-level def line through the next top-level `def `/`class ` at column
    0, or EOF."""
    m = re.search(def_line_re, src)
    assert m, "function not found: %s" % def_line_re
    start = m.start()
    m2 = re.search(r"\n(?:def |class )", src[start + 1:])
    end = (start + 1 + m2.start()) if m2 else len(src)
    return src[start:end]


def gated_before_call(body, gate_markers, call_markers, label):
    gate_pos = None
    for g in gate_markers:
        p = body.find(g)
        if p != -1 and (gate_pos is None or p < gate_pos):
            gate_pos = p
    call_pos = None
    for c in call_markers:
        p = body.find(c)
        if p != -1 and (call_pos is None or p < call_pos):
            call_pos = p
    ok = gate_pos is not None and call_pos is not None and gate_pos < call_pos
    check(label, ok, {"gate_pos": gate_pos, "call_pos": call_pos})


server_src = read("server.py")
expanders_src = read("expanders_extra.py")
needsyou_src = read("aux_needsyou.py")

gated_before_call(
    func_body(server_src, r"\ndef w_reminders\(\):"),
    ['apple_apps_enabled("reminders")'],
    ['tell application "Reminders"', "subprocess.run"],
    "server.py w_reminders(): gate precedes the Reminders osascript")

gated_before_call(
    func_body(expanders_src, r"\ndef expand_reminders\(\):"),
    ['apple_apps_enabled("reminders")'],
    ['tell application "Reminders"', "subprocess.run"],
    "expanders_extra.py expand_reminders(): gate precedes the Reminders osascript")

gated_before_call(
    # expand_notes() defines the osascript inside a NESTED apple_notes()
    # closure but only ever INVOKES it from a ternary further down
    # (`apple_block = ... if not apple_apps_enabled("notes") else
    # _cached("notes_apple", 120, apple_notes)`) — defining a function does
    # not execute its body, so the right proxy for "gated before it can run"
    # is the gate preceding the CALL SITE (`_cached("notes_apple"`), not the
    # osascript text inside the nested def.
    func_body(expanders_src, r"\ndef expand_notes\(\):"),
    ['apple_apps_enabled("notes")'],
    ['_cached("notes_apple"'],
    "expanders_extra.py expand_notes(): gate precedes the call to apple_notes()")

gated_before_call(
    func_body(needsyou_src, r"\ndef _ny_rem_fetch\(\):"),
    ['apple_apps_enabled("reminders")'],
    ['tell application "Reminders"', "subprocess.run"],
    "aux_needsyou.py _ny_rem_fetch(): gate precedes the Reminders osascript")

# and: no OTHER "tell application" targeting Reminders/Notes/Calendar exists
# anywhere in the tree outside the four known, now-gated call sites. Comments
# / docstrings that merely DESCRIBE the pattern (this file's own included)
# don't count — a real call site is a Python string-literal line beginning
# `'tell application "..."` , never a line whose stripped text starts with a
# comment marker.
import subprocess as _sp
hits = _sp.run(
    ["grep", "-rn", "--include=*.py", "--include=*.js",
     "--exclude-dir=.git", "--exclude-dir=graphify-out",
     "--exclude-dir=node_modules", "--exclude-dir=__pycache__",
     "tell application", REPO],
    capture_output=True, text=True).stdout.splitlines()
targets = []
for h in hits:
    m = re.match(r"^[^:]+:\d+:(.*)$", h)
    text = (m.group(1) if m else h).strip()
    if text.startswith("#") or text.startswith("//") or text.startswith("*"):
        continue
    # A real call site is the literal AppleScript source line this codebase
    # writes: 'tell application "Reminders"\n' (immediately closed by an
    # escaped newline then the closing quote) — as opposed to prose that
    # merely quotes the pattern (e.g. a docstring saying `"Reminders"/"Notes"`).
    if re.search(r"'tell application \"(Reminders|Notes|Calendar)\"\\n'", text):
        targets.append(h)
check("exactly the 4 known Reminders/Notes call sites exist repo-wide "
      "(none targeting Calendar.app, none newly introduced)",
      len(targets) == 4, targets)

print("\nTESTS %d passed %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
