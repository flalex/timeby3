"""Deterministic contract interpreter: scoring, recommendations, nudges. No LLM, no randomness."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import pstdev

from .model import (
    NEGATIVE, POSITIVE, Contract, ContractReport, Event, Message, Profile, Recommendation,
    Reminder, Rule, RuleKind, RuleResult, Status, Window,
)

OBSERVE_RADIUS = timedelta(hours=3)  # an observed anchor must be within this of the planned time


@dataclass(frozen=True)
class Context:
    profile: Profile
    events: tuple[Event, ...]
    wait_for_observed: bool = False  # hold rules anchored on an unobserved event (e.g. wake) as WAITING
    tracked_from: dict[date, datetime] | None = None  # live: when tracking started each day; None = judge everything
    plan_changes: dict[tuple[date, str], int | None] | None = None  # owner's changes for one day: minutes moved, None = skipped

    def logical_day(self, at: datetime) -> date:
        start = self.profile.day_start
        return (at - timedelta(hours=start.hour, minutes=start.minute)).date()

    def first_day(self, default: date) -> date:
        return min((self.logical_day(e.at) for e in self.events), default=default)

    def planned_anchor(self, name: str, day: date) -> datetime:
        t = self.profile.anchors[name].time
        at = datetime.combine(day, t)
        return at + timedelta(days=1) if t < self.profile.day_start else at

    def observed(self, name: str, day: date) -> datetime | None:
        """When the anchor actually happened, if an observing event was logged."""
        planned = self.planned_anchor(name, day)
        action = self.profile.anchors[name].observed_by
        seen = [e.at for e in self.events if e.action == action and abs(e.at - planned) <= OBSERVE_RADIUS]
        return min(seen, key=lambda at: abs(at - planned), default=None)

    def moved_by(self, rule_id: str, day: date) -> timedelta:
        return timedelta(minutes=(self.plan_changes or {}).get((day, rule_id)) or 0)

    def skipped(self, rule_id: str, day: date) -> bool:
        changes = self.plan_changes or {}
        return (day, rule_id) in changes and changes[(day, rule_id)] is None

    def untracked(self, day: date, end: datetime) -> bool:
        """Live mode: a window that closed before tracking started that day can't be judged — nobody was watching."""
        if self.tracked_from is None:
            return False
        start = self.tracked_from.get(day)
        return start is None or end <= start

    def observed_anchor(self, name: str, day: date) -> datetime:
        return self.observed(name, day) or self.planned_anchor(name, day)

    def matching(self, rule: Rule, start: datetime, end: datetime, now: datetime, day: date) -> list[Event]:
        """This rule's events: ones the owner tied to it (that day), else by action inside the window.
        An event tied to another goal still counts for limits (avoid), never for another task."""
        hits = []
        for e in self.events:
            if e.action not in rule.actions or e.at > now:
                continue
            if e.goal == rule.id:
                if self.logical_day(e.at) == day:
                    hits.append(e)
            elif (e.goal is None or rule.kind is RuleKind.AVOID) and start <= e.at < end:
                hits.append(e)
        return hits


def bounds(window: Window, anchor_at: datetime) -> tuple[datetime, datetime]:
    return anchor_at + timedelta(minutes=window.start), anchor_at + timedelta(minutes=window.end)


def rule_anchor(rule: Rule, ctx: Context, day: date) -> datetime:
    """Regularity is judged against the plan; do/avoid windows follow the observed day."""
    anchor = ctx.planned_anchor if rule.kind is RuleKind.REGULARITY else ctx.observed_anchor
    return anchor(rule.window.anchor, day) + ctx.moved_by(rule.id, day)


def _target(rule: Rule, ctx: Context, day: date) -> datetime:
    """The planned time a regularity goal is judged against that day (moved if the owner moved it)."""
    return ctx.planned_anchor(rule.window.anchor, day) + ctx.moved_by(rule.id, day)


def scheduled(rule: Rule, ctx: Context, day: date) -> bool:
    """On the plan that day: its weekday, and not skipped by the owner."""
    return applies(rule, day) and not ctx.skipped(rule.id, day)


def rule_bounds(rule: Rule, ctx: Context, day: date) -> tuple[datetime, datetime]:
    return bounds(rule.window, rule_anchor(rule, ctx, day))


# --- evaluation -----------------------------------------------------------------------------

