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
import sys
import threading
import time

SERVE_HOST = "127.0.0.1"
SERVE_PORT = int(os.environ.get("HERMES_SERVE_PORT", "9119"))
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard",
                          "serve-token")
TURN_TIMEOUT = int(os.environ.get("HUB_TURN_TIMEOUT", "600"))
RECORDER_HOOK = None   # set by dashboard/aux_recorder.py; called (sid, etype, payload)
# set by dashboard/aux_branch.py; called (chat_meta) -> extra `session.create`
# params. Returns {} for an ordinary conversation (so the minting path below is
# unchanged) and {"messages": [...seed...], "parent_session_id": ...} for a
# branched one, which is how a fork carries its pre-branch history without a
# single change to hermes-agent.
SEED_HOOK = None

# The longest the dashboard keeps one approval card answerable. serve's own
# wait is `approvals.gateway_timeout` (default 300 s) and `approval.respond`
# carries no request id: it resolves the OLDEST entry still pending for the
# session (tools/approval.py resolve_gateway_approval). A card that outlived
# serve's entry would therefore hand the owner's click to the NEXT, unseen
# command. Ours ends a little earlier, with an explicit deny (silence is not
# consent) — see _Approvals.
APPROVAL_TTL = int(os.environ.get("HUB_APPROVAL_TTL", "290"))

# serve's blocking prompts (tui_gateway/server.py `_block`): it emits the
# event, then holds the agent thread until the matching *.respond arrives or
# 300 s (sudo 120 s) pass (2026-10-21 audit C115). The dashboard has no UI for
# them, so they are answered at once — a clarify with an explicit "nobody is
# here" instruction, sudo and secrets with NOTHING (a password or a secret
# never passes through this chat) — and the reply tells the owner what was
# asked. event -> (respond method, answer field)
#
# terminal.read.request is the read_terminal tool's bridge to the desktop
# GUI's terminal pane (30 s wait). This chat has no terminal pane, so it is
# answered at once with an empty buffer, which the tool itself turns into "No
# in-app terminal is open" — not a 30 s stall per call (R2-9).
BLOCKING_PROMPTS = {
    "clarify.request": ("clarify.respond", "answer"),
    "sudo.request": ("sudo.respond", "password"),
    "secret.request": ("secret.respond", "value"),
    "terminal.read.request": ("terminal.read.respond", "text"),
}
CLARIFY_NO_USER = (
    "(No answer: the person is not watching this chat live and cannot reply "
    "mid-turn. Do not wait for them. Pick the safest reasonable option, say "
    "plainly in your reply which assumption you made, and do not take any "
    "destructive or irreversible action on the strength of that assumption.)")

# A transcript replayed into a fresh session (see _transcript_seed) keeps the
# newest messages up to this many characters, so one lost resume of a very
# long conversation does not become a multi-minute prefill.
SEED_MAX_CHARS = int(os.environ.get("HUB_SEED_MAX_CHARS", "48000"))
_SEED_ROLE = {"user": "user", "bot": "assistant", "assistant": "assistant"}

# The owner's Stop (POST /api/chat/stop). A stop and the decision to submit
# the prompt are taken under ONE lock, so a turn is either stopped before
# anything reached the agent (the dashboard ends it, "nothing was run") or it
# was submitted and is stopped through serve's own `session.interrupt` — never
# both, never a prompt sent after the owner pressed Stop. Once interrupted,
# the turn has STOP_GRACE seconds to end by itself (its interrupted
# message.complete) before the job is ended anyway.
STOP_GRACE = float(os.environ.get("HUB_STOP_GRACE", "10"))
_STOP_LOCK = threading.Lock()
STOPPED_BEFORE_SUBMIT = ("Stopped by you before it reached the agent, so "
                         "nothing was run.")


def request_stop(job, pre_submit=None):
    """Mark `job` stopped by the owner -> "pre" | "post" | "again".

    "pre": the prompt was never submitted and never will be (run_turn checks
    under the same lock); `pre_submit(job)`, when given, runs INSIDE the lock
    so the job's ending is in place before any other thread can finish it.
    "post": run_turn owns the ending (interrupt, deny, stopped note).
    "again": a stop was already requested — nothing is sent twice.

    A click the owner gave before the stop that has not been forwarded yet
    is discarded here, under the same lock every approving answer is claimed
    under (`_claim_approve`): once this returns, nothing but "deny" can leave
    for this job. An approving answer that was already claimed and on its way
    is recorded (`stop_after_approval`) so the reply can say so."""
    with _STOP_LOCK:
        if job.get("stop_requested"):
            return "again"
        job["stop_requested"] = True
        job["stop_ts"] = time.time()
        job.pop("pending_choice", None)
        job.pop("pending_approval_id", None)
        if int(job.get("_approving") or 0) > 0:
            job["stop_after_approval"] = True
        if job.get("submit_claimed"):
            return "post"
        if pre_submit is not None:
            pre_submit(job)
        return "pre"


def _claim_submit(job):
    """True when the prompt may be submitted (no stop came first)."""
    with _STOP_LOCK:
        if job.get("stop_requested"):
            return False
        job["submit_claimed"] = True
        return True


def _claim_approve(job):
    """May an APPROVING answer ("once") leave for `job` now? Decided under
    _STOP_LOCK, atomic with request_stop: False once a stop was requested.
    On True the caller sends, then calls _approve_done(job). The lock is
    never held across the socket call — `_approving` is what a stop landing
    mid-send sees."""
    with _STOP_LOCK:
        if job.get("stop_requested"):
            return False
        job["_approving"] = int(job.get("_approving") or 0) + 1
        return True


def _approve_done(job):
    with _STOP_LOCK:
        job["_approving"] = max(0, int(job.get("_approving") or 0) - 1)


def stopped_reply(text, tools_ran, ended=True, told=True, approval_sent=False):
    """The reply of a turn the owner stopped: what had streamed, KEPT, then
    one plain line saying who stopped it."""
    note = "Stopped by you."
    if approval_sent:
        note += (" An approval you gave just before stopping had already been "
                 "sent, so that action may have run.")
    if tools_ran:
        note += " Check what it already did before repeating the request."
    if not told:
        note += (" (It could not be told to stop, so its session may still "
                 "finish in the background.)")
    elif not ended:
        note += (" (It did not stop in time, so its session may still finish "
                 "in the background.)")
    text = str(text or "").rstrip()
    return (text + "\n\n" + note) if text.strip() else note


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


class RPCError(WSError):
    """serve ANSWERED the request, with a JSON-RPC error — as opposed to a
    timeout or a dropped socket, where it may or may not have acted on it."""


