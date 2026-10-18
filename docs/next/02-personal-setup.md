# 2 · Personal setup — "make it yours in 60 seconds"

## Why this is the next big thing
Every schedule in the demo is hard-coded: wake 07:00, breakfast 07:45, lunch 12:30, dinner 19:00, bed 23:00. A
prospect who wakes at 06:00 or eats lunch at 14:00 immediately thinks "this isn't for me". The strongest demo move is
to hand over the keyboard: *"Tell me your day"* → the agents re-plan everything instantly, still deterministic and
still evidence-based. It also turns the ⚙︎ gear on each agent — currently "coming soon" — into a real feature, and
it is the foundation for real users in `/live`.

## What it is
1. **"Your day" setup** (opened from a new "Your day" button in the Agents tab, and on first visit to `/live`):
   five time pickers — wake-up, breakfast, lunch, dinner, lights out — plus "which agents do you want".
   Save → the whole plan, reminders, limits and tomorrow's wake-up recompute immediately.
2. **Agent setup (⚙︎)**: per agent, the weekdays and times of its goals where that makes sense
   (e.g. Fitness: which days are padel days; Business: development block length). First version: weekdays per
   goal for weekday-based goals, plus enable/disable per goal (already exists) — kept in one small panel.

## Guard rails (keep it deterministic and sane)
- Validation: times must be in order (wake < breakfast < lunch < dinner < lights out), at least 6 h between wake
  and lights out, meals ≥ 2 h apart. Invalid input is explained inline and never saved.
- Changing the profile never rewrites history; regularity is judged against the plan that was valid that day
  (first version: against the current plan, documented as a known limit).
- Evidence still applies: e.g. if lights out minus wake gives < 7 h, show the AASM warning instead of silently
  accepting it.

## Design
- Engine: `Profile` becomes replaceable on the `Assistant` (`Assistant.set_profile(...)` with validation);
  rule weekdays become overridable per owner (`Assistant.set_days(contract_id, rule_id, days)`), applied in
  `Assistant.active` like the on/off switches — the engine stays pure.
- Server: `profile` (get/set) and `days` ops; state exposes the current profile.
- UI: a modal/sheet with time inputs; the ⚙︎ opens the per-agent panel.

## Done when
- Setting wake 06:00 / lunch 13:30 moves every dependent goal, reminder and limit; tests cover validation and the
  recompute; ⚙︎ lets you change padel days and the timeline follows.