def evaluate_rule(rule: Rule, ctx: Context, now: datetime) -> RuleResult:
    if rule.kind is RuleKind.REGULARITY:
        return _judge_regularity(rule, ctx, now)
    return _judge_window(rule, ctx, now)


def _earned(rule: Rule, status: Status) -> float | None:
    if status in POSITIVE:
        return rule.weight
    if status is Status.LATE:
        return rule.weight * rule.late_credit
    return 0.0 if status in NEGATIVE else None


def _result(rule: Rule, ctx: Context, day: date, status: Status, earned: float | None,
            detail: str = "", hit_at: datetime | None = None, end: datetime | None = None) -> RuleResult:
    anchor_at = rule_anchor(rule, ctx, day)
    start, planned_end = bounds(rule.window, anchor_at)
    end = end or planned_end
    until = ctx.planned_anchor(rule.reopens, day + timedelta(days=1)) if rule.reopens else end
    return RuleResult(rule, status, earned, start, end, anchor_at + timedelta(minutes=deadline_offset(rule)),
                      detail, anchor_at, _is_estimated(rule, ctx, day), reminder(rule, anchor_at), hit_at, until)


def _is_estimated(rule: Rule, ctx: Context, day: date) -> bool:
    if rule.kind is RuleKind.REGULARITY:  # judged against the plan, never estimated
        return False
    observable = ctx.profile.anchors[rule.window.anchor].observed_by is not None
    return observable and ctx.observed(rule.window.anchor, day) is None


def _waits_for_anchor(rule: Rule, ctx: Context, day: date, now: datetime) -> bool:
    """A rule that follows an event (window starts at/after it) can't start before the event is observed —
    but stops waiting once the event is long overdue (then the plan stands)."""
    overdue = now >= ctx.planned_anchor(rule.window.anchor, day) + OBSERVE_RADIUS
    return ctx.wait_for_observed and rule.window.start >= 0 and _is_estimated(rule, ctx, day) and not overdue


def _open_until_observed(rule: Rule, ctx: Context, day: date, end: datetime, now: datetime) -> datetime:
    """A limit that runs up to an event (e.g. bedtime) stays on until that event actually happens."""
    if not (ctx.wait_for_observed and rule.kind is RuleKind.AVOID and rule.window.end >= 0
            and _is_estimated(rule, ctx, day)):
        return end
    latest = ctx.planned_anchor(rule.window.anchor, day) + OBSERVE_RADIUS
    return max(end, min(now + timedelta(minutes=1), latest))


def _judge_window(rule: Rule, ctx: Context, now: datetime) -> RuleResult:
    day = ctx.logical_day(now)
    start, end = rule_bounds(rule, ctx, day)
    end = _open_until_observed(rule, ctx, day, end, now)
    hits = ctx.matching(rule, start, end, now, day)
    on_time = [h for h in hits if start <= h.at < end]
    hit, over = bool(hits), now >= end
    if not hit and over and ctx.untracked(day, end):
        status = Status.UNTRACKED
    elif _waits_for_anchor(rule, ctx, day, now) and not hit and not over:
        status = Status.WAITING
    elif rule.kind is RuleKind.DO:  # a goal-tied event outside the window happened, but late
        status = Status.DONE if on_time else Status.LATE if hit else Status.MISSED if over else Status.PENDING
        hits = on_time or hits
    else:
        status = Status.VIOLATED if hit else Status.CLEAN if over else Status.PENDING
    return _result(rule, ctx, day, status, _earned(rule, status), hit_at=hits[0].at if hits else None, end=end)


def _closest_hit(rule: Rule, ctx: Context, day: date, now: datetime) -> tuple[bool, datetime | None, bool]:
    """(judgeable, when the action happened, untracked). None = skipped once the window closed."""
    target = _target(rule, ctx, day)
    start, end = bounds(rule.window, target)
    hits = ctx.matching(rule, start, end, now, day)
    if hits:
        return True, min(hits, key=lambda e: abs(e.at - target)).at, False
    return now >= end, None, now >= end and ctx.untracked(day, end)


