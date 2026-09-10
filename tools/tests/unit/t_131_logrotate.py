#!/usr/bin/env python3
"""1.3.1 item 4 — size-based rotation for ~/.hermes/logs/dashboard.log.

launchd opens StandardOutPath once, before exec, and never rotates it, so the
file grew for the life of the machine (doctor already had to warn about a 4 MB+
one).  server.py now rotates it at 8 MB, at startup and once an hour, by
COPY-TRUNCATE — a rename would leave the process writing to an inode with no
name.  The two things that must survive the change are the fd (new lines have to
land in the truncated file, at offset 0, not after a multi-megabyte hole) and
doctor's "error lines since the last start" count, which is scoped to the LAST
start banner in the tail — a marker the rotation carries away unless it is
re-emitted.

The rotation block is EXEC'd straight out of dashboard/server.py, so this tests
the shipped source rather than a copy — importing server.py would exec every aux
module, start its loops and touch the owner's ~/.hermes.
No model, no network, no dashboard, no ~/.hermes write.
"""
import io, os, re, sys, time, shutil, tempfile

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
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(name)


def extract(path, start, end):
    """The source between two anchors, so the test runs the real code."""
    src = io.open(path, encoding="utf-8").read()
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def build(home):
    g = {"__name__": "server_logrotate_t131", "os": os, "sys": sys,
         "time": time, "shutil": shutil,
         "HOME": home, "DASH_HOST": "127.0.0.1", "DASH_PORT": 7788}
    block = extract(SERVER,
                    "# dashboard.log rotation — launchd never rotates",
                    "def main():")
    exec(compile(block, SERVER, "exec"), g)
    return g


def doctor_res():
    """doctor.py's own two regexes, read out of the file, never re-typed."""
    src = io.open(DOCTOR, encoding="utf-8").read()
    boot = re.search(r'_DASH_BOOT_RE = re\.compile\(\s*(r?"[^"]*")\s*\)', src)
    err = re.search(r'_ERR_RE = re\.compile\(\s*\n?\s*(r"[^"]*")', src)
    return re.compile(eval(boot.group(1))), re.compile(eval(err.group(1)), re.I)


def fill(path, mb):
    line = "x" * 255 + "\n"
    with io.open(path, "w", encoding="utf-8") as f:
        while f.tell() < mb * MB:
            f.write(line)
    return os.path.getsize(path)


