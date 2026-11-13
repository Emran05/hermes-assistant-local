# aux_promotion.py — P3.2 (B2) Model Promotion Gate.
#
# exec'd into server.py's globals by the aux-module loader (sorted AFTER
# aux_metrics.py, so wrapping switch_model here captures the metrics-wrapped
# version and preserves the chain: gate-warning -> metrics load-watch ->
# original).  Uses these server.py globals: DATA, MODEL_URL, CHAT_JOBS,
# read_json, _model_registry, _model_downloaded, _hf_cache_dir, active_model,
# agent_paused, switch_model, models_payload, register_get, register_post.
# ZERO server.py edits.
#
# What it does:
#   * POST /api/models/drill {model_id} — runs a canned 6-case tool-calling
#     drill DIRECTLY against the mlx server (/v1/chat/completions, OpenAI
#     tools schema).  Deterministic (temperature 0), no agent, no approval
#     surface, synthetic tools that exist nowhere in hermes-agent.  If the
#     requested model is not the one being served, it coordinates a TEMPORARY
#     switch via the existing switch_model global (bootout->bootstrap), waits
#     for load, drills, then RESTORES the original model.  Refuses politely
#     while a chat job is running or the agent is paused.
#   * Results persist in ~/.hermes/dashboard/promotion.json (0600, atomic):
#     {model_id: {score, of, pass, cases:[...], drilled_at, parser, ...}}.
#   * models_payload() is rebound (exec-order wins) to decorate every roster
#     model with {drilled, drill_score, drill_of, drill_pass, drilling,
#     license_note} — license_note carries the "Built with Llama" attribution
#     the DEVPLAN risk table requires for Llama-family models.
#   * switch_model() is rebound to WARN (never block — the user's choice is
#     sovereign) when switching TO a model that failed or never ran the drill.
#
# Why the drill separates models honestly: mlx_lm's server owns tool-call
# parsing (tool_parser inferred from the chat template).  A model whose
# template can't carry tools (e.g. this Hermes-3-8B conversion ships a bare
# ChatML template) never even sees the tools array — the server drops it and
# the model freestyles prose that LOOKS like success.  The drill scores only
# real, server-parsed tool_calls, so deflection can't pass.

import os
import json
import time
import glob
import threading
import urllib.request
import urllib.error
import http.client as _pro_httpclient
import re as _pro_re
import datetime as _pro_datetime   # private alias — never rebind bare `datetime`

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
PRO_FILE = os.path.join(DATA, "promotion.json")
PRO_OF = 6                      # number of drill cases
PRO_THRESHOLD = 5               # promotion bar: >= 5/6
PRO_CHAT_URL = MODEL_URL.replace("/v1/models", "/v1/chat/completions")
PRO_CASE_TIMEOUT = 150          # s per completion call (covers on-demand load)
PRO_LOAD_BUDGET = 300           # s to wait for a model to come up after switch
PRO_MAX_TOKENS = 700
# E103: written BEFORE a temporary swap, removed once the owner's model is
# back. A dashboard that ends mid-drill (update, kickstart -k, crash, logout)
# finds it at the next start and puts the original model back.
PRO_RESTORE_FILE = os.path.join(DATA, "drill-restore.json")
PRO_BOOT_DELAY = 3              # s after load before the boot recovery runs

# C07.1: a case that raises one of these got NO ANSWER to judge — the model
# server refused the connection, timed out, crashed mid-reply or sent a body
# that is not JSON (URLError and socket timeouts are OSErrors). That is an
# infrastructure error, not a failed case: the run stops, `err` is set and no
# verdict is stored. The same three classes aux_evals.py's _EV_INFRA_EXC uses.
_PRO_INFRA_EXC = (OSError, _pro_httpclient.HTTPException, json.JSONDecodeError)

# Small static license map (first substring hit wins).  Llama-family models
# carry the "Built with Llama" attribution required by the Llama 3.1
# Community License (DEVPLAN risk table).
PRO_LICENSES = [
    ("llama",   "Built with Llama · Llama 3.1 Community License"),
    ("qwen",    "Apache-2.0"),
    ("mistral", "Apache-2.0"),
    ("gemma",   "Gemma Terms of Use"),
    ("phi-",    "MIT"),
    ("smollm",  "Apache-2.0"),
    ("glm",     "MIT"),
]


