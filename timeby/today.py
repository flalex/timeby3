"""v3 'Today' view model: turns rule results into what the owner sees — earlier / now / later. Pure, no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta

from .engine import planned_start
from .model import ContractReport, RuleKind, RuleResult, Status
from .recovery import RecoveryPlan
from .sleep_plan import WakePlan

WAKE_ANCHOR = "wake"  # profile anchor of waking up; its regularity goal is the root of a late start


def build_today(reports: list[ContractReport], now: datetime, notes: dict | None = None,
                wake: WakePlan | None = None, day_start: datetime | None = None,
                late_start: RecoveryPlan | None = None, skipped: set[str] | None = None,
                tracked_from: datetime | None = None) -> dict:
    notes, skipped = notes or {}, skipped or set()
    day_start = day_start or now.replace(hour=0, minute=0)
    slots = {"earlier": [], "now": [], "later": [], "avoid_now": []}
    for rep in reports:
        for r in rep.results:
            item = _skipped_item(rep, r) if r.rule.id in skipped and r.status is Status.PENDING else _place(rep, r, now)
            item.update(_extras(rep, r, item, notes.get((rep.contract.id, r.rule.id), {}), wake, now, day_start))
            slots[item["slot"]].append(item)
    by_time = lambda item: item["sort"]
    earlier = sorted(slots["earlier"], key=by_time)
    current = sorted(slots["now"], key=lambda item: (item.get("late", False), item["minutes_left"], -item["weight"]))
    leads = [i for i in current if i["remind"]]  # companion goals (no own reminder) never lead the Now card
    hero = leads[0] if leads else None
    if hero:
        hero["pair"] = _pair_for(hero, current)
    return {
        "scores": [{"id": rep.contract.id, "name": rep.contract.name, "score": rep.score} for rep in reports],
        "earlier": earlier,
        "now": {"hero": hero, "also": [i for i in current if i is not hero], "avoid": slots["avoid_now"]},
        "later": sorted(slots["later"], key=by_time),
        "tomorrow": _tomorrow(wake) if wake else None,
        "late_start": _mark_late_start(late_start, earlier, skipped),
        "tracking": _tracking(tracked_from, earlier),
    }


def _skipped_item(rep: ContractReport, r: RuleResult) -> dict:
    return _item(rep, r, "earlier", r.deadline, outcome="Skipped · late start", tone="skip")


def _pair_for(hero: dict, current: list[dict]) -> dict | None:
    """A goal that can be done together with the hero and is open right now."""
    open_now = {i["rule_id"]: i for i in current}
    for with_rule, text in hero["pairs"]:
        partner = open_now.get(with_rule)
        if partner:
            return {"text": text, "rule_id": with_rule, "action": partner["action"], "title": partner["title"]}
    return None


def _mark_late_start(plan: RecoveryPlan | None, earlier: list[dict], skipped: set[str]) -> dict | None:
    """Tag the misses a late wake-up caused, so the timeline shows one root cause instead of a wall of red."""
    if not plan:
        return None
    caused = 0
    for item in earlier:
        knock_on = item["tone"] in ("bad", "skip") and item["sort"] <= plan.wake_at.isoformat() or item["rule_id"] in skipped
        if item.get("root") or not knock_on:
            continue
        item["cause"] = "late_start"
        caused += 1
    return {"wake_at": f"{plan.wake_at:%H:%M}", "usual": f"{plan.usual:%H:%M}", "delay_min": plan.delay_min,
            "caused": caused}


# --- placement: earlier / now / later -------------------------------------------------------

def _place(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    if r.status is Status.UNTRACKED:  # nobody was watching yet: no outcome is invented
        return _item(rep, r, "earlier", r.deadline if r.rule.kind is not RuleKind.AVOID else r.end, outcome="No record", tone="none")
    if r.rule.kind is RuleKind.AVOID:
        return _place_avoid(rep, r, now)
    return _place_task(rep, r, now)


def _tracking(tracked_from: datetime | None, earlier: list[dict]) -> dict | None:
    """Live mode: when today's tracking started and how many earlier goals have no record (never judged)."""
    if tracked_from is None:
        return None
    return {"from": f"{tracked_from:%H:%M}", "untracked": sum(i["tone"] == "none" for i in earlier)}


def _place_avoid(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    if r.status is Status.VIOLATED:
        return _item(rep, r, "earlier", r.hit_at, outcome=f"Broken · {r.hit_at:%H:%M}", tone="bad")
    if r.status is Status.CLEAN:
        return _item(rep, r, "earlier", r.end, outcome="Kept", tone="good")
    if r.status is Status.PENDING and r.start <= now:
        return _item(rep, r, "avoid_now", r.start)
    return _item(rep, r, "later", r.start, time=f"{r.start:%H:%M}")


def _place_task(rep: ContractReport, r: RuleResult, now: datetime) -> dict:
    planned = planned_start(r.rule, r.anchor_at)
    if r.status is Status.DONE:
        return _item(rep, r, "earlier", r.hit_at, outcome=f"{r.hit_at:%H:%M}", tone="good")
    if r.status is Status.LATE:
        credit = f"{round(r.rule.late_credit * 100)}% credit"
        return _item(rep, r, "earlier", r.hit_at, outcome=f"Late · {r.hit_at:%H:%M} · {credit}", tone="late",
                     late_note=f"Late earns {credit} — missed earns 0. {r.rule.late_why}")
    if r.status is Status.MISSED:
        return _item(rep, r, "earlier", r.deadline, outcome="Missed", tone="bad")
    if r.status is Status.WAITING:
        return _item(rep, r, "later", planned, time="After waking", by=f"{r.deadline:%H:%M}", waiting=True)
    if now >= r.deadline:  # off schedule but the window is still open: late, worth doing for partial credit
        return _item(rep, r, "now", planned, opens=r.deadline, now=now, due=r.end, late=True)
    opens = planned - timedelta(minutes=r.rule.tolerance)  # doing it earlier still counts ("Done early")
    if now >= opens:
        return _item(rep, r, "now", planned, opens=opens, now=now)
    return _item(rep, r, "later", planned, time=f"{planned:%H:%M}", by=f"{r.deadline:%H:%M}")


def _item(rep: ContractReport, r: RuleResult, slot: str, at: datetime, *, opens: datetime | None = None,
          now: datetime | None = None, due: datetime | None = None, **extra) -> dict:
    item = {
        "slot": slot, "sort": at.isoformat(), "at": f"{at:%H:%M}",
        "contract_id": rep.contract.id, "rule_id": r.rule.id, "kind": r.rule.kind.value,
        "title": r.rule.label, "advice": r.rule.advice, "action": r.rule.actions[0], "weight": r.rule.weight,
        "remind": r.rule.remind, "pairs": [(p.with_rule, p.text) for p in r.rule.pairs],
        "root": r.rule.kind is RuleKind.REGULARITY and r.rule.window.anchor == WAKE_ANCHOR,
        **extra,
    }
    if slot == "now":
        due = due or r.deadline
        span = (due - opens).total_seconds() or 1
        item["minutes_left"] = round((due - now).total_seconds() / 60)
        item["by"] = f"{due:%H:%M}"
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
                          "action": r.hit_action or rule.actions[0], "counts": list(rule.actions),
                          "min": f"{earliest:%H:%M}", "max": f"{latest:%H:%M}", "until_now": latest == now}
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
