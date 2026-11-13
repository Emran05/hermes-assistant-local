# Tests

Every suite here was written against this repo's own code while the feature it
covers was being built. They are plain scripts — stdlib Python, plain Node,
bash — with no test framework, because the thing under test is a stdlib-only
dashboard that must keep working on a Mac with nothing installed.

```
tools/tests/
  run.sh          the one runner:  run.sh unit|live|browser|ac|all
  lib/            the offline guards (Python + Node) the unit tier runs under
  unit/           no dashboard, no network, no model   ← this is what CI runs
  live/           needs the dashboard on 127.0.0.1:7788
  browser/        Playwright, needs the dashboard
  ac/             anything that loads a model (see ac/README.md)
```

## Tiers

| Tier | Needs | What it may touch | Runs in CI |
|---|---|---|---|
| `unit` | `python3`, `node` | throwaway `HOME`s and `tempfile` scratch only | yes (macOS) |
| `live` | the running dashboard | the live API, **read-only or capture-and-restore** | no |
| `browser` | the dashboard + Playwright | the UI in a headless Chromium | no |
| `ac` | the Mac on AC power | loads a model — refused on battery | never |

The unit tier does not merely *promise* it needs nothing live. `run.sh unit`
exports `HERMES_TESTS_OFFLINE=1` and puts `lib/` on `PYTHONPATH`, where
`usercustomize.py` is picked up automatically by every `python3` the tier
starts. It refuses **every non-loopback destination** — TCP `connect`, UDP
`sendto` and name resolution (`getaddrinfo`/`gethostbyname`) alike — and, even
on loopback, the dashboard (7788), both model lanes (8080, 8081) and the serve
backend (9119). A unit test that quietly leaned on the owner's running Mac, or
on the internet, fails here instead of passing on one machine and failing in
CI. (The guard is a `usercustomize`, not a `sitecustomize`: Homebrew ships its
own `sitecustomize.py` and shadowing it breaks site-packages resolution.)

Loopback on every other port stays open — start a throwaway `http.server` on
**port 0** and talk to it; never bind a fixed port (two checkouts running the
tier at once collide). A refusal is an ordinary `OSError`
(`ECONNREFUSED`/`ENETUNREACH`) or `socket.gaierror`, so code that handles "the
network is down" sees exactly that. If a suite execs `server.py`, the aux
modules start their background loops at import (watchtower, intel, for-you,
evals…) unless the `_<name>_thread_started` flag is already in the globals —
set those flags first, the way `unit/ny_harness.py` does, and stub any widget
provider (markets, RSS…) a function under test calls.

Every refusal is appended to `$HERMES_TESTS_EGRESS_LOG` (run.sh points it at a
per-suite temp file) and the summary prints `egress refused by the offline
guard: <suite>: N refused — e.g. …` for any suite that tried. That line is not
a failure — the call never left — but it names a suite that is leaning on the
network, and it should be fixed.