def _pro_license(mid):
    low = (mid or "").lower()
    for frag, note in PRO_LICENSES:
        if frag in low:
            return note
    return "license unverified — check the model card"


# --------------------------------------------------------------------------
# promotion.json — atomic, 0600
# --------------------------------------------------------------------------
def _pro_read():
    d = read_json(PRO_FILE, {})
    return d if isinstance(d, dict) else {}


def _pro_write(data):
    tmp = PRO_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PRO_FILE)


# --------------------------------------------------------------------------
# stage-1 style static probe: which tool parser (if any) mlx_lm infers from
# the model's chat template.  None => the server can never emit tool_calls
# for this model (it only warns and drops the tools array).
# --------------------------------------------------------------------------
def _pro_parser(mid):
    try:
        from mlx_lm.tokenizer_utils import _infer_tool_parser
    except Exception:
        return None
    tmpl = None
    try:
        snaps = glob.glob(os.path.join(_hf_cache_dir(mid), "snapshots", "*"))
        for snap in snaps:
            j = os.path.join(snap, "chat_template.jinja")
            if os.path.exists(j):
                with open(j) as f:
                    tmpl = f.read()
                break
            tc = os.path.join(snap, "tokenizer_config.json")
            if os.path.exists(tc):
                with open(tc) as f:
                    ct = json.load(f).get("chat_template")
                if isinstance(ct, list):     # named-template form
                    for ent in ct:
                        if isinstance(ent, dict) and ent.get("template"):
                            tmpl = ent["template"]
                            break
                elif isinstance(ct, str):
                    tmpl = ct
                break
    except Exception:
        return None
    if not tmpl:
        return None
    try:
        return _infer_tool_parser(tmpl)
    except Exception:
        return None


# --------------------------------------------------------------------------
# direct mlx-server completions (loopback http, urllib — aux_clip precedent)
# --------------------------------------------------------------------------
def _pro_completion(mid, messages, tools=None, max_tokens=PRO_MAX_TOKENS,
                    timeout=PRO_CASE_TIMEOUT):
    body = {"model": mid, "messages": messages,
            "temperature": 0, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        PRO_CHAT_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    # E20: server.py's proxy-free, redirect-free loopback opener (by name)
    opener = globals().get("local_urlopen") or urllib.request.urlopen
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _pro_msg(resp):
    try:
        return resp["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def _pro_calls(msg):
    """Normalize tool_calls -> [{id, name, args(dict|None), raw}]"""
    out = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        args = None
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str):
            try:
                args = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                args = None
        out.append({"id": tc.get("id") or "", "name": fn.get("name") or "",
                    "args": args, "raw": raw})
    return out


def _pro_leak(content):
    """Detect raw tool-call markup leaking into content (no server parser)."""
    c = content or ""
    return ("<tool_call>" in c or "<function=" in c or
            ('"arguments"' in c and '"name"' in c))


# --------------------------------------------------------------------------
# the drill: synthetic 3-tool schema (exists nowhere in hermes-agent) + 6 cases
# --------------------------------------------------------------------------
PRO_TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "City name"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "create_task",
        "description": "Add a task to the user's to-do list.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Task text"},
                "priority": {"type": "string",
                             "enum": ["low", "normal", "high"]}},
                "required": ["text", "priority"]}},
            "required": ["task"]}}},
    {"type": "function", "function": {
        "name": "run_terminal",
        "description": "Execute a shell command on the user's Mac and return "
                       "its output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The exact shell "
                        "command to execute"}},
            "required": ["command"]}}},
]

PRO_SYSTEM = (
    "You are Hermes, a local assistant agent running on the user's Mac. "
    "You have REAL, working tools available. When a request requires an "
    "action, you MUST call the appropriate tool — never describe, simulate, "
    "or predict a tool's output instead of actually calling it. When no tool "
    "is needed, just answer normally. If no available tool can do what is "
    "asked, say so plainly instead of pretending.")

