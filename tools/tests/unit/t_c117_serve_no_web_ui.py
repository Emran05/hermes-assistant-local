#!/usr/bin/env python3
"""C117 — `hermes serve` must not publish its web UI (and with it the token).

Hermes 0.18's web_server mounts its SPA at import: an unauthenticated GET /
answers index.html with `window.__HERMES_SESSION_TOKEN__="<token>"` in it, and
its CORS regex and WebSocket origin check admit every loopback port.  Any page
a local dev server hands the browser could therefore read the token and open
/api/ws as the owner.  The hub only ever speaks /api/ws (hermes_rpc.py), so the
fix is to start serve with no UI: web_server reads HERMES_WEB_DIST at import,
and when that directory does not exist mount_spa() registers a JSON 404
instead.  `--skip-build` must go with it — that flag exits 1 when
<dist>/index.html is missing — and an explicit HERMES_WEB_DIST already skips
the npm build.  (Verified in-process against the installed 0.18.0 with a
throwaway HOME: GET / went 200-with-token -> 404, /api/ws still accepts the
token and refuses a wrong one.)

This suite renders the serve plist with the SHIPPED heredoc out of
install-services.sh (bash, no launchctl — only the plist-writing lines run, into
a temp dir) and drives doctor's detection with HOME pointed at a temp dir and
launchctl stubbed.  No service, no network, nothing under ~/Library or ~/.hermes.
"""
import importlib.util, io, os, plistlib, re, shutil, stat, subprocess, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
INSTALL = os.path.join(REPO, "install-services.sh")
FAILS = []
CHECKS = [0]
TOKEN = "tok-C117-not-a-real-secret"


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def serve_block():
    """install-services.sh from the serve section's first line through the
    `chmod 600 "$SERVE_PLIST"` that closes it — exactly what ships."""
    src = io.open(INSTALL, encoding="utf-8").read()
    m = re.search(r'\n((?:# hermes serve WITHOUT[^\n]*\n(?:#[^\n]*\n)*SERVE_NO_WEB_UI=[^\n]*\n)?'
                  r'(?:\( umask 077\n)?(?:rm -f "\$SERVE_PLIST"\n)?'
                  r'cat > "\$SERVE_PLIST" <<EOF\n.*?\nEOF\n(?:\)\n)?chmod 600 "\$SERVE_PLIST"[^\n]*\n)',
                  src, re.S)
    return m.group(1) if m else None