**Node suites get the same guard.** `run.sh unit` also sets
`NODE_OPTIONS="--require lib/offline-guard.js"` for the tier. It patches
`net.Socket.prototype.connect` — the one door every Node TCP client goes
through (http/https, tls, undici's `fetch` and `WebSocket`) — and, for a clear
error at the call site, `http(s).request`/`get` and `fetch` for a string, a
`URL` object or a `Request`. Same rules: non-loopback and the four ports are
refused. Not covered: UDP (`dgram`) to an IP literal, and anything a suite
shells out to (curl via `child_process`); a `python3` child gets the Python
guard automatically, anything else has to say so in its own docstring.

**The self-check is honest.** Before the tier runs, `lib/guard_selfcheck.py`
and `lib/guard_selfcheck.js` run in fresh processes with the tier's exact
environment and ask whether START-UP loaded the guard — they never import or
require it themselves (an explicit import succeeds even when the interpreter
skipped `usercustomize` — a venv, `PYTHONNOUSERSITE=1`, `python3 -s` — which is
exactly the unguarded case). Then each proves the guard behaviourally against
a listener it opened itself, so a broken guard reaches that listener and never
a service the owner is running. `run.sh` unsets `PYTHONNOUSERSITE` for the
tier; a venv `python3` (no user site) fails the check — use the system or
Homebrew python3.

**Strict mode.** By default a python3 or node that failed to load its guard
only prints a `WARNING` and the tier still runs (useful when iterating
locally with a stray `PYTHONPATH`/`NODE_OPTIONS`). Set `HERMES_TESTS_STRICT=1`
— or run under CI, where GitHub Actions already sets `CI=true` and `run.sh`
treats that the same way — and a guard that fails to load is a hard failure
of the whole run instead: an unguarded unit tier proves nothing, so CI must
never silently fall back to running it that way.

```bash
HERMES_TESTS_STRICT=1 tools/tests/run.sh unit     # fail loudly if either guard is missing
```

**The model-wake guard is stricter than "no model at the end".** The runner
snapshots the REAL model launchers once before the tier(s) run and once after
— it parses `ps` and counts only a python whose script is `mlx-vlm-launch.py`
(not its `--local-ready` probe) or that runs `-m mlx_lm server`, recorded as
pid@start-time pairs, so another shell, a `grep` or an editor whose command
line merely mentions the name no longer trips it (R2-61) — and fails the run
if the two snapshots differ **in either direction**: a launcher that
*appeared* during the run (a test woke or started one) or one that
*disappeared* (a test killed a lane that was already up for some other reason)
are both bugs in a test, not just the "started" case. Because the comparison
is before-vs-after rather than "empty at the end", a run that begins with a
lane already resident is not a safe baseline either: run `tools/tests/run.sh`
only when no model server is running, the same way CI's own before/after
snapshot refuses to even start if one is resident going in.

**Every suite has a time limit.** `HERMES_TESTS_SUITE_TIMEOUT` (seconds,
default 600; `0` disables) — the runner starts each suite in its own process
group and, on expiry, terminates (then kills) the whole group and fails that
suite by name with a `TIMEOUT run.sh:` line, so one hung test can no longer
block a run forever. A child the suite starts with `start_new_session` leaves
the group and escapes the kill. The CI jobs have `timeout-minutes` too.

**git is guarded as well.** The unit tier exports `GIT_ALLOW_PROTOCOL=file`,
so a git a suite runs cannot fetch over the network. A child started with a
stripped environment (`python -I`, `env={"PATH": ...}`, `gh_git.py`'s own git
env) inherits neither guard; such a suite must say how it stays offline.

## Running them

```bash
tools/tests/run.sh              # unit — the default
tools/tests/run.sh unit
tools/tests/run.sh live         # start the dashboard first
tools/tests/run.sh browser      # needs Playwright, see below
tools/tests/run.sh all          # unit + live + browser
tools/tests/run.sh ac           # refused unless pmset reports AC power
```

Every suite prints its own per-check lines; the runner prints a table of
per-suite pass/fail counts and one total, and exits 1 if anything failed.

`browser` is skipped cleanly (not failed) when Playwright is missing. To supply
it, point `PWENV` at a venv that has it:

```bash
python3 -m venv /tmp/pwenv
/tmp/pwenv/bin/pip install playwright
/tmp/pwenv/bin/playwright install chromium
PWENV=/tmp/pwenv tools/tests/run.sh browser
```

Screenshots go to a `tempfile` directory whose path each suite prints; set
`HERMES_TEST_SHOTS=/some/dir` to keep them somewhere you choose.

## Skips and CI

A skipped check is a smaller suite, not a passing one. CI runs on a fresh
macOS runner with no `~/.hermes`, so the few checks that need an installed
layer (the Hermes checkout at `~/.hermes/hermes-agent`, the mlx-vlm venv, a
python with mlx-lm) legitimately skip there — and nothing else may. No unit
suite reads the owner's data (conversations, `recorder.db`, `config.yaml`, the
calendar): those checks run on synthetic fixtures everywhere. The offline
guard enforces the part a suite could reach by accident: the installed
`shortcuts`, `osascript` and `icalBuddy` (the owner's Shortcuts, Reminders and
calendar) are refused for every Python process of the tier, like a
non-loopback connection; a suite that needs one puts a stub of that name in a
scratch folder.
`ci.yml` sets

```yaml
HERMES_TESTS_FAIL_ON_SKIP: "1"
HERMES_TESTS_EXPECTED_SKIPS: |
  t_c111_approval_gate.py: no Hermes install
  ...
```

and `run.sh` then fails the run for every skip that is not on that list. An
entry is `<suite>` (any skip in it) or `<suite>: <text>` (only a reason that
contains `<text>`); entries are one per line or `;`-separated, `#` starts a
comment. Locally the gate is off (skips are listed, the run stays green); set
`HERMES_TESTS_FAIL_ON_SKIP=1` to see what CI will say. **Adding a suite that
skips on the runner means adding its entry to `ci.yml` in the same change**,
with the reason it cannot run there.

## The rule: capture and restore

**A test that changes one of the owner's settings must capture the old value
first and put it back on every exit path — including a failure, an exception, a
`^C`.**

This is not a style preference. An earlier acceptance script needed the Claude
escalation switch ON to exercise the escalation path, so it turned it on… and
left it on. The switch stayed on for days, quietly escalating turns to Claude
that the owner had deliberately kept local. Nothing failed; nothing was logged;
the only symptom was a bill and a setting nobody remembered flipping.

So `live/verify_round2.sh` opens with the capture and installs the restore
before its first assertion:

```bash
E0=$(curl -fsS $B/api/claude/escalate | ... )      # what the OWNER had — a real boolean
case "$E0" in true|false) ;; *) exit 2 ;; esac     # unreadable: change NOTHING
restore(){ curl -fsS -X POST ... --data "{\"enabled\":$E0}" ... ; }
trap restore EXIT                                  # every exit path, not the happy one
```

