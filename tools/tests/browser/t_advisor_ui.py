#!/usr/bin/env python3
"""Headless browser suite for the model advisor's UI (round 3, C3;
docs/plans/model-advisor-2026-10.md §8 and §10).

HERMETIC. The REAL dashboard/index.html and every aux_*.js / vendor file are
served off disk by a throwaway http.server on 127.0.0.1:<random port> (never
the dashboard's 7788), and every /api answer comes from this process:

  * the advisor routes run the REAL aux_advisor.py route functions out of the
    REAL server.py, loaded against a throwaway HOME, with `_adv_rank_local`
    swapped for three SYNTHETIC Macs (16 GB M2 Air, 36 GB M3 Pro, 64 GB M5 Max)
    so no detection command runs and the ranking is the shipping code's;
  * this process imports tools/tests/lib/usercustomize.py with
    HERMES_TESTS_OFFLINE=1 first, so the server.py it loads (and its aux
    loops) cannot reach the network, a model or the dashboard;
  * POST /api/models/download is stubbed to FAIL LOUDLY: any call fails the
    suite (nothing here may ever start a download);
  * every request the browser makes is recorded; one that leaves the fixture
    origin fails the suite (and the browser runs behind a dead proxy for
    anything that is not loopback).

DRIVERS. Playwright (sync API) when it is importable; otherwise a small
stdlib Chrome DevTools Protocol driver for a Chromium found in the Playwright
cache (or $HERMES_CHROME). Neither available -> one SKIP line, exit 0.

Covers: the onboarding step body for the 16/36/64 GB fixtures in BOTH themes
(screenshots), a re-rank within one debounce of a real click, the Settings
card mounted as the FIRST card of #sec-models, the model menu's "why this
fits" rows and Uncensored badge, the keyboard (Space toggles a chip, arrow
keys move the priority radio group, Tab order, visible focus), reduced
motion (no inline style left either way), the narrow (<=760 px) single-column
layout with no horizontal scroll, >=40 px targets, zero emoji, and no page
error from aux_advisor.js; and (R3-seams motion-overlays-4) a plan action
pressed from the keyboard gives focus back to its button once the round
trip ends, instead of leaving it where the engine's focus fixup parked it.
"""
import base64
import contextlib
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
LIB = os.path.join(REPO, "tools", "tests", "lib")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:700]))


def skip_all(why):
    print("SKIP " + why)
    print("TESTS 0 passed 0 failed")
    sys.exit(0)


# --------------------------------------------------------------------------
# the offline guard, explicitly (a venv python skips usercustomize at start)
# --------------------------------------------------------------------------
os.environ["HERMES_TESTS_OFFLINE"] = "1"
sys.path.insert(0, LIB)
try:
    import usercustomize  # noqa: E402,F401
    GUARD = bool(getattr(usercustomize, "HERMES_OFFLINE_GUARD", False))
except Exception:
    GUARD = False

# --------------------------------------------------------------------------
# drivers available?
# --------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright  # noqa: E402
    HAVE_PW = True
except Exception:
    HAVE_PW = False


def find_chrome():
    env = os.environ.get("HERMES_CHROME")
    if env:
        return env if os.path.exists(env) else None
    home = os.path.expanduser("~")
    cands = []
    for root in (os.path.join(home, "Library", "Caches", "ms-playwright"),
                 os.path.join(home, ".cache", "ms-playwright")):
        try:
            names = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for n in names:
            d = os.path.join(root, n)
            try:
                subs = os.listdir(d)
            except OSError:
                continue
            for s in subs:
                if n.startswith("chromium_headless_shell-"):
                    cands.append(os.path.join(d, s, "chrome-headless-shell"))
                elif n.startswith("chromium-"):
                    cands.append(os.path.join(d, s, "Chromium.app", "Contents", "MacOS", "Chromium"))
                    cands.append(os.path.join(d, s, "chrome"))
    return next((c for c in cands if os.path.exists(c)), None)


CHROME = None if HAVE_PW else find_chrome()
if not HAVE_PW and not CHROME:
    skip_all("neither Playwright nor a Chromium build is available "
             "(pip install playwright && playwright install chromium, or set HERMES_CHROME)")
if not GUARD:
    skip_all("the offline guard (tools/tests/lib/usercustomize.py) did not activate; "
             "refusing to load server.py unguarded")
for need in ("index.html", "aux_advisor.js", "aux_anim.js"):
    if not os.path.isfile(os.path.join(DASH, need)):
        skip_all("dashboard/%s is missing" % need)

