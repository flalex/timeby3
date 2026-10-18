"""The owner's plan as calendar events (write path) and demo meetings (read path). Pure, no I/O."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .calendar_ics import CalEvent, write
from .model import ContractReport, RuleKind, RuleResult
from .sleep_plan import SOURCES as WAKE_SOURCES, WakePlan

ICONS = {"caffeine": "☕", "meal": "🍽️", "exercise_intense": "🏋️", "bright_light": "💡", "dev_work": "💻",
         "marketing_work": "📣", "sunlight": "☀️", "exercise_light": "🚶", "wind_down": "🧘", "surf": "🏄",
         "padel": "🎾", "water": "💧"}
WAKE_EVENT = timedelta(minutes=15)
SAMPLE_MEETINGS = (("12:00", "13:00", "Client call"), ("15:00", "15:30", "Design review"))


def feed(reports: list[ContractReport], wake: WakePlan, stamp: datetime) -> str:
    return write(plan_events(reports, wake), stamp, "timeby plan")


def plan_events(reports: list[ContractReport], wake: WakePlan) -> list[CalEvent]:
    """Goals that take time, limits as blocks until they lift, and tomorrow's wake-up — sorted by start."""
    events = [_goal_event(rep, r) for rep in reports for r in rep.results if _takes_time(r)]
    events += [_limit_event(rep, r) for rep in reports for r in rep.results if r.rule.kind is RuleKind.AVOID]
    events.append(_wake_event(wake))
    return sorted(events, key=lambda e: e.start)


def _takes_time(r: RuleResult) -> bool:
    return r.rule.kind is not RuleKind.AVOID and r.rule.duration is not None


def _goal_event(rep: ContractReport, r: RuleResult) -> CalEvent:
    start = r.anchor_at if r.rule.kind is RuleKind.REGULARITY else r.start  # same anchors the engine judges by
    return CalEvent(start, start + timedelta(minutes=r.rule.duration), _titled(r, r.rule.label),
                    _description(rep, r), _uid(start.date(), rep, r))


def _limit_event(rep: ContractReport, r: RuleResult) -> CalEvent:
    until = r.until or r.end
    return CalEvent(r.start, until, _titled(r, f"{r.rule.label} until {until:%H:%M}"), _description(rep, r),
                    _uid(r.start.date(), rep, r))


def _wake_event(wake: WakePlan) -> CalEvent:
    reason = f"{wake.reason}\n\nEvidence:\n" + "\n".join(f"• {s}" for s in WAKE_SOURCES)
    return CalEvent(wake.at, wake.at + WAKE_EVENT, "⏰ Wake up", reason, f"{wake.at:%Y%m%d}-wake@timeby")


def _titled(r: RuleResult, text: str) -> str:
    icon = ICONS.get(r.rule.actions[0])
    return f"{icon} {text}" if icon else text


def _description(rep: ContractReport, r: RuleResult) -> str:
    evidence = "\n".join(f"• {rep.contract.sources[s]}" for s in r.rule.sources)
    return f"{r.rule.advice}\n\nWhy: {r.rule.why}\n\nEvidence ({r.rule.strength}):\n{evidence}"


def _uid(day: date, rep: ContractReport, r: RuleResult) -> str:
    """Stable per goal and day, so a refreshed feed updates events instead of duplicating them."""
    return f"{day:%Y%m%d}-{rep.contract.id}-{r.rule.id}@timeby"


def sample_meetings(day: date, stamp: datetime) -> str:
    """A demo calendar with meetings on `day`, as .ics text — loaded through the same parser as uploads."""
    events = [CalEvent(_on(day, start), _on(day, end), summary, uid=f"{day:%Y%m%d}-sample-{i}@timeby")
              for i, (start, end, summary) in enumerate(SAMPLE_MEETINGS)]
    return write(events, stamp, "Sample meetings")


def _on(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))