def run(tmp):
    home = os.path.join(tmp, "h")
    logs = os.path.join(home, ".hermes", "logs")
    os.makedirs(logs, exist_ok=True)
    g = build(home)
    log = os.path.join(logs, "dashboard.log")

    print("\n1. the shipped constants")
    check("cap is 8 MB", g["DASH_LOG_MAX"] == 8 * MB, g["DASH_LOG_MAX"])
    check("keeps 3 generations", g["DASH_LOG_KEEP"] == 3, g["DASH_LOG_KEEP"])
    check("hourly", g["DASH_LOG_EVERY_S"] == 3600, g["DASH_LOG_EVERY_S"])
    check("targets ~/.hermes/logs/dashboard.log",
          g["DASH_LOG"] == log, g["DASH_LOG"])

    print("\n2. under the cap nothing happens")
    small = fill(log, 1)
    check("no rotation", g["_log_rotate_once"](log) is None)
    check("no .1 created", not os.path.exists(log + ".1"))
    check("file untouched", os.path.getsize(log) == small, os.path.getsize(log))

    print("\n3. a 9 MB log rotates: .1 gets the bytes, the live path is TRUNCATED")
    size = fill(log, 9)
    head = io.open(log, encoding="utf-8").read(64)
    # an fd opened BEFORE the rotation, the way launchd holds one
    fd = os.open(log, os.O_WRONLY | os.O_APPEND)
    try:
        got = g["_log_rotate_once"](log)
        check("reports the rotated size", got == size, (got, size))
        check(".1 exists", os.path.exists(log + ".1"))
        check(".1 holds every byte", os.path.getsize(log + ".1") == size,
              os.path.getsize(log + ".1"))
        check(".1 is the same content", io.open(log + ".1", encoding="utf-8").read(64) == head)
        check("the live path is empty", os.path.getsize(log) == 0,
              os.path.getsize(log))
        check("no .2 yet", not os.path.exists(log + ".2"))

        print("\n4. the pre-existing fd still writes — at offset 0, no sparse hole")
        os.write(fd, b"after the rotation\n")
        st = os.stat(log)
        check("the new line is the whole file", st.st_size == 19, st.st_size)
        check("and it is really there",
              io.open(log, encoding="utf-8").read() == "after the rotation\n")
        check("the file is not sparse (no 9 MB of NULs)",
              st.st_blocks * 512 < 64 * 1024, st.st_blocks)
    finally:
        os.close(fd)

    print("\n5. generations shift .1 -> .2 -> .3 and stop there")
    for gen in range(2, 5):
        fill(log, 9)
        io.open(log, "a", encoding="utf-8").write("GEN%d\n" % gen)
        g["_log_rotate_once"](log)
    check(".1 .2 .3 all exist",
          all(os.path.exists("%s.%d" % (log, i)) for i in (1, 2, 3)))
    check("no .4 is ever written", not os.path.exists(log + ".4"))
    tails = [io.open("%s.%d" % (log, i), encoding="utf-8").read()[-6:].strip()
             for i in (1, 2, 3)]
    check(".1 is newest, .3 oldest", tails == ["GEN4", "GEN3", "GEN2"], tails)

    print("\n6. the marker keeps doctor's 'since the last start' count working")
    boot_re, err_re = doctor_res()
    marker = g["_dash_log_marker"](9 * MB)
    check("doctor reads it as a start marker", bool(boot_re.match(marker)), marker)
    check("and NOT as an error line", not err_re.search(marker), marker)
    check("it names the archive", "dashboard.log.1" in marker, marker)
    fill(log, 9)
    g["_log_rotate_once"](log, marker=g["_dash_log_marker"])
    lines = io.open(log, encoding="utf-8").read().splitlines()
    check("it is the FIRST line of the fresh log", bool(lines) and
          boot_re.match(lines[0]), lines[:1])
    # ...and doctor's scoping logic then finds it
    idx = [i for i, ln in enumerate(lines) if boot_re.match(ln)]
    check("_last_boot_index would return 0", idx and idx[-1] == 0, idx)
    check("a string marker works too (not only the callable)", True)
    fill(log, 9)
    g["_log_rotate_once"](log, marker="plain string marker")
    check("string marker written verbatim",
          io.open(log, encoding="utf-8").read() == "plain string marker\n",
          io.open(log, encoding="utf-8").read()[:60])

    print("\n7. it never raises, whatever the state of the filesystem")
    check("absent file -> None", g["_log_rotate_once"](os.path.join(logs, "nope.log")) is None)
    check("a directory -> None", g["_log_rotate_once"](logs) is None)
    fill(log, 9)
    check("max_bytes=0 disables it", g["_log_rotate_once"](log, max_bytes=0) is None)
    check("negative disables it", g["_log_rotate_once"](log, max_bytes=-1) is None)
    check("still 9 MB", os.path.getsize(log) > 8 * MB)
    ro = os.path.join(tmp, "ro")
    os.makedirs(ro, exist_ok=True)
    rolog = os.path.join(ro, "dashboard.log")
    fill(rolog, 9)
    os.chmod(ro, 0o500)                      # cannot create the .1 beside it
    try:
        check("an unwritable directory -> None, no exception",
              g["_log_rotate_once"](rolog) is None)
        check("and the log is left intact", os.path.getsize(rolog) > 8 * MB)
    finally:
        os.chmod(ro, 0o700)

    print("\n8. the loop is hourly and swallows its own failures")
    src = io.open(SERVER, encoding="utf-8").read()
    check("log_rotate_loop sleeps DASH_LOG_EVERY_S",
          "time.sleep(DASH_LOG_EVERY_S)" in src)
    # The startup rotation must write the marker ITSELF. Under launchd stdout
    # is a file, so main()'s banner is block-buffered and can sit unflushed for
    # minutes — relying on it would leave doctor with no marker in the fresh log.
    call = "_log_rotate_once(DASH_LOG, marker=_dash_log_marker)"
    check("main() rotates with the marker", call in src)
    check("and does it before the banner",
          src.index(call) < src.index('print(f"Hermes Assistant dashboard:'))
    check("the hourly loop passes the marker too",
          "_log_rotate_once(DASH_LOG, marker=_dash_log_marker)" in
          src[src.index("def log_rotate_loop"):src.index("def main():")])
    check("errors.log is deliberately NOT rotated here",
          "errors.log is NOT rotated here" in src)


def main():
    tmp = tempfile.mkdtemp(prefix="t131rot-")
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
