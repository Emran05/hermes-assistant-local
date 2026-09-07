#!/usr/bin/env python3
"""decode_bench.py — decode tok/s and TTFT for one Hermes model lane.

Promoted from the session-scratch `baseline.py` / `baseline_next.py` that
produced the tables in docs/plans/post-v1-baseline.md (2026-09-04). Same
prompts, same shared ~6.5k-token prefix, same phase order, so a run today is
directly comparable to the numbers recorded there.

Two modes:

  * default — measure the server that is ALREADY serving the chosen lane
    (:8080 primary, :8081 background). Loads nothing extra; if the lane is
    asleep the script says so and exits instead of waking it.

  * --restart-with none,2,3,4 — **AC POWER ONLY, never the default.** Starts a
    PRIVATE copy of the model server on a spare port (8090) once per MTP draft
    block size, with the exact flags mlx-server.sh builds for the roster entry,
    measures, then kills it. The launcher is copied to a temp file whose name
    does NOT contain "mlx-vlm-launch", because the dashboard identifies model
    servers with `pgrep -f "mlx_lm server|mlx-vlm-launch"` (server.py
    `_mlx_proc_alive` / `_mlx_footprint_gb`) — the bench server must stay
    invisible to idle-suspend and the memory guard. This is how the block-size
    table in post-v1-baseline.md was produced.

BATTERY RULE (CLAUDE.md, docs/plans/post-v1-backlog.md): the model services are
on-demand. Every mode that generates refuses to run off AC power unless
--i-am-on-ac is passed, and --unload-after boots the lane back out afterwards.
--dry-run prints the plan and sends nothing.

Discovery is from the repo's own configuration, never hard-coded session paths:
GET /api/models on the dashboard (127.0.0.1:7788, $DASH_PORT honoured) with a
fallback to ~/.hermes/dashboard/{active-model,bg-model,models.json,server-backend}.

Results: a table on stdout plus one JSON line per phase appended to --out
(default ~/.hermes/bench/results.jsonl, created on demand). Stdlib only.

Usage:
  tools/bench/decode_bench.py --runs 3
  tools/bench/decode_bench.py --lane bg --runs 2
  tools/bench/decode_bench.py --restart-with none,3 --i-am-on-ac   # AC only
  tools/bench/decode_bench.py --dry-run
"""
import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = os.path.expanduser("~")
DASH_DIR = os.path.join(HOME, ".hermes", "dashboard")
LOG_DIR = os.path.join(HOME, ".hermes", "logs")
DEFAULT_OUT = os.path.join(HOME, ".hermes", "bench", "results.jsonl")
VLM_PY = os.path.join(HOME, ".hermes", "mlx-vlm-venv", "bin", "python")
LAUNCHER = os.path.join(REPO, "mlx-vlm-launch.py")

# Lane facts mirror mlx-server.sh / mlx-server-bg.sh / install-services.sh.
LANES = {
    "primary": {"port": 8080, "label": "com.hermes.mlx-server",
                "log": os.path.join(LOG_DIR, "mlx-server.log"),
                "model_file": os.path.join(DASH_DIR, "active-model"),
                "default_model": "mlx-community/Qwen3.8-27B-4bit"},
    "bg": {"port": int(os.environ.get("BG_PORT", "8081")), "label": "com.hermes.mlx-bg",
           "log": os.path.join(LOG_DIR, "mlx-bg.log"),
           "model_file": os.path.join(DASH_DIR, "bg-model"),
           "default_model": "mlx-community/Qwen3.5-9B-4bit"},
}

# --- prompts (identical to the scratch baseline, do not edit casually) -------
# Changing any of these invalidates comparison with docs/plans/post-v1-baseline.md.
PARA = ("Hermes keeps a running notebook of the day: which folders were touched, which reminders "
        "moved, what the calendar looks like after lunch, and which messages still need a reply. "
        "Every entry is timestamped and tagged with the tool that produced it so the assistant can "
        "explain its reasoning later without re-running anything. ")
PREFIX = "You are Hermes, a careful local assistant. Context notebook follows.\n\n" + PARA * 90
COLD_Q = "In one sentence, what does the notebook track?"
WARM_Q = "In one sentence, why are entries timestamped?"
PROSE_Q = ("Write a 180-word plain-prose note to a colleague summarizing why local-first software "
           "matters for privacy. No lists, no headings.")
