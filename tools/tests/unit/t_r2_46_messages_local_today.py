#!/usr/bin/env python3
"""R2-46 (docs/audits/2026-10-08-codebase-audit.md, P3 Swift shell / messages)
— the Message Center fallback counts "today" from LOCAL midnight.

BEFORE: expand_messages() in expanders_extra.py (the pop-out used when
aux_messages fails to load) computed today's start as `now - now % 86400`,
i.e. UTC midnight. Anywhere off UTC that is wrong for hours every day: in
Honolulu (UTC-10) it counts yesterday evening's messages as today after 2 PM,
and misses this morning's before it. The Swift MessagesSync path has always
used Calendar.current.startOfDay — local midnight.

AFTER: today starts at mktime of the local date at 00:00:00.

The REAL expand_messages runs over a throwaway chat.db (the five tables it
reads) with TZ pinned to Pacific/Honolulu, which has no DST, so UTC midnight
and local midnight are always 10 hours apart and the window between them is
never empty. One message sits inside that window; the old code always gets
its count wrong, one way or the other.
"""
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _extra_harness as H  # noqa: E402

check = H.check
APPLE = 978307200


def apple_ns(ts):
    return int((ts - APPLE) * 1e9)


def make_db(home, stamps):
    d = os.path.join(home, "Library", "Messages")
    os.makedirs(d)
    con = sqlite3.connect(os.path.join(d, "chat.db"))
    con.executescript("""
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
                           display_name TEXT, style INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT,
                              attributedBody BLOB, is_from_me INTEGER,
                              date INTEGER, cache_has_attachments INTEGER,
                              associated_message_type INTEGER,
                              handle_id INTEGER, is_read INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        INSERT INTO chat VALUES (1, '+15550100', '', 45);
        INSERT INTO handle VALUES (1, '+15550100');
        INSERT INTO chat_handle_join VALUES (1, 1);
    """)
    for i, ts in enumerate(stamps, 1):
        con.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?)",
                    (i, "msg %d" % i, None, 0, apple_ns(ts), 0, 0, 1, 1))
        con.execute("INSERT INTO chat_message_join VALUES (1, ?)", (i,))
    con.commit()
    con.close()


def main():
    print("R2-46: Message Center fallback counts today from local midnight")
    os.environ["TZ"] = "Pacific/Honolulu"
    time.tzset()
    now = time.time()
    lt = time.localtime(now)
    local_mid = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    utc_mid = now - now % 86400
    check("the zone is really off UTC here (10 h between the two midnights)",
          abs(local_mid - utc_mid) in (10 * 3600, 14 * 3600), (local_mid, utc_mid))

    lo, hi = sorted((local_mid, utc_mid))
    between = (lo + hi) / 2.0           # inside the disputed window
    stamps = [now - 1,                  # clearly today, both ways
              between,
              lo - 3 * 3600]            # clearly before both midnights
    home = tempfile.mkdtemp(prefix="hermes-r246-")
    make_db(home, stamps)
    g = H.load(home=home)
    out = g["expand_messages"]()
    check("the fallback reads the throwaway chat.db", out.get("available") is True, out)

    want = sum(1 for ts in stamps if ts >= local_mid)
    utc_count = sum(1 for ts in stamps if ts >= utc_mid)
    check("the old (UTC) rule would give a different answer here", want != utc_count,
          (want, utc_count))
    check("today_count counts from local midnight (%d)" % want,
          out.get("today_count") == want, (out.get("today_count"), want, utc_count))
    check("the conversation itself is still listed",
          len(out.get("conversations") or []) == 1, out.get("conversations"))
    H.finish()


if __name__ == "__main__":
    main()
