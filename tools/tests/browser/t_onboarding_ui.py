#!/usr/bin/env python3
"""Headless UI harness for aux_onboarding.js (release 1.1.1; round 3: 1.4.0).

Serves the REAL dashboard/index.html on the dashboard origin through Playwright
routes (so relative /aux_*.js loads resolve), and routes every /api/** call to a
fixture. No dashboard is started, no model is ever woken, and
/api/models/download is stubbed with a hard failure so a regression that calls
it is loud rather than expensive.

Covers:
  * the sheet auto-opens when done:false, and does NOT appear when done:true
  * all four steps, in BOTH themes, screenshotted
  * a 16 GB Mac gets a different recommendation than a 64 GB Mac
  * keyboard navigation: Tab is trapped, Enter advances, Esc is swallowed on a
    first run and closes on a re-run
  * the sheet paints the app's --ground token in both themes (token inheritance)
  * round 3: a step registered through window.hermesOnboardingSteps (the
    extra-steps hook) — order, "Step n of 5", Skip, a save() err keeping the
    step, its applied/summary lines on the Ready step, its status row — and
    the models step delegated to a stub window.hermesAdvisor whose routes
    (/api/advisor/state, /api/advisor/answers) are fixtures here, with the
    sheet's own action row under it quoting the plan's size

Hermetic: every request is answered by page.route() from the worktree's files
and the fixtures below — nothing reaches a server, and /vendor/* is not served
(the motion runtime takes its instant path, so screenshots are end states;
tools/tests/browser/t_onboarding_real.js runs the motion on the real
libraries). SKIPs cleanly (exit 0) without Playwright or its Chromium.
"""
import json
import os
import re
import sys
import tempfile

try:
    from playwright.sync_api import sync_playwright
except Exception as _e:                      # not installed: the suite cannot run here
    print("SKIP Playwright for Python is not installed (%s)" % type(_e).__name__)
    print("TESTS 0 passed 0 failed")
    sys.exit(0)

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
SHOTS = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(
    prefix="hermes-shots-")
os.makedirs(SHOTS, exist_ok=True)
ORIGIN = "http://127.0.0.1:7788"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
CATALOG = {
    "mlx-community/Qwen3.5-2B-4bit": ("Qwen3.5-2B", 3, 1.7,
                                      "smallest brain · chat and light tools · fastest"),
    "mlx-community/Qwen3.5-4B-4bit": ("Qwen3.5-4B", 4, 3.1,
                                      "small brain · chat and everyday tools"),
    "mlx-community/Qwen3.5-9B-4bit": ("Qwen3.5-9B", 7, 6.0,
                                      "background lane · news, scraping, briefings · fast"),
    "mlx-community/Qwen3.8-27B-4bit": ("Qwen3.8-27B", 19, 17.0,
                                       "assistant brain · dense · MTP ~2x · Aug-2026"),
}
M2B = "mlx-community/Qwen3.5-2B-4bit"
M4B = "mlx-community/Qwen3.5-4B-4bit"
M9B = "mlx-community/Qwen3.5-9B-4bit"
M27B = "mlx-community/Qwen3.8-27B-4bit"


def fit(ram, machine):
    if ram > 0.85 * machine:
        return "no"
    if ram > 0.60 * machine:
        return "tight"
    return "ok"


def fits(machine, downloaded=()):
    out = []
    for mid, (label, ram, size, note) in CATALOG.items():
        out.append({"id": mid, "label": label, "ram": ram, "size_gb": size,
                    "note": note, "role": "primary",
                    "fit": fit(ram, machine), "downloaded": mid in downloaded})
    return out


def state_fixture(ram=64, done=False, chip="Apple M5 Max", laptop=True,
                  claude=True, downloaded=(), free=412.0):
    if ram >= 48:
        rec = {"tier": "full", "primary": M27B, "background": M9B, "tight": False,
               "alt": None,
               "why": "Room for the full roster: the 27B assistant stays warm "
                      "while a 9B lane handles news and briefings.", "note": ""}
    elif ram >= 36:
        rec = {"tier": "large", "primary": M27B, "background": M9B, "tight": True,
               "alt": {"primary": M9B, "background": M2B,
                       "label": "Lighter on battery",
                       "why": "A 9B assistant with a 2B background lane keeps "
                              "the whole roster under 10 GB resident."},
               "why": "The 27B assistant will load, but 26 GB resident with the "
                      "background lane is tight here — expect swapping under "
                      "heavy use.", "note": "tight"}
    elif ram >= 24:
        rec = {"tier": "balanced", "primary": M9B, "background": M2B,
               "tight": False, "alt": None,
               "why": "Enough RAM for a 9B assistant plus a small always-on "
                      "lane, so news and briefings never interrupt you.", "note": ""}
    elif ram >= 16:
        rec = {"tier": "small", "primary": M4B, "background": None, "tight": False,
               "alt": None,
               "why": "A 4B assistant fits comfortably and leaves the Mac "
                      "usable. No background lane at this size — briefings run "
                      "on the same model.", "note": ""}
    else:
        rec = {"tier": "minimal", "primary": M2B, "background": None, "tight": False,
               "alt": None,
               "why": "Under 16 GB there is room for one small brain and nothing "
                      "else — chat only, small context.",
               "note": "chat only, small context"}
    ids = [i for i in (rec["primary"], rec["background"]) if i]
    need = sum(CATALOG[i][2] for i in ids if i not in downloaded)
    rec.update({
        "primary_label": CATALOG[rec["primary"]][0],
        "background_label": CATALOG[rec["background"]][0] if rec["background"] else "",
        "download_gb": round(need, 1),
        "total_gb": round(sum(CATALOG[i][2] for i in ids), 1),
        "already_downloaded": bool(ids) and not need,
        "fits": fits(ram, downloaded)})
    return {
        "ok": True, "version": "1.1.1",
        "done": done, "done_at": 1757030000.0 if done else None,
        "done_version": "1.1.1" if done else None,
        "sysinfo": {"chip": chip, "ram_gb": ram, "disk_free_gb": free,
                    "macos": "26.1", "laptop": laptop, "cores": 16},
        "detect": {"hermes_cli": True, "hermes_path": "/Users/you/.local/bin/hermes",
                   "claude_cli": claude, "mlx_venv": True,
                   "models_downloaded": list(downloaded), "fda": False,
                   "telegram_configured": True, "google_configured": False},
        "recommendation": rec,
        "prefs": {"theme": None, "idle_min": 10, "idle_enabled": True,
                  "prewarm": True, "claude_escalation": True,
                  "briefings": True, "news": True,
                  "quiet_hours": {"start": "22:00", "end": "07:00"}},
    }