def _judge_regularity(rule: Rule, ctx: Context, now: datetime) -> RuleResult:
    today = ctx.logical_day(now)
    first = ctx.first_day(today)
    days = [today - timedelta(days=i) for i in range(rule.lookback_days)]
    judged, on_time, credit, devs, today_status, today_hit = 0, 0, 0.0, [], Status.PENDING, None

    for day in (d for d in days if d >= first):
        judgeable, hit_at, untracked = _closest_hit(rule, ctx, day, now)
        if untracked and day == today:
            today_status = Status.UNTRACKED
        if not judgeable or untracked:
            continue
        dev = round((hit_at - _target(rule, ctx, day)).total_seconds() / 60) if hit_at else None
        ok = dev is not None and abs(dev) <= rule.tolerance
        status = Status.DONE if ok else Status.LATE if dev is not None else Status.MISSED
        judged += 1
        on_time += ok
        credit += 1.0 if ok else rule.late_credit if dev is not None else 0.0
        if dev is not None:
            devs.append(dev)
        if day == today:
            today_status, today_hit = status, hit_at

    earned = rule.weight * credit / judged if judged else None
    return _result(rule, ctx, today, today_status, earned, _regularity_detail(rule, judged, on_time, devs), today_hit)


def _regularity_detail(rule: Rule, judged: int, on_time: int, devs: list[int]) -> str:
    if not judged:
        return "no data yet"
    detail = f"{on_time}/{judged} days within ±{rule.tolerance} min"
    if len(devs) >= 2:
        detail += f", spread ±{pstdev(devs):.0f} min"
    return detail


def applies(rule: Rule, day: date) -> bool:
    return not rule.days or WEEKDAYS[day.weekday()] in rule.days


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def evaluate(contract: Contract, ctx: Context, now: datetime) -> ContractReport:
    day = ctx.logical_day(now)
    results = tuple(evaluate_rule(r, ctx, now) for r in contract.rules if scheduled(r, ctx, day))
    judged = [r for r in results if r.earned is not None]
    total = sum(r.rule.weight for r in judged)
    score = round(100 * sum(r.earned for r in judged) / total) if total else None
    return ContractReport(contract, _current_phase(contract, ctx, now), score, results)


def _current_phase(contract: Contract, ctx: Context, now: datetime):
    day = ctx.logical_day(now)
    for phase in contract.phases:
        start, end = bounds(phase.window, ctx.observed_anchor(phase.window.anchor, day))
        if start <= now < end:
            return phase
    return None


# --- proactive layer ------------------------------------------------------------------------

def recommendations(report: ContractReport, now: datetime) -> list[Recommendation]:
    """Pending rules whose window is open right now: things to do, and things to avoid."""
    return [
        Recommendation(report.contract, r.rule, r.deadline if r.deadline > now else r.end)
        for r in report.results
        if r.status is Status.PENDING and r.start <= now < r.end
    ]


LAST_CALL = timedelta(minutes=15)


def deadline_offset(rule: Rule) -> int:
    """Minutes after the anchor by which the rule must be met on time."""
    return rule.tolerance if rule.kind is RuleKind.REGULARITY else rule.window.end


def reminder(rule: Rule, anchor_at: datetime) -> Reminder | None:
    """At most one scheduled reminder per rule: its contract nudge, else a last call before the deadline."""
    if not rule.remind:
        return None
    if rule.nudge:
        return Reminder(anchor_at + timedelta(minutes=rule.nudge.at), "nudge",
                        anchor_at + timedelta(minutes=rule.window.end), rule.nudge.text)
    if rule.kind is RuleKind.AVOID:
        return None
    deadline = anchor_at + timedelta(minutes=deadline_offset(rule))
    return Reminder(deadline - LAST_CALL, "last_call", deadline,
                    f"Last call: {rule.advice} (on-time window closes {deadline:%H:%M})")


def due_reminders(report: ContractReport, ctx: Context, now: datetime) -> list[Message]:
    """Reminders whose time has come while their rule is still unresolved."""
    return [
        Message(r.reminder.at, r.reminder.kind, report.contract.id, r.rule.id, r.reminder.text)
        for r in report.results
        if r.status is Status.PENDING and r.reminder and r.reminder.at <= now < r.reminder.expires
    ]


def day_plan(contracts: list[Contract], ctx: Context, day: date) -> list[tuple[datetime, Contract, Rule]]:
    """Every scheduled reminder for a day, ready to be pushed to a calendar."""
    plan = []
    for contract in contracts:
        for rule in (r for r in contract.rules if scheduled(r, ctx, day)):
            rem = reminder(rule, rule_anchor(rule, ctx, day))  # same anchor the live reminder uses
            if rem:
                plan.append((rem.at, contract, rule))
    return sorted(plan, key=lambda item: item[0])
