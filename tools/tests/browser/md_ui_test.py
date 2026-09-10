#!/usr/bin/env python3
"""aux_md.js in a real browser: the chat bubble renderer and the Quick Ask
popover shell.  Needs the RUNNING dashboard.

Read-only.  The escalation section dispatches the `hermes:claude-escalation`
event IN THE PAGE only — it never POSTs /api/claude/escalate, so the owner's
switch is not touched — and nothing here wakes a model.
"""
import json, os, sys, tempfile
from playwright.sync_api import sync_playwright
SP = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(prefix="hermes-shots-")
os.makedirs(SP, exist_ok=True)
BASE = os.environ.get("HERMES_DASH_BASE", "http://127.0.0.1:7788")
URL = BASE + "/"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  %s" % (name, extra))

MD = ('<think>Let me plan.\nCompare options.</think>\n## Local models compared\n\n'
      '| Model | Speed | RAM |\n|---|---:|:-:|\n| Qwen3 8B | 30 tok/s | 6 GB |\n| Qwen3.8 27B | 12 tok/s | 17 GB |\n\n'
      '**Steps**\n1. Install `mlx-vlm`\n2. Run it\n   - nested point\n   - another\n\n> Tip: keep it on-demand.\n\n---\n'
      '```python\nprint("hi")\n```\nDone with ~~old~~ new approach, see https://example.com/a_b.')
SHELL = ('<!doctype html><meta charset="utf-8"><script>window.__HERMES_QUICKASK__=1;</script>'
         '<div id="qa" style="padding:16px">starting…</div><script src="/motion.min.js"></script>'
         '<script src="/aux_quickask.js"></script><script src="/aux_clip.js"></script>')
res = {"errors": [], "checks": {}}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: res["errors"].append("main: " + str(e)[:200]))
    page.on("console", lambda m: res["errors"].append("main console: " + m.text[:200]) if m.type == "error" else None)
    page.goto(URL, wait_until="networkidle", timeout=60000); page.wait_for_timeout(1500)
    page.evaluate("()=>{try{const t=document.querySelector('[data-tab=chat],#tab-chat');if(t)t.click();}catch(e){}}")
    c = page.evaluate("""(md)=>{
      const b = addBubble(md,'bot',true); const m = b.querySelector('.md');
      const has = s => !!m.querySelector(s);
      return {hermesMd: typeof window.hermesMd==='object', table: has('.md-table table tbody tr td'), th_right: !!m.querySelector('th[style*="right"]'),
        quote: has('blockquote'), hr: has('hr'), nested: has('ol > li > ul > li'), think: has('details.md-think summary'),
        lang: has('code.lang-python'), del: has('del'), link: !!m.querySelector('a[href="https://example.com/a_b"]'),
        h2: has('h2'), rawPipes: /\\|/.test(m.innerText), html: m.innerHTML.length};
    }""", MD)
    res["checks"]["render"] = c
    bub = page.query_selector("#msgs .bubble.bot:last-child")
    if bub: bub.scroll_into_view_if_needed(); bub.screenshot(path=f"{SP}/md_bubble_dark.png")
    # escalation label must not appear for synthesized statuses, nor for bridge tools while the switch is off
    e = page.evaluate("""()=>{
      const before = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      window.dispatchEvent(new CustomEvent('hermes:claude-escalation',{detail:{enabled:false,available:true}}));
      setAgentState('thinking…'); const a1 = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      setAgentState('writing…'); const a2 = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      setAgentState('using claude_think'); const a3 = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      setAgentState(null);
      setAgentState('using web_search'); const web = (document.body.innerText.match(/Searched the web|Searching/g)||[]).length;
      setAgentState(null);
      window.dispatchEvent(new CustomEvent('hermes:claude-escalation',{detail:{enabled:true,available:true}}));
      setAgentState('thinking…'); const on1 = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      setAgentState('using claude_think'); const on2 = (document.body.innerText.match(/Escalated to Claude|Thinking with Claude/g)||[]).length;
      setAgentState(null);
      return {before, off_thinking:a1, off_writing:a2, off_bridge_tool:a3, web_card:web, on_thinking:on1, on_bridge_tool:on2,
              cached: localStorage.getItem('hermes_claude_esc'), state: window.claudeEscalationState()};
    }""")
    res["checks"]["escalation"] = e
    # popover shell (same origin via route)
    pop = ctx.new_page()
    pop.on("pageerror", lambda x: res["errors"].append("popover: " + str(x)[:200]))
    pop.on("console", lambda m: res["errors"].append("popover console: " + m.text[:200]) if m.type == "error" else None)
    pop.route(URL + "__qa_test", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=SHELL))
    pop.set_viewport_size({"width": 380, "height": 560})
    pop.goto(URL + "__qa_test", wait_until="networkidle", timeout=60000); pop.wait_for_timeout(1500)
    q = pop.evaluate("""()=>({hermesMd: typeof window.hermesMd==='object', scripts:[...document.scripts].map(s=>s.src.replace(location.origin,'')),
        qaBuilt: !!document.querySelector('#qa .qa-strip, #qa .qa-ask, #qa textarea, #qa input'), text: document.body.innerText.slice(0,80)})""")
    res["checks"]["popover"] = q
    pop.screenshot(path=f"{SP}/popover_md.png")
    b.close()
r = res["checks"]["render"]
print("\n=== 1. the bubble renderer ===")
check("window.hermesMd is loaded", r["hermesMd"])
for k, label in (("table", "a table becomes a real <table>"),
                 ("th_right", "column alignment survives"),
                 ("quote", "a blockquote renders"),
                 ("hr", "a rule renders"),
                 ("nested", "a list nested under an ordered list renders"),
                 ("think", "<think> collapses into a <details>"),
                 ("lang", "a fenced block keeps its language"),
                 ("del", "~~strikethrough~~ renders"),
                 ("link", "a bare url with underscores is one link"),
                 ("h2", "a heading renders")):
    check(label, r[k] is True, r)
check("no raw pipes leaked into the text", r["rawPipes"] is False, r)
check("the bubble actually has markup", r["html"] > 300, r["html"])

e = res["checks"]["escalation"]
print("\n=== 2. the Claude-escalation label ===")
check("switch OFF: a synthesized 'thinking' status is not labelled",
      e["off_thinking"] == e["before"], e)
check("switch OFF: a synthesized 'writing' status is not labelled",
      e["off_writing"] == e["before"], e)
check("switch OFF: a bridge tool is not labelled either",
      e["off_bridge_tool"] == e["before"], e)
check("switch ON: a synthesized status is STILL not labelled",
      e["on_thinking"] == e["before"], e)
check("switch ON: a real bridge tool IS labelled",
      e["on_bridge_tool"] == e["before"] + 1, e)
check("an unrelated tool keeps its own card", e["web_card"] == 1, e)

q = res["checks"]["popover"]
print("\n=== 3. the Quick Ask popover shell ===")
check("hermesMd is loaded in the popover too", q["hermesMd"])
check("aux_md.js is pulled in by aux_quickask.js",
      any(s.endswith("/aux_md.js") for s in q["scripts"]), q["scripts"])
check("the popover built its UI", q["qaBuilt"], q["text"])

print("\n=== 4. the page is quiet ===")
check("no console or page errors", not res["errors"], res["errors"][:5])

print("\nshots in " + SP)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("   - %s" % f)
sys.exit(1 if FAILS else 0)