CUR = {"state": state_fixture(64, done=False)}
CALLS = {"apply": [], "done": 0, "reset": 0, "download": 0,
         "adv_state": 0, "adv_answers": []}

# the stub advisor's /api/advisor/state (only the fields the stub reads)
ADV_STATE = {"ok": True, "version": 1, "result": {"ok": True, "recommended": {
    "kind": "recommended", "label": "Recommended",
    "primary": {"id": "mlx-community/Qwen3.8-27B-4bit", "label": "Qwen3.8-27B"},
    "background": {"id": "mlx-community/Qwen3.5-9B-4bit", "label": "Qwen3.5-9B"},
    "download_gb": 23.0, "why": "Fits with room to spare (36 of 48 GB)."}}}

# a step another module registers BEFORE aux_onboarding.js loads (contract 1)
EXTRA_STEP_JS = r"""
(window.hermesOnboardingSteps = window.hermesOnboardingSteps || []).push({
  key: "harness", title: "Harness step", sub: "Registered by the test, before the sheet loaded",
  eyebrow: "Connections", after: "prefs", optional: true,
  render: function (ctx) {
    return '<p class="onb-p hx">An extra step body.</p>' +
      '<div class="onb-row"><div class="onb-row-txt"><span class="onb-row-l">A field</span></div>' +
      '<div class="onb-row-ctl"><input class="onb-time hx-in" aria-label="Harness field"></div></div>';
  },
  wire: function (node, ctx) { window.__hxWired = (window.__hxWired || 0) + 1; window.__hxCtx = ctx; },
  save: function (ctx) {
    window.__hxSaves = (window.__hxSaves || 0) + 1;
    return Promise.resolve(window.__hxReply || { applied: ["Harness: saved by its own step"] });
  },
  statusRow: function (det) { return ["ok", "Harness", "A row from the registered step"]; },
  summary: function (ctx) { return ["Harness: its summary line"]; }
});
"""

# a stand-in for aux_advisor.js's window.hermesAdvisor (contract 2)
ADVISOR_STUB_JS = r"""
window.hermesAdvisor = { v: 1, step: {
  render: function (ctx) {
    return '<div class="adv-stub"><ul class="onb-stats"><li><span class="onb-stat-k">Chip</span>' +
      '<span class="onb-stat-v">Apple M5 Max</span></li></ul>' +
      '<fieldset role="group" aria-label="What will you use it for?" style="border:0;padding:0">' +
      '<label class="onb-sw"><input type="checkbox" data-use="code">' +
      '<span class="onb-sw-t" aria-hidden="true"></span><span class="onb-sw-l">Coding help</span></label>' +
      '</fieldset><p class="onb-p adv-plan">Reading this Mac…</p></div>';
  },
  wire: function (node, ctx) {
    window.__advWires = (window.__advWires || 0) + 1;
    fetch("/api/advisor/state").then(function (r) { return r.json(); }).then(function (st) {
      var rec = st.result.recommended, p = node.querySelector(".adv-plan");
      if (p) p.textContent = rec.primary.label + " as the assistant, " + rec.background.label +
        " for background work";
      if (!window.__advPicked) {
        window.__advPicked = 1;
        ctx.setPick({ primary: rec.primary.id, background: rec.background.id,
                      download_gb: rec.download_gb });
      }
    });
  },
  save: function (ctx) {
    return fetch("/api/advisor/answers", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uses: ["chat", "agent"], priority: "balanced" }) })
      .then(function (r) {
        if (r.status === 400) return r.json().then(function (j) { return { err: (j.errors || []).join(" · ") }; });
        return {};
      }).catch(function () { return {}; });
  }
} };
"""

STATIC = {"/": "index.html", "/index.html": "index.html"}
MIME = {".js": "application/javascript", ".html": "text/html",
        ".css": "text/css", ".json": "application/json"}