# --------------------------------------------------------------------------
# the REAL advisor routes over synthetic Macs
# --------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="hermes-advui-browser-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
sys.path.insert(0, DASH)
_spec = importlib.util.spec_from_file_location("hermes_server_advui_browser", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(_spec)
sys.modules["hermes_server_advui_browser"] = srv
with contextlib.redirect_stdout(io.StringIO()):
    _spec.loader.exec_module(srv)

M27 = "mlx-community/Qwen3.8-27B-4bit"
M9 = "mlx-community/Qwen3.5-9B-4bit"
M4 = "mlx-community/Qwen3.5-4B-4bit"
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
MACS = {
    "m16": ("Apple M2", 16, 10, "MacBook Air", 48.0),
    "m36": ("Apple M3 Pro", 36, 18, "MacBook Pro", 210.0),
    "m64": ("Apple M5 Max", 64, 40, "MacBook Pro", 412.0),
}
CUR = {"m16": (M27, None), "m36": (M27, M9), "m64": (M27, M9)}
DL = {"m16": set(), "m36": {M27}, "m64": {M27}}
HW = {k: srv._adv_machine(m[0], m[1], gpu_cores=m[2], machine_name=m[3], disk_free_gb=m[4],
                          macos="26.1") for k, m in MACS.items()}
MAC = {"k": "m64"}


def _fake_local(answers=None, hw=None, bust=False):
    k = MAC["k"]
    inputs = {"measured": {}, "prefix": {}, "drills": {}, "bg_wanted": True,
              "downloaded": set(DL[k]), "current": CUR[k], "profile": None}
    res = srv._adv_rank(HW[k], answers if answers is not None else srv._adv_answers_stored(),
                        downloaded=set(DL[k]), current=CUR[k])
    return res, HW[k], inputs


srv._adv_rank_local = _fake_local          # resolved by name at call time
srv._machine_ram_gb = lambda: MACS[MAC["k"]][1]
srv._disk_free_gb = lambda: MACS[MAC["k"]][4]
srv.bg_online = lambda *a, **k: False   # no :8081 probe from models_payload()
_wc = getattr(srv, "_widget_cache", None)


class _Ctx:
    def __init__(self, query=None, body=None):
        self.query, self.body, self.raw_path, self.headers = query or {}, body, "", {}


def _route(out):
    if isinstance(out, tuple):
        return out[1], out[0]
    return 200, out


def models_payload():
    if isinstance(_wc, dict):
        _wc.pop("adv_advice", None)       # the advice cache is per process; the Mac changed
    mp = srv.models_payload()
    mp["disk_free_gb"] = MACS[MAC["k"]][4]
    return mp


CAPS = {"skills": {"total": 3, "categories": [], "items": []}, "memory": {"count": 0, "facts": []},
        "insights": {"sessions": 2, "tool_calls": 5, "messages": 9, "input_tokens": 10,
                     "output_tokens": 4, "by_day": [], "platforms": []}}

# --------------------------------------------------------------------------
# the fixture server
# --------------------------------------------------------------------------
LOG = {"req": [], "download": 0, "add": [], "apply": [], "switch": [], "answers": []}
ADD_DELAY = [0.0]       # seconds /api/models/add takes (section [7b] needs a real round trip)
MIME = {".js": "application/javascript", ".html": "text/html; charset=utf-8",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}
STATIC_RE = re.compile(r"^/(?:[A-Za-z0-9_.-]+\.(?:js|css)|vendor/(?:motion|gsap|anime)/[A-Za-z0-9._-]+\.js)$")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method, path, q, body):
        if path == "/api/advisor/state":
            return _route(srv._adv_state_route(_Ctx(q)))
        if path == "/api/advisor/rank":
            return _route(srv._adv_rank_route(_Ctx(q)))
        if path == "/api/advisor/answers" and method == "POST":
            LOG["answers"].append(body)
            return _route(srv._adv_answers_route(_Ctx(body=body)))
        if path == "/api/models" and method == "GET":
            return 200, models_payload()
        if path == "/api/models/download":
            LOG["download"] += 1
            return 500, {"ok": False, "error": "STUB: no real downloads"}
        if path == "/api/models/add":
            LOG["add"].append(body)
            if ADD_DELAY[0]:
                time.sleep(ADD_DELAY[0])
            return 200, {"ok": True}
        if path == "/api/models/switch":
            LOG["switch"].append(body)
            return 200, {"ok": True, "loading": True}
        if path == "/api/onboarding/apply":
            LOG["apply"].append(body)
            return 200, {"ok": True, "applied": dict(body or {}), "errors": []}
        if path == "/api/onboarding/state":
            return 200, {"ok": True, "done": True, "sysinfo": {}, "detect": {}, "prefs": {},
                         "recommendation": {}}
        if path == "/api/capabilities":
            return 200, CAPS
        return 200, {"ok": True}

    def _handle(self, method):
        u = urllib.parse.urlsplit(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        LOG["req"].append((method, self.path))
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
        if path.startswith("/api/"):
            try:
                st, obj = self._api(method, path, q, body)
            except Exception as e:      # a fixture bug must be loud, not a hang
                st, obj = 500, {"ok": False, "error": "fixture: %s: %s" % (type(e).__name__, e)}
            return self._send(st, obj)
        fn = "index.html" if path in ("/", "/index.html") else (path.lstrip("/") if STATIC_RE.match(path) else None)
        if path == "/motion.min.js":
            fn = "vendor/motion/motion-14.6.0.min.js"
        if fn:
            fp = os.path.join(DASH, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, raw=f.read(), ctype=MIME.get(os.path.splitext(fp)[1], "text/plain"))
        return self._send(404, raw=b"", ctype="text/plain")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


SRV = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % SRV.server_address[1]
SHOTS = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(prefix="hermes-advui-shots-")
os.makedirs(SHOTS, exist_ok=True)

# errors from the page, kept with their source so only aux_advisor.js counts
INIT = r"""
window.__errs = [];
addEventListener('error', function (e) {
  window.__errs.push({msg: String(e.message), src: String(e.filename || ''), stack: String(e.error && e.error.stack || '')});
});
addEventListener('unhandledrejection', function (e) {
  window.__errs.push({msg: String(e.reason && e.reason.message || e.reason), src: '', stack: String(e.reason && e.reason.stack || '')});
});
"""


# --------------------------------------------------------------------------
# driver 1: Playwright
# --------------------------------------------------------------------------
class PWDriver:
    name = "playwright"

    def __init__(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch()
        self.ctx = self.browser.new_context(viewport={"width": 1180, "height": 860}, device_scale_factor=2)
        self.ctx.add_init_script(INIT)
        self.page = self.ctx.new_page()
        self.urls = []

        def guard(route):
            url = route.request.url
            self.urls.append(url)
            if url.startswith(BASE) or url.startswith("data:") or url.startswith("about:"):
                return route.continue_()
            return route.abort()
        self.page.route("**/*", guard)

    def goto(self, path):
        self.page.goto(BASE + path, wait_until="domcontentloaded")

    def js(self, expr):
        return self.page.evaluate(expr)

    def key(self, k, shift=False):
        self.page.keyboard.press(("Shift+" if shift else "") + k)

    def shot(self, path):
        self.page.screenshot(path=path, full_page=False)

    def viewport(self, w, h):
        self.page.set_viewport_size({"width": w, "height": h})

    def media(self, scheme=None, reduce=None):
        self.page.emulate_media(color_scheme=scheme or "light", reduced_motion=reduce or "no-preference")

    def close(self):
        for f in (self.ctx.close, self.browser.close, self.pw.stop):
            try:
                f()
            except Exception:
                pass


# --------------------------------------------------------------------------
# driver 2: the DevTools protocol, stdlib only
# --------------------------------------------------------------------------
class _WS:
    """A minimal RFC 6455 client: masked text frames out, any frames in."""

    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n" % (u.path, u.hostname, u.port, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.sock.recv(4096)
            if not c:
                raise IOError("handshake closed")
            buf += c
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
            raise IOError("handshake refused: %r" % head[:80])

    def send(self, text):
        p = text.encode()
        mask = os.urandom(4)
        n = len(p)
        hdr = struct.pack("!BB", 0x81, 0x80 | n) if n < 126 else (
            struct.pack("!BBH", 0x81, 0x80 | 126, n) if n < 65536 else struct.pack("!BBQ", 0x81, 0x80 | 127, n))
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))

    def _need(self, n, deadline):
        while len(self.buf) < n:
            self.sock.settimeout(max(0.01, deadline - time.time()))
            try:
                c = self.sock.recv(1 << 20)
            except socket.timeout:
                return False
            if not c:
                raise IOError("socket closed")
            self.buf += c
        return True

    def recv(self, timeout):
        """One whole text message, or None on timeout (bytes stay buffered)."""
        deadline = time.time() + timeout
        msg = b""
        while True:
            if not self._need(2, deadline):
                return None
            b1, b2 = self.buf[0], self.buf[1]
            n, off = b2 & 0x7F, 2
            if n == 126:
                if not self._need(4, deadline):
                    return None
                n, off = struct.unpack("!H", self.buf[2:4])[0], 4
            elif n == 127:
                if not self._need(10, deadline):
                    return None
                n, off = struct.unpack("!Q", self.buf[2:10])[0], 10
            if not self._need(off + n, deadline):
                return None
            data, self.buf = self.buf[off:off + n], self.buf[off + n:]
            op = b1 & 0x0F
            if op == 0x8:
                raise IOError("closed by the browser")
            if op in (0x9, 0xA):
                continue
            msg += data
            if b1 & 0x80:
                return msg.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class CDPDriver:
    name = "cdp"

    def __init__(self, chrome):
        self.prof = tempfile.mkdtemp(prefix="hermes-advui-chrome-")
        args = [chrome, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--remote-debugging-port=0", "--user-data-dir=" + self.prof, "--window-size=1180,860",
                # anything that is not loopback goes to a dead proxy
                "--proxy-server=127.0.0.1:9", "--proxy-bypass-list=127.0.0.1"]
        if not re.search(r"headless_shell|chrome-headless-shell", chrome):
            args.insert(1, "--headless=new")
        self.proc = subprocess.Popen(args + ["about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = None
        for _ in range(150):
            try:
                with open(os.path.join(self.prof, "DevToolsActivePort")) as f:
                    port = int(f.readline().strip())
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        if not port:
            self.close()
            raise RuntimeError("Chromium did not open its debugging port")
        targets = None
        for _ in range(50):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
                    targets = [t for t in json.loads(r.read()) if t.get("type") == "page"]
                if targets:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        if not targets:
            self.close()
            raise RuntimeError("no page target")
        self.ws = _WS(targets[0]["webSocketDebuggerUrl"])
        self.id = 0
        self.urls = []
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": INIT})
        self.viewport(1180, 860)

    def _event(self, m):
        if m.get("method") == "Network.requestWillBeSent":
            self.urls.append(m["params"]["request"]["url"])

    def call(self, method, params=None, timeout=30):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(max(0.05, deadline - time.time()))
            if raw is None:
                continue
            m = json.loads(raw)
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"]))
                return m.get("result") or {}
            self._event(m)
        raise RuntimeError("%s timed out" % method)

    def pump(self, secs):
        end = time.time() + secs
        while time.time() < end:
            raw = self.ws.recv(max(0.02, end - time.time()))
            if raw:
                self._event(json.loads(raw))

    def goto(self, path):
        self.call("Page.navigate", {"url": BASE + path})
        for _ in range(100):
            try:
                if self.js("document.readyState") in ("interactive", "complete"):
                    return
            except Exception:
                pass
            time.sleep(0.05)

    def js(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            ex = r["exceptionDetails"]
            raise RuntimeError("page: %s" % ((ex.get("exception") or {}).get("description") or ex.get("text")))
        return (r.get("result") or {}).get("value")

    KEYS = {"Space": (" ", "Space", 32, " "), "ArrowRight": ("ArrowRight", "ArrowRight", 39, ""),
            "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, ""), "ArrowDown": ("ArrowDown", "ArrowDown", 40, ""),
            "Tab": ("Tab", "Tab", 9, ""), "Enter": ("Enter", "Enter", 13, "\r")}

    def key(self, k, shift=False):
        key, code, vk, text = self.KEYS[k]
        mods = 8 if shift else 0
        down = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        if text:
            down["text"] = text
        self.call("Input.dispatchKeyEvent", down)
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code,
                                             "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
                                             "modifiers": mods})

    def shot(self, path):
        r = self.call("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r.get("data") or ""))

    def viewport(self, w, h):
        self.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h,
                                                         "deviceScaleFactor": 2, "mobile": False})

    def media(self, scheme=None, reduce=None):
        self.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": scheme or "light"},
            {"name": "prefers-reduced-motion", "value": reduce or "no-preference"}]})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.kill()
            self.proc.wait(5)
        except Exception:
            pass
        shutil.rmtree(self.prof, True)


