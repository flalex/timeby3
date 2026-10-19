"""The owner's own day: validated anchor times. Pure functions — the Assistant applies the result."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time, timedelta

from .model import Profile
from .sleep_plan import MIN_SLEEP

DAY_ORDER = ("wake", "breakfast", "lunch", "dinner", "sleep")
WORK = ("work_start", "work_end")  # the working day of a full-time job
ANCHORS = DAY_ORDER + WORK
LABELS = {"wake": "Wake-up", "breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "sleep": "Lights out",
          "work_start": "Work start", "work_end": "Work end"}
WORK_SPAN = (timedelta(hours=4), timedelta(hours=12))
MEALS = ("breakfast", "lunch", "dinner")
MIN_AWAKE = timedelta(hours=6)
MIN_MEAL_GAP = timedelta(hours=2)


@dataclass(frozen=True)
class DayCheck:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def parse_times(raw: dict[str, str]) -> dict[str, time]:
    """HH:MM strings → times, for every anchor of the day."""
    missing = [LABELS[name] for name in ANCHORS if not raw.get(name)]
    if missing:
        raise ValueError(f"Missing time: {', '.join(missing)}.")
    try:
        return {name: time.fromisoformat(raw[name]) for name in ANCHORS}
    except ValueError:
        raise ValueError("Times must be HH:MM, e.g. 07:00.") from None


def check_day(times: dict[str, time], day_start: time) -> DayCheck:
    """Order, awake span and meal spacing are hard rules; a short night only warns (AASM 7 h minimum)."""
    offset = {name: _since(times[name], day_start) for name in ANCHORS}
    errors = [*_order_errors(offset), *_span_errors(offset), *_work_errors(offset)]
    return DayCheck(tuple(errors), () if errors else _sleep_warnings(offset))


def with_times(profile: Profile, times: dict[str, time]) -> Profile:
    """New anchor times; everything else (observed_by, day_start, other anchors) stays."""
    anchors = {name: replace(a, time=times[name]) if name in times else a for name, a in profile.anchors.items()}
    return replace(profile, anchors=anchors)


def _since(t: time, day_start: time) -> timedelta:
    """Position within the logical day, so lights out at 00:30 comes after dinner."""
    minutes = (t.hour * 60 + t.minute) - (day_start.hour * 60 + day_start.minute)
    return timedelta(minutes=minutes % (24 * 60))


def _order_errors(offset: dict[str, timedelta]) -> list[str]:
    return [f"{LABELS[later]} must come after {LABELS[earlier].lower()}."
            for earlier, later in zip(DAY_ORDER, DAY_ORDER[1:]) if offset[later] <= offset[earlier]]


def _span_errors(offset: dict[str, timedelta]) -> list[str]:
    errors = []
    if offset["sleep"] - offset["wake"] < MIN_AWAKE:
        errors.append("Keep at least 6 h between wake-up and lights out.")
    errors += [f"{LABELS[later]} must be at least 2 h after {LABELS[earlier].lower()}."
               for earlier, later in zip(MEALS, MEALS[1:])
               if timedelta(0) < offset[later] - offset[earlier] < MIN_MEAL_GAP]
    return errors


def _work_errors(offset: dict[str, timedelta]) -> list[str]:
    span = offset["work_end"] - offset["work_start"]
    if not WORK_SPAN[0] <= span <= WORK_SPAN[1]:
        return ["Work end must be 4–12 h after work start."]
    if offset["work_start"] < offset["wake"] or offset["work_end"] > offset["sleep"]:
        return ["The working day must fit between wake-up and lights out."]
    return []


def _sleep_warnings(offset: dict[str, timedelta]) -> tuple[str, ...]:
    night = timedelta(days=1) - (offset["sleep"] - offset["wake"])
    if night >= MIN_SLEEP:
        return ()
    hours = night.total_seconds() / 3600
    return (f"Lights out to wake-up leaves {hours:g} h of sleep. The AASM recommends at least 7 h for adults.",)
