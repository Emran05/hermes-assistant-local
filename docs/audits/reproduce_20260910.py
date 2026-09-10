#!/usr/bin/env python3
"""Offline evidence for the 2026-09-10 audit, not a regression test suite.

Run: python3 docs/audits/reproduce_20260910.py

Loads selected definitions via AST to avoid server import-time background
threads. All state is temporary; sockets, inference, subprocesses and network
requests are replaced with fakes. A CONFIRMED line means the defect exists.
After a fix, the corresponding assertion should stop passing.
"""

import ast
import collections
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import struct
import subprocess
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import urllib.parse
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
CONFIRMED = []


def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {node.name for node in found} == set(names)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"),
         namespace)


def constant(relative, name):
    tree = ast.parse((REPO / relative).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def confirmed(name, detail):
    CONFIRMED.append(name)
    print(f"CONFIRMED {name}: {detail}")


def store_namespace(scratch):
    ns = dict(os=os, json=json, time=time, threading=threading,
              _state_lock=threading.Lock(), CHATS=str(scratch),
              SETTINGS_FILE=str(scratch / "settings.json"))
    definitions("dashboard/server.py", ["read_json", "write_json", "chat_path",
                "load_chat", "save_chat", "get_settings"], ns)
    return ns


def chat_lost_update(ns):
    ns["save_chat"]("concurrent", {"messages": [{"role": "user", "text": "Hi"}]})
    first = ns["load_chat"]("concurrent")
    second = ns["load_chat"]("concurrent")
    first["messages"].append({"role": "bot", "text": "Local answer"})
    second["messages"].append({"role": "bot", "text": "Claude answer"})
    ns["save_chat"]("concurrent", first)
    ns["save_chat"]("concurrent", second)
    stored = ns["load_chat"]("concurrent")["messages"]
    assert [m["text"] for m in stored] == ["Hi", "Claude answer"]
    confirmed("chat-lost-update", "two successful appends leave only the second answer")


def bridge_defaults(ns):
    ns["CB_ESC_DEFAULT"] = constant("dashboard/aux_claudebridge.py", "CB_ESC_DEFAULT")
    definitions("dashboard/aux_claudebridge.py",
                ["claude_escalation_enabled", "_cb_set_escalation"], ns)
    assert ns["claude_escalation_enabled"]() is True
    Path(ns["SETTINGS_FILE"]).write_text("{broken")
    assert ns["claude_escalation_enabled"]() is True
    assert constant("dashboard/aux_autoroute.py", "AR_DEFAULT_MODE") == "auto"
    confirmed("cloud-default-enabled", "missing AND corrupt settings enable the bridge; routing defaults to auto")


def stale_weather_settings(ns):
    ns["write_json"](ns["SETTINGS_FILE"], {
        "weather_city": "Sample City", "claude_escalation": {"enabled": True}})

    def fake_http(url):
        if "geocoding-api" in url:
            # A user switches off the bridge while an earlier weather request
            # waits for the geocoding response.
            with contextlib.redirect_stdout(io.StringIO()):
                ns["_cb_set_escalation"](False)
            assert ns["get_settings"]()["claude_escalation"]["enabled"] is False
            return {"results": [{"name": "Sample City", "latitude": 1, "longitude": 2}]}
        return {"current": {}, "daily": {}}

    ns.update(_http_json=fake_http, _cached=lambda key, ttl, fn: fn(),
              urllib=SimpleNamespace(parse=urllib.parse), WMO={})
    definitions("dashboard/server.py", ["weather"], ns)
    ns["weather"]()
    assert ns["get_settings"]()["claude_escalation"]["enabled"] is True
    confirmed("settings-toggle-reverted", "weather geocoding silently turns an explicitly disabled bridge back on")


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = collections.deque(chunks)

    def recv(self, size):
        item = self.chunks.popleft() if self.chunks else b""
        if isinstance(item, Exception):
            raise item
        return item

    def sendall(self, data):
        pass

    def settimeout(self, value):
        pass

    def close(self):
        pass


def websocket_probes():
    import base64
    ns = dict(socket=socket, struct=struct, secrets=secrets,
              threading=threading, base64=base64)
    definitions("dashboard/hermes_rpc.py", ["WSError", "WSClient"], ns)
    client_type = ns["WSClient"]

    # One legal fragmented text message; the polling timeout happens between
    # its non-final text frame and final continuation frame.
    client = client_type.__new__(client_type)
    client.sock = FakeSocket([b"\x01\x03abc", socket.timeout(), b"\x80\x03def"])
    client._buf = b""
    client._lock = threading.Lock()
    assert client.recv_text(1) is None
    assert client.recv_text(1) == "def"

    # A delay after a frame header escapes as an exception instead of an
    # ordinary polling timeout, and the consumed header is no longer buffered.
    client.sock = FakeSocket([b"\x81\x03", socket.timeout(), b"abc"])
    client._buf = b""
    try:
        client.recv_text(1)
    except socket.timeout:
        pass
    else:
        raise AssertionError("expected partial-frame timeout")

    # An upgrade response may share a TCP read with the first WebSocket frame.
    fake = FakeSocket([b"HTTP/1.1 101 Switching Protocols\r\n\r\n\x81\x02ok"])
    with patch.object(socket, "create_connection", return_value=fake):
        client = client_type("127.0.0.1", 1, "/fake")
    assert client._buf == b""
    try:
        client.recv_text(1)
    except ns["WSError"]:
        pass
    else:
        raise AssertionError("expected first frame to have been discarded")
    confirmed("websocket-data-loss", "lost fragment prefix, unhandled partial-frame timeout, discarded upgrade remainder")


def duplicate_fallback(ns):
    actions = []
    continued = []

    def fake_turn(job, meta, prompt, save_meta):
        actions.append("serve performed the requested write")
        job["_submitted_ts"] = time.time()
        job["text"] = "I updated the file"
        raise ConnectionError("stream disconnected after a completed tool action")

    def fake_cli(prompt, session):
        continued.append(session)
        # Match the installed CLI's behavior: --continue resolves an actual
        # agent session ID/title, not the dashboard's separate file key.
        if session != "actual-agent-session":
            return False, "No session found matching the dashboard ID"
        actions.append("CLI repeated the requested write")
        return True, "Done"

    ns.update(hermes_rpc=SimpleNamespace(run_turn=fake_turn),
              agent_idle_suspended=lambda: False, agent_paused=lambda: False,
              model_online=lambda: True, run_agent=fake_cli, traceback=traceback,
              sys=__import__("sys"))
    definitions("dashboard/server.py", ["_chat_worker", "_finish_chat_job"], ns)
    ns["save_chat"]("retry", {"serve_key": "actual-agent-session",
                                "messages": [{"role": "user", "text": "Update a file"}]})
    job = {"reply": "", "ok": False, "done": False}
    with contextlib.redirect_stderr(io.StringIO()):
        ns["_chat_worker"](job, "retry", "Update a file")
    assert len(actions) == 1 and continued == ["retry"] and not job["ok"]
    assert ns["load_chat"]("retry")["serve_key"] == "actual-agent-session"
    confirmed("fallback-session-and-retry", "after submission, the worker attempts a CLI retry with the dashboard ID instead of the saved agent session key")


def timezone_roundtrip():
    ns = dict(CFG_SETTINGS_ALLOW=constant("dashboard/aux_config.py", "CFG_SETTINGS_ALLOW"),
              CFG_TICKER_RE=re.compile(r"^[A-Za-z0-9.^=:-]{1,16}$"),
              CFG_URL_RE=re.compile(r"^https?://", re.I))
    definitions("dashboard/aux_config.py", ["_cfg_clean_settings"], ns)
    value = {"timezones": [["Paris", "Europe/Paris"], ["Tokyo", "Asia/Tokyo"]]}
    assert ns["_cfg_clean_settings"](value) == {"timezones": []}
    confirmed("timezone-export-loss", "valid label/timezone pairs become an empty list in the exported snapshot")


def competing_updates(scratch):
    ready = threading.Barrier(2)
    spawned = []
    outcomes = []

    def running():
        ready.wait(timeout=5)
        return False, {}

    def spawn(*args, **kwargs):
        spawned.append(args[0])
        return SimpleNamespace(pid=12345)

    ns = dict(_upd_running=running, _upd_channel=lambda: "stable",
              _upd_os=os, _UPD_SCRIPT=str(REPO / "update.sh"),
              _UPD_ROOT=str(REPO), _UPD_LOG=str(scratch / "update.log"),
              _upd_dirty_files=lambda: [], _upd_time=time,
              _upd_subprocess=SimpleNamespace(Popen=spawn, DEVNULL=-3),
              _upd_state_write=lambda obj: None,
              _upd_state_read=lambda: {}, _upd_version=lambda: "audit")
    definitions("dashboard/aux_update.py", ["_upd_apply"], ns)
    threads = [threading.Thread(target=lambda: outcomes.append(
        ns["_upd_apply"](SimpleNamespace(body={})) )) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert len(spawned) == 2 and all(r["started"] for r in outcomes)
    confirmed("competing-updates", "two concurrent apply requests both pass the running check and launch an updater")


def large_file_undo(scratch):
    import hashlib
    target = scratch / "large-output.bin"
    with target.open("wb") as f:
        f.truncate(32 * 1024 * 1024 + 1)  # sparse: no large allocation
    ns = dict(os=os, hashlib=hashlib, HASH_CAP_BYTES=32 * 1024 * 1024)
    definitions("dashboard/aux_recorder.py", ["_after_state", "_sha256_file"], ns)
    after = ns["_after_state"](str(target))
    assert after["sha256"] is None
    with target.open("ab") as f:
        f.write(b"new owner edit")
    assert target.stat().st_size != after["size"]

    row = dict(status="done", kind="write", reversible="yes", target=str(target),
               after_state=json.dumps(after), undo_note="", session="fake", ts=1,
               snapshot_ref=json.dumps({"workdir": str(scratch), "commit": "fake"}))
    restore_calls = []

    def restore(op, **kwargs):
        restore_calls.append(kwargs)
        return {"success": True, "restored_to": "fake"}

    conn = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: row),
                           close=lambda: None)
    ns.update(json=json, _rec_init=lambda: None, _rec_lock=threading.Lock(),
              _rec_conn=lambda: conn, _rec_json=json.loads, UNDO_WHITELIST={"write"},
              _ckpt=restore, _newest_prerollback=lambda wd: {},
              _finish_undo=lambda *a, **kw: None, _rec_log=lambda msg: None)
    definitions("dashboard/aux_recorder.py", ["recorder_undo_handler"], ns)
    result = ns["recorder_undo_handler"](SimpleNamespace(body={"id": 1, "force": False}))
    assert result["ok"] and len(restore_calls) == 1
    confirmed("undo-missing-conflict-check", "a changed file over 32 MiB reaches restore with force=False")