# --------------------------------------------------------------------------
# page helpers
# --------------------------------------------------------------------------
# a sheet-like host: fixed, full viewport, the onboarding column's width, and
# a stub of the onboarding ctx (contract 2) that records setPick / repaint
MOUNT = r"""(async () => {
  let s = document.getElementById('t-sheet');
  if (s) s.remove();
  s = document.createElement('div');
  s.id = 't-sheet';
  s.setAttribute('style', 'position:fixed;inset:0;z-index:9000;overflow-y:auto;overflow-x:hidden;' +
    'background:var(--ground);color:var(--ink);font:14px/1.5 -apple-system,system-ui,sans-serif');
  s.innerHTML = '<div id="t-col" style="max-width:680px;margin:0 auto;padding:28px 16px"></div>';
  document.body.appendChild(s);
  window.__picks = []; window.__repaints = 0; window.__pick = null;
  window.__ctx = {rerun: false, data: {sysinfo: {chip: 'x', ram_gb: %(ram)d, laptop: true}},
    repaint() { window.__repaints++; }, setErr() {},
    getPick() { return window.__pick; }, setPick(p) { window.__picks.push(p); window.__pick = p; }};
  const col = document.getElementById('t-col');
  col.innerHTML = window.hermesAdvisor.step.render(window.__ctx);
  window.hermesAdvisor.step.wire(col, window.__ctx);
  for (let i = 0; i < 100; i++) {
    if (col.querySelector('input[data-use]') && col.querySelector('.adv-rec')) break;
    await new Promise(r => setTimeout(r, 30));
  }
  return !!col.querySelector('.adv-rec');
})()"""

