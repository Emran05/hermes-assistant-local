#!/usr/bin/env python3
"""GitHub sandbox change pipeline over REAL HTTP (round 3, review of R3-GH2).

t_gh_changes.py proves the decide/retract speed bump (docs/plans/
github-sandbox-2026-10.md §5.4) against a RouteCtx built by hand. This suite
proves the plumbing in front of it: the REAL Handler (ast-extracted from
server.py by _http_harness) on an ephemeral loopback port, its _guard(),
do_POST -> _dispatch_aux -> RouteCtx(headers=self.headers), with the pipeline's
real handlers registered as routes — so the headers the speed bump reads are
the ones an actual request carried (an http.client message, not a dict), and
the answers are what a client would see.

  * no Origin (curl)                      -> 403 owner_only
  * Origin but no Sec-Fetch-Site          -> 403 owner_only
  * Origin != Host (both allowed)         -> 403 owner_only
  * a foreign Origin / cross-site         -> refused by the core _guard() first
  * the dashboard's own headers           -> the push goes through
  * reject needs none of it
  * nothing reached the remote until the last of those
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as G  # noqa: E402
import _http_harness as HH  # noqa: E402

check = G.check

OWN = {"Origin": "http://127.0.0.1:7788", "Sec-Fetch-Site": "same-origin"}


def main():
    w = G.standard_world("http")
    ns = G.load(w)
    tok = G.connect(w, ns, ["octo/app"])
    sb = G.prepare(ns, "octo/app")
    G.write(sb, "src/login.py", "COPY = 'Over HTTP'\n")
    st, r = G.request(ns, "octo/app", "over-http", title="Over HTTP")
    cid = r["id"]
    digest = G.get_change(ns, cid)["digest"]
    before = w.all_refs("octo/app")

    with tempfile.TemporaryDirectory(prefix="hermes-ghhttp-") as d:
        http = HH.http_namespace(d)
        ns["ALLOWED_HOSTS"] = http["ALLOWED_HOSTS"]       # the loader's globals, in production
        ns["_hdr"] = http["_hdr"]
        for (verb, path), fn in ns["ROUTES"].items():
            (http["register_get"] if verb == "GET" else http["register_post"])(path, fn)
        srv = HH.Server(http["Handler"])

        def post(path, body, headers=None):
            st, _h, raw = HH.req(srv.port, "POST", path, json.dumps(body).encode(),
                                 dict({"Content-Type": "application/json"}, **(headers or {})))
            return st, (HH.as_json(raw) or {}), raw

        decide = {"id": cid, "decision": "push_only", "digest": digest, "acks": []}
        try:
            st, j, _ = post("/api/github/changes/decide", decide)
            check("decide with no Origin (curl) -> 403 owner_only", st == 403
                  and j.get("error") == "owner_only", (st, j))
            st, j, _ = post("/api/github/changes/decide", decide, {"Origin": OWN["Origin"]})
            check("...an Origin without Sec-Fetch-Site -> 403 owner_only", st == 403
                  and j.get("error") == "owner_only", (st, j))
            st, j, _ = post("/api/github/changes/decide", decide,
                            {"Sec-Fetch-Site": "same-origin"})
            check("...a Sec-Fetch-Site without an Origin -> 403 owner_only", st == 403
                  and j.get("error") == "owner_only", (st, j))
            st, j, _ = post("/api/github/changes/decide", decide,
                            {"Origin": "http://localhost:7788", "Sec-Fetch-Site": "same-origin"})
            check("...an allowed Origin that is not this request's Host -> 403 owner_only",
                  st == 403 and j.get("error") == "owner_only", (st, j))
            st, j, _ = post("/api/github/changes/decide", decide,
                            {"Origin": OWN["Origin"], "Sec-Fetch-Site": "same-site"})
            check("...same-site is not same-origin -> 403 owner_only", st == 403
                  and j.get("error") == "owner_only", (st, j))
            st, j, _ = post("/api/github/changes/decide", decide,
                            {"Origin": "http://evil.example", "Sec-Fetch-Site": "same-origin"})
            check("a foreign Origin never reaches the pipeline (core _guard 403)", st == 403
                  and "owner_only" not in json.dumps(j), (st, j))
            st, j, _ = post("/api/github/changes/decide", decide,
                            {"Origin": OWN["Origin"], "Sec-Fetch-Site": "cross-site"})
            check("...nor does a cross-site request", st == 403
                  and "owner_only" not in json.dumps(j), (st, j))
            st, j, _ = post("/api/github/changes/retract", {"id": cid})
            check("retract with no Origin -> 403 owner_only", st == 403
                  and j.get("error") == "owner_only", (st, j))
            check("none of those moved a ref, and the change is still pending",
                  w.all_refs("octo/app") == before and G.get_change(ns, cid)["state"] == "pending")

            G.write(sb, "docs/rej.md", "x\n")
            st, r = G.request(ns, "octo/app", "rej-http", title="Reject over HTTP")
            rid = r["id"]
            st, j, _ = post("/api/github/changes/decide",
                            {"id": rid, "decision": "reject",
                             "digest": G.get_change(ns, rid)["digest"]})
            check("reject over HTTP needs no Origin (it only reduces capability)", st == 200
                  and j.get("state") == "rejected", (st, j))

            st, j, raw = post("/api/github/changes/decide", decide, OWN)
            check("the dashboard's own headers -> the push goes through", st == 200
                  and j.get("state") == "pushed" and j.get("pushed_sha"), (st, j))
            check("...exactly one ref changed: hermes/over-http",
                  set(w.all_refs("octo/app").splitlines()) - set(before.splitlines())
                  == {"refs/heads/hermes/over-http " + j.get("pushed_sha", "")})
            check("...and the answer has no token", not G.leaks(raw.decode("utf-8", "replace"), tok))
            st, j, _ = post("/api/github/changes/retract", {"id": cid}, OWN)
            check("retract with the dashboard's own headers deletes the branch", st == 200
                  and j.get("state") == "retracted"
                  and "hermes/over-http" not in w.heads("octo/app"), (st, j))
        finally:
            srv.close()
        check("the server logged no handler error", not srv.errors, srv.errors[:1])
    w.stop()
    G.finish()


if __name__ == "__main__":
    main()
