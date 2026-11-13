#!/usr/bin/env python3
"""Regression test for C52 (docs/audits/2026-10-21-codebase-audit.md) — the
"Last outbound" evidence.

BEFORE: the Data & Network card built "Last outbound" from status reads that
answered other questions: "Feeds" was the newest watchtower ALERT (Sep 1)
while the feeds had been fetched minutes earlier, "Google" was when the status
file was read, and a refused Claude call counted as one that went out.

AFTER: GET /api/network/last (dashboard/aux_network.py) reads, per destination,
when a request really LEFT the Mac: the last real update check, the last feed
gather (intel.json `updated`), Telegram deliveries split into briefings and
alerts, bridge-log rows that spawned `claude -p` (split by caller), and
aux_google's last request. A destination the dashboard cannot observe is
absent ("not tracked"). It must make no network call.

The REAL dashboard/aux_network.py is exec'd on a throwaway HOME with fixture
stores; both the module-global path and the file fallback are exercised.
"""
import json
import os
import sys
import tempfile
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def done():
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


SRC = os.path.join(REPO, "dashboard", "aux_network.py")
check("dashboard/aux_network.py exists (the evidence route)", os.path.isfile(SRC), SRC)
if not os.path.isfile(SRC):
    done()


def _no_net(*a, **k):
    raise AssertionError("aux_network made a network call")


urllib.request.urlopen = _no_net

HOME = tempfile.mkdtemp(prefix="hermes-c52-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)


def wj(name, obj):
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f)


wj("update-check.json", {"checked_at": 1000.0, "channel": "stable",
                         "payload": {"source": "github-token", "checked_at": 1000.0}})
wj("intel.json", {"version": 1, "updated": 5000.0, "items": []})
LOG = [
    {"ts": 100.0, "type": "breaking_world", "delivered": ["telegram", "hub"]},
    {"ts": 200.0, "type": "midday_brief", "delivered": ["telegram", "hub"]},
    {"ts": 300.0, "type": "price_move", "delivered": [], "suppressed": "quiet_hours"},
    {"ts": 400.0, "type": "rss_keyword", "delivered": ["hub"]},
    {"ts": 250.0, "type": "evening_wrap", "delivered": [], "suppressed": "deliver_failed"},
]
with open(os.path.join(DATA, "watchtower-log.jsonl"), "w") as f:
    for r in LOG:
        f.write(json.dumps(r) + "\n")
    f.write("{not json\n")

CB_ROWS = [  # oldest first, as claude_think appends them to the bridge log
    {"ts": 600.0, "model": "claude-opus", "ok": True},
    {"ts": 700.0, "model": "claude-sonnet", "ok": False, "error": "timed out"},
    {"ts": 800.0, "model": "claude-sonnet", "ok": True, "caller": "foryou"},
    {"ts": 900.0, "model": None, "ok": False, "refused": True, "reason": "secrets"},
]
# the module-global path (CB_LOG_PATH) and the literal fallback are different
# files, so the "no bridge module" case below really reads nothing
CB_LOG_GLOBAL = os.path.join(DATA, "cb-log-global.jsonl")


