# Tests

Every suite here was written against this repo's own code while the feature it
covers was being built. They are plain scripts — stdlib Python, plain Node,
bash — with no test framework, because the thing under test is a stdlib-only
dashboard that must keep working on a Mac with nothing installed.

```
tools/tests/
  run.sh          the one runner:  run.sh unit|live|browser|ac|all
  lib/            the offline guard the unit tier runs under
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
starts. It refuses TCP connections to the dashboard (7788), the model server
(8080) and the serve backend (9119). A unit test that quietly leaned on the
owner's running Mac fails here instead of passing on one machine and failing in
CI. (The guard is a `usercustomize`, not a `sitecustomize`: Homebrew ships its
own `sitecustomize.py` and shadowing it breaks site-packages resolution.)

The runner also snapshots `pgrep -f 'mlx-vlm-launch|mlx_lm server'` before and
after every run outside the `ac` tier. If a model server appears, the run fails
even when every check passed — waking an 18 GB model on a laptop on battery is a
bug, not a side effect.

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
E0=$(curl -s $B/api/claude/escalate | ... )        # what the OWNER had
restore_esc(){ curl -s -X POST ... --data "{\"enabled\":$E0}" ... ; }
trap restore_esc EXIT                              # every exit path, not the happy one
```

and it does the same for the pause/idle marker files, restoring the exact prior
combination rather than a "sensible default". `live/t_mcp.py` snapshots
`~/.hermes/mcp-allow.json` byte-for-byte at the start and asserts at the end
that it is unchanged.

The strongest form of the rule is the unit tier's: don't touch the owner's state
at all. Point `HOME` at a `tempfile.mkdtemp()`, write fixtures there, and assert
at the end that the real store is exactly as it was. `unit/index_harness.py`
captures the real `chats/` listing and `index.db`'s (mtime, size) *before* it
redirects `HOME`, and compares at the end — because `os.path.expanduser("~")`
follows the throwaway HOME and would silently check the wrong thing.

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
5. **Print one line per check**, and finish with the line the runner reads:
   ```
   TESTS <n> passed <n> failed
   ```
   Exit non-zero when anything failed. A suite that cannot run at all should
   print a line starting with `SKIP` and exit 0 — the runner reports it as
   skipped rather than green.
6. **Never start a model.** If a test genuinely needs one, it goes in `ac/`, it
   is documented there, and it never runs in CI.
7. Run `tools/tests/run.sh unit` before you push; CI runs exactly that.
