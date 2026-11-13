#!/usr/bin/env python3
"""Regression test for audit finding C130 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: mlx_vlm 0.6.14 (and the mlx-lm fallback) answered every request with
whatever model it named — `get_cached_model(request.model)` downloads and
loads any Hugging Face repo — installed CORS `*` with credentials, and left
/unload, /cache/reset, PATCH /settings (drafter swap) and /v1/realtime open.
Any local process, container or web page that reached :8080/:8081 could make
the model server pull and load another repo. (.scratch/repro_c130.py: a POST
naming "attacker-org/repo" reached the loader, a foreign Origin got
`Access-Control-Allow-Origin` back, /unload and the drafter PATCH answered 200,
a rebound Host was served.)

AFTER: mlx-vlm-launch.py pins the server to its --model (400 for anything
else, before the loader runs), removes CORS, refuses non-loopback Host, any
Origin, the mutating management routes and the realtime socket, and honours an
optional bearer key (owner-created ~/.hermes/dashboard/model-api-key) on every
route except the read-only probes. The mlx-lm fallback runs through the same
launcher (`--mlx-lm`) with the same pin + guard. server.py stamps the key on
the dashboard's own direct model calls.

Three layers, all against the REAL code:
  1. the launcher's pure decision functions, imported by path (stdlib python);
  2. the real mlx_vlm app (venv python, when ~/.hermes/mlx-vlm-venv exists)
     driven through FastAPI's TestClient with the loader STUBBED — no weights,
     no uvicorn, no port;
  3. the real mlx_lm ModelProvider/APIHandler (framework python, when mlx_lm
     imports) with `_load` stubbed.
Plus the dashboard's urllib opener handler, ast-extracted from server.py.
Layers 2/3 are skipped (not failed) on a machine without those interpreters,
each with a `SKIP` line run.sh counts (CI lists them as expected skips).
T_C130_LAUNCHER overrides the launcher path (that is how the old copy is shown
to fail).
"""
import ast
import email.message
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
LAUNCHER = Path(os.environ.get("T_C130_LAUNCHER") or (REPO / "mlx-vlm-launch.py"))
VENV_PY = Path(os.path.expanduser("~/.hermes/mlx-vlm-venv/bin/python"))
FRAMEWORK_PY = Path("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3")
PINNED = "mlx-community/Qwen3.8-27B-4bit"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def load_launcher():
    spec = importlib.util.spec_from_file_location("hermes_mlx_launch_t", str(LAUNCHER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. pure decision logic
# --------------------------------------------------------------------------
def test_pure(L):
    ok_h = {"host": "127.0.0.1:8080"}
    R = L._request_refusal
    check("loopback chat request passes", R("POST", "/v1/chat/completions", ok_h) is None)
    check("localhost:8081 / [::1] hosts pass",
          R("GET", "/v1/models", {"host": "localhost:8081"}) is None
          and R("GET", "/health", {"host": "[::1]:8080"}) is None)
    check("rebound Host refused (DNS rebinding)",
          (R("POST", "/v1/chat/completions", {"host": "rebind.evil.example:8080"}) or (0,))[0] == 403)
    check("container Host refused", (R("GET", "/v1/models", {"host": "host.docker.internal:8080"}) or (0,))[0] == 403)
    check("missing Host refused", (R("GET", "/health", {}) or (0,))[0] == 403)
    check("any Origin refused (even 'null')",
          (R("POST", "/v1/chat/completions", dict(ok_h, origin="http://evil.example")) or (0,))[0] == 403
          and (R("OPTIONS", "/v1/chat/completions", dict(ok_h, origin="null")) or (0,))[0] == 403)
    for m, p in (("POST", "/unload"), ("POST", "/unload/"), ("POST", "/cache/reset"),
                 ("POST", "/v1/cache/reset"), ("PATCH", "/settings"),
                 ("PATCH", "/v1/settings"), ("GET", "/v1/realtime")):
        check("%s %s refused" % (m, p), (R(m, p, ok_h) or (0,))[0] == 403)
    check("GET /v1/settings (read-only) still answers", R("GET", "/v1/settings", ok_h) is None)
    key = "k-123456"
    check("with a key: no Authorization -> 401",
          (R("POST", "/v1/chat/completions", ok_h, key) or (0,))[0] == 401)
    check("with a key: wrong key -> 401",
          (R("POST", "/v1/chat/completions", dict(ok_h, authorization="Bearer nope"), key) or (0,))[0] == 401)
    check("with a key: right key passes",
          R("POST", "/v1/chat/completions", dict(ok_h, authorization="Bearer " + key), key) is None)
    check("with a key: liveness probes stay key-less (dashboard model_online, doctor)",
          R("GET", "/v1/models", ok_h, key) is None and R("GET", "/health", ok_h, key) is None)
    check("with a key: GET /v1/settings needs it", (R("GET", "/v1/settings", ok_h, key) or (0,))[0] == 401)

    with tempfile.TemporaryDirectory(prefix="hermes-c130-") as d:
        hub = Path(d) / "hub"
        repo = hub / ("models--" + PINNED.replace("/", "--"))
        snap = repo / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("abc123")
        old = os.environ.get("HF_HUB_CACHE")
        os.environ["HF_HUB_CACHE"] = str(hub)
        try:
            al = L._model_aliases(PINNED)
        finally:
            if old is None:
                os.environ.pop("HF_HUB_CACHE", None)
            else:
                os.environ["HF_HUB_CACHE"] = old
        check("aliases: the repo id and its snapshot realpath",
              PINNED in al and os.path.realpath(str(snap)) in al, al)
        check("aliases: another repo is not one", L._model_key("attacker-org/repo") not in al)
        check("model key: snapshot path spelled with a trailing slash matches",
              L._model_key(str(snap) + "/") in al)

        kf = Path(d) / "model-api-key"
        os.environ["HERMES_MODEL_API_KEY_FILE"] = str(kf)
        os.environ.pop("MLX_VLM_SERVER_API_KEY", None)
        try:
            check("no key file -> no key (old behaviour)", L._load_api_key() == "")
            kf.write_text("sekrit\n")
            os.chmod(kf, 0o600)
            check("0600 key file is read", L._load_api_key() == "sekrit")
            os.environ["MLX_VLM_SERVER_API_KEY"] = "fromenv"
            got = L._load_api_key()
            check("an exported MLX_VLM_SERVER_API_KEY wins and is popped (upstream "
                  "would otherwise lock the key-less probes)",
                  got == "fromenv" and "MLX_VLM_SERVER_API_KEY" not in os.environ)
        finally:
            os.environ.pop("HERMES_MODEL_API_KEY_FILE", None)
            os.environ.pop("MLX_VLM_SERVER_API_KEY", None)


def test_shell():
    for name in ("mlx-server.sh", "mlx-server-bg.sh"):
        text = (REPO / name).read_text()
        live = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        check("%s: no bare `python3 -m mlx_lm server` exec left" % name,
              not any("-m mlx_lm server" in ln for ln in live))
        check("%s: mlx-lm fallback goes through the guarded launcher" % name,
              any("mlx-vlm-launch.py\" --mlx-lm" in ln for ln in live))


# --------------------------------------------------------------------------
# 2. the real mlx_vlm app (venv)
# --------------------------------------------------------------------------
VLM_DRIVER = r'''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("hermes_launch_drv", sys.argv[1])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
pinned, key = sys.argv[2], sys.argv[3]
import mlx_vlm.server as pkg
appmod = sys.modules["mlx_vlm.server.app"]
from fastapi import HTTPException
seen = []
def stub(model_path, adapter_path=appmod._INHERIT_ADAPTER, *, model_kind="auto"):
    seen.append([str(model_path), model_kind])
    raise HTTPException(status_code=418, detail="stub loader reached")
appmod.get_cached_model = stub
pkg.get_cached_model = stub
hub = os.environ["HF_HUB_CACHE"]
repo = os.path.join(hub, "models--" + pinned.replace("/", "--"))
snap = os.path.join(repo, "snapshots", "s1")
os.makedirs(snap); os.makedirs(os.path.join(repo, "refs"))
open(os.path.join(snap, "config.json"), "w").write("{}")
open(os.path.join(repo, "refs", "main"), "w").write("s1")
L._install_vlm_guards(pinned, key)
import warnings; warnings.simplefilter("ignore")
from fastapi.testclient import TestClient
c = TestClient(appmod.app, base_url="http://127.0.0.1:8080")
auth = {"Authorization": "Bearer " + key} if key else {}
msg = [{"role": "user", "content": "hi"}]
out = {}
def chat(model, headers=None, path="/v1/chat/completions"):
    h = dict(auth); h.update(headers or {})
    del seen[:]
    r = c.post(path, json={"model": model, "messages": msg}, headers=h)
    return {"status": r.status_code, "seen": list(seen),
            "acao": r.headers.get("access-control-allow-origin")}
out["other"] = chat("attacker-org/repo")
out["pinned"] = chat(pinned)
out["snapshot_alias"] = chat(snap + "/")
out["anthropic_other"] = chat("attacker-org/repo", path="/v1/messages")
out["origin"] = chat(pinned, {"Origin": "http://evil.example"})
r = c.options("/v1/chat/completions", headers={"Origin": "http://evil.example",
              "Access-Control-Request-Method": "POST"})
out["preflight"] = {"status": r.status_code, "acao": r.headers.get("access-control-allow-origin")}
out["unload"] = c.post("/unload", headers=auth).status_code
before = appmod.runtime.config.current().get("spec_draft_model")
out["settings_patch"] = c.patch("/v1/settings", json={"spec_draft_model": "attacker/drafter"},
                                headers=auth).status_code
out["drafter_unchanged"] = appmod.runtime.config.current().get("spec_draft_model") == before
out["rebound"] = chat(pinned, {"Host": "rebind.evil.example:8080"})
out["embeddings"] = (lambda r: {"status": r.status_code, "seen": list(seen)})(
    c.post("/v1/embeddings", json={"model": pinned, "input": "x"}, headers=auth))
try:
    with c.websocket_connect("/v1/realtime") as ws:
        out["realtime"] = "open"
except Exception as e:
    out["realtime"] = "refused:" + type(e).__name__
out["noauth_chat"] = c.post("/v1/chat/completions", json={"model": pinned, "messages": msg}).status_code
out["noauth_models"] = c.get("/v1/models").status_code
out["noauth_health"] = c.get("/health").status_code
out["cors_left"] = [m.cls.__name__ for m in appmod.app.user_middleware]
print("RESULT " + json.dumps(out))
'''


def run_driver(py, driver, args, env_extra=None):
    with tempfile.TemporaryDirectory(prefix="hermes-c130-drv-") as d:
        f = Path(d) / "drv.py"
        f.write_text(driver)
        env = dict(os.environ)
        env["HF_HOME"] = str(Path(d) / "hf")          # never the owner's cache
        env["HF_HUB_CACHE"] = str(Path(d) / "hf" / "hub")
        env["HF_HUB_OFFLINE"] = "1"
        env["HOME"] = d
        env.update(env_extra or {})
        r = subprocess.run([str(py), str(f)] + [str(a) for a in args], cwd=d,
                           capture_output=True, text=True, timeout=300, env=env)
    line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("RESULT ")]
    if not line:
        return None, (r.stdout or "")[-800:] + (r.stderr or "")[-1500:]
    return json.loads(line[-1][7:]), ""


def test_vlm():
    if not VENV_PY.exists():
        # a SKIP line, so run.sh counts it and CI's FAIL_ON_SKIP sees it
        print("  SKIP venv layer: no mlx-vlm venv at %s — the guard on the real "
              "mlx_vlm app did not run" % VENV_PY)
        return
    out, err = run_driver(VENV_PY, VLM_DRIVER, [LAUNCHER, PINNED, ""])
    if out is None:
        check("venv driver ran", False, err)
        return
    check("mlx_vlm: another model is a 400 and the loader never sees it",
          out["other"]["status"] == 400 and out["other"]["seen"] == [], out["other"])
    check("mlx_vlm: the pinned model reaches the loader as the launch id",
          out["pinned"]["status"] == 418
          and out["pinned"]["seen"] == [[PINNED, "auto"]], out["pinned"])
    check("mlx_vlm: the snapshot-path spelling maps back to the launch id (no reload)",
          out["snapshot_alias"]["status"] == 418
          and out["snapshot_alias"]["seen"] == [[PINNED, "auto"]], out["snapshot_alias"])
    # upstream's /v1/messages turns every exception into a 500; what matters
    # is that the refusal happens before the loader
    check("mlx_vlm: the Anthropic route is pinned too",
          out["anthropic_other"]["status"] >= 400 and out["anthropic_other"]["seen"] == [],
          out["anthropic_other"])
    check("mlx_vlm: a foreign Origin is refused and gets no CORS grant",
          out["origin"]["status"] == 403 and not out["origin"]["acao"]
          and out["origin"]["seen"] == [], out["origin"])
    check("mlx_vlm: a CORS preflight is refused with no grant",
          out["preflight"]["status"] == 403 and not out["preflight"]["acao"], out["preflight"])
    check("mlx_vlm: CORSMiddleware removed, guard outermost",
          "CORSMiddleware" not in out["cors_left"] and out["cors_left"][:1] == ["_GuardASGI"],
          out["cors_left"])
    check("mlx_vlm: POST /unload refused", out["unload"] == 403, out["unload"])
    check("mlx_vlm: PATCH /v1/settings (drafter swap) refused, drafter unchanged",
          out["settings_patch"] == 403 and out["drafter_unchanged"], out)
    check("mlx_vlm: rebound Host refused before the loader",
          out["rebound"]["status"] == 403 and out["rebound"]["seen"] == [], out["rebound"])
    check("mlx_vlm: a non-text model kind (embeddings) is refused",
          out["embeddings"]["status"] == 400 and out["embeddings"]["seen"] == [], out["embeddings"])
    check("mlx_vlm: /v1/realtime socket refused", str(out["realtime"]).startswith("refused"),
          out["realtime"])
    check("mlx_vlm: no key configured -> key-less chat still served (old behaviour)",
          out["noauth_chat"] == 418, out["noauth_chat"])

    out, err = run_driver(VENV_PY, VLM_DRIVER, [LAUNCHER, PINNED, "k-test-0001"])
    if out is None:
        check("venv driver (keyed) ran", False, err)
        return
    check("mlx_vlm keyed: chat without the key -> 401", out["noauth_chat"] == 401, out["noauth_chat"])
    check("mlx_vlm keyed: chat with the key reaches the (stub) loader",
          out["pinned"]["status"] == 418, out["pinned"])
    check("mlx_vlm keyed: /v1/models and /health stay key-less",
          out["noauth_models"] == 200 and out["noauth_health"] == 200, out)


# --------------------------------------------------------------------------
# 3. the real mlx_lm server classes (framework python)
# --------------------------------------------------------------------------
LM_DRIVER = r'''
import argparse, email.message, importlib.util, io, json, sys
spec = importlib.util.spec_from_file_location("hermes_launch_drv", sys.argv[1])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
pinned = sys.argv[2]
import mlx_lm.server as S
L._install_mlx_lm_guards(S, pinned, "", "")
ns = argparse.Namespace(model=pinned, adapter_path=None, draft_model=None,
                        trust_remote_code=False, chat_template=None, pipeline=False,
                        use_default_chat_template=False)
p = S.ModelProvider(ns)
loads = []
p._load = lambda m, a=None, d=None: loads.append([m, a, d])
out = {}
def tryload(*a):
    del loads[:]
    try:
        p.load(*a)
        return {"ok": True, "loads": list(loads)}
    except Exception as e:
        return {"ok": False, "err": type(e).__name__, "loads": list(loads)}
out["default"] = tryload("default_model", None, "default_model")
out["by_id"] = tryload(pinned, None, "default_model")
out["other"] = tryload("attacker-org/repo", None, "default_model")
out["drafter"] = tryload(pinned, None, "attacker/drafter")
out["adapter"] = tryload(pinned, "attacker/adapter", "default_model")
def handle(method, headers):
    H = S.APIHandler
    h = H.__new__(H)
    h.command, h.path, h.request_version = method, "/v1/chat/completions", "HTTP/1.1"
    h.requestline = "%s /v1/chat/completions HTTP/1.1" % method
    h.client_address = ("127.0.0.1", 1)
    m = email.message.Message()
    for k, v in headers.items():
        m[k] = v
    h.headers, h.wfile, h.rfile = m, io.BytesIO(), io.BytesIO(b"")
    getattr(h, "do_" + method)()
    return h.wfile.getvalue().decode("latin-1")
out["origin_post"] = handle("POST", {"Host": "127.0.0.1:8080", "Origin": "http://evil.example"})[:40]
out["origin_options"] = handle("OPTIONS", {"Host": "127.0.0.1:8080", "Origin": "http://evil.example"})
out["rebound_get"] = handle("GET", {"Host": "rebind.evil.example"})[:40]
h = S.APIHandler.__new__(S.APIHandler); h._headers_buffer = []
h._set_cors_headers()
out["cors_headers"] = len(h._headers_buffer)
print("RESULT " + json.dumps(out))
'''


def test_mlx_lm():
    py = FRAMEWORK_PY
    if not py.exists() or subprocess.run([str(py), "-c", "import mlx_lm.server"],
                                         capture_output=True, timeout=120).returncode != 0:
        print("  SKIP mlx-lm layer: no python with mlx_lm here — the guard on the "
              "real mlx_lm server did not run")
        return
    # the driver runs under a throwaway HOME, so point Python at the owner's
    # user site explicitly (that is where mlx_lm lives) — read-only
    ub = subprocess.run([str(py), "-c", "import site; print(site.getuserbase())"],
                        capture_output=True, text=True, timeout=60).stdout.strip()
    out, err = run_driver(py, LM_DRIVER, [LAUNCHER, PINNED],
                          {"PYTHONUSERBASE": ub} if ub else None)
    if out is None:
        check("mlx-lm driver ran", False, err)
        return
    check("mlx-lm: default_model loads the launch model",
          out["default"]["ok"] and out["default"]["loads"] == [[PINNED, None, None]], out["default"])
    check("mlx-lm: the launch id maps to the same load (no reload churn)",
          out["by_id"]["ok"] and out["by_id"]["loads"] in ([], [[PINNED, None, None]]), out["by_id"])
    check("mlx-lm: another model refused before _load",
          not out["other"]["ok"] and out["other"]["loads"] == [], out["other"])
    check("mlx-lm: a request-named drafter refused",
          not out["drafter"]["ok"] and out["drafter"]["loads"] == [], out["drafter"])
    check("mlx-lm: a request-named adapter refused",
          not out["adapter"]["ok"] and out["adapter"]["loads"] == [], out["adapter"])
    def status(raw):
        parts = raw.split("\r\n", 1)[0].split(" ")
        return parts[1] if len(parts) > 1 else ""

    check("mlx-lm: a foreign Origin POST is a 403",
          status(out["origin_post"]) == "403", out["origin_post"])
    check("mlx-lm: a preflight with Origin is a 403 without Access-Control-*",
          status(out["origin_options"]) == "403"
          and "access-control" not in out["origin_options"].lower(), out["origin_options"][:200])
    check("mlx-lm: rebound Host refused", status(out["rebound_get"]) == "403",
          out["rebound_get"])
    check("mlx-lm: CORS headers never emitted", out["cors_headers"] == 0, out["cors_headers"])


# --------------------------------------------------------------------------
# 4. the dashboard stamps the key on its own direct model calls
# --------------------------------------------------------------------------
def test_dashboard_handler():
    src = (REPO / "dashboard" / "server.py").read_text()
    tree = ast.parse(src)
    want = {"_model_api_key", "_is_model_server_url", "_ModelAuthHandler"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
             and n.name in want]
    check("server.py defines the model-key opener pieces",
          {n.name for n in nodes} == want, [n.name for n in nodes])
    if {n.name for n in nodes} != want:
        return
    check("server.py installs the opener at import",
          # E20: the opener also routes loopback URLs around any proxy
          "urllib.request.install_opener(urllib.request.build_opener(\n"
          "    _LoopbackDirectProxyHandler(), _ModelAuthHandler()))" in src)
    import urllib.parse
    with tempfile.TemporaryDirectory(prefix="hermes-c130-key-") as d:
        ns = {"os": os, "urllib": urllib, "MODEL_API_KEY_FILE": os.path.join(d, "model-api-key"),
              "_model_key_memo": {"mtime": None, "key": ""},
              "MODEL_URL": "http://127.0.0.1:8080/v1/models",
              "BG_MODEL_URL": "http://127.0.0.1:8081/v1/models"}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "server.py", "exec"), ns)
        h = ns["_ModelAuthHandler"]()

        def stamped(url):
            req = urllib.request.Request(url)
            h.http_request(req)
            return req.unredirected_hdrs.get("Authorization")

        check("no key file -> no header", stamped("http://127.0.0.1:8080/v1/chat/completions") is None)
        with open(ns["MODEL_API_KEY_FILE"], "w") as f:
            f.write("dash-key\n")
        check("key file -> primary lane gets the bearer header",
              stamped("http://127.0.0.1:8080/v1/chat/completions") == "Bearer dash-key")
        check("key file -> background lane too",
              stamped("http://127.0.0.1:8081/v1/chat/completions") == "Bearer dash-key")
        check("never sent to the dashboard's own port or a remote host",
              stamped("http://127.0.0.1:7788/api/models") is None
              and stamped("http://example.com:8080/v1/chat/completions") is None)


def main():
    print("launcher under test: %s" % LAUNCHER)
    try:
        L = load_launcher()
    except Exception as e:
        check("launcher imports", False, repr(e))
        L = None
    if L is not None:
        missing = [n for n in ("_request_refusal", "_model_aliases", "_load_api_key",
                               "_install_vlm_guards", "_install_mlx_lm_guards")
                   if not hasattr(L, n)]
        check("launcher has the C130 guard functions", not missing, missing)
        if not missing:
            test_pure(L)
    test_shell()
    test_vlm()
    test_mlx_lm()
    test_dashboard_handler()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