def write_cb(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


write_cb(CB_LOG_GLOBAL, CB_ROWS)
ROUTES = {}


def make_ns(with_globals):
    ns = {"__name__": "aux_network", "HOME": HOME, "DATA": DATA,
          "register_get": lambda p, fn: ROUTES.__setitem__(p, fn),
          "register_post": lambda p, fn: None}
    if with_globals:
        ns["CB_LOG_PATH"] = CB_LOG_GLOBAL
        ns["_GOOG_NET"] = {"last_out": 7000.0, "refresh": None}
        ns["_wt_log_read"] = lambda limit=200: list(LOG)
        ns["GOOG_TOKEN"] = os.path.join(HOME, ".hermes", "google_token.json")
    exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
    return ns


ns = make_ns(True)
check("GET /api/network/last is registered", "/api/network/last" in ROUTES)
r = ROUTES["/api/network/last"](None)
last = r.get("last") or {}
print("== feeds, updates ==")
check("ok", r.get("ok") is True, r)
check("Feeds = the last feed GATHER (intel.json updated), not the alert log",
      last.get("feeds") == 5000.0, last.get("feeds"))
check("Updates = the last real GitHub check", last.get("updates") == 1000.0, last)
check("...and it says the check signed in with a token",
      r.get("updates_signed") is True, r)
print("== Telegram ==")
check("Alerts = newest non-brief row DELIVERED to Telegram (not suppressed, not "
      "hub-only)", last.get("alerts") == 100.0, last.get("alerts"))
check("Briefings = newest brief row delivered to Telegram (a failed one does "
      "not count)", last.get("briefings") == 200.0, last.get("briefings"))
check("the always-on gateway is not observable here: absent = not tracked",
      "telegram" not in last, last)
print("== Claude ==")
check("Claude = newest call that spawned claude -p (a refusal never left)",
      last.get("claude") == 700.0, last.get("claude"))
check("For-You's calls are counted on their own row",
      last.get("foryou_claude") == 800.0, last.get("foryou_claude"))
print("== Google ==")
check("Google = when a request last went to Google",
      last.get("google") == 7000.0, last.get("google"))
check("google_connected follows the token file", r.get("google_connected") is False, r)
for k in ("models", "websearch", "tts", "dictation", "widgets"):
    check("%s is not claimed as tracked" % k, k not in last, last)

print("== the file fallbacks (aux_watchtower / aux_update not loaded yet) ==")
ns2 = make_ns(False)
r2 = ROUTES["/api/network/last"](None)
l2 = r2.get("last") or {}
check("the watchtower log is read straight from the file",
      l2.get("alerts") == 100.0 and l2.get("briefings") == 200.0, l2)
check("no bridge module: Claude rows read as no record, not a crash",
      l2.get("claude") is None and "claude" in l2, l2)
check("no aux_google: Google reads as no record", l2.get("google") is None, l2)

print("== an escalation older than the last 4 KB of the bridge log ==")
# review C52: the route used the bridge's _cb_log_tail(), which reads only the
# last 4 KB (~20 rows), so a day of hourly For-You rows hid the owner's own
# escalation. The REAL aux_claudebridge.py is loaded first here (as the aux
# loader does: aux_claudebridge sorts before aux_network).
BRIDGE_LOG = os.path.join(DATA, "claude-bridge-log.jsonl")
many = [{"ts": 1000.0, "depth": "deep", "model": "opus", "ok": True,
         "task_summary": "the owner asked", "ms": 30000}]
many += [{"ts": 2000.0 + i, "depth": "quick", "model": "sonnet", "ok": True,
          "caller": "foryou", "ms": 21000, "task_summary": "x" * 150}
         for i in range(40)]
write_cb(BRIDGE_LOG, many)
check("the fixture really is wider than 4 KB", os.path.getsize(BRIDGE_LOG) > 4096,
      os.path.getsize(BRIDGE_LOG))
ns3 = {"__name__": "aux_network", "HOME": HOME, "DATA": DATA,
       "register_get": lambda p, fn: ROUTES.__setitem__(p, fn),
       "register_post": lambda p, fn: None, "settings_update": lambda fn: None}
for rel in ("aux_claudebridge.py", "aux_network.py"):
    src = os.path.join(REPO, "dashboard", rel)
    exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns3)
l4 = ROUTES["/api/network/last"](None).get("last") or {}
check("Claude still finds the escalation behind 40 For-You rows",
      l4.get("claude") == 1000.0, l4.get("claude"))
check("...and For-You's newest call", l4.get("foryou_claude") == 2039.0,
      l4.get("foryou_claude"))
os.remove(BRIDGE_LOG)

print("== a missing or corrupt store is 'no record', never a crash ==")
os.remove(os.path.join(DATA, "intel.json"))
with open(os.path.join(DATA, "update-check.json"), "w") as f:
    f.write("{corrupt")
