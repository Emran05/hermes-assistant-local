#!/usr/bin/env python3
"""C119 (+ the config half of C66) — the repo's seed config.yaml, which
install.sh copies onto a fresh Mac, must use keys Hermes 0.18 actually reads
and must not seed what the posture forbids.

The old seed set model.name / model.context_window / model.api_key_env,
agent.approval.mode, auxiliary.command_approval and cron.jobs — none of which
0.18 reads, so a fresh install got NO model.default, NO pinned approvals.mode
and no scheduled job — and it seeded an enabled `npx -y @google/mcp-workspace`
MCP server asking for gmail.compose (a scope that can send) on every start.
It also lacked the `custom_providers: bg` entry the dashboard's background
lane runs `--provider custom:bg` against.

Parsed with doctor.py's own stdlib YAML reader and judged by its own drift
rules; the model ids are cross-checked against the launch scripts' defaults.
Offline, no Hermes import, nothing outside a temp HOME.
"""
import importlib.util, io, os, re, shutil, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def at(tree, *path):
    cur = tree
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def script_default(name, var):
    src = io.open(os.path.join(REPO, name), encoding="utf-8").read()
    m = re.search(r'^%s="([^"]+)"' % var, src, re.M)
    return m.group(1) if m else None


def main(tmp):
    os.environ["HOME"] = os.path.join(tmp, "home")
    # doctor.py is the CURRENT one (it carries the parser); only the seed is
    # the file under test, so HERMES_REPO swaps just config.yaml.
    here_repo = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
    spec = importlib.util.spec_from_file_location("doctor_c119", os.path.join(here_repo, "dashboard", "doctor.py"))
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    raw = io.open(os.path.join(REPO, "config.yaml"), encoding="utf-8").read()
    cfg, probs = d._hcfg_parse(raw)

    print("\n1. it parses, and only with keys 0.18 reads")
    check("no unreadable lines", probs == [], probs)
    for path in (("model", "name"), ("model", "context_window"), ("model", "api_key_env"),
                 ("agent", "timezone"), ("agent", "approval"), ("auxiliary", "command_approval"),
                 ("cron", "jobs")):
        check("no %s (0.18 does not read it)" % ".".join(path), at(cfg, *path) is None, at(cfg, *path))
    check("model.default is set (the key 0.18 reads)", isinstance(at(cfg, "model", "default"), str)
          and at(cfg, "model", "default"), at(cfg, "model"))
    check("model.provider custom on loopback :8080", at(cfg, "model", "provider") == "custom"
          and at(cfg, "model", "base_url") == "http://127.0.0.1:8080/v1", at(cfg, "model"))
    ctx = at(cfg, "model", "context_length")
    check("model.context_length >= 64k (Hermes' MINIMUM_CONTEXT_LENGTH)", isinstance(ctx, int) and ctx >= 64000, ctx)
    check("model.default is the model mlx-server.sh loads by default",
          at(cfg, "model", "default") == script_default("mlx-server.sh", "DEFAULT_MODEL"),
          (at(cfg, "model", "default"), script_default("mlx-server.sh", "DEFAULT_MODEL")))

    print("\n2. the posture is pinned, not left to upstream defaults")
    check("approvals.mode manual", at(cfg, "approvals", "mode") == "manual", at(cfg, "approvals"))
    check("approvals.cron_mode deny", at(cfg, "approvals", "cron_mode") == "deny", at(cfg, "approvals"))
    check("tts.provider is a local one", at(cfg, "tts", "provider") in d._TTS_LOCAL, at(cfg, "tts"))
    check("no MCP server is seeded", not at(cfg, "mcp_servers"), at(cfg, "mcp_servers"))
    live = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    for word in ("gmail.compose", "gmail.send", "gmail.modify", "@google/mcp-workspace", "npx"):
        check("no %s outside comments" % word, word not in live)
    check("no api_key or key placeholder", "api_key" not in live and "local-anything" not in live)
    found = d._drift_findings(cfg, plugins_dir=os.path.join(tmp, "plugins"))
    bad = [(s, r) for s, r, _, _ in found if s != "pass" and r != "computer_use"]
    check("doctor's drift rules pass it (computer_use is the owner's call)", bad == [], bad)
    check("and nothing FAILs", not any(s == "fail" for s, _, _, _ in found), found)

    print("\n3. the background lane's provider is there (C66)")
    cps = at(cfg, "custom_providers") or []
    bg = [c for c in cps if isinstance(c, dict) and c.get("name") == "bg"]
    check("custom_providers has a 'bg' entry", len(bg) == 1, cps)
    if bg:
        src = io.open(os.path.join(REPO, "mlx-server-bg.sh"), encoding="utf-8").read()
        port = re.search(r'^PORT="\$\{BG_PORT:-(\d+)\}"', src, re.M)
        check("mlx-server-bg.sh's default port is readable", port is not None)
        port = port.group(1) if port else "?"
        check("it points at mlx-server-bg.sh's port on loopback",
              bg[0].get("base_url") == "http://127.0.0.1:%s/v1" % port, (bg[0].get("base_url"), port))
        check("and names the model mlx-server-bg.sh loads by default",
              bg[0].get("model") == script_default("mlx-server-bg.sh", "DEFAULT_BG_MODEL"),
              (bg[0].get("model"), script_default("mlx-server-bg.sh", "DEFAULT_BG_MODEL")))
        check("context_length >= 64k", isinstance(bg[0].get("context_length"), int)
              and bg[0]["context_length"] >= 64000, bg[0])


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="t-c119-")
    try:
        main(tmp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
