#!/usr/bin/env python3
"""C06 (docs/audits/2026-10-21-codebase-audit.md): hub_prewarm_loop and the
60 s hub poll called the Needs-you provider with mark=True, recording
sightings nobody saw and deflating now_precision.

BEFORE: WIDGETS["needsyou"].provider was w_needsyou -> _ny_payload() with the
default mark=True.  hub_prewarm_loop submits every widget provider every
45 s whether or not a window is open, and /api/hub runs them all again, so
every `now` row was "shown" within 45 s of existing — with nobody looking,
there is never an act row, and the precision counter drifts toward 0.

AFTER: the provider never records.  The hub widget (aux_needsyou.js) posts
the ids it rendered to POST /api/needsyou/seen only while the page is
visible and the card is laid out (and again when the page becomes visible);
the server accepts only ids in its own cached payload, in its own bucket.
GET /api/needsyou without mark=0 and the pop-out still record.

Python half: the real provider driven through server.py's real _run_provider
(ast-extracted), plus the new route.  JS half: the real renderer in node.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()
E.write_messages(data, [
    E.convo("+15550000071", "can you send it by 5pm?", NOW - 60, name="Ana Lee"),
    E.convo("+15550000072", "are you free thursday?", NOW - 60, name="Ben Ode"),
], generated_at=NOW)
g["CHAT_JOBS"] = {"j1": {"id": "j1", "state": "approval", "done": False,
                         "approval": {"command": "rm -rf /tmp/x"}, "ts": NOW}}
p = g["_ny_build"](NOW)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
E.check("fixture has Now rows", len(p["now"]) >= 1, [i["id"] for i in p["now"]])

src = open(os.path.join(E.DASH, "server.py")).read()
fn = next(n for n in ast.parse(src).body
          if isinstance(n, ast.FunctionDef) and n.name == "_run_provider")
srv = {"_hub_last_good": {}}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "server.py", "exec"), srv)


def shown():
    return g["_ny_load"]()["shown"]


print("=== C06: the hub provider never records a sighting ===")
prov = g["WIDGETS"]["needsyou"]["provider"]
for _ in range(3):                       # three prewarm ticks / hub polls
    d = srv["_run_provider"]("needsyou", prov)
E.check("the provider still returns the payload",
        isinstance(d, dict) and d.get("ok") is True, d)
E.check("no sighting after prewarm/hub calls", shown() == {}, shown())
E.check("now_precision denominator untouched",
        g["_ny_metrics"](None, time.time())["now_shown"] == 0)

print("=== C06: POST /api/needsyou/seen records what the widget showed ===")
seen = g["POST_ROUTES"].get("/api/needsyou/seen")
E.check("route registered", callable(seen))
if callable(seen):
    r = seen(E.Ctx(body={"ids": "message:x"}))
    E.check("non-list ids -> 400", isinstance(r, tuple) and r[1] == 400, r)
    r = seen(E.Ctx(body={"ids": ["message:not-in-the-payload", "x" * 999]}))
    E.check("ids the server never served are ignored",
            r.get("marked") == 0 and shown() == {}, (r, shown()))
    now_id = p["now"][0]["id"]
    r = seen(E.Ctx(body={"ids": [now_id]}))
    E.check("a shown Now row is recorded", r.get("marked") == 1, r)
    rows = shown()
    E.check("...in the server's bucket, once",
            len(rows) == 1 and list(rows.values())[0]["bucket"] == "now", rows)
    seen(E.Ctx(body={"ids": [now_id]}))
    E.check("a repeat beacon adds nothing", len(shown()) == 1, shown())

print("=== C06: the explicit client paths still record ===")
g2, d2 = E.load()
E.write_messages(d2, [E.convo("+15550000073", "can you call me?", NOW - 60,
                              name="Cy")], generated_at=NOW)
g2["_ny_cache"]["payload"] = g2["_ny_build"](NOW)
g2["_ny_cache"]["at"] = time.time()
g2["EXPANDERS"]["needsyou"]()
E.check("opening the pop-out records", len(g2["_ny_load"]()["shown"]) >= 1)

print("=== C06: the widget posts only what is on screen (node) ===")
JS = r"""
// esc() is the REAL one, cut out of index.html's inline script (E138: a stand-in
// tests a different escaping from the one the dashboard ships)
globalThis.esc = require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
globalThis.relTime = function () { return "1m ago"; };
globalThis.RENDER = {}; globalThis.EXPAND_RENDER = {}; globalThis.WICONS = {};
globalThis.window = globalThis;
const listeners = {};
globalThis.document = {
  visibilityState: "visible",
  getElementById: function () { return null; },
  createElement: function () { return {}; },
  head: { appendChild: function () {} },
  addEventListener: function (k, f) { listeners[k] = f; }
};
globalThis.setTimeout = function (f) { f(); return 0; };
const posts = [];
globalThis.fetch = function (url, opt) {
  posts.push({ url: url, body: opt && opt.body ? JSON.parse(opt.body) : null });
  return Promise.resolve({ ok: true, json: function () { return {}; } });
};
const fs = require("fs");
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
const d = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
function el(laidOut) {
  return { innerHTML: "", textContent: "",
           querySelectorAll: function () { return []; },
           getClientRects: function () { return laidOut ? [{}] : []; } };
}
const out = {};
RENDER.needsyou(el(true), d, el(true));
out.visible = posts.slice();
RENDER.needsyou(el(true), d, el(true));
out.repeat = posts.length;
posts.length = 0;
const d2 = JSON.parse(JSON.stringify(d)); d2.today = [];
document.visibilityState = "hidden";
const hidden = el(true);
RENDER.needsyou(hidden, d2, el(true));
out.hidden = posts.length;
document.visibilityState = "visible";
if (listeners.visibilitychange) listeners.visibilitychange();
out.revealed = posts.slice();
posts.length = 0;
const d3 = JSON.parse(JSON.stringify(d)); d3.later = [];
RENDER.needsyou(el(false), d3, el(true));
out.detached = posts.length;
process.stdout.write(JSON.stringify(out));
"""
tmp = tempfile.mkdtemp(prefix="ny-c06-")
E._HOMES.append(tmp)
hj, cj = os.path.join(tmp, "h.js"), os.path.join(tmp, "d.json")
with open(hj, "w") as f:
    f.write(JS)
payload = {"ok": True, "generated_at": NOW, "metrics": {}, "sources": {},
           "now": [{"id": "approval:j1", "source": "approval", "title": "A"}],
           "today": [{"id": "message:+1", "source": "message", "sender": "B"}],
           "later": [{"id": "watchtower:c", "source": "watchtower", "title": "C"}]}
with open(cj, "w") as f:
    json.dump(payload, f)
r = subprocess.run(["node", hj, os.path.join(E.DASH, "aux_needsyou.js"), cj],
                   capture_output=True, text=True, timeout=60)
try:
    o = json.loads(r.stdout)
except ValueError:
    o = None
    E.check("renderer ran", False, r.stderr[-400:])
if o:
    seen_posts = [x for x in o["visible"] if x["url"] == "/api/needsyou/seen"]
    E.check("a visible, laid-out widget posts one beacon", len(seen_posts) == 1,
            o["visible"])
    if seen_posts:
        E.check("...carrying every row it rendered",
                sorted(seen_posts[0]["body"]["ids"])
                == ["approval:j1", "message:+1", "watchtower:c"], seen_posts)
    E.check("re-rendering the same rows does not re-post", o["repeat"] == 1,
            o["repeat"])
    E.check("a hidden page posts nothing", o["hidden"] == 0, o["hidden"])
    E.check("becoming visible posts what was rendered meanwhile",
            len(o["revealed"]) == 1
            and sorted(o["revealed"][0]["body"]["ids"])
            == ["approval:j1", "watchtower:c"], o["revealed"])
    E.check("a card that is not laid out posts nothing", o["detached"] == 0,
            o["detached"])

sys.exit(E.finish())
