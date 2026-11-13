#!/usr/bin/env python3
"""Regression test for C110 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: write_json() preserved an existing target's mode, so legacy 0644
personal stores (32 chats, settings.json, tasks, briefing on this Mac) stayed
0644 forever; server.py created ~/.hermes/dashboard, chats/ and inbox/ with
the umask's 0755; and nothing checked that ~/.hermes itself is 0700, the one
thing keeping those files from other accounts.

AFTER:
  * write_json() keeps only the OWNER bits of an existing target: 0644/0640
    become 0600 on the next write, an owner's deliberate 0400 stays 0400, a
    new file is 0600;
  * a store moved aside as .corrupt-<ts> (C27) is owner-only too;
  * server.py creates DATA / CHATS / INBOX 0700 when they do not exist;
  * doctor's config check WARNs when ~/.hermes is group/other-accessible.

Runs the REAL code (ast-extracted from dashboard/server.py and doctor.py);
everything happens in a temp dir.
"""
import ast
import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def mode(p):
    return os.stat(p).st_mode & 0o777


def makedirs_loop():
    """The module-level `for d in (DATA, CHATS, INBOX): os.makedirs(...)`."""
    tree = ast.parse(H.SRC.read_text())
    for node in tree.body:
        if (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)
                and [getattr(e, "id", None) for e in node.iter.elts]
                == ["DATA", "CHATS", "INBOX"]):
            return compile(ast.Module(body=[node], type_ignores=[]),
                           str(H.SRC), "exec")
    raise AssertionError("DATA/CHATS/INBOX makedirs loop not found")


def main():
    old_umask = os.umask(0o022)          # the umask launchd/Terminal give it
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-c110-") as d:
            ns = dict(os=os, json=json, time=time, threading=threading, sys=sys)
            H.extract(["write_json"], ns,
                      optional=["_json_unparsable", "_quarantine_corrupt"])
            wj = ns["write_json"]

            p = os.path.join(d, "chat-legacy.json")
            with open(p, "w") as f:
                f.write("{}")
            os.chmod(p, 0o644)
            wj(p, {"messages": ["private"]})
            check("a legacy 0644 store is narrowed to 0600 on its next write",
                  mode(p) == 0o600, oct(mode(p)))

            os.chmod(p, 0o640)
            wj(p, {"n": 2})
            check("0640 -> 0600 (group bits never survive)",
                  mode(p) == 0o600, oct(mode(p)))

            os.chmod(p, 0o400)
            wj(p, {"n": 3})
            check("an owner's deliberate 0400 is respected",
                  mode(p) == 0o400 and json.load(open(p)) == {"n": 3},
                  oct(mode(p)))
            os.chmod(p, 0o600)

            fresh = os.path.join(d, "fresh.json")
            wj(fresh, {})
            check("a brand-new store is 0600", mode(fresh) == 0o600,
                  oct(mode(fresh)))

            bad = os.path.join(d, "tasks.json")
            with open(bad, "w") as f:
                f.write("{oops")
            os.chmod(bad, 0o644)
            with contextlib.redirect_stderr(io.StringIO()):
                wj(bad, {"tasks": []})
            copies = glob.glob(bad + ".corrupt-*")
            if "_quarantine_corrupt" in ns:
                check("a store moved aside as .corrupt is owner-only too",
                      len(copies) == 1 and mode(copies[0]) == 0o600,
                      [(c, oct(mode(c))) for c in copies])
            check("the rewritten store is 0600", mode(bad) == 0o600,
                  oct(mode(bad)))

        # -- the directories server.py creates -------------------------------
        with tempfile.TemporaryDirectory(prefix="hermes-c110-") as d:
            data = os.path.join(d, "dashboard")
            dns = dict(os=os, DATA=data, CHATS=os.path.join(data, "chats"),
                       INBOX=os.path.join(data, "inbox"))
            exec(makedirs_loop(), dns)
            got = {n: oct(mode(dns[n])) for n in ("DATA", "CHATS", "INBOX")}
            check("DATA, chats/ and inbox/ are created 0700 (was: 0755)",
                  all(v == "0o700" for v in got.values()), got)
            os.chmod(data, 0o755)
            exec(makedirs_loop(), dns)
            check("...and an existing directory is left alone (no surprise "
                  "chmod at import)", mode(data) == 0o755, oct(mode(data)))
    finally:
        os.umask(old_umask)

    # -- doctor checks the ~/.hermes parent -----------------------------------
    with tempfile.TemporaryDirectory(prefix="hermes-c110-doc-") as top:
        home = os.path.join(top, ".hermes")
        data = os.path.join(home, "dashboard")
        os.makedirs(data)
        cfg = os.path.join(data, "config.yaml")
        open(cfg, "w").write("model: {}\n")
        open(os.path.join(data, "settings.json"), "w").write("{}")
        dns = dict(os=os, json=json, _glob=glob, DATA=data,
                   SETTINGS_FILE=os.path.join(data, "settings.json"),
                   REPO_CONFIG=cfg, HERMES_CONFIG=cfg,
                   PASS="pass", WARN="warn", FAIL="fail",
                   _yaml_sane=lambda p: (True, ""),
                   check=lambda *a, **k: (lambda f: f))
        H.extract(["_json_state", "_chk_config"], dns,
                  src=H.REPO / "dashboard" / "doctor.py")
        os.chmod(home, 0o700)
        r0 = dns["_chk_config"]()
        check("doctor: ~/.hermes 0700 -> config passes", r0["status"] == "pass", r0)
        os.chmod(home, 0o755)
        r1 = dns["_chk_config"]()
        check("doctor: ~/.hermes 0755 -> WARN with `chmod 700 ~/.hermes`",
              r1["status"] == "warn" and "755" in r1["detail"]
              and r1.get("fix") == "chmod 700 ~/.hermes", r1)
        os.chmod(home, 0o700)

    H.finish()


if __name__ == "__main__":
    main()
