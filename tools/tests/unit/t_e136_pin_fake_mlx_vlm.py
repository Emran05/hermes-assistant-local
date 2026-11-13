#!/usr/bin/env python3
"""E136 (2026-11-09 audit, P3): the launcher's single-model pin is tested in
the unit tier, on every machine — not only where the mlx-vlm venv exists.

BEFORE: _patch_pin_model / _install_vlm_guards ran only inside
t_c130_model_server_lockdown / t_r3_roster_launcher's venv layer, which is an
expected SKIP in CI and on any fresh machine. A mutant that drops the pin
(`_model_key(model_path) not in allowed`) survived every suite CI runs.

AFTER (this suite): the REAL launcher (imported by path, stdlib only) installs
its pin and guards into small FAKE `mlx_vlm.server`, `mlx_vlm.server.app`,
`fastapi` and `starlette.middleware.cors` modules with the attribute shapes
upstream 0.6.14 has, and the whole stack is then driven as ASGI: a fake chat
route resolves `mlx_vlm.server.get_cached_model` at call time exactly like
upstream's routes do. No venv, no model, no SKIP.

HERMES_T_LAUNCHER=<path> runs it against another launcher file (a mutant).
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
LAUNCHER = os.environ.get("HERMES_T_LAUNCHER") or os.path.join(REPO, "mlx-vlm-launch.py")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-400:]))


MODEL = "mlx-community/Qwen3.8-27B-4bit"
LOADS = []


class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class CORSMiddleware:
    pass


class _Mw:
    def __init__(self, cls, **kw):
        self.cls, self.kwargs = cls, kw


class FakeApp:
    """The bits of a Starlette app _install_vlm_guards touches; the middleware
    stack is built like Starlette builds it (user_middleware[0] outermost)."""

    def __init__(self):
        self.user_middleware = [_Mw(CORSMiddleware, allow_origins=["*"])]

    def add_middleware(self, cls, **kw):
        self.user_middleware.insert(0, _Mw(cls, **kw))

    async def route(self, scope, receive, send):
        msg = await receive()
        body = json.loads(msg.get("body") or b"{}")
        pkg = sys.modules["mlx_vlm.server"]          # upstream: resolved per call
        try:
            kw = {}
            if "adapter_path" in body:
                kw["adapter_path"] = body["adapter_path"]
            if "model_kind" in body:
                kw["model_kind"] = body["model_kind"]
            pkg.get_cached_model(body.get("model"), **kw)
            status, out = 200, b'{"ok": true}'
        except HTTPException as e:
            status, out = e.status_code, json.dumps({"detail": e.detail}).encode()
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": out})

    def stack(self):
        app = self.route
        for mw in reversed(self.user_middleware):
            if mw.cls is CORSMiddleware:
                continue                              # (would add CORS headers)
            app = mw.cls(app, **mw.kwargs)
        return app


def install_fakes():
    INHERIT = object()

    def get_cached_model(model_path, adapter_path=INHERIT, *, model_kind="auto"):
        LOADS.append((model_path, adapter_path is INHERIT and "inherit" or adapter_path, model_kind))
        return ("model", "processor", "config")

    app = FakeApp()
    appmod = types.ModuleType("mlx_vlm.server.app")
    appmod.get_cached_model, appmod._INHERIT_ADAPTER, appmod.app = get_cached_model, INHERIT, app
    pkg = types.ModuleType("mlx_vlm.server")
    pkg.get_cached_model, pkg.app, pkg.__path__ = get_cached_model, app, []
    root = types.ModuleType("mlx_vlm")
    root.__path__, root.server = [], pkg
    fastapi = types.ModuleType("fastapi")
    fastapi.HTTPException = HTTPException
    st, stm, cors = (types.ModuleType(n) for n in
                     ("starlette", "starlette.middleware", "starlette.middleware.cors"))
    cors.CORSMiddleware = CORSMiddleware
    st.__path__, stm.__path__ = [], []
    sys.modules.update({"mlx_vlm": root, "mlx_vlm.server": pkg, "mlx_vlm.server.app": appmod,
                        "fastapi": fastapi, "starlette": st, "starlette.middleware": stm,
                        "starlette.middleware.cors": cors})
    return app, appmod, pkg, get_cached_model


def load_launcher():
    spec = importlib.util.spec_from_file_location("mlx_vlm_launch_e136", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def call(app, body, path="/v1/chat/completions", host=b"127.0.0.1:8080", extra=()):
    out = []
    raw = json.dumps(body).encode()
    q = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return q.pop(0) if q else {"type": "http.disconnect"}

    async def send(m):
        out.append(m)
    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(b"host", host), (b"content-type", b"application/json")] + list(extra)}
    await app(scope, receive, send)
    return next((m["status"] for m in out if m["type"] == "http.response.start"), None)


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-e136-") as tmp:
        # a hub cache holding the launch model's snapshot (for the path alias)
        snap = os.path.join(tmp, "hub", "models--" + MODEL.replace("/", "--"), "snapshots", "abc123")
        os.makedirs(snap)
        os.makedirs(os.path.join(tmp, "hub", "models--" + MODEL.replace("/", "--"), "refs"))
        with open(os.path.join(tmp, "hub", "models--" + MODEL.replace("/", "--"), "refs", "main"), "w") as f:
            f.write("abc123")
        other = os.path.join(tmp, "elsewhere-model")
        os.makedirs(other)
        os.environ["HF_HUB_CACHE"] = os.path.join(tmp, "hub")
        os.environ.pop("MLX_VLM_MODEL_SUBFOLDER", None)
        os.environ["HERMES_MODELS_FILE"] = os.path.join(tmp, "no-models.json")
        app, appmod, pkg, orig = install_fakes()
        L = load_launcher()

        print("\n1. _install_vlm_guards on the fake mlx_vlm app")
        try:
            got = L._install_vlm_guards(MODEL, api_key="", subfolder="")
            err = None
        except Exception as e:
            got, err = None, "%s: %s" % (type(e).__name__, e)
        check("installs without error (layout check passes)", err is None and got is app, err)
        check("the guard is the OUTERMOST middleware",
              app.user_middleware and app.user_middleware[0].cls is L._GuardASGI,
              [m.cls.__name__ for m in app.user_middleware])
        check("CORS middleware removed",
              not any(m.cls is CORSMiddleware for m in app.user_middleware))
        check("both get_cached_model references are the pinned wrapper",
              pkg.get_cached_model is appmod.get_cached_model
              and pkg.get_cached_model is not orig
              and getattr(pkg.get_cached_model, "__wrapped__", None) is orig)

        stack = app.stack()
        print("\n2. the pin, driven through the ASGI stack")
        LOADS.clear()
        st = asyncio.run(call(stack, {"model": MODEL, "messages": []}))
        check("the launch model: 200, loaded under the launch id",
              st == 200 and LOADS == [(MODEL, "inherit", "auto")], (st, LOADS))
        LOADS.clear()
        st = asyncio.run(call(stack, {"model": snap, "messages": []}))
        check("its snapshot-path spelling: 200, mapped back to the launch id (no reload)",
              st == 200 and LOADS == [(MODEL, "inherit", "auto")], (st, LOADS))
        for label, body in (
                ("another repo id", {"model": "evil-org/Other-Model"}),
                ("another local directory", {"model": other}),
                ("the launch id with a different case", {"model": MODEL.upper()}),
                ("an empty model", {"model": ""}),
                ("an adapter", {"model": MODEL, "adapter_path": "/tmp/adapter"}),
                ("a non-text model kind", {"model": MODEL, "model_kind": "image_generation"}),
                ("an embedding kind", {"model": MODEL, "model_kind": "embedding"})):
            LOADS.clear()
            st = asyncio.run(call(stack, dict(body, messages=[])))
            check("%s: 400 and NOTHING loaded" % label, st == 400 and LOADS == [], (st, LOADS))
        LOADS.clear()
        st = asyncio.run(call(stack, {"model": MODEL, "model_kind": "text_generation"}))
        check("model_kind text_generation for the launch model: allowed", st == 200, st)

        print("\n3. the guard in front of it (same stack)")
        LOADS.clear()
        st = asyncio.run(call(stack, {"model": MODEL}, host=b"evil.example:8080"))
        check("a rebound Host: 403, nothing loaded", st == 403 and LOADS == [], st)
        st = asyncio.run(call(stack, {"model": MODEL}, extra=[(b"origin", b"null")]))
        check("any Origin: 403", st == 403, st)
        st = asyncio.run(call(stack, {"model": MODEL}, path="/unload"))
        check("/unload: 403", st == 403, st)
        LOADS.clear()
        st = asyncio.run(call(stack, {"model": MODEL, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "/Users/me/Desktop/a.png"}}]}]}))
        check("a local image path in the content: 400 before the route (E100)",
              st == 400 and LOADS == [], (st, LOADS))
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
