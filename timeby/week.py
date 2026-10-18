"""Weekly view model: Monday–Sunday, every goal's day-by-day outcome and whether its weekly target is still safe.

Built only from the engine's own day results (each past day judged at its end, today live, later days planned), so
the week can never disagree with the day view. Pure: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .assistant import Assistant
from .engine import applies
from .model import Contract, Rule, Status

DONE, LATE, MISSED, OPEN, PLANNED = "done", "late", "missed", "open", "planned"
CELL = {Status.DONE: DONE, Status.CLEAN: DONE, Status.LATE: LATE, Status.MISSED: MISSED, Status.VIOLATED: MISSED,
        Status.PENDING: OPEN, Status.WAITING: OPEN}
HABIT_SHARE = 5 / 7  # default target: most planned days — an occasional miss doesn't break a habit
DEFAULT_WHY = "Most days count — an occasional miss doesn't break the habit."


@dataclass(frozen=True)
class Progress:
    done: int
    judged: int
    remaining: list[date]  # days still open: today (if not resolved) and the planned days ahead
    target: int


def build_week(assistant: Assistant, now: datetime) -> dict:
    today = assistant.ctx.logical_day(now)
    days = [today - timedelta(days=today.weekday()) + timedelta(days=i) for i in range(7)]
    results = {day: _day_results(assistant, day, now, today) for day in days if day <= today}
    agents = [_agent(c, days, today, results) for c in assistant.active]
    return {
        "days": [{"date": d.isoformat(), "label": f"{d:%a}", "num": d.day, "today": d == today, "past": d < today}
                 for d in days],
        "range": f"{days[0]:%a %d %b} – {days[-1]:%a %d %b}",
        "agents": agents,
        "focus": _focus(agents, days.index(today)),
    }


def _focus(agents: list[dict], today_index: int) -> list[dict]:
    """Goals that need the owner today: still doable today and critical or at risk — most critical, heaviest first."""
    urgent = [dict(goal, agent=a["name"]) for a in agents for goal in a["goals"]
              if goal["cells"][today_index] == OPEN and goal["state"] in ("critical", "at_risk")]
    return sorted(urgent, key=lambda g: (g["state"] != "critical", -g["weight"]))


def _day_results(a: Assistant, day: date, now: datetime, today: date) -> dict[tuple[str, str], Status]:
    """Each past day judged at its very end; today as it stands now."""
    when = now if day == today else datetime.combine(day + timedelta(days=1), a.profile.day_start) - timedelta(minutes=1)
    return {(rep.contract.id, r.rule.id): r.status for rep in a.status(when) for r in rep.results}


def _agent(contract: Contract, days: list[date], today: date, results: dict) -> dict:
    goals = [_goal(contract.id, rule, days, today, results) for rule in contract.rules]
    done, judged = sum(g["done"] for g in goals), sum(g["judged"] for g in goals)
    return {"id": contract.id, "name": contract.name, "done": done, "judged": judged,
            "states": {s: sum(g["state"] == s for g in goals) for s in ("met", "on_track", "at_risk", "critical", "out_of_reach")},
            "goals": goals}


def _goal(contract_id: str, rule: Rule, days: list[date], today: date, results: dict) -> dict:
    cells = [_cell(contract_id, rule, day, today, results) for day in days]
    progress = _progress(rule, days, cells, today)
    state, note = _verdict(progress, today)
    return {"id": rule.id, "contract_id": contract_id, "label": rule.label, "topic": rule.topic, "action": rule.actions[0],
            "kind": rule.kind.value, "weight": rule.weight, "cells": cells, "done": progress.done, "judged": progress.judged,
            "target": progress.target, "planned": sum(c is not None for c in cells), "state": state, "note": note,
            "why": rule.weekly_why or DEFAULT_WHY}


def _cell(contract_id: str, rule: Rule, day: date, today: date, results: dict) -> str | None:
    if day > today:
        return PLANNED if applies(rule, day) else None
    status = results[day].get((contract_id, rule.id))
    if status is None:
        return None  # not planned that day (weekday goal) or switched off
    cell = CELL[status]
    return MISSED if cell == OPEN and day < today else cell


def _progress(rule: Rule, days: list[date], cells: list[str | None], today: date) -> Progress:
    planned = sum(c is not None for c in cells)
    done = sum(c == DONE for c in cells)  # the week counts proper, on-time completions; late shows as ◐
    judged = done + sum(c in (LATE, MISSED) for c in cells)
    remaining = [d for d, c in zip(days, cells) if c in (OPEN, PLANNED)]
    target = rule.weekly_target if rule.weekly_target is not None else max(1, round(planned * HABIT_SHARE))
    return Progress(done, judged, remaining, min(target, planned) if planned else 0)


def _verdict(p: Progress, today: date) -> tuple[str, str]:
    """met / on_track / at_risk / critical / out_of_reach, with the reason in plain words."""
    need = p.target - p.done
    if not p.target:
        return "none", "Not planned this week."
    if need <= 0:
        return "met", f"Weekly target met ({p.done}/{p.target})."
    if need > len(p.remaining):
        return "out_of_reach", f"{p.target} this week is out of reach now — a fresh start on Monday."
    days = ", ".join("today" if d == today else f"{d:%a}" for d in p.remaining)
    if need == len(p.remaining):
        return "critical", f"Critical: every remaining one is needed ({days}) to reach {p.target} this week."
    if p.remaining and p.remaining[0] == today and need == len(p.remaining) - 1:
        later = [f"{d:%a}" for d in p.remaining[1:]]
        what = "the rest of the week becomes" if len(later) > 2 else f"{' and '.join(later)} {'becomes' if len(later) == 1 else 'become'}"
        return "at_risk", f"Do it today — otherwise {what} critical."
    return "on_track", f"{need} more needed, {len(p.remaining)} chances left ({days})."
