#!/usr/bin/env python3
"""Round-2 regression test for X-model-lifecycle-4 (b) (cross-cluster review of
the merged 2026-10-21 fixes): the dashboard and mlx-server.sh disagreed about
which model runs when there is no active-model file.

BEFORE: mlx-server.sh loads `active-model` else its hardcoded DEFAULT_MODEL,
but server.py's active_model() fell back to config.yaml's model.default. On a
never-switched install with model.default = the 9B, the server ran the 27B
while the menu named the 9B "active" and every direct call (briefing,
watchtower, For-You via active_model()) asked the pinned 27B server for the
9B id and was refused.

AFTER: active_model() is mlx-server.sh's rule exactly — the file, else
DEFAULT_MODEL — and the two DEFAULT_MODEL literals are pinned equal here so
they cannot drift apart again. (A Hermes model.default that differs is drift
for doctor's hermes_drift check — a doctor.py change, outside this file.)

REAL active_model, ast-extracted from dashboard/server.py; a temp HOME with a
config.yaml. HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t_r2_6_lifecycle_races as L  # noqa: E402  (shared loader)

check = L.check


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x4-") as home:
        os.makedirs(os.path.join(home, ".hermes", "dashboard"))
        with open(os.path.join(home, ".hermes", "config.yaml"), "w") as f:
            f.write("model:\n  default: mlx-community/Qwen3.5-9B-4bit\n"
                    "  context_length: 65536\n")
        active = os.path.join(home, ".hermes", "dashboard", "active-model")
        ns = {"os": os, "re": re, "HOME": home, "ACTIVE_MODEL_FILE": active}
        L.load(["active_model", "_config_model_default", "DEFAULT_MODEL"], ns)
        sh = open(os.path.join(L.REPO, "mlx-server.sh"), encoding="utf-8").read()
        m = re.search(r'^DEFAULT_MODEL="([^"]+)"', sh, re.M)
        shell_default = m.group(1) if m else None
        check("mlx-server.sh and server.py name the same DEFAULT_MODEL",
              shell_default and shell_default == ns["DEFAULT_MODEL"],
              (shell_default, ns["DEFAULT_MODEL"]))
        got = ns["active_model"]()
        check("THE FIX: no active-model file -> the model mlx-server.sh loads "
              "(DEFAULT_MODEL), not config.yaml's model.default",
              got == shell_default, got)
        with open(active, "w") as f:
            f.write("mlx-community/Qwen3.5-9B-4bit\n")
        check("an active-model file still wins (switch_model writes it)",
              ns["active_model"]() == "mlx-community/Qwen3.5-9B-4bit", ns["active_model"]())
        with open(active, "w") as f:
            f.write("  \n")
        check("a blank active-model file falls back to DEFAULT_MODEL",
              ns["active_model"]() == shell_default, ns["active_model"]())
    print("\nTESTS %d passed %d failed" % (L.PASSES[0], len(L.FAILS)))
    sys.exit(1 if L.FAILS else 0)


if __name__ == "__main__":
    main()
