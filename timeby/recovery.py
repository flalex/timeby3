"""Late-start recovery: when the owner wakes up late, one plan instead of a cascade of misses. Deterministic.

Policy (Option D):
- goals that follow wake-up (e.g. daylight within 1 h of waking) simply run from the actual wake time;
- fixed-time goals still in their window are "late but worth it" (they earn late credit);
- a late meal is skipped when the next meal of the same kind is close (merge into it), never pushed into it;
- everything later today stays exactly where it was — one bad morning must not shift the evening.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import ContractReport, RuleKind, RuleResult, Status

MERGE_GAP = timedelta(hours=3)  # a late meal this close to the next one is merged into it


@dataclass
class RecoveryPlan:
    wake_at: datetime
    usual: datetime
    now_items: list[tuple[str, datetime]] = field(default_factory=list)      # (label, due)
    shifted: list[tuple[str, datetime]] = field(default_factory=list)        # (label, new start) — moved with wake-up
    late_items: list[tuple[str, datetime]] = field(default_factory=list)     # (label, window closes)
    skipped: list[tuple[str, str]] = field(default_factory=list)             # (label, reason)
    unchanged: list[tuple[str, datetime]] = field(default_factory=list)      # (label, planned time)
    combos: list[str] = field(default_factory=list)
    mentions: list[tuple[str, str]] = field(default_factory=list)            # (contract_id, rule_id) covered
    skipped_ids: list[tuple[str, str]] = field(default_factory=list)

    @property
    def delay_min(self) -> int:
        return int((self.wake_at - self.usual).total_seconds() // 60)


def build_recovery(reports: list[ContractReport], wake_at: datetime, usual: datetime, now: datetime,
                   wake_anchor: str) -> RecoveryPlan:
    plan = RecoveryPlan(wake_at, usual)
    tasks = [(rep.contract.id, r) for rep in reports for r in rep.results if _plannable(r.rule, wake_anchor)]
    open_now: dict[str, RuleResult] = {}
    for cid, r in tasks:
        _classify(plan, cid, r, now, wake_anchor, tasks, open_now)
    plan.combos = _combos(open_now)
    plan.now_items.sort(key=lambda item: item[1])
    plan.unchanged.sort(key=lambda item: item[1])
    plan.shifted.sort(key=lambda item: item[1])
    return plan


def _classify(plan: RecoveryPlan, cid: str, r: RuleResult, now: datetime, wake_anchor: str,
              tasks: list, open_now: dict) -> None:
    rule, key = r.rule, (cid, r.rule.id)
    pending = r.status in (Status.PENDING, Status.WAITING)
    if rule.window.anchor == wake_anchor:  # follows the actual wake-up
        if pending and r.start <= now:
            plan.now_items.append((rule.label, r.deadline))
            plan.mentions.append(key)
            open_now[rule.id] = r
        elif pending:
            plan.shifted.append((rule.label, r.start))
        return
    if r.status is Status.MISSED:
        plan.skipped.append((rule.label, "window closed before you woke up"))
        plan.mentions.append(key)
        return
    if not pending:
        return
    if now < r.deadline:
        if rule.kind is RuleKind.REGULARITY:
            plan.unchanged.append((rule.label, r.anchor_at))
        return
    nxt = _next_same_kind(r, now, tasks)
    if nxt:
        plan.skipped.append((rule.label, f"too close to {nxt.rule.label.lower()} — merge into it"))
        plan.skipped_ids.append(key)
    else:
        plan.late_items.append((rule.label, r.end))
        open_now[rule.id] = r
    plan.mentions.append(key)


def _plannable(rule, wake_anchor: str) -> bool:
    """Tasks with their own reminder, except the wake-up goal itself (it is the late start)."""
    is_wake_goal = rule.kind is RuleKind.REGULARITY and rule.window.anchor == wake_anchor
    return rule.kind is not RuleKind.AVOID and rule.remind and not is_wake_goal


def _next_same_kind(r: RuleResult, now: datetime, tasks: list) -> RuleResult | None:
    for _, other in tasks:
        if other.rule.id != r.rule.id and other.rule.kind is RuleKind.REGULARITY \
                and set(other.rule.actions) & set(r.rule.actions) and now < other.anchor_at < now + MERGE_GAP:
            return other
    return None


def _combos(open_now: dict[str, RuleResult]) -> list[str]:
    """Pairing suggestions for goals that are both open right now (each pair once)."""
    combos, seen = [], set()
    for r in open_now.values():
        for p in r.rule.pairs:
            pair = frozenset((r.rule.id, p.with_rule))
            if p.with_rule in open_now and pair not in seen:
                seen.add(pair)
                combos.append(p.text)
    return combos
