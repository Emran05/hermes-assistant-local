#!/usr/bin/env python3
"""t_dictation.py — harness for dashboard/aux_dictation.py.

Runs the module the way server.py does (exec'd into a namespace that already
holds the handful of globals it names) but against a SANDBOX HOME, so nothing
here can touch ~/.hermes/dashboard/settings.json or the real dictation store.

    python3 t_dictation.py            # every check
    python3 t_dictation.py -v         # + one line per check

The model path is exercised with a STUBBED lane — a tiny HTTP server that
answers like an OpenAI-compatible /v1/chat/completions.  A real lane is never
contacted and nothing here can wake a model: `bg_online`/`model_online` are
functions this file controls.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(HERE)))
MODULE = os.path.join(REPO, "dashboard", "aux_dictation.py")

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILURES = []


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print("  ok   %s" % name)
    else:
        FAIL += 1
        FAILURES.append((name, got, want))
        print("  FAIL %s\n       got:  %r\n       want: %r" % (name, got, want))


def truthy(name, got):
    check(name, bool(got), True)


# ---------------------------------------------------------------------------
# load the module into a sandbox namespace
# ---------------------------------------------------------------------------

SANDBOX = tempfile.mkdtemp(prefix="hermes-dictation-t-")
DATA = os.path.join(SANDBOX, ".hermes", "dashboard")
os.makedirs(DATA, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA, "settings.json")

_settings = {}
_state_lock = threading.Lock()
GET_ROUTES, POST_ROUTES = {}, {}

LANE_ONLINE = {"bg": False, "primary": False}
LANE_URL = {"url": None}


def get_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def settings_update(mutate_fn):
    """Stand-in for server.py's settings_update() — ONE locked
    read-modify-write of settings.json, the only way an aux module is allowed
    to write it since the 2026-09-10 audit (A01)."""
    with _state_lock:
        s = get_settings()
        if not isinstance(s, dict):
            s = {}
        out = mutate_fn(s)
        if isinstance(out, dict):
            s = out
        write_json(SETTINGS_FILE, s)
        return json.loads(json.dumps(s))


def bg_online():
    return LANE_ONLINE["bg"]


def model_online():
    return LANE_ONLINE["primary"]


def bg_lane():
    # Deliberately mirrors server.py: it falls back to the PRIMARY when the bg
    # lane is down, which is exactly why the module must check online-ness first.
    if LANE_ONLINE["bg"]:
        return {"lane": "bg", "chat_url": LANE_URL["url"], "model": "stub-9b"}
    return {"lane": "primary", "chat_url": LANE_URL["url"], "model": "stub-27b"}


NS = {
    "__name__": "aux_dictation",
    "HOME": SANDBOX,
    "DATA": DATA,
    "HERE": os.path.join(REPO, "dashboard"),
    "SETTINGS_FILE": SETTINGS_FILE,
    "_state_lock": _state_lock,
    "get_settings": get_settings,
    "write_json": write_json,
    "settings_update": settings_update,
    "bg_online": bg_online,
    "model_online": model_online,
    "bg_lane": bg_lane,
    "register_get": lambda p, f: GET_ROUTES.__setitem__(p, f),
    "register_post": lambda p, f: POST_ROUTES.__setitem__(p, f),
}

with open(MODULE) as f:
    exec(compile(f.read(), MODULE, "exec"), NS)

clean = NS["dictation_clean_rules"]


class Ctx:
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}
        self.raw_path = ""

    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d


def POST(path, body):
    res = POST_ROUTES[path](Ctx(body=body))
    return res[0] if isinstance(res, tuple) else res


def GET(path, query=None):
    res = GET_ROUTES[path](Ctx(query=query))
    return res[0] if isinstance(res, tuple) else res


# ---------------------------------------------------------------------------
# 1. rules: fillers
# ---------------------------------------------------------------------------
print("rules — filler removal")
check("leading um", clean("Um, this is a test."), "This is a test.")
check("mid-sentence uh", clean("this is, uh, a test"), "This is a test.")
check("several", clean("um so uh the thing er works"), "So the thing works.")
check("you know", clean("it is, you know, fine"), "It is fine.")
check("hmm", clean("hmm let me think"), "Let me think.")
check("not a real word (summer)", clean("summer is coming"), "Summer is coming.")
check("not a real word (uhuru)", clean("uhuru park"), "Uhuru park.")
check("er inside a word (error)", clean("error handling matters"),
      "Error handling matters.")

# ---------------------------------------------------------------------------
# 2. rules: doubled words
# ---------------------------------------------------------------------------
print("rules — doubled words")
check("simple double", clean("the the file is open"), "The file is open.")
check("triple", clean("I I I think so"), "I think so.")
check("legitimate repeat kept across a comma",
      clean("very, very good"), "Very, very good.")

# ---------------------------------------------------------------------------
# 3. rules: self-corrections
# ---------------------------------------------------------------------------
print("rules — self-corrections")
check("the real selftest transcript",
      clean("Um, this is a test of the local dictation system. "
            "No, wait, the local dictation pipeline."),
      "This is a test of the local dictation pipeline.")
check("no wait, no comma",
      clean("send it to the blue folder no wait the green folder"),
      "Send it to the green folder.")
check("last anchor wins",
      clean("open the local file, no wait, the local folder"),
      "Open the local folder.")
check("i mean",
      clean("meet me on Tuesday, I mean, on Wednesday"),
      "Meet me on Wednesday.")
check("scratch that with no anchor falls back to the clause",
      clean("call the vet, scratch that, book a table"),
      "Call the vet, book a table.")
check("marker with nothing after it is dropped",
      clean("this is fine, no wait"), "This is fine.")

# ---------------------------------------------------------------------------
# 4. rules: capitalisation and punctuation
# ---------------------------------------------------------------------------
print("rules — capitalisation and punctuation")
check("sentence case", clean("hello there. how are you"), "Hello there. How are you.")
check("standalone i", clean("i think i am right"), "I think I am right.")
check("i inside a word is untouched", clean("it is a big win"), "It is a big win.")
check("final stop added once", clean("already done."), "Already done.")
check("question mark kept", clean("are we done?"), "Are we done?")
check("space before comma", clean("yes , of course"), "Yes, of course.")
check("empty in, empty out", clean("   "), "")

# ---------------------------------------------------------------------------
# 5. rules: dictionary
# ---------------------------------------------------------------------------
print("rules — dictionary")
D = {"hermes": "Hermes", "mlx": "MLX", "kew bee": "kubectl"}
check("simple", clean("ask hermes about it", "prose", D), "Ask Hermes about it.")
check("case-insensitive", clean("HERMES is local", "prose", D), "Hermes is local.")
check("multi-word key", clean("run kew bee get pods", "prose", D),
      "Run kubectl get pods.")
check("word boundary respected", clean("hermetic seal", "prose", D),
      "Hermetic seal.")
check("longest key first",
      clean("open hermes assistant", "prose",
            {"hermes": "Hermes", "hermes assistant": "Hermes Assistant"}),
      "Open Hermes Assistant.")

# ---------------------------------------------------------------------------
# 6. rules: per-app styles
# ---------------------------------------------------------------------------
print("rules — styles")
check("prose adds a full stop", clean("on my way", "prose"), "On my way.")
check("casual does not", clean("on my way", "casual"), "On my way")
check("casual still capitalises and de-fillers",
      clean("um, on my way", "casual"), "On my way")
check("verbatim leaves case and punctuation alone",
      clean("um, git commit dash m fix", "verbatim"), "git commit dash m fix")
check("verbatim still removes fillers and doubles",
      clean("uh make make clean", "verbatim"), "make clean")
check("verbatim applies the dictionary",
      clean("run kew bee logs", "verbatim", D), "run kubectl logs")
check("unknown style falls back to prose",
      clean("hello", "shouty"), "Hello.")

# ---------------------------------------------------------------------------
# 7. settings round-trip
# ---------------------------------------------------------------------------
print("settings")
s = GET("/api/dictation/settings")
check("default mode", s["cleanup"], "rules")
check("history off by default", s["keep_history"], False)
check("default history days", s["history_days"], 7)
truthy("seeded app styles", s["app_styles"].get("com.apple.Terminal") == "verbatim")

s = POST("/api/dictation/settings", {"cleanup": "model", "history_days": 999,
                                     "dictionary": {"  foo  ": " bar "},
                                     "app_styles": {"com.x": "casual",
                                                    "com.y": "nonsense"},
                                     "keep_history": True})
check("mode persisted", s["cleanup"], "model")
check("history days clamped", s["history_days"], 90)
check("dictionary trimmed", s["dictionary"], {"foo": "bar"})
check("bad style dropped", s["app_styles"], {"com.x": "casual"})
check("keep_history on", s["keep_history"], True)
check("survives a fresh read", GET("/api/dictation/settings")["cleanup"], "model")

s = POST("/api/dictation/settings", {"cleanup": "banana"})
check("unknown mode ignored", s["cleanup"], "model")

check("style for a known bundle",
      NS["_dct_style_for"]("com.x", "", s), "casual")
check("style for an unknown bundle", NS["_dct_style_for"]("com.zz", "", s), "prose")

# ---------------------------------------------------------------------------
# 8. finish, rules mode
# ---------------------------------------------------------------------------
print("finish — rules mode")
POST("/api/dictation/settings", {"cleanup": "rules", "keep_history": False,
                                 "dictionary": {}, "app_styles":
                                 {"com.apple.Terminal": "verbatim"}})
r = POST("/api/dictation/finish",
         {"text": "Um, this is a test of the local dictation system. "
                  "No, wait, the local dictation pipeline.",
          "app_bundle": "com.apple.Notes", "app_name": "Notes",
          "duration_ms": 2400, "engine": "SpeechAnalyzer"})
check("finish mode", r["mode"], "rules")
check("finish text", r["text"], "This is a test of the local dictation pipeline.")
truthy("finish echoes the raw transcript", r["raw"].startswith("Um, this is"))
truthy("finish is fast", r["ms"] < 200)

r = POST("/api/dictation/finish", {"text": "um, ls dash l", "app_bundle":
                                   "com.apple.Terminal", "app_name": "Terminal"})
check("terminal style resolved", r["style"], "verbatim")
check("terminal text left alone", r["text"], "ls dash l")

r, code = POST_ROUTES["/api/dictation/finish"](Ctx(body={"text": "   "}))
check("empty transcript is a 400", code, 400)

r = POST("/api/dictation/finish", {"text": "um uh er"})
check("cleanup that empties the text falls back to raw", r["text"], "um uh er")
check("...and says so", r["mode"], "off")

POST("/api/dictation/settings", {"cleanup": "off"})
r = POST("/api/dictation/finish", {"text": "Um, this is,  uh, raw."})
check("off mode is untouched (whitespace only)", r["text"], "Um, this is, uh, raw.")
check("off mode says off", r["mode"], "off")

# ---------------------------------------------------------------------------
# 9. model mode against a STUBBED lane
# ---------------------------------------------------------------------------
print("finish — model mode (stubbed lane)")

STUB = {"answer": "This is the model's cleaned line.", "delay": 0.0,
        "calls": 0, "seen": []}


class StubLane(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        STUB["calls"] += 1
        STUB["seen"].append(body)
        if STUB["delay"]:
            time.sleep(STUB["delay"])
        payload = json.dumps({"choices": [{"message":
                              {"content": STUB["answer"]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), StubLane)
LANE_URL["url"] = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

POST("/api/dictation/settings", {"cleanup": "model"})

# 9a. no lane online -> the rules result stands, and nothing is contacted
LANE_ONLINE["bg"] = LANE_ONLINE["primary"] = False
before = STUB["calls"]
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("offline: mode stays rules", r["mode"], "rules")
check("offline: rules result", r["text"], "Hello there.")
check("offline: the lane was never called", STUB["calls"], before)
truthy("offline: says why", "no model lane" in (r.get("note") or ""))

# 9b. bg lane online -> the model answer is used
LANE_ONLINE["bg"] = True
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("online: mode is model", r["mode"], "model")
check("online: model text used", r["text"], "This is the model's cleaned line.")
sent = STUB["seen"][-1]
check("the model saw the RULES output, not the raw text",
      sent["messages"][-1]["content"], "Hello there.")
check("temperature is 0", sent["temperature"], 0.0)
truthy("style hint travels with the prompt",
       "prose" in sent["messages"][0]["content"].lower()
       or "full sentences" in sent["messages"][0]["content"].lower())

# 9c. an implausible answer is refused
STUB["answer"] = ("Sure! Here is your cleaned text, and by the way I also "
                  "looked up the weather for you, which is sunny, and I have "
                  "added three paragraphs of helpful context about dictation "
                  "systems in general, plus a bulleted summary at the end.")
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("chatty answer refused", r["mode"], "rules")
check("chatty answer: rules stand", r["text"], "Hello there.")

STUB["answer"] = ""
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("empty answer refused", r["mode"], "rules")

STUB["answer"] = "```\nHello there.\n```"
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("fenced answer refused", r["mode"], "rules")

# 9d. a slow lane must not hold the user
STUB["answer"] = "Hello there."
STUB["delay"] = NS["DCT_MODEL_TIMEOUT"] + 1.5
t0 = time.time()
r = POST("/api/dictation/finish", {"text": "um, hello there"})
elapsed = time.time() - t0
check("slow lane: rules stand", r["mode"], "rules")
truthy("slow lane: capped near the 4s budget (%.1fs)" % elapsed,
       elapsed < NS["DCT_MODEL_TIMEOUT"] + 2.0)
STUB["delay"] = 0.0

# 9e. the primary lane alone is enough, but only when it is genuinely up
LANE_ONLINE["bg"] = False
LANE_ONLINE["primary"] = True
STUB["answer"] = "Primary lane answer."
r = POST("/api/dictation/finish", {"text": "um, hello there"})
check("primary lane used when bg is down", r["text"], "Primary lane answer.")
LANE_ONLINE["primary"] = False

# ---------------------------------------------------------------------------
# 10. status heartbeat + the GET payload
# ---------------------------------------------------------------------------
print("status and payload")
POST("/api/dictation/settings", {"cleanup": "rules"})
POST("/api/dictation/status", {"running": True, "mic": "granted",
                               "accessibility": "denied", "speech": "banana",
                               "engine": "SpeechAnalyzer", "hotkey": "Right Option",
                               "version": "1.2.4", "state": "idle"})
p = GET("/api/dictation")
check("running", p["running"], True)
check("mic", p["status"]["mic"], "granted")
check("accessibility", p["status"]["accessibility"], "denied")
check("unknown grant word normalised", p["status"]["speech"], "unknown")
truthy("age is small", p["age_s"] is not None and p["age_s"] < 5)
truthy("today has counts", p["today"]["dictations"] > 0)
truthy("recent list is populated", len(p["recent"]) > 0)
check("no text stored while keep_history is off",
      any("text" in r for r in p["recent"]), False)
truthy("install block present", p["install"]["build_cmd"] == "bash app/build-dictation.sh")

# a stale heartbeat is not "running"
store = os.path.join(DATA, "dictation.json")
with open(store) as f:
    raw = json.load(f)
raw["status"]["ts"] = time.time() - 600
with open(store, "w") as f:
    json.dump(raw, f)
p = GET("/api/dictation")
check("stale heartbeat is not running", p["running"], False)
check("stale is flagged", p["stale"], True)

# ---------------------------------------------------------------------------
# 11. history privacy
# ---------------------------------------------------------------------------
print("history privacy")
POST("/api/dictation/settings", {"keep_history": True})
POST("/api/dictation/finish", {"text": "my private sentence", "app_name": "Notes"})
p = GET("/api/dictation")
truthy("text kept while the toggle is on",
       any(r.get("text") for r in p["recent"]))
POST("/api/dictation/settings", {"keep_history": False})
p = GET("/api/dictation")
check("turning it off drops the text already stored",
      any("text" in r for r in p["recent"]), False)
truthy("but the counts survive", p["today"]["dictations"] > 0)

with open(store) as f:
    on_disk = json.dumps(json.load(f))
check("no dictated text left on disk", "my private sentence" in on_disk, False)
check("store is 0600", oct(os.stat(store).st_mode & 0o777), "0o600")

# history_days = 0 keeps nothing at all
POST("/api/dictation/settings", {"history_days": 0})
POST("/api/dictation/finish", {"text": "gone in a moment"})
p = GET("/api/dictation")
check("history_days 0 keeps no rows", len(p["recent"]), 0)
POST("/api/dictation/settings", {"history_days": 7})

# ---------------------------------------------------------------------------
# 12. the module must not have poisoned the shared datetime global
# ---------------------------------------------------------------------------
print("aux discipline")
import datetime as _dt
check("datetime global untouched (CLAUDE.md)",
      hasattr(NS.get("_dct_datetime", _dt), "datetime"), True)
check("no bare `datetime` name introduced", "datetime" in NS, False)
check("routes registered",
      sorted(list(GET_ROUTES) + list(POST_ROUTES)),
      sorted(["/api/dictation", "/api/dictation/settings", "/api/dictation/install",
              "/api/dictation/finish", "/api/dictation/settings",
              "/api/dictation/status"]))

srv.shutdown()
shutil.rmtree(SANDBOX, ignore_errors=True)

print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
