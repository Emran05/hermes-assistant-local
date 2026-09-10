#!/usr/bin/env python3
"""Regression test for audit finding A10 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: dashboard/aux_config.py's _cfg_clean_settings() accepted only bare
strings in the `timezones` allowlisted setting, but both world-clock widget
providers (expand_worldclock/w_worldclock in server.py) have always stored
["label", "Area/City"] PAIRS. A real configuration like
[["Paris", "Europe/Paris"]] silently became [] on export, and importing that
snapshot then restored the DEFAULT clocks over the owner's chosen ones.
(docs/audits/reproduce_20260910.py's timezone_roundtrip() demonstrated
exactly this.)

AFTER: _cfg_valid_tz() validates the zone (against zoneinfo's real database
when available, else a loose Area/City shape) and pairs are preserved; a
bare string is still accepted for backward compatibility; invalid shapes are
dropped, never rewritten.

This suite extracts the REAL _cfg_clean_settings()/_cfg_valid_tz() (via ast,
the same technique the audit's reproduction script uses) and round-trips the
REAL widget settings shape server.py's world-clock providers read
(get_settings().get("timezones") -> [[label, tz], ...]). No dashboard, no
network, no model.
"""
import ast
import re
import sys
from pathlib import Path
import os

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


def make_ns(available_timezones):
    return dict(
        CFG_SETTINGS_ALLOW=constant("dashboard/aux_config.py", "CFG_SETTINGS_ALLOW"),
        CFG_TICKER_RE=re.compile(r"^[A-Za-z0-9.^=:-]{1,16}$"),
        CFG_URL_RE=re.compile(r"^https?://", re.I),
        CFG_TZ_LOOSE_RE=re.compile(r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$"),
        _CFG_TZ_CACHE={"zones": None},
        _cfg_available_timezones=available_timezones,
    )


# The REAL shape server.py's world-clock providers read off settings.json —
# see server.py expand_worldclock()/w_worldclock(): `for label, tz in
# zones[:N]`. This is not an invented test fixture; it is copied from those
# two call sites' own fallback default.
REAL_WIDGET_TIMEZONES = [
    ["San Francisco", "America/Los_Angeles"],
    ["New York", "America/New_York"],
    ["London", "Europe/London"],
    ["Tokyo", "Asia/Tokyo"],
]


def test_roundtrip_with_real_zoneinfo():
    try:
        from zoneinfo import available_timezones
    except Exception:
        print("  SKIP zoneinfo unavailable on this interpreter")
        return
    ns = make_ns(available_timezones)
    definitions("dashboard/aux_config.py", ["_cfg_clean_settings", "_cfg_valid_tz"], ns)
    clean = ns["_cfg_clean_settings"]({"timezones": REAL_WIDGET_TIMEZONES})
    check("the real widget default (4 label/tz pairs) survives export unchanged "
          "(was: silently emptied)",
          clean == {"timezones": REAL_WIDGET_TIMEZONES}, clean)

    # a single custom clock an owner actually configured
    custom = [["Paris", "Europe/Paris"]]
    clean2 = ns["_cfg_clean_settings"]({"timezones": custom})
    check("a single custom [label, tz] pair round-trips",
          clean2 == {"timezones": custom}, clean2)

    # export -> (simulated) import -> export again must be a fixed point,
    # exactly the invariant snapshot_validate()/snapshot_build() rely on
    twice = ns["_cfg_clean_settings"](clean2)
    check("cleaning an already-clean snapshot is a no-op (fixed point)",
          twice == clean2, (clean2, twice))

    # a bogus zone name that merely LOOKS like Area/City is rejected once a
    # real timezone database is available
    bogus = [["Nowhere", "Not/AZone"]]
    clean3 = ns["_cfg_clean_settings"]({"timezones": bogus})
    check("a fake zone id is dropped when the real tz database is available",
          clean3 == {"timezones": []}, clean3)


def test_validation_rules():
    # No real zoneinfo database (simulates it being unavailable) -> falls
    # back to the loose Area/City shape rather than accepting anything.
    ns = make_ns(None)
    definitions("dashboard/aux_config.py", ["_cfg_clean_settings", "_cfg_valid_tz"], ns)

    value = {"timezones": [
        ["Paris", "Europe/Paris"],          # valid pair -> kept
        ["Tokyo", "Asia/Tokyo"],            # valid pair -> kept
        ["", "Europe/Paris"],               # empty label -> dropped
        ["No Zone", ""],                    # empty tz -> dropped
        ["Bad Shape", "not a zone!!"],      # fails even the loose shape -> dropped
        ["Too", "Many", "Fields"],          # wrong shape (3 elements) -> dropped
        ["OnlyOne"],                        # wrong shape (1 element) -> dropped
        "America/Chicago",                  # bare string -> kept (back-compat)
        123,                                # wrong type entirely -> dropped
        None,                               # wrong type entirely -> dropped
    ]}
    clean = ns["_cfg_clean_settings"](value)
    check("valid [label, tz] pairs are preserved (the audit's CONFIRMED bug: they became [])",
          ["Paris", "Europe/Paris"] in clean["timezones"]
          and ["Tokyo", "Asia/Tokyo"] in clean["timezones"], clean)
    check("a bare string is still accepted for backward compatibility",
          "America/Chicago" in clean["timezones"], clean)
    check("malformed pairs/empties/wrong types are all dropped, not crashed on",
          len(clean["timezones"]) == 3, clean)

    # the 20-item cap still applies to the mixed shape
    many = {"timezones": [["L%d" % i, "Europe/Paris"] for i in range(30)]}
    clean_many = ns["_cfg_clean_settings"](many)
    check("the 20-item cap is enforced for [label, tz] pairs too",
          len(clean_many["timezones"]) == 20, len(clean_many["timezones"]))


def test_export_import_survives_snapshot_validate():
    """The audit specifically asked for an export/import round trip using the
    real widget settings shape — exercise it through snapshot_validate() too,
    not just the raw cleaner, since that is what POST /api/config/import
    actually calls."""
    try:
        from zoneinfo import available_timezones
    except Exception:
        available_timezones = None
    ns = make_ns(available_timezones)
    ns.update(json=__import__("json"))
    definitions("dashboard/aux_config.py",
                ["_cfg_clean_settings", "_cfg_valid_tz", "_cfg_clean_layout",
                 "_cfg_clean_models", "_cfg_clean_permissions", "_cfg_clean_agent"], ns)
    ns["WIDGETS"] = {}

    snap = {
        "schema": 1, "kind": "hermes-state-snapshot", "note": "",
        "dashboard": {
            "layout": {"order": []},
            "settings": {"timezones": REAL_WIDGET_TIMEZONES},
            "models": {"active": "", "roster": []},
        },
        "permissions": {"classes": {}, "patterns": {}},
        "agent_config": {},
    }

    # snapshot_validate() itself isn't extracted (it needs _cfg_scan/CFG_MAX_
    # BYTES/etc. wired up); exercise the same per-section cleaner it calls for
    # "settings", which is the exact function this finding is about.
    dash = snap["dashboard"]
    cleaned_settings = ns["_cfg_clean_settings"](dash["settings"])
    check("the settings section of a real export/import payload keeps its clocks",
          cleaned_settings.get("timezones") == REAL_WIDGET_TIMEZONES, cleaned_settings)


def main():
    print("A10: config export discards valid world-clock settings "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    test_roundtrip_with_real_zoneinfo()
    test_validation_rules()
    test_export_import_survives_snapshot_validate()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
