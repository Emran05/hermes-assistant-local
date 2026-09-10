#!/usr/bin/env python3
"""Browser harness for "Branch from here" (release 1.2.5).

Drives the LIVE dashboard against a scratch conversation this suite creates
itself (`chat-branchtest-src`) and removes again in `finally` — every file it
creates (the source and whatever branch id the click produces) is deleted at
the end, on every exit path (capture-and-restore: this suite's own state is
the only thing it ever touches, and none of it survives the run). NO prompt is
ever submitted, so no model is started: the only writes are the scratch
conversation and the branch itself.

  1. full chat mode, open the scratch conversation
  2. every bubble carries data-i and a "Branch from here" action
  3. click it on bubble 1 -> the branch opens with the truncated history
  4. the sidebar shows the fork line on the child and the count on the source
  5. screenshot; zero console errors throughout
"""
import json
import os
import sys
import tempfile
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("HERMES_DASH_BASE", "http://127.0.0.1:7788")
URL = BASE + "/"
SRC = "chat-branchtest-src"

SHOT_DIR = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(prefix="hermes-shots-")
os.makedirs(SHOT_DIR, exist_ok=True)
SHOT = os.path.join(SHOT_DIR, "branch-ui.png")

# The live dashboard runs as this same user; chats live under its real HOME.
# This suite creates exactly one conversation (SRC) plus whatever id branching
# it produces, and removes both in `finally` — a pre-existing conversation of
# the owner's is never read, written or at risk.
CHATS_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard", "chats")
SRC_PATH = os.path.join(CHATS_DIR, SRC + ".json")

FIXTURE_MESSAGES = [
    {"role": "user", "text": "What's on my calendar today?", "ts": 1000.0},
    {"role": "bot", "text": "Two events: standup at 10, dentist at 3.", "ts": 1001.0},
    {"role": "user", "text": "Move the dentist appointment to Friday", "ts": 1002.0},
    {"role": "bot", "text": "Moved the dentist appointment to Friday at 3pm.", "ts": 1003.0},
    {"role": "user", "text": "Thanks — also remind me to call the vet", "ts": 1004.0},
    {"role": "bot", "text": "Added a reminder to call the vet.", "ts": 1005.0},
]

