#!/usr/bin/env python3
"""Round-2 doctor follow-ups (docs/audits/2026-10-21-codebase-audit.md).

  R2-57  serve not loaded is a FAIL, not a WARN: since C36 removed the one-shot
         `hermes -z` fallback, the dashboard chat and Quick Ask refuse every
         turn while com.hermes.serve is down.
  C21.2  a Google grant that Google no longer accepts (needs_reauth /
         refresh_failed:<code>) shows up in doctor, not only on the Google
         card.  The refresh outcome lives in the dashboard's memory, so the
         check is driven through the REAL aux_google status functions
         (ast-extracted) in-process, and through a throwaway /api/google/status
         for the CLI path.
  R2-58  lmstudio / ollama (and Hermes' other local-server aliases) default to
         loopback, so they are not "a hosted provider" while the base_url next
         to them is unset or on loopback.

Runs the shipped doctor.py with HOME pointed at a throwaway directory and
launchctl / plist lookups stubbed.  No dashboard, no network, nothing under the
real ~/.hermes.
"""
import ast, datetime, http.server, importlib.util, io, json, os, shutil, sys, tempfile, threading, time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-r2-doctor-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = "9"           # a closed port: the CLI path never reaches a real dashboard
spec = importlib.util.spec_from_file_location("doctor_r2w12", os.path.join(REPO, "dashboard", "doctor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def t_serve_down():
    print("\nR2-57. serve not loaded: every dashboard turn is refused, so it is a FAIL")
    d._plist_installed = lambda lb: True
    d._serve_web_ui_exposed = lambda: False

    def lc(down):
        return lambda lb: ({"loaded": False, "pid": None, "exit": None} if lb in down
                           else {"loaded": True, "pid": 4242 if lb in (d.LABEL_DASH, d.LABEL_SERVE) else None,
                                 "exit": None})
    d._launchctl = lc({d.LABEL_SERVE})
    r = d._chk_services()
    check("serve not loaded -> FAIL", r["status"] == d.FAIL, r)
    check("the detail still says chat runs nothing until serve is back",
          "runs nothing until serve is back" in r["detail"], r)
    check("the fix is unchanged (./install-services.sh)", r["fix"] == "./install-services.sh", r)
    d._launchctl = lc(set())
    r = d._chk_services()
    check("serve loaded -> PASS", r["status"] == d.PASS, r)
    d._launchctl = lc({d.LABEL_MLX, d.LABEL_BG})
    r = d._chk_services()
    check("the on-demand model lanes being booted out is still a PASS", r["status"] == d.PASS, r)


GOOG_NAMES = {"GOOG_DIR", "GOOG_TOKEN", "GOOG_SECRET", "GOOG_PENDING", "GOOG_SCOPES",
              "GOOG_EXTRA_OK", "GOOG_FORBIDDEN", "GOOG_SKILL_STOCK", "_GOOG_NET",
              "GOOG_DEAD_GRANT", "_goog_net_note", "_goog_token_mtime", "_goog_note_refresh",
              "_goog_refresh_failure", "_goog_enforce_600", "_goog_bust", "_goog_read_json",
              "_goog_scope_list", "_goog_scope_check", "_goog_parse_expiry",
              "_goog_status_compute"}


def real_google():
    """aux_google.py's status path, exec'd from the shipped file."""
    path = os.path.join(REPO, "dashboard", "aux_google.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in GOOG_NAMES)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in GOOG_NAMES for t in n.targets))]
    g = {"os": os, "json": json, "time": time, "sys": sys, "_goog_datetime": datetime,
         "HOME": HOME, "_widget_cache": {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), g)
    return g


def token(**kw):
    os.makedirs(os.path.join(HOME, ".hermes"), exist_ok=True)
    t = {"token": "ya29.unit", "refresh_token": "1//unit", "account": "someone@example.com",
         "scopes": ["https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/calendar.readonly",
                    "https://www.googleapis.com/auth/contacts.readonly"],
         "expiry": "2000-01-01T00:00:00Z"}
    t.update(kw)
    with open(os.path.join(HOME, ".hermes", "google_token.json"), "w") as f:
        f.write(t.pop("_raw", None) or json.dumps(t))


def untoken():
    try:
        os.remove(os.path.join(HOME, ".hermes", "google_token.json"))
    except OSError:
        pass


def t_google():
    print("\nC21.2. doctor reports a Google grant that no longer works")
    fn = getattr(d, "_chk_google", None)
    check("doctor has a Google check", callable(fn) and "google" in [c[0] for c in d.CHECKS])
    if not callable(fn):
        return
    g = real_google()
    d.HOST = g                                      # in-process: the dashboard's own helper
    untoken()
    r = fn()
    check("not connected -> PASS (Google is optional)", r["status"] == d.PASS
          and "not connected" in r["detail"], r)
    token()
    r = fn()
    check("connected, no failed refresh -> PASS", r["status"] == d.PASS
          and "connected" in r["detail"], r)
    check("the account e-mail is never printed (the report is pasted into issues)",
          "example.com" not in r["detail"] + r["fix"], r)

    g["_goog_note_refresh"](False, 400, {"error": "invalid_grant"})
    st = g["_goog_status_compute"]()
    check("premise: aux_google now says needs_reauth + refresh_failed:invalid_grant",
          st.get("needs_reauth") is True and st.get("reason") == "refresh_failed:invalid_grant", st)
    r = fn()
    check("dead grant -> WARN", r["status"] == d.WARN, r)
    check("it names Google's code and that the calendar is not reaching Hermes",
          "invalid_grant" in r["detail"] and "not reaching Hermes" in r["detail"], r)
    check("the fix is the reconnect on the Google card",
          "Settings › Connections › Google" in r["fix"] and "connect again" in r["fix"], r)
    check("no token or e-mail in the line", "ya29" not in r["detail"] and "1//unit" not in r["detail"]
          and "example.com" not in r["detail"], r)

    g["_goog_note_refresh"](False, 0, None)            # a transient failure
    r = fn()
    check("transient refresh failure -> WARN, and says it is retried",
          r["status"] == d.WARN and "network_error" in r["detail"] and "retried" in r["detail"], r)
    g["_goog_note_refresh"](True, 200, {})
    r = fn()
    check("a refresh that works again -> PASS", r["status"] == d.PASS, r)

    token(scopes=["https://www.googleapis.com/auth/gmail.readonly",
                  "https://www.googleapis.com/auth/calendar"])
    r = fn()
    check("a pre-C51 grant with the read/write calendar scope -> WARN, not 'read-only'",
          r["status"] == d.WARN and "read-only" in r["detail"] and "wider" in r["detail"], r)
    token(_raw="{not json")
    r = fn()
    check("an unreadable token file -> WARN (Google context is off)",
          r["status"] == d.WARN and "token_unreadable" in r["detail"], r)

    print("\n   the CLI path asks the dashboard's /api/google/status")
    d.HOST = {}
    served = {"body": {}}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/api/google/status"
            b = json.dumps(served["body"]).encode()
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = d.DASH_BASE
    try:
        d.DASH_BASE = "http://127.0.0.1:%d" % srv.server_address[1]
        served["body"] = {"ok": True, "connected": True, "needs_reauth": True,
                          "reason": "refresh_failed:invalid_client", "refresh_failed_at": time.time()}
        r = fn()
        check("CLI: the dashboard's needs_reauth verdict -> WARN",
              r["status"] == d.WARN and "invalid_client" in r["detail"], r)
        served["body"] = {"ok": True, "connected": True, "reason": ""}
        r = fn()
        check("CLI: a healthy grant -> PASS", r["status"] == d.PASS, r)
    finally:
        srv.shutdown()
        d.DASH_BASE = base
    token()
    r = fn()
    check("CLI, dashboard down, token on file -> PASS that says the refresh state is unknown",
          r["status"] == d.PASS and "running dashboard" in r["detail"], r)
    untoken()
    r = fn()
    check("CLI, dashboard down, no token -> not connected", r["status"] == d.PASS
          and "not connected" in r["detail"], r)


def t_local_providers():
    print("\nR2-58. lmstudio / ollama on loopback are not a hosted provider")

    def prov(tree):
        return [(st, det) for st, rule, det, fix in d._drift_findings(tree, plugins_dir=TMP)
                if rule == "provider"]
    for name in ("lmstudio", "LM-Studio", "ollama", "vllm", "llama.cpp"):
        r = prov({"model": {"provider": name}})
        check("model.provider %s with no base_url -> no provider WARN" % name, r == [], r)
    r = prov({"model": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1"}})
    check("ollama on 127.0.0.1:11434 -> no provider WARN", r == [], r)
    r = prov({"auxiliary": {"vision": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1"}}})
    check("auxiliary.vision lmstudio on localhost -> no provider WARN", r == [], r)
    r = prov({"delegation": {"provider": "ollama", "base_url": "http://gpu-box.example:11434/v1"}})
    check("ollama on a remote host still WARNs", len(r) == 1 and r[0][0] == d.WARN
          and "delegation.provider" in r[0][1], r)
    r = prov({"model": {"provider": "ollama-cloud"}})
    check("ollama-cloud is hosted and still WARNs", len(r) == 1 and r[0][0] == d.WARN, r)
    r = prov({"model": {"provider": "openrouter"}})
    check("a real hosted provider still WARNs", len(r) == 1 and "hosted provider" in r[0][1], r)
    r = prov({"model": {"provider": "custom"}})
    check("custom stays local", r == [], r)


def main():
    t_serve_down()
    t_google()
    t_local_providers()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
