# Next: making the demo customer-ready

The engine already does the hard part (deterministic contracts, proactive messages, late-start recovery). What still
stands between this and a confident customer demo:

| # | Doc | Problem it removes | Why now |
|---|-----|--------------------|---------|
| 1 | [Story mode](01-story-mode.md) | Fragile, presenter-dependent demos | Every sales call needs the same 6 moments, reliably, in 3 minutes |
| 2 | [Personal setup](02-personal-setup.md) | "That's not my schedule" | Handing over the keyboard is the strongest demo move; also the base for real users |
| 3 | [Calendar connector v1](03-calendar-connector.md) | "It's only a simulator" | One real integration (no OAuth needed) proves the assistant acts in the owner's life |

Considered and deferred:
- **LLM conversation layer** — high wow, but needs API keys/costs and careful guard rails; the deterministic core must
  be demo-stable first. Next after these three (LLM classifies and phrases, never decides).
- **Weekly trends & streaks** — valuable for retention, less decisive in a first meeting.
- **Persistence** — needed for real users, invisible in a demo; comes with personal setup's follow-up.