def dictation_history_race(scratch):
    current = dict(cleanup="model", dictionary={}, keep_history=True, history_days=30)
    ns = dict(_dct_os=os, _dct_json=json, _dct_time=time,
              _dct_lock=threading.Lock(), DCT_STORE=str(scratch / "dictation.json"),
              DCT_MAX_CHARS=16000, DCT_HISTORY_MAX=100,
              dictation_settings=lambda: copy.deepcopy(current),
              _dct_style_for=lambda *a: "prose", dictation_clean_rules=lambda text, *a: text,
              _dct_norm=lambda text: text, _dct_log=lambda msg: None)
    definitions("dashboard/aux_dictation.py",
                ["_dct_load", "_dct_save", "_dct_prune", "_dct_forget_text",
                 "_dct_record", "_dct_finish"], ns)

    def cleanup(text, style):
        current["keep_history"] = False
        ns["_dct_forget_text"]()
        return text, "fake cleanup"

    ns["_dct_model_clean"] = cleanup
    ns["_dct_finish"](SimpleNamespace(body={"text": "Private audit sample"}))
    assert current["keep_history"] is False
    assert ns["_dct_load"]()["history"][-1]["text"] == "Private audit sample"
    confirmed("dictation-history-after-opt-out", "in-flight cleanup saves transcript text after history has been disabled and scrubbed")