r3 = ROUTES["/api/network/last"](None)
check("still ok", r3.get("ok") is True, r3)
check("feeds: no record", r3["last"].get("feeds") is None, r3["last"])
check("updates: no record, and not signed", r3["last"].get("updates") is None and
      r3.get("updates_signed") is False, r3)

print("== GitHub: the repositories you connect, and pushes you approve (round 3) ==")
# The REAL dashboard/aux_github.py is loaded first (the aux loader's sorted
# order: aux_github < aux_network); its state file and ledger are written
# through its own helpers. Both evidence rows are file reads, no network:
# urllib.request.urlopen still raises (top of this file) and the route still
# answers ok.
ns5 = {"__name__": "aux_network", "HOME": HOME, "DATA": DATA,
       "HERE": os.path.join(REPO, "dashboard"),
       "register_get": lambda p, fn: ROUTES.__setitem__(p, fn),
       "register_post": lambda p, fn: None, "_gh_checks_thread_started": True,
       "recorder_record_local": lambda **kw: {"ok": True},
       "_wt_send_telegram": lambda text: (True, ""), "_ssl_context": lambda: None}
if os.path.join(REPO, "dashboard") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "dashboard"))
for rel in ("aux_github.py", "aux_network.py"):
    src = os.path.join(REPO, "dashboard", rel)
    exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns5)
lg = ROUTES["/api/network/last"](None)
check("no GitHub record yet: both keys present (tracked) and None",
      lg.get("ok") is True and "github_repos" in lg["last"] and "github_push" in lg["last"]
      and lg["last"]["github_repos"] is None and lg["last"]["github_push"] is None, lg)


def _gh_fix(s):
    s["last_read"], s["last_check"] = 3000.0, 4000.0
    s["repos"]["octo/app"] = {"full_name": "octo/app", "private": True,
                              "default_branch": "main", "enabled": True,
                              "last_fetch": 6000.0, "last_check": 5000.0}


ns5["_gh_state_update"](_gh_fix)
SHA = "a" * 40
for row in ({"event": "push", "repo": "octo/app", "branch": "hermes/fix", "sha": SHA,
             "change_id": "ghc-0123456789ab", "ts": 8000.0},
            {"event": "pr_open", "repo": "octo/app", "branch": "hermes/fix",
             "pr_number": 12, "ts": 8100.0},
            # the compensation row for a push that provably did not land
            {"event": "push", "repo": "octo/app", "branch": "hermes/fix2", "sha": "b" * 40,
             "change_id": "ghc-0123456789ac:undo", "ts": 9000.0},
            {"event": "pr_close", "repo": "octo/app", "branch": "hermes/fix",
             "pr_number": 12, "ts": 9500.0},
            {"event": "ref_delete", "repo": "octo/app", "branch": "hermes/fix", "ts": 9600.0}):
    ns5["_gh_ledger_append"](row)
lg = ROUTES["/api/network/last"](None)
check("still ok with the connector loaded (no network call was attempted)",
      lg.get("ok") is True, lg)
check("github_repos = the newest READ from GitHub (a repository's last fetch beats "
      "the last check and the store's last_read)",
      lg["last"].get("github_repos") == 6000.0, lg["last"])
check("github_push = the newest push / PR open in the ledger; an ':undo' "
      "compensation row, a PR close and a branch delete are not pushes",
      lg["last"].get("github_push") == 8100.0, lg["last"])
check("aux_network resolves them by name: the connector's own answers",
      lg["last"]["github_repos"] == ns5["_gh_last_read_ts"]()
      and lg["last"]["github_push"] == ns5["_gh_last_push_ts"]())
ns5["_gh_last_read_ts"] = lambda: (_ for _ in ()).throw(OSError("unreadable"))
ns5["_gh_last_push_ts"] = lambda: float("nan")
lg = ROUTES["/api/network/last"](None)
check("a connector that raises, or answers NaN, reads as no record — never a 500",
      lg.get("ok") is True and lg["last"].get("github_repos") is None
      and lg["last"].get("github_push") is None, lg)
done()