def install_routes(page):
    def handle(route):
        req = route.request
        path = re.sub(r"\?.*$", "", req.url.replace(ORIGIN, "")) or "/"

        # ---- the routes under test -----------------------------------------
        if path == "/api/onboarding/state":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(CUR["state"]))
        if path == "/api/onboarding/apply":
            body = {}
            try:
                body = json.loads(req.post_data or "{}")
            except Exception:
                pass
            CALLS["apply"].append(body)
            applied = dict(body)
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "applied": applied,
                                                  "errors": [],
                                                  "prefs": CUR["state"]["prefs"]}))
        if path == "/api/onboarding/done":
            CALLS["done"] += 1
            CUR["state"] = dict(CUR["state"], done=True)
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "done": True,
                                                  "version": "1.1.1", "ts": 1}))
        if path == "/api/onboarding/reset":
            CALLS["reset"] += 1
            CUR["state"] = dict(CUR["state"], done=False)
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "done": False}))

        # ---- round 3: the model advisor's routes (the stub advisor uses them)
        if path == "/api/advisor/state":
            CALLS["adv_state"] += 1
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(ADV_STATE))
        if path == "/api/advisor/answers":
            body = {}
            try:
                body = json.loads(req.post_data or "{}")
            except Exception:
                pass
            CALLS["adv_answers"].append(body)
            if CUR.get("adv_400"):
                return route.fulfill(status=400, content_type="application/json",
                                     body=json.dumps({"ok": False, "errors": [CUR["adv_400"]]}))
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "answers": body}))

        # ---- a real download must NEVER be reachable from a test -----------
        if path == "/api/models/download":
            CALLS["download"] += 1
            return route.fulfill(status=500, content_type="application/json",
                                 body=json.dumps({"ok": False,
                                                  "error": "STUB: no real downloads"}))
        if path == "/api/models":
            st = CUR["state"]
            models = [{"id": m["id"], "label": m["label"], "ram": m["ram"],
                       "note": m["note"], "fit": m["fit"],
                       "downloaded": m["downloaded"], "downloading": False,
                       "active": False}
                      for m in st["recommendation"]["fits"]]
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"models": models, "mem": {"machine_gb":
                                                  st["sysinfo"]["ram_gb"]}}))

        # ---- everything else the shell pokes at: benign empties -------------
        if path.startswith("/api/"):
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True}))

        # ---- modules that change the sheet's shape -------------------------
        # The legacy models body and the four built-in steps are what [1]-[8]
        # check, and [9]/[10] bring their own extra step and advisor stand-in.
        # Once index.html loads the real aux_advisor.js (it replaces the models
        # body) and aux_github.js (it registers a step), they are answered 404
        # here so this suite stays deterministic; each has its own suite.
        if path in ("/aux_advisor.js", "/aux_github.js"):
            return route.fulfill(status=404, body="")

        # ---- static files off disk ------------------------------------------
        fn = STATIC.get(path)
        if fn is None and re.match(r"^/[A-Za-z0-9_.-]+\.(js|css|html)$", path):
            fn = path.lstrip("/")
        if fn:
            fp = os.path.join(DASH, fn)
            if os.path.isfile(fp):
                ext = os.path.splitext(fp)[1]
                with open(fp, "rb") as f:
                    return route.fulfill(status=200,
                                         content_type=MIME.get(ext, "text/plain"),
                                         body=f.read())
        return route.fulfill(status=404, body="")

    page.route("**/*", handle)


JS_OPEN = "() => !!document.getElementById('onb-sheet') && " \
          "!document.getElementById('onb-sheet').hidden"


def shot(page, name):
    p = os.path.join(SHOTS, name + ".png")
    page.screenshot(path=p, full_page=False)
    return p


def set_theme(page, theme):
    page.evaluate("""(t) => {
      if (t === 'system') { document.documentElement.removeAttribute('data-theme');
        localStorage.removeItem('hermes_theme'); }
      else { document.documentElement.setAttribute('data-theme', t);
        localStorage.setItem('hermes_theme', t); }
    }""", theme)


SHOT_PATHS = []