def render(tmp, pre_mode=None, drop_chmod=False):
    block = serve_block()
    if block is None:
        return None, None
    if drop_chmod:
        block = re.sub(r'chmod 600 "\$SERVE_PLIST"[^\n]*\n$', "", block)
    out = os.path.join(tmp, "com.hermes.serve.plist")
    if os.path.exists(out):
        os.remove(out)
    if pre_mode is not None:
        with open(out, "w") as f:
            f.write("old plist\n")
        os.chmod(out, pre_mode)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(tmp, "home"), "SERVE_PLIST": out,
           "SERVE_LABEL": "com.hermes.serve", "SVC_PATH": "/usr/bin:/bin",
           "SERVE_TOKEN": TOKEN, "LOGS": os.path.join(tmp, "logs")}
    # R2-55: the heredocs interpolate XML-escaped copies (X_*), which the
    # script derives before its first plist; these temp values need none.
    env.update({"X_" + k: env[k] for k in ("HOME", "LOGS", "SVC_PATH", "SERVE_TOKEN")})
    script = "set -euo pipefail\numask 022\n" + block
    r = subprocess.run(["/bin/bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None, r.stderr
    return out, None


def t_plist(tmp):
    print("\n1. the serve plist install-services.sh writes")
    out, err = render(tmp)
    check("the serve block renders", out is not None, err)
    if out is None:
        return
    with open(out, "rb") as f:
        pl = plistlib.load(f)
    args = pl.get("ProgramArguments", [])
    env = pl.get("EnvironmentVariables", {})
    check("it still runs `hermes serve` on 127.0.0.1:9119",
          "serve" in args and "9119" in args and "127.0.0.1" in args, args)
    check("no --skip-build (it exits 1 when the dist has no index.html)", "--skip-build" not in args, args)
    dist = env.get("HERMES_WEB_DIST")
    check("HERMES_WEB_DIST is set", isinstance(dist, str) and dist, env.keys())
    if isinstance(dist, str) and dist:
        check("and names a path that does not exist", not os.path.exists(dist), dist)
        parent = os.path.dirname(dist)
        check("and never can: its parent is not a directory", os.path.exists(parent) and not os.path.isdir(parent), parent)
    check("the token still reaches serve (the hub authenticates /api/ws with it)",
          env.get("HERMES_DASHBOARD_SESSION_TOKEN") == TOKEN)
    check("the plist is 0600", stat.S_IMODE(os.stat(out).st_mode) == 0o600, oct(os.stat(out).st_mode))

    print("\n2. 0600 from the first byte, even over an older 0644 copy")
    out, err = render(tmp, pre_mode=0o644, drop_chmod=True)
    check("rendered over a 0644 file with the trailing chmod removed", out is not None, err)
    if out:
        check("the token never sits in a 0644 file", stat.S_IMODE(os.stat(out).st_mode) == 0o600,
              oct(os.stat(out).st_mode))


def t_doctor(tmp):
    home = os.path.join(tmp, "dhome")
    agents = os.path.join(home, "Library", "LaunchAgents")
    os.makedirs(agents)
    os.environ["HOME"] = home
    spec = importlib.util.spec_from_file_location("doctor_c117", os.path.join(REPO, "dashboard", "doctor.py"))
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    print("\n3. doctor notices a serve plist that still publishes the UI")
    fn = getattr(d, "_serve_web_ui_exposed", None)
    check("doctor.py has _serve_web_ui_exposed", callable(fn))
    if not callable(fn):
        return
    d._launchctl = lambda lb: {"loaded": True, "pid": 4242 if lb in (d.LABEL_DASH, d.LABEL_SERVE) else None}
    d._plist_installed = lambda lb: True
    p = os.path.join(agents, "com.hermes.serve.plist")

    def write(envd):
        with open(p, "wb") as f:
            plistlib.dump({"Label": "com.hermes.serve",
                           "ProgramArguments": ["hermes", "serve"],
                           "EnvironmentVariables": dict({"HERMES_DASHBOARD_SESSION_TOKEN": TOKEN}, **envd)}, f)

    check("no plist -> unknown (None), no warning", fn() is None)
    write({})
    check("no HERMES_WEB_DIST -> exposed", fn() is True)
    r = d._chk_services()
    check("services -> WARN with the re-render fix", r["status"] == "warn"
          and "web UI" in r["detail"] and "install-services.sh" in r["fix"], r)
    check("the token is never printed", TOKEN not in r["detail"] + r["fix"])
    real = os.path.join(tmp, "web_dist")
    os.makedirs(real)
    write({"HERMES_WEB_DIST": real})
    check("HERMES_WEB_DIST at a real directory -> exposed", fn() is True)
    write({"HERMES_WEB_DIST": "/dev/null/hermes-serve-has-no-web-ui"})
    check("HERMES_WEB_DIST at an impossible path -> not exposed", fn() is False)
    r = d._chk_services()
    check("services -> PASS", r["status"] == "pass" and "web UI" not in r["detail"], r)
    with open(p, "wb") as f:
        f.write(b"not a plist")
    check("an unreadable plist -> unknown, never a crash", fn() is None)
    out, _ = render(tmp)
    if out:
        shutil.copyfile(out, p)
        check("the plist install-services.sh writes reads as not exposed", fn() is False)


def main():
    tmp = tempfile.mkdtemp(prefix="t-c117-")
    try:
        t_plist(tmp)
        t_doctor(tmp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
