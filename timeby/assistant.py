"""The agent facade: ingests events, proactively nudges, reports state. Clients/data sources plug in via ports."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Protocol

from . import battery, context, engine, intents, narration, personal
from . import tone as tones
from .recovery import RecoveryPlan, build_recovery
from .model import Contract, ContractReport, Event, Message, Profile, Recommendation, Rule, RuleKind, RuleResult, Status

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
        self.disabled_rules = {(c.id, r.id) for c in contracts for r in c.rules if r.off_by_default}
        self.day_overrides: dict[tuple[str, str], tuple[str, ...]] = {}  # owner's weekdays per goal
        self.notes: dict[tuple[date, str, str], dict] = {}  # owner's duration/comment per goal per day
        self.late_starts: dict[date, RecoveryPlan] = {}       # recovery plan of a day that started late
        self.skipped: set[tuple[date, str, str]] = set()      # goals the recovery plan dropped (no reminders)
        self.agent_context: dict[str, str] = {}               # owner's extra context per agent (AI recommendations)
        self.agent_situation: dict[str, context.Situation] = {}  # owner's "right now" per agent (travelling …), expires
        self.battery_on = True                                 # the energy battery agent (derived, no goals of its own)
        self.battery_condition: context.Situation | None = None  # how the owner feels: well / tired / unwell, expires
        self.tone = tones.DEFAULT                              # voice of every message (facts never change)
        self.tracked_from: dict[date, datetime] | None = None  # live: when each day's tracking started
        self.planned: list[Event] = []  # owner's records for later today: counted only once their time comes
        self.plan_changes: dict[tuple[date, str], int | None] = {}  # owner's changes for a day: minutes moved / None = skipped
        self.actions = actions
        self.notifier = notifier
        self.wake_anchor = wake_anchor
        self.sleep_anchor = sleep_anchor
        self.wait_for_observed = wait_for_observed
        self.compact_routines = compact_routines  # on when the client already shows the day's items
        self.events: list[Event] = []
        self.sent: set[tuple] = set()  # (day, kind, contract_id, rule_id) already handled — persisted in live mode
        self._snoozed: dict[tuple, datetime] = {}
        self._mentioned: dict[tuple, datetime] = {}
        self.delivered: set[tuple] = set()  # (day, contract_id, rule_id) of reminders actually sent
        self._last_rule: tuple[str, str] | None = None  # what "why?" refers to
        self.busy: list[tuple[datetime, datetime]] = []  # meetings: reminders wait until they end

    @staticmethod
    def _validate_anchors(profile: Profile, contracts: list[Contract], extra: set[str]) -> None:
        used = {w.anchor for c in contracts for w in [*(r.window for r in c.rules), *(p.window for p in c.phases)]}
        used |= {r.reopens for c in contracts for r in c.rules if r.reopens}
        missing = (used | extra) - profile.anchors.keys()
        if missing:
            raise ValueError(f"profile is missing anchors: {sorted(missing)}")

    @property
    def ctx(self) -> engine.Context:
        return engine.Context(self.profile, tuple(self.events), self.wait_for_observed, self.tracked_from, self.plan_changes)

    # --- inbound ----------------------------------------------------------------------------

    def log(self, action: str, at: datetime, goal: str | None = None, duration: int | None = None,
            note: str = "") -> list[Message]:
        """Record an action and send immediate feedback for every rule it affects."""
        self._check_action(action)
        self.events.append(Event(action, at, goal, duration, note.strip()))
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

    def plan_record(self, action: str, at: datetime, goal: str | None = None, duration: int | None = None,
                    note: str = "") -> None:
        """The owner logs something for later today ("surf at 18:00"): a plan, not a fact, until its time comes."""
        self._check_action(action)
        self.planned.append(Event(action, at, goal, duration, note.strip()))
        self.planned.sort(key=lambda e: e.at)

    def _check_action(self, action: str) -> None:
        if action not in self.actions:
            raise ValueError(f"unknown action '{action}'")

    def _count_due_plans(self, now: datetime) -> None:
        """Planned records whose time has come count as done — the owner said so — and the owner is told, so a
        changed plan is one tap to delete."""
        due = [e for e in self.planned if e.at <= now]
        self.planned = [e for e in self.planned if e.at > now]
        for e in due:
            self.log(e.action, e.at, e.goal, e.duration, e.note)
            self._reply(now, f"Counted your planned {self.actions[e.action].lower()} at {e.at:%H:%M}. "
                             "If plans changed, delete it in the timeline.")

    def tick(self, now: datetime) -> list[Message]:
        """Call periodically. Each rule gets one reminder per day, unless snoozed or just mentioned."""
        day = self.ctx.logical_day(now)
        self._count_due_plans(now)
        self.start_tracking(now)
        reports = self.status(now)
        candidates = self._routines(day, reports, now)  # first, so the brief can cover reminders due now
        candidates += [m for rep in reports for m in engine.due_reminders(rep, self.ctx, now)]
        candidates = [m for m in candidates if not self._held(day, m, now)]
        return self._send([m for m in candidates if self._claim(day, m, now)])

    def set_busy(self, intervals: list[tuple[datetime, datetime]]) -> None:
        self.busy = sorted(intervals)

    def is_busy(self, now: datetime) -> bool:
        return any(start <= now < end for start, end in self.busy)

    def start_tracking(self, now: datetime) -> None:
        """Live mode: today is judged from the first moment the assistant is running (no-op elsewhere)."""
        if self.tracked_from is not None:
            self.tracked_from.setdefault(self.ctx.logical_day(now), now)

    def _held(self, day: date, m: Message, now: datetime) -> bool:
        """No reminders for goals the recovery plan dropped, none during a meeting (held, not claimed, so they
        go out once it ends if still valid), and none but the wake-up call while still asleep."""
        if m.kind not in ("nudge", "last_call"):
            return False
        if (day, m.contract_id, m.rule_id) in self.skipped or self.is_busy(now):
            return True
        return self.wait_for_observed and self._asleep(day, now) and self._rule(m.contract_id, m.rule_id).window.anchor != self.wake_anchor

    def _asleep(self, day: date, now: datetime) -> bool:
        """Past the planned wake-up with no sign of the owner yet. Any record today means they're up (it's the day's
        first record that tells), and after a few hours without one the plan stands anyway."""
        ctx = self.ctx
        planned = ctx.planned_anchor(self.wake_anchor, day)
        any_record = any(ctx.logical_day(e.at) == day and e.at <= now for e in self.events)
        return ctx.observed(self.wake_anchor, day) is None and not any_record and planned <= now < planned + engine.OBSERVE_RADIUS

    def _claim(self, day: date, m: Message, now: datetime) -> bool:
        key, rule_key = (day, m.kind, m.contract_id, m.rule_id), (day, m.contract_id, m.rule_id)
        snoozed_until = self._snoozed.get(rule_key)
        if snoozed_until:
            if now < snoozed_until:
                return False
            del self._snoozed[rule_key]
        elif key in self.sent:
            return False
        elif now - self._mentioned.get(rule_key, datetime.min) < MENTION_COOLDOWN:
            self.sent.add(key)  # covered by a recent message: drop it, don't postpone it
            return False
        self.sent.add(key)
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
            messages.append(self._morning_message(day, reports, wake, now))
        if sleep <= now < sleep + ROUTINE_WINDOW:
            messages.append(Message(sleep, "recap", "", "recap", narration.evening_recap(reports, not self.compact_routines)))
        return messages

    def _morning_message(self, day: date, reports: list[ContractReport], wake: datetime, now: datetime) -> Message:
        """The brief — or, after a late wake-up, one recovery plan instead of a cascade of misses."""
        usual = self.ctx.planned_anchor(self.wake_anchor, day)
        if self.wait_for_observed and wake - usual > timedelta(minutes=self._wake_tolerance()):
            plan = self.late_starts.get(day) or build_recovery(reports, wake, usual, now, self.wake_anchor)
            self.late_starts[day] = plan
            self.skipped |= {(day, cid, rid) for cid, rid in plan.skipped_ids}
            return Message(wake, "recovery", "", "recovery", narration.recovery_message(plan), {"mentions": plan.mentions})
        ctx = self.ctx
        yesterday = self.status(datetime.combine(day, self.profile.day_start) - timedelta(minutes=1)) \
            if ctx.first_day(day) < day else []
        focus = narration.focus(yesterday)
        mentions = [(self._contract_of(focus.rule).id, focus.rule.id)] if focus else []
        return Message(wake, "brief", "", "brief",
                       narration.morning_brief(yesterday, reports, self.wake_anchor, not self.compact_routines),
                       {"mentions": mentions})

    def _wake_tolerance(self) -> int:
        wake_goals = [r for c in self.active for r in c.rules
                      if r.kind is RuleKind.REGULARITY and r.window.anchor == self.wake_anchor]
        return wake_goals[0].tolerance if wake_goals else 30

    def _rule(self, contract_id: str, rule_id: str):
        return next(r for c in self.contracts if c.id == contract_id for r in c.rules if r.id == rule_id)

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

    def correct(self, contract_id: str, rule_id: str, happened: bool, at: datetime | None, now: datetime,
                action: str | None = None) -> None:
        """Rewrite today's events for a goal: did it happen (which of its activities, and when), or not.
        For a limit (avoid) 'happened' means it was broken."""
        result, _ = self._lookup((contract_id, rule_id), now)
        rule, day = result.rule, self.ctx.logical_day(now)
        action = action or result.hit_action or rule.actions[0]
        if action not in rule.actions:
            raise ValueError(f"{self.actions.get(action, action)} doesn't count for {rule.label}")
        earliest, latest = self.correctable_range(result, now)
        at = at or min(result.anchor_at, now)
        planning = happened and at > now and rule.kind is not RuleKind.AVOID and self.ctx.logical_day(at) == day
        if happened and not planning and not earliest <= at <= latest:
            raise ValueError(f"time must be between {earliest:%H:%M} and {latest:%H:%M}")
        self.events = [e for e in self.events if not self._belongs(e, rule, result, day)]
        self.planned = [e for e in self.planned if e.goal != rule.id]
        if planning:  # "I'll do it at 16:10": the goal stays open until then
            self.plan_record(action, at, rule.id)
        elif happened:  # a task's event is tied to its goal, so a late breakfast stays breakfast
            self.events.append(Event(action, at, None if rule.kind is RuleKind.AVOID else rule.id))
            self.events.sort(key=lambda e: e.at)

    # --- the owner reshapes today's plan and fixes their own records --------------------------

    def move_goal(self, contract_id: str, rule_id: str, to: datetime, now: datetime) -> None:
        """Move an upcoming goal to another time today (today only; the contract stays the same)."""
        result = self._upcoming(contract_id, rule_id, now)
        day = self.ctx.logical_day(now)
        if not now <= to < datetime.combine(day + timedelta(days=1), self.profile.day_start):
            raise ValueError(f"pick a time between {now:%H:%M} and the end of today")
        shown = engine.planned_start(result.rule, result.anchor_at)
        moved = self.plan_changes.get((day, rule_id)) or 0
        self.plan_changes[(day, rule_id)] = moved + round((to - shown).total_seconds() / 60)

    def skip_goal(self, contract_id: str, rule_id: str, now: datetime, day: date | None = None) -> None:
        """Take a goal off one day's plan — today (while still upcoming) or a day ahead: no reminder, no score."""
        today = self.ctx.logical_day(now)
        day = day or today
        if day < today:
            raise ValueError("that day is already over — edit its records instead")
        if day == today:
            self._upcoming(contract_id, rule_id, now)
        elif not self._planned_weekday(contract_id, rule_id, day):
            raise ValueError(f"'{rule_id}' isn't planned on {day:%A}")
        self.plan_changes[(day, rule_id)] = None

    def _planned_weekday(self, contract_id: str, rule_id: str, day: date) -> bool:
        """The goal is switched on and its weekdays include this day."""
        rules = (r for c in self.active if c.id == contract_id for r in c.rules if r.id == rule_id)
        return any(engine.applies(r, day) for r in rules)

    def restore_goal(self, rule_id: str, now: datetime, day: date | None = None) -> None:
        self.plan_changes.pop((day or self.ctx.logical_day(now), rule_id), None)

    def _upcoming(self, contract_id: str, rule_id: str, now: datetime) -> RuleResult:
        result, _ = self._lookup((contract_id, rule_id), now)
        if result.status not in (Status.PENDING, Status.WAITING):
            raise ValueError(f"{result.rule.label} is already settled today — edit it as a past item instead")
        return result

    def update_record(self, action: str, at: datetime, now: datetime, *, to: datetime, new_action: str,
                      duration: int | None, note: str, quality: str | None = None) -> None:
        """Change the owner's own record: its time (any time today — later = a plan that counts once its time
        comes), what it was, how long it took and the note."""
        event = self._record(action, at)
        self._check_action(new_action)
        if self.ctx.logical_day(to) != self.ctx.logical_day(now):
            raise ValueError("pick a time today")
        self.delete_record(action, at)
        changed = replace(event, action=new_action, at=to, duration=duration, note=note.strip(),
                          quality=context.check_quality(quality))
        target = self.planned if to > now else self.events
        target.append(changed)
        target.sort(key=lambda e: e.at)

    def delete_record(self, action: str, at: datetime) -> None:
        event = self._record(action, at)
        (self.planned if event in self.planned else self.events).remove(event)

    def _record(self, action: str, at: datetime) -> Event:
        event = next((e for e in [*self.events, *self.planned] if e.action == action and e.at == at), None)
        if event is None:
            raise ValueError("that record no longer exists")
        return event

    def correctable_range(self, result: RuleResult, now: datetime) -> tuple[datetime, datetime]:
        """Tasks can be logged any time today (late is still real); a limit only within its window."""
        if result.rule.kind is RuleKind.AVOID:
            return result.start, min(result.end - timedelta(minutes=1), now)
        day_start = datetime.combine(self.ctx.logical_day(now), self.profile.day_start)
        return day_start, now

    def _belongs(self, e: Event, rule, result: RuleResult, day: date) -> bool:
        if e.goal:
            return e.goal == rule.id and self.ctx.logical_day(e.at) == day
        return e.action in rule.actions and result.start <= e.at < result.end

    def annotate(self, contract_id: str, rule_id: str, now: datetime,
                 duration: int | None = None, comment: str = "", quality: str | None = None) -> None:
        self.notes[(self.ctx.logical_day(now), contract_id, rule_id)] = {
            "duration": duration, "comment": comment.strip(), "quality": context.check_quality(quality)}

    def rate_goal(self, contract_id: str, rule_id: str, now: datetime, quality: str | None, comment: str) -> None:
        """How it went ("done, but only soup at the airport"): keeps the duration already noted."""
        self._lookup((contract_id, rule_id), now)
        note = self.notes.get((self.ctx.logical_day(now), contract_id, rule_id), {})
        self.annotate(contract_id, rule_id, now, note.get("duration"), comment, quality)

    def rate_record(self, action: str, at: datetime, quality: str | None, note: str) -> None:
        event = self._record(action, at)
        target = self.planned if event in self.planned else self.events
        target[target.index(event)] = replace(event, quality=context.check_quality(quality), note=note.strip())

    def set_situation(self, contract_id: str, text: str, scope: str, now: datetime) -> None:
        self._check_agent(contract_id)
        self.agent_situation[contract_id] = context.situation(text, scope, self.ctx.logical_day(now))

    def set_condition(self, state: str, scope: str, now: datetime) -> None:
        """How the owner feels (well / tired / unwell) — lowers today's battery ceiling until it expires."""
        if state not in battery.CONDITIONS:
            raise ValueError(f"unknown condition '{state}'")
        self.battery_condition = None if state == "well" else context.situation(state, scope, self.ctx.logical_day(now))

    def situation_of(self, contract_id: str, now: datetime) -> context.Situation | None:
        s = self.agent_situation.get(contract_id)
        return s if s and s.active(self.ctx.logical_day(now)) else None

    def notes_for(self, day: date) -> dict[tuple[str, str], dict]:
        return {(c, r): n for (d, c, r), n in self.notes.items() if d == day}

    # --- control: agents (contracts) and their goals (rules) can be switched off -------------

    def _check_agent(self, contract_id: str) -> None:
        if not any(c.id == contract_id for c in self.contracts):
            raise ValueError(f"unknown agent '{contract_id}'")

    def set_enabled(self, contract_id: str, enabled: bool, rule_id: str | None = None) -> None:
        self._check_agent(contract_id)
        target, key = (self.disabled_rules, (contract_id, rule_id)) if rule_id else (self.disabled_contracts, contract_id)
        target.discard(key) if enabled else target.add(key)

    def is_enabled(self, contract_id: str, rule_id: str | None = None) -> bool:
        if contract_id in self.disabled_contracts:
            return False
        return rule_id is None or (contract_id, rule_id) not in self.disabled_rules

    # --- personal setup: the owner's own day and goal weekdays ------------------------------

    def set_profile(self, times: dict) -> tuple[str, ...]:
        """Move the day's anchors (wake, meals, lights out). Invalid times raise; returns warnings."""
        check = personal.check_day(times, self.profile.day_start)
        if check.errors:
            raise ValueError(" ".join(check.errors))
        self.profile = personal.with_times(self.profile, times)
        return check.warnings

    def profile_warnings(self) -> tuple[str, ...]:
        times = {name: self.profile.anchors[name].time for name in personal.ANCHORS}
        return personal.check_day(times, self.profile.day_start).warnings

    def set_days(self, contract_id: str, rule_id: str, days: list[str]) -> None:
        if not any(c.id == contract_id and r.id == rule_id for c in self.contracts for r in c.rules):
            raise ValueError(f"unknown goal '{contract_id}.{rule_id}'")
        unknown = set(days) - set(engine.WEEKDAYS)
        if unknown:
            raise ValueError(f"unknown weekdays: {sorted(unknown)}")
        if not days:
            raise ValueError("Pick at least one day, or switch the goal off.")
        self.day_overrides[(contract_id, rule_id)] = tuple(d for d in engine.WEEKDAYS if d in days)

    def customized(self, contract_id: str, rule: Rule) -> Rule:
        days = self.day_overrides.get((contract_id, rule.id))
        return replace(rule, days=days) if days else rule

    @property
    def active(self) -> list[Contract]:
        """Enabled agents with only their enabled goals, as the owner set them up — everything downstream sees just these."""
        active = []
        for c in self.contracts:
            rules = tuple(self.customized(c.id, r) for r in c.rules if self.is_enabled(c.id, r.id))
            if rules:
                active.append(replace(c, rules=rules))
        return active

    def _contract_of(self, rule) -> Contract:
        return next(c for c in self.contracts if any(r.id == rule.id for r in c.rules))  # by id: active rules are copies

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

    def set_tone(self, tone_id: str) -> None:
        if tone_id not in tones.TONES:
            raise ValueError(f"unknown tone '{tone_id}'")
        self.tone = tone_id

    def _send(self, messages: list[Message]) -> list[Message]:
        styled = [self._in_tone(m) for m in messages]
        for m in styled:
            if m.rule_id and m.contract_id:
                self._last_rule = (m.contract_id, m.rule_id)
            self.notifier.send(m)
        return styled

    def _in_tone(self, m: Message) -> Message:
        contract = next((c for c in self.contracts if c.id == m.contract_id), None)
        rule = next((r for r in contract.rules if r.id == m.rule_id), None) if contract else None
        text = tones.style(self.tone, m.kind, m.text, rule, contract.sources if contract else None, m.meta.get("status"))
        return m if text == m.text else replace(m, text=text)
