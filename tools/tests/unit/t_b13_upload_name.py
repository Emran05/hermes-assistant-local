#!/usr/bin/env python3
"""B13 (2026-10-08 audit): an upload whose filename has a character above
U+00FF silently did nothing.

uploadFile() put file.name straight into the X-Filename header. A header
value must be a ByteString (every code point <= U+00FF); WebKit's fetch()
throws before sending anything otherwise, nothing caught it, and every macOS
screenshot name carries U+202F (narrow no-break space) before "AM"/"PM".

Runs the REAL uploadHeaderName/uploadFile from index.html in node with a
fetch() that enforces the ByteString rule the way the browser does.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


SCREENSHOT = "Screenshot 2026-10-21 at 9.41.07 AM.png"
NAMES = [SCREENSHOT, "Résumé.pdf", "日本語.txt",
         "naıve—draft.md", "\U0001F600 party.png", "a\u0000b\tc.txt", "",
         "Å ngström.csv", "plain-name_1.txt"]

SCRIPT = r"""
%(dom)s
let attachments=[]; function renderAttach(){}
globalThis.alert=(m)=>{ALERTS.push(String(m));};
const ALERTS=[]; const SENT=[];
// the browser's rule: a header value with a code point > U+00FF is a TypeError
globalThis.fetch=async(url,opts)=>{
  const v=(opts.headers||{})['X-Filename'];
  for(const ch of String(v)){ if(ch.codePointAt(0)>0xFF) throw new TypeError("String contains non ISO-8859-1 code point."); }
  SENT.push(v);
  return {json:async()=>({ok:true,path:'/inbox/'+v})};
};
%(helper)s
%(fn)s
const NAMES=%(names)s;
(async()=>{
  const out={mapped:{}, unhandled:0};
  process.on('unhandledRejection',()=>{out.unhandled++;});
  if(typeof uploadHeaderName==='function') for(const n of NAMES) out.mapped[n]=uploadHeaderName(n);
  for(const n of NAMES){ try{ await uploadFile({name:n}); }catch(e){ out.unhandled++; } }
  out.sent=SENT; out.attachments=attachments; out.toasts=TOASTS; out.alerts=ALERTS;
  // a dashboard that is not answering: say so instead of throwing
  globalThis.fetch=async()=>{ throw new TypeError('Load failed'); };
  try{ await uploadFile({name:'x.txt'}); out.downToast=TOASTS.slice(-1)[0]||''; }
  catch(e){ out.downThrew=true; }
  console.log(JSON.stringify(out));
})();
"""


def main():
    fn = ui.extract("uploadFile")
    helper = ui.extract("uploadHeaderName") or ""
    check("index.html has uploadFile", bool(fn))
    if not fn:
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    rc, d, out = ui.run_node(SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn, "helper": helper,
                                       "names": json.dumps(NAMES)})
    check("ran in node", rc == 0 and isinstance(d, dict), out[-1500:])
    d = d or {}
    sent = d.get("sent") or []
    check("THE FIX: every file was actually sent (no silent no-op)",
          len(sent) == len(NAMES), sent)
    check("THE FIX: the macOS screenshot name goes through, with a plain space",
          "Screenshot 2026-10-21 at 9.41.07 AM.png" in sent, sent)
    check("every header value is a ByteString",
          all(all(ord(c) <= 0xFF for c in v) for v in sent), sent)
    m = d.get("mapped") or {}
    check("Latin-1 accents are kept as they are",
          m.get("Résumé.pdf") == "Résumé.pdf", m)
    check("an accent above U+00FF folds to its base letter (Å -> A)",
          m.get("Å ngström.csv") == "A ngström.csv", m)
    check("characters with no Latin-1 form become '_'",
          m.get("日本語.txt") == "___.txt"
          and m.get("\U0001F600 party.png") == "_ party.png", m)
    check("control characters become '_' / spaces",
          m.get("a\u0000b\tc.txt") == "a_b c.txt", m)
    check("an empty name falls back to upload.bin", m.get("") == "upload.bin", m)
    check("an ordinary name is untouched", m.get("plain-name_1.txt") == "plain-name_1.txt", m)
    check("all uploads were attached", len(d.get("attachments") or []) == len(NAMES), d)
    check("nothing escaped as an unhandled rejection", d.get("unhandled") == 0, d)
    check("a dashboard that does not answer is reported, not thrown",
          not d.get("downThrew") and "Could not upload" in (d.get("downToast") or ""), d)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
