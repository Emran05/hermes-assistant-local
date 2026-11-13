#!/usr/bin/env python3
"""Round-2 regression test for R2-3 (2026-10-08 audit P3 prose, sessions):
deleting a conversation while its turn is running brought it back.

BEFORE: POST /api/sessions/delete removed chats/<sid>.json (outside
_state_lock), and when the still-running turn finished, _finish_chat_job's
save_chat_update read the missing file as {messages: []} and wrote a NEW
conversation holding only the bot reply — the chat the owner had just deleted
reappeared in the sidebar as a one-message stub.

AFTER: the delete runs under _state_lock and records a short-lived tombstone;
_save_chat_locked refuses to recreate a tombstoned id (one [save_chat] line,
the job finishes done with persisted:false), unless the save is a NEW user
message, which starts the conversation again.

REAL Handler + chat store + _finish_chat_job, ast-extracted from
dashboard/server.py, on an ephemeral loopback port; chats in a temp dir; the
"in-flight job" is a stub dict exactly like _new_job's.
HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
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


def namespace(chats, lock):
    ns = H.http_namespace(chats, CHATS=chats, _state_lock=lock,
                          SESSION_RE=re.compile(r"^[A-Za-z0-9._-]{1,80}$"))
    H.extract(["read_json", "write_json", "chat_path", "load_chat",
               "_new_message_id", "stamp_message_ids", "_save_chat_locked",
               "save_chat", "save_chat_update", "_finish_chat_job", "_clear_awaiting_reply"], ns,
              optional=["_json_unparsable", "_quarantine_corrupt",
                        "CHAT_TOMBSTONE_TTL", "_chat_tombstones", "_last_role", "session_key",
                        "ChatDeletedError", "chat_delete"])
    return ns


def job(reply="the late reply"):
    return {"id": "j1", "reply": reply, "ok": True, "state": "done",
            "done": False, "text": reply}


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-3-") as d:
        lock = threading.Lock()
        ns = namespace(d, lock)
        p = os.path.join(d, "c1.json")
        ns["save_chat_update"]("c1", lambda c: c["messages"].append(
            {"role": "user", "text": "start a long tool run"}))
        srv = H.Server(ns["Handler"])
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                print("\n1. the delete itself runs under _state_lock")
                res = {}
                lock.acquire()
                t = threading.Thread(target=lambda: res.update(r=H.req(
                    srv.port, "POST", "/api/sessions/delete",
                    json.dumps({"session": "c1"}).encode())))
                t.start()
                time.sleep(0.4)
                still_there = os.path.exists(p)
                lock.release()
                t.join(5)
                check("THE FIX: the file is not removed while another save holds the lock",
                      still_there)
                st = (res.get("r") or (None,))[0]
                check("...and is removed once it is released (200)",
                      st == 200 and not os.path.exists(p), (st, os.listdir(d)))

                print("\n2. the in-flight turn finishes after the delete")
                j = job()
                ns["_finish_chat_job"](j, "c1")
                check("THE FIX: the deleted conversation is NOT recreated",
                      not os.path.exists(p),
                      os.path.exists(p) and open(p).read()[:200])
                check("...the job still finishes (done) and says persisted:false",
                      j.get("done") is True and j.get("persisted") is False, j)
                try:
                    ns["save_chat_update"]("c1", lambda c: c.update(title="renamed"))
                    raised = None
                except Exception as e:
                    raised = e
                check("a background meta save (rename) does not recreate it either",
                      not os.path.exists(p) and raised is not None, repr(raised))
                try:
                    ns["save_chat_update"]("c1", lambda c: c["messages"].append(
                        {"role": "bot", "text": "claude's deep answer"}))
                    raised = None
                except Exception as e:
                    raised = e
                check("a late background bot answer is refused, not written",
                      not os.path.exists(p) and raised is not None, repr(raised))
                check("...as a ValueError subclass flagged chat_deleted",
                      isinstance(raised, ValueError)
                      and getattr(raised, "chat_deleted", False), repr(raised))

                print("\n3. a NEW user message starts the conversation again")
                ns["save_chat_update"]("c1", lambda c: c["messages"].append(
                    {"role": "user", "text": "hello again"}))
                saved = json.load(open(p)) if os.path.exists(p) else {}
                check("the user message is saved",
                      [m.get("text") for m in saved.get("messages", [])]
                      == ["hello again"], saved)
                j2 = job("answer to hello again")
                ns["_finish_chat_job"](j2, "c1")
                saved = json.load(open(p))
                check("...and the next turn's reply is persisted after it",
                      j2.get("persisted") is True and
                      [m.get("text") for m in saved["messages"]]
                      == ["hello again", "answer to hello again"], saved)

                print("\n4. a conversation that was never deleted is unaffected")
                ns["save_chat_update"]("c2", lambda c: c["messages"].append(
                    {"role": "user", "text": "q"}))
                j3 = job("a")
                ns["_finish_chat_job"](j3, "c2")
                check("an ordinary turn persists as before",
                      j3.get("persisted") is True and
                      len(json.load(open(os.path.join(d, "c2.json")))["messages"]) == 2)
                tombs = ns.get("_chat_tombstones")
                if isinstance(tombs, dict):
                    tombs["old"] = time.time() - ns["CHAT_TOMBSTONE_TTL"] - 5
                    ns["save_chat_update"]("old", lambda c: c["messages"].append(
                        {"role": "bot", "text": "x"}))
                    check("an expired tombstone no longer refuses a save",
                          os.path.exists(os.path.join(d, "old.json"))
                          and "old" not in tombs)
                else:
                    check("an expired tombstone no longer refuses a save", False,
                          "no _chat_tombstones")
            log = buf.getvalue()
            check("the refusal is one [save_chat] line naming the conversation",
                  "[save_chat] c1: not recreating a conversation that was deleted" in log,
                  log[-400:])
            check("no request reached socketserver's handle_error",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
