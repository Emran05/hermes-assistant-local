#!/usr/bin/env python3
"""Regression test for B27 (docs/audits/2026-10-08-codebase-audit.md).

BEFORE: aux_config's _cfg_clean_models kept only id/label/ram/note, and
snapshot_apply wrote that list over models.json. A config export -> import
round trip therefore stripped every model's backend, template_args, drafter
(draft_model/draft_subfolder/draft_kind/draft_block_size), ctx,
ignore_patterns and hf_offline — the 27B came back on mlx-lm with thinking on
and no MTP drafter — and deleted every model the snapshot did not list.

AFTER: the cleaner keeps the full roster schema, each field validated
(invalid -> dropped, never rewritten; trust_remote_code is never carried),
and apply MERGES into the live roster: the snapshot's fields win for the ids
it lists, live-only fields and live-only models are kept.

ast-extracts the REAL cleaner/merge/snapshot_apply from dashboard/aux_config.py
with the dashboard globals stubbed; seeds come from server.py's _SEED_MODELS.
"""
import ast
import json
import os
import re
import sys
import threading
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
CFG = REPO / "dashboard" / "aux_config.py"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def extract(path, names, ns):
    tree = ast.parse(Path(path).read_text())
    found, nodes = set(), []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names:
            nodes.append(n); found.add(n.name)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n); found.update(t.id for t in n.targets if isinstance(t, ast.Name))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    return set(names) - found


def literal(path, name):
    for n in ast.parse(Path(path).read_text()).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                             for t in n.targets):
            return ast.literal_eval(n.value)
    raise KeyError(name)


SEEDS = literal(REPO / "dashboard" / "server.py", "_SEED_MODELS")
P27 = "mlx-community/Qwen3.8-27B-4bit"
UNC = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
CUSTOM = {"id": "me/custom-7b", "label": "Custom", "ram": 5, "note": "mine",
          "backend": "mlx_vlm", "template_args": {"enable_thinking": False},
          "draft_model": "me/custom-drafter", "draft_kind": "mtp", "draft_block_size": 3,
          "trust_remote_code": True}


