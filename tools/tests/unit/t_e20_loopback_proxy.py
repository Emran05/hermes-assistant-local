#!/usr/bin/env python3
"""E20 (2026-11-09 audit, P2): a system web proxy must never see the
dashboard's loopback model calls.

BEFORE: server.py installed `build_opener(_ModelAuthHandler())`, which keeps
urllib's default ProxyHandler. With a macOS "Web Proxy (HTTP)" set (or
http_proxy in the environment) every context-less urlopen the dashboard makes
to 127.0.0.1:8080/8081 — the liveness probes, clip transforms, the drill,
evals, For-You, the Needs-you pass, watchtower, dictation — went to the
proxy, prompt text and the model bearer key included, and model_online()
read False for a model that was up.

AFTER: the installed opener routes loopback URLs around any proxy (external
URLs keep the owner's proxy), and `local_urlopen()` is the documented
proxy-free, redirect-free helper for other modules.

REAL server.py code (ast-extracted: the opener pieces, the install_opener
statement, model_online, _bg_probe, local_urlopen); a scratch listener plays
the proxy, a second one plays the model server. Both the env-proxy path and
the macOS system-proxy path (urllib's _scproxy entry points replaced, the
verifier's method) are driven. Offline, scratch HOME.
"""
import ast
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
SRC = os.path.join(REPO, "dashboard", "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-400:]))


SEEN = []          # what the "proxy" received
MODEL_SEEN = []    # what the "model server" received


class Proxy(http.server.BaseHTTPRequestHandler):
    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        b = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        SEEN.append((self.requestline, self.headers.get("Authorization"), b[:80]))
        self.send_response(502)
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_GET = do_POST = _h

    def log_message(self, *a):
        pass


class Model(http.server.BaseHTTPRequestHandler):
    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        b = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        MODEL_SEEN.append((self.command, self.path, self.headers.get("Authorization"), b[:80]))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"data": [{"id": "m"}]}' if self.command == "GET" else \
            b'{"choices": [{"message": {"content": "ok"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = _h

    def log_message(self, *a):
        pass


def serve(handler):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


WANT_FUNCS = {"model_online", "_bg_probe", "_model_api_key", "_is_model_server_url",
              "_is_loopback_url", "local_urlopen"}
WANT_CLASSES = {"_ModelAuthHandler", "_LoopbackDirectProxyHandler", "_NoRedirectHandler"}
WANT_ASSIGNS = {"_model_key_memo", "_LOCAL_OPENER"}


def extract(src):
    tree = ast.parse(src, filename=SRC)
    keep, names = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS:
            keep.append(n); names.add(n.name)
        elif isinstance(n, ast.ClassDef) and n.name in WANT_CLASSES:
            keep.append(n); names.add(n.name)
        elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANT_ASSIGNS for t in n.targets):
            keep.append(n)
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.Expr) and "install_opener" in (ast.get_source_segment(src, n) or ""):
            keep.append(n); names.add("<install_opener>")
    # the opener statements reference the classes: keep source order
    return ast.Module(body=keep, type_ignores=[]), names


def build(home, model_port, bg_port):
    src = open(SRC, encoding="utf-8").read()
    mod, names = extract(src)
    data = os.path.join(home, "data")
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "model-api-key"), "w") as f:
        f.write("scratch-test-key\n")
    ns = {"os": os, "sys": sys, "urllib": urllib,
          "MODEL_URL": "http://127.0.0.1:%d/v1/models" % model_port,
          "BG_MODEL_URL": "http://127.0.0.1:%d/v1/models" % bg_port,
          "MODEL_API_KEY_FILE": os.path.join(data, "model-api-key")}
    exec(compile(mod, SRC, "exec"), ns)
    return ns, names


def clean_env():
    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            del os.environ[k]


