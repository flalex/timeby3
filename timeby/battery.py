"""Energy battery: an estimate of how much energy the owner has right now, and how much they can have today.

Not a contract with goals of its own — it reads what the other agents already know (sleep and wake from the
records, today's goal outcomes from the engine) and does one transparent calculation:

  capacity (today's max)  = 100 − sleep debt − last evening's sleep-hurting habits − an off-schedule wake − how you feel
  level now               = capacity at wake-up − the waking day's natural drain + charges − spends

Every number carries its reason, so the owner can see why. Deterministic; an estimate from records, not a measurement
(a fitness tracker's HRV can replace it later).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .context import Situation
from .model import ContractReport, Status

SLEEP_NEED_H = 8.0
SLEEP_DEBT_PER_H = 10       # capacity lost per hour slept below the need
SLEEP_DEBT_MAX = 40
EVENING_HIT = 4             # each sleep-hurting habit last evening (late caffeine, late hard workout, bright screens)
OFF_SCHEDULE_WAKE = 5
END_OF_DAY_SHARE = 0.2      # with nothing else happening, the day drains to 20 % of capacity by bedtime
CONDITIONS = {"well": (0, "Feeling normal"), "tired": (10, "Feeling tired"), "unwell": (30, "Feeling unwell")}
EVENING_RULES = {"caffeine_cutoff": "Coffee too close to bed", "no_late_intense_exercise": "Hard workout close to bed",
                 "dim_evening_light": "Bright light and screens before bed"}

# today's goals that charge the battery: (charge when done on time, drain when missed, why)
CHARGES = {
    "morning_light": (6, 0, "Daylight after waking strengthens the morning cortisol peak — the body's 'on' switch."),
    "morning_movement": (4, 0, "Morning movement raises alertness for hours."),
    "water_on_waking": (2, 0, "Rehydrates after the night."),
    "regular_breakfast": (4, 4, "Fuel after the overnight fast."),
    "regular_lunch": (4, 4, "Keeps energy steady through the afternoon."),
    "regular_dinner": (2, 3, "Refuels for the evening."),
}
# activities that spend energy, per hour (or per session when no duration is known)
SPENDS = {"exercise_intense": (8, "Hard training"), "running": (8, "Run"), "padel": (10, "Padel"), "surf": (10, "Surfing"),
          "dev_work": (4, "Focused work"), "marketing_work": (2, "Marketing work")}
STATES = ((80, "charged", "Charged"), (50, "steady", "Steady"), (25, "low", "Low"), (0, "drained", "Drained"))
ADVICE = {
    "charged": "Good moment for the hardest thing on your list.",
    "steady": "Pace yourself — a meal or daylight on time keeps it steady.",
    "low": "Protect what's left: easy tasks, no hard workout tonight, early night.",
    "drained": "Running on empty — rest, eat, and get to bed on time; tomorrow starts from tonight's sleep.",
}


@dataclass(frozen=True)
class Factor:
    label: str
    delta: int
    why: str = ""
    at: str = ""


def view(a, reports: list[ContractReport], now: datetime, condition: Situation | None) -> dict:
    """The battery for this moment, with every factor that made it."""
    ctx, day = a.ctx, a.ctx.logical_day(now)
    wake = _wake(a, day)
    sleep_h, sleep_known = _last_night(a, day, wake)
    by_rule = {r.rule.id: r for rep in reports for r in rep.results}
    capacity_factors = _capacity_factors(a, day, sleep_h, sleep_known, by_rule, condition, now)
    capacity = max(10, 100 + sum(f.delta for f in capacity_factors))
    bedtime = ctx.planned_anchor(a.sleep_anchor, day)
    base = {"capacity": capacity, "capacity_factors": [vars(f) for f in capacity_factors], "sleep_h": round(sleep_h, 1),
            "condition": _condition(condition, day), "bedtime": f"{bedtime:%H:%M}"}
    if now < wake:  # not up yet: the day hasn't started draining
        return base | {"level": None, "asleep": True, "events": [], "state": "", "label": "",
                       "advice": f"Up to {capacity}% today when you wake."}
    events = _charges(by_rule) + _spends(a, day, now)
    drain = _drain(capacity, wake, bedtime, now)
    level = max(0, min(capacity, capacity - drain + sum(f.delta for f in events)))
    at_bed = max(0, level - (_drain(capacity, wake, bedtime, bedtime) - drain)) if now < bedtime else level
    state, label = next((s, l) for floor, s, l in STATES if level >= floor)
    return base | {"level": level, "asleep": False, "state": state, "label": label, "advice": ADVICE[state],
                   "events": [vars(f) for f in sorted(events, key=lambda f: f.at)], "drain": -drain,
                   "at_bedtime": at_bed, "wake": f"{wake:%H:%M}"}


# --- capacity -----------------------------------------------------------------------------

def _wake(a, day: date) -> datetime:
    return a.ctx.observed(a.wake_anchor, day) or a.ctx.planned_anchor(a.wake_anchor, day)


def _last_night(a, day: date, wake: datetime) -> tuple[float, bool]:
    """Hours between the last lights-out before today's wake-up and the wake-up; the plan when there's no record."""
    sleeps = [e.at for e in a.events if e.action == "sleep" and wake - timedelta(hours=16) <= e.at < wake]
    if sleeps:
        return (wake - max(sleeps)).total_seconds() / 3600, True
    planned_bed = a.ctx.planned_anchor(a.sleep_anchor, day - timedelta(days=1))
    return (a.ctx.planned_anchor(a.wake_anchor, day) - planned_bed).total_seconds() / 3600, False


def _capacity_factors(a, day, sleep_h, sleep_known, by_rule, condition, now) -> list[Factor]:
    factors = []
    debt = min(SLEEP_DEBT_MAX, round(SLEEP_DEBT_PER_H * max(0.0, SLEEP_NEED_H - sleep_h)))
    slept = f"Slept {int(sleep_h)} h {round(sleep_h % 1 * 60):02d}" if sleep_known else f"No sleep record — assuming your planned {sleep_h:.0f} h"
    factors.append(Factor(slept, -debt, "Adults need 7–9 h; every hour short lowers today's ceiling." if debt else "Enough sleep — full ceiling."))
    factors += _evening_hits(a, day)
    wake_goal = by_rule.get("consistent_wake")
    if wake_goal and wake_goal.status in (Status.LATE, Status.MISSED):
        factors.append(Factor("Woke off schedule", -OFF_SCHEDULE_WAKE, "A shifted wake-up puts the body clock out of sync for the day."))
    state = condition.text if condition and condition.active(day) else "well"
    if CONDITIONS[state][0]:
        factors.append(Factor(CONDITIONS[state][1], -CONDITIONS[state][0], "Your own read of how you feel."))
    return factors


def _evening_hits(a, day: date) -> list[Factor]:
    """Last evening's habits that hurt sleep quality — judged at the end of yesterday, like the week view does."""
    yesterday_end = datetime.combine(day, a.profile.day_start) - timedelta(minutes=1)
    if not any(e.at <= yesterday_end and a.ctx.logical_day(e.at) == day - timedelta(days=1) for e in a.events):
        return []  # no records yesterday: nothing to judge
    broken = [r.rule.id for rep in a.status(yesterday_end) for r in rep.results
              if r.rule.id in EVENING_RULES and r.status is Status.VIOLATED]
    return [Factor(EVENING_RULES[rid], -EVENING_HIT, "Hurts sleep quality, so less recharge overnight.") for rid in broken]


