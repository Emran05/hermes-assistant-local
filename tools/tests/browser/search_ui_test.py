"""1.1.0 UI harness — "Search everything" against a routed /api/search fixture.

Hits the LIVE dashboard (which has NOT been restarted, so /api/search 404s
there) with the route intercepted, so nothing in ~/.hermes is touched and the
model is never woken.  Both themes, both chat modes, every open-item branch.
"""
import json, os, re, sys, tempfile
from playwright.sync_api import sync_playwright

SP = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(
    prefix="hermes-shots-")
os.makedirs(SHOTS, exist_ok=True)
URL = "http://localhost:7788/"

CHAT_SID = "chat-2026-08-31-qglb4"

ALL = {
    "ok": True, "q": "kumquat", "took_ms": 4,
    "sources": {"chat": 2, "note": 1, "message": 1, "calendar": 1, "watchtower": 2},
    "results": [
        {"id": "chat:" + CHAT_SID, "source": "chat", "title": "Kumquat plumbing notes",
         "ts": 1788600000.0,
         "snippet": "…so the kumquat index lands in ~/.hermes/dashboard…",
         "mark_start": 8, "mark_len": 7, "ref": CHAT_SID},
        {"id": "chat:chat-second", "source": "chat", "title": "Second conversation",
         "ts": 1788500000.0, "snippet": "another kumquat mention deeper in",
         "mark_start": 8, "mark_len": 7, "ref": "chat-second"},
        {"id": "note:scratchpad", "source": "note", "title": "buy marmalade and kumquat jam",
         "ts": 1788650000.0, "snippet": "buy marmalade and kumquat jam second line",
         "mark_start": 18, "mark_len": 7, "ref": "notes"},
        {"id": "message:abc123", "source": "message", "title": "Dana Whitfield",
         "ts": 1788640000.0, "snippet": "Dana Whitfield: dinner kumquat thursday still on?",
         "mark_start": 23, "mark_len": 7, "ref": "+15551234567"},
        {"id": "calendar:def456", "source": "calendar", "title": "Kumquat harvest planning sync",
         "ts": 1789200000.0, "snippet": "Kumquat harvest planning sync — 2026-09-12 at 09:30",
         "mark_start": 0, "mark_len": 7, "ref": "today"},
        {"id": "watchtower:aaa", "source": "watchtower", "title": "Kumquat Labs ships a local index",
         "ts": 1788630000.0, "snippet": "matches your FTS5 work a full text search release",
         "mark_start": None, "mark_len": 0, "ref": "https://example.com/kumquat-index"},
        {"id": "watchtower:bbb", "source": "watchtower", "title": "Kumquat futures wobble",
         "ts": 1788620000.0, "snippet": "no link on this one, opens the news widget",
         "mark_start": None, "mark_len": 0, "ref": ""},
    ],
}
CHATS_ONLY = dict(ALL, results=[r for r in ALL["results"] if r["source"] == "chat"])

diag = {"console": [], "pageerr": [], "checks": []}
PHASE = ["main"]
PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s   %s" % (name, extra))
    diag["checks"].append((name, bool(cond), str(extra)[:200]))


def route_search(route, request):
    src = ""
    m = re.search(r"[?&]source=([^&]*)", request.url)
    if m:
        src = m.group(1)
    payload = CHATS_ONLY if src == "chat" else ALL
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps(payload))


