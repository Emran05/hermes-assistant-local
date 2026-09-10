"""Offline guard for the unit tier — proof, not a promise.

`run.sh unit` puts this directory on PYTHONPATH and sets HERMES_TESTS_OFFLINE=1.
Python imports `usercustomize` automatically at interpreter start (after
`sitecustomize`, which is why this file is NOT called that — Homebrew ships its
own sitecustomize.py and shadowing it breaks site-packages resolution), so every
unit test, and every python3 it spawns, runs with the dashboard's port, the
model server's port and the serve backend's port made unreachable.

A unit test that quietly depended on the owner's running dashboard therefore
FAILS here instead of passing on one machine and failing in CI.  Nothing is
patched unless the env var is set, so this file is inert everywhere else.
"""
import os

HERMES_OFFLINE_GUARD = False

if os.environ.get("HERMES_TESTS_OFFLINE") == "1":
    import errno
    import socket

    def _ports():
        raw = os.environ.get("HERMES_TESTS_BLOCKED_PORTS", "7788,8080,9119")
        out = set()
        for bit in raw.split(","):
            bit = bit.strip()
            if bit.isdigit():
                out.add(int(bit))
        return out

    _BLOCKED = _ports()
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex

    def _port_of(address):
        try:
            return int(address[1])
        except (TypeError, ValueError, IndexError):
            return None

    def connect(self, address):
        port = _port_of(address)
        if port in _BLOCKED:
            raise OSError(errno.ECONNREFUSED,
                          "HERMES_TESTS_OFFLINE: port %s is out of bounds for "
                          "the unit tier" % port)
        return _real_connect(self, address)

    def connect_ex(self, address):
        if _port_of(address) in _BLOCKED:
            return errno.ECONNREFUSED
        return _real_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    HERMES_OFFLINE_GUARD = True