def fresh_install_start_gate(scratch):
    script = (REPO / "mlx-server.sh").read_text()
    gate = script.split("# --- Model choice", 1)[0]
    gate = gate.replace('"$HOME/.hermes/dashboard/model-autostart-off"',
                        shlex.quote(str(scratch / "model-autostart-off")))
    gate = gate.replace('"$HOME/.hermes/dashboard/model-start-ok"',
                        shlex.quote(str(scratch / "model-start-ok")))
    # Execute ONLY the gate. There is no model launch command in this probe.
    gate += '\nprintf "REACHED_MODEL_LAUNCH_SECTION\\n"\n'
    result = subprocess.run(["bash"], input=gate, text=True, capture_output=True, check=True)
    assert "REACHED_MODEL_LAUNCH_SECTION" in result.stdout
    for installer in ("install.sh", "install-services.sh"):
        assert "model-autostart-off" not in (REPO / installer).read_text()
    app = (REPO / "app/main.swift").read_text()
    assert '"com.hermes.mlx-server"' in app
    assert 'p.arguments = ["kickstart", "gui/\\(uid)/\\(label)"]' in app
    confirmed("fresh-install-autostart", "installer omits the opt-in gate marker; the app kickstarts the model and the launch gate permits it")


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-audit-") as directory:
        scratch = Path(directory)
        ns = store_namespace(scratch)
        chat_lost_update(ns)
        bridge_defaults(ns)
        stale_weather_settings(ns)
        websocket_probes()
        duplicate_fallback(ns)
        timezone_roundtrip()
        competing_updates(scratch)
        large_file_undo(scratch)
        dictation_history_race(scratch)
        fresh_install_start_gate(scratch)
    print(f"\n{len(CONFIRMED)} defects reproduced offline; no real agent/service called.")


if __name__ == "__main__":
    main()
