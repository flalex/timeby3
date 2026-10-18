# 3 · Calendar connector v1 — recommendations land in your real calendar

## Why this is the next big thing
The product promise is "an assistant that can replace a human assistant". A human assistant doesn't just remind
you — they put things in your calendar. Right now every connector says "coming soon". Making **one** connector
genuinely work turns the demo from "nice simulator" into "this touches my real life": the prospect subscribes on
their own phone and sees *Development 09:00–11:00*, *Lunch 12:30*, *Caffeine cut-off 17:00* appear in Google /
Apple / Outlook Calendar — while we still need zero OAuth, zero accounts and zero third-party SDKs.

## What it is
1. **Calendar feed (write path)**: the server publishes the owner's plan as a standard iCalendar feed
   (`/calendar.ics`, RFC 5545): one event per goal that takes time (duration badge goals), all-day-free "limit"
   events for cut-offs (e.g. *☕ Caffeine cut-off until 07:00*), and tomorrow's recommended wake-up. Each event
   description carries the advice and the evidence source, so the "why" travels with it.
   Calendars that subscribe to the URL refresh it automatically; a "Download .ics" button covers one-off import.
2. **Busy times (read path, v1)**: paste an .ics URL or upload an .ics file with meetings; the agent then
   (a) shows meetings as grey blocks in the timeline and (b) never sends a reminder in the middle of a meeting —
   it waits until the meeting ends (if the goal's window is still open) — deterministic rule, unit-tested.
3. **Connectors tab**: Calendar shows "connected" once a feed is opened or busy times are loaded, with the
   subscribe URL and copy button.

## Design
- `timeby/calendar_ics.py`: stdlib-only iCalendar writer and a minimal parser (VEVENT DTSTART/DTEND/SUMMARY,
  UTC and floating local times; no recurrence in v1 — documented).
- Engine: busy intervals are passed to the assistant (`Assistant.set_busy([...])`); `tick` holds reminders while
  busy and releases them after, within the reminder's validity — same "hold, don't drop" principle as sleep.
- Server: `GET /calendar.ics` (and `/calendar/live.ics`), op `busy` to load meetings; state exposes busy blocks.

## Done when
- The feed validates (opens in Apple Calendar / Google import) and contains today's goals with times and advice.
- A loaded meeting 12:00–13:00 delays the 12:15 lunch reminder to 13:00 and shows in the timeline; tests cover
  writer, parser and reminder holding.
