#!/usr/bin/env python3
"""Round-2 regression test for R2-2 (2026-10-08 audit P3 prose, HTTP /
persistence): three robustness gaps in the chat store.

BEFORE:
  * read_json() caught OSError and JSONDecodeError only, so a store that is not
    UTF-8 raised UnicodeDecodeError out of EVERY reader (load_chat, the
    sidebar, /api/history ...).
  * _save_chat_locked() treated ANY OSError on the read of the existing file
    (EMFILE under fd pressure, EACCES) as corruption and moved a HEALTHY chat
    aside as .corrupt-*; save_chat_update() read it through read_json(), so the
    turn was appended to an EMPTY conversation and that one message became the
    whole chat.
  * list_sessions() indexed chat["messages"][0].get(...) with no shape checks,
    so one valid-JSON chat of the wrong shape failed the whole sidebar.

AFTER: read_json() catches ValueError (UnicodeDecodeError is one); only a JSON
or Unicode decode error (or valid JSON that is not an object) is moved aside,
any other OSError raises so the job reports persisted:false; list_sessions()
skips a malformed file with one rate-limited [sessions] stderr line.

REAL functions, ast-extracted from dashboard/server.py into a throwaway
namespace; chats live in a temp dir. `open` is shadowed in that namespace to
raise EMFILE for one path (the functions resolve `open` through their globals).
HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import builtins
import errno
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def namespace(chats):
    ns = dict(os=os, json=json, re=re, time=time, threading=threading, sys=sys,
              _state_lock=threading.Lock(), CHATS=chats,
              PREWARM_SESSION="__prewarm__")
    H.extract(["read_json", "write_json", "chat_path", "load_chat",
               "_new_message_id", "stamp_message_ids", "_save_chat_locked",
               "save_chat", "save_chat_update", "list_sessions",
               "DRAFT_SESSION", "HIDDEN_SESSIONS", "SESSIONS_LIST_CAP"], ns,
              optional=["_json_unparsable", "_quarantine_corrupt",
                        "session_hidden", "session_key",          # E69
                        "_BAD_CHAT_LOG_GAP", "_bad_chat_logged",
                        "_log_bad_chat"])
    return ns


def write(chats, sid, obj=None, raw=None):
    with open(os.path.join(chats, sid + ".json"), "wb") as f:
        f.write(raw if raw is not None else json.dumps(obj).encode())


def corrupt_copies(chats, sid):
    return [f for f in os.listdir(chats) if f.startswith(sid + ".json.corrupt")]


def emfile_open(target):
    """An `open` that fails like fd exhaustion for exactly one path."""
    def _open(path, *a, **kw):
        if os.path.abspath(str(path)) == os.path.abspath(target):
            raise OSError(errno.EMFILE, "Too many open files", str(path))
        return builtins.open(path, *a, **kw)
    return _open


def chat2():
    return {"title": "Trip", "rev": 3, "messages": [
        {"role": "user", "text": "plan the trip", "id": "a1"},
        {"role": "bot", "text": "here is a plan", "id": "a2"}]}


def test_read_json_non_utf8():
    print("\n1. read_json(): a store that is not UTF-8 reads as the default")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-2-") as d:
        ns = namespace(d)
        p = os.path.join(d, "latin.json")
        with open(p, "wb") as f:
            f.write(b'{"title": "caf\xe9"}')       # Latin-1, not UTF-8
        try:
            got, err = ns["read_json"](p, {"dflt": 1}), None
        except Exception as e:                    # the OLD code raised here
            got, err = None, e
        check("THE FIX: a non-UTF-8 file returns the default instead of raising",
              err is None and got == {"dflt": 1}, repr(err))
        write(d, "latin", raw=b'{"messages": [{"role": "user", "text": "\xff\xfe"}]}')
        try:
            got, err = ns["load_chat"]("latin"), None
        except Exception as e:
            got, err = None, e
        check("load_chat() of a non-UTF-8 chat is an empty conversation",
              err is None and got == {"messages": [], "title": ""}, repr(err))


def test_save_on_unreadable():
    print("\n2. saving onto a chat whose read fails with EMFILE / EACCES")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-2-") as d:
        ns = namespace(d)
        write(d, "c1", chat2())
        before = open(os.path.join(d, "c1.json"), "rb").read()
        ns["open"] = emfile_open(os.path.join(d, "c1.json"))
        calls = []

        def mutate(chat):
            calls.append(len(chat.get("messages") or []))
            chat["messages"].append({"role": "bot", "text": "the reply"})

        err = None
        with redirect_stderr(io.StringIO()):
            try:
                ns["save_chat_update"]("c1", mutate)
            except OSError as e:
                err = e
        del ns["open"]
        check("THE FIX: save_chat_update() raises (the job reports persisted:false)",
              isinstance(err, OSError) and err.errno == errno.EMFILE, repr(err))
        check("...without ever handing mutate_fn an empty conversation",
              calls == [], calls)
        check("the healthy chat was NOT moved aside as .corrupt-*",
              corrupt_copies(d, "c1") == [], corrupt_copies(d, "c1"))
        check("...and is byte-for-byte untouched",
              open(os.path.join(d, "c1.json"), "rb").read() == before)

        # the raw save_chat() path, same read failure
        ns["open"] = emfile_open(os.path.join(d, "c1.json"))
        snap = chat2()
        snap["messages"].append({"role": "user", "text": "next"})
        err = None
        with redirect_stderr(io.StringIO()):
            try:
                ns["save_chat"]("c1", snap)
            except OSError as e:
                err = e
        del ns["open"]
        check("save_chat() onto an EMFILE read raises too",
              isinstance(err, OSError), repr(err))
        check("...and still moves nothing aside",
              corrupt_copies(d, "c1") == [] and
              open(os.path.join(d, "c1.json"), "rb").read() == before)

        # EACCES, for real (chmod 000) — skipped as root, where it can't bite
        if os.geteuid() != 0:
            p = os.path.join(d, "c2.json")
            write(d, "c2", chat2())
            os.chmod(p, 0)
            err = None
            with redirect_stderr(io.StringIO()):
                try:
                    ns["save_chat_update"]("c2", mutate)
                except OSError as e:
                    err = e
            os.chmod(p, 0o600)
            check("an EACCES chat raises instead of being replaced",
                  isinstance(err, PermissionError), repr(err))
            check("...and survives with both its messages",
                  corrupt_copies(d, "c2") == [] and
                  len(json.load(open(p))["messages"]) == 2)
        else:
            print("  SKIP EACCES case: running as root")


def test_decode_errors_still_move_aside():
    print("\n3. a file that IS unparsable is still moved aside and the turn saved")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-2-") as d:
        ns = namespace(d)
        cases = (("notjson", b"{this is not json"),
                 ("latin", b'{"messages": [], "title": "caf\xe9"}'),
                 ("alist", b"[1, 2, 3]"))
        for sid, raw in cases:
            write(d, sid, raw=raw)
            err = None
            with redirect_stderr(io.StringIO()):
                try:
                    out = ns["save_chat_update"](
                        sid, lambda c: c["messages"].append(
                            {"role": "user", "text": "hello"}))
                except Exception as e:
                    out, err = None, e
            try:
                saved = json.load(open(os.path.join(d, sid + ".json")))
            except ValueError:
                saved = {"messages": []}
            if not isinstance(saved, dict):
                saved = {"messages": []}
            check("%s: the save succeeds with the new turn" % sid,
                  err is None and [m["text"] for m in saved["messages"]] == ["hello"],
                  repr(err))
            kept = corrupt_copies(d, sid)
            check("%s: the original is kept as .corrupt-*" % sid,
                  len(kept) == 1 and
                  open(os.path.join(d, kept[0]), "rb").read() == raw, kept)


def test_list_sessions_shapes():
    print("\n4. list_sessions(): one wrong-shaped chat does not fail the sidebar")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-2-") as d:
        ns = namespace(d)
        write(d, "good", chat2())
        write(d, "alist", [1, 2])
        write(d, "msgdict", {"messages": {"0": "x"}})
        write(d, "msgstr", {"messages": "hello"})
        write(d, "firststr", {"messages": ["just a string"]})
        write(d, "titleint", {"title": 7, "messages": [{"role": "user",
                                                        "text": "titled by text"}]})
        write(d, "textint", {"messages": [{"role": "user", "text": 5}]})
        write(d, "branchesint", {"branches": 3, "messages": [
            {"role": "user", "text": "b"}]})
        write(d, "forkbad", {"forked_from": {"session": 9, "at_index": 1},
                             "messages": [{"role": "user", "text": "f"}]})
        write(d, "latin", raw=b'{"messages": [{"text": "\xe9"}]}')
        # valid JSON to Python: 1e999 parses to inf, and int(inf) overflows
        write(d, "forkinf", raw=b'{"forked_from": {"session": "good", "at_index": 1e999},'
                                b' "messages": [{"role": "user", "text": "i"}]}')
        buf = io.StringIO()
        with redirect_stderr(buf):
            try:
                rows, err = ns["list_sessions"](None), None
            except Exception as e:
                rows, err = None, e
        check("THE FIX: the sidebar builds instead of raising",
              err is None and isinstance(rows, list), repr(err))
        rows = {r["id"]: r for r in rows or []}
        check("the healthy conversation is listed with its title",
              rows.get("good", {}).get("title") == "Trip", rows.get("good"))
        for sid in ("alist", "msgdict", "msgstr"):
            check("%s (not a conversation object) is skipped" % sid,
                  sid not in rows)
        check("a non-dict first message lists with an empty title",
              rows.get("firststr", {}).get("title") == "", rows.get("firststr"))
        check("a non-string title falls back to the first message",
              rows.get("titleint", {}).get("title") == "titled by text",
              rows.get("titleint"))
        check("a non-string first text gives an empty title",
              rows.get("textint", {}).get("title") == "", rows.get("textint"))
        check("a non-list `branches` is not counted",
              "branches" not in rows.get("branchesint", {"branches": 1}))
        check("a non-string forked_from.session does not break the title pass",
              "forkbad" in rows and isinstance(rows["forkbad"].get("forked_title"), str),
              rows.get("forkbad"))
        check("a non-UTF-8 chat is skipped, not fatal", "latin" not in rows)
        check("REVIEW FIX: an at_index that overflows int() lists with no turn number",
              rows.get("forkinf", {}).get("forked_turn", "x") is None, rows.get("forkinf"))
        log = buf.getvalue()
        check("each malformed file is named on stderr as [sessions]",
              all("[sessions] skipped chats/%s.json" % s in log
                  for s in ("alist", "msgdict", "msgstr")), log)
        buf2 = io.StringIO()
        with redirect_stderr(buf2):
            try:
                ns["list_sessions"](None)
                ns["list_sessions"](None)
            except Exception as e:
                print("[sessions] raised %r" % e, file=sys.stderr)
        check("...rate-limited: a polled sidebar does not repeat the line",
              "[sessions]" not in buf2.getvalue(), buf2.getvalue())


def main():
    test_read_json_non_utf8()
    test_save_on_unreadable()
    test_decode_errors_still_move_aside()
    test_list_sessions_shapes()
    H.finish()


if __name__ == "__main__":
    main()
