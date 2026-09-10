#!/usr/bin/env python3
"""1.3.1 items 2 + 3, the UI half — the two renderers, headless in node.

Both aux JS files are eval'd in a bare node process with the inline helpers
stubbed (`esc`, `relTime`) and no DOM, the way CLAUDE.md says to verify a
renderer: it catches the `esc`-on-a-number class of throw that only shows up
once the real payload has a null or an integer where the code assumed a string.

  * aux_context.js  — the recent-turns table must show a Lane column and name
    each row's lane, and must survive a row with nulls everywhere.
  * aux_recorder.js — the retention footer must render the configured window,
    clamp messaging, "keep forever", and an error payload.

No model, no network, no dashboard, no browser.
"""
import io, os, re, sys, json, shutil, tempfile, subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(name)


HARNESS = r"""
// --- the inline helpers these modules expect from index.html -----------------
globalThis.esc = function (s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
  });
};
globalThis.relTime = function (ts) { return "just now"; };
globalThis.REDUCE = true;
globalThis.setTimeout = function () { return 0; };   // no dangling timers
globalThis.fetch = function () { return Promise.reject(new Error("no network")); };

const fs = require("fs");
const OUT = {};

// aux_context.js is an IIFE that publishes window.hermesContext
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
const cx = globalThis.hermesContext;

// aux_recorder.js is a plain script: top-level declarations only, nothing runs
(0, eval)(fs.readFileSync(process.argv[3], "utf8"));

const CASES = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));

OUT.exports = Object.keys(cx).sort();
OUT.recent = {};
for (const [name, rows] of Object.entries(CASES.recent)) {
  try { OUT.recent[name] = cx.recentRows(rows, 65536); }
  catch (e) { OUT.recent[name] = "THREW: " + e; }
}
OUT.lane = {};
for (const l of ["primary", "bg", "", null, "nonsense"]) {
  try { OUT.lane[String(l)] = cx.laneLabel(l); }
  catch (e) { OUT.lane[String(l)] = "THREW: " + e; }
}
OUT.card = (function () {
  try { return cx.cardHTML(CASES.card); } catch (e) { return "THREW: " + e; }
})();
OUT.retain = {};
for (const [name, p] of Object.entries(CASES.retention)) {
  try { OUT.retain[name] = renderRecorderRetention(p); }
  catch (e) { OUT.retain[name] = "THREW: " + e; }
}
OUT.rows = (function () {
  try { return renderRecorderRows(CASES.actions); } catch (e) { return "THREW: " + e; }
})();
process.stdout.write(JSON.stringify(OUT));
"""


def row(ts, lane, prompt, cached_pct, prefill, compacted, pct, epoch=1788800000.0):
    return {"ts": ts, "epoch": epoch, "lane": lane, "model": "m/x",
            "prompt_tokens": prompt, "cached_tokens": 100, "new_tokens": 10,
            "prefill_s": prefill, "decode_tps": 40.0, "generated_tokens": 12,
            "elapsed_s": 1.0, "finish_reason": "stop", "pct_of_window": pct,
            "cache_pct": cached_pct, "prev_prompt_tokens": 100,
            "compacted": compacted}