PRO_REFUSE_RE = _pro_re.compile(
    r"(can.?t|cannot|unable|no\s.{0,28}tool|don.?t have|do not have|"
    r"not (?:able|possible|equipped|available)|lack|sorry|only have)",
    _pro_re.IGNORECASE)


def _pro_case_simple(mid):
    """1. must call a simple function when asked"""
    r = _pro_completion(mid, [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content": "Use the get_weather tool to check the "
                                    "current weather in Tokyo."}], PRO_TOOLS)
    m = _pro_msg(r)
    calls = _pro_calls(m)
    for c in calls:
        if c["name"] == "get_weather" and isinstance(c["args"], dict) \
                and "tokyo" in str(c["args"].get("city", "")).lower():
            return True, "called get_weather(city=%r)" % c["args"].get("city")
    if calls:
        return False, "wrong call: " + json.dumps(
            [{c['name']: c['raw']} for c in calls])[:200]
    if _pro_leak(m.get("content")):
        return False, "raw tool markup leaked into content (server has no " \
                      "parser for this model's template)"
    return False, "no tool_call — prose instead: " + \
        repr((m.get("content") or "")[:140])


def _pro_case_nested(mid):
    """2. must call with correct nested args"""
    r = _pro_completion(mid, [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content": "Add 'buy milk' to my task list as a "
                                    "high priority task."}], PRO_TOOLS)
    m = _pro_msg(r)
    calls = _pro_calls(m)
    for c in calls:
        if c["name"] == "create_task" and isinstance(c["args"], dict):
            task = c["args"].get("task")
            if isinstance(task, dict) and \
                    isinstance(task.get("text"), str) and task["text"].strip() \
                    and task.get("priority") == "high":
                return True, "nested args correct: " + json.dumps(task)[:120]
            return False, "create_task called but args not the required " \
                          "nested shape: " + json.dumps(c["args"])[:160]
    if calls:
        return False, "wrong call: " + json.dumps(
            [{c['name']: c['raw']} for c in calls])[:200]
    if _pro_leak(m.get("content")):
        return False, "raw tool markup leaked into content"
    return False, "no tool_call — prose instead: " + \
        repr((m.get("content") or "")[:140])


def _pro_case_restraint(mid):
    """3. must NOT call when a plain answer suffices"""
    r = _pro_completion(mid, [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content": "What is 2 + 2? Just tell me the "
                                    "number."}], PRO_TOOLS)
    m = _pro_msg(r)
    calls = _pro_calls(m)
    if calls:
        return False, "called a tool for trivia: " + \
            json.dumps([c["name"] for c in calls])
    content = m.get("content") or ""
    if "4" in content:
        return True, "plain answer, no tool_calls: " + repr(content[:80])
    return False, "no tool call but wrong/empty answer: " + repr(content[:120])


def _pro_case_chain(mid):
    """4. must chain: call -> read result -> second call (two-turn)"""
    msgs = [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content":
            "Check the current weather in Paris with get_weather. Based on "
            "the result, create a task reminding me what to pack: high "
            "priority if it is raining, normal priority if not."}]
    r1 = _pro_completion(mid, msgs, PRO_TOOLS)
    m1 = _pro_msg(r1)
    calls1 = _pro_calls(m1)
    first = next((c for c in calls1 if c["name"] == "get_weather"
                  and isinstance(c["args"], dict)
                  and "paris" in str(c["args"].get("city", "")).lower()), None)
    if first is None:
        if _pro_leak(m1.get("content")):
            return False, "turn 1: raw tool markup leaked into content"
        return False, "turn 1: no get_weather(Paris) call — " + \
            repr((m1.get("content") or "")[:120])
    # feed a synthetic tool result back (it is raining -> expect high priority)
    assistant = {"role": "assistant", "content": m1.get("content") or "",
                 "tool_calls": m1.get("tool_calls")}
    msgs = msgs + [assistant, {
        "role": "tool", "tool_call_id": first["id"] or "call_1",
        "name": "get_weather",
        "content": json.dumps({"city": "Paris", "temp_c": 12,
                               "conditions": "heavy rain"})}]
    r2 = _pro_completion(mid, msgs, PRO_TOOLS)
    m2 = _pro_msg(r2)
    calls2 = _pro_calls(m2)
    for c in calls2:
        if c["name"] == "create_task" and isinstance(c["args"], dict):
            blob = json.dumps(c["args"]).lower()
            if '"high"' in blob:
                return True, "chained: get_weather -> tool result (rain) -> " \
                             "create_task high priority"
            return False, "turn 2: create_task called but priority not " \
                          "'high' after a rain result: " + blob[:140]
    return False, "turn 2: no create_task after tool result — " + \
        repr((m2.get("content") or "")[:120])


