#!/usr/bin/env python3
"""1.2.1 browser smoke — every aux settings card of the release really mounts.

Needs the RUNNING dashboard.  Read-only: it opens the Mind view and reads the
DOM, clicks nothing that writes, and never wakes a model.

The cards are mounted lazily by the `window.mindExtras` chain into the settings
panel each one names (`sec-models` / `sec-memory`), so "is the title somewhere
in document.innerText" is not a usable question — a collapsed panel renders no
text.  Ask the DOM for the card by id instead.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("HERMES_DASH_BASE", "http://127.0.0.1:7788")

# card id -> (settings panel it must land in, its heading)
CARDS = {
    "mind-extra-promptbudget": ("sec-models", "Prompt budget"),
    "mind-extra-toolbudget": ("sec-models", "Tool output budget"),
    "mind-extra-context": ("sec-models", "Context & compaction"),
    "mind-extra-memlayer": ("sec-memory", "What I know about you"),
}
SCRIPTS = ("aux_promptbudget.js", "aux_toolbudget.js", "aux_context.js",
           "aux_memlayer.js")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  %s" % (name, extra))


errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:160]))
    pg.on("console",
          lambda m: errs.append("console: " + m.text[:160]) if m.type == "error" else None)
    pg.goto(BASE + "/", wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("window.setView && window.setView('mind')")
    # the cards paint after their own fetches resolve
    for card in CARDS:
        try:
            pg.wait_for_selector("#%s h2" % card, timeout=20000)
        except Exception:
            pass
    seen = pg.evaluate(
        """(ids)=>{
          const out = {};
          for (const id of ids) {
            const e = document.getElementById(id);
            out[id] = e ? {panel: (e.closest('[id^=sec-]')||{}).id || null,
                           h2: ((e.querySelector('h2')||{}).textContent||'').trim(),
                           len: e.innerHTML.length}
                        : null;
          }
          return {cards: out,
                  scripts: [...document.scripts].map(s => s.src.split('/').pop()),
                  titles: [...document.querySelectorAll('h1,h2,h3,.set-h,.card-title')]
                            .map(e => e.textContent.trim())};
        }""", list(CARDS))
    b.close()

print("\n=== 1. the release's aux modules are served ===")
for s in SCRIPTS:
    check("%s is on the page" % s, s in seen["scripts"])

print("\n=== 2. every card mounts, in its own panel, with its heading ===")
for cid, (panel, title) in CARDS.items():
    got = seen["cards"].get(cid)
    check("%s exists" % cid, got is not None)
    if not got:
        continue
    check("%s mounts under #%s" % (cid, panel), got["panel"] == panel, got["panel"])
    check("%s is headed %r" % (cid, title), got["h2"] == title, got["h2"])
    check("%s rendered a body" % cid, got["len"] > 500, got["len"])

print("\n=== 3. the health card is present ===")
check("a Health card exists",
      any(t == "Health" or t.startswith("Health ") for t in seen["titles"]))

print("\n=== 4. the page is quiet ===")
check("no console or page errors", not errs, errs[:5])

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("   - %s" % f)
sys.exit(1 if FAILS else 0)