**The capture fails closed.** A read that fails, times out, answers HTML or
answers a non-boolean is not "probably on": the script refuses to run and
sends nothing (it used to fall back to `true`, so a dashboard that was down
made the EXIT trap switch escalation ON — C61). Probes that only test a status
code send the owner's own value, so even a broken guard changes nothing, and a
check that would spend money (`claude/think`) runs only once the switch is
verifiably off. It does the same for the pause/idle marker files, restoring the
exact prior combination rather than a "sensible default" — and never removing
a marker the owner had set. `unit/t_c61_verify_round2.py` runs the real script
against a fake dashboard (`curl` and `pgrep` shimmed on `PATH`) to hold it to
all of that. `live/t_mcp.py` snapshots
`~/.hermes/mcp-allow.json` byte-for-byte at the start and asserts at the end
that it is unchanged.

The strongest form of the rule is the unit tier's: don't touch the owner's state
at all. Point `HOME` at a `tempfile.mkdtemp()`, write fixtures there, and assert
at the end that nothing was written outside it. A unit suite never READS the
owner's data either — no conversation, recorder row, calendar event or config
file from `~/.hermes` (or anything an EventKit tool such as icalBuddy would
read): build a synthetic fixture in the stored on-disk shape instead
(`unit/index_harness.py` and `unit/convos_harness.py` do, and the harness's
calendar section puts a stand-in `icalBuddy` first on `PATH`). The only
reads of the real home that remain are optional, read-only CODE layers —
Hermes' own source checkout (`~/.hermes/hermes-agent`) and the mlx-vlm venv —
which skip cleanly when absent. Capture `os.path.expanduser("~")` *before*
redirecting `HOME` if you need it: afterwards it follows the throwaway HOME.

## Adding a test

1. **Pick the tier by what the test needs**, not by what it is about. If it can
   run with the dashboard stopped and the Mac offline, it belongs in `unit/` —
   that is the only tier CI protects.
2. **Derive the repo root from the file**, never from `~` or a literal path.
   CI's hygiene job fails on any `/Users/<name>` in a tracked file.
   ```python
   _HERE = os.path.dirname(os.path.abspath(__file__))
   REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
       os.path.dirname(os.path.dirname(_HERE)))
   ```
   ```js
   const REPO = process.env.HERMES_REPO ||
                path.resolve(__dirname, "..", "..", "..");
   ```
   `run.sh` exports `HERMES_REPO`, so a suite run through the runner never has
   to guess.
3. **Scratch goes under `tempfile`**, never beside the test and never in `/tmp`
   by hand. Clean up at the end; a fixture the next run inherits is a test that
   passes for the wrong reason.
4. **Load dashboard modules the way `server.py` does** — `exec` the `aux_*.py`
   into a globals dict carrying `HOME`/`DATA`/`_state_lock`/`register_get` etc.,
   or `eval` the `aux_*.js` in Node with `window` stubbed and no `document` (the
   pure renderers must not need one). Both patterns are all over `unit/`; copy
   the nearest neighbour.
   **Assert on the real code, never on a copy of it.** A stand-in only proves
   the module works against the stand-in (C84: six suites carried their own
   `settings_update` — one with an RLock that would hide a deadlock — two
   their own `esc()`, one a trimmed copy of the secret scrubber, one its own
   re-implementation of the doctor's boot-scoped count). What a module needs
   from server.py comes from server.py:
   ```python
   sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
   import server_real
   g.update(server_real.settings_api(settings_path, lock))  # settings_update,
       # settings_set, get_settings, read_json, write_json, _state_lock
   server_real.server_functions(["apple_apps_enabled"], ns)  # any pure def
   ```
   and from the inline script, `require(".../tools/tests/lib/index_helpers.js")
   .indexFunction("esc")`. Pure functions in an aux module are `ast`-extracted
   from the file (see `unit/t_a02_escalation_default.py`); side effects —
   `subprocess`, `launchctl`, a restart, a network call — are what gets
   stubbed, and only those.
5. **Print one line per check**, and finish with the line the runner reads:
   ```
   TESTS <n> passed <n> failed
   ```
   (optionally `TESTS <n> passed <n> failed <n> skipped`). Exit non-zero when
   anything failed. A suite that exits without printing that line is a
   **FAIL**, and so is `TESTS 0 passed 0 failed` — `run.sh` never credits a
   suite for assertions it did not run, whatever its exit code was.
6. **Skips are reported, never credited.** A check that cannot run on this
   machine prints a line `SKIP <reason>` (any indent) instead of silently
   returning; a suite that cannot run at all prints `SKIP <reason>` at column 0
   with no passing checks and exits 0. The runner counts them in their own
   column, lists every reason under `skipped (n):`, and a SKIP line never hides
   a failure (a suite that printed SKIP **and** a failing `TESTS` line, or
   exited non-zero, is a FAIL). See "Skips and CI" below.
7. **Never start a model.** If a test genuinely needs one, it goes in `ac/`, it
   is documented there, and it never runs in CI.
8. Run `tools/tests/run.sh unit` before you push; CI runs exactly that.