def _pro_case_impossible(mid):
    """5. must refuse gracefully when no tool can do it"""
    r = _pro_completion(mid, [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content": "Set my bedroom thermostat to 68 "
                                    "degrees."}], PRO_TOOLS)
    m = _pro_msg(r)
    calls = _pro_calls(m)
    if calls:
        return False, "invented a tool call for an impossible request: " + \
            json.dumps([{c['name']: c['raw']} for c in calls])[:200]
    content = m.get("content") or ""
    if PRO_REFUSE_RE.search(content):
        return True, "graceful refusal, no tool_calls: " + repr(content[:120])
    return False, "no refusal language — may be faking ability: " + \
        repr(content[:140])


def _pro_case_deflection(mid):
    """6. the deflection probe — the instruction the 8B historically fakes.
    PASS = a real, server-parsed run_terminal tool_call carrying the exact
    sentinel command.  FAIL = prose pretending (including 'predicting' the
    output without calling)."""
    sentinel = "HERMES_GATE_7431"
    r = _pro_completion(mid, [
        {"role": "system", "content": PRO_SYSTEM},
        {"role": "user", "content":
            "Run this exact command in the terminal with the run_terminal "
            "tool right now: echo " + sentinel + " — then report the "
            "output."}], PRO_TOOLS)
    m = _pro_msg(r)
    calls = _pro_calls(m)
    for c in calls:
        if c["name"] == "run_terminal" and isinstance(c["args"], dict) \
                and sentinel in str(c["args"].get("command", "")):
            return True, "real tool_call: run_terminal(command=%r)" % \
                c["args"].get("command")
    if calls:
        return False, "wrong call: " + json.dumps(
            [{c['name']: c['raw']} for c in calls])[:200]
    content = m.get("content") or ""
    if _pro_leak(content):
        return False, "raw tool markup leaked into content — server has no " \
                      "parser, agent would receive prose"
    if sentinel in content:
        return False, "DEFLECTION: predicted the output without calling " \
                      "the tool: " + repr(content[:140])
    return False, "no tool_call — prose deflection: " + repr(content[:140])


PRO_CASES = [
    ("simple_call", "calls a simple function on request", _pro_case_simple),
    ("nested_args", "correct nested arguments", _pro_case_nested),
    ("restraint", "no tool call when none needed", _pro_case_restraint),
    ("chain", "reads a tool result, then second call", _pro_case_chain),
    ("impossible", "refuses gracefully on an impossible ask",
     _pro_case_impossible),
    ("deflection_probe", "really runs a command instead of faking it",
     _pro_case_deflection),
]


# --------------------------------------------------------------------------
# drill orchestration (bg thread; temporary model swap when needed)
# --------------------------------------------------------------------------
_PRO_LOCK = threading.Lock()
_PRO_STATE = {"running": None, "started": 0, "note": "", "last": None,
              "error": None}


def _pro_busy_jobs():
    try:
        return any(not v.get("done") for v in list(CHAT_JOBS.values()))
    except Exception:
        return False


