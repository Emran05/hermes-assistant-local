"""B07 — removing a widget must stick across restarts, and a restart must never
drop other widgets from the saved layout.

The bug: four aux modules (claude_usage, foryou, messages, needsyou) re-added
their widget at EVERY boot whenever it was absent, and did it with
get_layout() + save_layout(). get_layout() filters the order to ids already in
WIDGETS, and aux modules load one by one in sorted order, so the first inject
(aux_claude_usage) wrote back a layout without foryou / needsyou / framing /
trends: foryou + needsyou came back at the front, framing + trends were gone.

Each "boot" below is a fresh python3 process that execs the REAL server.py
(the whole aux chain) against a throwaway HOME — exactly what a dashboard
restart does to layout.json — then optionally drives the real /api/layout
handler logic. No model, no network (the offline guard is inherited), no
~/.hermes.
"""
import json, os, shutil, subprocess, sys, tempfile

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
DASH = os.path.join(ROOT, "dashboard")

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:400]))


CHILD = r"""
import json, os, sys
dash, ops = sys.argv[1], json.loads(sys.argv[2])
G = {"__name__": "b07_boot", "__file__": os.path.join(dash, "server.py")}
sys.path.insert(0, dash)
exec(compile(open(os.path.join(dash, "server.py")).read(), "server.py", "exec"), G)
out = {"after_boot": json.load(open(G["LAYOUT_FILE"])), "widgets": sorted(G["WIDGETS"]),
       "view": G["get_layout"](), "results": []}
# drive the real POST /api/layout code path (Handler.do_POST) without a socket
import io
class FakeH(G["Handler"]):
    def __init__(self, body):
        self.path = "/api/layout"; self._b = json.dumps(body).encode()
        self.headers = {"Content-Length": str(len(self._b)), "Host": "127.0.0.1"}
        self.rfile = io.BytesIO(self._b); self.sent = None
    def _guard(self): return True
    def _json(self, obj, status=200): self.sent = (status, obj)
for op in ops:
    if "__save_layout__" in op:          # the config-import path (aux_config)
        G["save_layout"]({"order": op["__save_layout__"]})
        out["results"].append([200, {"view": G["get_layout"]()["order"]}])
        continue
    h = FakeH(op); h.do_POST(); out["results"].append(h.sent)
out["final"] = json.load(open(G["LAYOUT_FILE"]))
print("B07JSON " + json.dumps(out))
"""


def boot(home, ops=()):
    env = dict(os.environ, HOME=home)
    p = subprocess.run([sys.executable, "-c", CHILD, DASH, json.dumps(list(ops))],
                       capture_output=True, text=True, env=env, timeout=120)
    for line in p.stdout.splitlines():
        if line.startswith("B07JSON "):
            return json.loads(line[8:])
    raise RuntimeError("boot failed rc=%s\n%s\n%s" % (p.returncode, p.stdout[-800:], p.stderr[-1500:]))


def home_with(layout=None):
    h = tempfile.mkdtemp(prefix="hermes-b07-")
    d = os.path.join(h, ".hermes", "dashboard")
    os.makedirs(os.path.join(d, "chats"), exist_ok=True)
    json.dump({"master": {"briefings": False, "news": False}}, open(os.path.join(d, "watchtower.json"), "w"))
    if layout is not None:
        with open(os.path.join(d, "layout.json"), "w") as f:
            f.write(layout if isinstance(layout, str) else json.dumps(layout))
    return h


def order(lay):
    return (lay or {}).get("order")


