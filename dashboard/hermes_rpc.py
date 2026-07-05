"""Minimal stdlib client for the `hermes serve` JSON-RPC/WebSocket backend.

Speaks RFC 6455 (client side, masked frames) over a loopback TCP socket —
no third-party deps, matching the dashboard's stdlib-only rule. One
connection per agent turn: connect, ensure a session, prompt.submit, then
consume `event` notifications until message.complete / error.

Auth: `hermes serve` runs with a pinned HERMES_DASHBOARD_SESSION_TOKEN
(written by install-services.sh to ~/.hermes/dashboard/serve-token); we pass
it as ?token= on the WS URL, same as the official SPA does on loopback.
"""

import base64
import json
import os
import secrets
import socket
import struct
import threading
import time

SERVE_HOST = "127.0.0.1"
SERVE_PORT = int(os.environ.get("HERMES_SERVE_PORT", "9119"))
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard",
                          "serve-token")
TURN_TIMEOUT = int(os.environ.get("HUB_TURN_TIMEOUT", "600"))
RECORDER_HOOK = None   # set by dashboard/aux_recorder.py; called (sid, etype, payload)

try:
    import permissions as _perm   # P1.3 graduated permission tiers (policy engine)
except Exception:                 # engine missing/broken must never kill a turn
    _perm = None


def read_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


class WSError(Exception):
    pass


