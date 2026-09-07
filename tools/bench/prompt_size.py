#!/usr/bin/env python3
"""prompt_size.py — how big is a Hermes turn, right now, WITHOUT loading a model.

Every other script in tools/bench costs a model load. This one costs a file
read: it parses the MLX server's own log (the launchd job's stdout/stderr —
install-services.sh points com.hermes.mlx-server at ~/.hermes/logs/mlx-server.log
and com.hermes.mlx-bg at ~/.hermes/logs/mlx-bg.log) and reports, per recent chat
request, the prompt size, how much of it the APC exact prefix cache served, the
completion length and the server's own prefill/decode rates — plus medians.

Why it matters: docs/plans/post-v1-baseline.md measured a ~6.5k-token prefix at
~6.3 s cold and ~0.2 s warm, and noted the real Hermes system prompt is ~18k
tokens. This tells you what the prompt has actually grown to since, so you know
whether a wake-latency number is still explained by the same prefill, on
battery, with nothing resident.

It reads only lines the mlx-vlm backend writes:

  Generation queued:  request=<id> prompt_tokens=N max_tokens=M ...
  Prefill completed:  request=<id> prompt_tokens=N cached_tokens=C elapsed=Ts rate=R tok/s
  Request completed:  endpoint=/chat/completions model=... prompt_tokens=N
                      generated_tokens=G elapsed=Ts prefill=P tok/s decode=D tok/s
                      finish_reason=F in_flight=I

The completion line carries no request id, so cached_tokens is matched back by
prompt_tokens against the most recent unconsumed prefill line. The mlx-lm
backend logs none of this — against an mlx-lm-only log the script says so
instead of printing an empty table.

Usage:
  tools/bench/prompt_size.py
  tools/bench/prompt_size.py --lane bg --limit 40
  tools/bench/prompt_size.py --log ~/.hermes/logs/mlx-server.log --json
"""
import argparse
import json
import os
import re
import statistics
import sys

HOME = os.path.expanduser("~")
LOG_DIR = os.path.join(HOME, ".hermes", "logs")
LANES = {"primary": os.path.join(LOG_DIR, "mlx-server.log"),
         "bg": os.path.join(LOG_DIR, "mlx-bg.log")}
TAIL_BYTES = 4 * 1024 * 1024          # plenty for hundreds of requests; keeps it instant

TS = r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
RE_PREFILL = re.compile(TS + r".*Prefill completed: request=(?P<req>\S+) "
                        r"prompt_tokens=(?P<prompt>\d+) cached_tokens=(?P<cached>\d+) "
                        r"elapsed=(?P<elapsed>[\d.]+)s rate=(?P<rate>[\d.]+) tok/s")
RE_DONE = re.compile(TS + r".*Request completed: endpoint=(?P<endpoint>\S+) "
                     r"model=(?P<model>\S+) .*?prompt_tokens=(?P<prompt>\d+) "
                     r"generated_tokens=(?P<gen>\d+) elapsed=(?P<elapsed>[\d.]+)s "
                     r"prefill=(?P<prefill>[\d.]+) tok/s decode=(?P<decode>[\d.]+) tok/s"
                     r"(?: finish_reason=(?P<finish>\S+))?")
RE_ANY_MLX = re.compile(r"Starting MLX server:|Starting MLX-VLM server:")


def read_tail(path, nbytes):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > nbytes:
            f.seek(size - nbytes)
            f.readline()               # drop the partial first line
        return f.read().decode("utf-8", "replace"), size


def parse(text, endpoint_filter="/chat/completions"):
    """Walk the log forward, pairing each completion with the most recent
    unconsumed prefill line that had the same prompt_tokens."""
    pending = {}                       # prompt_tokens -> [prefill dicts, oldest first]
    out = []
    for line in text.splitlines():
        m = RE_PREFILL.search(line)
        if m:
            pending.setdefault(int(m.group("prompt")), []).append(
                {"cached": int(m.group("cached")), "prefill_elapsed_s": float(m.group("elapsed")),
                 "prefill_rate": float(m.group("rate")), "req": m.group("req")})
            continue
        m = RE_DONE.search(line)
        if not m:
            continue
        if endpoint_filter and m.group("endpoint") != endpoint_filter:
            continue
        prompt = int(m.group("prompt"))
        pre = pending.get(prompt)
        pre = pre.pop(0) if pre else {}
        out.append({"ts": m.group("ts"), "request": pre.get("req"),
                    "model": m.group("model"), "endpoint": m.group("endpoint"),
                    "prompt_tokens": prompt, "cached_tokens": pre.get("cached"),
                    "new_tokens": (prompt - pre["cached"]) if "cached" in pre else None,
                    "completion_tokens": int(m.group("gen")),
                    "elapsed_s": float(m.group("elapsed")),
                    "prefill_tps": float(m.group("prefill")),
                    "decode_tps": float(m.group("decode")),
                    "finish_reason": m.group("finish")})
    return out


