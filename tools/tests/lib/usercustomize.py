"""Offline guard for the unit tier — proof, not a promise.

`run.sh unit` puts this directory on PYTHONPATH and sets HERMES_TESTS_OFFLINE=1.
Python imports `usercustomize` automatically at interpreter start (after
`sitecustomize`, which is why this file is NOT called that — Homebrew ships its
own sitecustomize.py and shadowing it breaks site-packages resolution), so every
unit test, and every python3 it spawns, runs with:

  * every NON-LOOPBACK destination refused — TCP connect, UDP sendto and name
    resolution alike (C81: the tier used to block only three local ports, so a
    suite that fetched a real feed, or an aux loop started at import, quietly
    went out to the internet and passed or failed on the weather);
  * the dashboard's port (7788), both model lanes (8080, 8081) and the serve
    backend (9119) refused even on loopback.

Loopback on any other port stays open: several suites start a throwaway
`http.server` on port 0 and talk to it, which is the whole point of a hermetic
test.  A refusal is an OSError with errno ECONNREFUSED/ENETUNREACH (or a
socket.gaierror for a name), so code that handles "the network is down" sees
exactly that — and a suite that quietly needed the internet fails here instead
of passing on one machine and failing on another.

`HERMES_TESTS_EGRESS_LOG=<path>` appends one line per refusal (debugging aid:
refusals inside a background thread are usually swallowed).

Nothing is patched unless the env var is set, so this file is inert everywhere
else.  `run.sh` verifies the guard is ACTIVE in a fresh interpreter rather than
importing this file itself (C82: an explicit `import usercustomize` succeeds
even when the interpreter skipped it at start-up — a venv, PYTHONNOUSERSITE=1,
`python3 -s`/`-I` — and so proved nothing).
"""
import os

HERMES_OFFLINE_GUARD = False