CASES = {
    "recent": {
        "both": [row("2026-09-07 10:00:00", "primary", 24000, 96.1, 2.18, False, 36.6),
                 row("2026-09-07 10:01:00", "bg", 41635, 94.5, 1.41, False, 63.5, 1788800060.0),
                 row("2026-09-07 10:02:00", "primary", 4000, 0.0, 9.9, True, 6.1, 1788800120.0)],
        "empty": [],
        # every optional field null: the shape a log line that half-parsed gives
        "nulls": [{"ts": None, "epoch": None, "lane": None, "model": None,
                   "prompt_tokens": None, "cached_tokens": None, "new_tokens": None,
                   "prefill_s": None, "decode_tps": None, "generated_tokens": None,
                   "elapsed_s": None, "finish_reason": None, "pct_of_window": None,
                   "cache_pct": None, "prev_prompt_tokens": None, "compacted": None}],
        "hostile": [dict(row("2026-09-07 10:00:00", "<img src=x onerror=alert(1)>",
                             24000, 96.1, 2.18, False, 36.6))],
    },
    "card": {
        "data": {"ok": True, "window": 65536, "compact_at_tokens": 32768,
                 "target_tokens": 6553,
                 "values": {"threshold": 0.5, "target_ratio": 0.2,
                            "protect_last_n": 20, "protect_first_n": 3},
                 "limits": {}, "auxiliary": {"effective": "mlx/aux", "inherits_main": True}},
        "recent": {"turns": [row("2026-09-07 10:00:00", "primary", 24000, 96.1, 2.18, False, 36.6),
                             row("2026-09-07 10:01:00", "bg", 41635, 94.5, 1.41, False, 63.5)]},
    },
    "retention": {
        "default": {"ok": True, "retain_days": 90, "forever": False, "min_days": 14,
                    "max_days": 3650, "default_days": 90, "undo_trash_days": 14,
                    "total": 3391, "clamped": False,
                    "next_sweep": {"would_delete": 0, "kept_undone": 0, "kept_snapshot": 0}},
        "will_sweep": {"ok": True, "retain_days": 30, "forever": False, "min_days": 14,
                       "max_days": 3650, "default_days": 90, "undo_trash_days": 14,
                       "total": 3391, "clamped": False,
                       "next_sweep": {"would_delete": 601, "kept_undone": 2,
                                      "kept_snapshot": 1}},
        "clamped": {"ok": True, "retain_days": 14, "forever": False, "min_days": 14,
                    "max_days": 3650, "default_days": 90, "undo_trash_days": 14,
                    "total": 10, "clamped": True, "asked": 7,
                    "next_sweep": {"would_delete": 1, "kept_undone": 0, "kept_snapshot": 0}},
        "forever": {"ok": True, "retain_days": 0, "forever": True, "min_days": 14,
                    "max_days": 3650, "default_days": 90, "undo_trash_days": 14,
                    "total": 3391, "clamped": False, "next_sweep": None},
        "odd_window": {"ok": True, "retain_days": 45, "forever": False, "min_days": 14,
                       "max_days": 3650, "default_days": 90, "undo_trash_days": 14,
                       "total": None, "clamped": False, "next_sweep": None},
        "error": {"ok": False, "error": "<script>bad</script>"},
        "empty": {},
    },
    "actions": [{"id": 1, "ts": 1788800000.0, "tool": "write_file", "kind": "write",
                 "reversible": "yes", "status": "ok", "target": "/tmp/x",
                 "summary": "", "has_snapshot": True, "undone_ts": None,
                 "origin": "hub", "source": "hub"}],
}


