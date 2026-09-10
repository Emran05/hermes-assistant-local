#!/usr/bin/env python3
"""Regression test for audit finding A02 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: dashboard/aux_claudebridge.py's CB_ESC_DEFAULT was True and
claude_escalation_enabled() fell back to it whenever settings.json was
missing OR corrupt — so a fresh install, or a broken settings file, silently
enabled outbound calls to Claude. aux_autoroute.py's AR_DEFAULT_MODE was
"auto" (would start auto-routing turns to Claude by default) and its
_ar_escalation_on() failed OPEN (True) on a missing helper or any exception.
aux_onboarding.py's _onb_prefs() defaulted the same reading to True, and the
first-run sheet's own JS rendered/saved/summarized the toggle on
`!== false`, so an absent/undefined value displayed and saved as "on".
(docs/audits/reproduce_20260910.py's bridge_defaults() demonstrated the
Python half of this.)

AFTER: outbound inference defaults to False everywhere and requires an
EXPLICIT boolean true; every failure mode (missing file, corrupt file,
missing helper, an exception) fails CLOSED; a settings read error is
surfaced once as `config_error`; an existing explicit `true` keeps working.

This suite extracts the REAL functions/constants (via ast, the same
technique the audit's reproduction script uses) from the real files. No
dashboard, no network, no model, no live settings.json is touched (a
throwaway tempfile.mkdtemp() stands in for HOME/SETTINGS_FILE).
"""
import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path

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
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"), namespace)


