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
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
            sw = switch_model(mid)          # metrics-wrapped chain preserved
            if not (isinstance(sw, dict) and sw.get("ok")):
                raise RuntimeError("switch failed: " +
                                   str((sw or {}).get("error")))
            swapped = True
        _PRO_STATE["note"] = "waiting for model load"
        ok, e = _pro_wait_ready(mid, PRO_LOAD_BUDGET if swapped else 120)
        if not ok:
            raise RuntimeError("model never came up: " + e)
        for cid, label, fn in PRO_CASES:
            _PRO_STATE["note"] = "case: " + cid
            c0 = time.time()
            try:
                passed, detail = fn(mid)
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
                rs = switch_model(orig)
                ok2, e2 = _pro_wait_ready(orig, PRO_LOAD_BUDGET)
                restore = {"ok": bool(isinstance(rs, dict) and rs.get("ok")
                                      and ok2),
                           "model": orig, "error": e2 or None}
            except Exception as e:
                restore = {"ok": False, "model": orig,
                           "error": type(e).__name__ + ": " + str(e)[:160]}
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
                                "pass": score >= PRO_THRESHOLD, "error": err,
                                "restore": restore,
                                "finished": time.time()})


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _pro_drill_post(ctx):
    body = ctx.body or {}
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
    with _PRO_LOCK:
        if _PRO_STATE.get("running"):
            return {"ok": False, "error": "a drill is already running",
                    "model": _PRO_STATE["running"]}
        _PRO_STATE.update(running=mid, started=time.time(),
                          note="starting", error=None)
    threading.Thread(target=promotion_drill_run, args=(mid,),
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
