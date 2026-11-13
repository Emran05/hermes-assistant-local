#!/usr/bin/env python3
"""C31 — two doctor lines that said something untrue.

1. The CLI doctor's copy of the Claude-escalation read still defaulted to ON
   (the pre-A02 rule), so `python3 dashboard/doctor.py` on a fresh install said
   "escalation on" while the dashboard — which reads it through
   aux_claudebridge.claude_escalation_enabled() — had it OFF.  The copy must
   now agree with the real helper on every shape of settings.json: missing,
   empty, corrupt, a non-boolean value, and an explicit true.
2. The Apple-apps check said "Google Calendar used for context" whenever the
   Reminders/Notes automation was off, whether or not Google was connected.

Runs the shipped doctor.py with HOME pointed at a throwaway directory; the
dashboard's own helper is exec'd out of aux_claudebridge.py for the
cross-check.  No dashboard, no network, nothing under the real ~/.hermes.
"""
import ast, importlib.util, io, json, os, shutil, sys, tempfile

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


TMP = tempfile.mkdtemp(prefix="t-c31-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
os.environ["HOME"] = HOME
spec = importlib.util.spec_from_file_location("doctor_c31", os.path.join(REPO, "dashboard", "doctor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
SETTINGS = os.path.join(DATA, "settings.json")


def real_helper():
    """aux_claudebridge's two functions, exec'd from the shipped file."""
    path = os.path.join(REPO, "dashboard", "aux_claudebridge.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    want = {"_cb_settings_dict", "claude_escalation_enabled"}
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CB_ESC_DEFAULT"
                                                   for t in n.targets))]
    g = {"json": json, "sys": sys, "SETTINGS_FILE": SETTINGS,
         "_CB_CONFIG_ERROR": {"error": None, "logged": True}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), g)
    return g["claude_escalation_enabled"]


def put(raw):
    if os.path.exists(SETTINGS):
        os.remove(SETTINGS)
    if raw is not None:
        with io.open(SETTINGS, "w", encoding="utf-8") as f:
            f.write(raw)


def main():
    real = real_helper()
    print("\n1. the CLI doctor reads escalation exactly like the dashboard does")
    cases = (
        ("no settings.json (a fresh install)", None, False),
        ("an empty object", "{}", False),
        ("claude_escalation absent", '{"ticker": ["AAPL"]}', False),
        ("enabled true", '{"claude_escalation": {"enabled": true}}', True),
        ("enabled false", '{"claude_escalation": {"enabled": false}}', False),
        ('enabled "true" (a string)', '{"claude_escalation": {"enabled": "true"}}', False),
        ("enabled 1", '{"claude_escalation": {"enabled": 1}}', False),
        ("block is not an object", '{"claude_escalation": true}', False),
        ("settings.json is corrupt", '{"claude_escalation": {"enabled": true', False),
        ("settings.json is a list", "[1, 2]", False),
    )
    for name, raw, want in cases:
        put(raw)
        got = d._escalation_on()
        check("%s -> %s" % (name, "on" if want else "off"), got is want, got)
        check("%s: same answer as claude_escalation_enabled()" % name, got == real(), (got, real()))

    print("\n2. the live helper wins inside the dashboard, and a failing one fails closed")
    put('{"claude_escalation": {"enabled": true}}')
    d.HOST = {"claude_escalation_enabled": lambda: False}
    check("host says off -> off, whatever the file says", d._escalation_on() is False)

    def boom():
        raise RuntimeError("helper broke")
    d.HOST = {"claude_escalation_enabled": boom}
    check("host helper raises -> off (never falls back to an ON default)", d._escalation_on() is False)
    d.HOST = {}

    print("\n3. the Claude-bridge line says OFF on a fresh install")
    put(None)
    d._claude_bin = lambda: "/usr/local/bin/claude"
    r = d._chk_claude()
    check("fresh install -> 'escalation OFF'", "escalation OFF" in r["detail"], r)
    put('{"claude_escalation": {"enabled": true}}')
    r = d._chk_claude()
    check("explicit true -> 'escalation on'", "escalation on" in r["detail"], r)

    print("\n4. the Apple-apps line claims nothing about Google")
    put(None)
    r = d._chk_apple_apps()
    check("automation off -> PASS", r["status"] == "pass", r)
    check("and no 'Google Calendar used for context'", "Google" not in r["detail"], r["detail"])
    put('{"apple_apps": {"reminders": true}}')
    r = d._chk_apple_apps()
    check("automation on -> WARN naming the app", r["status"] == "warn" and "Reminders" in r["detail"], r)


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