def med(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return statistics.median(vals) if vals else None


def fmt(v, nd=1):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%%.%df" % nd) % v
    return str(v)


def table(rows):
    hdr = ("when", "prompt", "cached", "new", "completion", "elapsed_s",
           "prefill tok/s", "decode tok/s", "finish")
    body = [(r["ts"][5:], fmt(r["prompt_tokens"]), fmt(r["cached_tokens"]),
             fmt(r["new_tokens"]), fmt(r["completion_tokens"]), fmt(r["elapsed_s"], 2),
             fmt(r["prefill_tps"]), fmt(r["decode_tps"]), r["finish_reason"] or "-")
            for r in rows]
    body.append(("median", fmt(med(rows, "prompt_tokens")), fmt(med(rows, "cached_tokens")),
                 fmt(med(rows, "new_tokens")), fmt(med(rows, "completion_tokens")),
                 fmt(med(rows, "elapsed_s"), 2), fmt(med(rows, "prefill_tps")),
                 fmt(med(rows, "decode_tps")), ""))
    w = [max(len(hdr[i]), max(len(r[i]) for r in body)) for i in range(len(hdr))]
    print("  ".join(h.rjust(w[i]) if i else h.ljust(w[i]) for i, h in enumerate(hdr)))
    print("  ".join("-" * w[i] for i in range(len(hdr))))
    for i, r in enumerate(body):
        if i == len(body) - 1:
            print("  ".join("-" * w[j] for j in range(len(hdr))))
        print("  ".join(r[j].rjust(w[j]) if j else r[j].ljust(w[j]) for j in range(len(hdr))))


def main():
    ap = argparse.ArgumentParser(
        description="Per-turn prompt size from the MLX server log — loads no model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Reads a log file and nothing else: safe on battery, safe with every "
               "model unloaded.")
    ap.add_argument("--lane", choices=sorted(LANES), default="primary",
                    help="which lane's log to read (default primary = mlx-server.log)")
    ap.add_argument("--log", help="explicit log path (overrides --lane)")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many of the most recent requests to show (default 20)")
    ap.add_argument("--all-endpoints", action="store_true",
                    help="include non-/chat/completions requests (completions, embeddings)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    path = args.log or LANES[args.lane]
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print("prompt_size: no log at %s\n"
              "The model services write there via install-services.sh "
              "(StandardOutPath/StandardErrorPath). Nothing to read until a lane has run."
              % path, file=sys.stderr)
        return 1

    text, size = read_tail(path, TAIL_BYTES)
    rows = parse(text, None if args.all_endpoints else "/chat/completions")

    if args.json:
        shown = rows[-args.limit:]
        print(json.dumps({"log": path, "log_bytes": size, "requests": len(rows),
                          "shown": shown,
                          # median over the SHOWN rows, so it matches the table
                          "median": {k: med(shown, k) for k in
                                     ("prompt_tokens", "cached_tokens", "new_tokens",
                                      "completion_tokens", "elapsed_s", "prefill_tps",
                                      "decode_tps")}}, indent=1))
        return 0

    print("log      : %s (%.1f MB, scanned last %.1f MB)"
          % (path, size / 1048576.0, min(size, TAIL_BYTES) / 1048576.0))
    if not rows:
        if RE_ANY_MLX.search(text):
            print("\nNo chat requests logged in that window.\n"
                  "Either the lane has served nothing since it last started, or it ran on the\n"
                  "mlx-lm backend, which logs no token counts (only the mlx_vlm backend writes\n"
                  "the 'Request completed: ... prompt_tokens=' lines this parses). Check the\n"
                  "roster entry's `backend` in ~/.hermes/dashboard/models.json, or raise\n"
                  "--limit / point --log at an older log.")
        else:
            print("\nNo MLX server lines at all in that window — is %s the right log?" % path)
        return 1

    models = sorted({r["model"] for r in rows})
    print("model    : %s" % ", ".join(models))
    print("requests : %d parsed, showing the last %d" % (len(rows), min(args.limit, len(rows))))
    print("")
    shown = rows[-args.limit:]
    table(shown)
    p = med(shown, "prompt_tokens")
    c = med(shown, "cached_tokens")
    n = med(shown, "new_tokens")
    if p:
        line = "\nMedian turn sends ~%d prompt tokens" % p
        if c is not None and p:
            line += "; ~%d (%.0f%%) came off the APC exact prefix cache, ~%s newly prefilled" \
                    % (c, 100.0 * c / p, fmt(n, 0))
        print(line + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
