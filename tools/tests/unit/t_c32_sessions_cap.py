"""C32 — the sidebar listed at most 30 conversations and nothing said more
existed; everything older was invisible.

Server half: GET /api/sessions still returns the newest 30 (pinned first) but
now carries `total`, and ?limit=all / ?limit=N lift or set the cap. UI half:
the real renderChatSide() from index.html (headless, node) shows a
"Show all N conversations" button when total > rows, and that button
re-renders with ?limit=all.

Real server.py import + the real Handler on a free loopback port, throwaway
HOME. No model, no ~/.hermes.
"""
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-c32-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time()
N = 37
for i in range(N):
    p = os.path.join(CH, "chat-2026-10-%02d-c%04d.json" % (1 + i % 28, i))
    json.dump({"title": "conversation %d" % i, "pinned": i in (0, 1),
               "messages": [{"role": "user", "text": "hello %d" % i, "ts": NOW - i}]}, open(p, "w"))
    os.utime(p, (NOW - 1000 * i, NOW - 1000 * i))      # c0000 newest ... c0036 oldest

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server._ix_cal_lines = lambda: []

from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:300]))


def get(path):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                               headers={"Host": "127.0.0.1:%d" % PORT})
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode())


UI = r"""
const fs = require("fs"), vm = require("vm"), path = require("path");
const { El, install, extract } = require(process.argv[1]);      // node -e: argv[1] is the first arg
const HTML = fs.readFileSync(process.argv[2], "utf8");
const ROWS = JSON.parse(process.argv[3]);
const G = { console, Promise, JSON, Math, String, Number, Array, Object, Error, setImmediate };
install(G);
const els = {};
G.$ = id => els[id] || (els[id] = new El("div"));
els["p-model"] = new El("span"); els["p-model"].className = "pdot";
Object.assign(G, { esc: require(require("path").resolve(process.argv[2], "..", "..", "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc"), cvTerm: "", cvLast: [], cvPaint: () => {},
  CV_IC_CHAT: "", CV_IC_PINNED: "", CV_IC_BRANCH: "", CV_IC_PIN: "", CV_IC_PEN: "", CV_IC_TRASH: "",
  SPARK_IC: "", QUICK_ACTIONS: [], session: "nope", revealStagger: () => {}, renderAIInfo: () => {},
  cvActBtn: () => new El("button"), cvMeta: async () => true, loadSessions: () => {}, cvRenameRow: () => {},
  cvDeleteRow: () => {}, loadHistory: async () => {}, setChatMode: () => {}, sendMsg: () => {},
  kbdButton: el => el });
G.__respond = c => {
  const all = /limit=all/.test(c.url);
  return { status: 200, body: { sessions: all ? ROWS : ROWS.slice(0, 30), total: ROWS.length } };
};
let src = extract(HTML, /\nasync function renderChatSide\(\)\{/);
const pre = /\nlet csAll=false;[^\n]*/.exec(HTML);
// the page's own no-runtime motion API (ANIM_NULL + HA()), verbatim: the
// sidebar keys each row's entrance on HA().once (E132)
const a0 = HTML.indexOf("\nconst ANIM_NULL="), tailHA = "\nconst HA=()=>window.HermesAnim||ANIM_NULL;";
const a1 = HTML.indexOf(tailHA, a0);
const anim = a0 >= 0 && a1 > a0 ? HTML.slice(a0 + 1, a1 + tailHA.length) + "\n" : "";
G.window = G;
vm.createContext(G);
vm.runInContext("var cvTerm='';" + anim + (pre ? pre[0] : "") + src, G);
(async () => {
  const out = {};
  await G.renderChatSide(); await G.flush();
  const cv = G.$("cs-convos");
  const items = () => cv.all().filter(e => e.classList.contains("cs-item"));
  const more = () => cv.all().filter(e => e.classList.contains("cs-more"))[0];
  out.first_url = G.__fetches[0] && G.__fetches[0].url;
  out.first_rows = items().filter(e => e.textContent !== "(new conversation)").length;
  out.more_text = more() ? more().textContent : null;
  if (more()) { more().click(); await G.flush(); await G.flush(); }
  out.second_url = G.__fetches[1] && G.__fetches[1].url;
  out.second_rows = items().filter(e => e.textContent !== "(new conversation)").length;
  out.more_after = more() ? more().textContent : null;
  console.log("C32JSON " + JSON.stringify(out));
})().catch(e => { console.log("C32JSON " + JSON.stringify({ error: String(e && e.stack || e) })); });
"""

try:
    print("\n=== server: GET /api/sessions ===")
    d = get("/api/sessions")
    rows = d.get("sessions") or []
    ok("default view is still the 30 rows the sidebar always showed", len(rows) == 30, len(rows))
    ok("... and says how many exist (total)", d.get("total") == N, d.get("total"))
    ok("pinned rows still lead", [r["pinned"] for r in rows[:2]] == [True, True], rows[:2])
    d_all = get("/api/sessions?limit=all")
    ok("?limit=all returns every conversation", len(d_all.get("sessions") or []) == N
       and d_all.get("total") == N, (len(d_all.get("sessions") or []), d_all.get("total")))
    ok("?limit=all keeps the same order the capped view uses",
       [r["id"] for r in d_all["sessions"][:30]] == [r["id"] for r in rows])
    ok("the oldest conversation is reachable", any(r["id"].endswith("c0036") for r in d_all["sessions"]))
    ok("?limit=5 returns 5", len(get("/api/sessions?limit=5")["sessions"]) == 5)
    ok("a junk limit falls back to the default 30", len(get("/api/sessions?limit=-3x")["sessions"]) == 30)
    try:
        ok("list_sessions(None) is uncapped", len(server.list_sessions(None)) == N)
    except TypeError as e:
        ok("list_sessions(None) is uncapped", False, e)

    print("\n=== UI: renderChatSide() offers Show all ===")
    # the sidebar's own fixture, independent of the server under test
    ROWS_ALL = [{"id": "chat-x-%04d" % i, "title": "conversation %d" % i,
                 "pinned": i < 2, "updated": NOW - i} for i in range(N)]
    p = subprocess.run(["node", "-e", UI, os.path.join(SP, "_fakedom.js"),
                        os.path.join(REPO, "index.html"), json.dumps(ROWS_ALL)],
                       capture_output=True, text=True, timeout=60)
    line = next((l for l in p.stdout.splitlines() if l.startswith("C32JSON ")), None)
    ui = json.loads(line[8:]) if line else {"error": p.stderr[-800:]}
    ok("node ran the real renderChatSide", "error" not in ui, ui.get("error"))
    ok("first render asks for the default (capped) list", ui.get("first_url") == "/api/sessions", ui)
    ok("first render shows the 30 rows", ui.get("first_rows") == 30, ui)
    ok("a 'Show all %d conversations' button appears" % N,
       ui.get("more_text") == "Show all %d conversations" % N, ui)
    ok("clicking it re-renders with ?limit=all", ui.get("second_url") == "/api/sessions?limit=all", ui)
    ok("... showing all %d rows" % N, ui.get("second_rows") == N, ui)
    ok("... and the button is gone", "error" not in ui and ui.get("more_after") is None, ui)
except Exception as e:
    import traceback
    traceback.print_exc()
    ok("suite ran to the end", False, repr(e))
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)
    print("TESTS %d passed %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
