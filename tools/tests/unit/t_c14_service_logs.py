#!/usr/bin/env python3
"""C14 — the model, serve and gateway launchd logs are rotated too, and doctor
reports their sizes.

1.3.1 taught server.py to copy-truncate dashboard.log at 8 MB, but launchd
opens mlx-server.log, mlx-bg.log, serve.log and gateway.error.log the same way
(StandardOutPath, O_APPEND, before exec) and nothing rotated those: on this Mac
gateway.error.log had reached 9 MB and mlx-bg.log 6.7 MB.  They now go through
the same `_log_rotate_once`, at startup and hourly.  gateway.log is left alone
on purpose — the agent's own RotatingFileHandler renames it at 5 MB, and a
copy-truncate under a renaming writer loses lines on both sides.

The rotation block is EXEC'd straight out of dashboard/server.py (as
t_131_logrotate.py does) and doctor.py is imported with HOME pointed at a
throwaway directory.  No model, no dashboard, no ~/.hermes write.
"""
import ast, importlib.util, io, os, shutil, sys, tempfile, time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")
DOCTOR = os.path.join(REPO, "dashboard", "doctor.py")
MB = 1024 * 1024
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def build(home):
    src = io.open(SERVER, encoding="utf-8").read()
    i = src.index("# dashboard.log rotation — launchd never rotates")
    j = src.index("def main():", i)
    g = {"__name__": "server_c14", "os": os, "sys": sys, "time": time, "shutil": shutil,
         "HOME": home, "DASH_HOST": "127.0.0.1", "DASH_PORT": 7788}
    exec(compile(src[i:j], SERVER, "exec"), g)
    return g


def fill(path, mb):
    line = "x" * 255 + "\n"
    with io.open(path, "w", encoding="utf-8") as f:
        while f.tell() < mb * MB:
            f.write(line)
    return os.path.getsize(path)


def calls_in(fn_name, callee):
    tree = ast.parse(io.open(SERVER, encoding="utf-8").read(), SERVER)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == callee
                       for n in ast.walk(node))
    return False


def t_rotation(tmp):
    home = os.path.join(tmp, "h")
    logs = os.path.join(home, ".hermes", "logs")
    os.makedirs(logs)
    g = build(home)
    svc = g.get("SVC_LOGS")
    rot = g.get("_svc_logs_rotate")
    print("\n1. which logs the dashboard rotates")
    check("server.py defines SVC_LOGS and _svc_logs_rotate", svc is not None and callable(rot), sorted(k for k in g if "log" in k.lower()))
    if svc is None or not callable(rot):
        return
    for name in ("mlx-server.log", "mlx-bg.log", "serve.log", "gateway.error.log"):
        check("%s is rotated" % name, name in svc, svc)
    for name in ("gateway.log", "errors.log", "agent.log", "dashboard.log"):
        check("%s is NOT in the service list" % name, name not in svc, svc)

    print("\n2. one pass: every big service log is copy-truncated, nothing else is touched")
    sizes = {name: fill(os.path.join(logs, name), 9) for name in svc}
    sizes["gateway.log"] = fill(os.path.join(logs, "gateway.log"), 9)
    sizes["errors.log"] = fill(os.path.join(logs, "errors.log"), 9)
    fds = {name: os.open(os.path.join(logs, name), os.O_WRONLY | os.O_APPEND) for name in svc}
    try:
        rot()
        for name in svc:
            p = os.path.join(logs, name)
            check("%s.1 holds the old bytes" % name,
                  os.path.exists(p + ".1") and os.path.getsize(p + ".1") == sizes[name])
            check("%s is truncated in place" % name, os.path.getsize(p) == 0, os.path.getsize(p))
            os.write(fds[name], b"next line\n")
            st = os.stat(p)
            check("%s: the launchd fd still writes, at offset 0" % name,
                  st.st_size == 10 and st.st_blocks * 512 < 64 * 1024, (st.st_size, st.st_blocks))
    finally:
        for fd in fds.values():
            os.close(fd)
    for name in ("gateway.log", "errors.log"):
        p = os.path.join(logs, name)
        check("%s is left to the agent" % name,
              os.path.getsize(p) == sizes[name] and not os.path.exists(p + ".1"))

    print("\n3. under the cap, and when absent, nothing happens")
    small = fill(os.path.join(logs, "serve.log"), 1)
    os.remove(os.path.join(logs, "mlx-bg.log"))
    before = sorted(os.listdir(logs))
    rot()
    check("a 1 MB serve.log is not rotated", os.path.getsize(os.path.join(logs, "serve.log")) == small)
    check("a missing log is no error and is not created", not os.path.exists(os.path.join(logs, "mlx-bg.log")))
    check("no new files", set(os.listdir(logs)) <= set(before), sorted(set(os.listdir(logs)) - set(before)))

    print("\n4. it runs where dashboard.log's rotation runs")
    check("main() rotates the service logs at startup", calls_in("main", "_svc_logs_rotate"))
    check("log_rotate_loop() rotates them hourly", calls_in("log_rotate_loop", "_svc_logs_rotate"))


def t_doctor(tmp):
    home = os.path.join(tmp, "dh")
    logs = os.path.join(home, ".hermes", "logs")
    os.makedirs(logs)
    os.environ["HOME"] = home
    spec = importlib.util.spec_from_file_location("doctor_c14", DOCTOR)
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    print("\n5. doctor reports every service log's size")
    with open(os.path.join(logs, "dashboard.log"), "w") as f:
        f.write("Hermes Assistant dashboard: http://127.0.0.1:7788\n")
    for name, mb in (("mlx-server.log", 3), ("serve.log", 1), ("gateway.error.log", 5), ("gateway.log", 2)):
        with open(os.path.join(logs, name), "w") as f:
            f.truncate(mb * MB)                       # sparse: the size is what doctor reads
    r = d._chk_logs()
    check("small logs -> PASS", r["status"] == "pass", r)
    for name, mb in (("mlx-server.log", 3), ("serve.log", 1), ("gateway.error.log", 5), ("gateway.log", 2)):
        check("the detail carries %s %.1f MB" % (name, mb), "%s %.1f MB" % (name, mb) in r["detail"], r["detail"])
    check("a log that does not exist is simply left out", "mlx-bg.log" not in r["detail"], r["detail"])

    print("\n6. a service log nothing is rotating -> WARN with the one to truncate")
    with open(os.path.join(logs, "serve.log"), "w") as f:
        f.truncate(300 * MB)
    r = d._chk_logs()
    check("a 300 MB serve.log -> WARN", r["status"] == "warn", r)
    check("it names serve.log", "serve.log" in r["detail"], r["detail"])
    check("the fix truncates serve.log in place",
          r["fix"].startswith(": > ") and r["fix"].split()[2].endswith("serve.log"), r["fix"])
    with open(os.path.join(logs, "serve.log"), "w") as f:
        f.truncate(1 * MB)
    with open(os.path.join(logs, "dashboard.log"), "a") as f:
        f.truncate(300 * MB)
    r = d._chk_logs()
    check("a 300 MB dashboard.log still warns, first in line", r["status"] == "warn"
          and r["fix"].split()[2].endswith("dashboard.log"), r)


def main():
    tmp = tempfile.mkdtemp(prefix="t-c14-")
    try:
        t_rotation(tmp)
        t_doctor(tmp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