HOMES = []
try:
    print("\n=== 1. the audit's repro: one Remove, one restart (legacy layout.json) ===")
    before = ["needsyou", "foryou", "clock", "framing", "weather", "trends", "messages"]
    h = home_with({"order": before}); HOMES.append(h)
    r = boot(h)
    ok("the removed claude_usage does NOT come back", "claude_usage" not in order(r["after_boot"]), order(r["after_boot"]))
    ok("framing + trends (modules that load after the injectors) are kept",
       all(w in order(r["after_boot"]) for w in ("framing", "trends")), order(r["after_boot"]))
    ok("the owner's order is byte-for-byte unchanged", order(r["after_boot"]) == before, order(r["after_boot"]))

    print("\n=== 2. fresh install, then Remove + restart ===")
    h = home_with(None); HOMES.append(h)
    r = boot(h)
    o = order(r["after_boot"])
    ok("fresh install: needsyou first, foryou second", o[:2] == ["needsyou", "foryou"], o)
    ok("fresh install: claude_usage + messages present once",
       o.count("claude_usage") == 1 and o.count("messages") == 1, o)
    ok("fresh install: every shown id is recorded as seen",
       set(o) <= set(r["after_boot"].get("seen") or []), r["after_boot"])
    r = boot(h, [{"op": "remove", "id": "claude_usage"}, {"op": "remove", "id": "foryou"},
                 {"op": "add", "id": "framing"}, {"op": "add", "id": "trends"}])
    ok("remove/add answer ok", all(x and x[0] == 200 and x[1].get("ok") for x in r["results"]), r["results"])
    r2 = boot(h)
    o2 = order(r2["after_boot"])
    ok("after a restart the removed claude_usage stays removed", "claude_usage" not in o2, o2)
    ok("after a restart the removed foryou stays removed", "foryou" not in o2, o2)
    ok("after a restart framing + trends are still there", "framing" in o2 and "trends" in o2, o2)
    ok("a restart changes nothing at all", o2 == order(r["final"]), (o2, order(r["final"])))
    r3 = boot(h, [{"op": "add", "id": "foryou"}])
    ok("re-adding by hand still works", "foryou" in order(r3["final"]), order(r3["final"]))
    ok("... and a second restart keeps it where it was put",
       order(boot(h)["after_boot"]) == order(r3["final"]))

    print("\n=== 3. a NEW widget id is still auto-added exactly once ===")
    h = home_with({"order": ["clock", "weather"], "seen": ["clock", "weather"]}); HOMES.append(h)
    r = boot(h)
    o = order(r["after_boot"])
    ok("never-seen front widgets go to the front", o[:2] == ["needsyou", "foryou"], o)
    ok("never-seen tail widgets are appended", o[-2:] == ["claude_usage", "messages"] or
       (o.index("claude_usage") > o.index("weather") and o.index("messages") > o.index("weather")), o)
    ok("the owner's own ids keep their relative order", [w for w in o if w in ("clock", "weather")] == ["clock", "weather"], o)

    print("\n=== 4. an id the catalog does not know this boot is kept on disk ===")
    h = home_with({"order": ["clock", "ghost_widget", "weather"],
                   "seen": ["clock", "ghost_widget", "weather"] + ["claude_usage", "foryou", "messages", "needsyou"]})
    HOMES.append(h)
    r = boot(h, [{"op": "remove", "id": "weather"}])
    ok("boot leaves an unknown id in layout.json", "ghost_widget" in order(r["after_boot"]), order(r["after_boot"]))
    ok("the shown view filters it out", "ghost_widget" not in order(r["view"]), r["view"])
    ok("a user op keeps it on disk too", "ghost_widget" in order(r["final"]), r["final"])
    ok("the op itself applied", "weather" not in order(r["final"]) and r["results"][0][1].get("order") == ["clock"],
       r["results"])

    print("\n=== 5. layout.json that is not a dict no longer breaks the hub ===")
    h = home_with("[1, 2, 3]"); HOMES.append(h)
    try:
        r = boot(h)
        ok("a list layout.json boots and falls back to the default", isinstance(order(r["view"]), list)
           and "clock" in order(r["view"]), r["view"])
    except RuntimeError as e:
        ok("a list layout.json boots and falls back to the default", False, e)

    print("\n=== 6. the /api/layout op contract is unchanged ===")
    h = home_with({"order": ["clock", "weather", "markets"],
                   "seen": ["clock", "weather", "markets", "claude_usage", "foryou", "messages", "needsyou"]})
    HOMES.append(h)
    r = boot(h, [{"op": "add", "id": "clock"}, {"op": "move", "id": "markets", "dir": "up"},
                 {"op": "set", "order": ["markets", "clock", "nope"]}, {"op": "bogus"}])
    res = r["results"]
    ok("add of an id already shown -> 400", res[0][0] == 400 and res[0][1] == {"ok": False}, res[0])
    ok("move up -> ok + new order", res[1][0] == 200 and res[1][1]["order"] == ["clock", "markets", "weather"], res[1])
    ok("set filters unknown ids", res[2][1]["order"] == ["markets", "clock"], res[2])
    ok("unknown op -> 400", res[3][0] == 400, res[3])
    ok("seen never shrinks (weather was set away but stays seen)", "weather" in r["final"]["seen"], r["final"])

    print("\n=== 7. save_layout (config import) keeps seen + unknown ids ===")
    h = home_with({"order": ["clock", "ghost_widget", "weather"],
                   "seen": ["clock", "ghost_widget", "weather", "claude_usage", "foryou", "messages", "needsyou"]})
    HOMES.append(h)
    r = boot(h, [{"__save_layout__": ["weather", "markets", "nope"]}])
    ok("save_layout applies the imported order", r["results"][0][1]["view"] == ["weather", "markets"], r["results"])
    ok("save_layout keeps an unknown stored id on disk", "ghost_widget" in order(r["final"]), r["final"])
    ok("save_layout never shrinks seen", "clock" in r["final"]["seen"] and "markets" in r["final"]["seen"], r["final"])
    r2 = boot(h)
    ok("... and a restart after an import re-adds nothing", order(r2["after_boot"]) == order(r["final"]),
       (order(r2["after_boot"]), order(r["final"])))
except Exception as e:
    import traceback
    traceback.print_exc()
    ok("suite ran to the end", False, repr(e))
finally:
    for h in HOMES:
        shutil.rmtree(h, ignore_errors=True)
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