def run_case(label, home, set_proxy):
    """set_proxy(px_url) installs the proxy setting BEFORE the opener is built
    (urllib's ProxyHandler snapshots getproxies() at construction, exactly as
    server.py's import-time install_opener does)."""
    print("\n%s" % label)
    SEEN.clear(); MODEL_SEEN.clear()
    px = serve(Proxy)
    mdl = serve(Model)
    bg_dead = serve(Model)
    bg_port = bg_dead.server_address[1]
    bg_dead.shutdown(); bg_dead.server_close()          # the bg lane is down
    saved = urllib.request._opener
    try:
        set_proxy("http://127.0.0.1:%d" % px.server_address[1])
        ns, names = build(home, mdl.server_address[1], bg_port)
        check("model_online() is True for a model that is up",
              ns["model_online"]() is True)
        check("_bg_probe() is False for a lane that is down (no proxy answer)",
              ns["_bg_probe"]() is False)
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % mdl.server_address[1],
            data=json.dumps({"model": "m", "messages": [
                {"role": "user", "content": "PRIVATE CLIPBOARD TEXT"}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:     # what aux_clip & co do
                out = json.loads(r.read().decode())
        except Exception as e:
            out = {"error": repr(e)}
        check("a context-less loopback POST reaches the model directly",
              out.get("choices") and MODEL_SEEN and MODEL_SEEN[-1][0] == "POST", MODEL_SEEN)
        check("the model call still carries the bearer key (stamped, not lost)",
              MODEL_SEEN and MODEL_SEEN[-1][2] == "Bearer scratch-test-key", MODEL_SEEN)
        lh = urllib.request.Request("http://localhost:%d/v1/models" % mdl.server_address[1])
        try:
            with urllib.request.urlopen(lh, timeout=5) as r:
                check("localhost spelling goes direct too", r.status == 200)
        except Exception as e:
            check("localhost spelling goes direct too", False, repr(e))
        check("the PROXY received nothing (no prompt, no key, no probe)",
              SEEN == [], SEEN)
        # the external path keeps the owner's proxy (not loopback -> proxied)
        SEEN.clear()
        try:
            urllib.request.urlopen("http://example.invalid/feed", timeout=5)
        except urllib.error.HTTPError as e:
            check("an EXTERNAL http URL still goes through the configured proxy",
                  e.code == 502 and SEEN and "example.invalid" in SEEN[0][0], SEEN)
        except Exception as e:
            check("an EXTERNAL http URL still goes through the configured proxy",
                  False, "%s: %s" % (type(e).__name__, e))
        # the documented helper
        SEEN.clear(); MODEL_SEEN.clear()
        check("server.py defines local_urlopen()", "local_urlopen" in ns)
        if "local_urlopen" in ns:
            with ns["local_urlopen"](req, timeout=5) as r:
                check("local_urlopen: direct, proxy untouched, key stamped",
                      r.status == 200 and SEEN == [] and MODEL_SEEN
                      and MODEL_SEEN[-1][2] == "Bearer scratch-test-key", (SEEN, MODEL_SEEN))
            try:
                ns["local_urlopen"]("http://example.invalid/x", timeout=2)
                check("local_urlopen refuses a non-loopback URL", False, "no error")
            except ValueError:
                check("local_urlopen refuses a non-loopback URL", True)
            except Exception as e:
                check("local_urlopen refuses a non-loopback URL", False, repr(e))
            try:
                ns["local_urlopen"]("http://127.0.0.1:%d/redirect" % mdl.server_address[1],
                                    timeout=2)
                check("local_urlopen never follows a redirect", False, "followed")
            except urllib.error.HTTPError as e:
                check("local_urlopen never follows a redirect", e.code == 302 and SEEN == [],
                      (e.code, SEEN))
            except Exception as e:
                check("local_urlopen never follows a redirect", False, repr(e))
            for u, want in (("http://127.0.0.1:1/x", True), ("http://127.9.9.9/x", True),
                            ("http://[::1]:8080/x", True), ("http://localhost/x", True),
                            ("http://LOCALHOST:1/x", True),
                            ("http://127.0.0.1.nip.io/x", False), ("http://10.0.0.1/x", False),
                            ("http://evil@example.com/x", False), ("http://0.0.0.0/x", False),
                            ("file:///etc/hosts", False), ("http://[bad/x", False)):
                check("_is_loopback_url(%r) is %s" % (u, want),
                      ns["_is_loopback_url"](u) is want)
    finally:
        urllib.request._opener = saved
        clean_env()
        px.shutdown(); mdl.shutdown()


def main():
    clean_env()
    with tempfile.TemporaryDirectory(prefix="hermes-e20-") as home:
        def env_proxy(px):
            os.environ["http_proxy"] = px
            os.environ["HTTP_PROXY"] = px
        run_case("1. http_proxy in the environment", os.path.join(home, "a"), env_proxy)

        # 2. the macOS SYSTEM proxy path: no env vars; urllib's _scproxy entry
        # points answer what SystemConfiguration does with "Web Proxy (HTTP)"
        # on and the default bypass list (*.local, 169.254/16).
        gp = getattr(urllib.request, "_get_proxies", None)
        gs = getattr(urllib.request, "_get_proxy_settings", None)
        if gp is None or gs is None:
            # not darwin: getproxies() is env-only here, so case 1 covers it
            check("system-proxy path (darwin only) — env path already proved", True)
        else:
            def sys_proxy(px):
                urllib.request._get_proxies = lambda: {"http": px}
                urllib.request._get_proxy_settings = lambda: {
                    "exclude_simple": False, "exceptions": ("*.local", "169.254/16")}
            try:
                run_case("2. macOS system Web Proxy (HTTP), default bypass list",
                         os.path.join(home, "b"), sys_proxy)
            finally:
                urllib.request._get_proxies = gp
                urllib.request._get_proxy_settings = gs
    print("\n3. the drill and evals completions use the proxy-free helper")
    for name in ("aux_promotion.py", "aux_evals.py"):
        src = open(os.path.join(REPO, "dashboard", name), encoding="utf-8").read()
        check("%s resolves local_urlopen by name for its model call" % name,
              'globals().get("local_urlopen")' in src)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
