"""Is the unit tier's Python offline guard ACTIVE in a fresh interpreter?

run.sh runs this with exactly the environment the suites get (PYTHONPATH,
HERMES_TESTS_OFFLINE=1) and treats a non-zero exit as "the tier is unguarded".

It deliberately does NOT `import usercustomize` (C82): an explicit import runs
the guard module and so succeeds even when the interpreter skipped it at
start-up — a venv, PYTHONNOUSERSITE=1, `python3 -s`/`-I` — which is exactly the
case the check exists to catch.  Instead it asks whether start-up loaded it,
then proves it behaviourally against a listener it opened itself, so a broken
guard reaches that listener and never a service the owner is running.

Exit 0 = guarded.  Any other exit prints why on stderr.
"""
import os
import socket
import sys


def main():
    mod = sys.modules.get("usercustomize")
    if mod is None or not getattr(mod, "HERMES_OFFLINE_GUARD", False):
        print("usercustomize was not loaded at interpreter start-up "
              "(venv? PYTHONNOUSERSITE? python3 -s/-I?)", file=sys.stderr)
        return 2
    if not getattr(socket.socket.connect, "__hermes_offline_guard__", False):
        print("socket.socket.connect is not the guard's", file=sys.stderr)
        return 3

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    os.environ["HERMES_TESTS_BLOCKED_PORTS"] = str(port)
    try:
        c = socket.socket()
        c.settimeout(2)
        try:
            c.connect(("127.0.0.1", port))
        except OSError as e:
            if "HERMES_TESTS_OFFLINE" not in str(e):
                print("blocked port refused, but not by the guard: %s" % e,
                      file=sys.stderr)
                return 4
        else:
            print("a blocked loopback port was reachable", file=sys.stderr)
            return 5
        finally:
            c.close()
        try:
            socket.getaddrinfo("hermes-guard-selfcheck.invalid", 80)
        except socket.gaierror as e:
            if "HERMES_TESTS_OFFLINE" not in str(e):
                print("name lookup failed, but not by the guard: %s" % e,
                      file=sys.stderr)
                return 6
        else:
            print("a non-loopback name resolved", file=sys.stderr)
            return 7
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
