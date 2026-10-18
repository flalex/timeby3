# 1 · Story mode — a presenter-proof 3-minute demo

## Why this is the next big thing
Today the demo is powerful but fragile: the presenter must know which scenario to pick, when to jump the clock,
what to click and what to say. In a sales call that is where demos die — dead air, a wrong click, a screen full of
red. Prospects buy the *moments* (the agent reaching out first, recovering a bad morning, explaining itself with
science), so the demo must reliably hit those moments in order, in three minutes, every time.

## What it is
A **"▶ Story"** button in the demo bar runs a scripted walkthrough of one day on top of the real engine — no fake
screens. Each chapter moves the clock, triggers real owner/sensor events and shows a caption card that says what
just happened and why it matters. The presenter can pause, go back a chapter, or exit at any time.

| # | Chapter | What the viewer sees | The point to land |
|---|---------|----------------------|-------------------|
| 1 | 06:30 · Meet your agents | Agents panel highlighted, contracts with sources | Every recommendation comes from a science-based contract |
| 2 | 08:25 · Overslept | Alarm at 07:00, silence while asleep, one recovery plan | Proactive, and calm: one plan instead of a wall of red |
| 3 | 08:30 · Do both | Now card suggests daylight + breakfast together, "Done both" | Productivity: compress, don't drop |
| 4 | 17:45 · Caught by data | Café card payment → caffeine flagged with the reason | Data sources act for the owner, nothing to type |
| 5 | 18:00 · Fix the record | Owner edits a missed item → score updates | The owner stays in control; scores stay honest |
| 6 | 23:10 · Day closed | Recap, tomorrow's wake-up plan with the reason | Closes the loop and plans tomorrow |

## Design
- Pure client-side: `timeby/web/story.js` drives the existing API (`reset`, `jump`, `log`, `reply`, `edit`) — the
  engine stays the single source of truth, so the story can never show something the product can't do.
- Captions are a floating card with chapter title, one-sentence explanation, progress dots and ← / ⏸ / → controls;
  the element being discussed gets a soft highlight ring.
- Deterministic: a dedicated `story` scenario (owner script + sensor events) so every run is identical.

## Done when
- One click plays all chapters end to end with no presenter input; ← / → jumps between chapters; Esc exits.
- Works on /v3 at desktop width; no console errors; no impact on /live.
