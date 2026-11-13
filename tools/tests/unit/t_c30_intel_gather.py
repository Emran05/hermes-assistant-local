#!/usr/bin/env python3
"""C30 — an intel gather that fetched nothing, or could not curate, must not
pretend otherwise.

_intel_gather stamped "updated" = now even when no feed answered (offline), so
the brief / Needs-you read a dead hour as fresh, and _intel_curate returned []
whenever the model was asleep or paused, which overwrote the stored curated
picks with an empty list on every hourly pass. Drives the real _intel_gather /
_intel_curate / intel_loop from aux_watchtower.py (see _wt_harness.py); no
network, no model.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check


class _Stop(Exception):
    pass


def main():
    print("C30: intel gather keeps 'updated' and the curated picks honest")
    g, _home, data = H.load()
    intel = os.path.join(data, "intel.json")
    now = time.time()
    item = {"title": "Lab ships a model", "source": "OpenAI", "topic": "Labs",
            "url": "https://example.org/a", "ts": now - 3600}
    gone = {"title": "Old pick", "source": "OpenAI", "url": "https://example.org/gone"}
    pick = {"title": "Lab ships a model", "source": "OpenAI",
            "url": "https://example.org/a", "why": "it matters"}
    H.wj(intel, {"version": 1, "updated": 1000.0, "items": [item],
                 "curated": [pick, gone], "feeds": 2})

    g["_intel_feeds"] = lambda: [("Labs", "OpenAI", "https://feed.example/1"),
                                 ("Labs", "DeepMind", "https://feed.example/2")]
    fetched = {"rows": []}
    g["_intel_fetch_feed"] = lambda url, source, topic: list(fetched["rows"])
    g["model_online"] = lambda: False

    # ---- offline: no feed answered, model asleep -------------------------
    g["_intel_gather"]()
    st = json.load(open(intel))
    check("offline pass: 'updated' is not stamped", st.get("updated") == 1000.0, st.get("updated"))
    check("offline pass: the attempt is recorded (paces the loop)",
          abs((st.get("last_attempt") or 0) - time.time()) < 60, st.get("last_attempt"))
    check("model asleep: the stored pick survives",
          [c.get("url") for c in st.get("curated") or []] == ["https://example.org/a"],
          st.get("curated"))
    check("model asleep: a pick whose item left the store is dropped",
          all(c.get("url") != gone["url"] for c in st.get("curated") or []), st.get("curated"))

    # ---- feeds answer, model paused --------------------------------------
    fetched["rows"] = [{"title": "Fresh", "source": "DeepMind", "topic": "Labs",
                        "url": "https://example.org/b", "ts": time.time() - 60}]
    g["model_online"] = lambda: True
    g["agent_paused"] = lambda: True
    g["_intel_gather"]()
    st = json.load(open(intel))
    check("feeds answered: 'updated' is stamped",
          abs((st.get("updated") or 0) - time.time()) < 60, st.get("updated"))
    check("model paused: the stored pick survives",
          [c.get("url") for c in st.get("curated") or []] == ["https://example.org/a"],
          st.get("curated"))

    # ---- the model ran and chose nothing: that IS an answer --------------
    g["agent_paused"] = lambda: False

    def model(req):
        return H.FakeResp(json.dumps({"choices": [{"message": {"content": "[]"}}]}),
                          req.full_url)
    with H.patch_urlopen(model):
        g["_intel_gather"]()
    st = json.load(open(intel))
    check("model ran and picked nothing: curated is []", st.get("curated") == [], st.get("curated"))

    # ---- the model call failed: keep what we had --------------------------
    H.wj(intel, dict(st, curated=[pick]))

    def boom(req):
        raise OSError("connection refused")
    with H.patch_urlopen(boom):
        g["_intel_gather"]()
    st = json.load(open(intel))
    check("model call failed: the stored pick survives",
          [c.get("url") for c in st.get("curated") or []] == ["https://example.org/a"],
          st.get("curated"))

    # ---- intel_loop paces on the last ATTEMPT, not only on 'updated' ------
    H.wj(intel, {"version": 1, "updated": 1000.0, "last_attempt": time.time() - 60,
                 "items": [], "curated": []})
    runs = []
    real_time = g["time"]

    class FakeTime:
        n = 0

        def time(self):
            return real_time.time()

        def sleep(self, s):
            FakeTime.n += 1
            if FakeTime.n > 2:
                raise _Stop()
    g["time"] = FakeTime()
    g["_intel_gather"] = lambda: runs.append(1)
    try:
        g["intel_loop"]()
    except _Stop:
        pass
    g["time"] = real_time
    check("intel_loop: an offline attempt a minute ago does not re-gather",
          runs == [], runs)
    H.finish()


if __name__ == "__main__":
    main()
