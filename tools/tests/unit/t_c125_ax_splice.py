#!/usr/bin/env python3
"""t_c125_ax_splice.py — the Accessibility splice works in UTF-16 units.

2026-10-21 audit C125: when a field refuses kAXSelectedTextAttribute, the
helper splices the dictated text into kAXValueAttribute at the
kAXSelectedTextRangeAttribute range.  That range counts UTF-16 code units;
the old code indexed `Array(value)` (Characters) with it, so with two emoji
before the caret the text landed two characters late, and a selection over
the emoji also ate the user's own " t".

The REAL `enum AXSplice` is lifted out of app/dictation/main.swift and run
by a compiled harness (see _dictation_swift.py).  The helper app is never
built.  Against the old source the enum does not exist, so the suite fails.

    python3 t_c125_ax_splice.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _dictation_swift as SW  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


if not SW.available():
    print("SKIP swiftc is not installed (the helper is macOS-only)")
    sys.exit(0)

src = SW.read_source()
piece = SW.extract(src, "enum AXSplice {")
check("main.swift declares the UTF-16 splice (enum AXSplice)", piece is not None)
ax = SW.extract(src, "    private static func axInsert(") or ""
check("axInsert splices through it, not Array(value)",
      "AXSplice.apply(value, range: selection, insert: text)" in ax
      and "Array(value)" not in ax, ax[:80])

if piece is not None:
    E2 = "\\u{1F389}\\u{1F38A}"     # two emoji: 2 Characters, 4 UTF-16 units
    main = r'''
func cf(_ l: Int, _ n: Int) -> CFRange { CFRange(location: l, length: n) }
let E = "%(E2)s"
let field = "party " + E + " tonight"
let caretAfterEmoji = ("party " + E as NSString).length   // 10

// 1. caret between the emoji and " tonight"
var (m, caret) = AXSplice.apply(field, range: cf(caretAfterEmoji, 0), insert: " DICTATED")
check("caret after emoji: text lands at the caret",
      m == "party " + E + " DICTATED tonight", m)
check("caret after emoji: new caret is just after the insertion",
      caret == caretAfterEmoji + 9, "\(caret)")

// 2. the two emoji selected: replace exactly them
(m, caret) = AXSplice.apply(field, range: cf(6, (E as NSString).length), insert: "[X]")
check("selection over emoji replaces exactly the emoji",
      m == "party [X] tonight", m)
check("caret after replacing the selection", caret == 9, "\(caret)")

// 3. decomposed accent (e + U+0301) before the caret
let dec = "cafe\u{301} ok"                       // 7 Characters, 8 units
let afterCafe = ("cafe\u{301}" as NSString).length  // 5
(m, caret) = AXSplice.apply(dec, range: cf(afterCafe, 0), insert: "!")
check("combining mark before the caret", m == "cafe\u{301}! ok", m)

// 4. no selection readable: append at the end (the old behaviour, kept)
(m, caret) = AXSplice.apply(field, range: nil, insert: "!")
check("no range appends", m == field + "!", m)
check("no range: caret at the new end", caret == (field as NSString).length + 1, "\(caret)")

// 5. out-of-range values from a confused field are clamped, never a crash
(m, caret) = AXSplice.apply("abc", range: cf(99, 5), insert: "d")
check("location past the end clamps to the end", m == "abcd" && caret == 4, m)
(m, caret) = AXSplice.apply("abc", range: cf(-3, 2), insert: "z")
check("a range wholly before the start replaces nothing", m == "zabc" && caret == 1, m)
(m, caret) = AXSplice.apply("abc", range: cf(-1, 2), insert: "z")
check("a range straddling the start is cut to the inside", m == "zbc" && caret == 1, m)
(m, caret) = AXSplice.apply("abc", range: cf(Int.max - 1, Int.max), insert: "d")
check("huge numbers neither trap nor misplace", m == "abcd" && caret == 4, m)
(m, caret) = AXSplice.apply("abc", range: cf(1, Int.max), insert: "Z")
check("a huge length from a valid start", m == "aZ" && caret == 2, m)
(m, caret) = AXSplice.apply("abc", range: cf(1, 99), insert: "Z")
check("length past the end clamps", m == "aZ" && caret == 2, m)
(m, caret) = AXSplice.apply("abc", range: cf(Int.min, -1), insert: "z")
check("a range that overflows BELOW Int.min replaces nothing (not the whole field)",
      m == "zabc" && caret == 1, m)
(m, caret) = AXSplice.apply("abc", range: cf(-5, Int.min), insert: "z")
check("a huge negative length replaces nothing", m == "zabc" && caret == 1, m)

// 6. an emoji in the INSERTED text moves the caret by its UTF-16 length
(m, caret) = AXSplice.apply("ab", range: cf(1, 0), insert: E)
check("caret counts the inserted text in UTF-16", caret == 1 + 4, "\(caret)")
''' % {"E2": E2}
    p, f, out = SW.run([piece], main)
    print("\n".join("  " + ln for ln in out.splitlines() if ln and not ln.startswith("SWIFT")))
    PASS += p
    FAIL += f

print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