class WSClient:
    """Tiny RFC 6455 client: text frames only, handles ping/close/fragments.

    ALL parser state lives on the INSTANCE (`_buf` raw bytes, `_frag`/
    `_frag_opcode` the fragmented message in progress) and a frame is consumed
    out of `_buf` only once its whole payload is buffered. So every ordinary
    read boundary is resumable: a poll timeout mid-header, mid-payload or
    between two fragments returns None with nothing lost, and the caller's
    next `recv_text()` continues the same message. (Audit 2026-09-10 A05 —
    the old reader consumed a header before it could time out, kept the
    fragment prefix in a local, and dropped bytes that shared a read with the
    HTTP 101.)
    """

    # Class-level defaults so an instance built without __init__ (the audit's
    # AST-extracted probes, unit fixtures) still parses correctly.
    _frag = b""            # payload of the fragmented message in progress
    _frag_opcode = None    # its opcode (0x1 text / 0x2 binary); None = idle
    MAX_FRAME_BYTES = 16 * 1024 * 1024     # one frame, before reassembly
    MAX_MESSAGE_BYTES = 64 * 1024 * 1024   # one reassembled message

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
        head, _, rest = resp.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise WSError(f"handshake rejected: {status}")
        # Whatever followed the header terminator is already WebSocket: the
        # server is free to put the 101 and its first frame in ONE segment,
        # and this used to be reset to b"" — i.e. the first event of the turn
        # was silently discarded whenever that happened.
        self._buf = rest
        self._frag = b""
        self._frag_opcode = None
        self._lock = threading.Lock()

    def _fail(self, msg):
        """Close and raise — the stream is out of sync or over a bound."""
        try:
            self.close()
        except Exception:
            pass
        raise WSError(msg)

    def _fill(self, timeout):
        """One socket read appended to `_buf`. False on timeout; raises on EOF."""
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not chunk:
            raise WSError("connection closed")
        self._buf += chunk
        return True

    def _take_frame(self):
        """One WHOLE buffered frame as (fin, opcode, payload), else None.

        Consumes from `_buf` only when every byte of the frame is present, so
        returning None never loses anything the socket already handed us.
        """
        buf = self._buf
        if len(buf) < 2:
            return None
        b1, b2 = buf[0], buf[1]
        n, off = b2 & 0x7F, 2
        if n == 126:
            if len(buf) < off + 2:
                return None
            n = struct.unpack("!H", buf[off:off + 2])[0]
            off += 2
        elif n == 127:
            if len(buf) < off + 8:
                return None
            n = struct.unpack("!Q", buf[off:off + 8])[0]
            off += 8
        mask = b""
        if b2 & 0x80:  # masked server frame — never valid, but be lenient
            if len(buf) < off + 4:
                return None
            mask, off = buf[off:off + 4], off + 4
        if n > self.MAX_FRAME_BYTES:   # refuse BEFORE buffering the payload
            self._fail("frame too large: %d bytes (max %d)"
                       % (n, self.MAX_FRAME_BYTES))
        if len(buf) < off + n:
            return None
        data = buf[off:off + n]
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._buf = buf[off + n:]
        return b1 & 0x80, b1 & 0x0F, data

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
        """Next complete text message, or None on timeout.

        `timeout` bounds the WHOLE message on a monotonic deadline. On timeout
        every byte already read and every fragment already received stays on
        the instance, so the next poll resumes the same message instead of
        losing its prefix or escaping as socket.timeout into the CLI fallback.
        """
        # Local import: `time` is a module global here, but this class is also
        # exec'd on its own by the audit probe / unit fixtures, whose namespace
        # only carries socket/struct/secrets/threading/base64.
        import time as _time
        deadline = _time.monotonic() + max(0.0, float(timeout))
        while True:
            frame = self._take_frame()
            if frame is None:
                left = deadline - _time.monotonic()
                if left <= 0 or not self._fill(left):
                    return None
                continue
            fin, opcode, data = frame
            if opcode >= 0x8:                      # control frame
                if not fin or len(data) > 125:     # never fragmented, ≤125 B
                    self._fail("invalid control frame")
                if opcode == 0x9:
                    self._send_control(0xA, data)  # ping -> pong
                elif opcode == 0x8:
                    raise WSError("server closed connection")
                continue                           # pong / reserved: ignore
            if opcode == 0x0:
                if self._frag_opcode is None:
                    self._fail("continuation frame with nothing to continue")
            elif self._frag_opcode is not None:
                self._fail("new data frame inside a fragmented message")
            else:
                self._frag_opcode = opcode
            if len(self._frag) + len(data) > self.MAX_MESSAGE_BYTES:
                self._fail("message too large: over %d bytes"
                           % self.MAX_MESSAGE_BYTES)
            self._frag += data
            if not fin:
                continue
            payload, op = self._frag, self._frag_opcode
            self._frag, self._frag_opcode = b"", None
            if op == 0x1:
                return payload.decode(errors="replace")
            # binary message — ignore it and keep waiting for a text one

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

    def call(self, method, params, timeout=30, on_sent=None):
        """One JSON-RPC request/response.

        `on_sent` fires the instant the request bytes have been written to the
        socket, BEFORE we start waiting for the reply. run_turn uses it to mark
        a prompt as submitted: a call that times out waiting for its response
        may still have been received and acted on, and the chat fallback has to
        know that (2026-09-10 audit A04) — a retry after a submitted prompt can
        repeat a tool action.
        """
        rid = self._next_id
        self._next_id += 1
        self.ws.send_text(json.dumps({"jsonrpc": "2.0", "id": rid,
                                      "method": method, "params": params}))
        if on_sent is not None:
            try:
                on_sent()
            except Exception:
                pass          # bookkeeping must never break a turn
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
                    raise RPCError(obj["error"].get("message", "rpc error"))
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


def turn_status(session_id, timeout=8):
    """Best-effort `session.status` for a session whose stream we lost.

    Opens a FRESH connection — the point is that the old one is gone. Returns
    the RPC result dict, or None if serve is unreachable / does not know the
    session. Never raises. Used by server.py's chat fallback to say something
    truthful about a turn that was already submitted when the stream dropped,
    instead of silently re-running it (2026-09-10 audit A04).
    """
    if not session_id:
        return None
    srv = None
    try:
        srv = ServeSession()
        return srv.call("session.status", {"session_id": session_id},
                        timeout=timeout)
    except Exception:
        return None
    finally:
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass


def _resolved_count(res):
    """approval.respond's `resolved` (how many serve entries it unblocked), or
    None when the reply does not say (an older serve)."""
    try:
        n = (res or {}).get("resolved")
        return None if n is None else int(n)
    except (TypeError, ValueError, AttributeError):
        return None


