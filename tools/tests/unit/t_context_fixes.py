#!/usr/bin/env python3
"""aux_context.py review fixes: non-finite rejection, the locked atomic write,
and the block rewrite's indent anchor.  Exec's the aux module the way server.py
does, against a throwaway HOME.  No model, no network, no dashboard."""
import io, os, sys, glob, json, time, shutil, hashlib, tempfile, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_context.py")
FAILS = []
CHECKS = [0]

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (CHECKS[0] - len(FAILS), len(FAILS))))

def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " %s" % (detail,)))
    if not cond:
        FAILS.append(name)

class Ctx:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body if body is not None else {}
    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d

def build(home, cfg_text):
    os.makedirs(os.path.join(home, ".hermes", "logs"), exist_ok=True)
    io.open(os.path.join(home, ".hermes", "config.yaml"), "w",
            encoding="utf-8").write(cfg_text)
    g = {"__name__": "aux_context_test", "HOME": home,
         "DATA": os.path.join(home, ".hermes", "dashboard"),
         "_state_lock": threading.Lock(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g

def sha(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest()

CFG = """model:
  default: mlx-community/Qwen3.8-27B
  context_length: 65536
compression:
  enabled: true
  threshold: 0.5   # when to compact
  target_ratio: 0.2
  protect_last_n: 20
"""

def main():
    tmp = tempfile.mkdtemp(prefix="cxfix-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        return 1
    print("ALL PASS")
    return 0

def run(tmp):
    print("\n1. non-finite values are refused (they used to pass the range check)")
    home = os.path.join(tmp, "a")
    g = build(home, CFG)
    cfgp = os.path.join(home, ".hermes", "config.yaml")
    before = sha(cfgp)
    for label, body in (("nan", {"threshold": float("nan")}),
                        ('"nan"', {"threshold": "nan"}),
                        ("inf", {"threshold": float("inf")}),
                        ("-inf", {"threshold": float("-inf")}),
                        ('"NaN" ratio', {"target_ratio": "NaN"})):
        r = g["_cx_compression_post"](Ctx(body=body))
        check("%s -> 400" % label, isinstance(r, tuple) and r[1] == 400, r)
    check("config untouched by every refusal", sha(cfgp) == before)
    check("and no nan reached the file",
          "nan" not in io.open(cfgp, encoding="utf-8").read().lower())
    r = g["_cx_compression_post"](Ctx(body={"protect_last_n": float("nan")}))
    check("an int key refuses nan too", isinstance(r, tuple) and r[1] == 400, r)
    r = g["_cx_compression_post"](Ctx(body={"threshold": 0.72}))
    check("a real value still writes", r.get("ok") and r["changed"], r)
    check("and lands in the file",
          "threshold: 0.72" in io.open(cfgp, encoding="utf-8").read())
    check("the trailing comment survived",
          "# when to compact" in io.open(cfgp, encoding="utf-8").read())

    print("\n2. a no-op POST leaves the file byte-identical")
    now = sha(cfgp)
    r = g["_cx_compression_post"](Ctx(body={"threshold": 0.72}))
    check("changed is False", r.get("changed") is False, r)
    check("sha unchanged", sha(cfgp) == now)
    check("no backup was written",
          not glob.glob(cfgp + ".bak-context-*") or r.get("backup") is None, r)
    check("no temp file left behind", not glob.glob(cfgp + ".tmp-*"))

    print("\n3. the temp file is unpredictable, exclusive and 0600")
    seen = set()
    real_open = os.open
    def spy(path, flags, mode=0o777, **kw):
        if isinstance(path, str) and ".tmp-" in path:
            seen.add((os.path.basename(path), flags & os.O_EXCL, mode))
        return real_open(path, flags, mode, **kw)
    g["_cx_os"].open = spy
    try:
        for v in (0.61, 0.62, 0.63):
            g["_cx_compression_post"](Ctx(body={"threshold": v}))
    finally:
        g["_cx_os"].open = real_open
    check("three distinct temp names", len(seen) == 3, seen)
    check("every one O_EXCL + 0600",
          all(x[1] and x[2] == 0o600 for x in seen), seen)
    check("none of them is the old fixed name",
          all(not n.endswith(".context.tmp") for n, _, _ in seen), seen)
    check("nothing left behind", not glob.glob(cfgp + ".tmp-*"))

    print("\n4. concurrent writers do not lose each other's keys")
    home = os.path.join(tmp, "b")
    g2 = build(home, CFG)
    cfg2 = os.path.join(home, ".hermes", "config.yaml")
    errs = []
    def w(key, val):
        try:
            for _ in range(12):
                g2["_cx_write"]({key: val})
                g2["_cx_write"]({key: val + (0.01 if isinstance(val, float) else 1)})
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=w, args=a) for a in
          (("threshold", 0.55), ("target_ratio", 0.25))]
    for t in ts: t.start()
    for t in ts: t.join()
    txt = io.open(cfg2, encoding="utf-8").read()
    check("no writer raised", not errs, errs)
    check("both keys survived", "threshold: 0.5" in txt and
          "target_ratio: 0.2" in txt, txt)
    check("the file is still one compression block",
          txt.count("compression:") == 1, txt)
    check("no temp files left", not glob.glob(cfg2 + ".tmp-*"))
    class CountingLock(object):
        """Stands in for server.py's _state_lock and records that the whole
        read-modify-write really runs inside it."""
        def __init__(self):
            self.inner = threading.Lock()
            self.held = False
            self.n = 0
            self.reentered = False
        def __enter__(self):
            self.inner.acquire()
            if self.held:
                self.reentered = True
            self.held = True
            self.n += 1
            return self
        def __exit__(self, *a):
            self.held = False
            self.inner.release()
            return False
    cl = CountingLock()
    g2["_state_lock"] = cl
    g2["_cx_write"]({"threshold": 0.66})
    g2["_cx_write"]({"threshold": 0.66})          # a no-op still takes it
    check("_state_lock wraps every write", cl.n == 2, cl.n)
    check("and is never re-entered (it is not an RLock)", not cl.reentered)

    print("\n4b. the cross-process flock: taken inside the state lock, and real")
    import contextlib, subprocess, textwrap
    order = []
    class OrderLock(object):
        def __enter__(self): order.append("state-in"); return self
        def __exit__(self, *a): order.append("state-out"); return False
    real_flock = g2["_cx_config_lock"]
    @contextlib.contextmanager
    def spy_flock(timeout=None):
        order.append("flock-in")
        with real_flock():
            try:
                yield
            finally:
                order.append("flock-out")
    g2["_state_lock"] = OrderLock()
    g2["_cx_config_lock"] = spy_flock
    g2["_cx_write"]({"threshold": 0.44})
    check("both locks, state outside flock",
          order == ["state-in", "flock-in", "flock-out", "state-out"], order)
    g2["_cx_config_lock"] = real_flock
    g2["_state_lock"] = threading.Lock()

    lockf = os.path.join(home, ".hermes", "config.yaml.lock")
    check("the lock is a separate file next to the config",
          os.path.exists(lockf) and lockf != cfg2, lockf)
    check("and it is 0600", oct(os.stat(lockf).st_mode & 0o777) == "0o600",
          oct(os.stat(lockf).st_mode & 0o777))

    # a REAL second process, holding the same flock the other two writers take
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import fcntl, os, sys, time
            fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            sys.stdout.write("held\\n"); sys.stdout.flush()
            time.sleep(1.2)
        """), lockf], stdout=subprocess.PIPE, text=True)
    check("the other process took the lock",
          holder.stdout.readline().strip() == "held")
    try:
        g2["_cx_config_lock"](timeout=0.2).__enter__()
        check("a held lock times out rather than proceeding", False, "acquired!")
    except TimeoutError as e:
        check("a held lock times out rather than proceeding", True)
        check("and the timeout is an OSError (the route answers 500, not 502)",
              isinstance(e, OSError))
    t0 = time.time()
    changed, _bak = g2["_cx_write"]({"target_ratio": 0.33})
    waited = time.time() - t0
    holder.wait()
    check("the write WAITED for the other process, then went through",
          changed and waited > 0.4, round(waited, 2))
    check("and the value landed", "target_ratio: 0.33" in
          io.open(cfg2, encoding="utf-8").read())

    print("\n5. a nested key of the same name is not rewritten")
    nested = """compression:
  enabled: true
  threshold: 0.5
  summariser:
    threshold: 0.99
    protect_last_n: 99
other:
  threshold: 0.11
"""
    out = g["_cx_apply_text"](nested, {"threshold": 0.8,
                                       "protect_last_n": 30})
    check("the block's own threshold moved", "\n  threshold: 0.8\n" in out, out)
    check("the nested one did not",
          "    threshold: 0.99" in out, out)
    check("nor the nested protect_last_n",
          "    protect_last_n: 99" in out, out)
    check("a key the block lacks is appended at the block's indent",
          "\n  protect_last_n: 30\n" in out, out)
    check("the sibling top-level block is untouched",
          "\nother:\n  threshold: 0.11\n" in out, out)
    check("nothing else moved",
          out.replace("threshold: 0.8", "threshold: 0.5")
             .replace("  protect_last_n: 30\n", "") == nested, out)

    print("\n6. a missing block is still created, and a no-op is still no-op")
    out = g["_cx_apply_text"]("model:\n  default: x\n", {"threshold": 0.4})
    check("block appended with every default",
          "compression:\n  threshold: 0.4\n  target_ratio: 0.2\n"
          "  protect_last_n: 20\n  protect_first_n: 3\n" in out, out)
    check("identical values return the source unchanged",
          g["_cx_apply_text"](CFG, {"threshold": 0.5}) == CFG)

if __name__ == "__main__":
    sys.exit(main())