SET_THEME = """(() => { document.documentElement.setAttribute('data-theme', '%s');
  try { localStorage.setItem('hermes_theme', '%s'); } catch (e) {} return 1; })()"""

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF]")
SHOT_PATHS = []


def api_count(prefix, method=None):
    return len([r for r in LOG["req"] if r[1].startswith(prefix) and (method is None or r[0] == method)])


def wait_for(drv, expr, secs=4.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if drv.js(expr):
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def shot(drv, name):
    p = os.path.join(SHOTS, name + ".png")
    try:
        drv.shot(p)
        SHOT_PATHS.append(p)
    except Exception as e:
        print("       (screenshot %s failed: %s)" % (name, e))


def boot(drv, mac):
    MAC["k"] = mac
    LOG["req"][:] = []
    drv.goto("/")
    ok = wait_for(drv, "!!(window.hermesAdvisor && window.hermesAdvisor.step && window.HermesAnim)", 10)
    time.sleep(0.4)
    return ok


def run(drv):
    print("  (driver: %s; fixture origin %s)" % (drv.name, BASE))

    # ======================================================================
    print("\n[1] 64 GB: nothing on load, then the step body")
    check("the page loads with the advisor and the motion runtime", boot(drv, "m64"))
    check("loading the page made NO advisor request", api_count("/api/advisor/") == 0,
          [r for r in LOG["req"] if "/advisor/" in r[1]])
    check("...and no POST at all", api_count("/api/", "POST") == 0, [r for r in LOG["req"] if r[0] == "POST"])
    check("the step body renders in a sheet-like host", drv.js(MOUNT % {"ram": 64}))
    check("one GET /api/advisor/state, nothing else from the advisor",
          api_count("/api/advisor/") == 1 and api_count("/api/advisor/state") == 1)
    t = drv.js("document.getElementById('t-col').innerText")
    check("64 GB: 27B as the assistant, 9B for background work",
          "Qwen3.8-27B as the assistant, Qwen3.5-9B for background work" in t, t[:300])
    check("setPick was handed the Recommended plan, once", drv.js("JSON.stringify(window.__picks)") ==
          json.dumps([{"primary": M27, "background": M9, "uncensored": False, "download_gb": 6}],
                     separators=(",", ":")), drv.js("JSON.stringify(window.__picks)"))
    check("ctx.repaint() is never called", drv.js("window.__repaints") == 0)
    for theme in ("light", "dark"):
        drv.js(SET_THEME % (theme, theme))
        time.sleep(0.25)
        shot(drv, "advisor-step-64gb-%s" % theme)
        bg = drv.js("getComputedStyle(document.querySelector('.adv-rec')).borderLeftColor")
        ink = drv.js("getComputedStyle(document.querySelector('.adv-plan-h')).color")
        check("%s: the plan is painted from the app's own tokens" % theme, bool(bg) and bool(ink), (bg, ink))
    drv.js(SET_THEME % ("light", "light"))
    time.sleep(0.1)
    light_ink = drv.js("getComputedStyle(document.querySelector('.adv-plan-h')).color")
    drv.js(SET_THEME % ("dark", "dark"))
    time.sleep(0.1)
    dark_ink = drv.js("getComputedStyle(document.querySelector('.adv-plan-h')).color")
    check("the theme switch reaches the advisor (inherited tokens, none re-declared)",
          light_ink and dark_ink and light_ink != dark_ink, (light_ink, dark_ink))
    drv.js(SET_THEME % ("light", "light"))

    # ---- live re-rank, within one debounce ---------------------------------
    n0 = api_count("/api/advisor/rank")
    t0 = time.time()
    drv.js("(() => { const c = document.querySelector('#t-col input[data-use=\"code\"]').closest('label');"
           " c.click(); document.querySelector('#t-col input[data-pri=\"best\"]').closest('label').click(); return 1; })()")
    time.sleep(0.06)
    check("no re-rank before the 150 ms debounce", api_count("/api/advisor/rank") == n0)
    got = wait_for(drv, "document.querySelector('#t-col [data-adv-part=\"status\"]').textContent.indexOf('Coding help') >= 0", 3)
    dt = time.time() - t0
    check("one re-rank for two quick answers, landed within ~one debounce + a round trip (%.0f ms)" % (dt * 1000),
          got and api_count("/api/advisor/rank") == n0 + 1 and dt < 1.5, (api_count("/api/advisor/rank") - n0, dt))
    rk = [r for r in LOG["req"] if r[1].startswith("/api/advisor/rank")][-1:]
    check("...asking for code + best", rk and "uses=chat%2Cagent%2Ccode" in rk[0][1] and "priority=best" in rk[0][1], rk)
    time.sleep(0.6)
    check("live motion: the re-rank's fade left no inline style behind",
          drv.js("document.querySelector('#t-col .adv-res').getAttribute('style')") in (None, ""),
          drv.js("document.querySelector('#t-col .adv-res').getAttribute('style')"))

    # ---- keyboard ------------------------------------------------------------
    print("\n[2] keyboard")
    drv.js("document.querySelector('#t-col input[data-use=\"docs\"]').focus(), 1")
    before = drv.js("document.querySelector('#t-col input[data-use=\"docs\"]').checked")
    drv.key("Space")
    time.sleep(0.1)
    check("Space toggles a chip", drv.js("document.querySelector('#t-col input[data-use=\"docs\"]').checked") != before)
    check("...and the focused chip shows a visible focus ring",
          drv.js("getComputedStyle(document.activeElement.closest('label')).outlineStyle") == "solid",
          drv.js("getComputedStyle(document.activeElement.closest('label')).outlineStyle"))
    drv.key("Space")                       # put it back
    drv.key("Tab")
    check("Tab moves on to the next chip, in order",
          drv.js("document.activeElement.getAttribute('data-use')") == "vision",
          drv.js("document.activeElement && document.activeElement.outerHTML.slice(0,80)"))
    drv.key("Tab", shift=True)
    check("Shift+Tab comes back", drv.js("document.activeElement.getAttribute('data-use')") == "docs")
    drv.js("document.querySelector('#t-col input[data-pri=\"faster\"]').closest('label').click(), "
           "document.querySelector('#t-col input[data-pri=\"faster\"]').focus(), 1")
    n1 = api_count("/api/advisor/rank")
    drv.key("ArrowRight")
    time.sleep(0.1)
    check("ArrowRight moves the priority radio group (Faster -> Balanced)",
          drv.js("document.querySelector('#t-col input[data-pri=\"balanced\"]').checked") is True and
          drv.js("document.activeElement.getAttribute('data-pri')") == "balanced",
          drv.js("document.activeElement && document.activeElement.getAttribute('data-pri')"))
    time.sleep(0.5)                        # one debounce + the round trip
    check("...and that re-ranks too", api_count("/api/advisor/rank") >= n1 + 1, api_count("/api/advisor/rank") - n1)

    # ---- the uncensored acknowledgement --------------------------------------
    print("\n[3] the uncensored chip")
    drv.js("document.querySelector('#t-col input[data-use=\"uncensored\"]').closest('label').click(), 1")
    time.sleep(0.15)
    check("the chip stays off until the acknowledgement",
          drv.js("document.querySelector('#t-col input[data-use=\"uncensored\"]').checked") is False)
    ack_txt = srv._ADVR_UNCENSORED_ACK_TEXT
    check("the server's acknowledgement copy is shown",
          ack_txt in drv.js("document.querySelector('#t-col .adv-ack').innerText"))
    shot(drv, "advisor-step-64gb-ack")
    drv.js("document.querySelector('#t-col input[data-ack]').closest('label').click(), 1")
    ok = wait_for(drv, "document.querySelector('#t-col input[data-use=\"uncensored\"]').checked === true", 3)
    check("checking it stores the acknowledgement (POST) and turns the chip on",
          ok and LOG["answers"] and LOG["answers"][-1] == {"uncensored_ack": True}, LOG["answers"][-1:])
    got = wait_for(drv, "[...document.querySelectorAll('#t-col li.adv-alt')].some(li => /Uncensored/.test(li.innerText))", 3)
    check("an uncensored alternative appears, never in the Recommended slot",
          got and "Uncensored" not in drv.js("document.querySelector('#t-col .adv-rec').innerText"))
    drv.js("document.querySelector('#t-col input[data-ack]').closest('label').click(), 1")
    wait_for(drv, "document.querySelector('#t-col input[data-use=\"uncensored\"]').checked === false", 3)
    check("withdrawing it is one click too", LOG["answers"][-1] == {"uncensored_ack": False}, LOG["answers"][-1:])

    # ======================================================================
    print("\n[4] 16 GB and 36 GB")
    for mac, want in (("m16", "Qwen3.5-4B as the assistant, with no background lane"),
                      ("m36", "Qwen3.5-9B as the assistant, Qwen3.5-4B for background work")):
        boot(drv, mac)
        drv.js(MOUNT % {"ram": MACS[mac][1]})
        t = drv.js("document.getElementById('t-col').innerText")
        check("%s: %s" % (mac, want), want in t, t[:400])
        for theme in ("light", "dark"):
            drv.js(SET_THEME % (theme, theme))
            time.sleep(0.25)
            shot(drv, "advisor-step-%s-%s" % (mac[1:] + "gb", theme))
        drv.js(SET_THEME % ("light", "light"))
    check("16 GB lists what will not fit, collapsed",
          (boot(drv, "m16") and drv.js(MOUNT % {"ram": 16}) and
           drv.js("(() => { const d = document.querySelector('#t-col details.adv-wont'); return !!d && !d.open && /won.t work well here/.test(d.innerText); })()")))

    # ======================================================================
    print("\n[5] narrow and wide layout")
    boot(drv, "m64")
    drv.js(MOUNT % {"ram": 64})
    cols_wide = drv.js("getComputedStyle(document.querySelector('#t-col .adv-top')).gridTemplateColumns.split(' ').length")
    check("wide: Detected and the questions sit side by side (2 columns)", cols_wide == 2, cols_wide)
    drv.viewport(375, 812)
    time.sleep(0.3)
    cols = drv.js("getComputedStyle(document.querySelector('#t-col .adv-top')).gridTemplateColumns.split(' ').length")
    check("narrow (375 px): one column", cols == 1, cols)
    check("...no horizontal scroll in the sheet", drv.js(
        "(() => { const s = document.getElementById('t-sheet'); return s.scrollWidth <= s.clientWidth + 1; })()"),
        drv.js("(() => { const s = document.getElementById('t-sheet'); return [s.scrollWidth, s.clientWidth]; })()"))
    check("...nothing inside overflows the column", drv.js(
        "(() => { const c = document.getElementById('t-col').getBoundingClientRect();"
        " return [...document.querySelectorAll('#t-col .adv *')].filter(e => { const r = e.getBoundingClientRect();"
        " return r.width && (r.right > c.right + 1 || r.left < c.left - 1); }).length; })()") == 0,
        drv.js("(() => { const c = document.getElementById('t-col').getBoundingClientRect();"
               " return [...document.querySelectorAll('#t-col .adv *')].filter(e => { const r = e.getBoundingClientRect();"
               " return r.width && (r.right > c.right + 1 || r.left < c.left - 1); }).slice(0,4).map(e => e.className + ':' + Math.round(e.getBoundingClientRect().right)); })()"))
    shot(drv, "advisor-step-64gb-narrow")
    small = drv.js("""(() => {
      const bad = [];
      document.querySelectorAll('#t-col .adv-chip, #t-col .adv-seg-o, #t-col .adv-pick, #t-col .adv-b,'
        + ' #t-col summary, #t-col .adv-check').forEach(e => {
        const r = e.getBoundingClientRect(); if (r.width && r.height < 39.5) bad.push(e.className + ':' + Math.round(r.height)); });
      return bad; })()""")
    check("every advisor control is a >=40 px target", small == [], small)
    drv.viewport(1180, 860)
    time.sleep(0.2)

    # ======================================================================
    print("\n[6] reduced motion")
    drv.media(reduce="reduce")
    time.sleep(0.2)
    inst0 = drv.js("(HermesAnim.stats().instant || {}).reduced || 0")
    drv.js("document.querySelector('#t-col input[data-use=\"vision\"]').closest('label').click(), 1")
    wait_for(drv, "document.querySelector('#t-col [data-adv-part=\"status\"]').textContent.indexOf('Screenshots') >= 0", 3)
    check("under reduce the re-rank lands at once (the instant path) with no inline style",
          drv.js("(HermesAnim.stats().instant || {}).reduced || 0") > inst0 and
          drv.js("document.querySelector('#t-col .adv-res').getAttribute('style')") in (None, ""))
    drv.media(reduce="no-preference")

    # ======================================================================
    print("\n[7] the Settings card")
    n_state = api_count("/api/advisor/state")
    drv.js("(() => { const s = document.getElementById('t-sheet'); if (s) s.remove(); return 1; })()")
    drv.js("(async () => { setView('mind'); await new Promise(r => setTimeout(r, 300));"
           " if (typeof mindExtras === 'function') await mindExtras(); return 1; })()")
    ok = wait_for(drv, "!!document.querySelector('#mind-extra-advisor .adv-rec')", 6)
    check("the card mounts with its plan", ok)
    pos = drv.js("""(() => { const c = document.getElementById('mind-extra-advisor'); const b = c && c.parentNode;
      return c ? {panel: !!c.closest('#sec-models'), first: b.firstElementChild === c, order: c.style.order,
                  inMindRoot: c.parentNode.id === 'view-mind'} : null; })()""")
    check("...as the FIRST card of #sec-models, order -1, never at the #view-mind root",
          pos and pos["panel"] and pos["first"] and pos["order"] == "-1" and not pos["inMindRoot"], pos)
    check("...without a second state read (this page already has it)", api_count("/api/advisor/state") == n_state)
    t = drv.js("document.getElementById('mind-extra-advisor').innerText")
    check("What you run now + 'Last analysed h:mm AM/PM · Re-check'",
          "WHAT YOU RUN NOW" in t.upper() and re.search(r"Last analysed \d{1,2}:\d\d (AM|PM)", t), t[:300])
    btn = drv.js("(() => { const b = document.querySelector('#mind-extra-advisor button[data-plan-act=\"download\"]');"
                 " return b ? b.textContent : null; })()")
    check("the Download button quotes the size and the free space", btn and re.match(r"Download \(~6\sGB · 412\sGB free\)", btn), btn)
    drv.js("(() => { location.hash = '#settings/models'; return 1; })()")
    wait_for(drv, "(() => { const r = document.getElementById('mind-extra-advisor').getBoundingClientRect();"
                  " return r.height > 100 && r.width > 200; })()", 3)
    box = drv.js("(() => { const c = document.getElementById('mind-extra-advisor'); const r = c.getBoundingClientRect();"
                 " const first = [...c.parentNode.children].filter(e => e.getBoundingClientRect().height > 0)"
                 ".sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];"
                 " return {h: Math.round(r.height), w: Math.round(r.width), topmost: first === c}; })()")
    check("Settings › Agent & Models shows the card, and it is the topmost card there",
          box and box["h"] > 100 and box["topmost"], box)
    time.sleep(0.3)
    shot(drv, "advisor-card-64gb-light")
    drv.js(SET_THEME % ("dark", "dark"))
    time.sleep(0.25)
    shot(drv, "advisor-card-64gb-dark")
    drv.js(SET_THEME % ("light", "light"))
    drv.js("window.__confirms = []; window.confirm = t => { window.__confirms.push(String(t)); return false; }; 1")
    drv.js("document.querySelector('#mind-extra-advisor button[data-plan-act=\"download\"]').click(), 1")
    time.sleep(0.3)
    check("Download asks first; a No sends nothing", drv.js("window.__confirms.length") == 1 and
          LOG["download"] == 0 and not LOG["add"])
    drv.js("document.querySelector('#mind-extra-advisor button[data-adv-act=\"recheck\"]').click(), 1")
    wait_for(drv, "!document.querySelector('#mind-extra-advisor button[data-adv-act=\"recheck\"]').disabled", 3)
    check("Re-check asks for ?bust=1", any(r[1] == "/api/advisor/state?bust=1" for r in LOG["req"]))

    # ======================================================================
    # R3-seams motion-overlays-4: planAction disables the focused button for
    # the round trip; the engine's focus fixup then parks focus on <body> and
    # re-enabling never gave it back, so a keyboard user lost their place
    print("\n[7b] a plan action pressed from the keyboard gives focus back to its button")
    ADD_DELAY[0] = 0.35
    n_add = len(LOG["add"])
    sel = '#mind-extra-advisor .adv-rec button[data-plan-act="add"]'
    on = drv.js("(() => { const b = document.querySelector('%s'); if (!b) return false; b.focus();"
                " return document.activeElement === b; })()" % sel)
    check("(the keyboard is on the Recommended plan's 'Add to model menu')", on)
    drv.key("Space")
    mid = wait_for(drv, "(() => { const b = document.querySelector('%s'); return !!b && b.disabled &&"
                        " document.activeElement === document.body; })()" % sel, 2)
    check("...while the add is in flight the button is disabled and the engine has parked focus on <body>", mid)
    back = wait_for(drv, "(() => { const b = document.querySelector('%s'); return !!b && !b.disabled; })()" % sel, 5)
    time.sleep(0.2)
    ADD_DELAY[0] = 0.0
    where = drv.js("(() => { const a = document.activeElement, b = document.querySelector('%s');"
                   " return a === b ? 'it' : (a === document.body ? 'body' : a.tagName + '.' + a.className); })()" % sel)
    check("...after it (one add per lane went out), focus is back on that button, never <body>",
          back and len(LOG["add"]) >= n_add + 1 and where == "it", (where, len(LOG["add"]) - n_add))

    # ======================================================================
    print("\n[8] the model menu: why this fits, and the Uncensored badge")
    drv.js("setView('hub'), 1")
    drv.js("(async () => { await loadModels(); return 1; })()")
    drv.js("(() => { const m = document.getElementById('model-menu'); m.hidden = false; return 1; })()")
    time.sleep(0.2)
    drv.js("(() => { const b = document.querySelector('#model-menu .mmdt[data-det=\"%s\"]'); b.click(); return 1; })()" % M27)
    time.sleep(0.25)
    dd = drv.js("(() => { const d = document.getElementById('mmdd-mlx-community_Qwen3_8-27B-4bit'); return d ? d.innerText : ''; })()")
    check("the 27B's Details show Speed here / First reply after wake / Memory with cache / Advisor",
          all(k in dd for k in ("Speed here", "First reply after wake", "Memory with cache", "Advisor")), dd[-400:])
    check("...and the advisor says recommended for you", "recommended for you" in dd)
    unc = drv.js("""(() => { const r = document.querySelector('#model-menu .mmi[data-id="%s"]');
      return r ? {flag: r.getAttribute('data-uncensored'), chip: !!r.querySelector('.mmunc svg'),
                  line: (r.querySelector('.mmunc-l') || {}).textContent || '',
                  color: getComputedStyle(r.querySelector('.mmunc')).color,
                  warn: getComputedStyle(document.documentElement).getPropertyValue('--warn').trim()} : null; })()""" % ORCA)
    check("the uncensored row is flagged and badged", unc and unc["flag"] == "1" and unc["chip"] and
          unc["line"] == "no refusals — the approval gate is the only brake", unc)
    shot(drv, "advisor-menu-light")
    drv.js(SET_THEME % ("dark", "dark"))
    time.sleep(0.25)
    shot(drv, "advisor-menu-dark")
    drv.js(SET_THEME % ("light", "light"))

    # ======================================================================
    print("\n[9] safety and hygiene")
    check("/api/models/download was NEVER called", LOG["download"] == 0, LOG["download"])
    check("/api/models/switch was never called without a click", not LOG["switch"], LOG["switch"])
    check("no onboarding apply was sent", not LOG["apply"], LOG["apply"])
    errs = drv.js("window.__errs") or []
    mine = [e for e in errs if "aux_advisor" in (e.get("src", "") + e.get("stack", ""))]
    check("no page error originates in aux_advisor.js", mine == [], mine[:3])
    print("       (%d unrelated page error(s) from modules the catch-all fixture under-feeds)" % (len(errs) - len(mine)))
    check("zero emoji in the advisor's surfaces", not EMOJI.search(
        drv.js("[...document.querySelectorAll('.adv, #model-menu')].map(e => e.innerHTML).join('')") or ""))
    leaked = [u for u in drv.urls if not (u.startswith(BASE) or u.startswith("data:") or u.startswith("about:"))]
    check("every request stayed on the fixture origin", leaked == [], leaked[:5])
    check("...and the dashboard's real port was never asked for anything",
          not [u for u in drv.urls if ":7788" in u])


def main():
    drv = None
    try:
        if HAVE_PW:
            try:
                drv = PWDriver()
            except Exception as e:
                c = find_chrome()
                if not c:
                    skip_all("Playwright is installed but could not launch Chromium (%s)" % e)
                drv = CDPDriver(c)
        else:
            drv = CDPDriver(CHROME)
    except Exception as e:
        skip_all("could not start a headless Chromium: %s" % e)
    try:
        run(drv)
    except Exception:
        import traceback
        traceback.print_exc()
        FAILS.append("EXCEPTION")
    finally:
        try:
            drv.close()
        except Exception:
            pass
        try:
            SRV.shutdown()
        except Exception:
            pass
        shutil.rmtree(TMP, True)
    print("\nscreenshots (%d) in %s" % (len(SHOT_PATHS), SHOTS))
    for p in SHOT_PATHS:
        print("   " + p)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    for f in FAILS:
        print("   - %s" % f)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