def _serve_config(srv):
    """serve's live config.yaml as a dict ({} when it will not say). One
    `config.get full` per turn at most — the caller caches it."""
    try:
        cfg = (srv.call("config.get", {"key": "full"}, timeout=10) or {}).get("config")
    except WSError:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _approval_ttl(cfg):
    """APPROVAL_TTL, shortened to end before serve's own approval wait does
    (tools/approval.py reads `approvals.gateway_timeout` per request, int(),
    300 on anything unparseable)."""
    try:
        gt = int(((cfg or {}).get("approvals") or {}).get("gateway_timeout", 300))
    except (TypeError, ValueError, AttributeError):
        gt = 300
    gt = max(gt, 0)
    return max(0.0, min(float(APPROVAL_TTL), gt - min(10.0, gt * 0.1)))


def _transcript_seed(chat_meta):
    """The conversation so far as `session.create` `messages` — user/assistant
    text only, the shape aux_branch.br_seed_history builds and the gateway's
    `_coerce_seed_history` keeps.

    Used when the stored agent session cannot be used (2026-10-21 audit
    C23/C112), so the new one at least sees the words of the earlier turns.
    The trailing user message is dropped (/api/chat appended it before the
    worker ran; it is the prompt about to be submitted), dashboard error
    bubbles are not the assistant's words, and only the newest messages up to
    SEED_MAX_CHARS are kept."""
    msgs = list((chat_meta or {}).get("messages") or [])
    if msgs and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "user":
        msgs = msgs[:-1]
    out = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("err"):
            continue
        role = _SEED_ROLE.get(m.get("role"))
        text = m.get("text")
        if role is None or not isinstance(text, str) or not text.strip():
            continue
        out.append({"role": role, "content": text})
    keep, total = [], 0
    for m in reversed(out):
        total += len(m["content"])
        if total > SEED_MAX_CHARS and keep:
            break
        keep.append(m)
    return list(reversed(keep))


def _config_model(cfg):
    """(model, provider) a NEW session would get — tui_gateway's
    `_config_model_target()` read off config.yaml (provider "auto" = none)."""
    m = (cfg or {}).get("model")
    if isinstance(m, dict):
        model = str(m.get("default") or "").strip()
        prov = str(m.get("provider") or "").strip()
        return model, ("" if prov.lower() == "auto" else prov)
    if isinstance(m, str):
        return m.strip(), ""
    return "", ""


def _align_session_model(srv, sid, res, cfg):
    """2026-10-21 audit C112. A cold `session.resume` rebuilds the agent with
    the model stored in that session's state.db row (`_stored_session_runtime_
    overrides`), and serve's per-turn config sync skips any session carrying
    that override — so an old conversation keeps asking the model server for a
    model the roster no longer has. Move THIS session to the model a new
    session would get, the way the per-turn sync would have: model plus the
    configured provider, session-scoped (`--session`: config.yaml is never
    written).

    The agent is built first (`process.list` runs serve's blocking `_sess()`):
    a cold resume defers the build, and a switch on an unbuilt session with an
    explicit provider only records an override that the deferred build then
    ignores in favour of the stored one.

    Returns (ok, stored_model, wanted_model); ok=False means the session is
    still on the stored model and must not be used for this turn."""
    info = (res or {}).get("info") if isinstance(res, dict) else None
    have = str((info or {}).get("model") or "").strip() if isinstance(info, dict) else ""
    if not have:
        return True, "", ""
    want, prov = _config_model(cfg)
    if not want or want == have:
        return True, have, want
    value = want + (" --provider " + prov if prov else "") + " --session"
    try:
        srv.call("process.list", {"session_id": sid}, timeout=90)
        r = srv.call("config.set", {"session_id": sid, "key": "model",
                                    "value": value}, timeout=60)
    except WSError as e:
        print(f"[hermes_rpc] could not move resumed session {sid} from "
              f"{have} to {want}: {e}", file=sys.stderr, flush=True)
        return False, have, want
    if (r or {}).get("confirm_required"):
        return False, have, want
    print(f"[hermes_rpc] resumed session {sid}: {have} -> {want}",
          file=sys.stderr, flush=True)
    return True, have, want


def _turn_running(srv, sid):
    """Is serve still running a turn for `sid`? True / False / None (unknown)."""
    try:
        res = srv.call("session.status", {"session_id": sid}, timeout=8) or {}
    except WSError:
        return None
    return _status_running(res)


def _status_running(res):
    """True / False / None from a session.status result. It has no structured
    flag; its `output` text carries the line "Agent Running: Yes|No"
    (tui_gateway/server.py session.status)."""
    res = res if isinstance(res, dict) else {}
    if isinstance(res.get("running"), bool):
        return res["running"]
    out = str(res.get("output") or "")
    if "Agent Running: Yes" in out:
        return True
    if "Agent Running: No" in out:
        return False
    return None


def _status_model(res):
    """The model a LIVE session's agent is on, from session.status ("" when
    it will not say). Its `output` carries "Model: <id> (<provider>)", and
    "(unknown)" while the agent is not built yet."""
    res = res if isinstance(res, dict) else {}
    if isinstance(res.get("model"), str):
        return res["model"].strip()
    for line in str(res.get("output") or "").splitlines():
        if line.startswith("Model: "):
            model = line[len("Model: "):].strip()
            if model.endswith(")") and " (" in model:
                model = model.rsplit(" (", 1)[0].strip()
            return "" if model in ("", "(unknown)") else model
    return ""


def _with_notices(reply, notices):
    if not notices:
        return reply
    tail = "\n".join("(" + n + ")" for n in notices)
    return (reply.rstrip() + "\n\n" + tail) if reply else tail


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _cmd_label(payload):
    """What an approval was for, as one short inline-code span for a notice."""
    p = payload if isinstance(payload, dict) else {}
    s = _one_line(p.get("command") or p.get("description") or "", 120)
    s = s.replace("`", "'")
    return ("`%s`" % s) if s else "a command"


