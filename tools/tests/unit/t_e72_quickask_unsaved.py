#!/usr/bin/env python3
"""E72: Quick Ask says when a reply was not saved, and keeps a refused message.

2026-11-09 audit (chat-core-6):
  (a) a turn that ends with `persisted: false` (the reply could not be written
      to the conversation — "Clear session" mid-turn, a write error) was shown
      in Quick Ask as a normal reply; it was gone on the next open. The main
      window has always said so.
  (b) the composer was cleared before POST /api/chat and the text handed back
      only for a 409 busy; a send refused for any other reason (agent paused,
      memory ceiling, dashboard unreachable) lost what was typed and left an
      unsent message in the thread.
The main window's own refused send dropped the text and the attachments the
same way; it is checked statically here (sendMsg's refused branch).

Runs the REAL send / poll cut out of aux_quickask.js in node, on the harness
t_b11_quickask_busy uses. No dashboard, no model.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402
import t_b11_quickask_busy as qb  # noqa: E402  (main() is guarded)

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


SEND = qb.HARNESS + r"""
const BODIES=%(bodies)s;
const OUT=[];
(async()=>{
  for(const body of BODIES){
    THREAD.children=[]; LINES.length=0; POLLED.length=0; POP.busy=false; POP.job=null;
    globalThis.j=(url,b)=>{
      if(url==='/api/chat'){ POSTS.push(b); return Promise.resolve(body); }
      POLLED.push(url); return Promise.resolve({state:'done',done:true,reply:'r',text:'r'});
    };
    POP.input.value='book the dentist';
    send();
    for(let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
    OUT.push({input:POP.input.value, busy:POP.busy, polled:POLLED.slice(),
      userTurns:THREAD.querySelectorAll('.qa-turn').length, lines:LINES.slice()});
  }
  console.log(JSON.stringify(OUT));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""

POLL = qb.HARNESS + r"""
const ENDS=%(ends)s;
const OUT=[];
(async()=>{
  for(const end of ENDS){
    THREAD.children=[]; LINES.length=0; POP.busy=true;
    globalThis.j=(url,b)=>Promise.resolve(end);
    poll('J1', busyLine(''));
    for(let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
    OUT.push({lines:LINES.slice(), busy:POP.busy, shown:THREAD._all().map(e=>e._html).join('')});
  }
  console.log(JSON.stringify(OUT));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def main():
    fns = [qb.qa_fn(n) for n in ("send", "poll", "qaMarkHidden", "qaShape", "approvalBox")]
    check("aux_quickask.js has send / poll", all(fns))
    if not all(fns):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    src = "\n".join(fns)

    paused = "The agent is paused to save memory. Resume it from the model menu."
    bodies = [{"ok": False, "reply": paused}, None]
    rc, data, out = ui.run_node(SEND % {"dom": ui.FAKE_DOM, "fns": src,
                                        "bodies": json.dumps(bodies)})
    check("send ran in node", rc == 0 and isinstance(data, list) and len(data) == 2, out[-1500:])
    for label, d in zip(("refused (agent paused)", "dashboard unreachable"),
                        data if isinstance(data, list) else []):
        check("%s: the typed text is handed back" % label, d["input"] == "book the dentist", d)
        check("%s: no unsent message is left in the thread" % label, d["userTurns"] == 0, d)
        check("%s: nothing is polled, the popover is usable" % label,
              d["polled"] == [] and d["busy"] is False, d)
    if isinstance(data, list) and len(data) == 2:
        check("refused: the server's reason is shown",
              any("paused" in t and k == "err" for t, k in data[0]["lines"]), data[0]["lines"])
        check("unreachable: says the dashboard could not be reached",
              any("Could not reach" in t for t, _ in data[1]["lines"]), data[1]["lines"])

    ends = [{"state": "done", "done": True, "reply": "Booked for 3 PM.", "text": "Booked for 3 PM.",
             "err": False, "persisted": False},
            {"state": "done", "done": True, "reply": "Booked for 3 PM.", "text": "Booked for 3 PM.",
             "err": False, "persisted": True}]
    rc, data, out = ui.run_node(POLL % {"dom": ui.FAKE_DOM, "fns": src, "ends": json.dumps(ends)})
    check("poll ran in node", rc == 0 and isinstance(data, list) and len(data) == 2, out[-1500:])
    if isinstance(data, list) and len(data) == 2:
        check("persisted:false — the reply is still shown",
              "Booked for 3 PM." in data[0]["shown"], data[0])
        check("persisted:false — and Quick Ask says it was not saved",
              any("could not be saved" in t and k == "err" for t, k in data[0]["lines"]),
              data[0]["lines"])
        check("persisted:true — no such line",
              not any("could not be saved" in t for t, _ in data[1]["lines"]), data[1]["lines"])

    # the main window: a refused send (not busy, no job) hands back the text
    # and the attachments too, as the busy branch always did
    fn = ui.extract("sendMsg") or ""
    i = fn.find("res=d.ok?{sent:true,ok:true}")
    blk = fn[max(0, i - 400):i + 200] if i >= 0 else ""
    check("main window: a refused send restores the typed text",
          "$('input').value=text" in blk, blk)
    check("main window: ...and the attachments",
          "attachments=atts.concat(attachments)" in blk, blk)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