def _pro_wait_ready(mid, budget_s):
    """Wait until the server answers a tiny completion for mid (i.e. the
    model is actually loaded and generating). Returns (ok, err)."""
    deadline = time.time() + budget_s
    last = ""
    while time.time() < deadline:
        try:
            left = max(10, min(180, deadline - time.time()))
            r = _pro_completion(
                mid, [{"role": "user",
                       "content": "Reply with the single word: ready"}],
                None, max_tokens=8, timeout=left)
            if r.get("choices"):
                return True, ""
        except Exception as e:
            last = type(e).__name__ + ": " + str(e)[:120]
        time.sleep(4)
    return False, last


def _pro_wake_serving():
    """C07.1: drilling the SERVING model (no switch) while it is asleep — an
    idle-suspend, or down without the marker — used to wait 120 s for a
    model nothing woke and fail with 'model never came up'. Wake it the way
    a chat turn does (agent_wake, resolved by name). Returns None when no
    wake was needed or it worked, else the reason it did not."""
    g = globals()
    try:
        asleep = g.get("agent_idle_suspended")
        paused = g.get("agent_paused")
        online = g.get("model_online")
        need = bool((callable(asleep) and asleep()) or
                    (callable(online) and not (callable(paused) and paused())
                     and not online()))
    except Exception:
        need = False
    wake = g.get("agent_wake")
    if not need or not callable(wake):
        return None
    _PRO_STATE["note"] = "waking the model"
    try:
        if wake(wait=True, timeout=PRO_LOAD_BUDGET):
            return None
        return "the model did not wake in time"
    except Exception as e:
        return "wake failed: " + type(e).__name__ + ": " + str(e)[:120]


def _pro_restore_original(orig, mid):
    """E19: put the owner's model back after a temporary swap — unless the
    owner took over while the drill ran. switch_model() on a PAUSED agent
    resumes it (that is the menu's rule), so restoring through it undid the
    owner's Pause a few seconds after it was pressed, and a switch the owner
    made mid-drill was reverted. Returns the record stored in last.restore:
      * the active model is no longer the drilled one -> nothing is touched,
        {skipped: "switched", active: <that model>};
      * paused or asleep -> only the original's FILES are re-activated
        (_activate_model: active-model, server args, Hermes model.default;
        nothing starts), so the next start loads the owner's model and the
        pause holds — {skipped: "paused"|"asleep", files_only: True};
      * otherwise switch_model(orig) and wait for it, as before."""
    g = globals()
    try:
        cur = active_model()
    except Exception:
        cur = None
    if cur != mid:
        return {"ok": True, "model": orig, "skipped": "switched", "active": cur,
                "error": None}
    try:
        paused = bool(agent_paused())
    except Exception:
        paused = False
    asleep = False
    try:
        fn = g.get("agent_idle_suspended")
        asleep = bool(callable(fn) and fn())
    except Exception:
        asleep = False
    if paused or asleep:
        act = g.get("_activate_model")
        if not callable(act):
            return {"ok": False, "model": orig, "files_only": True,
                    "skipped": "paused" if paused else "asleep",
                    "error": "no _activate_model to put the files back"}
        try:
            ok, why = act(orig)
        except Exception as e:
            ok, why = False, type(e).__name__ + ": " + str(e)[:160]
        return {"ok": bool(ok), "model": orig, "files_only": True,
                "skipped": "paused" if paused else "asleep", "error": why or None}
    rs = switch_model(orig)
    ok2, e2 = _pro_wait_ready(orig, PRO_LOAD_BUDGET)
    err = e2 or None
    if not (isinstance(rs, dict) and rs.get("ok")):
        err = str((rs or {}).get("error") or err or "switch back failed")[:200]
    return {"ok": bool(isinstance(rs, dict) and rs.get("ok") and ok2),
            "model": orig, "error": err}


def _pro_restore_mark(orig, mid):
    """E103: record the restore point before a temporary swap (0600, atomic)."""
    tmp = PRO_RESTORE_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"restore_to": orig, "candidate": mid, "at": time.time()}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PRO_RESTORE_FILE)


def _pro_restore_clear():
    try:
        os.remove(PRO_RESTORE_FILE)
    except OSError:
        pass


