#!/usr/bin/env python3
"""F03 — doctor's "Hermes config drift" check over ~/.hermes/config.yaml.

Hermes upstream has moved several safety and egress defaults between 0.18 and
0.22 (approvals.mode -> smart, sessions.auto_prune, the iteration cap), and two
0.18 defaults already break "inference and data stay on this Mac": the core
text_to_speech tool goes to Microsoft Edge's cloud voices when `tts:` is unset,
and computer_use runs with no approval card on every surface but the
interactive CLI (audit C111).  doctor.py now reads the agent's config with a
stdlib YAML SUBSET reader (no python on this Mac has PyYAML, and doctor must run
with the dashboard down) and holds it against those keys.

Everything here runs the shipped code: doctor.py is imported from the repo with
HOME pointed at a throwaway directory, so `~/.hermes/config.yaml` is a fixture.
No dashboard, no model, no network, no subprocess (both are booby-trapped
while the check runs), nothing under the real ~/.hermes.
"""
import importlib.util, io, os, random, shutil, socket, subprocess, sys, tempfile
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="t-f03-")
HOME = os.path.join(TMP, "home")
os.makedirs(os.path.join(HOME, ".hermes", "plugins"))
os.environ["HOME"] = HOME
spec = importlib.util.spec_from_file_location("doctor_f03", os.path.join(REPO, "dashboard", "doctor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
CFG = os.path.join(HOME, ".hermes", "config.yaml")


def need(*names):
    missing = [n for n in names if not hasattr(d, n)]
    check("doctor.py defines %s" % ", ".join(names), not missing, missing)
    return not missing


def parse(text):
    return d._hcfg_parse(text)


def rules(text, plugins_dir=None):
    """{rule: [(status, detail, fix)]} for a YAML fixture."""
    tree, probs = parse(text)
    out = {}
    for st, rule, det, fix in d._drift_findings(tree, plugins_dir=plugins_dir or os.path.join(TMP, "none")):
        out.setdefault(rule, []).append((st, det, fix))
    return out


def statuses(text, rule, plugins_dir=None):
    return [s for s, _, _ in rules(text, plugins_dir).get(rule, [])]


def text_of(text, rule, plugins_dir=None):
    return " | ".join(det + " || " + fix for _, det, fix in rules(text, plugins_dir).get(rule, []))


SAFE = """\
approvals:
  mode: manual
tts:
  provider: piper
platform_toolsets:
  cli:
  - terminal
  - file
  - web
  telegram: [terminal, file]
model:
  provider: custom
  base_url: http://127.0.0.1:8080/v1
  api_key: a-local-key
mcp_servers:
  higgsfield:
    url: https://mcp.example.com/mcp
    enabled: false
agent:
  max_turns: 60
"""


def with_(base, extra):
    return base + extra


def t_parser():
    print("\n1. the YAML subset reader agrees with PyYAML on what it accepts")
    if not need("_hcfg_parse"):
        return
    fx = """\
# a comment
model:
  default: "mlx-community/Qwen3.8-27B-4bit"
  base_url: http://127.0.0.1:8080/v1   # trailing comment
  context_length: 262144
lit: |
  line one
    indented
  line three

fold: >-
  para one
  continues

  para two
flowl: [a, "b, c", 'd', 3, true]
flowm: {x: 1, y: "two", z: off}
seq:
  - one
  - two: 2
    three: 3
  - "quoted # not a comment"
  - /bin/tool: arg
  - http://host/path
emptyval:
nulls: ~
yes_is: yes
On_cap: On
octal: 0755
hexa: 0x1F
sexa: 1:30
clock: 07:00
notfloat: 1e5
flt: 1.5
under: 1_000
str_colon: "a: b"
url_frag: http://x.y/z#frag
multi_plain: this is
  continued here
gap_plain: first

  second
it's: 'it''s'
platform_toolsets:
  cli:
  - terminal
  telegram:
  - hermes-telegram
"""
    want = {
        "model": {"default": "mlx-community/Qwen3.8-27B-4bit",
                  "base_url": "http://127.0.0.1:8080/v1", "context_length": 262144},
        "lit": "line one\n  indented\nline three\n",
        "fold": "para one continues\npara two",
        "flowl": ["a", "b, c", "d", 3, True],
        "flowm": {"x": 1, "y": "two", "z": False},
        "seq": ["one", {"two": 2, "three": 3}, "quoted # not a comment",
                {"/bin/tool": "arg"}, "http://host/path"],
        "emptyval": None, "nulls": None, "yes_is": True, "On_cap": True,
        "octal": 493, "hexa": 31, "sexa": 90, "clock": "07:00", "notfloat": "1e5",
        "flt": 1.5, "under": 1000, "str_colon": "a: b", "url_frag": "http://x.y/z#frag",
        "multi_plain": "this is continued here", "gap_plain": "first\nsecond",
        "it's": "it's",
        "platform_toolsets": {"cli": ["terminal"], "telegram": ["hermes-telegram"]},
    }
    tree, probs = parse(fx)
    check("no problems reported for valid YAML", probs == [], probs)
    for k, v in want.items():
        check("%s parses to %r" % (k, v), tree.get(k) == v, tree.get(k))
    check("nothing extra", set(tree) == set(want), sorted(set(tree) ^ set(want)))

    print("\n2. what it does NOT understand is reported by line, never guessed")
    bad = """\
approvals:
  mode: manual
base: &b
  x: 1
other: *b
\tmode: off
list: [a, [b]]
quote: "unterminated
tts:
  provider: piper
"""
    tree, probs = parse(bad)
    lines = sorted({ln for ln, _ in probs})
    check("anchor, alias, TAB, nested flow and open quote are all problems",
          lines == [3, 5, 6, 7, 8], probs)
    check("the readable keys around them still parse",
          tree.get("approvals") == {"mode": "manual"} and tree.get("tts") == {"provider": "piper"}, tree)
    check("the TAB line never becomes approvals.mode",
          tree.get("approvals", {}).get("mode") == "manual", tree)
    tree, probs = parse("- a\n- b\n")
    check("a top-level list is a problem, not a config", tree == {} and probs, (tree, probs))
    tree, probs = parse("lit: |\n  \tkept with its TAB\nnext: 1\n")
    check("a TAB inside a block scalar is content, not a problem",
          probs == [] and tree == {"lit": "\tkept with its TAB\n", "next": 1}, (tree, probs))

    print("\n2b. what PyYAML's own dumper writes (values checked against PyYAML 6.0.3)")
    dumped = """\
' lead': 1
8080: port
esc: "a\\x41\\u00e9\\U0001F600\\N\\_b"
wrapped: "abc \\
  def\\
  \\ ghi"
heading: 'one

  # two
  three'
dq_hash: "x
  #y"
"""
    tree, probs = parse(dumped)
    check("no problems reported", probs == [], probs)
    for k, v in ((" lead", 1), (8080, "port"), ("esc", "aA\u00e9\U0001F600\x85\xa0b"),
                 ("wrapped", "abc def ghi"), ("heading", "one\n# two three"), ("dq_hash", "x #y")):
        check("%r parses to %r" % (k, v), tree.get(k) == v, tree.get(k))
    check("a quoted key keeps its spaces, a plain number key is an int",
          " lead" in tree and "lead" not in tree and 8080 in tree and "8080" not in tree, sorted(map(repr, tree)))
    tree, probs = parse("l:\n- ? x\n  : 1\nk: 2\n")
    check("a complex '? key' item is reported, never read as text",
          [ln for ln, _ in probs] == [2] and tree.get("l") == [None] and tree.get("k") == 2, (tree, probs))

    print("\n3. it never raises and always ends, whatever it is fed")
    rnd = random.Random(1)
    junk = ["&a", "*a", "!!str", "[", "{", "]", "\t", "- ", "- - x", "'", '"', "|", ">",
            ":", "#", "---", "key", ": v", "x: [a, [b]]", "%YAML 1.1", "?", "@x", "\r", "﻿"]
    base = SAFE.split("\n")
    crashed = 0
    for _ in range(600):
        ls = list(base)
        for _ in range(rnd.randint(1, 5)):
            i = rnd.randrange(len(ls))
            pos = rnd.randint(0, len(ls[i]))
            ls[i] = ls[i][:pos] + rnd.choice(junk) + ls[i][pos:]
        try:
            t, p = parse("\n".join(ls))
            d._drift_findings(t, plugins_dir=TMP)
            ok_shape = isinstance(t, dict) and isinstance(p, list)
        except Exception:
            ok_shape = False
        crashed += 0 if ok_shape else 1
    check("600 mangled configs: no exception, always (dict, problems)", crashed == 0, crashed)


def t_rules():
    if not need("_drift_findings"):
        return
    print("\n4. a safe config is all PASS")
    r = rules(SAFE)
    worst = sorted({s for v in r.values() for s, _, _ in v})
    check("every finding PASS", worst == ["pass"], {k: [s for s, _, _ in v] for k, v in r.items()})
    for rid in ("approvals", "tts", "computer_use", "mcp", "max_turns", "base_url"):
        check("rule %s reported" % rid, rid in r, sorted(r))

    print("\n5. approvals.mode — the human gate")
    noapp = SAFE.replace("approvals:\n  mode: manual\n", "")
    check("unpinned -> WARN (0.19+ reads that as smart)", statuses(noapp, "approvals") == ["warn"])
    check("unpinned names smart", "smart" in text_of(noapp, "approvals"))
    for val, want in (("off", "fail"), ('"off"', "fail"), ("smart", "warn"), ("Manual", "pass"),
                      ("auto", "warn"), ("''", "warn"), ("true", "pass")):
        got = statuses(SAFE.replace("mode: manual", "mode: %s" % val), "approvals")
        check("mode: %s -> %s" % (val, want), got == [want], got)
    check("the fix pins manual",
          "hermes config set approvals.mode manual" in text_of(SAFE.replace("mode: manual", "mode: smart"), "approvals"))
    for val, warns in (("approve", True), ('"off"', True), ("off", False), ("deny", False), ("yes", False)):
        got = statuses(SAFE.replace("  mode: manual\n", "  mode: manual\n  cron_mode: %s\n" % val), "approvals")
        check("cron_mode: %s %s" % (val, "warns" if warns else "does not warn"),
              ("warn" in got) == warns, got)

    print("\n6. tts.provider — where spoken text goes")
    notts = SAFE.replace("tts:\n  provider: piper\n", "")
    check("unset -> WARN naming Edge's cloud voices",
          statuses(notts, "tts") == ["warn"] and "Edge" in text_of(notts, "tts"), text_of(notts, "tts"))
    for prov, want in (("edge", "warn"), ("elevenlabs", "warn"), ("openai", "warn"), ("gemini", "warn"),
                       ("piper", "pass"), ("neutts", "pass"), ("kittentts", "pass"), ("mystery", "warn")):
        got = statuses(SAFE.replace("provider: piper", "provider: %s" % prov), "tts")
        check("tts.provider %s -> %s" % (prov, want), got == [want], got)
    cmd = SAFE.replace("tts:\n  provider: piper\n",
                       "tts:\n  provider: mine\n  providers:\n    mine:\n      type: command\n"
                       "      command: \"say -o {output_path} < {input_path}\"\n")
    check("a command provider -> PASS", statuses(cmd, "tts") == ["pass"], statuses(cmd, "tts"))
    check("toolsets without tts: says it is not on today",
          "not on for cli or telegram" in text_of(SAFE.replace("provider: piper", "provider: edge"), "tts"))
    full = notts.replace("platform_toolsets:\n  cli:\n  - terminal\n  - file\n  - web\n  telegram: [terminal, file]\n", "")
    check("default toolsets: says it is on for cli and telegram",
          "on for cli" in text_of(full, "tts") and "telegram" in text_of(full, "tts"), text_of(full, "tts"))

    print("\n7. computer_use on surfaces with no approval callback (C111)")
    nopt = SAFE.replace("platform_toolsets:\n  cli:\n  - terminal\n  - file\n  - web\n  telegram: [terminal, file]\n", "")
    t = text_of(nopt, "computer_use")
    check("no platform_toolsets -> WARN for both surfaces",
          statuses(nopt, "computer_use") == ["warn"] and "hermes-cli" in t and "hermes-telegram" in t, t)
    explicit = SAFE.replace("  - web\n", "  - web\n  - computer_use\n")
    check("cli lists computer_use -> WARN",
          statuses(explicit, "computer_use") == ["warn"] and "cli" in text_of(explicit, "computer_use"))
    check("the fix is the per-platform disable command",
          "hermes tools disable computer_use --platform cli" in text_of(explicit, "computer_use"))
    for alias in ("all", "'*'"):
        every = SAFE.replace("telegram: [terminal, file]", "telegram: [%s]" % alias)
        check("telegram: [%s] (every toolset) -> WARN" % alias,
              statuses(every, "computer_use") == ["warn"] and "telegram via" in text_of(every, "computer_use"),
              text_of(every, "computer_use"))
    narrow = SAFE.replace("  - terminal\n  - file\n  - web\n", "  - hermes-acp\n").replace(
        "telegram: [terminal, file]", "telegram: [hermes-webhook]")
    check("narrow composites (acp, webhook) -> PASS", statuses(narrow, "computer_use") == ["pass"],
          text_of(narrow, "computer_use"))
    gated = nopt + "plugins:\n  enabled:\n  - tool-budget\n  - approval-gate\n"
    pdir = os.path.join(TMP, "plugins-on")
    os.makedirs(os.path.join(pdir, "approval-gate"), exist_ok=True)
    check("approval-gate enabled and on disk -> PASS",
          statuses(gated, "computer_use", plugins_dir=pdir) == ["pass"], text_of(gated, "computer_use", pdir))
    check("approval-gate enabled but missing -> WARN",
          statuses(gated, "computer_use", plugins_dir=os.path.join(TMP, "plugins-off")) == ["warn"])
    both = gated + "  disabled:\n  - approval-gate\n"
    check("approval-gate also in plugins.disabled -> WARN",
          statuses(both, "computer_use", plugins_dir=pdir) == ["warn"], text_of(both, "computer_use", pdir))

    print("\n8. the placeholder model key (C130) — and real keys are never echoed")
    ph = SAFE.replace("api_key: a-local-key", "api_key: local-anything") + \
        "custom_providers:\n- name: bg\n  base_url: http://127.0.0.1:8081/v1\n  api_key: local-anything\n"
    t = text_of(ph, "api_key")
    check("placeholder on model and custom provider -> WARN naming both",
          statuses(ph, "api_key") == ["warn"] and "model.api_key" in t and "bg" in t, t)
    real = SAFE.replace("api_key: a-local-key", "api_key: sk-live-0123456789abcdefSECRET")
    everything = " ".join(det + fix for v in rules(real).values() for _, det, fix in v)
    check("a real key is not a finding", "api_key" not in rules(real))
    check("and never appears in any line", "SECRET" not in everything)

    print("\n9. MCP servers and sampling")
    on = SAFE.replace("    enabled: false\n", "    enabled: true\n")
    t = text_of(on, "mcp")
    check("an enabled remote server -> WARN with its host", statuses(on, "mcp") == ["warn"]
          and "mcp.example.com" in t and "sampling on" in t, t)
    sec = on.replace("https://mcp.example.com/mcp", "https://user:tok3n@mcp.example.com/mcp?key=QSECRET")
    t = text_of(sec, "mcp")
    check("credentials and query in the URL are not echoed", "tok3n" not in t and "QSECRET" not in t, t)
    check("enabled: \"false\" (boolish) is off",
          statuses(SAFE.replace("enabled: false", 'enabled: "false"'), "mcp") == ["pass"])
    check("a disabled server is counted, not flagged", "(1 disabled)" in text_of(SAFE, "mcp"))
    stdio = SAFE.replace("  higgsfield:\n    url: https://mcp.example.com/mcp\n    enabled: false\n",
                         "  files:\n    command: /usr/local/bin/npx\n    sampling:\n      enabled: false\n")
    t = text_of(stdio, "mcp")
    check("a stdio server -> WARN, its command reduced to a basename, sampling off said so",
          statuses(stdio, "mcp") == ["warn"] and "stdio npx" in t and "sampling on" not in t, t)
    strf = stdio.replace("enabled: false", 'enabled: "false"')
    check("sampling.enabled \"false\" is still ON (Hermes tests it for truthiness)",
          "sampling on" in text_of(strf, "mcp"), text_of(strf, "mcp"))

    print("\n10. the 0.22 upgrade keys warn only when present and risky")
    check("sessions absent -> no finding", "sessions" not in rules(SAFE))
    check("auto_prune false -> no finding", "sessions" not in rules(SAFE + "sessions:\n  auto_prune: false\n"))
    ap = SAFE + "sessions:\n  auto_prune: true\n  retention_days: 30\n"
    check("auto_prune true -> WARN with its retention", statuses(ap, "sessions") == ["warn"]
          and "30 days" in text_of(ap, "sessions"), text_of(ap, "sessions"))
    check("auto_prune \"false\" (a string, truthy to Hermes) -> WARN",
          statuses(SAFE + 'sessions:\n  auto_prune: "false"\n', "sessions") == ["warn"])
    check("max_turns 60 -> PASS", statuses(SAFE, "max_turns") == ["pass"])
    check("max_turns 500 -> WARN", statuses(SAFE.replace("max_turns: 60", "max_turns: 500"), "max_turns") == ["warn"])
    check("max_turns absent -> PASS (not pinned)",
          statuses(SAFE.replace("agent:\n  max_turns: 60\n", ""), "max_turns") == ["pass"])
    check("legacy root max_turns 200 -> WARN",
          statuses(SAFE.replace("agent:\n  max_turns: 60\n", "max_turns: 200\n"), "max_turns") == ["warn"])

    print("\n11. inference stays on loopback")
    for url, warns in (("https://api.openai.com/v1", True), ("http://localhost:8080/v1", False),
                       ("http://[::1]:8080/v1", False), ("http://127.0.0.2:8080", False),
                       ("http://192.168.1.5:8080/v1", True)):
        got = statuses(SAFE.replace("http://127.0.0.1:8080/v1", url), "base_url")
        check("base_url %s %s" % (url, "warns" if warns else "passes"), ("warn" in got) == warns, got)
    leak = SAFE.replace("http://127.0.0.1:8080/v1", "https://me:pw0rdSECRET@evil.example/v1")
    t = text_of(leak, "base_url")
    check("a remote base_url is reduced to its host", "evil.example" in t and "pw0rd" not in t, t)
    nested = SAFE + "custom_providers:\n- name: bg\n  base_url: https://openrouter.ai/api/v1\n"
    check("a custom provider's base_url is walked too", "custom_providers[0].base_url" in text_of(nested, "base_url"))
    for key, val, warns in (("provider", "openrouter", True), ("provider", "custom", False),
                            ("provider", "custom:bg", False), ("provider", "auto", False)):
        got = statuses(SAFE.replace("provider: custom", "%s: %s" % (key, val)), "provider")
        check("model.provider %s %s" % (val, "warns" if warns else "passes"), ("warn" in got) == warns, got)
    check("auxiliary.vision.provider openrouter -> WARN",
          "warn" in statuses(SAFE + "auxiliary:\n  vision:\n    provider: openrouter\n", "provider"))
    check("fallback_model.provider anthropic -> WARN",
          "warn" in statuses(SAFE + "fallback_model:\n  provider: anthropic\n  model: x\n", "provider"))
    check("fallback_providers non-empty -> WARN",
          "warn" in statuses(SAFE + "fallback_providers:\n- openrouter\n", "provider"))


class _Trap(Exception):
    pass


def _snapshot(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            out[p] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def t_check():
    print("\n12. the check itself: registered, read-only, offline, honest")
    ids = [c[0] for c in d.CHECKS]
    check("hermes_drift is a registered check", "hermes_drift" in ids, ids)
    if "hermes_drift" not in ids:
        return
    check("it sits right after config", ids.index("hermes_drift") == ids.index("config") + 1, ids)
    fn = [c[2] for c in d.CHECKS if c[0] == "hermes_drift"][0]
    check("it reads the config under HOME", d.HERMES_CONFIG == CFG, d.HERMES_CONFIG)

    def trap(*a, **k):
        raise _Trap("touched %r" % (a[:1],))
    saved = (subprocess.run, subprocess.Popen, subprocess.check_output, urllib.request.urlopen,
             socket.socket.connect, socket.create_connection)

    def run(text, raw=None):
        if os.path.isdir(CFG):
            os.rmdir(CFG)
        elif os.path.exists(CFG):
            os.remove(CFG)
        if raw is not None:
            with open(CFG, "wb") as f:
                f.write(raw)
        elif text is not None:
            with io.open(CFG, "w", encoding="utf-8") as f:
                f.write(text)
        before = _snapshot(HOME)
        subprocess.run = subprocess.Popen = subprocess.check_output = trap
        urllib.request.urlopen = socket.create_connection = trap
        socket.socket.connect = trap
        try:
            res = fn()
        finally:
            (subprocess.run, subprocess.Popen, subprocess.check_output, urllib.request.urlopen,
             socket.socket.connect, socket.create_connection) = saved
        return res, before == _snapshot(HOME)

    res, ro = run(SAFE)
    check("a safe config -> PASS", res.get("status") == "pass", res)
    check("PASS lists what is fine", "approvals manual" in res.get("detail", ""), res)
    check("nothing was written, no subprocess, no socket", ro)
    res, ro = run(SAFE.replace("mode: manual", "mode: off"))
    check("approvals off -> FAIL", res.get("status") == "fail", res)
    check("the fix is a command the owner runs", "hermes config set approvals.mode manual" in res.get("fix", ""), res)
    check("still read-only", ro)
    res, ro = run(SAFE.replace("mode: manual", "mode: smart").replace("provider: piper", "provider: edge"))
    check("two warnings -> WARN carrying both", res.get("status") == "warn"
          and "smart" in res["detail"] and "edge" in res["detail"], res)
    res, ro = run(None)
    check("no config -> WARN saying Hermes' defaults apply", res.get("status") == "warn"
          and "defaults apply" in res.get("detail", ""), res)
    check("still read-only with no file", ro and not os.path.exists(CFG))
    res, ro = run(SAFE + "extra: &anchor\n  x: 1\n")
    check("an unreadable line -> WARN naming its line", res.get("status") == "warn"
          and "line 21" in res.get("detail", ""), res)
    res, ro = run(None, raw=b"approvals:\n  mode: \xff\xfe\n")
    check("bytes that are not UTF-8 -> WARN, nothing checked", res.get("status") == "warn"
          and "could not read" in res.get("detail", ""), res)
    os.remove(CFG)
    os.makedirs(CFG)
    res = fn()
    check("a config path that is a directory -> WARN, never a crash", res.get("status") == "warn", res)
    os.rmdir(CFG)


def main():
    try:
        t_parser()
        t_rules()
        t_check()
    except Exception as e:                       # a crash is a failure, not a hang-up
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