CODE_Q = ("Write a Python function `top_k(words, k)` that returns the k most frequent words with "
          "counts, with a docstring and one usage example. Code only.")
GEN_MAX = 260

# mlx-vlm's own completion line, e.g.
#   Request completed: endpoint=/chat/completions model=... prompt_tokens=26925
#   generated_tokens=153 elapsed=7.092s prefill=12238.0 tok/s decode=32.7 tok/s ...
SRV_RE = re.compile(r"prompt_tokens=(\d+) generated_tokens=(\d+) elapsed=([\d.]+)s "
                    r"prefill=([\d.]+) tok/s decode=([\d.]+) tok/s")


def log(*a):
    print(*a, flush=True)


# --- discovery ---------------------------------------------------------------
def dash_base():
    """Dashboard base URL, the way launch-dashboard.sh resolves it."""
    env = os.environ.get("HERMES_DASH_URL")
    if env:
        return env.rstrip("/")
    return "http://127.0.0.1:%s" % os.environ.get("DASH_PORT", "7788")


def fetch_json(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def roster_from_disk():
    """models.json as _model_registry() would read it (list of roster entries)."""
    try:
        with open(os.path.join(DASH_DIR, "models.json")) as f:
            return json.load(f).get("models") or []
    except Exception:
        return []


def discover(lane):
    """Resolve {url, model, entry, source} for a lane without waking anything."""
    cfg = LANES[lane]
    payload = fetch_json(dash_base() + "/api/models")
    model, entry, source = "", {}, "files"
    if payload:
        source = "dashboard"
        if lane == "primary":
            model = payload.get("active") or ""
        else:
            model = ((payload.get("bg") or {}).get("model") or "")
        for m in payload.get("models") or []:
            if m.get("id") == model:
                entry = m
                break
    if not model:
        model = read_file(cfg["model_file"]) or cfg["default_model"]
    if not entry:
        for m in roster_from_disk():
            if m.get("id") == model:
                entry = m
                break
    if lane == "primary" and not entry:
        # last resort: what mlx-server.sh is actually running with right now
        try:
            with open(os.path.join(DASH_DIR, "server-backend")) as f:
                entry = json.load(f)
        except Exception:
            entry = {}
    return {"lane": lane, "url": "http://127.0.0.1:%d" % cfg["port"], "model": model,
            "entry": entry, "source": source, "log": cfg["log"], "label": cfg["label"]}


def online(base, timeout=2):
    try:
        urllib.request.urlopen(base + "/v1/models", timeout=timeout).read()
        return True
    except Exception:
        return False


# --- power / process guards --------------------------------------------------
def on_ac():
    """True when pmset reports AC power. Unknown (pmset missing) counts as NOT AC."""
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return False
    return "AC Power" in out


def require_ac(args, what):
    if args.i_am_on_ac or on_ac():
        return
    sys.exit("refusing to %s off AC power (battery rule). Plug in, or pass "
             "--i-am-on-ac if pmset is lying." % what)


def model_server_running():
    """The dashboard's own definition of 'a model server exists' (server.py
    _mlx_proc_alive). The bench server is deliberately NOT matched by it."""
    try:
        return bool(subprocess.run(["pgrep", "-f", "mlx_lm server|mlx-vlm-launch"],
                                   capture_output=True, text=True, timeout=5).stdout.strip())
    except Exception:
        return True


def footprint_gb(pid):
    try:
        out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return None
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*(GB|MB)", out)
    if not m:
        return None
    return float(m.group(1)) if m.group(2) == "GB" else round(float(m.group(1)) / 1024, 2)


# --- measurement -------------------------------------------------------------
def post_stream(base, model, messages, max_tokens, timeout=300, temperature=0):
    """One streaming /v1/chat/completions call; client-side TTFT + decode rate."""
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens,
                       "temperature": temperature, "stream": True}).encode()
    req = urllib.request.Request(base + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    n = 0
    text = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            try:
                d = json.loads(payload)
            except Exception:
                continue
            delta = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                if first is None:
                    first = time.time()
                n += 1
                text.append(delta)
    t1 = time.time()
    return {"ttft_s": round(first - t0, 3) if first else None,
            "total_s": round(t1 - t0, 3), "chunks": n,
            "client_tps": round(n / (t1 - first), 1) if (first and t1 > first) else None,
            "head": "".join(text)[:80]}


def server_stats(logpath, since_pos):
    """The server's own 'Request completed' numbers written after since_pos."""
    try:
        with open(logpath, "rb") as f:
            f.seek(since_pos)
            chunk = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    return [{"prompt_tokens": int(m[1]), "generated_tokens": int(m[2]), "elapsed_s": float(m[3]),
             "prefill_tps": float(m[4]), "server_tps": float(m[5])}
            for m in SRV_RE.finditer(chunk)]


def logpos(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def measure(base, model, logpath, runs, want_cold, variant, meta):
    """The baseline.py phase sequence: warmup, cold TTFT, warm TTFT, N x prose,
    N x code. Returns one record per measured phase (warmup is not recorded)."""
    recs = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    def one(phase, messages, max_tokens):
        pos = logpos(logpath)
        r = post_stream(base, model, messages, max_tokens)
        srv = server_stats(logpath, pos)
        s = srv[-1] if srv else {}
        rec = {"ts": stamp, "script": "decode_bench", "phase": phase, "variant": variant,
               "model": model, "url": base, "ttft_s": r["ttft_s"], "total_s": r["total_s"],
               "chunks": r["chunks"], "client_tps": r["client_tps"],
               "server_tps": s.get("server_tps"), "prefill_tps": s.get("prefill_tps"),
               "prompt_tokens": s.get("prompt_tokens"),
               "generated_tokens": s.get("generated_tokens")}
        rec.update(meta)
        recs.append(rec)
        log("  %-6s ttft %-7s client %-6s server %-6s prompt_tok %s"
            % (phase, rec["ttft_s"], rec["client_tps"], rec["server_tps"], rec["prompt_tokens"]))
        return rec

    log("  warmup (absorbs lazy init: Metal compile, drafter) — not recorded")
    post_stream(base, model, [{"role": "user", "content": "Say OK."}], 4)

    if want_cold:
        one("cold", [{"role": "system", "content": PREFIX},
                     {"role": "user", "content": COLD_Q}], 24)
    one("warm", [{"role": "system", "content": PREFIX},
                 {"role": "user", "content": WARM_Q}], 24)
    for _ in range(runs):
        one("prose", [{"role": "user", "content": PROSE_Q}], GEN_MAX)
    for _ in range(runs):
        one("code", [{"role": "user", "content": CODE_Q}], GEN_MAX)
    return recs


# --- private bench server (--restart-with) -----------------------------------
def spawn_bench_server(model, entry, block, port, venv_py, logpath, timeout=600):
    """Launch mlx-vlm-launch.py (renamed copy) with the flags mlx-server.sh
    builds. Returns (proc, logfile). Caller must kill_server()."""
    tmpdir = tempfile.mkdtemp(prefix="hermes-bench-")
    launcher = os.path.join(tmpdir, "vlm-bench-launch.py")   # name must NOT match the dashboard pgrep
    shutil.copy2(LAUNCHER, launcher)
    args = [venv_py, launcher, "--model", model, "--host", "127.0.0.1", "--port", str(port),
            "--max-tokens", "1024", "--trust-remote-code"]
    draft = entry.get("draft_model") or ""
    if block != "none":
        if not draft:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError("roster entry for %s has no draft_model — cannot bench block %s"
                               % (model, block))
        args += ["--draft-model", draft, "--draft-kind", entry.get("draft_kind") or "mtp",
                 "--draft-block-size", str(block)]
    env = dict(os.environ, APC_ENABLED="1",
               APC_EXACT_CACHE_ENTRIES=os.environ.get("APC_EXACT_CACHE_ENTRIES", "6"))
    lf = open(logpath, "wb")
    t0 = time.time()
    p = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT, env=env,
                         start_new_session=True)
    base = "http://127.0.0.1:%d" % port
    while True:
        if p.poll() is not None:
            lf.close()
            raise RuntimeError("bench server died during load — see %s" % logpath)
        if online(base):
            break
        if time.time() - t0 > timeout:
            kill_server(p)
            lf.close()
            raise RuntimeError("bench server load timeout after %ds" % timeout)
        time.sleep(1)
    return p, lf, round(time.time() - t0, 1), tmpdir


def kill_server(p):
    if p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        p.wait(timeout=25)
    except Exception:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass


# --- output ------------------------------------------------------------------
def append_jsonl(path, recs):
    if not recs:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    log("\nappended %d line(s) to %s" % (len(recs), path))


def med(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def table(recs):
    if not recs:
        log("no measurements")
        return
    log("")
    hdr = ("variant", "phase", "n", "ttft_s", "client tok/s", "server tok/s", "prompt_tok")
    rows = []
    seen = []
    for r in recs:
        key = (r["variant"], r["phase"])
        if key not in seen:
            seen.append(key)
    for variant, phase in seen:
        g = [r for r in recs if r["variant"] == variant and r["phase"] == phase]
        rows.append((str(variant), phase, str(len(g)), str(med([r["ttft_s"] for r in g])),
                     str(med([r["client_tps"] for r in g])), str(med([r["server_tps"] for r in g])),
                     str(g[-1].get("prompt_tokens"))))
    w = [max(len(hdr[i]), max(len(r[i]) for r in rows)) for i in range(len(hdr))]
    log("  ".join(h.ljust(w[i]) for i, h in enumerate(hdr)))
    log("  ".join("-" * w[i] for i in range(len(hdr))))
    for r in rows:
        log("  ".join(r[i].ljust(w[i]) for i in range(len(hdr))))
    log("\n(median of n runs; treat differences under ~5%% as noise — single runs are noisy)")


def unload(lane):
    """The repo's own way to stop an on-demand lane: bootout the launchd job
    (KeepAlive means a plain kill would come back), then leave the dashboard's
    idle-suspend marker so the next real turn transparently wakes it."""
    cfg = LANES[lane]
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, cfg["label"])],
                   capture_output=True, text=True)
    time.sleep(3)
    if lane == "primary":
        try:
            with open(os.path.join(DASH_DIR, "agent-idle-suspended"), "w") as f:
                f.write(str(int(time.time())))
            pausef = os.path.join(DASH_DIR, "agent-paused")
            if os.path.exists(pausef):
                os.remove(pausef)
        except Exception as e:
            log("could not write the idle marker: %s" % e)
    log("unloaded %s; still running: %s"
        % (cfg["label"], "yes (check by hand)" if model_server_running() else "no"))