def _pro_boot_recover():
    """E103: a restore point left by a drill the dashboard did not live to
    finish. The candidate is still the assistant's model (active-model and
    Hermes model.default name it — for an uncensored candidate, with no
    acknowledgement), so put the original back through the normal path:
    switch_model when the model is running and not paused, otherwise the
    files only (_activate_model) so the next start loads the owner's model
    and a pause or sleep holds. Nothing is done when the owner has switched
    since. Returns the restore record (None when there was no restore point)."""
    try:
        with open(PRO_RESTORE_FILE) as f:
            rec = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        rec = None
    orig = rec.get("restore_to") if isinstance(rec, dict) else None
    cand = rec.get("candidate") if isinstance(rec, dict) else None
    if not (isinstance(orig, str) and orig.strip() and len(orig) <= 200
            and isinstance(cand, str) and cand.strip() and len(cand) <= 200):
        _pro_restore_clear()
        print("[promotion] drill restore point unreadable — removed", flush=True)
        return {"ok": False, "skipped": "unreadable", "model": None, "error": None}
    try:
        online = bool(globals().get("model_online", lambda: False)())
    except Exception:
        online = False
    if online:
        restore = _pro_restore_original(orig, cand)
    else:
        try:
            cur = active_model()
        except Exception:
            cur = None
        if cur != cand:
            restore = {"ok": True, "model": orig, "skipped": "switched",
                       "active": cur, "error": None}
        else:
            act = globals().get("_activate_model")
            try:
                ok, why = act(orig) if callable(act) else (False, "no _activate_model")
            except Exception as e:
                ok, why = False, type(e).__name__ + ": " + str(e)[:160]
            restore = {"ok": bool(ok), "model": orig, "files_only": True,
                       "skipped": "down", "error": why or None}
    try:
        still = active_model() == cand
    except Exception:
        still = True
    if not still:
        _pro_restore_clear()
    restore["after_restart"] = True
    print("[promotion] the dashboard restarted during a drill of %s — restore of %s: %s"
          % (cand, orig, json.dumps(restore)[:300]), flush=True)
    with _PRO_LOCK:
        if not _PRO_STATE.get("running"):
            _PRO_STATE.update(last={"model": cand, "score": 0, "of": PRO_OF,
                                    "pass": None, "restore": restore,
                                    "error": "the dashboard restarted during the "
                                             "drill — no verdict stored",
                                    "finished": time.time()})
    return restore


def _pro_boot_recover_later():
    time.sleep(PRO_BOOT_DELAY)
    try:
        _pro_boot_recover()
    except Exception as e:
        print("[promotion] drill restore after restart failed: %s: %s"
              % (type(e).__name__, e), flush=True)


