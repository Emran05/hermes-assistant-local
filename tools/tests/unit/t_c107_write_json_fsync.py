#!/usr/bin/env python3
"""Regression test for C107 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: write_json() wrote the temp file and os.replace()d it over the target
with no fsync — every sibling store writer (aux_messages, aux_memory,
aux_needsyou, aux_google, aux_watchtower, permissions.py) syncs first. After a
crash or power loss the rename could be on disk while the data was not, i.e.
settings.json / a chat / layout.json replaced by an empty or partial file.

AFTER: the temp file's data is flushed and fsync()ed BEFORE the rename, on
the temp file's own descriptor, with the complete document in it.

Runs the REAL write_json (ast-extracted from dashboard/server.py) with an `os`
proxy that records fsync/replace order; everything else is the real os.
"""
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


class OsSpy:
    """The real os module, with fsync/replace recorded."""

    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        return getattr(os, name)

    def fsync(self, fd):
        # what is on disk for this descriptor at the moment it is synced
        size = os.fstat(fd).st_size
        self.events.append(("fsync", fd, size))
        return os.fsync(fd)

    def replace(self, src, dst):
        self.events.append(("replace", os.path.basename(src), os.path.getsize(src)))
        return os.replace(src, dst)


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c107-") as d:
        spy = OsSpy()
        ns = dict(os=spy, json=json, time=time, threading=threading, sys=sys)
        H.extract(["write_json"], ns,
                  optional=["_json_unparsable", "_quarantine_corrupt"])
        path = os.path.join(d, "settings.json")
        doc = {"tickers": ["NVDA"] * 50, "note": "x" * 4000}
        want = len(json.dumps(doc, indent=1))

        ns["write_json"](path, doc)          # brand-new target
        ns["write_json"](path, doc)          # existing target
        check("the result is still the document", json.load(open(path)) == doc)

        kinds = [e[0] for e in spy.events]
        check("write_json fsyncs (was: never)", "fsync" in kinds, spy.events)
        # each write: one fsync, then its replace
        pairs = list(zip(kinds[0::2], kinds[1::2]))
        check("every write syncs BEFORE it renames",
              kinds.count("fsync") == 2 and pairs == [("fsync", "replace")] * 2,
              spy.events)
        syncs = [e for e in spy.events if e[0] == "fsync"]
        check("the synced descriptor already holds the whole document",
              bool(syncs) and all(e[2] == want for e in syncs),
              (want, spy.events))
        reps = [e for e in spy.events if e[0] == "replace"]
        check("what is renamed is the temp file that was synced",
              bool(reps) and all(".tmp." in e[1] and e[2] == want for e in reps),
              spy.events)

    H.finish()


if __name__ == "__main__":
    main()
