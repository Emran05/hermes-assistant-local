#!/usr/bin/env python3
"""Round-2 input-validation follow-ups for aux_evals.py and aux_trace.py.

  C59.5  the evals flags `enabled` / `require_ac` / `wake_if_ac` and the run's
         `force` are JSON booleans: bool("false") is True, so the string
         "false" used to ENABLE the scheduled suite, and a "false" force could
         wake the model.  Anything else is a 400 and nothing is stored.
  R2-60  JSON Infinity / NaN (Python's json parses both): evals at_hour
         Infinity raised OverflowError in _ev_is_num (a 500); trace since=nan
         passed both range checks and then datetime.fromtimestamp(nan) raised
         (a 500).  Both are now a 400.  The trace half ast-extracts the REAL
         _tr_range (+ the module's own imports) from aux_trace.py.

Execs the REAL dashboard/aux_evals.py into a stub server.py globals namespace
(the same way t_evals.py does, with the scheduler thread pre-disabled) and
drives its route functions.  evals_power() is stubbed to "on battery" so that
no code path can reach a model, even on the pre-fix code.  Offline; temp
HOME only.
"""
import ast, io, json, os, shutil, sys, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402  the REAL settings_update & co

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-r2-evtr-")
SETTINGS = os.path.join(TMP, "settings.json")


class Ctx(object):
    def __init__(self, body=None, query=None):
        self.body = body if body is not None else {}
        self.query = query or {}

    def q1(self, k, d=""):
        return self.query.get(k, d)


def read_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def load_evals():
    G = {"DATA": TMP, "MODEL_URL": "http://127.0.0.1:9/v1/models", "CHAT_JOBS": {},
         **server_real.settings_api(SETTINGS, threading.Lock()),
         "active_model": lambda: "test/Model",
         "model_online": lambda: False, "agent_paused": lambda: False,
         "agent_idle_suspended": lambda: False,
         "agent_wake": lambda wait=True, timeout=90: False,
         "register_get": lambda p, f: G.setdefault("_GETS", {}).__setitem__(p, f),
         "register_post": lambda p, f: G.setdefault("_POSTS", {}).__setitem__(p, f),
         "_cached": lambda k, ttl, fn: fn(),
         "_ev_thread_started": True}
    path = os.path.join(DASH, "aux_evals.py")
    with open(path) as f:
        exec(compile(f.read(), path, "exec"), G)
    G["evals_power"] = lambda: {"ac": False, "battery": True, "pct": 50}
    return G


def t_evals_bools(G):
    print("\nC59.5. evals flags are JSON booleans")
    post = G["_POSTS"]["/api/evals/settings"]
    for k in ("enabled", "require_ac", "wake_if_ac"):
        for v in ("false", "true", 0, 1, None):
            with open(SETTINGS, "w") as f:
                json.dump({}, f)
            r = post(Ctx({k: v}))
            check("settings %s=%r -> 400" % (k, v),
                  isinstance(r, tuple) and r[1] == 400 and k in r[0].get("error", ""), r)
            check("  and nothing is stored", "evals" not in read_settings(), read_settings())
    r = post(Ctx({"enabled": True, "at_hour": 4}))
    check("a real boolean is stored as is", isinstance(r, dict) and r.get("ok") is True
          and read_settings().get("evals", {}).get("enabled") is True, (r, read_settings()))
    r = post(Ctx({"enabled": False}))
    check("and false turns it off", read_settings().get("evals", {}).get("enabled") is False,
          read_settings())

    run = G["_POSTS"]["/api/evals/run"]
    for v in ("false", "true", 1, 0, None):
        r = run(Ctx({"force": v}))
        check("run force=%r -> 400" % (v,),
              isinstance(r, tuple) and r[1] == 400 and "force" in r[0].get("error", ""), r)
    r = run(Ctx({"force": True}))
    check("run force=true reaches the battery guard (a real bool is accepted)",
          isinstance(r, dict) and r.get("reason") == "on battery", r)
    r = run(Ctx({}))
    check("run with no force reaches the battery guard", isinstance(r, dict)
          and r.get("reason") == "on battery", r)