def main():
    live = [dict(m) for m in SEEDS] + [dict(CUSTOM)]
    written = []
    ns = {"re": re, "os": os, "json": json, "_state_lock": threading.Lock(),
          "MODELS_FILE": "/dev/null/models.json",
          "write_json": lambda p, obj: written.append(obj),
          "_model_registry": lambda: [dict(m) for m in live],
          "_cfg_backup": lambda: "pre-restore-test.json", "active_model": lambda: P27,
          "switch_model": lambda m: {"ok": True}, "_model_downloaded": lambda m: True,
          "_cfg_pm": None}
    missing = extract(CFG, {"CFG_MODEL_ID_RE", "CFG_SUBFOLDER_RE", "CFG_GLOB_RE",
                            "CFG_TA_KEY_RE", "_cfg_model_id", "_cfg_int",
                            "_cfg_clean_model_entry", "_cfg_clean_models",
                            "_cfg_merge_roster", "snapshot_apply"}, ns)
    if missing:
        # the pre-fix module: still run the round trip through what exists
        extract(CFG, {"_cfg_clean_models", "snapshot_apply"}, ns)
        check("roster schema helpers present", False, sorted(missing))

    # export = the cleaner over the live roster (what snapshot_build does)
    exported = ns["_cfg_clean_models"]({"active": P27, "roster": live})
    byid = {m["id"]: m for m in exported["roster"]}
    seed27 = next(m for m in SEEDS if m["id"] == P27)
    e27 = byid.get(P27, {})
    check("export keeps the 27B's backend/template_args/drafter/ctx",
          all(e27.get(k) == seed27[k] for k in ("backend", "template_args", "draft_model",
                                                 "draft_kind", "draft_block_size", "ctx")), e27)
    eu = byid.get(UNC, {})
    check("export keeps the uncensored entry's in-repo drafter + download scope + hf_offline",
          eu.get("draft_subfolder") == "mtp" and eu.get("hf_offline") is True
          and eu.get("ignore_patterns") == ["2-bit/*", "4-bit/*", "6-bit/*", "8-bit/*"], eu)
    check("export never carries trust_remote_code",
          "trust_remote_code" not in byid.get("me/custom-7b", {}), byid.get("me/custom-7b"))

    # import of that export (the round trip), roster section only
    written[:] = []
    ns["snapshot_apply"]({"dashboard": {"models": exported}}, ["models"], False, {})
    roster = written[-1]["models"] if written else []
    rb = {m["id"]: m for m in roster}
    check("round trip: the 27B still loads on mlx_vlm with its MTP drafter and thinking off",
          rb.get(P27, {}).get("backend") == "mlx_vlm"
          and rb.get(P27, {}).get("draft_model") == seed27["draft_model"]
          and rb.get(P27, {}).get("template_args") == {"enable_thinking": False}, rb.get(P27))
    check("round trip: the custom model keeps its backend and drafter",
          rb.get("me/custom-7b", {}).get("backend") == "mlx_vlm"
          and rb.get("me/custom-7b", {}).get("draft_model") == "me/custom-drafter", rb.get("me/custom-7b"))
    check("round trip: the owner's hand-set trust_remote_code survives (live-only field)",
          rb.get("me/custom-7b", {}).get("trust_remote_code") is True, rb.get("me/custom-7b"))

    # an OLD-format snapshot (id/label/ram/note only) that lists just one model
    old_snap = {"active": P27, "roster": [{"id": P27, "label": "Brain", "ram": 19, "note": "n"}]}
    written[:] = []
    ns["snapshot_apply"]({"dashboard": {"models": ns["_cfg_clean_models"](old_snap)}},
                         ["models"], False, {})
    roster = written[-1]["models"] if written else []
    rb = {m["id"]: m for m in roster}
    check("old-format import: unlisted models are NOT deleted",
          {m["id"] for m in live} <= set(rb), sorted(rb))
    check("old-format import: the listed model keeps its backend/drafter, takes the new label",
          rb.get(P27, {}).get("backend") == "mlx_vlm" and rb.get(P27, {}).get("draft_model")
          and rb.get(P27, {}).get("label") == "Brain", rb.get(P27))

    # hostile / malformed fields are dropped, never rewritten
    bad = ns["_cfg_clean_models"]({"active": "../../etc", "roster": [
        {"id": "../escape"}, {"id": "a b/c"},
        {"id": "org/x", "backend": "evil", "draft_model": "not a repo",
         "draft_subfolder": "../../x", "draft_block_size": 999, "ctx": True,
         "template_args": {"enable_thinking": False, "bad key": 1, "x": {"nested": 1}},
         "ignore_patterns": ["../*", "ok/*"], "trust_remote_code": True, "hf_offline": "yes"}]})
    ent = bad["roster"][0] if bad["roster"] else {}
    check("invalid ids and an invalid active are dropped",
          bad["active"] == "" and [m["id"] for m in bad["roster"]] == ["org/x"], bad)
    check("invalid fields dropped: backend, drafter, subfolder, block size, ctx, hf_offline",
          not any(k in ent for k in ("backend", "draft_model", "draft_subfolder",
                                     "draft_block_size", "ctx", "hf_offline")), ent)
    check("template_args keeps only valid scalar keys",
          ent.get("template_args") == {"enable_thinking": False}, ent.get("template_args"))
    check("ignore_patterns drops a '..' pattern", ent.get("ignore_patterns") == ["ok/*"], ent)
    check("trust_remote_code from a snapshot is never imported", "trust_remote_code" not in ent, ent)
    if "_cfg_merge_roster" in ns:
        merged = ns["_cfg_merge_roster"]([{"id": "org/x", "label": "L"}],
                                          [{"id": "org/x", "trust_remote_code": True}])
        check("merge never writes trust_remote_code even if handed one",
              "trust_remote_code" not in merged[0], merged)
    # review: template_args whose every key is invalid must be DROPPED, not
    # turned into {} — the merge would then wipe the live args, and an empty
    # template_args starts the Qwen3.8 27B with thinking ON (its xhigh default)
    allbad = ns["_cfg_clean_models"]({"active": P27, "roster": [
        {"id": P27, "template_args": {"x": {"nested": 1}, "bad key": 1}}]})
    e = allbad["roster"][0] if allbad["roster"] else {}
    check("all-invalid template_args are dropped, not emitted as {}",
          "template_args" not in e, e)
    if "_cfg_merge_roster" in ns:
        m2 = ns["_cfg_merge_roster"]([dict(seed27)], allbad["roster"])
        check("...so an import keeps the live 27B's thinking-off template args",
              m2[0].get("template_args") == {"enable_thinking": False}, m2[0])
    empty = ns["_cfg_clean_models"]({"active": P27, "roster": [
        {"id": P27, "template_args": {}}]})
    check("an explicitly empty template_args is still carried",
          (empty["roster"][0] if empty["roster"] else {}).get("template_args") == {}, empty)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