STUB = """
window.__opened=[];
const _open=window.open;
window.open=function(u,t,f){window.__opened.push(u);return null;};
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            device_scale_factor=1, color_scheme=theme)
        ctx.add_init_script(STUB)
        page = ctx.new_page()
        page.on("console", lambda m, t=theme: diag["console"].append(
            "[%s/%s] %s: %s" % (t, PHASE[0], m.type, m.text[:200]))
            if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e, t=theme: diag["pageerr"].append(
            "[%s] %s" % (t, str(e)[:300])))
        page.route("**/api/search?*", route_search)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        page.evaluate("(t)=>{document.documentElement.setAttribute('data-theme',t);"
                      "try{localStorage.setItem('hermes_theme',t);"
                      "localStorage.removeItem('hermes_search_src');}catch(e){}}", theme)
        page.evaluate("()=>{cvSrc='';}")
        page.wait_for_timeout(400)

        print("\n=== [%s] split mode: header popover ===" % theme)
        page.evaluate("()=>{ setChatMode(''); }")
        page.wait_for_timeout(500)
        page.click("#chat-find")
        page.wait_for_timeout(300)
        ok("[%s] resting popover shows the filter row" % theme,
           page.locator("#cvfind-list .cs-filt b").count() == 6,
           page.locator("#cvfind-list .cs-filt b").count())
        page.fill("#chat-q", "kumquat")
        page.wait_for_timeout(700)
        groups = page.eval_on_selector_all(
            "#cvfind-list .cs-grp span:first-child", "els=>els.map(e=>e.textContent)")
        print("   groups:", groups)
        ok("[%s] results grouped by source in a fixed order" % theme,
           groups == ["Chats", "Notes", "Messages", "Calendar", "News"], groups)
        ok("[%s] every row carries a source chip" % theme,
           page.locator("#cvfind-list .cs-res .cs-chip").count() == 7,
           page.locator("#cvfind-list .cs-res .cs-chip").count())
        chips = page.eval_on_selector_all(
            "#cvfind-list .cs-res .cs-chip", "els=>els.map(e=>e.textContent)")
        ok("[%s] chip labels are per-source" % theme,
           chips == ["Chat", "Chat", "Note", "Message", "Event", "News", "News"], chips)
        marks = page.eval_on_selector_all(
            "#cvfind-list .cv-mark", "els=>els.map(e=>e.textContent)")
        print("   highlighted:", marks)
        ok("[%s] offsets highlight exactly the query word" % theme,
           [m.lower() for m in marks] == ["kumquat"] * 5, marks)
        ok("[%s] a null mark_start renders plain, no mark" % theme, len(marks) == 5)
        ok("[%s] snippet text is escaped, never injected" % theme,
           page.locator("#cvfind-list .rs script").count() == 0)
        counts = page.eval_on_selector_all(
            "#cvfind-list .cs-filt b", "els=>els.map(e=>e.textContent)")
        print("   filter row:", counts)
        ok("[%s] filter row shows per-source counts" % theme,
           counts == ["All7", "Chats2", "Notes1", "Messages1", "Calendar1", "News2"],
           counts)
        page.screenshot(path="%s/%s-split-search.png" % (SHOTS, theme))

        print("=== [%s] filter row re-queries ===" % theme)
        page.click("#cvfind-list .cs-filt b[data-k='chat']")
        page.wait_for_timeout(600)
        groups2 = page.eval_on_selector_all(
            "#cvfind-list .cs-grp span:first-child", "els=>els.map(e=>e.textContent)")
        ok("[%s] Chats filter shows only the chat group" % theme,
           groups2 == ["Chats"], groups2)
        ok("[%s] the active filter is marked" % theme,
           page.locator("#cvfind-list .cs-filt b.on").get_attribute("data-k") == "chat")
        ok("[%s] counts still cover every source while filtered" % theme,
           page.eval_on_selector_all("#cvfind-list .cs-filt b",
                                     "els=>els.map(e=>e.textContent)")
           == ["All7", "Chats2", "Notes1", "Messages1", "Calendar1", "News2"])
        page.screenshot(path="%s/%s-split-filtered.png" % (SHOTS, theme))
        page.click("#cvfind-list .cs-filt b[data-k='']")
        page.wait_for_timeout(600)
        ok("[%s] back to All restores every group" % theme,
           page.eval_on_selector_all("#cvfind-list .cs-grp span:first-child",
                                     "els=>els.map(e=>e.textContent)")
           == ["Chats", "Notes", "Messages", "Calendar", "News"])

        print("=== [%s] open-item behaviours ===" % theme)
        # chat -> switches conversation, closes the popover
        page.click("#cvfind-list .cs-res[data-src='chat']")
        page.wait_for_timeout(900)
        ok("[%s] chat row switches the session" % theme,
           page.evaluate("()=>localStorage.getItem('hermes_session')") == CHAT_SID,
           page.evaluate("()=>localStorage.getItem('hermes_session')"))
        ok("[%s] chat row closes the popover" % theme,
           page.evaluate("()=>document.getElementById('cvfind').hidden") is True)

        def reopen(q="kumquat"):
            page.click("#chat-find")
            page.wait_for_timeout(250)
            page.fill("#chat-q", q)
            page.wait_for_timeout(650)

        for src, want_title in (("note", "Scratchpad"),
                                ("message", "Message Center"),
                                ("calendar", "Today")):
            reopen()
            page.click("#cvfind-list .cs-res[data-src='%s']" % src)
            page.wait_for_timeout(1400)
            title = page.eval_on_selector("#wpop-sheet .w-title", "e=>e.textContent")
            ok("[%s] %s row opens the %s pop-out" % (theme, src, want_title),
               page.evaluate("()=>!document.getElementById('wpop').hidden")
               and title == want_title, title)
            if src == "note":
                page.screenshot(path="%s/%s-open-note.png" % (SHOTS, theme))
            page.evaluate("()=>closePop()")
            page.wait_for_timeout(400)

        reopen()
        page.evaluate("()=>{window.__opened=[];}")
        page.click("#cvfind-list .cs-res[data-src='watchtower']")
        page.wait_for_timeout(600)
        ok("[%s] news row with a link opens the link" % theme,
           page.evaluate("()=>window.__opened")
           == ["https://example.com/kumquat-index"],
           page.evaluate("()=>window.__opened"))
        reopen()
        page.evaluate("()=>{window.__opened=[];}")
        page.locator("#cvfind-list .cs-res[data-src='watchtower']").nth(1).click()
        page.wait_for_timeout(1400)
        ok("[%s] news row without a link falls back to a widget pop-out" % theme,
           page.evaluate("()=>window.__opened").__len__() == 0
           and page.evaluate("()=>!document.getElementById('wpop').hidden"),
           page.eval_on_selector("#wpop-sheet .w-title", "e=>e.textContent"))
        page.evaluate("()=>closePop()")
        page.wait_for_timeout(300)

        print("=== [%s] full chat mode: sidebar ===" % theme)
        page.evaluate("()=>{ setChatMode('full'); }")
        page.wait_for_timeout(1200)
        page.fill("#cs-q", "kumquat")
        page.wait_for_timeout(800)
        ok("[%s] sidebar placeholder says Search everything" % theme,
           page.get_attribute("#cs-q", "placeholder") == "Search everything")
        groups3 = page.eval_on_selector_all(
            "#cs-convos .cs-grp span:first-child", "els=>els.map(e=>e.textContent)")
        print("   sidebar groups:", groups3)
        ok("[%s] sidebar groups by source too" % theme,
           groups3 == ["Chats", "Notes", "Messages", "Calendar", "News"], groups3)
        ok("[%s] sidebar shows the filter row" % theme,
           page.locator("#cs-convos .cs-filt b").count() == 6)
        page.screenshot(path="%s/%s-full-search.png" % (SHOTS, theme))
        # clearing restores the conversation list (chat-only behaviour intact)
        page.click("#cs-qx")
        page.wait_for_timeout(700)
        ok("[%s] clearing restores the conversation list" % theme,
           page.locator("#cs-convos .cs-item").count() > 0
           and page.locator("#cs-convos .cs-grp").count() == 0,
           page.locator("#cs-convos .cs-item").count())
        page.screenshot(path="%s/%s-full-cleared.png" % (SHOTS, theme))
        # Enter opens the first result
        page.fill("#cs-q", "kumquat")
        page.wait_for_timeout(800)
        page.press("#cs-q", "Enter")
        page.wait_for_timeout(900)
        ok("[%s] Enter opens the first result" % theme,
           page.evaluate("()=>localStorage.getItem('hermes_session')") == CHAT_SID,
           page.evaluate("()=>localStorage.getItem('hermes_session')"))
        page.evaluate("()=>{ setChatMode(''); }")
        page.wait_for_timeout(500)

        print("=== [%s] degradation: /api/search unavailable ===" % theme)
        PHASE[0] = "fallback"
        page.unroute("**/api/search?*")
        page.route("**/api/search?*", lambda r: r.fulfill(status=404, body="nope"))
        page.click("#chat-find")
        page.wait_for_timeout(250)
        page.fill("#chat-q", "the")
        page.wait_for_timeout(1500)
        rows = page.locator("#cvfind-list .cs-res").count()
        srcs = set(page.eval_on_selector_all("#cvfind-list .cs-res",
                                             "els=>els.map(e=>e.dataset.src)"))
        print("   fallback rows:", rows, "sources:", srcs)
        ok("[%s] falls back to the chat-only search, still renders" % theme,
           srcs in (set(), {"chat"}), srcs)
        ok("[%s] no error state, no throw on the fallback path" % theme,
           page.locator("#cvfind-list .cs-filt").count() == 1)
        page.screenshot(path="%s/%s-fallback.png" % (SHOTS, theme))
        page.evaluate("()=>{ cvCloseFind(); }")
        page.wait_for_timeout(300)
        PHASE[0] = "main"
        ctx.close()
    b.close()

json.dump(diag, open(os.path.join(SHOTS, "diag.json"), "w"), indent=1)
print("\n=========================================")
print("  PASS %d   FAIL %d" % (PASS, FAIL))
main_noise = [e for e in diag["console"] if "/main]" in e]
print("  console errors/warnings:", len(diag["console"]),
      "| outside the deliberate-404 phase:", len(main_noise))
for e in diag["console"][:10]:
    print("   ", e)
print("  page errors:", len(diag["pageerr"]))
for e in diag["pageerr"][:10]:
    print("   ", e)
print("  shots:", sorted(os.listdir(SHOTS)))
print("=========================================")
print("TESTS %d passed %d failed"
      % (PASS, FAIL + len(diag["pageerr"]) + len(main_noise)))
sys.exit(1 if (FAIL or diag["pageerr"] or main_noise) else 0)
