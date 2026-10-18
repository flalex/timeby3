"""Core data types. Contracts are pure data; the engine interprets them deterministically."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum


class RuleKind(str, Enum):
    DO = "do"                  # action should happen inside the window
    AVOID = "avoid"            # action should NOT happen inside the window
    REGULARITY = "regularity"  # action should happen at the same planned time every day


class Status(str, Enum):
    PENDING = "pending"
    DONE = "done"
    MISSED = "missed"
    CLEAN = "clean"
    VIOLATED = "violated"
    WAITING = "waiting"  # depends on an event (e.g. wake) that hasn't been observed yet
    LATE = "late"        # done, but off schedule: earns the rule's late_credit share


POSITIVE = {Status.DONE, Status.CLEAN}
NEGATIVE = {Status.MISSED, Status.VIOLATED}


@dataclass(frozen=True)
class Window:
    anchor: str  # name of a profile anchor, e.g. "wake", "sleep", "lunch"
    start: int   # minutes relative to anchor
    end: int


@dataclass(frozen=True)
class Nudge:
    at: int      # minutes relative to the rule's window anchor
    text: str


@dataclass(frozen=True)
class Pair:
    with_rule: str  # id of a goal (any agent) that can be done together with this one
    text: str       # how to combine them


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    phase: str
    kind: RuleKind
    actions: tuple[str, ...]
    window: Window
    weight: float
    strength: str              # evidence strength: strong | moderate | emerging
    why: str
    advice: str
    sources: tuple[str, ...]
    nudge: Nudge | None = None
    tolerance: int = 0         # regularity only: allowed deviation, minutes
    label: str = ""            # short app-facing name, e.g. "Breakfast"
    duration: int | None = None  # planned minutes the activity takes (do/regularity)
    reopens: str | None = None   # avoid only: anchor (next day) when the limit lifts, e.g. "wake"
    days: tuple[str, ...] = ()   # weekdays it applies (mon..sun); empty = every day
    topic: str = ""              # groups goals inside an agent, e.g. "Meal regularity"
    pairs: tuple[Pair, ...] = () # goals that can be combined with this one when time is short
    late_credit: float = 0.5     # share of the weight earned when done late (doing it late beats skipping)
    remind: bool = True          # False: no own reminder (e.g. raised together with a paired goal)
    lookback_days: int = 7     # regularity only


@dataclass(frozen=True)
class Phase:
    id: str
    goal: str
    window: Window


@dataclass(frozen=True)
class Contract:
    id: str
    name: str
    domain: str
    goal: str
    phases: tuple[Phase, ...]
    rules: tuple[Rule, ...]
    sources: dict[str, str]


@dataclass(frozen=True)
class Anchor:
    time: time
    observed_by: str | None = None  # action that reveals the real time (e.g. "wake")


@dataclass(frozen=True)
class Profile:
    anchors: dict[str, Anchor]
    day_start: time = time(4, 0)  # logical day boundary: 01:30 still belongs to "yesterday"


@dataclass(frozen=True)
class Event:
    action: str
    at: datetime
    goal: str | None = None  # rule id when the owner tied this event to a goal (beats window-based guessing)


@dataclass(frozen=True)
class Reminder:
    at: datetime
    kind: str          # "nudge" | "last_call"
    expires: datetime  # pointless to send after this
    text: str


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    status: Status
    earned: float | None  # None = not judged yet
    start: datetime
    end: datetime
    deadline: datetime    # latest moment the rule can still be satisfied on time ("due")
    detail: str = ""
    anchor_at: datetime | None = None  # the anchor today's times derive from
    estimated: bool = False            # anchor not observed yet: times are based on the plan
    reminder: Reminder | None = None   # the one scheduled reminder for this rule today
    hit_at: datetime | None = None     # when today's matching action happened
    until: datetime | None = None      # avoid only: when the limit lifts (end, or `reopens` next day)


@dataclass(frozen=True)
class ContractReport:
    contract: Contract
    phase: Phase | None
    score: int | None     # 0..100 over judged rules only
    results: tuple[RuleResult, ...]


@dataclass(frozen=True)
class Recommendation:
    contract: Contract
    rule: Rule
    deadline: datetime

    @property
    def avoid(self) -> bool:
        return self.rule.kind is RuleKind.AVOID


@dataclass(frozen=True)
class Message:
    at: datetime
    kind: str  # nudge | last_call | brief | recap | feedback | reply
    contract_id: str
    rule_id: str
    text: str
    meta: dict = field(default_factory=dict)