def _answer_blocking_prompt(srv, etype, payload, notices):
    """Answer one of serve's blocking prompts without the owner (C115) and
    note what was asked. Never sends a password or a secret. Returns the
    status line for the job."""
    method, field = BLOCKING_PROMPTS[etype]
    status = "answered the agent's question for you"
    if etype == "terminal.read.request":
        # Nothing to tell the owner: the tool reports "no terminal open" to
        # the agent, and the agent says what it could not read.
        answer, note = "", None
        status = "no terminal pane in this chat — told the agent so"
    elif etype == "clarify.request":
        answer = CLARIFY_NO_USER
        note = (
            'The agent asked: "%s". This chat cannot answer questions '
            "mid-turn yet, so it was told to pick the safest option and say "
            "what it assumed." % _one_line(payload.get("question"), 300))
    elif etype == "sudo.request":
        answer = ""
        note = (
            "The agent asked for your sudo password. Passwords are never "
            "passed through this chat, so it got none and the command ran "
            "without sudo (or failed).")
    else:
        answer = ""
        var = "".join(c for c in str(payload.get("env_var") or "")
                      if c.isalnum() or c == "_")[:64] or "a secret"
        note = (
            "A skill asked for %s. Secrets are never collected in this chat, "
            "so it was skipped — set it yourself in ~/.hermes/.env if you "
            "want that skill to work." % var)
    if note and note not in notices:   # every sudo command asks again: say it once
        notices.append(note)
    try:
        srv.call(method, {"request_id": payload.get("request_id", ""),
                          field: answer}, timeout=10)
    except WSError as e:
        print(f"[hermes_rpc] {method} failed: {e}", file=sys.stderr, flush=True)
    return status


