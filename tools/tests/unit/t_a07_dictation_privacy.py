#!/usr/bin/env python3
"""Regression test for audit finding A07 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: `_dct_finish()` snapshotted the dictation settings BEFORE the model
cleanup pass and decided from that snapshot whether the transcript was allowed
to be stored.  `_dct_record()` never rechecked.  So turning "keep history" off
during a dictation scrubbed every stored row via `_dct_forget_text()` and then
had the in-flight request append a BRAND-NEW row carrying the transcript: the
UI hid it, the words stayed on disk.
(docs/audits/reproduce_20260910.py's dictation_history_race() showed exactly
that.)

AFTER: the privacy decision is made at PERSISTENCE time.  `_dct_record()`
re-reads `keep_history` from the live settings and strips `text` when it is
off, keeping only the counts the "today" numbers are made of.  The settings
read happens BEFORE `_dct_lock` is taken — the module's one lock-order rule,
because `dictation_set_settings()` goes settings-lock-then-`_dct_lock` on the
scrub path and inverting that here would deadlock.

Real functions, extracted with `ast`.  No dashboard, no model, no microphone;
the store is a throwaway tempfile.mkdtemp().
"""
import ast
import copy
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {n.name for n in found} == set(names), \
        "missing %s in %s" % (set(names) - {n.name for n in found}, relative)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"),
         namespace)


def store_namespace(scratch, live):
    """aux_dictation's store half, with the live settings under `live`."""
    lock = threading.Lock()
    ns = dict(_dct_os=os, _dct_json=json, _dct_time=time, _dct_lock=lock,
              DCT_STORE=str(scratch / "dictation.json"),
              DCT_MAX_CHARS=16000, DCT_HISTORY_MAX=100,
              DCT_DEFAULTS={"cleanup": "rules", "hotkey": "f5",
                            "history_days": 30, "keep_history": True},
              _dct_style_for=lambda *a: "prose",
              dictation_clean_rules=lambda text, *a: text,
              _dct_norm=lambda text: text,
              _dct_log=lambda msg: None)

    def settings():
        # THE LOCK-ORDER ASSERTION: a settings read must never happen while
        # _dct_lock is held, or a scrub taking the settings lock first would
        # deadlock against it.
        if not lock.acquire(blocking=False):
            raise AssertionError("settings were read while _dct_lock was held "
                                 "— that is the deadlock this ordering exists "
                                 "to prevent")
        lock.release()
        return copy.deepcopy(live)

    ns["dictation_settings"] = settings
    definitions("dashboard/aux_dictation.py",
                ["_dct_num", "_dct_bool", "_dct_load", "_dct_save",
                 "_dct_prune", "_dct_forget_text", "_dct_record",
                 "_dct_finish"], ns)
    return ns


# ---------------------------------------------------------------------------
# 1. THE AUDIT PROBE: history is turned off DURING the model pass
# ---------------------------------------------------------------------------
def test_optout_during_the_model_pass():
    with tempfile.TemporaryDirectory(prefix="hermes-a07-") as d:
        live = {"cleanup": "model", "dictionary": {}, "keep_history": True,
                "history_days": 30}
        ns = store_namespace(Path(d), live)

        scrubbed = []

        def cleanup(text, style):
            # the owner flips "keep history" off while the model pass runs
            live["keep_history"] = False
            scrubbed.append(ns["_dct_forget_text"]())
            return text, "fake cleanup"

        ns["_dct_model_clean"] = cleanup
        out = ns["_dct_finish"](SimpleNamespace(
            body={"text": "Private audit sample"}))

        check("the scrub ran and reported success", scrubbed == [True], scrubbed)
        check("the setting really is off", live["keep_history"] is False)
        rows = ns["_dct_load"]()["history"]
        check("a row was still recorded (the counts are not a privacy problem)",
              len(rows) == 1, rows)
        check("THE FIX: the transcript is NOT on disk",
              "text" not in rows[-1], rows[-1])
        check("no row anywhere in the store carries text",
              not any("text" in r for r in rows), rows)
        check("the word count survives (the 'today' numbers still work)",
              rows[-1]["words"] == 3, rows[-1])
        check("the caller still gets its text back (it has to be typed)",
              out["text"] == "Private audit sample", out)
        raw = Path(ns["DCT_STORE"]).read_text()
        check("the transcript appears nowhere in the store file",
              "Private audit sample" not in raw, raw[:200])


