#!/usr/bin/env python3
"""concurrency_probe.py — two concurrent decode streams against one model server.

Promoted from the session-scratch `conc_probe.py`. This is the probe that
answered backlog #25 (native MTP does NOT disable continuous batching — two
streams interleave with zero queueing, but each collapses from ~47.8 tok/s solo
to 21.2 tok/s, a 42.5 aggregate against 59.8 with no drafter) and that PARKED
backlog #23: mlx-vlm 0.6.16/0.6.17 corrupt output when two requests overlap
while the MTP drafter is loaded — 0.6.14 was 6/6 rounds clean, 0.6.17 2/6,
0.6.16 1/3 plus a GPU page fault. See docs/plans/post-v1-baseline.md.

It is therefore the gate on any mlx-vlm upgrade: **6/6 clean rounds before the
live venv is touched** (install-mlx-vlm-venv.sh header has the rename-aside
rollback plan). Point --venv at the throwaway venv to test a candidate version
without going anywhere near ~/.hermes/mlx-vlm-venv.

Per round it fires two DIFFERENT 200-token prose prompts at the same instant and
records, per stream, first-token time, end time, chunk count, finish_reason and
the head/tail of the text. A round is "clean" when both streams interleaved,
both ran to full length, neither errored and neither shows a repeated-character
flood (the 0.6.17 signature was a token-0 flood of "!").

By default it starts its own server on a spare port (8090) from a copy of
mlx-vlm-launch.py renamed so the dashboard's `pgrep -f "mlx_lm server|
mlx-vlm-launch"` never sees it, and kills it afterwards. --url probes a server
that is already up instead, loading nothing.

BATTERY RULE (CLAUDE.md): AC power only; the spawned server is always killed,
and a --url run leaves the lane exactly as it found it.

Usage:
  tools/bench/concurrency_probe.py --block 3 --rounds 3        # AC only
  tools/bench/concurrency_probe.py --block none --rounds 3     # no drafter
  tools/bench/concurrency_probe.py --venv /tmp/venv-next/bin/python --block 3 --rounds 6
  tools/bench/concurrency_probe.py --url http://127.0.0.1:8080 --rounds 1
  tools/bench/concurrency_probe.py --dry-run
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = os.path.expanduser("~")
DASH_DIR = os.path.join(HOME, ".hermes", "dashboard")
DEFAULT_OUT = os.path.join(HOME, ".hermes", "bench", "results.jsonl")
VLM_PY = os.path.join(HOME, ".hermes", "mlx-vlm-venv", "bin", "python")
LAUNCHER = os.path.join(REPO, "mlx-vlm-launch.py")

PA = ("Write about 200 words of plain prose explaining why append-only logs make "
      "debugging distributed systems easier. No lists, no headings.")
PB = ("Write about 200 words of plain prose explaining why unit tests should avoid "
      "sleeping on wall-clock time. No lists, no headings.")
GEN = 200
FULL = 190          # chunks: "ran to full length" (the originals' threshold)

SRV_RE = re.compile(r"prompt_tokens=(\d+) generated_tokens=(\d+) elapsed=([\d.]+)s "
                    r"prefill=([\d.]+) tok/s decode=([\d.]+) tok/s")
FLOOD_RE = re.compile(r"(\S)\1{9,}")     # 10+ of the same non-space char in a row


def log(*a):
    print(*a, flush=True)


def stream(base, model, prompt, max_tokens, tag, out, temperature=0.0, t_zero=None):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": temperature,
                       "stream": True}).encode()
    req = urllib.request.Request(base + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    n = 0
    txt = []
    err = None
    finish = None
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:
                if not line.startswith(b"data:"):
                    continue
                p = line[5:].strip()
                if p == b"[DONE]":
                    break
                try:
                    d = json.loads(p)
                except Exception:
                    continue
                ch = (d.get("choices") or [{}])[0]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                delta = ch.get("delta", {}).get("content")
                if delta:
                    if first is None:
                        first = time.time()
                    n += 1
                    txt.append(delta)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    z = t_zero or t0
    text = "".join(txt)
    out[tag] = {"start": round(t0 - z, 3), "first": round(first - z, 3) if first else None,
                "end": round(time.time() - z, 3), "chunks": n, "finish_reason": finish,
                "error": err, "chars": len(text),
                "tps": round(n / (time.time() - first), 1) if first else None,
                "flood": bool(FLOOD_RE.search(text)),
                "text_head": text[:120], "text_tail": text[-80:]}


def srv_lines(logpath, pos):
    try:
        with open(logpath, "rb") as f:
            f.seek(pos)
            chunk = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    return [{"prompt_tokens": int(m[1]), "generated_tokens": int(m[2]),
             "elapsed_s": float(m[3]), "server_tps": float(m[5])}
            for m in SRV_RE.finditer(chunk)]


def logpos(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def on_ac():
    try:
        return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                                            text=True, timeout=10).stdout
    except Exception:
        return False


def model_server_running():
    try:
        return bool(subprocess.run(["pgrep", "-f", "mlx_lm server|mlx-vlm-launch"],
                                   capture_output=True, text=True, timeout=5).stdout.strip())
    except Exception:
        return True


def online(base, timeout=2):
    try:
        urllib.request.urlopen(base + "/v1/models", timeout=timeout).read()
        return True
    except Exception:
        return False


def active_model():
    try:
        with open(os.path.join(DASH_DIR, "active-model")) as f:
            v = f.read().strip()
        if v:
            return v
    except Exception:
        pass
    return "mlx-community/Qwen3.8-27B-4bit"


def roster_entry(model):
    for path in (os.path.join(DASH_DIR, "models.json"),):
        try:
            with open(path) as f:
                for m in json.load(f).get("models") or []:
                    if m.get("id") == model:
                        return m
        except Exception:
            pass
    try:
        with open(os.path.join(DASH_DIR, "server-backend")) as f:
            return json.load(f)
    except Exception:
        return {}


def spawn(model, entry, block, port, venv_py, logpath, timeout=600):
    tmpdir = tempfile.mkdtemp(prefix="hermes-conc-")
    launcher = os.path.join(tmpdir, "vlm-bench-launch.py")   # must NOT match the dashboard pgrep
    shutil.copy2(LAUNCHER, launcher)
    args = [venv_py, launcher, "--model", model, "--host", "127.0.0.1", "--port", str(port),
            "--max-tokens", "1024", "--trust-remote-code"]
    if block != "none":
        draft = entry.get("draft_model") or ""
        if not draft:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError("roster entry for %s has no draft_model — use --block none "
                               "or --draft-model" % model)
        args += ["--draft-model", draft, "--draft-kind", entry.get("draft_kind") or "mtp",
                 "--draft-block-size", str(block)]
    env = dict(os.environ, APC_ENABLED="1",
               APC_EXACT_CACHE_ENTRIES=os.environ.get("APC_EXACT_CACHE_ENTRIES", "6"))
    lf = open(logpath, "wb")
    t0 = time.time()
    p = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    base = "http://127.0.0.1:%d" % port
    while True:
        if p.poll() is not None:
            lf.close()
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError("probe server died during load — see %s" % logpath)
        if online(base):
            break
        if time.time() - t0 > timeout:
            kill(p)
            lf.close()
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError("probe server load timeout after %ds" % timeout)
        time.sleep(1)
    return p, lf, tmpdir, round(time.time() - t0, 1)


def kill(p):
    if p is None or p.poll() is not None:
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


def run_rounds(base, model, logpath, rounds):
    """Solo control first (so a corrupt round cannot be blamed on the prompt),
    then `rounds` two-way rounds."""
    res = {"solo": {}, "rounds": []}
    stream(base, model, PA, GEN, "solo_A", res["solo"])
    s = res["solo"]["solo_A"]
    log("  solo control: chunks=%s tps=%s fin=%s flood=%s"
        % (s["chunks"], s["tps"], s["finish_reason"], s["flood"]))
    for i in range(rounds):
        pos = logpos(logpath)
        out = {}
        z = time.time()
        ta = threading.Thread(target=stream, args=(base, model, PA, GEN, "A", out, 0.0, z))
        tb = threading.Thread(target=stream, args=(base, model, PB, GEN, "B", out, 0.0, z))
        ta.start()
        tb.start()
        ta.join()
        tb.join()
        A, B = out["A"], out["B"]
        interleaved = bool(A["first"] is not None and B["first"] is not None
                           and max(A["first"], B["first"]) < min(A["end"], B["end"]))
        full = A["chunks"] >= FULL and B["chunks"] >= FULL
        clean = bool(interleaved and full and not A["error"] and not B["error"]
                     and not A["flood"] and not B["flood"])
        srv = srv_lines(logpath, pos)
        agg = round(sum(x["server_tps"] for x in srv), 1) if srv else None
        r = {"round": i, "A": A, "B": B, "server": srv, "interleaved": interleaved,
             "both_full_length": full, "clean": clean, "aggregate_tps": agg}
        res["rounds"].append(r)
        log("  round %d: clean=%s interleaved=%s both_full=%s aggregate=%s tok/s\n"
            "           A(first=%s end=%s chunks=%s fin=%s flood=%s) "
            "B(first=%s end=%s chunks=%s fin=%s flood=%s)"
            % (i, clean, interleaved, full, agg,
               A["first"], A["end"], A["chunks"], A["finish_reason"], A["flood"],
               B["first"], B["end"], B["chunks"], B["finish_reason"], B["flood"]))
        time.sleep(2)
    return res


def sampled_probe(base, model):
    """#23 leftover: does a temperature>0 request survive (RNG shim), and do two
    identical prompts actually differ? (backlog #27 says they did not)."""
    out = {}
    for temp in (0.7, 1.0):
        stream(base, model, "Write two sentences about the sea.", 60, "temp%s" % temp,
               out, temperature=temp)
    log("  sampled: " + json.dumps({k: {"chunks": v["chunks"], "error": v["error"],
                                        "head": v["text_head"][:60]} for k, v in out.items()}))
    return out


def append_jsonl(path, recs):
    if not recs:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    log("appended %d line(s) to %s" % (len(recs), path))


def main():
    ap = argparse.ArgumentParser(
        description="Two concurrent decode streams against one model server "
                    "(see tools/bench/README.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Upgrade gate: 6/6 clean rounds before touching ~/.hermes/mlx-vlm-venv.\n"
               "BATTERY RULE: AC power only; the spawned server is always killed.")
    ap.add_argument("--rounds", type=int, default=3, help="two-way rounds (default 3; the "
                                                          "upgrade gate wants 6)")
    ap.add_argument("--block", default="3",
                    help='MTP draft block size, or "none" for no drafter (default 3)')
    ap.add_argument("--model", help="model id (default: ~/.hermes/dashboard/active-model)")
    ap.add_argument("--venv", default=VLM_PY,
                    help="python that has mlx-vlm; point at a throwaway venv to test a "
                         "candidate version (default %s)" % VLM_PY)
    ap.add_argument("--port", type=int, default=8090, help="spare port (default 8090)")
    ap.add_argument("--url", help="probe a server that is ALREADY running at this base URL "
                                  "instead of starting one (loads nothing)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="JSONL results file (default %s)" % DEFAULT_OUT)
    ap.add_argument("--sampled-probe", action="store_true",
                    help="also send two temperature>0 requests (#23 RNG shim / #27 sampling)")
    ap.add_argument("--teardown-probe", action="store_true",
                    help="finish by SIGTERMing the spawned server and reporting its exit code "
                         "(#23 os._exit teardown shim; ignored with --url)")
    ap.add_argument("--i-am-on-ac", action="store_true", help="override the pmset AC check")
    ap.add_argument("--force", action="store_true",
                    help="spawn even though another model server is resident")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, send nothing")
    args = ap.parse_args()

    model = args.model or active_model()
    entry = roster_entry(model)
    log("model    : %s" % model)
    log("block    : %s   drafter %s" % (args.block, entry.get("draft_model") or "(none)"))
    log("mode     : %s" % ("probe running server at %s" % args.url if args.url
                           else "spawn %s on port %d via %s" % (LAUNCHER, args.port, args.venv)))
    log("rounds   : %d two-way (2 x %d tokens, different prompts)" % (args.rounds, GEN))
    log("results  : %s" % args.out)
    log("power    : %s" % ("AC" if on_ac() else "BATTERY — would refuse without --i-am-on-ac"))

    if args.dry_run:
        log("\n[dry-run] would: solo control stream, then %d rounds of two simultaneous "
            "%d-token streams, scoring each round clean/interleaved/full-length/flood, "
            "reading decode= from the server log%s.\n"
            "[dry-run] nothing was sent, no model was loaded, no file was written."
            % (args.rounds, GEN, "" if args.url else ", then SIGTERM the spawned server"))
        return 0

    if not (args.i_am_on_ac or on_ac()):
        return err("not on AC power — refusing (battery rule). Plug in, or pass --i-am-on-ac.")

    p = lf = tmpdir = None
    logpath = os.path.join(os.path.dirname(args.out) or ".", "concurrency_probe-server.log")
    recs = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        if args.url:
            base = args.url.rstrip("/")
            if not online(base):
                return err("nothing answering %s/v1/models" % base)
            # a running lane logs to ~/.hermes/logs; server-side tok/s comes from there
            logpath = os.path.join(HOME, ".hermes", "logs",
                                   "mlx-bg.log" if base.endswith("8081") else "mlx-server.log")
            load_s = None
        else:
            if not os.path.exists(args.venv):
                return err("no python with mlx-vlm at %s — see install-mlx-vlm-venv.sh" % args.venv)
            if not os.path.exists(LAUNCHER):
                return err("launcher not found at %s" % LAUNCHER)
            if model_server_running() and not args.force:
                return err("a model server is already resident (pgrep 'mlx_lm server|"
                           "mlx-vlm-launch'). Stop it first, or pass --force.")
            os.makedirs(os.path.dirname(logpath), exist_ok=True)
            p, lf, tmpdir, load_s = spawn(model, entry, args.block, args.port, args.venv, logpath)
            base = "http://127.0.0.1:%d" % args.port
            log("  server up in %ss (log %s)" % (load_s, logpath))

        try:
            health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
        except Exception as e:
            health = {"error": "%s: %s" % (type(e).__name__, e)}
        log("  health: continuous_batching_enabled=%s"
            % (health.get("continuous_batching_enabled") if isinstance(health, dict) else "?"))

        res = run_rounds(base, model, logpath, args.rounds)
        clean = sum(1 for r in res["rounds"] if r["clean"])
        log("\n  VERDICT: %d/%d rounds clean%s"
            % (clean, len(res["rounds"]),
               "  — upgrade gate NOT met (needs 6/6)" if clean < len(res["rounds"]) else ""))
        rec = {"ts": stamp, "script": "concurrency_probe", "model": model, "block": args.block,
               "venv": None if args.url else args.venv, "url": base, "load_s": load_s,
               "rounds": len(res["rounds"]), "clean_rounds": clean,
               "continuous_batching": (health.get("continuous_batching_enabled")
                                       if isinstance(health, dict) else None),
               "solo_tps": res["solo"]["solo_A"].get("tps"),
               "aggregate_tps": [r["aggregate_tps"] for r in res["rounds"]],
               "detail": res["rounds"]}
        if args.sampled_probe:
            rec["sampled"] = sampled_probe(base, model)
        recs.append(rec)

        if args.teardown_probe and p is not None:
            log("  teardown probe: SIGTERM pid %d" % p.pid)
            os.kill(p.pid, signal.SIGTERM)
            t = time.time()
            try:
                rc = p.wait(timeout=60)
            except Exception:
                rc = "TIMEOUT"
            rec["teardown"] = {"exit_code": rc, "shutdown_s": round(time.time() - t, 1)}
            log("  teardown: exit=%s in %ss" % (rc, rec["teardown"]["shutdown_s"]))
    except Exception as e:
        log("ERROR %s: %s" % (type(e).__name__, e))
        recs.append({"ts": stamp, "script": "concurrency_probe", "model": model,
                     "block": args.block, "error": "%s: %s" % (type(e).__name__, e)})
    finally:
        kill(p)
        if lf is not None:
            lf.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    append_jsonl(args.out, recs)
    return 0


def err(msg):
    print("concurrency_probe: " + msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