class _Approvals:
    """The approval cards of ONE turn, in the same FIFO order as serve's own
    queue for the session (2026-10-08 audit B01).

    serve's `approval.respond {session_id, choice}` carries no request id: it
    resolves the OLDEST approval still pending for the session. So the only
    safe way to answer a card is to keep this mirror exactly in step with
    serve's queue and to answer only its head:

      * every card gets a dashboard-minted `approval_id`; the owner's answer
        (`job["pending_choice"]` + `job["pending_approval_id"]`, set by
        server.py's /api/chat/approve) is forwarded only while that id is
        still the head, and only once;
      * a request the permission policy decides by itself (auto / never) is
        answered at once;
      * a SECOND request while a card is still up is a desync (round-2
        X-approval-flow-6): serve appends to its queue under a lock but emits
        approval.request outside it, so two threads of one session can reach
        this socket in the opposite order to serve's queue, and an answer to
        the card shown would resolve the OTHER entry. Until approval.respond
        carries a request id, both are denied, the reply says so, and no card
        is shown — so the mirror never holds more than one entry;
      * a card ends at the TTL with an explicit deny, so serve's entry for it
        is gone before the next card can be clicked;
      * `resolved == 0` (2026-10-21 audit C33) means serve had NOTHING
        pending: the click decided nothing and every card here is stale;
      * an answer whose delivery is unknown (WSError) puts the two queues out
        of step, so everything still pending is denied in one call
        (`all: true`) rather than letting a later click land on the wrong
        entry.

    The head is published as `job["approval"]` / `job["state"] == "approval"`,
    assigned only when the head changes (aux_metrics counts each assignment
    as one approval asked). `close()` denies whatever is left when the turn
    ends, so no entry outlives it and takes the next turn's first click.
    """

    def __init__(self, srv, sid, job, notices, cfg_fn):
        self.srv, self.sid, self.job, self.notices = srv, sid, job, notices
        self._cfg_fn = cfg_fn
        self._ttl = None
        self.q = []
        self._shown = None
        self.broken = False   # a deny-all failed too: answer nothing by position

    def ttl(self):
        if self._ttl is None:
            self._ttl = _approval_ttl(self._cfg_fn())
        return self._ttl

    def _respond(self, wire, all_=False):
        if wire != "deny":
            # Every approving answer goes through _send(); a direct call here
            # with anything but "deny" is a bug, and is denied instead.
            wire = "deny"
        params = {"session_id": self.sid, "choice": wire}
        if all_:
            params["all"] = True
        return _resolved_count(self.srv.call("approval.respond", params,
                                             timeout=15))

    def _send(self, wire):
        """THE single place an answer for one entry leaves -> (wire_sent, n).

        An approving wire ("once") leaves only after `_claim_approve` says no
        stop was requested, decided under the stop lock; after an owner Stop
        it becomes "deny" here, whoever decided it (a card click or a Trust
        "auto" verdict). Raises like _respond."""
        if wire != "deny":
            if not _claim_approve(self.job):
                return "deny", self._respond("deny")
            try:
                params = {"session_id": self.sid, "choice": wire}
                return wire, _resolved_count(self.srv.call(
                    "approval.respond", params, timeout=15))
            finally:
                _approve_done(self.job)
        return "deny", self._respond("deny")

    def _audit(self, payload, action, choice=""):
        if _perm:
            try:
                _perm.audit(self.job, payload,
                            payload.get("_policy") or {"tier": "ask"},
                            action, choice=choice)
            except Exception:
                pass

    def _publish(self):
        head = self.q[0] if self.q else None
        if head is not None and self.job.get("stop_requested"):
            # After the owner's Stop nothing becomes a card. add() checks the
            # stop first, but a stop accepted between that check and this
            # call (the audit sits between them) used to put a card on screen
            # until serve acknowledged the interrupt. The entry stays in the
            # mirror, so the stop's close() still denies it.
            head = None
        hid = head.get("approval_id") if head else None
        if hid == self._shown:
            return
        self._shown = hid
        self.job["approval"] = head
        self.job["state"] = "approval" if head else "running"

    def card_up(self):
        """Is a card in front of the owner right now?"""
        return self._shown is not None

    def _note(self, text):
        if text not in self.notices:
            self.notices.append(text)

    def _forget_all(self, action):
        """serve has nothing pending for this session: every card is stale."""
        while self.q:
            self._audit(self.q.pop(0), action)

    def _deny_all(self):
        try:
            self._respond("deny", all_=True)
            self.broken = False
        except WSError:
            self.broken = True

    def _desync(self, why, note=None):
        """We no longer know which entry serve would resolve next: deny
        everything it has pending, which puts both queues back at empty."""
        self.job["status"] = why
        if self.q:
            self._withdraw(self.q[0])     # no click accepted while denying
        self._deny_all()
        self._note(note or ("An approval answer could not be confirmed, so "
                            "anything still waiting for approval in this "
                            "turn was denied."))
        self._forget_all("desync-denied")

    def close(self):
        """The turn is over. Anything still in the mirror is denied now: an
        entry left in serve's queue would otherwise be resolved by the next
        turn's first click, on a card for something else."""
        if not self.q:
            return
        try:
            self._desync("the turn ended with an approval still waiting — "
                         "denied",
                         note=("An approval was still waiting when this turn "
                               "ended, so it was denied. It did not run."))
        except Exception as e:     # a dead socket must not keep the turn open
            print(f"[hermes_rpc] deny-all at turn end failed: {e}",
                  file=sys.stderr, flush=True)
            self.q = []
            self._publish()

    def add(self, payload, verdict, auto=None):
        if self.job.get("no_human"):
            # X-approval-flow-5: nobody can answer this turn's cards (a
            # Needs-you draft quoting a stranger's message, in a hidden
            # session), so nothing it asks for is approved — not by a card,
            # not by a Trust "auto" tier. Denied at the head, like `never`.
            verdict = dict(verdict or {}, tier="never",
                           reason="a background draft never runs anything")
            auto = "deny"
        elif self.job.get("stop_requested"):
            # The owner pressed Stop: nothing this turn asks for afterwards
            # is shown or approved.
            verdict = dict(verdict or {}, tier="never",
                           reason="you stopped this turn")
            auto = "deny"
        if self.broken:
            # still out of step with serve: no card can be answered safely
            self._deny_all()
            self._audit(dict(payload, _policy=verdict), "desync-denied")
            self._publish()
            return
        payload["_policy"] = verdict
        payload["approval_id"] = secrets.token_hex(8)
        payload["_ts"] = time.time()
        if self.q:
            # A card is still up (an auto head is always answered before
            # settle() returns): we cannot know which of the two serve would
            # resolve next — see the class docstring.
            self._audit(payload, "desync-denied")
            self._desync("two approvals at once — both denied",
                         note=("Two approvals were requested at the same "
                               "time, which this chat cannot yet answer "
                               "safely, so both were denied: %s and %s. "
                               "Neither ran."
                               % (_cmd_label(self.q[0]), _cmd_label(payload))))
            self._publish()
            return
        if auto:
            payload["_auto"] = auto        # policy-decided: answered at once
        else:
            self._audit(payload, "asked")
        self.q.append(payload)
        self.settle()

    def answer(self, choice, aid):
        head = self.q[0] if self.q else None
        if (head is None or head.get("_auto") or choice not in ("approve", "deny")
                or not aid or aid != head.get("approval_id")):
            # Never forward an answer to anything but the card it was given
            # for — not even a well-meant one.
            if aid and aid == self.job.get("approval_expired"):
                # /api/chat/approve took this click in the instant the card
                # was being withdrawn at its TTL (R2-10): it was told {ok:
                # true}, so the reply has to say what really happened.
                self.job["status"] = ("your answer arrived just after that "
                                      "approval timed out — it was denied, "
                                      "the command did not run")
                self._note("Your answer to an approval arrived just after it "
                           "had timed out, so it was denied. The command did "
                           "not run.")
                return
            self.job["status"] = ("ignored an answer for an approval that is "
                                  "no longer waiting")
            return
        self.q.pop(0)
        self._forward(head, choice)
        self.settle()

    def _forward(self, head, choice):
        """Send the owner's answer for `head`, already popped off the mirror."""
        # "once", not "approve": the command guard reads any non-deny choice
        # as yes, but serve's MCP-elicitation path accepts only once/session/
        # always and reads "approve" as a decline. "once" also never grows
        # serve's allowlists.
        wire = "once" if choice == "approve" else "deny"
        try:
            sent, n = self._send(wire)
        except WSError as e:
            self._audit(head, "respond-failed", choice=choice)
            self._desync(f"approval failed: {e}")
            return
        if sent != wire:
            # the owner stopped the turn before this click could leave
            self._audit(head, "stopped-denied", choice=choice)
            self._note("Your approval for %s was not sent because you stopped "
                       "this turn, so it was denied. It did not run."
                       % _cmd_label(head))
            if n == 0:
                self._forget_all("expired")
            return
        if n == 0:
            self._audit(head, "expired", choice=choice)
            self.job["status"] = ("that approval had already timed out — the "
                                  "command did not run")
            if choice == "approve":
                # E71: the click was accepted ({ok:true}; Quick Ask printed
                # "Approved.") yet nothing ran — say so in the reply, as the
                # TTL path does. A late Deny needs no note: nothing ran, as asked.
                self._note("Your approval for %s arrived when the agent was no "
                           "longer waiting for it, so it did not run."
                           % _cmd_label(head))
            self._forget_all("expired")
        else:
            self._audit(head, "user-" + choice)

    def _withdraw(self, head):
        """Take `head`'s card off the job BEFORE answering for it, so that
        /api/chat/approve (which only takes a click while the job shows that
        card) refuses a click instead of accepting one nobody will forward."""
        if self._shown is not None and self._shown == head.get("approval_id"):
            self._shown = None
            self.job["approval"] = None
            self.job["state"] = "running"

    def settle(self):
        """Expire the head past its TTL, answer policy-decided heads, publish."""
        while self.q:
            head = self.q[0]
            if head.get("_auto"):
                wire = head["_auto"]
                try:
                    wire_sent, n = self._send(wire)
                except RPCError:
                    if self.job.get("stop_requested"):
                        # After an owner Stop nothing becomes a card: serve
                        # still holds this entry, so deny everything it has
                        # pending (its own interrupt does the same).
                        self.q.pop(0)
                        self._audit(head, "auto-denied")
                        self._deny_all()
                        self._note("%s was asked for after you stopped this "
                                   "turn, so it was denied. It did not run."
                                   % _cmd_label(head))
                        continue
                    # serve answered and refused: nothing was resolved, so
                    # this entry is still the head of its queue too. Ask the
                    # owner instead (the policy path's old fail-open to ASK).
                    del head["_auto"]
                    self._audit(head, "asked")
                    continue
                except WSError as e:
                    self.q.pop(0)
                    self._audit(head, "respond-failed")
                    self._desync(f"approval failed: {e}")
                    break
                self.q.pop(0)
                stopped_now = wire_sent != wire   # an auto "once" a stop overtook
                wire = wire_sent
                self._audit(head, "auto-approved" if wire == "once"
                            else "auto-denied")
                self.job["status"] = (
                    ("auto-approved · " if wire == "once" else "blocked by policy · ")
                    + str((head.get("_policy") or {}).get("reason") or ""))
                if wire != "once":
                    # serve reports this to the agent as the owner's own
                    # denial ("Command denied by user"), and the owner never
                    # saw a card: the reply has to say who decided.
                    reason = _one_line((head.get("_policy") or {}).get("reason"), 160)
                    if stopped_now or ((head.get("_policy") or {}).get("reason")
                                       == "you stopped this turn"):
                        self._note("%s was asked for after you stopped this "
                                   "turn, so it was denied. It did not run."
                                   % _cmd_label(head))
                    else:
                        self._note("Your Trust policy blocked %s%s. It did not run, "
                               "and no approval card was shown."
                               % (_cmd_label(head),
                                  " (%s)" % reason if reason else ""))
                if n == 0:
                    self._forget_all("expired")
                continue
            if time.time() - head.get("_ts", 0) <= self.ttl():
                break
            # R2-10: a click /api/chat/approve accepted (with {ok:true}) since
            # the run loop last looked is honoured rather than overridden by
            # the TTL deny — serve's own wait outlasts ours, so its entry is
            # still the head there too. The card is withdrawn first, so no new
            # click can be accepted while the deny is on its way.
            aid = head.get("approval_id")
            self._withdraw(head)
            choice = self.job.get("pending_choice")
            if (choice in ("approve", "deny") and aid
                    and self.job.get("pending_approval_id") == aid):
                self.job.pop("pending_choice", None)
                self.job.pop("pending_approval_id", None)
                self.q.pop(0)
                self._forward(head, choice)
                continue
            # A click that slips in after this point is answered with
            # "arrived just after it timed out" (see answer()).
            self.job["approval_expired"] = aid
            self.q.pop(0)
            try:
                n = self._respond("deny")
            except WSError as e:
                self._audit(head, "respond-failed")
                self._desync(f"approval failed: {e}")
                break
            self._audit(head, "timeout")
            self.job["status"] = ("an approval timed out unanswered — the "
                                  "command was not run")
            # serve tells the agent the owner denied it; nobody answered.
            self._note("The approval for %s timed out unanswered, so it was "
                       "denied. It did not run." % _cmd_label(head))
            if n == 0:
                self._forget_all("expired")
        self._publish()


