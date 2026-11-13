#!/usr/bin/env python3
"""C50 (+ C36 fix 2) — the agent web-research pass is an explicit, default-off
switch and runs with web_search only.

It was documented as dormant until a web provider existed; ddgs and
`web.backend: ddgs` made it live, unattended and ungated: a 6-hourly probe
search plus an hourly full-toolset `hermes -z` told to search the web NOW.
Drives the real aux_watchtower.py (see _wt_harness.py); subprocess.run and
urlopen are recorders, nothing runs and nothing leaves the process.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
EMPTY = b'<?xml version="1.0"?><rss><channel></channel></rss>'
AGENT_OUT = json.dumps([{"title": "Agent found", "url": "https://example.org/a",
                         "source": "web", "why": "w"}])


class P:
    def __init__(self, out, rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


class Runs:
    def __init__(self, g):
        self.g, self.calls = g, []

    def __enter__(self):
        self._orig = subprocess.run

        def fake(argv, *a, **k):
            self.calls.append(list(argv))
            if argv and argv[0] == self.g["_VENV_PY"]:
                return P(json.dumps({"success": True, "data": [{"t": 1}]}))
            return P(AGENT_OUT)
        subprocess.run = fake
        return self

    def __exit__(self, *a):
        subprocess.run = self._orig

    def probes(self):
        return [c for c in self.calls if c and c[0] == self.g["_VENV_PY"]]

    def agents(self):
        return [c for c in self.calls if c and c[0] == self.g["HERMES"]]


def gather(g):
    g["_widget_cache"].clear()                       # no cached probe verdict
    with H.patch_urlopen(lambda req: H.FakeResp(EMPTY, req.full_url)):
        with Runs(g) as r:
            g["_intel_gather"]()
    return r


def main():
    print("C50: agent web research is opt-in and web-search-only")
    g, home, data = H.load()
    wt = os.path.join(data, "watchtower.json")
    os.makedirs(os.path.dirname(g["_VENV_PY"]), exist_ok=True)   # "ddgs is installed"
    open(g["_VENV_PY"], "w").close()
    g["bg_lane"] = lambda: {"lane": "bg", "hermes_args": ["--provider", "custom:bg", "-m", "bg"]}
    post = g["watchtower_post_handler"]

    # ---- default: off, and nothing runs -------------------------------------
    H.wj(wt, {"master": {"briefings": True, "news": True}})
    cfg = g["_wt_load"]()
    check("default: web_research is off",
          (cfg.get("web_research") or {}).get("enabled") is False, cfg.get("web_research"))
    r = gather(g)
    check("default: the gather runs no probe search", r.probes() == [], r.probes())
    check("default: the gather runs no agent", r.agents() == [], r.agents())

    # ---- only an explicit boolean true turns it on --------------------------
    post(H.Ctx(body={"op": "set_web_research", "enabled": "true"}))
    check("string 'true' leaves it off", g["_wt_load"]()["web_research"]["enabled"] is False,
          g["_wt_load"]().get("web_research"))
    res = post(H.Ctx(body={"op": "set_web_research", "enabled": True}))
    check("explicit true turns it on", isinstance(res, dict) and res.get("ok") and
          g["_wt_load"]()["web_research"]["enabled"] is True, res)
    got = g["watchtower_get_handler"](H.Ctx())
    check("GET /api/watchtower carries web_research",
          got.get("web_research") == {"enabled": True}, got.get("web_research"))

    # ---- on, no run_agent(toolsets=) available: direct run, web search only --
    r = gather(g)
    check("on: the probe runs", len(r.probes()) == 1, r.calls)
    check("on: one agent run", len(r.agents()) == 1, r.agents())
    if r.agents():
        argv = r.agents()[0]
        check("fallback run passes --toolsets search before -z",
              "--toolsets" in argv and argv[argv.index("--toolsets") + 1] == "search"
              and argv.index("--toolsets") < argv.index("-z"), argv)
    items = g["_intel_load"]()["items"]
    check("the agent's items reach the store", any(i.get("topic") == "Agent" for i in items), items)

    # ---- run_agent WITHOUT a toolsets keyword is never used ------------------
    used = []
    g["run_agent"] = lambda message, session=None, lane="primary": used.append(lane) or (True, "[]")
    r = gather(g)
    check("old run_agent (no toolsets=) is not called", used == [], used)
    check("... the reduced direct run is used instead",
          len(r.agents()) == 1 and "--toolsets" in r.agents()[0], r.agents())

    # ---- R2-20: not even run_agent(toolsets=) — it holds _agent_lock 600 s ----
    seen = []

    def ra(message, session=None, lane="primary", toolsets=None):
        seen.append({"lane": lane, "toolsets": toolsets})
        return True, AGENT_OUT
    g["run_agent"] = ra
    r = gather(g)
    check("run_agent(toolsets=) is not used either", seen == [], seen)
    check("... the direct run on the bg lane with --toolsets search is",
          len(r.agents()) == 1 and r.agents()[0][1:5] == ["--provider", "custom:bg", "-m", "bg"]
          and r.agents()[0][r.agents()[0].index("--toolsets") + 1] == "search", r.agents())

    # ---- News off wins over the research switch -----------------------------
    H.wj(wt, {"master": {"briefings": True, "news": False}, "web_research": {"enabled": True}})
    seen.clear()
    r = gather(g)
    check("News off: no probe, no agent", r.probes() == [] and r.agents() == [] and seen == [],
          (r.calls, seen))

    # ---- GET /api/intel never runs the probe --------------------------------
    H.wj(wt, {"master": {"briefings": True, "news": True}, "web_research": {"enabled": True}})
    g["_widget_cache"].clear()
    with Runs(g) as r:
        out = g["intel_handler"](H.Ctx())
    check("GET /api/intel runs no probe search", r.calls == [], r.calls)
    check("GET /api/intel reports an unknown verdict as None",
          out.get("web_search_available") is None, out.get("web_search_available"))
    H.finish()


if __name__ == "__main__":
    main()
