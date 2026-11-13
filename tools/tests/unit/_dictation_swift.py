"""_dictation_swift.py — compile pieces of the REAL app/dictation/main.swift.

Not a suite (the runner skips files that start with "_"); imported by the
t_c122_/t_c123_/t_c125_ suites.  The helper app itself is never built, signed or launched
here: a rebuild resets its Microphone/Speech/Accessibility grants, which is
the owner's call.  Instead each suite lifts the AppKit-free declarations it
tests (an `enum`, a `struct`) out of main.swift by brace matching, appends a
tiny test `main`, compiles that with swiftc into a temp dir and runs it.

When swiftc is not available (a Linux runner), `available()` is False and the
suite prints a SKIP line.
"""
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(HERE)))
SOURCE = os.path.join(REPO, "app", "dictation", "main.swift")

# The test side of every harness: one line per check, read back by run().
PRELUDE = r'''
import Foundation

var __pass = 0
var __fail = 0
func check(_ name: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
    if ok { __pass += 1; print("ok   " + name) }
    else { __fail += 1; print("FAIL " + name + "  " + detail()) }
}
'''


def read_source(path=None):
    with open(path or SOURCE, encoding="utf-8") as f:
        return f.read()


def extract(src, header):
    """The declaration that starts with `header` (e.g. "enum AXSplice {"),
    through its matching closing brace.  None when it is not in the source —
    which is itself the finding when a suite runs against the old file."""
    i = src.find("\n" + header)
    if i < 0:
        return None
    i += 1
    depth, j, n = 0, src.index("{", i), len(src)
    while j < n:
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        elif c == '"':                       # skip a one-line string literal
            k = j + 1
            while k < n and src[k] != '"':
                k += 2 if src[k] == "\\" else 1
            j = k
        elif src.startswith("//", j):        # skip a line comment
            j = src.find("\n", j)
            if j < 0:
                break
        j += 1
    return None


def swiftc():
    if shutil.which("xcrun"):
        try:
            p = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True,
                               text=True, timeout=60)
            if p.returncode == 0 and p.stdout.strip():
                # Through xcrun, not the bare path it printed: xcrun is what
                # supplies the SDK, and a bare swiftc cannot find its stdlib.
                return ["xcrun", "swiftc"]
        except (OSError, subprocess.SubprocessError):
            pass
    found = shutil.which("swiftc")
    return [found] if found else None


def available():
    return swiftc() is not None


def run(pieces, main_code, frameworks=()):
    """Compile PRELUDE + pieces + main_code; returns (passed, failed, output).
    A compile failure counts as one failure, with the compiler's message."""
    cc = swiftc()
    tmp = tempfile.mkdtemp(prefix="hermes-dictation-swift-")
    try:
        src = os.path.join(tmp, "main.swift")
        with open(src, "w", encoding="utf-8") as f:
            f.write(PRELUDE + "\n" + "\n\n".join(pieces) + "\n\n" + main_code +
                    '\nprint("SWIFT \\(__pass) passed \\(__fail) failed")\n'
                    "exit(__fail == 0 ? 0 : 1)\n")
        out = os.path.join(tmp, "t")
        cmd = cc + ["-o", out, src]
        for fw in frameworks:
            cmd += ["-framework", fw]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            return 0, 1, "compile failed:\n" + p.stderr[-4000:]
        r = subprocess.run([out], capture_output=True, text=True, timeout=120)
        m = re.search(r"^SWIFT (\d+) passed (\d+) failed$", r.stdout, re.M)
        if not m:
            return 0, 1, "no summary line:\n" + r.stdout + r.stderr
        return int(m.group(1)), int(m.group(2)), r.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