def t_evals_nonfinite(G):
    print("\nR2-60. evals: Infinity / NaN are not numbers")
    post = G["_POSTS"]["/api/evals/settings"]
    body = json.loads('{"at_hour": Infinity, "at_minute": -Infinity, "days": NaN}')
    for k in ("at_hour", "at_minute", "days"):
        with open(SETTINGS, "w") as f:
            json.dump({}, f)
        try:
            r = post(Ctx({k: body[k]}))
        except Exception as e:
            r = "raised %s" % type(e).__name__
        check("settings %s=%r -> 400 (not an exception / 500)" % (k, body[k]),
              isinstance(r, tuple) and r[1] == 400 and "number" in r[0].get("error", ""), r)
        check("  and nothing is stored", "evals" not in read_settings(), read_settings())
    r = post(Ctx({"at_hour": 6.0, "days": "7"}))
    check("finite numbers are still accepted", isinstance(r, dict) and r.get("ok") is True
          and r["settings"]["at_hour"] == 6 and r["settings"]["days"] == 7, r)
    with open(SETTINGS, "w") as f:
        f.write('{"evals": {"at_hour": Infinity, "days": NaN}}')
    try:
        st = G["evals_settings"]()
        ok = st["at_hour"] == G["EV_DEFAULTS"]["at_hour"] and st["days"] == G["EV_DEFAULTS"]["days"]
    except Exception as e:
        st, ok = "raised %s" % type(e).__name__, False
    check("a hand-edited Infinity already in settings.json reads as the default", ok, st)


def load_trace_range():
    path = os.path.join(DASH, "aux_trace.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    keep = [n for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))
            or (isinstance(n, ast.FunctionDef) and n.name in ("_tr_range", "_tr_stamp", "_tr_days"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_TR_MAX_DAYS"
                                                   for t in n.targets))]
    g = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), g)
    return g


def t_trace_nonfinite():
    print("\nR2-60. trace: since/until must be finite epoch seconds")
    g = load_trace_range()
    rng = g["_tr_range"]
    now = __import__("time").time()
    for q in ({"since": "nan"}, {"until": "nan"}, {"since": "nan", "until": "nan"},
              {"since": "inf"}, {"since": "-inf"}, {"until": "inf"},
              {"since": "1e15", "until": "1.0000000000864e15"}, {"since": "Infinity"}):
        try:
            since, until, err = rng(Ctx(query=q))
        except Exception as e:
            since, until, err = None, None, "raised %s" % type(e).__name__
        check("%s -> 400" % json.dumps(q),
              isinstance(err, tuple) and err[1] == 400 and "epoch seconds" in err[0].get("error", ""),
              (since, until, err))
    since, until, err = rng(Ctx(query={"since": str(now - 3600), "until": str(now)}))
    check("a real one-hour window is accepted", err is None and until - since == 3600.0, err)
    check("  and stamps a filename", len(g["_tr_stamp"](since)) == 8, g["_tr_stamp"](since))
    since, until, err = rng(Ctx(query={}))
    check("the default (last 24 h) is accepted", err is None and abs(until - since - 86400.0) < 1e-6, err)
    _s, _u, err = rng(Ctx(query={"since": str(now), "until": str(now - 1)}))
    check("until before since is still a 400", isinstance(err, tuple) and err[1] == 400, err)

    # the last representable day: fromtimestamp() takes it, but _tr_days
    # steps to the day after `until` (review: this was still a 500)
    dt = __import__("datetime")
    last = dt.datetime(9999, 12, 31, 12, 0).timestamp()
    eve = dt.datetime(9999, 12, 30, 12, 0).timestamp()
    for what, q in (("a one-hour window", {"since": str(last - 3600), "until": str(last)}),
                    ("a window from the day before", {"since": str(eve), "until": str(last)})):
        try:
            since, until, err = rng(Ctx(query=q))
            days = g["_tr_days"](since, until) if err is None else None
        except Exception as e:
            err = "raised %s" % type(e).__name__
        check("until on 9999-12-31 (%s) -> 400, not an overflow in _tr_days" % what,
              isinstance(err, tuple) and err[1] == 400 and "epoch seconds" in err[0].get("error", ""),
              err)
    try:
        since, until, err = rng(Ctx(query={"since": str(eve - 3600), "until": str(eve)}))
        days = g["_tr_days"](since, until) if err is None else None
    except Exception as e:
        err, days = "raised %s" % type(e).__name__, None
    check("a window on 9999-12-30 is accepted and walks its one day", err is None
          and isinstance(days, list) and len(days) == 1, (err, days))
    for q in ({}, {"since": str(now - 3 * 86400), "until": str(now)}):
        since, until, err = rng(Ctx(query=q))
        try:
            days = g["_tr_days"](since, until)
        except Exception as e:
            days = "raised %s" % type(e).__name__
        check("an accepted range walks its days without raising (%d-day)" % round((until - since) / 86400),
              isinstance(days, list) and days, days)


def main():
    G = load_evals()
    t_evals_bools(G)
    t_evals_nonfinite(G)
    t_trace_nonfinite()


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
