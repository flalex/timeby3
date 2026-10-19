# timeby — contract-based personal assistant

## Priorities
Lightning speed and accuracy. Be ruthless on side things. Get a short, working demo to clients ASAP and iterate fast.

## Product
A contract-based agentic personal assistant. Critical behavior (proactive interaction, recommendations,
scoring of situation/state) must be deterministic and grounded — never random, inconsistent or hallucinated.

- Layer 1 contracts = scientific knowledge. v1 has exactly two:
  - `cortisol_rhythm` (hormones): high cortisol after waking, low before sleep.
  - `nutrition`: topics Meal regularity, Hydration, Plants & fibre, Protein. Also `fitness`, `business` agents.
- Architecture must allow unlimited contracts (dopamine, etc. come later).
- This repo is the core module only. Later it connects to: a client (chat/app), data sources (auto events),
  and services (calendar, email) to apply recommendations.

## Architecture (stdlib-only Python 3.11)
- `timeby/contracts/*.json` — a contract = phases + rules. Adding a contract = adding a JSON file, no code.
- `timeby/actions.json` — shared action vocabulary; contracts may only reference these.
- `timeby/loader.py` — parses + validates (every rule must cite a source; unknown actions/phases fail fast).
- `timeby/engine.py` — pure, deterministic: scoring, recommendations, nudges, day plan.
- `timeby/assistant.py` — agent facade (`log`, `tick`, `status`, `recommend`, `plan`) + ports
  (`Notifier`, `EventSource`) for clients/data sources.
- Rule kinds: `do` (happen in window), `avoid` (not in window), `regularity` (same planned time daily).
- Windows are relative to profile anchors (wake, breakfast, lunch, dinner, sleep). Anchors with
  `observed_by` follow the real event (e.g. actual wake time); regularity is always judged vs the plan.
- `timeby/narration.py` — all user-facing text, templated from contract data. `timeby/intents.py` — keyword
  free-text parser. An LLM may later rephrase/classify, never decide.
- Proactive messages (`Assistant.tick`): brief (at observed wake), recap (at observed bedtime, else planned +1 h), and
  exactly ONE reminder per rule per day (`engine.reminder`): its contract nudge, else a last_call 15 min before the
  on-time deadline. A reminder is dropped if the agent mentioned that rule in the last 30 min (e.g. brief's focus).
  Snoozable. The day plan and live reminders share `engine.reminder` — keep it that way.
- v3 extras: rule `duration` (planned minutes) and `reopens` (avoid limit lifts at that anchor next day, e.g. caffeine
  until wake). With `wait_for_observed`, bedtime-bound limits stay on until sleep is actually observed and the recap
  waits for real lights-out. `Assistant.correct()` rewrites today's events for a goal (owner edits past items);
  `Assistant.annotate()` stores duration/comment. `sleep_plan.plan_wake()` = tomorrow's wake-up (usual time unless
  sleep < 7 h, then shift ≤ 60 min). `scenarios.py` = user scenarios: what the owner does/skips/adds today (wearable reports wake/sleep), same typical history.
- Events the owner ties to a goal (`Event.goal` = rule id, set by `correct()`) count for that goal on that day even
  outside its window (late breakfast stays breakfast, shown "Late") and never for another task; limits still see them.
  Rule ids are unique across contracts. Rules may have `days` (mon..sun) — applies only on those weekdays.
- Late start (Option D, `recovery.py`): when wake is observed > wake-goal tolerance late, the brief is replaced by ONE
  recovery plan (now / moved with wake-up / late but worth it / skipped / unchanged + combine tips). While the owner
  is still asleep only the wake-up call is sent. Late-but-done earns `late_credit` (default 0.5, status LATE).
  Rules can declare `pairs` (goals to do together), `topic` (grouping), `remind: false` (companion goals).
- `connectors.py`: declared connectors (calendar, fitness tracker, location) — UI only, not implemented.
- Demo readiness (see `docs/next/`): `web/story.js` = Story mode (6 chapters on the real engine; uses the default
  day, restores the owner's setup on exit); `personal.py` + `web/setup.js` = the agent ⚙︎ dialog: its day times, goals
  on/off + weekdays, and (live) context for Claude
  (`Assistant.set_profile/set_days`, survive resets); the Agents tab shows each agent's insights (score, goals, why); `calendar_ics.py` + `calendar_feed.py` + `web/calendar.js` =
  `/calendar.ics` plan feed and meetings that hold reminders until they end (`Assistant.set_busy`).
  Feature scripts are served from `/static/<name>.js` and rely on v3.html globals.
- Assistant tone (`tone.py`, ⚙︎ on the Assistant panel, `web/assistant.js`): balanced (default), buddy, scientific,
  mentor, caring. Fixed templates applied in `Assistant._send`; facts never change; Claude gets the same voice.
- Messenger: `telegram.py` (stdlib Bot API) — connect bot token, find chat, delivery important/all/off; forwards
  only in /live (demo sends test messages only); token stays server-side in `data/live/settings.json`.
- Weekly view (`week.py`, `web/week.js`, Day | Week switch): Mon–Sun per goal from the engine's day results;
  progress = on-time / judged; rule `weekly_target` (default 5/7 of planned days) → met / on track / at risk /
  critical / out of reach with the reason; "Needs you today" = critical or at-risk goals still doable today.
  The v3 demo simulates Wednesday of the current week so the week has a past.
- Agents = contracts, goals = rules. `Assistant.set_enabled(contract_id, on, rule_id=None)` switches either off;
  `Assistant.active` (enabled agents with enabled goals only) is what status/tick/plan see, so off = fully silent.
- `timeby/server.py` + `timeby/web/index.html` — interactive simulator: simulated clock, simulated sensors
  (wearable wake/sleep, phone screen-time), chat with quick replies, live dashboard.

## Commands
- Interactive simulator: `python3 -m timeby.server` → http://localhost:8765 (v1), /v2, /v3 (current direction).
  Each version runs an independent simulation. v3 = desktop web app: Today/Insights + Assistant panel, demo bar + agent-activity feed;
  view model in `timeby/today.py` (earlier / now / later), `wait_for_observed=True`, `compact_routines=True`.
- Live (non-demo) mode: http://localhost:8765/live — real clock, nothing scripted, no fake records: only the owner's
  own logs and the agent's messages. Saved to `data/live/` (or `TIMEBY_DATA_DIR`): one JSON file per logical day
  (+ `tracking_from`) and `settings.json` (`storage.py`); restored on server start. Goals that ended before tracking
  started that day are UNTRACKED ("No record"): never judged missed/kept, excluded from scores and weekly targets.
  The day's first record means the owner is awake (releases reminders held "while asleep").
- AI recommendations (live only, `ai.py`): one function per agent (`recommend_<agent>`), shared prompt/call, grounded
  on the engine's facts + the owner's per-agent context (⚙︎). Needs `pip install -r requirements-ai.txt` and
  `ANTHROPIC_API_KEY`; model `claude-opus-5-5` with server-side refusal fallback. Claude advises, never decides.
  Demo controls on /v3 include "Jump to" (any time today; jumping back replays the day).
- Console demo: `python3 -m timeby`
- Tests: `python3 -m pytest -q tests`