def run(tmp):
    if shutil.which("node") is None:
        print("  --   node not installed; skipped")
        return
    hp = os.path.join(tmp, "harness.js")
    cp = os.path.join(tmp, "cases.json")
    io.open(hp, "w", encoding="utf-8").write(HARNESS)
    io.open(cp, "w", encoding="utf-8").write(json.dumps(CASES))

    print("\n0. both files parse")
    for f in ("aux_context.js", "aux_recorder.js"):
        p = subprocess.run(["node", "--check", os.path.join(DASH, f)],
                           capture_output=True, text=True)
        check("node --check %s" % f, p.returncode == 0, p.stderr.strip()[:200])

    p = subprocess.run(["node", hp, os.path.join(DASH, "aux_context.js"),
                        os.path.join(DASH, "aux_recorder.js"), cp],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        check("the harness ran", False, p.stderr.strip()[-600:])
        return
    out = json.loads(p.stdout)

    print("\n1. the recent-turns table names each row's lane")
    both = out["recent"]["both"]
    check("nothing threw", not both.startswith("THREW"), both[:200])
    check("a Lane column header", "<th>Lane</th>" in both, both[:200])
    check("six columns", both.count("<th>") == 6, both.count("<th>"))
    check("the primary rows read 'chat'", both.count(">chat<") == 2, both)
    check("the bg row reads 'background'", both.count(">background<") == 1, both)
    check("bg is rendered muted", 'class="is-muted">background<' in both, both[:400])
    check("a compacted row still says yes", ">yes<" in both, both[-300:])
    # the table renders newest-first, so the last row of the payload is on top
    check("newest first", both.index("4,000") < both.index("24,000"), both[:600])

    print("\n2. lane labels, including nonsense from an older payload")
    check("primary -> chat", out["lane"]["primary"] == "chat", out["lane"])
    check("bg -> background", out["lane"]["bg"] == "background", out["lane"])
    for k in ("", "null", "nonsense"):
        check("%r falls back to chat" % k, out["lane"][k] == "chat", out["lane"])

    print("\n3. degenerate payloads do not throw")
    check("empty list -> the empty-state copy",
          "No model requests are logged yet" in out["recent"]["empty"],
          out["recent"]["empty"][:120])
    nulls = out["recent"]["nulls"]
    check("a row of nulls renders", not nulls.startswith("THREW"), nulls[:200])
    check("nulls become em-dashes, not 'null'", "null" not in nulls, nulls[:400])
    check("an unknown lane still renders as chat", ">chat<" in nulls, nulls[:400])

    print("\n4. the lane string is escaped (it comes out of a log file)")
    hostile = out["recent"]["hostile"]
    check("no raw tag survives", "<img" not in hostile, hostile[:300])
    check("it is the label, not the raw value", ">chat<" in hostile, hostile[:300])

    print("\n5. the whole card still builds with the new column")
    card = out["card"]
    check("card renders", not card.startswith("THREW"), card[:300])
    check("Recent turns section present", "Recent turns" in card)
    check("lane column reached it", "<th>Lane</th>" in card)
    check("the foot explains the two lanes", "background lane" in card, card[-600:])
    check("and says the chip stays on the chat lane", "chat lane" in card, card[-600:])

    print("\n6. the retention footer")
    d = out["retain"]["default"]
    check("renders", not d.startswith("THREW"), d[:200])
    check("a labelled select", 'id="rec-retain-sel"' in d and "Keep history" in d, d[:200])
    check("the configured window is selected",
          '<option value="90" selected>90 days</option>' in d, d)
    check("'keep forever' is always offered", "keep forever" in d, d)
    check("no option below the minimum", '<option value="1"' not in d, d)
    check("the row count is shown", "3391 rows" in d, d)
    check("the exemption is stated", "Undone actions" in d and "snapshot" in d, d)
    check("nothing to sweep says so", "Nothing is old enough" in d, d)

    w = out["retain"]["will_sweep"]
    check("a pending sweep is quantified", "removes 601 rows" in w, w)
    c = out["retain"]["clamped"]
    check("a clamp explains itself", "14 days is the minimum" in c, c)
    f = out["retain"]["forever"]
    check("forever is selected", '<option value="0" selected>' in f, f)
    check("and warns the db grows", "grows with every tool call" in f, f)
    o = out["retain"]["odd_window"]
    check("a window outside the preset list is still offered",
          '<option value="45" selected>45 days</option>' in o, o)
    check("an unknown total is simply omitted", " rows" not in o, o)
    e = out["retain"]["error"]
    check("an error payload renders as an error", 'class="rr-note bad"' in e, e)
    check("and is escaped", "<script>" not in e, e)
    check("an empty payload does not throw",
          not out["retain"]["empty"].startswith("THREW"), out["retain"]["empty"][:120])

    print("\n7. the existing recorder feed is unchanged")
    check("rows still render", not out["rows"].startswith("THREW"), out["rows"][:200])
    check("harness surface published recentRows/laneLabel",
          "recentRows" in out["exports"] and "laneLabel" in out["exports"],
          out["exports"])


def main():
    tmp = tempfile.mkdtemp(prefix="t131ui-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("TESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    if FAILS:
        print("FAILED: %d  (%s)" % (len(FAILS), ", ".join(FAILS)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
