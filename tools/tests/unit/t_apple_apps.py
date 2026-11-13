#!/usr/bin/env python3
"""Harness for the Apple apps (Reminders/Notes) off-by-default gate.

Three parts:
  A. aux_appleapps.py exercised the way server.py execs it (stubbed globals,
     throwaway HOME) — settings read/write + the two HTTP handlers.
  B. apple_apps_enabled() — server.py's choke point — the REAL function,
     ast-extracted from server.py with the real get_settings (importing the
     whole server would open sockets/threads), exercised against every
     settings shape: absent, explicit true/false, corrupt file, non-dict
     value. (It used to be a re-implemented copy — C84.)
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
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402  the REAL settings_update & co (C84)

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


GET_ROUTES, POST_ROUTES = {}, {}


class Ctx(object):
    def __init__(self, body=None):
        self.body = body or {}


G = {
    "__name__": "aux_appleapps",
    "HOME": SB,
    "HERE": DASH,
    "DATA": DATA,
    # server.py's own settings_update / settings_set / get_settings /
    # read_json / write_json, sharing the module's _state_lock (C84)
    **server_real.settings_api(SETTINGS, _STATE_LOCK),
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

# C59.5: a JSON boolean only — bool("false") is True, which used to TURN ON
# Reminders/Notes automation (the app is then launched and scripted)
for v in ("false", "0", 0, 1, None, "true"):
    write_json(SETTINGS, {"apple_apps": {"reminders": False, "notes": False}})
    r = POST_ROUTES["/api/apple_apps"](Ctx({"reminders": v}))
    check("reminders=%r is refused with 400" % (v,),
          isinstance(r, tuple) and r[1] == 400 and "reminders" in r[0].get("error", ""), r)
    check("  and automation stays off",
          read_json(SETTINGS, {}).get("apple_apps") == {"reminders": False, "notes": False},
          read_json(SETTINGS, {}))
r = POST_ROUTES["/api/apple_apps"](Ctx({"reminders": True, "notes": "false"}))
check("one bad key refuses the whole POST (nothing half-applied)",
      isinstance(r, tuple) and r[1] == 400
      and read_json(SETTINGS, {}).get("apple_apps") == {"reminders": False, "notes": False}, r)


# ===========================================================================
# B. apple_apps_enabled() — server.py's choke point, the REAL function
# ===========================================================================
print("\n=== B. apple_apps_enabled() (server.py) ===")

B = os.path.join(SB, "b-settings.json")
_B_NS = dict(server_real.settings_api(B))
server_real.server_functions(["apple_apps_enabled"], _B_NS)
apple_apps_enabled = _B_NS["apple_apps_enabled"]

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


def _tell_hits():
    """`path:line:text` for every "tell application" line in the SHIPPED
    sources: the files git tracks (R2-61).  Walking the tree counted every
    untracked directory — another agent's scratch copies, mutants, a nested
    checkout — as call sites.  Outside a git checkout (a tarball install) it
    falls back to the old walk."""
    r = _sp.run(["git", "-C", REPO, "ls-files", "-z", "--", "*.py", "*.js"],
                capture_output=True, text=True)
    if r.returncode != 0:
        return _sp.run(
            ["grep", "-rn", "--include=*.py", "--include=*.js",
             "--exclude-dir=.git", "--exclude-dir=graphify-out",
             "--exclude-dir=node_modules", "--exclude-dir=__pycache__",
             "--exclude-dir=.scratch",
             "tell application", REPO],
            capture_output=True, text=True).stdout.splitlines()
    out = []
    for rel in sorted(p for p in r.stdout.split("\0") if p):
        path = os.path.join(REPO, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if "tell application" in line:
                        out.append("%s:%d:%s" % (path, n, line.rstrip("\n")))
        except OSError:
            continue                  # tracked but deleted in the working tree
    return out


hits = _tell_hits()
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