def main():
    ap = argparse.ArgumentParser(
        description="Decode tok/s + TTFT for a Hermes model lane (see tools/bench/README.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="BATTERY RULE: AC power only, and unload the model when you are done:\n"
               "  launchctl bootout gui/$(id -u)/com.hermes.mlx-server\n"
               "  (or pass --unload-after, which also writes the idle-suspend marker)")
    ap.add_argument("--lane", choices=sorted(LANES), default="primary",
                    help="which model lane to measure (default: primary :8080)")
    ap.add_argument("--url", help="override the server base URL (skips lane port discovery)")
    ap.add_argument("--model", help="override the model id sent in the request")
    ap.add_argument("--runs", type=int, default=3, help="prose/code generations per variant (default 3)")
    ap.add_argument("--no-cold", action="store_true",
                    help="skip the cold-TTFT phase (meaningless on a server that already "
                         "cached this prefix)")
    ap.add_argument("--restart-with", metavar="BLOCKS",
                    help="AC ONLY, never the default: comma list of MTP draft block sizes "
                         "(none,2,3,4). Starts a private server on --port per value with the "
                         "roster entry's drafter, measures it, kills it.")
    ap.add_argument("--port", type=int, default=8090,
                    help="spare port for --restart-with servers (default 8090)")
    ap.add_argument("--venv", default=VLM_PY, help="python that has mlx-vlm (default: %s)" % VLM_PY)
    ap.add_argument("--out", default=DEFAULT_OUT, help="JSONL results file (default %s)" % DEFAULT_OUT)
    ap.add_argument("--i-am-on-ac", action="store_true",
                    help="override the pmset AC check (use only when pmset is unavailable)")
    ap.add_argument("--unload-after", action="store_true",
                    help="bootout the lane's launchd job when finished (battery rule)")
    ap.add_argument("--force", action="store_true",
                    help="allow --restart-with while another model server is resident")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, send nothing")
    args = ap.parse_args()

    d = discover(args.lane)
    model = args.model or d["model"]
    blocks = [b.strip() for b in args.restart_with.split(",")] if args.restart_with else []

    log("lane        : %s (%s)" % (args.lane, d["label"]))
    log("model       : %s   [roster via %s]" % (model, d["source"]))
    log("server log  : %s" % d["log"])
    log("results     : %s" % args.out)
    if blocks:
        log("mode        : --restart-with %s on port %d via %s"
            % (",".join(blocks), args.port, args.venv))
        log("              launcher %s copied to a temp 'vlm-bench-launch.py'" % LAUNCHER)
        log("              drafter  %s" % (d["entry"].get("draft_model") or "(none in roster)"))
    else:
        log("mode        : measure the running server at %s" % (args.url or d["url"]))
    log("phases      : %s warm, %d x prose, %d x code (max_tokens %d)"
        % ("cold," if not args.no_cold else "", args.runs, args.runs, GEN_MAX))
    log("power       : %s" % ("AC" if on_ac() else "BATTERY — would refuse without --i-am-on-ac"))

    if args.dry_run:
        log("\n[dry-run] nothing was sent, no model was loaded, no file was written.")
        if blocks:
            log("[dry-run] would run, per block: load server, one 4-token warmup, "
                "cold+warm TTFT on the ~6.5k-token prefix, %d prose + %d code generations, "
                "read prefill=/decode= from the server log, then SIGTERM the process group."
                % (args.runs, args.runs))
        if args.unload_after:
            log("[dry-run] would then: launchctl bootout gui/%d/%s" % (os.getuid(), d["label"]))
        return 0

    recs = []
    if blocks:
        require_ac(args, "load a model server")
        if not os.path.exists(args.venv):
            return err("no mlx-vlm venv at %s — see install-mlx-vlm-venv.sh" % args.venv)
        if not os.path.exists(LAUNCHER):
            return err("launcher not found at %s" % LAUNCHER)
        if model_server_running() and not args.force:
            return err("a model server is already resident (pgrep 'mlx_lm server|mlx-vlm-launch'). "
                       "Loading a second copy would double the footprint — stop it first, or "
                       "pass --force if you know the RAM is there.")
        outdir = os.path.dirname(args.out) or "."
        os.makedirs(outdir, exist_ok=True)
        for block in blocks:
            blog = os.path.join(outdir, "decode_bench-server-block%s.log" % block)
            log("\n=== block %s ===" % block)
            p = tmpdir = None
            lf = None
            try:
                p, lf, load_s, tmpdir = spawn_bench_server(model, d["entry"], block, args.port,
                                                           args.venv, blog)
                log("  server up in %ss (log %s)" % (load_s, blog))
                base = "http://127.0.0.1:%d" % args.port
                meta = {"load_s": load_s, "footprint_gb": None, "lane": args.lane,
                        "backend": "mlx_vlm", "block": block}
                r = measure(base, model, blog, args.runs, not args.no_cold,
                            "block%s" % block, meta)
                fp = footprint_gb(p.pid)
                for x in r:
                    x["footprint_gb"] = fp
                log("  footprint %s GB" % fp)
                recs += r
            except Exception as e:
                log("  ERROR %s: %s" % (type(e).__name__, e))
            finally:
                if p is not None:
                    kill_server(p)
                if lf is not None:
                    lf.close()
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                time.sleep(3)
    else:
        base = args.url or d["url"]
        if not online(base):
            return err("nothing answering %s/v1/models. This script never wakes a lane "
                       "(battery rule) — wake it from the dashboard, or use --restart-with "
                       "on AC power." % base)
        require_ac(args, "run generations")
        meta = {"lane": args.lane, "backend": d["entry"].get("backend") or "unknown",
                "block": d["entry"].get("draft_block_size")}
        recs = measure(base, model, d["log"], args.runs, not args.no_cold, "running", meta)

    table(recs)
    append_jsonl(args.out, recs)
    if args.unload_after:
        unload(args.lane)
    else:
        log("\nBattery rule reminder — unload when you are done:\n"
            "  launchctl bootout gui/%d/%s" % (os.getuid(), d["label"]))
    return 0


def err(msg):
    print("decode_bench: " + msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
