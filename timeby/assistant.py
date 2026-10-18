"""The agent facade: ingests events, proactively nudges, reports state. Clients/data sources plug in via ports."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Protocol

from . import engine, intents, narration
from .model import Contract, ContractReport, Event, Message, Profile, Recommendation, RuleResult

ROUTINE_WINDOW = timedelta(hours=3)  # brief/recap only fire this soon after their anchor
RECAP_GRACE = timedelta(hours=1)     # without an observed bedtime, recap this long after the planned one
MENTION_COOLDOWN = timedelta(minutes=30)  # don't remind about a rule the agent just talked about


class Notifier(Protocol):
    """Outbound channel to the owner (chat client, push, etc.)."""
    def send(self, message: Message) -> None: ...


class EventSource(Protocol):
    """Inbound data (wearables, phone, calendar) that produces events automatically."""
    def pull(self, since: datetime, until: datetime) -> list[Event]: ...


class Assistant:
    def __init__(self, profile: Profile, contracts: list[Contract], actions: dict[str, str], notifier: Notifier,
                 wake_anchor: str = "wake", sleep_anchor: str = "sleep", wait_for_observed: bool = False,
                 compact_routines: bool = False):
        self._validate_anchors(profile, contracts, {wake_anchor, sleep_anchor})
        self.profile = profile
        self.contracts = contracts  # all agents; switched-off ones are filtered out by `active`
        self.disabled_contracts: set[str] = set()
        self.disabled_rules: set[tuple[str, str]] = set()
        self.notes: dict[tuple[date, str, str], dict] = {}  # owner's duration/comment per goal per day
        self.actions = actions
        self.notifier = notifier
        self.wake_anchor = wake_anchor
        self.sleep_anchor = sleep_anchor
        self.wait_for_observed = wait_for_observed
        self.compact_routines = compact_routines  # on when the client already shows the day's items
        self.events: list[Event] = []
        self._sent: set[tuple] = set()
        self._snoozed: dict[tuple, datetime] = {}
        self._mentioned: dict[tuple, datetime] = {}
        self.delivered: set[tuple] = set()  # (day, contract_id, rule_id) of reminders actually sent
        self._last_rule: tuple[str, str] | None = None  # what "why?" refers to

    @staticmethod
    def _validate_anchors(profile: Profile, contracts: list[Contract], extra: set[str]) -> None:
        used = {w.anchor for c in contracts for w in [*(r.window for r in c.rules), *(p.window for p in c.phases)]}
        used |= {r.reopens for c in contracts for r in c.rules if r.reopens}
        missing = (used | extra) - profile.anchors.keys()
        if missing:
            raise ValueError(f"profile is missing anchors: {sorted(missing)}")

    @property
    def ctx(self) -> engine.Context:
        return engine.Context(self.profile, tuple(self.events), self.wait_for_observed)

    # --- inbound ----------------------------------------------------------------------------

    def log(self, action: str, at: datetime) -> list[Message]:
        """Record an action and send immediate feedback for every rule it affects."""
        if action not in self.actions:
            raise ValueError(f"unknown action '{action}'")
        self.events.append(Event(action, at))
        self.events.sort(key=lambda e: e.at)
        affected = [(rep, r) for rep in self.status(at) for r in rep.results
                    if action in r.rule.actions and r.start <= at < r.end]
        return self._send([self._feedback(rep, r, at) for rep, r in affected])

    def answer(self, text: str, now: datetime) -> list[Message]:
        """Handle a free-text message from the owner."""
        intent = intents.parse(text)
        if intent.kind == "log":
            return [m for action in intent.actions for m in self.log(action, now) or self._noted(action, now)]
        return self._reply(now, self._query(intent.kind, now))

    def _noted(self, action: str, now: datetime) -> list[Message]:
        return self._reply(now, f"Noted: {self.actions[action]}. Nothing to flag right now.")

    def _query(self, kind: str, now: datetime) -> str:
        if kind == "status":
            return narration.status_reply(self.status(now))
        if kind == "recommend":
            return narration.recommend_reply(self.recommend(now), now)
        if kind == "plan":
            return "\n".join(f"{at:%H:%M} {rule.title}" for at, _, rule in self.plan(self.ctx.logical_day(now)))
        if kind == "why" and self._last_rule:
            return self.explain(*self._last_rule, now)
        return "I track your cortisol rhythm and meal timing. Tell me what you did (\"had coffee\"), " \
               "or ask \"status\", \"what now\", \"plan\", \"why\"."

    def explain(self, contract_id: str, rule_id: str, now: datetime) -> str:
        return narration.why_reply(*self._lookup((contract_id, rule_id), now))

    def snooze(self, contract_id: str, rule_id: str, now: datetime, minutes: int = 15) -> str:
        until = now + timedelta(minutes=minutes)
        result, _ = self._lookup((contract_id, rule_id), now)
        if until >= result.end:
            return f"That's past the window (closes {result.end:%H:%M}), so I won't remind you again today."
        self._snoozed[(self.ctx.logical_day(now), contract_id, rule_id)] = until
        return f"OK, I'll remind you at {until:%H:%M}."

    # --- proactive --------------------------------------------------------------------------

    def tick(self, now: datetime) -> list[Message]:
        """Call periodically. Each rule gets one reminder per day, unless snoozed or just mentioned."""
        day = self.ctx.logical_day(now)
        reports = self.status(now)
        candidates = self._routines(day, reports, now)  # first, so the brief can cover reminders due now
        candidates += [m for rep in reports for m in engine.due_reminders(rep, self.ctx, now)]
        return self._send([m for m in candidates if self._claim(day, m, now)])

    def _claim(self, day: date, m: Message, now: datetime) -> bool:
        key, rule_key = (day, m.kind, m.contract_id, m.rule_id), (day, m.contract_id, m.rule_id)
        snoozed_until = self._snoozed.get(rule_key)
        if snoozed_until:
            if now < snoozed_until:
                return False
            del self._snoozed[rule_key]
        elif key in self._sent:
            return False
        elif now - self._mentioned.get(rule_key, datetime.min) < MENTION_COOLDOWN:
            self._sent.add(key)  # covered by a recent message: drop it, don't postpone it
            return False
        self._sent.add(key)
        self.delivered.add(rule_key)
        for mentioned in [rule_key, *((day, c, r) for c, r in m.meta.get("mentions", ()))]:
            self._mentioned[mentioned] = now
        return True

    def _routines(self, day: date, reports: list[ContractReport], now: datetime) -> list[Message]:
        ctx = self.ctx
        planned_wake = None if self.wait_for_observed else ctx.planned_anchor(self.wake_anchor, day)
        wake = ctx.observed(self.wake_anchor, day) or planned_wake
        grace = engine.OBSERVE_RADIUS if self.wait_for_observed else RECAP_GRACE  # don't close the day on a night owl
        sleep = ctx.observed(self.sleep_anchor, day) or ctx.planned_anchor(self.sleep_anchor, day) + grace
        messages = []
        if wake and wake <= now < wake + ROUTINE_WINDOW:
            has_history = ctx.first_day(day) < day
            yesterday = self.status(datetime.combine(day, self.profile.day_start) - timedelta(minutes=1)) \
                if has_history else []
            focus = narration.focus(yesterday)
            mentions = [(self._contract_of(focus.rule).id, focus.rule.id)] if focus else []
            messages.append(Message(wake, "brief", "", "brief",
                                    narration.morning_brief(yesterday, reports, self.wake_anchor, not self.compact_routines),
                                    {"mentions": mentions}))
        if sleep <= now < sleep + ROUTINE_WINDOW:
            messages.append(Message(sleep, "recap", "", "recap", narration.evening_recap(reports, not self.compact_routines)))
        return messages

    # --- read models ------------------------------------------------------------------------

    def status(self, now: datetime) -> list[ContractReport]:
        ctx = self.ctx
        return [engine.evaluate(c, ctx, now) for c in self.active]

    def recommend(self, now: datetime) -> list[Recommendation]:
        recs = [rec for report in self.status(now) for rec in engine.recommendations(report, now)]
        return sorted(recs, key=lambda r: (r.avoid, r.deadline, -r.rule.weight))

    def plan(self, day: date):
        return engine.day_plan(self.active, self.ctx, day)

    # --- corrections: the owner fixes what really happened --------------------------------

    def correct(self, contract_id: str, rule_id: str, happened: bool, at: datetime | None, now: datetime) -> None:
        """Rewrite today's events for a goal: did its action happen (and when), or not.
        For a limit (avoid) 'happened' means it was broken."""
        result, _ = self._lookup((contract_id, rule_id), now)
        actions, start, end = result.rule.actions, result.start, result.end
        at = at or min(result.anchor_at, now)
        latest = min(end - timedelta(minutes=1), now)
        if happened and not start <= at <= latest:
            raise ValueError(f"time must be between {start:%H:%M} and {latest:%H:%M}")
        self.events = [e for e in self.events if not (e.action in actions and start <= e.at < end)]
        if happened:
            self.events.append(Event(actions[0], at))
            self.events.sort(key=lambda e: e.at)

    def annotate(self, contract_id: str, rule_id: str, now: datetime,
                 duration: int | None = None, comment: str = "") -> None:
        self.notes[(self.ctx.logical_day(now), contract_id, rule_id)] = {"duration": duration, "comment": comment.strip()}

    def notes_for(self, day: date) -> dict[tuple[str, str], dict]:
        return {(c, r): n for (d, c, r), n in self.notes.items() if d == day}

    # --- control: agents (contracts) and their goals (rules) can be switched off -------------

    def set_enabled(self, contract_id: str, enabled: bool, rule_id: str | None = None) -> None:
        if not any(c.id == contract_id for c in self.contracts):
            raise ValueError(f"unknown agent '{contract_id}'")
        target, key = (self.disabled_rules, (contract_id, rule_id)) if rule_id else (self.disabled_contracts, contract_id)
        target.discard(key) if enabled else target.add(key)

    def is_enabled(self, contract_id: str, rule_id: str | None = None) -> bool:
        if contract_id in self.disabled_contracts:
            return False
        return rule_id is None or (contract_id, rule_id) not in self.disabled_rules

    @property
    def active(self) -> list[Contract]:
        """Enabled agents with only their enabled goals — everything downstream sees just these."""
        active = []
        for c in self.contracts:
            rules = tuple(r for r in c.rules if self.is_enabled(c.id, r.id))
            if rules:
                active.append(replace(c, rules=rules))
        return active

    def _contract_of(self, rule) -> Contract:
        return next(c for c in self.contracts if rule in c.rules)

    def _lookup(self, key: tuple[str, str], now: datetime) -> tuple[RuleResult, dict[str, str]]:
        contract_id, rule_id = key
        report = next(r for r in self.status(now) if r.contract.id == contract_id)
        return next(r for r in report.results if r.rule.id == rule_id), report.contract.sources

    # --- outbound ---------------------------------------------------------------------------

    def _feedback(self, report: ContractReport, result: RuleResult, at: datetime) -> Message:
        return Message(at, "feedback", report.contract.id, result.rule.id, narration.feedback(result),
                       {"status": result.status.value})

    def _reply(self, at: datetime, text: str) -> list[Message]:
        return self._send([Message(at, "reply", "", "", text)])

    def _send(self, messages: list[Message]) -> list[Message]:
        for m in messages:
            if m.rule_id and m.contract_id:
                self._last_rule = (m.contract_id, m.rule_id)
            self.notifier.send(m)
        return messages
