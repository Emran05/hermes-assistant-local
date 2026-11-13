#!/usr/bin/env python3
"""S2 seam (E03): the main window's approval card says why it is asking.

hermes_rpc._Approvals.add() stores the Trust policy's verdict on the payload
as `_policy` (permissions.decide(): {tier, class, reason, ...}) and /api/chat/
poll hands the whole `approval` dict to the page.  The card showed only the
command, so "more than one command — asking" (E03's scope clamp: a Trust
"Auto" class that the command could not be judged against) never reached the
owner.  Now the card carries ONE quiet line with that reason, as text (never
markup), with the same hidden-character / blank-run / combining-mark marking
the card applies to the command, flattened to one line and capped.

Drives the REAL streamJob (index.html) in node on the shared fake DOM, the
way t_e15_approval_multiline.py does.  Offline; no dashboard.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402
import t_e15_approval_multiline as e15  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


GRAB = r"""
function grab(){ const b=$('msgs').querySelectorAll('.approval')[0]; if(!b)return null;
  const p=b.querySelector('.pol');
  return {code:b.querySelector('code').textContent, pol:p?p.textContent:null,
          polHtml:p?p.innerHTML:null, html:b.innerHTML,
          order:Array.from(b.children).map(c=>c.className||c.tagName)}; }
"""

E03 = ("terminal:rm → filesystem is set to Auto, but more than one command — asking")
EVIL = ('<img src=x onerror="globalThis.PWNED=1">‮danger​' + "⠀" * 60 + "end\nsecond line")


def main():
    fn = ui.extract("streamJob")
    check("index.html has a top-level streamJob", bool(fn))
    if not fn:
        return 1
    ap = lambda aid, **kw: {"state": "approval", "done": False, "text": "", "status": "",
                            "approval": dict({"command": "ls; rm -rf build", "approval_id": aid}, **kw)}
    steps = [
        ap("A", _policy={"tier": "ask", "class": "filesystem", "reason": E03}),
        ap("B"),                                                     # no verdict at all
        ap("C", _policy={"tier": "ask", "reason": EVIL}),
        ap("D", _policy={"tier": "ask", "reason": "x" * 900}),
        ap("E", _policy="not a dict"),
        ap("F", _policy={"tier": "ask", "reason": "   "}),
        ap("G", command="echo " + "y\n" * 40, _policy={"tier": "ask", "reason": "terminal:echo → shell is set to Ask"}),
        {"state": "done", "done": True, "reply": "fin", "text": "fin"},
    ]
    script = e15.SCRIPT.replace(
        e15.SCRIPT[e15.SCRIPT.index("function grab()"):e15.SCRIPT.index("globalThis.fetch=")], GRAB)
    rc, data, out = ui.run_node(script % {"dom": ui.FAKE_DOM, "fn": fn, "steps": json.dumps(steps)})
    check("streamJob ran in node", rc == 0 and data and "error" not in data, out[-1500:])
    if not data or "error" in data:
        return 1
    seen = data["seen"]
    a = seen[1] or {}
    check("the E03 reason is on the card", a.get("pol") == E03, a)
    check("...as one line under the description, above the command",
          a.get("order", [])[:3] == ["hd", "pol", "CODE"] or
          a.get("order", [])[:3] == ["hd", "pol", "code"], a.get("order"))
    b = seen[2] or {}
    check("no verdict -> no reason line (and the card is otherwise unchanged)",
          b.get("pol") is None and "pol" not in (b.get("order") or []), b)
    c = seen[3] or {}
    pol = c.get("pol") or ""
    check("a reason is text, never markup", "<img" in pol and "<img" not in (c.get("polHtml") or "").replace("&lt;img", ""),
          c.get("polHtml"))
    check("...a right-to-left override and a zero-width space are marked",
          "⟦U+202E⟧" in pol and "⟦U+200B⟧" in pol and "‮" not in pol and "​" not in pol, pol)
    check("...a 60-blank run is one marker", "⟦60 other blank characters⟧" in pol, pol)
    check("...and it is ONE line", "\n" not in pol and "end second line" in pol, repr(pol))
    d = seen[4] or {}
    check("a long reason is capped (≤ 300 characters, ends with …)",
          0 < len(d.get("pol") or "") <= 300 and (d.get("pol") or "").endswith("…"), len(d.get("pol") or ""))
    check("a non-dict _policy shows nothing and breaks nothing", (seen[5] or {}).get("pol") is None, seen[5])
    check("a blank reason shows nothing", (seen[6] or {}).get("pol") is None, seen[6])
    g = seen[7] or {}
    order = g.get("order") or []
    check("the long-command line still sits right above the buttons",
          "long" in order and order.index("long") == order.index("row") - 1, order)

    # the CSS for the line: quiet and wrapping, never clipped
    idx = ui.index_src()
    r = e15.rule(idx, ".approval .pol") or ""
    check("`.approval .pol` is styled quiet (muted) and wraps",
          "var(--muted)" in r and "overflow-wrap:anywhere" in r.replace(" ", ""), r)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
