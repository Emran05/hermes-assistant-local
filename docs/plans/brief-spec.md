# World Brief — content spec (user-defined 2026-07-05)
Delivery: 8:00am daily → Telegram (@emran_hermes_bot DM) + dashboard Briefing widget.
NOT a personal-agenda ping. Composition (in order):
1. Your day: calendar events, open tasks, anything the agent flagged overnight.
2. World & tech front page: top 2-3 per News Desk section (Tech/World/Business/Science).
3. Markets: notable overnight/premarket movers from watchlist + indices, with % and one-line why if known.
4. Underground signal: GitHub trending picks, HN risers, niche items from research feeds — "things most people haven't seen yet."
5. Look-ahead: what to watch today + further out (earnings, releases, weather inflection, deadlines).
Constraints: compose from CACHED widget data (hub pre-warmer) + one local-model synthesis pass;
no extra network fetches at 8am; 12-hour times; scannable in <60s; zero emoji, bespoke tone.
Implementation lands in P2 (Watchtower) but the briefing generator gets refactored P1-compatible.