if os.environ.get("HERMES_TESTS_OFFLINE") == "1":
    import errno
    import ipaddress
    import socket

    DEFAULT_BLOCKED_PORTS = "7788,8080,8081,9119"

    def _ports():
        raw = os.environ.get("HERMES_TESTS_BLOCKED_PORTS", DEFAULT_BLOCKED_PORTS)
        out = set()
        for bit in raw.split(","):
            bit = bit.strip()
            if bit.isdigit():
                out.add(int(bit))
        return out

    _LOCAL_NAMES = {"localhost", "localhost.", "ip6-localhost", "ip6-loopback"}
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex
    _real_sendto = socket.socket.sendto
    _real_getaddrinfo = socket.getaddrinfo

    def _log(what):
        path = os.environ.get("HERMES_TESTS_EGRESS_LOG")
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s pid=%d %s\n" % (
                    os.environ.get("HERMES_TESTS_SUITE", "-"), os.getpid(), what))
        except OSError:
            pass

    def _is_loopback(host):
        if host is None:
            return True
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        h = str(host).strip().lower()
        if h in ("",) or h in _LOCAL_NAMES:
            return True
        if h.startswith("[") and h.endswith("]"):
            h = h[1:-1]
        h = h.split("%", 1)[0]          # fe80::1%en0 style scope ids
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            return False                 # any other NAME is the internet
        if ip.is_loopback:
            return True
        mapped = getattr(ip, "ipv4_mapped", None)
        return bool(mapped is not None and mapped.is_loopback)

    def _verdict(address):
        """None when allowed, else (errno, message)."""
        if not isinstance(address, tuple) or len(address) < 2:
            return None                  # AF_UNIX path, or not an inet address
        host, port = address[0], address[1]
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = None
        if not _is_loopback(host):
            return (errno.ENETUNREACH,
                    "HERMES_TESTS_OFFLINE: egress to %s:%s is out of bounds for "
                    "the unit tier (non-loopback)" % (host, port))
        if port in _ports():             # read per call: cheap, and lets a
                                         # self-check block its OWN listener
            return (errno.ECONNREFUSED,
                    "HERMES_TESTS_OFFLINE: port %s is out of bounds for the "
                    "unit tier" % port)
        return None

    def connect(self, address):
        v = _verdict(address)
        if v:
            _log("connect %r" % (address,))
            raise OSError(v[0], v[1])
        return _real_connect(self, address)

    def connect_ex(self, address):
        v = _verdict(address)
        if v:
            _log("connect_ex %r" % (address,))
            return v[0]
        return _real_connect_ex(self, address)

    def sendto(self, data, *args):
        address = args[-1] if args else None
        v = _verdict(address)
        if v:
            _log("sendto %r" % (address,))
            raise OSError(v[0], v[1])
        return _real_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            _log("getaddrinfo %r" % (host,))
            raise socket.gaierror(
                socket.EAI_NONAME,
                "HERMES_TESTS_OFFLINE: name resolution of %r is out of bounds "
                "for the unit tier (non-loopback)" % (host,))
        return _real_getaddrinfo(host, *args, **kwargs)

    def _name_guard(real):
        def resolve(host, *args, **kwargs):
            if not _is_loopback(host):
                _log("%s %r" % (real.__name__, host))
                raise socket.gaierror(
                    socket.EAI_NONAME,
                    "HERMES_TESTS_OFFLINE: name resolution of %r is out of "
                    "bounds for the unit tier (non-loopback)" % (host,))
            return real(host, *args, **kwargs)
        return resolve

    connect.__hermes_offline_guard__ = True
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = _name_guard(socket.gethostbyname)
    socket.gethostbyname_ex = _name_guard(socket.gethostbyname_ex)
    HERMES_OFFLINE_GUARD = True

    # ---- the owner's own data, through a system tool (review R4-seams) ----
    # "No unit suite reads the owner's data" has to hold for what a suite
    # STARTS as well as for the files it opens: three suites reached the real
    # /usr/bin/shortcuts (the owner's Shortcuts list) through access_preamble.
    # The installed `shortcuts`, `osascript` (Reminders, Calendar, Notes) and
    # `icalBuddy` (the calendar) are refused here, as an OSError the code under
    # test already handles ("the tool failed"). A stub of the same name that a
    # suite put in a scratch folder is not an installed tool and runs normally.
    import shutil as _shutil
    import sys as _sys

    _OWNER_TOOLS = ("shortcuts", "osascript", "icalBuddy")
    _INSTALLED = ("/usr/", "/bin/", "/sbin/", "/opt/", "/System/", "/Applications/",
                  "/Library/")

    def _owner_tool(first):
        if isinstance(first, bytes):
            first = first.decode("utf-8", "replace")
        if not isinstance(first, str) or os.path.basename(first) not in _OWNER_TOOLS:
            return None
        path = first if os.sep in first else (_shutil.which(first) or "")
        path = os.path.abspath(path) if path else ""
        return path if path.startswith(_INSTALLED) else None

    def _owner_tool_hook(event, args):
        if event == "subprocess.Popen":
            exe, argv = args[0], args[1]
            first = exe or (argv[0] if isinstance(argv, (list, tuple)) and argv else argv)
        elif event in ("os.posix_spawn", "os.exec"):
            first = args[0]
        else:
            return
        if isinstance(first, (list, tuple)):
            first = first[0] if first else ""
        try:
            first = os.fspath(first)
        except TypeError:
            return
        if isinstance(first, str) and " " in first and os.sep not in first.split(" ", 1)[0]:
            first = first.split(" ", 1)[0]          # shell=True: "osascript -e ..."
        path = _owner_tool(first)
        if path:
            _log("owner-tool %s" % path)
            raise PermissionError(
                errno.EPERM, "HERMES_TESTS_OFFLINE: %s reads the owner's own data and is "
                "out of bounds for the unit tier (use a stub)" % path)

    _sys.addaudithook(_owner_tool_hook)
    HERMES_OWNER_TOOL_GUARD = True