# ---------------------------------------------------------------------------
# 2. the decision is made at persistence time, both ways
# ---------------------------------------------------------------------------
def test_record_rechecks():
    with tempfile.TemporaryDirectory(prefix="hermes-a07-") as d:
        live = {"cleanup": "off", "dictionary": {}, "keep_history": True,
                "history_days": 30}
        ns = store_namespace(Path(d), live)

        # history ON -> the text is stored (the fix is not "never store")
        ns["_dct_record"]({"ts": time.time(), "words": 2, "text": "hello there"},
                          {"history_days": 30})
        check("with history on, the transcript IS stored",
              ns["_dct_load"]()["history"][-1].get("text") == "hello there")

        # a stale snapshot saying True cannot re-enable storage
        live["keep_history"] = False
        ns["_dct_record"]({"ts": time.time(), "words": 2, "text": "secret"},
                          {"history_days": 30, "keep_history": True})
        last = ns["_dct_load"]()["history"][-1]
        check("THE FIX: a caller's stale keep_history=True is ignored",
              "text" not in last, last)
        check("_dct_record reports whether it wrote",
              ns["_dct_record"]({"ts": time.time(), "words": 1},
                                {"history_days": 30}) is True)

        # …and back on again
        live["keep_history"] = True
        ns["_dct_record"]({"ts": time.time(), "words": 1, "text": "again"},
                          {"history_days": 30})
        check("turning history back on stores text again",
              ns["_dct_load"]()["history"][-1].get("text") == "again")

        # the live retention window wins over the caller's snapshot too
        live["history_days"] = 0
        ns["_dct_record"]({"ts": time.time(), "words": 1}, {"history_days": 30})
        check("history_days is also read live (0 days keeps nothing)",
              ns["_dct_load"]()["history"] == [],
              ns["_dct_load"]()["history"])


# ---------------------------------------------------------------------------
# 3. lock order: settings first, then the store lock
# ---------------------------------------------------------------------------
def test_lock_order():
    with tempfile.TemporaryDirectory(prefix="hermes-a07-") as d:
        live = {"cleanup": "off", "dictionary": {}, "keep_history": True,
                "history_days": 30}
        ns = store_namespace(Path(d), live)
        # store_namespace's settings stub raises if _dct_lock is held; a clean
        # record therefore proves the order.
        try:
            ns["_dct_record"]({"ts": time.time(), "words": 1, "text": "x"},
                              {"history_days": 30})
            ok, why = True, ""
        except AssertionError as e:
            ok, why = False, str(e)
        check("_dct_record reads settings BEFORE taking _dct_lock", ok, why)

        src = (REPO / "dashboard/aux_dictation.py").read_text()
        body = src.split("def _dct_record")[1].split("\ndef ")[0]
        check("the settings read is outside the `with _dct_lock` block",
              body.index("dictation_settings()") < body.index("with _dct_lock"),
              body[:0])
        check("the lock order is written down where the next reader will see it",
              "LOCK ORDER" in body)
        check("_dct_finish no longer decides persistence from its snapshot",
              "_dct_record re-reads keep_history itself"
              in src.split("def _dct_finish")[1])
        # CLAUDE.md: an aux module must never rebind the shared global
        # `datetime` — it has to import under a private alias. (The phrase
        # appears in this module's own warning comment, so check the IMPORT
        # statements, not the raw text.)
        imports = [ln.strip() for ln in src.splitlines()
                   if ln.startswith(("import ", "from "))]
        check("datetime is imported under a private alias (CLAUDE.md aux rule)",
              "import datetime as _dct_datetime" in imports
              and not any(i.startswith("from datetime import") for i in imports),
              imports)


def main():
    print("A07: in-flight dictation persists text after history is turned off "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    print("\n1. the audit probe: opt-out during the model pass")
    test_optout_during_the_model_pass()
    print("\n2. the decision is made at persistence time")
    test_record_rechecks()
    print("\n3. lock order")
    test_lock_order()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
