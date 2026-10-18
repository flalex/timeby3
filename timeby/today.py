"""v3 'Today' view model: turns rule results into what the owner sees — earlier / now / later. Pure, no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta

from .model import ContractReport, RuleKind, RuleResult, Status
from .sleep_plan import WakePlan


def build_today(reports: list[ContractReport], now: datetime, notes: dict | None = None,
                wake: WakePlan | None = None, day_start: datetime | None = None) -> dict:
    notes = notes or {}
    day_start = day_start or now.replace(hour=0, minute=0)
    slots = {"earlier": [], "now": [], "later": [], "avoid_now": []}
    for rep in reports:
        for r in rep.results:
            item = _place(rep, r, now)
            item.update(_extras(rep, r, item, notes.get((rep.contract.id, r.rule.id), {}), wake, now, day_start))
            slots[item["slot"]].append(item)
    by_time = lambda item: item["sort"]
    current = sorted(slots["now"], key=lambda item: (item["minutes_left"], -item["weight"]))
    return {
        "scores": [{"id": rep.contract.id, "name": rep.contract.name, "score": rep.score} for rep in reports],
        "earlier": sorted(slots["earlier"], key=by_time),
        "now": {"hero": current[0] if current else None, "also": current[1:], "avoid": slots["avoid_now"]},
        "later": sorted(slots["later"], key=by_time),
        "tomorrow": _tomorrow(wake) if wake else None,
    }


# --- placement: earlier / now / later -------------------------------------------------------

def _place(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    if r.rule.kind is RuleKind.AVOID:
        return _place_avoid(rep, r, now)
    return _place_task(rep, r, now)


def _place_avoid(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    if r.status is Status.VIOLATED:
        return _item(rep, r, "earlier", r.hit_at, outcome=f"Broken · {r.hit_at:%H:%M}", tone="bad")
    if r.status is Status.CLEAN:
        return _item(rep, r, "earlier", r.end, outcome="Kept", tone="good")
    if r.status is Status.PENDING and r.start <= now:
        return _item(rep, r, "avoid_now", r.start)
    return _item(rep, r, "later", r.start, time=f"{r.start:%H:%M}")


def _place_task(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    regular = r.rule.kind is RuleKind.REGULARITY
    planned = r.anchor_at if regular else r.start
    if r.status is Status.DONE:
        return _item(rep, r, "earlier", r.hit_at, outcome=f"{r.hit_at:%H:%M}", tone="good")
    if r.status is Status.MISSED:
        late = f"Late · {r.hit_at:%H:%M}" if r.hit_at else "Missed"
        return _item(rep, r, "earlier", r.hit_at or r.deadline, outcome=late, tone="bad")
    if r.status is Status.WAITING:
        return _item(rep, r, "later", planned, time="After waking")
    if now >= r.deadline:  # still loggable, but no longer on time
        return _item(rep, r, "earlier", r.deadline, outcome="Missed", tone="bad")
    opens = r.anchor_at - timedelta(minutes=r.rule.tolerance) if regular else r.start
    if now >= opens:
        return _item(rep, r, "now", planned, opens=opens, now=now)
    return _item(rep, r, "later", planned, time=f"{planned:%H:%M}")


def _item(rep: ContractReport, r: RuleResult, slot: str, at: datetime, *, opens: datetime | None = None,
          now: datetime | None = None, **extra) -> dict:
    item = {
        "slot": slot, "sort": at.isoformat(), "at": f"{at:%H:%M}",
        "contract_id": rep.contract.id, "rule_id": r.rule.id, "kind": r.rule.kind.value,
        "title": r.rule.label, "advice": r.rule.advice, "action": r.rule.actions[0], "weight": r.rule.weight,
        **extra,
    }
    if slot == "now":
        span = (r.deadline - opens).total_seconds() or 1
        item["minutes_left"] = round((r.deadline - now).total_seconds() / 60)
        item["by"] = f"{r.deadline:%H:%M}"
        item["progress"] = round(min(1.0, max(0.0, (now - opens).total_seconds() / span)), 3)
    return item


# --- details: duration, limits, notes, editing ------------------------------------------------

def _extras(rep: ContractReport, r: RuleResult, item: dict, note: dict, wake: WakePlan | None,
            now: datetime, day_start: datetime) -> dict:
    rule = r.rule
    extras = {"duration": note.get("duration") or _planned_duration(r, wake), "comment": note.get("comment", "")}
    if rule.kind is RuleKind.AVOID and r.until:
        extras["until"] = _until_label(r)
    if item["slot"] == "earlier":
        happened = r.hit_at is not None
        default_at = r.hit_at or (r.anchor_at if rule.kind is RuleKind.REGULARITY else r.start)
        # same range Assistant.correctable_range enforces: tasks any time today, limits only in their window
        earliest, latest = (r.start, min(r.end - timedelta(minutes=1), now)) if rule.kind is RuleKind.AVOID \
            else (day_start, now)
        extras["edit"] = {"happened": happened, "at": f"{min(default_at, latest):%H:%M}",
                          "min": f"{earliest:%H:%M}", "max": f"{latest:%H:%M}"}
    return extras


ANCHOR_WORDS = {"sleep": "bedtime", "wake": "wake-up"}


def _until_label(r: RuleResult) -> str:
    if r.rule.reopens:
        return f"{r.until:%H:%M}" + (" tomorrow" if r.until.date() > r.start.date() else "")
    if r.rule.window.end == 0:  # lasts until the anchor event itself, e.g. until you actually go to bed
        return ANCHOR_WORDS.get(r.rule.window.anchor, r.rule.window.anchor)
    return f"{r.until:%H:%M}"


def _planned_duration(r: RuleResult, wake: WakePlan | None) -> int | None:
    if r.rule.actions[0] == "sleep" and wake:  # lights out lasts until tomorrow's wake-up
        return int(wake.sleep.total_seconds() // 60)
    return r.rule.duration


def _tomorrow(wake: WakePlan) -> dict:
    shift = int((wake.at - wake.planned).total_seconds() // 60)
    return {"at": f"{wake.at:%H:%M}", "usual": f"{wake.planned:%H:%M}", "shift_min": shift,
            "sleep_min": int(wake.sleep.total_seconds() // 60), "bed_at": f"{wake.bed_at:%H:%M}", "reason": wake.reason}