class WSClient:
    """Tiny RFC 6455 client: text frames only, handles ping/close/fragments."""

    def __init__(self, host, port, path, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("connection closed during handshake")
            resp += chunk
            if len(resp) > 65536:
                raise WSError("oversized handshake response")
        status = resp.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise WSError(f"handshake rejected: {status}")
        self._buf = b""
        self._lock = threading.Lock()

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text):
        payload = text.encode()
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        with self._lock:
            self.sock.sendall(header + mask + masked)

    def _send_control(self, opcode, payload=b""):
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(struct.pack("!BB", 0x80 | opcode,
                                          0x80 | len(payload)) + mask + masked)

    def recv_text(self, timeout):
        """Next complete text message, or None on timeout."""
        self.sock.settimeout(timeout)
        message = b""
        while True:
            try:
                b1, b2 = self._read_exact(2)
            except socket.timeout:
                return None
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read_exact(8))[0]
            if b2 & 0x80:  # masked server frame — never valid, but be lenient
                mask = self._read_exact(4)
                data = bytes(b ^ mask[i % 4]
                             for i, b in enumerate(self._read_exact(n)))
            else:
                data = self._read_exact(n)
            if opcode == 0x9:
                self._send_control(0xA, data)
                continue
            if opcode == 0x8:
                raise WSError("server closed connection")
            if opcode in (0x1, 0x0):
                message += data
                if fin:
                    return message.decode(errors="replace")
                continue
            # binary/pong/etc — ignore

    def close(self):
        try:
            self._send_control(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class ServeSession:
    """One JSON-RPC conversation with hermes serve over a fresh WS."""

    def __init__(self):
        token = read_token()
        if not token:
            raise WSError(f"no serve token at {TOKEN_FILE}")
        self.ws = WSClient(SERVE_HOST, SERVE_PORT, f"/api/ws?token={token}")
        self._next_id = 1
        self._events = []

    def call(self, method, params, timeout=30):
        rid = self._next_id
        self._next_id += 1
        self.ws.send_text(json.dumps({"jsonrpc": "2.0", "id": rid,
                                      "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv_text(timeout=min(2.0, deadline - time.time()))
            if raw is None:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("id") == rid:
                if "error" in obj:
                    raise WSError(obj["error"].get("message", "rpc error"))
                return obj.get("result") or {}
            if obj.get("method") == "event":
                self._events.append(obj.get("params") or {})
        raise WSError(f"timeout waiting for {method}")

    def next_event(self, timeout):
        if self._events:
            return self._events.pop(0)
        raw = self.ws.recv_text(timeout=timeout)
        if raw is None:
            return None
        try:
            obj = json.loads(raw)
        except ValueError:
            return None
        if obj.get("method") == "event":
            return obj.get("params") or {}
        return None

    def close(self):
        self.ws.close()


def run_turn(job, chat_meta, prompt, save_meta):
    """Drive one agent turn; mutates `job` dict in place as events stream.

    job fields consumed by the poll endpoint: state, text, status, approval,
    reply, ok, done. `chat_meta` carries serve_sid/serve_key persistence via
    save_meta() so the conversation resumes across turns and serve restarts.
    """
    srv = ServeSession()
    try:
        sid = chat_meta.get("serve_sid") or ""
        key = chat_meta.get("serve_key") or ""
        alive = False
        if sid:
            try:
                srv.call("session.status", {"session_id": sid}, timeout=10)
                alive = True
            except WSError:
                alive = False
        if not alive and key:
            try:
                res = srv.call("session.resume", {"session_id": key},
                               timeout=20)
                sid = res.get("session_id") or sid
                alive = bool(sid)
            except WSError:
                alive = False
        if not alive:
            res = srv.call("session.create",
                           {"title": chat_meta.get("title") or "Hub chat",
                            "cwd": os.path.expanduser("~"), "source": "hub"},
                           timeout=20)
            sid = res.get("session_id") or ""
            key = res.get("stored_session_id") or res.get("session_key") or ""
            if not sid:
                raise WSError("session.create returned no session_id")
        chat_meta["serve_sid"], chat_meta["serve_key"] = sid, key
        save_meta()

        srv.call("prompt.submit", {"session_id": sid, "text": prompt},
                 timeout=30)
        job["_submitted_ts"] = time.time()   # metrics P1.5: setup/serve TTFT split

        text, status = "", ""
        deadline = time.time() + TURN_TIMEOUT
        while time.time() < deadline:
            choice = job.pop("pending_choice", None)
            if choice:
                try:
                    srv.call("approval.respond",
                             {"session_id": sid, "choice": choice}, timeout=15)
                except WSError as e:
                    job["status"] = f"approval failed: {e}"
                job["approval"] = None
                job["state"] = "running"
                if _perm and job.get("_approval_payload"):
                    try:
                        _perm.audit(job, job.pop("_approval_payload"),
                                    {"tier": "ask"}, "user-" + str(choice))
                    except Exception:
                        pass
            ev = srv.next_event(timeout=1.0)
            if ev is None:
                continue
            if ev.get("session_id") not in ("", sid):
                continue
            etype = ev.get("type") or ""
            payload = ev.get("payload") or {}
            if RECORDER_HOOK and etype in ("tool.start", "tool.complete"):
                try:
                    RECORDER_HOOK(sid, etype, payload)
                except Exception:
                    pass   # recorder must never break a turn
            if etype == "message.delta":
                text += (payload.get("text") or payload.get("delta") or "")
                job["text"] = text
            elif etype in ("tool.start", "tool.generating"):
                job["status"] = "using " + (payload.get("name")
                                            or payload.get("tool") or "a tool")
            elif etype == "tool.complete":
                job["status"] = ""
            elif etype == "status.update":
                job["status"] = (payload.get("text") or payload.get("kind")
                                 or "")
            elif etype == "approval.request":
                # P1.3: consult the graduated-permission policy. Sends only
                # "once"/"deny" upstream (never session/always), so hermes's
                # own allowlists never grow. decide() never raises; every
                # failure path falls through to ASK (fail-safe).
                v = _perm.decide(payload) if _perm else {"tier": "ask", "reason": ""}
                if v.get("tier") == "auto":
                    try:
                        srv.call("approval.respond",
                                 {"session_id": sid, "choice": "once"}, timeout=15)
                        job["status"] = "auto-approved · " + v.get("reason", "")
                        if _perm:
                            _perm.audit(job, payload, v, "auto-approved")
                        continue
                    except WSError:
                        pass               # fail open to ASK
                elif v.get("tier") == "never":
                    try:
                        srv.call("approval.respond",
                                 {"session_id": sid, "choice": "deny"}, timeout=15)
                        job["status"] = "blocked by policy · " + v.get("reason", "")
                        if _perm:
                            _perm.audit(job, payload, v, "auto-denied")
                        continue
                    except WSError:
                        pass               # user can still deny by hand
                payload["_policy"] = v
                job["_approval_payload"] = payload
                job["approval"] = payload
                job["state"] = "approval"
                if _perm:
                    _perm.audit(job, payload, v, "asked")
            elif etype == "message.complete":
                final = payload.get("text") or text
                job.update(reply=final or "(empty response)", ok=True,
                           state="done", done=True)
                return
            elif etype == "error":
                msg = payload.get("message") or payload.get("text") or "agent error"
                job.update(reply=msg, ok=False, state="done", done=True)
                return
        job.update(reply=f"The agent took longer than {TURN_TIMEOUT}s and was "
                         "stopped. (Its session may still finish in the "
                         "background.)", ok=False, state="done", done=True)
        try:
            srv.call("session.interrupt", {"session_id": sid}, timeout=5)
        except WSError:
            pass
    finally:
        srv.close()
