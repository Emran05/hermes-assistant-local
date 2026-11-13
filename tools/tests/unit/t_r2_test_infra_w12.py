#!/usr/bin/env python3
"""R2-61 — three test-infrastructure faults that failed runs for the wrong reason.

  a. run.sh's model-wake guard pgrep'd its pattern system-wide: another
     agent's shell / grep whose command line merely mentioned "mlx_lm server"
     failed the run — while a recycled PID let a new launcher through.  Now it
     records REAL launchers as pid@start-time pairs; it still trips in either
     direction (tools/tests/README.md), on a NEW pair and on a pair that is
     GONE after the run.
     Exercised by running the REAL run.sh (copied into a throwaway tree with a
     one-suite unit/ dir) with `ps` and `pgrep` stubbed on PATH from fixtures.
  b. t_apple_apps.py counted call sites in every untracked directory (another
     agent's scratch copies).  It now scans `git ls-files`.  Exercised by
     running the REAL t_apple_apps.py against a throwaway git copy of
     dashboard/ + tools/tests/ with an untracked mutant beside it — and a
     TRACKED new call site must still fail it.
  c. t_c38_draft_session.py bound the fixed port 7803, so two runs at once
     collided.  It now binds a port the kernel picks.  Exercised by running two
     copies of the REAL suite at the same time.

Offline: nothing leaves loopback, nothing touches the real ~/.hermes.
"""
import json, os, shutil, stat, subprocess, sys, tempfile

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


TMP = tempfile.mkdtemp(prefix="t-r2-infra-")
# the outer tier's guard / suite variables must not leak into a nested run
SCRUB = ("PYTHONPATH", "NODE_OPTIONS", "CI", "HERMES_TESTS_OFFLINE", "HERMES_TESTS_STRICT",
         "HERMES_TESTS_SUITE", "HERMES_TESTS_EGRESS_LOG", "HERMES_TESTS_FAIL_ON_SKIP",
         "HERMES_TESTS_EXPECTED_SKIPS", "HERMES_REPO", "HERMES_DASH_BASE", "HERMES_MCP_DASHBOARD")


def clean_env(**kw):
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env["HOME"] = os.path.join(TMP, "home")
    os.makedirs(env["HOME"], exist_ok=True)
    env.update(kw)
    return env


# --------------------------------------------------------------------------
# a. the model-wake guard
# --------------------------------------------------------------------------
STUB = r'''#!/usr/bin/env python3
# fake %(tool)s: call 1 answers from before.txt, every later call from after.txt
import os, re, sys
d = os.environ["FAKE_PS_DIR"]
n_path = os.path.join(d, "%(tool)s.n")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
rows = [l.rstrip("\n") for l in open(os.path.join(d, "before.txt" if n == 1 else "after.txt"))
        if l.strip()]
if "%(tool)s" == "ps":
    print("\n".join(rows))
    sys.exit(0)
pat = re.compile(sys.argv[-1])
hits = [r.split(None, 1)[0] for r in rows if pat.search(r.split(None, 6)[6])]
print("\n".join(hits))
sys.exit(0 if hits else 1)
'''

T1, T2 = "Mon Oct 26 09:00:00 2026", "Mon Oct 26 09:05:00 2026"
VENV_LAUNCH = "/Users/you/.hermes/mlx-vlm-venv/bin/python /Users/you/HermesAssistant/mlx-vlm-launch.py --model m --port 8080"
FW_LAUNCH = ("/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python "
             "/Users/you/HermesAssistant/mlx-vlm-launch.py --mlx-lm --model m --port 8081")
MLX_LM = "python3 -m mlx_lm server --model m --port 8080"
NOISE = ["/bin/zsh -c grep -rn \"mlx_lm server\" /Users/you/HermesAssistant",
         "rg mlx-vlm-launch.py /Users/you/HermesAssistant",
         "/bin/bash /Users/you/HermesAssistant/mlx-server.sh",
         "python3 /Users/you/HermesAssistant/mlx-vlm-launch.py --local-ready m",
         "python3 -c print('mlx-vlm-launch.py') mlx-vlm-launch.py",
         "vim /Users/you/HermesAssistant/mlx-vlm-launch.py"]


def row(pid, when, cmd):
    return "%5d %s     %s" % (pid, when, cmd)


def setup_runner():
    root = os.path.join(TMP, "runner")
    tests = os.path.join(root, "tools", "tests")
    os.makedirs(os.path.join(tests, "unit"))
    shutil.copy(os.path.join(REPO, "tools", "tests", "run.sh"), tests)
    shutil.copytree(os.path.join(REPO, "tools", "tests", "lib"), os.path.join(tests, "lib"))
    with open(os.path.join(tests, "unit", "t_ok.py"), "w") as f:
        f.write('print("  ok   trivial")\nprint("TESTS 1 passed 0 failed")\n')
    fake = os.path.join(TMP, "fakebin")
    os.makedirs(fake)
    for tool in ("ps", "pgrep"):
        p = os.path.join(fake, tool)
        with open(p, "w") as f:
            f.write(STUB % {"tool": tool})
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return os.path.join(tests, "run.sh"), fake


