#!/usr/bin/env python3
"""C133.2 — the Telegram text never goes in argv.

BEFORE: _wt_send_telegram ran `hermes send --to telegram --json <text>`, so
the brief (calendar, tasks, For-You people) and every alert sat in the
process's argv, where any local user can read it with ps.
AFTER: `hermes send --to telegram --json --file -` with the text on stdin
(hermes_cli/send_cmd.py reads `--file -` from stdin).

Part 1 drives the real _wt_send_telegram (see _wt_harness.py) with
subprocess.run recorded. Part 2 runs it against a stub `hermes` executable
that records its argv and stdin, so the real subprocess plumbing (encoding,
non-ASCII text) is exercised too. Nothing leaves the machine.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
SECRET = "Dentist with Dr. Okafor at 3:30 PM — call Priya about the term sheet"
STUB = r'''#!/usr/bin/env python3
import json, os, sys
data = sys.stdin.read()
with open(os.environ["STUB_LOG"], "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "stdin": data}, f)
print(json.dumps({"success": True}))
'''


class P:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


def main():
    print("C133.2: the alert text goes on stdin, never in argv")
    g, home, _data = H.load()

    seen = []
    orig = subprocess.run

    def fake(argv, *a, **k):
        seen.append((list(argv), k))
        return P('{"success": true}')
    subprocess.run = fake
    try:
        ok, det = g["_wt_send_telegram"]("Your day\n" + SECRET)
    finally:
        subprocess.run = orig
    argv, kw = seen[-1] if seen else ([], {})
    check("the send succeeds", ok is True and det == "", (ok, det))
    check("THE FIX: the text is nowhere in argv",
          argv and not any(SECRET in str(a) or "Your day" in str(a) for a in argv), argv)
    check("argv is `hermes send --to telegram --json --file -`",
          argv[1:] == ["send", "--to", "telegram", "--json", "--file", "-"], argv)
    check("the text is handed over on stdin",
          isinstance(kw.get("input"), str) and SECRET in kw["input"], kw.get("input"))
    check("still no chat_id anywhere (home channel only)",
          not any("chat" in str(a).lower() for a in argv), argv)
    check("the 60 s timeout is kept", kw.get("timeout") == g["SEND_TIMEOUT"], kw.get("timeout"))

    # ---- against a real child process ------------------------------------
    bindir = tempfile.mkdtemp(prefix="c133-bin-")
    stub = os.path.join(bindir, "hermes")
    with open(stub, "w") as f:
        f.write("#!%s\n" % sys.executable + STUB.split("\n", 1)[1])
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
    log = os.path.join(bindir, "log.json")
    g["HERMES"] = stub
    g["_hermes_env"] = lambda: dict(os.environ, STUB_LOG=log)
    text = "Morning brief\n• café 22° — " + SECRET
    ok, det = g["_wt_send_telegram"](text)
    rec = json.load(open(log, encoding="utf-8")) if os.path.exists(log) else {}
    check("a real child: the send reports success", ok is True, (ok, det))
    check("a real child: argv carries no message text",
          rec.get("argv") == ["send", "--to", "telegram", "--json", "--file", "-"],
          rec.get("argv"))
    check("a real child: stdin carries the exact text (non-ASCII intact)",
          rec.get("stdin") == g["_strip_emoji"](text), rec.get("stdin"))
    H.finish()


if __name__ == "__main__":
    main()