def _condition(condition: Situation | None, day: date) -> dict:
    state = condition.text if condition and condition.active(day) else "well"
    return {"state": state, "scope": condition.scope if condition and state != "well" else ""}


# --- today --------------------------------------------------------------------------------

def _drain(capacity: int, wake: datetime, bedtime: datetime, at: datetime) -> int:
    """The waking day's natural drain: linear from wake-up to 20 % of capacity at bedtime (and on, if up late)."""
    day_len = max(1.0, (bedtime - wake).total_seconds())
    return round(capacity * (1 - END_OF_DAY_SHARE) * max(0.0, (at - wake).total_seconds()) / day_len)


def _charges(by_rule) -> list[Factor]:
    out = []
    for rid, (charge, miss, why) in CHARGES.items():
        r = by_rule.get(rid)
        if not r:
            continue
        when = f"{r.hit_at:%H:%M}" if r.hit_at else f"{r.deadline:%H:%M}"
        if r.status is Status.DONE:
            out.append(Factor(r.rule.label, charge, why, when))
        elif r.status is Status.LATE:
            out.append(Factor(f"{r.rule.label} (late)", charge // 2, why, when))
        elif r.status is Status.MISSED and miss:
            out.append(Factor(f"{r.rule.label} skipped", -miss, "Running on empty.", when))
    return out


def _spends(a, day: date, now: datetime) -> list[Factor]:
    out = []
    for e in a.events:
        if e.action in SPENDS and e.at <= now and a.ctx.logical_day(e.at) == day:
            per_hour, label = SPENDS[e.action]
            hours = (e.duration / 60) if e.duration else 1
            out.append(Factor(label, -round(per_hour * hours), "Energy spent.", f"{e.at:%H:%M}"))
    return out