def guard_run(runsh, fake, before, after):
    d = tempfile.mkdtemp(dir=TMP, prefix="ps-")
    open(os.path.join(d, "before.txt"), "w").write("\n".join(before) + "\n")
    open(os.path.join(d, "after.txt"), "w").write("\n".join(after) + "\n")
    env = clean_env(PATH=fake + os.pathsep + os.environ.get("PATH", ""), FAKE_PS_DIR=d)
    r = subprocess.run(["bash", runsh, "unit"], capture_output=True, text=True, env=env, timeout=180)
    out = r.stdout + r.stderr
    return r.returncode, ("model-wake guard" in out), out


def t_guard():
    print("\nR2-61a. run.sh's model-wake guard: real launchers as pid@start pairs, either direction")
    runsh, fake = setup_runner()
    rc, tripped, out = guard_run(runsh, fake, [], [])
    check("premise: the nested run.sh runs its one suite green", rc == 0 and "all green" in out
          and "t_ok.py" in out, out[-600:])
    L1 = row(500, T1, VENV_LAUNCH)
    cases = [
        ("a real launcher that was there before the run and is gone after it", [L1], [], True),
        ("noise that was there before the run and is gone after it",
         [row(700 + i, T1, c) for i, c in enumerate(NOISE)], [], False),
        ("shells / grep / an editor / the --local-ready probe mentioning the launcher",
         [], [row(700 + i, T2, c) for i, c in enumerate(NOISE)], False),
        ("the same launcher before and after", [L1, row(9, T1, "/sbin/launchd")], [L1], False),
        ("a new mlx-vlm-launch.py under the venv python", [], [row(600, T2, VENV_LAUNCH)], True),
        ("a new launcher under the framework Python.app binary", [], [row(601, T2, FW_LAUNCH)], True),
        ("a new `python3 -m mlx_lm server`", [], [row(602, T2, MLX_LM)], True),
        ("a NEW launcher on a recycled PID (same pid, later start time)", [L1],
         [row(500, T2, VENV_LAUNCH)], True),
    ]
    for label, before, after, want in cases:
        rc, tripped, out = guard_run(runsh, fake, before, after)
        if want:
            check("%s -> the guard trips (exit 1)" % label, rc == 1 and tripped, (rc, out[-500:]))
        else:
            check("%s -> no trip (exit 0)" % label, rc == 0 and not tripped, (rc, out[-500:]))
    r = subprocess.run(["bash", "-n", os.path.join(REPO, "tools", "tests", "run.sh")],
                       capture_output=True, text=True)
    check("run.sh parses (bash -n)", r.returncode == 0, r.stderr)


# --------------------------------------------------------------------------
# b. t_apple_apps scans the tracked tree
# --------------------------------------------------------------------------
def t_apple_scan():
    print("\nR2-61b. t_apple_apps.py counts call sites in tracked files only")
    r = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--", "dashboard", "tools/tests"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("SKIP not a git checkout — the tracked-file scan cannot be exercised here")
        return
    copy = os.path.join(TMP, "repo")
    for rel in (p for p in r.stdout.split("\0") if p):
        src = os.path.join(REPO, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(copy, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    env = clean_env(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", copy], check=True, env=env)
    subprocess.run(["git", "-C", copy, "add", "-A"], check=True, env=env)
    suite = os.path.join(copy, "tools", "tests", "unit", "t_apple_apps.py")

    def run_suite():
        e = clean_env(HERMES_REPO=copy, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        p = subprocess.run([sys.executable, suite], capture_output=True, text=True, env=e, timeout=120)
        return p.returncode, p.stdout + p.stderr

    rc, out = run_suite()
    check("premise: t_apple_apps passes on the copy", rc == 0, out[-400:])
    mut = os.path.join(copy, "other-agent-scratch")
    os.makedirs(mut)
    shutil.copy(os.path.join(copy, "dashboard", "aux_needsyou.py"), os.path.join(mut, "aux_needsyou.py"))
    rc, out = run_suite()
    check("an UNTRACKED copy of a call site (another agent's scratch) does not fail it",
          rc == 0, out[-400:])
    subprocess.run(["git", "-C", copy, "add", "-A"], check=True, env=env)
    rc, out = run_suite()
    check("the same file, once TRACKED, is a new call site and still fails it",
          rc != 0 and "exactly the 4 known" in out, out[-400:])


# --------------------------------------------------------------------------
# c. t_c38 binds a port the kernel picks
# --------------------------------------------------------------------------
def t_c38_port():
    print("\nR2-61c. two t_c38 runs at once do not collide")
    suite = os.path.join(REPO, "tools", "tests", "unit", "t_c38_draft_session.py")
    src = open(suite, encoding="utf-8").read()
    check("t_c38 no longer names port 7803", "7803" not in src)
    env = dict(os.environ)          # keep the tier's offline guard for the server boot
    procs = [subprocess.Popen([sys.executable, suite], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=env) for _ in range(2)]
    outs = [p.communicate(timeout=240)[0] for p in procs]
    for i, (p, out) in enumerate(zip(procs, outs)):
        check("concurrent run %d passes" % (i + 1), p.returncode == 0
              and "0 failed" in out.strip().splitlines()[-1], out[-500:])


def main():
    t_guard()
    t_apple_scan()
    t_c38_port()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