def constant(relative, name):
    tree = ast.parse((REPO / relative).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("%s not found in %s" % (name, relative))


# ---------------------------------------------------------------------------
# aux_claudebridge.py — the master switch
# ---------------------------------------------------------------------------
def test_bridge_default():
    with tempfile.TemporaryDirectory(prefix="hermes-a02-") as d:
        settings_file = os.path.join(d, "settings.json")
        cb_default = constant("dashboard/aux_claudebridge.py", "CB_ESC_DEFAULT")
        check("CB_ESC_DEFAULT is False (was True)", cb_default is False, cb_default)

        ns = dict(os=os, json=json, sys=sys, SETTINGS_FILE=settings_file,
                  CB_ESC_DEFAULT=cb_default,
                  _CB_ESC_LOGGED={"done": False},
                  _CB_CONFIG_ERROR={"error": None, "logged": False})

        definitions("dashboard/aux_claudebridge.py",
                    ["claude_escalation_enabled", "_cb_settings_dict"], ns)
        enabled = ns["claude_escalation_enabled"]

        # 1. no settings.json at all (fresh install) -> OFF, no config_error
        check("missing settings.json -> disabled", enabled() is False)
        check("...and that is NOT reported as a config error (absence is normal)",
              ns["_CB_CONFIG_ERROR"]["error"] is None)

        # 2. settings.json present but corrupt -> OFF, AND surfaced as config_error
        Path(settings_file).write_text("{broken")
        check("corrupt settings.json -> disabled (was: still enabled)", enabled() is False)
        check("...and IS surfaced as config_error for GET /api/claude/bridge",
              ns["_CB_CONFIG_ERROR"]["error"] is not None,
              ns["_CB_CONFIG_ERROR"]["error"])

        # 3. key absent entirely -> OFF
        Path(settings_file).write_text(json.dumps({}))
        check("empty settings object -> disabled", enabled() is False)
        check("...and the config_error clears on a subsequent clean read",
              ns["_CB_CONFIG_ERROR"]["error"] is None)

        # 4. present but not a literal boolean true -> OFF (no truthy coercion)
        Path(settings_file).write_text(json.dumps({"claude_escalation": {"enabled": "true"}}))
        check('the string "true" is not accepted (must be the JSON boolean)',
              enabled() is False)
        Path(settings_file).write_text(json.dumps({"claude_escalation": {"enabled": 1}}))
        check("the integer 1 is not accepted (must be the JSON boolean)",
              enabled() is False)

        # 5. explicit false -> OFF
        Path(settings_file).write_text(json.dumps({"claude_escalation": {"enabled": False}}))
        check("explicit false -> disabled", enabled() is False)

        # 6. explicit true -> ON (must keep working — the owner's live choice)
        Path(settings_file).write_text(json.dumps({"claude_escalation": {"enabled": True}}))
        check("explicit true -> enabled (must keep working)", enabled() is True)


# ---------------------------------------------------------------------------
# aux_autoroute.py — the router's own default and its fail-closed switch check
# ---------------------------------------------------------------------------
def test_autoroute_default():
    ar_default = constant("dashboard/aux_autoroute.py", "AR_DEFAULT_MODE")
    check('AR_DEFAULT_MODE is "off" or "suggest" (never calls Claude on its own); was "auto"',
          ar_default in ("off", "suggest"), ar_default)

    # missing claude_escalation_enabled helper (bridge module not loaded) ->
    # must fail CLOSED, not open
    ns = dict()
    definitions("dashboard/aux_autoroute.py", ["_ar_escalation_on"], ns)
    check("missing master-switch helper -> router treats escalation as OFF (was: ON)",
          ns["_ar_escalation_on"]() is False)

    # helper present but raises -> must also fail closed
    ns2 = dict(claude_escalation_enabled=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    definitions("dashboard/aux_autoroute.py", ["_ar_escalation_on"], ns2)
    check("a raising master-switch helper -> router treats escalation as OFF (was: ON)",
          ns2["_ar_escalation_on"]() is False)

    # helper present and returns True -> router sees ON (sanity: not ALWAYS False)
    ns3 = dict(claude_escalation_enabled=lambda: True)
    definitions("dashboard/aux_autoroute.py", ["_ar_escalation_on"], ns3)
    check("a working, enabled master switch is still read through correctly",
          ns3["_ar_escalation_on"]() is True)


# ---------------------------------------------------------------------------
# aux_onboarding.py — the first-run sheet's own reading of the switch
# ---------------------------------------------------------------------------
def test_onboarding_default():
    # No claude_escalation_enabled in globals (bridge module absent/not yet
    # loaded) — _onb_prefs()'s own fallback must be False, not True.
    ns = dict()
    definitions("dashboard/aux_onboarding.py", ["_onb_g", "_onb_prefs"], ns)
    prefs = ns["_onb_prefs"]()
    check("onboarding's own default for an unavailable switch is False (was True)",
          prefs.get("claude_escalation") is False, prefs)

    # The real helper, explicitly False -> prefs reflects False (not the
    # unrelated fallback default masking a real answer).
    ns2 = dict(claude_escalation_enabled=lambda: False)
    definitions("dashboard/aux_onboarding.py", ["_onb_g", "_onb_prefs"], ns2)
    check("onboarding reflects an explicit False from the real helper",
          ns2["_onb_prefs"]()["claude_escalation"] is False)

    ns3 = dict(claude_escalation_enabled=lambda: True)
    definitions("dashboard/aux_onboarding.py", ["_onb_g", "_onb_prefs"], ns3)
    check("onboarding reflects an explicit True from the real helper",
          ns3["_onb_prefs"]()["claude_escalation"] is True)


# ---------------------------------------------------------------------------
# aux_onboarding.js — the sheet must not fail open on an undefined value
# ---------------------------------------------------------------------------
def test_onboarding_js_copy():
    src = (REPO / "dashboard" / "aux_onboarding.js").read_text()
    check("no remaining fail-open `claude_escalation !== false` pattern in the sheet",
          "claude_escalation !== false" not in src)
    hits = len(re.findall(r"claude_escalation\s*===\s*true", src))
    check("the sheet now gates the toggle on a strict `=== true` (found >= 4 sites)",
          hits >= 4, hits)
    check('the toggle copy says it is off by default',
          "off by default" in src.lower())


def main():
    print("A02: cloud escalation defaults on / fails open "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    test_bridge_default()
    test_autoroute_default()
    test_onboarding_default()
    test_onboarding_js_copy()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