FAILS, PASSES = [], [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def api(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.load(r)


def chat_path(session):
    return os.path.join(CHATS_DIR, session + ".json")


new_id = None
errors = []

try:
    os.makedirs(CHATS_DIR, exist_ok=True)
    if os.path.exists(SRC_PATH):
        os.remove(SRC_PATH)      # a leftover from an interrupted previous run
    with open(SRC_PATH, "w", encoding="utf-8") as f:
        json.dump({"messages": FIXTURE_MESSAGES, "title": "Branch scratch"}, f)

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 940})
        pg.on("console", lambda m: errors.append("%s: %s" % (m.type, m.text))
              if m.type in ("error",) else None)
        pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))

        pg.goto(URL, wait_until="networkidle")
        pg.evaluate("setChatMode('full')")
        pg.wait_for_selector(".cs-item", timeout=10000)

        # open the scratch conversation through the normal switch path. The
        # sidebar (renderChatSide) and the <select> (loadSessions) are two separate
        # fetches, so waiting for .cs-item is NOT enough — wait for the option.
        pg.wait_for_function(
            """(sid)=>!!document.querySelector(
                 '#sessions option[value="'+sid+'"]')""", arg=SRC, timeout=10000)
        pg.evaluate("""(sid)=>{const s=document.getElementById('sessions');
          s.value=sid;s.dispatchEvent(new Event('change'));}""", SRC)
        pg.wait_for_function(
            "()=>document.querySelectorAll('#msgs .bubble').length===6", timeout=10000)

        n = pg.eval_on_selector_all("#msgs .bubble", "els=>els.length")
        check("history rendered", n == 6, n)
        idx = pg.eval_on_selector_all("#msgs .bubble", "els=>els.map(e=>e.dataset.i)")
        check("every bubble carries its message index",
              idx == ["0", "1", "2", "3", "4", "5"], idx)
        acts = pg.eval_on_selector_all(
            "#msgs .bubble", "els=>els.map(e=>!!e.querySelector('.msg-branch'))")
        check("user AND assistant bubbles both get the action", all(acts), acts)
        check("the copy action still only exists on assistant bubbles",
              pg.eval_on_selector_all("#msgs .bubble.user .msg-copy", "e=>e.length") == 0
              and pg.eval_on_selector_all("#msgs .bubble.bot .msg-copy", "e=>e.length") == 3)

        # the action is quiet until you hover the bubble it belongs to
        rest = pg.eval_on_selector(
            "#msgs .bubble:nth-child(2) .msg-branch",
            "e=>getComputedStyle(e).opacity")
        pg.hover("#msgs .bubble:nth-child(2)")
        pg.wait_for_timeout(250)
        hov = pg.eval_on_selector(
            "#msgs .bubble:nth-child(2) .msg-branch",
            "e=>getComputedStyle(e).opacity")
        check("hidden at rest, shown on hover", rest == "0" and hov == "1",
              (rest, hov))
        check("the two actions do not overlap on a bot bubble",
              pg.eval_on_selector("#msgs .bubble:nth-child(2)", """e=>{
                const a=e.querySelector('.msg-branch').getBoundingClientRect();
                const b=e.querySelector('.msg-copy').getBoundingClientRect();
                return a.right<=b.left+0.5;}"""))

        # branch from bubble index 1 (the first assistant turn)
        pg.click("#msgs .bubble:nth-child(2) .msg-branch")
        pg.wait_for_function(
            "()=>document.querySelectorAll('#msgs .bubble').length===2", timeout=10000)
        check("the branch opened with the truncated history",
              pg.eval_on_selector_all("#msgs .bubble", "e=>e.length") == 2)

        new_id = pg.evaluate("()=>localStorage.getItem('hermes_session')")
        check("the new conversation is the active one",
              bool(new_id) and new_id != SRC, new_id)
        check("its bubbles are re-indexed from 0",
              pg.eval_on_selector_all("#msgs .bubble", "e=>e.map(x=>x.dataset.i)")
              == ["0", "1"])
        check("the toast names what is not carried",
              "tool results" in (pg.eval_on_selector(
                  "#toast", "e=>e.textContent") or "").lower(),
              pg.eval_on_selector("#toast", "e=>e.textContent"))

        pg.wait_for_timeout(400)
        fork = pg.eval_on_selector_all(".cs-item.forked .cs-fork span",
                                       "e=>e.map(x=>x.textContent)")
        check("the sidebar marks the branch with its origin",
              any("from Branch scratch" in t and "turn 1" in t for t in fork), fork)
        hasb = pg.eval_on_selector_all(".cs-item.hasbranch .cs-fork span",
                                       "e=>e.map(x=>x.textContent)")
        check("the sidebar marks the source with its branch count",
              any("1 branch" in t for t in hasb), hasb)
        check("the fork line uses the branch glyph, not text or an emoji",
              pg.eval_on_selector(".cs-item.forked .cs-fork svg", "e=>e.tagName") == "svg")
        check("the lineage line does not push the row off the rail",
              pg.eval_on_selector(".cs-item.forked",
                                  "e=>e.getBoundingClientRect().height<=64"),
              pg.eval_on_selector(".cs-item.forked", "e=>e.getBoundingClientRect().height"))

        pg.mouse.move(760, 700)          # resting state: the hover overlay would
        pg.wait_for_timeout(300)         # otherwise plate over the lineage line
        pg.screenshot(path=SHOT)
        print("  screenshot: %s" % SHOT)

        # the server agrees with the screen
        tree = api("/api/sessions/tree?session=" + new_id)
        check("tree route agrees", tree["ancestors"][0]["id"] == SRC
              and tree["ancestors"][0]["at_index"] == 2, tree.get("ancestors"))

        br.close()

    check("zero console errors", not errors, errors)

finally:
    # capture-and-restore: remove exactly what this run created, nothing else.
    for p in (SRC_PATH, chat_path(new_id) if new_id else None):
        if p and os.path.exists(p):
            os.remove(p)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
sys.exit(1 if FAILS else 0)