def run_turn(job, chat_meta, prompt, save_meta, source="hub"):
    """Drive one agent turn; mutates `job` dict in place as events stream.

    job fields consumed by the poll endpoint: state, text, status, approval,
    reply, ok. `chat_meta` carries serve_sid/serve_key persistence via
    save_meta() so the conversation resumes across turns and serve restarts.

    This function sets `state="done"` plus reply/ok when the turn ends, but it
    does NOT set `done`. server.py's _finish_chat_job publishes that, and only
    after the reply has been written to chats/<session>.json — `done` used to
    reach the UI first, so a reply could be shown as delivered and then fail to
    persist with nothing to say so (2026-09-10 audit A03).

    It also stamps three bookkeeping flags the chat fallback needs: `submit_sent`
    (the prompt.submit request left the socket), `submitted` (serve acknowledged
    it) and `tool_events` (how many tool.* events arrived). See A04. Once the
    prompt is in, `turn_deadline` is the wall-clock time the turn will be
    stopped at: TURN_TIMEOUT of agent time, plus every second a card waited
    for the owner.

    `source` is written to the serve session's `sessions.source` column in
    state.db (the gateway takes it verbatim: `str(params.get("source") or
    "tui")`). It stays "hub" for every real dashboard turn; server.py's
    prewarm-after-wake passes its own value so that synthetic turn can be told
    apart from genuine user activity in `_newest_external_turn_ts()`.

    `job["approval"]` is the approval card the owner is looking at: serve's
    approval.request payload plus a dashboard-minted `approval_id`. An answer
    reaches serve only through `pending_choice` + `pending_approval_id`, and
    only while that id is still the head of the queue — see _Approvals
    (2026-10-08 audit B01).
    """
    srv = ServeSession()
    notices = []          # things the owner must hear about, appended to the reply
    cfg = []              # serve's config, fetched at most once per turn

    def _cfg():
        if not cfg:
            cfg.append(_serve_config(srv))
        return cfg[0]

    approvals = None      # the turn's _Approvals, once prompt.submit is in

    def _end(reply, ok):
        if approvals is not None:
            approvals.close()     # nothing pending may outlive the turn
        job["approval"] = None
        job.update(reply=_with_notices(reply, notices), ok=ok, state="done")

    def _end_stopped(text, ended, told):
        job["stopped"] = True
        _end(stopped_reply(text, int(job.get("tool_events") or 0) > 0,
                           ended=ended, told=told,
                           approval_sent=bool(job.get("stop_after_approval"))), False)

    # The owner's Stop, once the prompt may have reached the agent: serve is
    # told through `session.interrupt` ONCE per turn, on this socket or, when
    # it is gone, on a fresh one (bounded: 5 s each). None = not tried yet.
    stopst = {"told": None, "sid": ""}

    def _interrupt_once():
        if stopst["told"] is not None or not stopst["sid"]:
            return bool(stopst["told"])
        told = False
        try:
            srv.call("session.interrupt", {"session_id": stopst["sid"]}, timeout=5)
            told = True
        except Exception as e:
            print(f"[hermes_rpc] session.interrupt({stopst['sid']}) for a stop "
                  f"failed: {e} — trying a fresh connection",
                  file=sys.stderr, flush=True)
            fresh = None
            try:
                fresh = ServeSession()
                fresh.call("session.interrupt", {"session_id": stopst["sid"]},
                           timeout=5)
                told = True
            except Exception as e2:
                print(f"[hermes_rpc] session.interrupt on a fresh connection "
                      f"failed too: {e2}", file=sys.stderr, flush=True)
            finally:
                if fresh is not None:
                    try:
                        fresh.close()
                    except Exception:
                        pass
        stopst["told"] = told
        return told

    if job.get("stop_requested") and not job.get("submit_claimed"):
        # Stopped before anything was sent: /api/chat/stop already gave the
        # job its ending. Do not even open a session.
        srv.close()
        return

    try:
        sid = chat_meta.get("serve_sid") or ""
        key = chat_meta.get("serve_key") or ""
        had_session = bool(sid or key)
        alive = False
        created = False       # this turn made a new serve session
        lost = ""             # why the stored agent session could not be used

        def _unaligned(s, have, want):
            """The session is on a model this Mac no longer serves and could
            not be moved: close it, so a new (seeded) one is used instead."""
            try:
                srv.call("session.close", {"session_id": s}, timeout=10)
            except WSError:
                pass
            return ("it was last used with %s, which is not the active model "
                    "(%s), and could not be moved to it" % (have, want))

        if sid:
            try:
                st = srv.call("session.status", {"session_id": sid}, timeout=10)
                alive = True
            except WSError:
                alive = False
            # Round-2 X-model-lifecycle-2: C112 aligned a session's model only
            # at a cold resume, but that alignment PINS a session override,
            # and serve's per-turn config sync skips every pinned session — so
            # after a model switch each conversation resumed since serve
            # started kept naming the old model, and the pinned model server
            # answered every turn with a 400. Compare on every turn (one
            # string compare; config.get once per turn). A session that is
            # mid-turn is left alone: serve refuses a switch then, and closing
            # it would kill that turn.
            if alive and _status_running(st) is not True:
                have = _status_model(st)
                if have:
                    ok_model, have, want = _align_session_model(
                        srv, sid, {"info": {"model": have}}, _cfg())
                    if not ok_model:
                        lost = _unaligned(sid, have, want)
                        alive = False
        if not alive and key and not lost:
            try:
                res = srv.call("session.resume", {"session_id": key},
                               timeout=20)
                sid = res.get("session_id") or sid
                alive = bool(sid)
                if alive:
                    ok_model, have, want = _align_session_model(srv, sid, res,
                                                                _cfg())
                    if not ok_model:
                        lost = _unaligned(sid, have, want)
                        alive = False
            except WSError as e:
                alive = False
                lost = "the agent could not resume it (%s)" % _one_line(e, 120)
                # 2026-10-21 audit C23: this fell through to a brand-new
                # session with nothing in the log, on the job or in the
                # transcript — the model answered with no memory while the
                # dashboard still showed the whole conversation.
                print(f"[hermes_rpc] session.resume({key}) failed: {e} — "
                      "starting a new agent session for this conversation",
                      file=sys.stderr, flush=True)
        if not alive:
            params = {"title": chat_meta.get("title") or "Hub chat",
                      "cwd": os.path.expanduser("~"), "source": source}
            # A branched conversation is born with the transcript it forked
            # from (aux_branch.br_seed_params). Ordinary chats get {} back and
            # take exactly the call they always took; a hook that raises is a
            # branch with no context, never a lost turn — but that used to be
            # a bare `except: pass` with nothing in the log and nothing on
            # chat_meta, so a branch silently starting with no memory of its
            # source was indistinguishable from a normal one until the user
            # noticed. Log it, and for a branch specifically (chat_meta has
            # `forked_from` — set by aux_branch, just a dict key here, no
            # import needed) stamp `seeded` on chat_meta; save_meta() below
            # persists it so aux_branch's tree/node views can report it.
            if SEED_HOOK is not None:
                try:
                    seed_update = SEED_HOOK(chat_meta) or {}
                except Exception as e:
                    print(f"[hermes_rpc] SEED_HOOK raised ({type(e).__name__}: "
                          f"{e}) — session created with no seeded context",
                          file=sys.stderr, flush=True)
                    seed_update = {}
                params.update(seed_update)
                if chat_meta.get("forked_from"):
                    chat_meta["seeded"] = bool(seed_update.get("messages"))
            # E70: a conversation with earlier replies but NO stored agent
            # session (written before the serve backend, or a save_meta()
            # that failed once) used to get a blank agent with no notice. Seed
            # it the same way. Not a Needs-you draft (`no_human`): each draft
            # gets a fresh, unseeded session by design (C38). Not a branch
            # whose SEED_HOOK already gave it messages (its normal first turn).
            if (not had_session and not job.get("no_human")
                    and not params.get("messages")
                    and _transcript_seed(chat_meta)):
                had_session = True
                lost = lost or "no agent session was stored for it"
            if had_session:
                # The conversation had an agent session and it is unusable
                # (C23 / C112): carry the conversation's words into the new
                # one, and say so — whoever seeded it. A branch's SEED_HOOK
                # supplies its own messages, and that used to skip the notice
                # too, so a branch's reply read as continuous with its tool
                # history gone (2026-10-21 round 2).
                if not params.get("messages"):
                    seed = _transcript_seed(chat_meta)
                    if seed:
                        params["messages"] = seed
                if lost or params.get("messages"):
                    notices.append(
                        "This conversation's agent session could not be used "
                        "— %s — so this reply comes from a new one. %s"
                        % (lost or "it could not be found",
                           "It was given the earlier messages as text; tool "
                           "results from before this point were not carried "
                           "over." if params.get("messages") else
                           "It does not have the earlier messages."))
            res = srv.call("session.create", params, timeout=20)
            sid = res.get("session_id") or ""
            key = res.get("stored_session_id") or res.get("session_key") or ""
            if not sid:
                raise WSError("session.create returned no session_id")
            created = True
        # The owner's Stop and the submit are decided under one lock: past
        # this point a stop goes through session.interrupt below; before it,
        # nothing is submitted and the session is not even saved (a stopped
        # turn's empty new session must not replace a later turn's).
        if not _claim_submit(job):
            if created:
                try:
                    srv.call("session.close", {"session_id": sid}, timeout=10)
                except WSError:
                    pass
            return
        stopst["sid"] = sid
        chat_meta["serve_sid"], chat_meta["serve_key"] = sid, key
        save_meta()

        # `submit_sent` is set from inside srv.call the moment the request has
        # been written to the socket — deliberately BEFORE the response is
        # confirmed. From here on the prompt may have reached the agent, so
        # server.py's _chat_worker must never re-run it through the one-shot
        # CLI (2026-09-10 audit A04). `submitted` is the confirmed form.
        def _sent():
            job["submit_sent"] = True

        try:
            submit = srv.call("prompt.submit", {"session_id": sid, "text": prompt},
                              timeout=30, on_sent=_sent)
        except RPCError as e:
            # serve answered and refused it (e.g. "subagent still running"):
            # nothing ran. Say what it said, instead of letting the chat
            # fallback report a dropped connection after submission
            # (2026-10-08 audit, P3 chat pipeline).
            if job.get("stop_requested"):
                notices.append("The agent did not take this message: %s"
                               % _one_line(e, 300))
                _end_stopped("", True, _interrupt_once())
                return
            _end("The agent did not take this message: %s" % _one_line(e, 300),
                 False)
            return
        job["submitted"] = True
        job["_submitted_ts"] = time.time()   # metrics P1.5: setup/serve TTFT split

        # 2026-10-21 audit C113: serve does not refuse a prompt for a session
        # that is already mid-turn. It queues it (by default interrupting the
        # live turn), answers {"status": "queued"}, and from then on every
        # event for the session comes to THIS socket — the live turn's last
        # deltas and its (interrupted) message.complete included, which used
        # to be taken as the answer to this message. A queued prompt runs as
        # the session's next turn, and every turn opens with message.start
        # (tui_gateway _run_prompt_submit), so nothing before that start is
        # ours. ("steered" folds this text into the live turn, whose complete
        # IS the answer.) /api/chat refuses a second send per conversation;
        # this covers a turn still running from before a dashboard restart.
        sst = str((submit or {}).get("status") or "")
        await_start = sst == "queued"
        if sst in ("queued", "steered"):
            print(f"[hermes_rpc] prompt.submit on a busy session {sid}: {sst}",
                  file=sys.stderr, flush=True)
            if await_start:
                job["status"] = "waiting for the agent to finish an earlier turn"

        text, status = "", ""
        approvals = _Approvals(srv, sid, job, notices, _cfg)
        pending_err, err_probe_at = None, 0.0
        # X-approval-flow-3: TURN_TIMEOUT bounds the AGENT's time. While a
        # card is in front of the owner the deadline moves with the clock
        # (each card is itself bounded by its TTL), so a desktop task with a
        # card per click is not cut off mid-way by the owner reading them.
        # `turn_deadline` is the live ceiling, for anyone waiting on the job
        # (aux_autoroute persists its answer after this turn's reply).
        deadline = time.time() + TURN_TIMEOUT
        job["turn_deadline"] = deadline
        last, card_up = time.time(), False
        stop_sent, stop_told, stop_deadline = False, False, None
        while True:
            now = time.time()
            if card_up and not stop_sent:
                deadline += now - last
                job["turn_deadline"] = deadline
            last = now
            if job.get("stop_requested") and not stop_sent:
                # The owner pressed Stop. Interrupt first (serve also denies
                # its own pending approvals), then deny ours through the
                # mirror exactly as the end of a turn does. request_stop
                # already discarded a click not yet forwarded, and _send
                # turns any later approving answer into a deny. Then give the
                # turn STOP_GRACE to end by itself — nothing extends that.
                stop_sent = True
                job["status"] = "stopping…"
                stop_told = _interrupt_once()
                approvals.close()
                card_up = False
                stop_deadline = now + STOP_GRACE if stop_told else now
            if stop_deadline is not None and deadline > stop_deadline:
                deadline = stop_deadline
                job["turn_deadline"] = deadline
            if now >= deadline:
                break
            choice = job.pop("pending_choice", None)
            if choice:
                approvals.answer(choice, job.pop("pending_approval_id", None))
            approvals.settle()
            card_up = approvals.card_up()
            if pending_err is not None and time.time() >= err_probe_at:
                if _turn_running(srv, sid) is not True:
                    if stop_sent:
                        _end_stopped(text, True, True)
                    else:
                        _end(pending_err, False)
                    return
                err_probe_at = time.time() + 5.0
            ev = srv.next_event(timeout=1.0)
            if ev is None:
                continue
            if ev.get("session_id") not in ("", sid):
                continue
            etype = ev.get("type") or ""
            payload = ev.get("payload") or {}
            if etype in ("tool.start", "tool.generating", "tool.complete"):
                # The agent has begun DOING things. Independently of
                # `submitted`, this is the signal that a re-run could repeat a
                # side effect (2026-09-10 audit A04).
                job["tool_events"] = int(job.get("tool_events") or 0) + 1
            if RECORDER_HOOK and etype in ("tool.start", "tool.complete"):
                try:
                    RECORDER_HOOK(sid, etype, payload)
                except Exception:
                    pass   # recorder must never break a turn
            if await_start and etype in ("message.delta", "message.complete",
                                         "approval.request"):
                # the earlier turn's words, ending and approvals (C113). Its
                # approvals are not ours to show: the interrupt denies them
                # (or, queued without one, serve's own timeout drops them
                # before our turn can start) — a card for one would sit at the
                # head of the mirror and hand a click to OUR next request.
                continue
            if etype == "message.start":
                if await_start:
                    await_start = False
                    pending_err = None   # the earlier turn's, not this one's
            elif etype == "message.delta":
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
            elif etype in BLOCKING_PROMPTS:
                # C115: these used to be dropped while serve held the agent
                # 120-300 s for an answer nobody could give.
                job["status"] = _answer_blocking_prompt(srv, etype, payload,
                                                        notices)
            elif etype == "approval.request":
                # P1.3: consult the graduated-permission policy. Sends only
                # "once"/"deny" upstream (never session/always), so hermes's
                # own allowlists never grow. decide() never raises; every
                # failure path falls through to ASK (fail-safe).
                v = _perm.decide(payload) if _perm else {"tier": "ask", "reason": ""}
                # auto / never are answered by the policy, ask is a card — all
                # through the one FIFO mirror (B01): a policy answer goes out
                # at once, any request behind a waiting card denies both
                # (X-approval-flow-6), and one whose delivery is unknown puts both queues
                # back in step instead of turning into a card for an entry
                # serve may already have resolved — a later click on that
                # card would land on the NEXT, unseen request.
                approvals.add(payload, v,
                              auto={"auto": "once", "never": "deny"}.get(v.get("tier")))
            elif etype == "message.complete":
                final = payload.get("text") or text
                st = str(payload.get("status") or "complete")
                if pending_err is not None:
                    notices.append("The agent reported: %s"
                                   % _one_line(pending_err, 300))
                if stop_sent and st != "complete":
                    # The turn the owner stopped ended. Keep what streamed —
                    # what was on screen — never serve's internal
                    # "Operation interrupted…" text in its place.
                    _end_stopped(text or (final if st != "interrupted" else ""),
                                 True, True)
                    return
                # 2026-10-21 audit C121: `status` is complete | interrupted |
                # error (tui_gateway _run_prompt_submit). Only a complete turn
                # is a success.
                if st == "interrupted":
                    _end((final + "\n\n" if final else "")
                         + "(The agent was interrupted before it finished "
                           "this reply.)", False)
                elif st == "error":
                    _end(final or "The agent hit an error before it could "
                                  "reply.", False)
                else:
                    _end(final or "(empty response)", True)
                return
            elif etype == "error":
                # C121: not every `error` ends the turn ("Could not switch to
                # configured model" is followed by the turn itself), so ask
                # serve before calling it over — see the probe above.
                pending_err = (payload.get("message") or payload.get("text")
                               or "agent error")
                job["status"] = "agent error: " + _one_line(pending_err, 160)
                # a terminal error clears serve's `running` just after it is
                # emitted; give that a moment before asking
                err_probe_at = time.time() + 1.5
        if stop_sent or job.get("stop_requested"):
            # Stopped by the owner and the turn did not end within
            # STOP_GRACE (or serve could not be told): end the job anyway.
            _end_stopped(text, False, _interrupt_once())
            return
        # Interrupt first: the reply may only say the session could still
        # finish in the background when serve could not be told to stop it.
        interrupted = False
        try:
            srv.call("session.interrupt", {"session_id": sid}, timeout=5)
            interrupted = True
        except WSError:
            pass
        _end(f"The agent took longer than {TURN_TIMEOUT}s and was stopped."
             + ("" if interrupted else
                " (It could not be told to stop, so its session may still "
                "finish in the background.)"), False)
    except Exception as e:
        # After an owner Stop that came once the prompt could have reached
        # the agent, EVERY way out ends the same: serve is told (once, on a
        # fresh connection if this one died), what streamed is kept and the
        # reply says the owner stopped it. Anything else propagates to
        # _chat_worker's "connection dropped" path as before.
        if not (job.get("stop_requested") and job.get("submit_claimed")
                and job.get("state") != "done"):
            raise
        print(f"[hermes_rpc] turn stopped by the owner ended on "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        told = _interrupt_once()
        _end_stopped(job.get("text") or "", False, told)
    finally:
        srv.close()
