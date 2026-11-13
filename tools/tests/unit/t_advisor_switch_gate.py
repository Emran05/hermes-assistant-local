#!/usr/bin/env python3
"""Round 3 (owner decision, model advisor): the UNCENSORED SWITCH GATE.

POST /api/models/switch to a roster row whose _roster_uncensored is true (or
whose advisor facts / curated shape say uncensored) answers

    409 {"ok": false, "needs_ack": true, "error": "uncensored_ack", "id", "label",
         "message", "ack_text"?}

and switches NOTHING, unless the body carries `ack_uncensored: true` (a JSON
boolean — the C59.1 rule; "true" is a 400) or settings.json
advisor.uncensored_ack is exactly true.  Internal callers of switch_model()
(wake/resume, the config import, the drill's temporary swap) are unchanged.

Two harnesses, both the REAL code:
  A. the REAL Handler + _switch_ack_refusal + the roster helpers,
     ast-extracted from dashboard/server.py (tools/tests/unit/_http_harness.py)
     on an ephemeral loopback port, with the real settings.json readers bound
     to a throwaway file and switch_model a recorder;
  B. the REAL server.py fully loaded (every aux module) against a throwaway
     HOME, its own Handler on an ephemeral port — the advisor's
     acknowledgement copy rides in the 409, and the stored acknowledgement
     written by POST /api/advisor/answers opens the gate.
No model is switched, woken or contacted; nothing touches ~/.hermes.
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

sys.path.insert(0, os.path.join(str(H.REPO), "tools", "tests", "lib"))
import server_real  # noqa: E402

check = H.check
SERVER = H.REPO / "dashboard" / "server.py"
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
POCKET = "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"
HERETIC = "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit"
HUIHUI = "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit"
BG9 = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
STOCK = ["mlx-community/Qwen3.8-27B-4bit", "mlx-community/Qwen3.5-9B-4bit",
         "mlx-community/Qwen3.6-35B-A3B-4bit"]
UNCENSORED = [ORCA, POCKET, HERETIC, HUIHUI, BG9]
CONTRACT_KEYS = {"ok", "needs_ack", "error", "id", "label", "message"}


def post(port, body, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    st, _h, b = H.req(port, "POST", "/api/models/switch", data,
                      headers={"Content-Type": "application/json"})
    return st, H.as_json(b)


# ==========================================================================
def part_a():
    print("\nA. the real Handler + gate, ast-extracted (no aux modules)")
    with tempfile.TemporaryDirectory(prefix="hermes-switch-gate-") as d:
        settings = os.path.join(d, "settings.json")
        calls = []
        roster = {"rows": None}

        def switch_model(mid):
            calls.append(mid)
            return {"ok": True, "active": mid, "loading": True}

        ns = H.http_namespace(d, switch_model=switch_model)
        H.extract(["_SEED_MODELS", "_seed_entry", "_roster_uncensored", "_switch_ack_refusal",
                   "_id_get"], ns)          # E104: ids matched case-insensitively
        ns.update(server_real.settings_api(settings))
        seeds = {m["id"]: m for m in ns["_SEED_MODELS"]}

        def model_entry(mid):
            rows = roster["rows"] if roster["rows"] is not None else list(seeds.values())
            return next((m for m in rows if m.get("id") == mid), None)
        ns["_model_entry"] = model_entry
        srv = H.Server(ns["Handler"])
        try:
            for mid in STOCK:
                del calls[:]
                st, j = post(srv.port, {"id": mid})
                check("a stock model (%s) switches with no acknowledgement" % mid.split("/")[1],
                      st == 200 and calls == [mid] and (j or {}).get("ok") is True, (st, j, calls))
            for mid in UNCENSORED:
                del calls[:]
                st, j = post(srv.port, {"id": mid})
                check("%s without an ack -> 409 needs_ack, NOTHING switched" % mid.split("/")[0],
                      st == 409 and calls == [] and j.get("ok") is False and j.get("needs_ack") is True
                      and j.get("error") == "uncensored_ack" and j.get("id") == mid, (st, j, calls))
            st, j = post(srv.port, {"id": ORCA})
            check("the 409 body is the published contract (ok, needs_ack, error, id, label, message)",
                  CONTRACT_KEYS <= set(j) and set(j) <= CONTRACT_KEYS | {"ack_text"}
                  and j["label"] == "Qwen3.8-27B Uncensored" and j["message"], j)
            check("...no ack_text without the advisor module (it is optional)", "ack_text" not in j, j)
            del calls[:]
            st, j = post(srv.port, {"id": ORCA, "ack_uncensored": True})
            check("ack_uncensored: true on the request lets it through to switch_model",
                  st == 200 and calls == [ORCA], (st, j, calls))
            del calls[:]
            st, j = post(srv.port, {"id": " %s " % ORCA, "ack_uncensored": True})
            check("...the id is stripped exactly as before", calls == [ORCA], calls)
            for bad in ("true", 1, "yes", [True], {"v": True}):
                del calls[:]
                st, j = post(srv.port, {"id": ORCA, "ack_uncensored": bad})
                check("ack_uncensored %r (not a JSON boolean) -> 400, nothing switched" % (bad,),
                      st == 400 and calls == [] and (j or {}).get("ok") is False, (st, j))
            del calls[:]
            st, j = post(srv.port, {"id": ORCA, "ack_uncensored": False})
            check("ack_uncensored: false -> still 409", st == 409 and calls == [], (st, j))
            del calls[:]
            st, j = post(srv.port, {"id": ORCA, "ack_uncensored": None})
            check("ack_uncensored: null is not a JSON boolean either -> 400, nothing switched",
                  st == 400 and calls == [], (st, j))
            # the stored acknowledgement (settings.json advisor.uncensored_ack)
            for stored, opens in (({"advisor": {"uncensored_ack": True}}, True),
                                  ({"advisor": {"uncensored_ack": "true"}}, False),
                                  ({"advisor": {"uncensored_ack": 1}}, False),
                                  ({"advisor": True}, False),
                                  ({"uncensored_ack": True}, False),
                                  ({}, False)):
                with open(settings, "w") as f:
                    json.dump(stored, f)
                del calls[:]
                st, j = post(srv.port, {"id": HERETIC})
                check("stored %s -> %s" % (json.dumps(stored), "switches" if opens else "409"),
                      (st == 200 and calls == [HERETIC]) if opens else (st == 409 and calls == []),
                      (st, j, calls))
            with open(settings, "w") as f:
                f.write("{ this is not json")
            del calls[:]
            st, j = post(srv.port, {"id": HERETIC})
            check("a CORRUPT settings.json reads as no acknowledgement: the gate fails closed",
                  st == 409 and calls == [], (st, j))
            os.remove(settings)
            del calls[:]
            st, j = post(srv.port, {"id": HERETIC})
            check("no settings.json at all -> 409", st == 409 and calls == [], (st, j))
            # roster edits cannot relabel a curated uncensored seed
            roster["rows"] = [dict(seeds[ORCA], uncensored=False)]
            del calls[:]
            st, j = post(srv.port, {"id": ORCA})
            check("a hand-edited models.json row saying uncensored:false for a curated "
                  "abliterated seed is still gated (sticky to the seed)", st == 409 and calls == [], (st, j))
            roster["rows"] = [{"id": "someone/self-added-abliterated", "label": "Mine",
                               "uncensored": True}]
            del calls[:]
            st, j = post(srv.port, {"id": "someone/self-added-abliterated"})
            check("a user-added row flagged uncensored:true is gated too", st == 409 and calls == [] and
                  j.get("label") == "Mine", (st, j))
            roster["rows"] = None
            del calls[:]
            st, j = post(srv.port, {"id": "someone/unknown-model"})
            check("an unknown id is not uncensored: it reaches switch_model (which answers "
                  "'unknown model' itself)", calls == ["someone/unknown-model"], (st, calls))
            # the gate reads the advisor's shapes / facts by name when they exist
            ns["_ADV_FACTS"] = {"someone/facts-only": {"uncensored": True}}
            del calls[:]
            st, j = post(srv.port, {"id": "someone/facts-only"})
            check("an id the advisor's FACTS mark uncensored is gated", st == 409 and calls == [], (st, j))
            ns["_ADV_BY_ID"] = {"someone/extra-only": {"id": "someone/extra-only", "uncensored": True}}
            del calls[:]
            st, j = post(srv.port, {"id": "someone/extra-only"})
            check("...and one the advisor's curated SHAPE marks uncensored", st == 409 and calls == [], (st, j))
            # the shapes that already existed keep working
            del calls[:]
            st, j = post(srv.port, {"id": 5})
            check("a non-string id is still a 400 (X-http-plumbing-3), nothing switched",
                  st == 400 and calls == [], (st, j))
            del calls[:]
            st, j = post(srv.port, {})
            check("no id still reaches switch_model('') as before", calls == [""], calls)
            check("no request reached socketserver's handle_error", not srv.errors, srv.errors[:1])
        finally:
            srv.close()


# ==========================================================================
def part_b():
    print("\nB. the REAL server.py (all aux modules) end to end")
    tmp = tempfile.mkdtemp(prefix="hermes-switch-gate-full-")
    os.environ["HOME"] = tmp
    os.makedirs(os.path.join(tmp, ".hermes", "dashboard"), exist_ok=True)
    try:
        dash = str(H.REPO / "dashboard")
        sys.path.insert(0, dash)
        spec = importlib.util.spec_from_file_location("hermes_server_switch_gate",
                                                      os.path.join(dash, "server.py"))
        srv = importlib.util.module_from_spec(spec)
        sys.modules["hermes_server_switch_gate"] = srv
        spec.loader.exec_module(srv)
        assert srv.HOME == tmp, srv.HOME
        calls, internal = [], []
        orig_switch = srv.switch_model

        def rec(mid):
            calls.append(mid)
            return {"ok": True, "active": mid, "loading": True}
        srv.switch_model = rec
        srv.model_online = lambda: False
        srv._bg_probe = lambda: False
        server = H.Server(srv.Handler)
        try:
            st, j = post(server.port, {"id": POCKET})
            check("full server: an uncensored seed without an ack -> 409, nothing switched",
                  st == 409 and calls == [] and j.get("needs_ack") is True
                  and j.get("error") == "uncensored_ack", (st, j))
            check("...the 409 carries the advisor's acknowledgement copy (ack_text) for the confirm",
                  isinstance(j.get("ack_text"), str) and "approval gate" in j["ack_text"]
                  and j["ack_text"] == srv._ADVR_UNCENSORED_ACK_TEXT, j)
            st, j = post(server.port, {"id": POCKET, "ack_uncensored": True})
            check("...and the retry with ack_uncensored: true goes through", st == 200 and calls == [POCKET],
                  (st, j, calls))
            del calls[:]
            st2, _h, b = H.req(server.port, "POST", "/api/advisor/answers",
                               json.dumps({"uncensored_ack": True}).encode(),
                               headers={"Content-Type": "application/json"})
            check("POST /api/advisor/answers {uncensored_ack: true} stores the acknowledgement",
                  st2 == 200 and (H.as_json(b) or {}).get("answers", {}).get("uncensored_ack") is True,
                  (st2, b[:200]))
            st, j = post(server.port, {"id": POCKET})
            check("...after which the switch needs no per-request ack", st == 200 and calls == [POCKET],
                  (st, j))
            del calls[:]
            H.req(server.port, "POST", "/api/advisor/answers",
                  json.dumps({"uses": ["chat", "agent"], "uncensored_ack": False}).encode(),
                  headers={"Content-Type": "application/json"})
            st, j = post(server.port, {"id": POCKET})
            check("...and revoking it closes the gate again", st == 409 and calls == [], (st, j))
            st, j = post(server.port, {"id": "mlx-community/Qwen3.8-27B-4bit"})
            check("the stock primary still switches without any ack",
                  st == 200 and calls == ["mlx-community/Qwen3.8-27B-4bit"], (st, j))
        finally:
            server.close()
            srv.switch_model = orig_switch
        # internal callers are unchanged: switch_model() itself has no gate
        src = SERVER.read_text()
        tree = ast.parse(src)
        sw = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "switch_model"][0]
        names = {n.id for n in ast.walk(sw) if isinstance(n, ast.Name)} | \
            {n.value for n in ast.walk(sw) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        check("switch_model() itself never consults the gate (wake/resume, config import and the "
              "drill's swap are unchanged)",
              "_switch_ack_refusal" not in names and "ack_uncensored" not in names
              and "uncensored_ack" not in names, sorted(x for x in names if "ack" in str(x)))
        refs = [fn for fn in sorted(os.listdir(dash)) if fn.endswith(".py")
                and "_switch_ack_refusal" in open(os.path.join(dash, fn)).read()]
        check("only server.py references the gate", refs == ["server.py"], refs)
        handler = re.search(r'if path == "/api/models/switch":(.*?)\n        if path == ', src, re.S)
        body = handler.group(1) if handler else ""
        check("the route checks the gate BEFORE calling switch_model, and returns on a refusal",
              body.find("_switch_ack_refusal") != -1
              and body.find("_switch_ack_refusal") < body.find("switch_model(mid")
              and "self._json(*refused)" in body and '"ack_uncensored", required=False' in body, body)
        # the internal config-import path: still switch_model, never the route
        ac = open(os.path.join(dash, "aux_config.py")).read()
        check("aux_config's apply_active still calls switch_model() directly (unchanged)",
              "switch_model(tgt)" in ac and "/api/models/switch" not in ac)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


part_a()
part_b()
H.finish()
