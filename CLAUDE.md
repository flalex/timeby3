# timeby — contract-based personal assistant

## Priorities
Lightning speed and accuracy. Be ruthless on side things. Get a short, working demo to clients ASAP and iterate fast.

## Product
A contract-based agentic personal assistant. Critical behavior (proactive interaction, recommendations,
scoring of situation/state) must be deterministic and grounded — never random, inconsistent or hallucinated.

- Layer 1 contracts = scientific knowledge. v1 has exactly two:
  - `cortisol_rhythm` (hormones): high cortisol after waking, low before sleep.
  - `meal_regularity` (nutrition): eat at consistent times.
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
- Agents = contracts, goals = rules. `Assistant.set_enabled(contract_id, on, rule_id=None)` switches either off;
  `Assistant.active` (enabled agents with enabled goals only) is what status/tick/plan see, so off = fully silent.
- `timeby/server.py` + `timeby/web/index.html` — interactive simulator: simulated clock, simulated sensors
  (wearable wake/sleep, phone screen-time), chat with quick replies, live dashboard.

## Commands
- Interactive simulator: `python3 -m timeby.server` → http://localhost:8765 (v1), /v2, /v3 (current direction).
  Each version runs an independent simulation. v3 = desktop web app: Today/Insights + Assistant panel, demo bar + agent-activity feed;
  view model in `timeby/today.py` (earlier / now / later), `wait_for_observed=True`, `compact_routines=True`.
- Live (non-demo) mode: http://localhost:8765/live — real clock, no history, nothing scripted; state is in memory.
  Demo controls on /v3 include "Jump to" (any time today; jumping back replays the day).
- Console demo: `python3 -m timeby`
- Tests: `python3 -m pytest -q tests`