def run():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:               # Playwright without its Chromium
            print("SKIP Playwright's Chromium could not start (%s)" % str(e).splitlines()[0][:120])
            print("TESTS 0 passed 0 failed")
            sys.exit(0)
        ctx = browser.new_context(viewport={"width": 1180, "height": 860},
                                  device_scale_factor=2)
        page = ctx.new_page()
        errs = []
        # keep the STACK: the catch-all {"ok":true} fixture below deliberately
        # under-feeds unrelated aux modules (aux_shortcuts wants d.actions,
        # aux_convos wants d.sessions), so the only errors that matter here are
        # the ones whose stack names aux_onboarding.js.
        page.on("pageerror", lambda e: errs.append(
            {"msg": str(e), "stack": (getattr(e, "stack", "") or "")}))
        install_routes(page)

        # ==================================================================
        print("\n[1] first run on a 64 GB Mac — the sheet auto-opens")
        CUR["state"] = state_fixture(64, done=False)
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_function(JS_OPEN, timeout=8000)
        check("sheet auto-opened with done:false", page.evaluate(JS_OPEN))
        check("aria: modal dialog",
              page.get_attribute("#onb-sheet", "role") == "dialog" and
              page.get_attribute("#onb-sheet", "aria-modal") == "true")
        check("zero emoji in the sheet",
              not re.search(r"[\U0001F300-\U0001FAFF☀-➿]",
                            page.inner_text("#onb-sheet")))

        # ---- all four steps, both themes ---------------------------------
        for theme in ("light", "dark"):
            set_theme(page, theme)
            page.evaluate("window.hermesOnboarding.go(0)")
            page.wait_for_timeout(180)
            for i, key in enumerate(["welcome", "models", "prefs", "done"]):
                page.evaluate("(i)=>window.hermesOnboarding.go(i)", i)
                page.wait_for_timeout(220)
                SHOT_PATHS.append(shot(page, "64gb-%s-%d-%s" % (theme, i + 1, key)))
            check("%s: 4 steps captured" % theme, True)

        # ---- token inheritance (no fifth palette) -------------------------
        for theme in ("light", "dark"):
            set_theme(page, theme)
            page.wait_for_timeout(120)
            got = page.evaluate("""() => {
              const s = getComputedStyle(document.getElementById('onb-sheet'));
              const r = getComputedStyle(document.documentElement);
              return [s.backgroundColor, r.getPropertyValue('--ground').trim()];
            }""")
            def rgb(h):
                h = h.lstrip("#")
                return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            check("%s: sheet paints the app's --ground token" % theme,
                  got[0] == rgb(got[1]), got)

        set_theme(page, "light")

        # ---- step 1 content ------------------------------------------------
        page.evaluate("window.hermesOnboarding.go(0)")
        page.wait_for_timeout(150)
        t1 = page.inner_text("#onb-sheet")
        # C52: the row count follows NETWORK_FACTS instead of a hard-coded 5
        nfacts = page.evaluate("window.hermesOnboarding.NETWORK_FACTS.length")
        ntoggles = page.evaluate(
            "window.hermesOnboarding.NETWORK_FACTS.filter(f => f.toggle).length")
        check("step 1 lists every egress path",
              all(w in t1 for w in ("GitHub", "Hugging Face", "News sites",
                                    "Telegram", "Claude", "Google")), t1[:200])
        check("step 1 table has one body row per NETWORK_FACTS entry",
              page.eval_on_selector_all("#onb-sheet table.onb-net tbody tr",
                                        "e=>e.length") == nfacts)
        check("the wired toggles are live checkboxes",
              page.eval_on_selector_all(
                  "#onb-sheet table.onb-net input[data-pref]", "e=>e.length") == ntoggles)
        check("R2-43: the unattended web search row has its own switch, Off by default",
              page.eval_on_selector_all(
                  "#onb-sheet table.onb-net input[data-pref='web_research']",
                  "e=>e.map(x=>x.checked)") == [False])
        check("R2-43: the News switch sits on the feeds row as well as the alerts row",
              page.eval_on_selector_all(
                  "#onb-sheet table.onb-net input[data-pref='news']", "e=>e.length") == 2)
        check("networkTableHTML is exported for the Data & Network panel",
              page.evaluate("() => typeof window.hermesOnboarding.networkTableHTML "
                            "=== 'function'"))
        check("read-only mode renders no inputs (the future panel's use)",
              page.evaluate("""() => {
                const h = window.hermesOnboarding.networkTableHTML(
                  {prefs:{news:true}, inputs:false});
                return h.indexOf('<input') < 0 && h.indexOf('<table') > -1;
              }"""))

        # ---- step 2 content ------------------------------------------------
        page.evaluate("window.hermesOnboarding.go(1)")
        page.wait_for_timeout(150)
        t2 = page.inner_text("#onb-sheet")
        check("step 2 shows the detected facts",
              all(w in t2 for w in ("Apple M5 Max", "64 GB", "26.1", "Laptop")), t2[:250])
        check("64 GB recommendation names both lanes",
              "Qwen3.8-27B" in t2 and "Qwen3.5-9B" in t2)
        check("64 GB quotes the 23 GB download", "23 GB to download" in t2, t2[:400])
        check("fit lines reuse the 1.0.3 phrasing",
              "needs ~19 GB · this Mac has 64 GB" in t2, t2[:400])
        check("primary action is 'Download recommended'",
              page.inner_text("#onb-sheet .onb-go").strip() == "Download recommended")
        check("secondary 'I'll choose later' is offered",
              "choose later" in page.inner_text("#onb-sheet .onb-foot"))
        # C63: 'later' is its own action and sends no model pick
        CALLS["apply"][:] = []
        page.click("#onb-sheet .onb-foot button[data-act='later']")
        page.wait_for_timeout(300)
        check("C63: 'I'll choose later' sends no model pick to apply",
              not any("primary" in b or "background" in b for b in CALLS["apply"]),
              CALLS["apply"])
        check("...and moves on to step 3",
              page.evaluate("() => window.hermesOnboarding.state.step") == 2)
        page.evaluate("window.hermesOnboarding.go(1)")
        page.wait_for_timeout(150)
        check("alternatives are listed as a compact list",
              page.eval_on_selector_all("#onb-sheet ul.onb-alts li", "e=>e.length") >= 2)
        check("no card inside a card",
              page.eval_on_selector_all("#onb-sheet .onb-rec .onb-rec", "e=>e.length") == 0)

        # ---- step 3 content ------------------------------------------------
        page.evaluate("window.hermesOnboarding.go(2)")
        page.wait_for_timeout(150)
        t3 = page.inner_text("#onb-sheet")
        for w in ("Appearance", "Sleep the model after", "Prewarm after wake",
                  "Claude escalation", "Briefings", "News and breaking alerts",
                  "Quiet hours", "Telegram", "Google", "Full Disk Access"):
            check("step 3 has '%s'" % w, w in t3)
        check("FDA row gives the exact System Settings path",
              "System Settings › Privacy & Security › Full Disk Access" in t3)
        check("Telegram reports configured (never the token)",
              "Configured." in t3 and "TELEGRAM_BOT_TOKEN" not in t3)
        check("sleep offers 5 / 10 / 20 / Never",
              page.eval_on_selector_all(
                  "#onb-sheet input[data-seg='idle']", "e=>e.map(x=>x.value)") ==
              ["5", "10", "20", "never"])
        check("theme offers System / Light / Dark",
              page.eval_on_selector_all(
                  "#onb-sheet input[data-seg='theme']", "e=>e.map(x=>x.value)") ==
              ["system", "light", "dark"])

        # theme applies IMMEDIATELY
        page.click("#onb-sheet .onb-seg label:has(input[data-seg='theme'][value='dark'])")
        page.wait_for_timeout(160)
        check("choosing Dark applies data-theme + localStorage at once",
              page.evaluate("() => document.documentElement.getAttribute('data-theme')"
                            " === 'dark' && localStorage.getItem('hermes_theme') === 'dark'"))
        SHOT_PATHS.append(shot(page, "64gb-dark-3-prefs-theme-applied"))
        page.click("#onb-sheet .onb-seg label:has(input[data-seg='theme'][value='light'])")
        page.wait_for_timeout(140)
        check("choosing Light flips it back",
              page.evaluate("() => document.documentElement.getAttribute('data-theme')"
                            " === 'light'"))

        # ---- hit areas -----------------------------------------------------
        small = page.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('#onb-sheet button, #onb-sheet .onb-sw, ' +
            '#onb-sheet .onb-seg label').forEach(e => {
            const r = e.getBoundingClientRect();
            if (!r.width) return;
            const cs = getComputedStyle(e, '::after');
            let h = r.height;
            const t = parseFloat(cs.top), b = parseFloat(cs.bottom);
            if (cs.content !== 'none' && !isNaN(t) && t < 0) h += (-t) + (-b || 0);
            if (h < 39.5) bad.push(e.className + ':' + Math.round(h));
          });
          return bad;
        }""")
        check("every control has a >=40px hit area", small == [], small)

        # ---- step 4 --------------------------------------------------------
        page.evaluate("window.hermesOnboarding.go(3)")
        page.wait_for_timeout(150)
        t4 = page.inner_text("#onb-sheet")
        check("step 4 shows the hotkey hint", "⌃⌥Space" in t4)
        check("step 4 primary action is Start",
              page.inner_text("#onb-sheet .onb-go").strip() == "Start")

        # ==================================================================
        print("\n[2] keyboard navigation")
        page.evaluate("window.hermesOnboarding.go(0)")
        page.wait_for_timeout(150)
        check("primary action is focused on paint",
              page.evaluate("() => document.activeElement.classList.contains('onb-go')"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        check("Esc on step 1 of a FIRST RUN does nothing", page.evaluate(JS_OPEN))
        check("Esc did not advance the step",
              page.evaluate("() => window.hermesOnboarding.state.step") == 0)
        page.keyboard.press("Enter")            # focus is the primary button
        page.wait_for_timeout(320)
        check("Enter on the primary action advances to step 2",
              page.evaluate("() => window.hermesOnboarding.state.step") == 1)
        check("advancing saved the step's fields",
              any("briefings" in b for b in CALLS["apply"]), CALLS["apply"])
        # Tab stays inside the sheet
        inside = True
        for _ in range(28):
            page.keyboard.press("Tab")
            if not page.evaluate("() => document.getElementById('onb-sheet')"
                                 ".contains(document.activeElement)"):
                inside = False
                break
        check("Tab is trapped inside the modal sheet", inside)
        page.keyboard.press("Shift+Tab")
        check("Shift+Tab stays inside too",
              page.evaluate("() => document.getElementById('onb-sheet')"
                            ".contains(document.activeElement)"))
        # Esc on the final step of a first run DOES close
        page.evaluate("window.hermesOnboarding.go(3)")
        page.wait_for_timeout(150)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("Esc on the FINAL step of a first run closes", not page.evaluate(JS_OPEN))
        # a re-run is dismissable from anywhere
        page.evaluate("window.hermesOnboarding.open({rerun:true})")
        page.wait_for_function(JS_OPEN, timeout=4000)
        page.wait_for_timeout(200)
        check("re-run opens at step 1",
              page.evaluate("() => window.hermesOnboarding.state.step") == 0)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("Esc closes a RE-RUN from step 1", not page.evaluate(JS_OPEN))

        # ==================================================================
        print("\n[3] 16 GB Mac gets a different, honest recommendation")
        CUR["state"] = state_fixture(16, done=False, chip="Apple M2",
                                     laptop=True, free=48.0)
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_function(JS_OPEN, timeout=8000)
        for theme in ("light", "dark"):
            set_theme(page, theme)
            page.evaluate("window.hermesOnboarding.go(1)")
            page.wait_for_timeout(220)
            SHOT_PATHS.append(shot(page, "16gb-%s-2-models" % theme))
        set_theme(page, "light")
        page.evaluate("window.hermesOnboarding.go(1)")
        page.wait_for_timeout(180)
        t16 = page.inner_text("#onb-sheet")
        check("16 GB recommends the 4B", "Qwen3.5-4B" in t16)
        check("16 GB says there is no background lane",
              "no background lane" in t16 or "none at this memory size" in t16, t16[:400])
        check("16 GB quotes a 3.1 GB download", "3.1 GB to download" in t16, t16[:400])
        check("the 27B is shown as too big to run here",
              "too big to run here" in t16, t16[:600])
        check("the bad fit uses the --bad token",
              page.eval_on_selector_all("#onb-sheet .onb-fit.bad", "e=>e.length") >= 1)
        # also capture the other two steps on this Mac
        for i, key in ((0, "welcome"), (2, "prefs"), (3, "done")):
            page.evaluate("(i)=>window.hermesOnboarding.go(i)", i)
            page.wait_for_timeout(200)
            SHOT_PATHS.append(shot(page, "16gb-light-%d-%s" % (i + 1, key)))

        # ==================================================================
        print("\n[4] all recommended already downloaded -> no download button")
        CUR["state"] = state_fixture(64, done=False, downloaded=(M27B, M9B))
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_function(JS_OPEN, timeout=8000)
        page.evaluate("window.hermesOnboarding.go(1)")
        page.wait_for_timeout(220)
        tdl = page.inner_text("#onb-sheet")
        check("says everything is already downloaded",
              "already downloaded on this Mac" in tdl, tdl[:400])
        check("the download button is replaced by Continue",
              page.inner_text("#onb-sheet .onb-go").strip() == "Continue")
        check("no 'choose later' secondary when there is nothing to fetch",
              "choose later" not in page.inner_text("#onb-sheet .onb-foot"))
        SHOT_PATHS.append(shot(page, "64gb-light-2-models-already-downloaded"))
        # C63: Continue used to send nothing — the picks never reached apply
        CALLS["apply"][:] = []
        page.click("#onb-sheet .onb-go")
        page.wait_for_timeout(400)
        mb = [b for b in CALLS["apply"] if "primary" in b]
        check("C63: Continue posts the picks, with no download",
              len(mb) == 1 and mb[0].get("primary") == M27B and
              mb[0].get("background") == M9B and mb[0].get("download") is False,
              CALLS["apply"])
        check("...and advances to step 3",
              page.evaluate("() => window.hermesOnboarding.state.step") == 2)

        # ==================================================================
        print("\n[5] done:true — the sheet must NOT appear")
        CUR["state"] = state_fixture(64, done=True)
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1800)
        check("no sheet is shown when done:true", not page.evaluate(JS_OPEN))
        check("the sheet element is absent or hidden",
              page.evaluate("() => { const n = document.getElementById('onb-sheet');"
                            " return !n || n.hidden === true; }"))
        check("but it can still be opened on demand",
              page.evaluate("""async () => {
                await window.hermesOnboarding.open({rerun:true});
                const n = document.getElementById('onb-sheet');
                return !!n && !n.hidden;
              }"""))
        page.evaluate("window.hermesOnboarding.close()")

        # ==================================================================
        print("\n[6] the Settings 'Run setup again' row")
        mounted = page.evaluate("""async () => {
          // simulate the Settings shell having built its panels
          if (!document.getElementById('sec-overview')) {
            const s = document.createElement('section');
            s.id = 'sec-overview';
            (document.getElementById('view-mind') || document.body).appendChild(s);
          }
          await window.mindExtras();
          const c = document.getElementById('mind-extra-onboarding');
          return c ? { parent: c.parentNode.id, cls: c.className,
                       txt: c.innerText, btn: !!c.querySelector('#onb-rerun') } : null;
        }""")
        check("card mounts into Settings › Overview",
              mounted and mounted["parent"] == "sec-overview", mounted)
        check("card uses the app's card+glass classes",
              mounted and "card" in mounted["cls"] and "glass" in mounted["cls"])
        check("card offers a Run setup button",
              mounted and mounted["btn"] and "Run setup again" in mounted["txt"])
        clicked = page.evaluate("""async () => {
          document.getElementById('onb-rerun').click();
          await new Promise(r => setTimeout(r, 500));
          const n = document.getElementById('onb-sheet');
          return !!n && !n.hidden && window.hermesOnboarding.state.rerun === true;
        }""")
        check("the button opens the sheet in re-run mode", clicked)
        SHOT_PATHS.append(shot(page, "settings-rerun-row-sheet"))
        page.evaluate("window.hermesOnboarding.close()")
        page.wait_for_timeout(150)
        SHOT_PATHS.append(shot(page, "settings-rerun-row"))

        # ==================================================================
        print("\n[7] finish writes done and hands over to the chat input")
        CUR["state"] = state_fixture(64, done=False)
        CALLS["done"] = 0
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_function(JS_OPEN, timeout=8000)
        page.evaluate("window.hermesOnboarding.go(3)")
        page.wait_for_timeout(180)
        page.click("#onb-sheet .onb-go")
        page.wait_for_timeout(600)
        check("POST /api/onboarding/done was called", CALLS["done"] == 1, CALLS["done"])
        check("the sheet closed", not page.evaluate(JS_OPEN))
        check("focus moved to the chat input",
              page.evaluate("() => document.activeElement && "
                            "document.activeElement.id === 'input'"),
              page.evaluate("() => document.activeElement && document.activeElement.id"))

        # ==================================================================
        print("\n[8] safety + hygiene")
        check("/api/models/download was NEVER called", CALLS["download"] == 0,
              CALLS["download"])
        mine = [e for e in errs if "aux_onboarding" in (e["stack"] or "")]
        check("no page error originates in aux_onboarding.js", mine == [], mine[:3])
        others = sorted(set(e["msg"] for e in errs))
        print("       (%d unrelated error(s) from other aux modules the "
              "catch-all fixture under-feeds: %s)" % (len(errs), others[:4]))
        check("reduced-motion rule is present",
              page.evaluate("""() => {
                const css = document.getElementById('onb-css');
                return !!css && css.textContent.indexOf('prefers-reduced-motion') > -1;
              }"""))
        check("no 'transition: all' anywhere in the sheet CSS",
              page.evaluate("""() => {
                const css = document.getElementById('onb-css');
                return !!css && !/transition\\s*:\\s*all/.test(css.textContent);
              }"""))
        check("text-wrap: pretty is used",
              page.evaluate("""() => {
                const css = document.getElementById('onb-css');
                return !!css && css.textContent.indexOf('text-wrap:pretty') > -1;
              }"""))
        check("the page body never scrolls horizontally",
              page.evaluate("() => document.documentElement.scrollWidth <= "
                            "document.documentElement.clientWidth + 1"))

        # ==================================================================
        print("\n[9] round 3: a step registered through the extra-steps hook")
        hpage = ctx.new_page()
        herrs = []
        hpage.on("pageerror", lambda e: herrs.append(
            {"msg": str(e), "stack": (getattr(e, "stack", "") or "")}))
        install_routes(hpage)
        hpage.add_init_script(EXTRA_STEP_JS)      # BEFORE aux_onboarding.js loads
        CUR["state"] = state_fixture(64, done=False)
        CALLS["apply"][:] = []
        hpage.goto(ORIGIN + "/", wait_until="domcontentloaded")
        hpage.wait_for_function(JS_OPEN, timeout=8000)
        check("the pre-load push is adopted: welcome, models, prefs, harness, done",
              hpage.evaluate("window.hermesOnboarding.steps().join(',')") ==
              "welcome,models,prefs,harness,done",
              hpage.evaluate("window.hermesOnboarding.steps()"))
        check("'Step 1 of 5'", hpage.inner_text("#onb-sheet .onb-step-n").strip() == "Step 1 of 5")
        hpage.evaluate("window.hermesOnboarding.go(2)")
        hpage.wait_for_timeout(150)
        check("its status row joins the Connections rows",
              "A row from the registered step" in hpage.inner_text("#onb-sheet"))
        hpage.click("#onb-sheet .onb-go")          # prefs -> harness
        hpage.wait_for_timeout(350)
        check("Continue on prefs lands on the extra step",
              hpage.evaluate("window.hermesOnboarding.steps()[window.hermesOnboarding.state.step]")
              == "harness")
        t9 = hpage.inner_text("#onb-sheet")
        check("its own title, eyebrow and body render", "Harness step" in t9 and
              "CONNECTIONS" in t9.upper() and "An extra step body." in t9, t9[:300])
        check("...wired with the step's body node", hpage.evaluate("window.__hxWired") >= 1)
        check("an extra step offers Skip next to Continue",
              hpage.eval_on_selector_all("#onb-sheet .onb-foot button[data-act='skip']",
                                         "e=>e.length") == 1)
        for theme in ("light", "dark"):
            set_theme(hpage, theme)
            hpage.wait_for_timeout(120)
            SHOT_PATHS.append(shot(hpage, "64gb-%s-4-extra-step" % theme))
        set_theme(hpage, "light")
        hpage.fill("#onb-sheet .hx-in", "typed by the owner")
        hpage.evaluate("window.__hxReply = {err: 'That key cannot see the repository.'}")
        hpage.click("#onb-sheet .onb-go")
        hpage.wait_for_timeout(300)
        check("save() {err}: the sheet stays on the step",
              hpage.evaluate("window.hermesOnboarding.steps()[window.hermesOnboarding.state.step]")
              == "harness")
        check("...the error shows in the sheet's error line",
              hpage.inner_text("#onb-sheet .onb-err").strip() ==
              "That key cannot see the repository." and hpage.is_visible("#onb-sheet .onb-err"))
        check("...and what the owner typed is still there (no re-render)",
              hpage.input_value("#onb-sheet .hx-in") == "typed by the owner")
        check("...and focus is still inside the sheet (the action row), not parked on <body>",
              hpage.evaluate("() => !!document.activeElement && "
                             "document.activeElement !== document.body && "
                             "!!document.activeElement.closest('#onb-sheet')"))
        SHOT_PATHS.append(shot(hpage, "64gb-light-4-extra-step-error"))
        saves = hpage.evaluate("window.__hxSaves")
        hpage.click("#onb-sheet .onb-foot button[data-act='skip']")
        hpage.wait_for_timeout(300)
        check("Skip goes on to Ready WITHOUT calling save()",
              hpage.evaluate("window.hermesOnboarding.state.step") == 4 and
              hpage.evaluate("window.__hxSaves") == saves)
        hpage.evaluate("window.hermesOnboarding.go(3)")
        hpage.wait_for_timeout(200)
        hpage.keyboard.press("Escape")
        hpage.wait_for_timeout(150)
        check("first run: Esc on the extra step is swallowed", hpage.evaluate(JS_OPEN))
        hpage.evaluate("window.__hxReply = null")
        hpage.click("#onb-sheet .onb-go")
        hpage.wait_for_timeout(350)
        t9b = hpage.inner_text("#onb-sheet")
        check("a clean save(): its applied line and summary() line are on the Ready step",
              "Harness: saved by its own step" in t9b and "Harness: its summary line" in t9b,
              t9b[:500])
        check("'Step 5 of 5' and Start", hpage.inner_text("#onb-sheet .onb-step-n").strip() ==
              "Step 5 of 5" and hpage.inner_text("#onb-sheet .onb-go").strip() == "Start")
        small9 = hpage.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('#onb-sheet .onb-foot button').forEach(e => {
            const r = e.getBoundingClientRect();
            if (r.width && r.height < 39.5) bad.push(e.textContent + ':' + Math.round(r.height));
          });
          return bad;
        }""")
        check("every action-row button has a >=40px hit area", small9 == [], small9)
        mine9 = [e for e in herrs if "aux_onboarding" in (e["stack"] or "")]
        check("no page error from aux_onboarding.js with the extra step", mine9 == [], mine9[:3])
        hpage.close()

        # ==================================================================
        print("\n[10] round 3: the models step through a (stub) model advisor")
        apage = ctx.new_page()
        aerrs = []
        apage.on("pageerror", lambda e: aerrs.append(
            {"msg": str(e), "stack": (getattr(e, "stack", "") or "")}))
        install_routes(apage)
        apage.add_init_script(ADVISOR_STUB_JS)
        CUR["state"] = state_fixture(64, done=False)
        CUR.pop("adv_400", None)
        CALLS["apply"][:] = []
        CALLS["adv_answers"][:] = []
        CALLS["adv_state"] = 0
        apage.goto(ORIGIN + "/", wait_until="domcontentloaded")
        apage.wait_for_function(JS_OPEN, timeout=8000)
        apage.evaluate("window.hermesOnboarding.go(1)")
        apage.wait_for_function("() => /Qwen3\\.8-27B as the assistant/.test("
                                "(document.querySelector('#onb-sheet .adv-plan')||{}).textContent||'')",
                                timeout=4000)
        t10 = apage.inner_text("#onb-sheet")
        check("the advisor renders the models step's body (its own routes, stubbed here)",
              apage.eval_on_selector_all("#onb-sheet .onb-adv .adv-stub", "e=>e.length") == 1
              and CALLS["adv_state"] >= 1)
        check("...instead of the legacy recommendation block",
              apage.eval_on_selector_all("#onb-sheet .onb-rec", "e=>e.length") == 0)
        check("...under the sheet's own heading", "Your Mac, and the brain that fits it" in t10)
        check("the action row quotes the advisor's plan: 'Download recommended (~23 GB)'",
              apage.inner_text("#onb-sheet .onb-go").strip() == "Download recommended (~23 GB)",
              apage.inner_text("#onb-sheet .onb-go"))
        geo = apage.evaluate("""() => {
          const b = document.querySelector('#onb-sheet .onb-adv').getBoundingClientRect();
          const f = document.querySelector('#onb-sheet .onb-foot').getBoundingClientRect();
          return [b.top, f.top];
        }""")
        check("...and sits BELOW the advisor body", geo[0] < geo[1], geo)
        check("setPick repainted only the action row (the advisor wired once)",
              apage.evaluate("window.__advWires") == 1, apage.evaluate("window.__advWires"))
        for theme in ("light", "dark"):
            set_theme(apage, theme)
            apage.wait_for_timeout(120)
            SHOT_PATHS.append(shot(apage, "64gb-%s-2-models-advisor" % theme))
        set_theme(apage, "light")
        CUR["adv_400"] = "uses: 'uncensored' needs uncensored_ack: true"
        apage.click("#onb-sheet .onb-foot button[data-act='later']")
        apage.wait_for_timeout(350)
        check("the advisor's 400 keeps the sheet on the models step",
              apage.evaluate("window.hermesOnboarding.state.step") == 1)
        check("...with its errors in the error line",
              "needs uncensored_ack" in apage.inner_text("#onb-sheet .onb-err"))
        check("...and nothing was posted to /api/onboarding/apply",
              not any("primary" in b for b in CALLS["apply"]), CALLS["apply"])
        CUR.pop("adv_400", None)
        apage.click("#onb-sheet .onb-foot button[data-act='later']")
        apage.wait_for_timeout(350)
        check("'later' after a clean save: the answers were posted, no model pick, step 3",
              len(CALLS["adv_answers"]) == 2 and apage.evaluate("window.hermesOnboarding.state.step") == 2
              and not any("primary" in b for b in CALLS["apply"]), CALLS["adv_answers"])
        apage.evaluate("window.hermesOnboarding.go(1)")
        apage.wait_for_timeout(250)
        CALLS["apply"][:] = []
        apage.click("#onb-sheet .onb-go")          # Download recommended (~23 GB)
        apage.wait_for_timeout(400)
        dl = [b for b in CALLS["apply"] if b.get("download") is True]
        check("Download posts the advisor's picks to apply, download:true",
              len(dl) == 1 and dl[0].get("primary") == M27B and dl[0].get("background") == M9B,
              CALLS["apply"])
        check("...and the progress block shows under the advisor body",
              "Downloading" in apage.inner_text("#onb-sheet .onb-dl"))
        check("/api/models/download was never called", CALLS["download"] == 0, CALLS["download"])
        mine10 = [e for e in aerrs if "aux_onboarding" in (e["stack"] or "")]
        check("no page error from aux_onboarding.js with the advisor", mine10 == [], mine10[:3])
        apage.close()

        browser.close()


try:
    run()
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("EXCEPTION")

print("\nscreenshots (%d) in %s" % (len(SHOT_PATHS), SHOTS))
for p in SHOT_PATHS:
    print("   " + p)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("   - %s" % f)
sys.exit(1 if FAILS else 0)