def promotion_drill_run(mid):
    """The drill body — runs in a background thread."""
    t0 = time.time()
    orig = active_model()
    swapped = False
    results = []
    err = None
    restore = None
    try:
        _PRO_STATE["note"] = "preparing"
        if mid != orig:
            _PRO_STATE["note"] = "switching to " + mid
            try:
                _pro_restore_mark(orig, mid)    # E103: before anything moves
            except OSError as e:
                raise RuntimeError("could not record the restore point (%s) — "
                                   "nothing was switched" % e)
            sw = switch_model(mid)          # metrics-wrapped chain preserved
            # a switch that wrote the candidate's files but could not start
            # it still left the candidate active: restore that too (E19)
            try:
                swapped = active_model() == mid
            except Exception:
                swapped = False
            if not (isinstance(sw, dict) and sw.get("ok")):
                raise RuntimeError("switch failed: " +
                                   str((sw or {}).get("error")))
            swapped = True
        else:
            why = _pro_wake_serving()
            if why:
                raise RuntimeError("model never came up: " + why)
        _PRO_STATE["note"] = "waiting for model load"
        ok, e = _pro_wait_ready(mid, PRO_LOAD_BUDGET if swapped else 120)
        if not ok:
            raise RuntimeError("model never came up: " + e)
        for cid, label, fn in PRO_CASES:
            _PRO_STATE["note"] = "case: " + cid
            c0 = time.time()
            try:
                passed, detail = fn(mid)
            except _PRO_INFRA_EXC as e:
                # no answer to judge (C07.1) — stop, store no verdict
                raise RuntimeError("model server error in case %s, no verdict "
                                   "stored: %s: %s" % (cid, type(e).__name__,
                                                       str(e)[:160]))
            except Exception as e:
                passed, detail = False, "case error: " + type(e).__name__ + \
                    ": " + str(e)[:160]
            results.append({"id": cid, "label": label, "pass": bool(passed),
                            "detail": detail,
                            "ms": round((time.time() - c0) * 1000)})
    except Exception as e:
        err = type(e).__name__ + ": " + str(e)[:200]
    finally:
        if swapped:
            _PRO_STATE["note"] = "restoring " + orig
            try:
                restore = _pro_restore_original(orig, mid)
            except Exception as e:
                restore = {"ok": False, "model": orig,
                           "error": type(e).__name__ + ": " + str(e)[:160]}
        if mid != orig:
            # E103: the restore point goes once the drilled model is no longer
            # the active one; a restore that could not move it keeps it for
            # the next dashboard start to retry
            try:
                gone = active_model() != mid
            except Exception:
                gone = False
            if gone:
                _pro_restore_clear()
    score = sum(1 for c in results if c["pass"])
    if err is None and results:
        rec = {"score": score, "of": PRO_OF, "pass": score >= PRO_THRESHOLD,
               "cases": results,
               "drilled_at": _pro_datetime.datetime.now(
                   _pro_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "parser": _pro_parser(mid),
               "license_note": _pro_license(mid),
               "swap_used": swapped,
               "total_s": round(time.time() - t0, 1)}
        if restore is not None:
            rec["restore"] = restore
        try:
            data = _pro_read()
            data[mid] = rec
            _pro_write(data)
        except Exception as e:
            err = "result write failed: " + type(e).__name__ + ": " + str(e)
    # metrics breadcrumb (aux_metrics loads before us; never raise)
    try:
        mrec = globals().get("metrics_record")
        if callable(mrec):
            mrec("gate", model=mid, score=score, of=PRO_OF,
                 ok=(err is None), swapped=swapped,
                 ms=round((time.time() - t0) * 1000))
        mcnt = globals().get("metrics_count")
        if callable(mcnt):
            mcnt("gate_runs", 1)
    except Exception:
        pass
    with _PRO_LOCK:
        _PRO_STATE.update(running=None, note="",
                          error=err,
                          last={"model": mid, "score": score, "of": PRO_OF,
                                # an aborted run is no verdict either way
                                "pass": (score >= PRO_THRESHOLD) if err is None
                                else None, "error": err,
                                "restore": restore,
                                "finished": time.time()})


def _pro_run_held(mid):
    """C07: keep the model up — and count the drill as owner activity — for the
    whole run, restore included. Without the hold idle_suspend_loop booted the
    model out mid-drill and every remaining case was stored as a failure."""
    hold = globals().get("model_hold")
    if hold is None:
        return promotion_drill_run(mid)
    with hold("drill"):
        return promotion_drill_run(mid)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _pro_drill_post(ctx):
    body = ctx.body or {}
    # E123: a wrong-typed id is a client error (400), never an AttributeError
    if any(body.get(k) is not None and not isinstance(body.get(k), str)
           for k in ("model_id", "id")):
        return ({"ok": False, "error": "model_id must be a string"}, 400)
    mid = (body.get("model_id") or body.get("id") or "").strip()
    if not mid:
        return ({"ok": False, "error": "model_id required"}, 400)
    if not any(m["id"] == mid for m in _model_registry()):
        return {"ok": False, "error": "unknown model"}
    if not _model_downloaded(mid):
        return {"ok": False, "error": "model not downloaded yet — download "
                                      "it first, then drill"}
    if agent_paused():
        return {"ok": False, "error": "the agent is paused — resume it "
                                      "before running the drill"}
    if _pro_busy_jobs():
        return {"ok": False, "error": "a chat turn is running right now — "
                                      "try again when the agent is idle"}
    # E117: an eval run and a drill exclude each other. The check and the
    # claim happen under aux_evals' _EV_LOCK (by name), the lock evals_run
    # holds while it checks _PRO_STATE and claims its own run.
    evl = globals().get("_EV_LOCK")
    evs = globals().get("_EV_STATE")
    if evl is not None:
        evl.acquire()
    try:
        if isinstance(evs, dict) and evs.get("running"):
            return {"ok": False, "error": "the eval suite is running right now — "
                                          "try again when it has finished"}
        with _PRO_LOCK:
            if _PRO_STATE.get("running"):
                return {"ok": False, "error": "a drill is already running",
                        "model": _PRO_STATE["running"]}
            _PRO_STATE.update(running=mid, started=time.time(),
                              note="starting", error=None)
    finally:
        if evl is not None:
            evl.release()
    threading.Thread(target=_pro_run_held, args=(mid,),
                     daemon=True).start()
    return {"ok": True, "status": "running", "model": mid,
            "swap": mid != active_model(),
            "note": "temporary model switch + restore — a few minutes"
            if mid != active_model() else "drilling the serving model"}


def _pro_drill_get(ctx):
    mid = ctx.q1("id")
    data = _pro_read()
    out = {"ok": True, "running": _PRO_STATE.get("running"),
           "note": _PRO_STATE.get("note") or "",
           "last": _PRO_STATE.get("last"), "error": _PRO_STATE.get("error"),
           "threshold": PRO_THRESHOLD, "of": PRO_OF}
    if mid:
        out["result"] = data.get(mid)
    else:
        out["results"] = data
    return out


register_post("/api/models/drill", _pro_drill_post)
# E103: a drill the previous dashboard process did not finish
if os.path.exists(PRO_RESTORE_FILE) and not globals().get("_pro_boot_checked"):
    globals()["_pro_boot_checked"] = True
    threading.Thread(target=_pro_boot_recover_later, name="drill-restore",
                     daemon=True).start()
register_get("/api/models/drill", _pro_drill_get)


# --------------------------------------------------------------------------
# rebinds (guarded so a re-exec can never wrap-of-wrap) — exec-order wins:
# models_payload gains drill metadata; switch_model gains the non-blocking
# gate warning.
# --------------------------------------------------------------------------
if not globals().get("_pro_wrapped"):
    globals()["_pro_wrapped"] = True

    _pro_orig_models_payload = models_payload

    def models_payload():
        out = _pro_orig_models_payload()
        try:
            data = _pro_read()
            running = _PRO_STATE.get("running")
            for m in out.get("models", []):
                rec = data.get(m.get("id"))
                m["drilled"] = bool(rec)
                m["drill_score"] = rec.get("score") if rec else None
                m["drill_of"] = rec.get("of", PRO_OF) if rec else PRO_OF
                m["drill_pass"] = rec.get("pass") if rec else None
                m["drilling"] = (m.get("id") == running)
                m["license_note"] = _pro_license(m.get("id"))
            out["drill_running"] = running
            out["drill_threshold"] = PRO_THRESHOLD
        except Exception:
            pass
        return out

    _pro_orig_switch_model = switch_model    # the metrics-wrapped version

    def switch_model(mid):
        out = _pro_orig_switch_model(mid)
        # review R4 (E103): a switch made while no drill runs is the owner's
        # choice (or the boot recovery's own restore) — a left-over restore
        # point must not revert it at some later dashboard start
        try:
            if isinstance(out, dict) and out.get("ok") and not _PRO_STATE.get("running"):
                _pro_restore_clear()
        except Exception:
            pass
        try:
            if isinstance(out, dict) and out.get("ok"):
                rec = _pro_read().get(mid)
                if rec is None:
                    out["warning"] = ("this model has not passed the "
                                      "tool-call drill yet — run the drill "
                                      "to verify agentic reliability")
                elif not rec.get("pass"):
                    out["warning"] = ("this model failed the tool-call "
                                      "drill (%s/%s) — agentic features may "
                                      "silently fail"
                                      % (rec.get("score"),
                                         rec.get("of", PRO_OF)))
        except Exception:
            pass
        return out
