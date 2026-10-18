"""Tomorrow's wake-up time. Consistency first; sleep length only as a guard rail. Deterministic policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .engine import Context

MIN_SLEEP = timedelta(hours=7)      # AASM/SRS adult minimum (Watson 2015)
MAX_SHIFT = timedelta(minutes=60)   # sleeping in more than ~1 h creates "social jetlag" (Wittmann 2006)
SOURCES = (
    "Windred DP et al. Sleep regularity is a stronger predictor of mortality risk than sleep duration. Sleep, 2024.",
    "Phillips AJK et al. Irregular sleep/wake patterns are associated with poorer academic performance and "
    "delayed circadian and sleep/wake timing. Sci Rep, 2017.",
    "Watson NF et al. Recommended amount of sleep for a healthy adult (AASM/SRS consensus). Sleep, 2015.",
    "Wittmann M et al. Social jetlag: misalignment of biological and social time. Chronobiol Int, 2006.",
)


@dataclass(frozen=True)
class WakePlan:
    at: datetime        # recommended wake-up
    planned: datetime   # the usual wake-up
    bed_at: datetime    # actual lights-out, or when it can happen at the earliest
    sleep: timedelta    # sleep you get with the recommendation
    reason: str


def plan_wake(ctx: Context, now: datetime, wake_anchor: str = "wake", sleep_anchor: str = "sleep") -> WakePlan:
    day = ctx.logical_day(now)
    bed_at = ctx.observed(sleep_anchor, day) or max(ctx.planned_anchor(sleep_anchor, day), now)
    planned = ctx.planned_anchor(wake_anchor, day + timedelta(days=1))
    shift = min(max(MIN_SLEEP - (planned - bed_at), timedelta(0)), MAX_SHIFT)
    at = planned + shift
    return WakePlan(at, planned, bed_at, at - bed_at, _reason(shift, at - bed_at))


def _reason(shift: timedelta, sleep: timedelta) -> str:
    if not shift:
        return ("Same time as every day: a regular wake time predicts health better than extra sleep, "
                "and it keeps tomorrow's cortisol peak on schedule.")
    minutes = int(shift.total_seconds() // 60)
    short = " Still under 7 h — aim for an earlier night." if sleep < MIN_SLEEP else ""
    return (f"{minutes} min later than usual to protect sleep. We never shift more than 60 min: "
            f"sleeping in longer breaks your rhythm more than it helps.{short}")
