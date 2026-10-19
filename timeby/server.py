"""Interactive simulator: a simulated clock + simulated sensors driving the real Assistant. Run: python -m timeby.server"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path

from . import calendar_feed, calendar_ics
from .assistant import Assistant
from .demo import PROFILE, seed_history
from .loader import load_actions, load_contracts
from .model import ContractReport, Message, RuleKind, RuleResult, Status
from .connectors import CONNECTORS
from .ai import RECOMMENDERS, AiUnavailable, facts_for, use_key_source
from .keystore import KeyStore
from .personal import ANCHORS, LABELS, parse_times
from .storage import LiveStore
from .telegram import TelegramChannel, TelegramError
from .tone import TONES, style
from .week import build_week
from .scenarios import DEFAULT, SCENARIOS, TYPICAL_WEEK, YOU
from .sleep_plan import plan_wake
from .today import build_today

WEB = Path(__file__).parent / "web"
PAGES = {"/": "index.html", "/index.html": "index.html", "/v2": "v2.html", "/v3": "v3.html", "/live": "v3.html",
         "/compare": "compare.html"}
MAX_ADVANCE = 24 * 60


class Outbox:
    def __init__(self):
        self.messages: list[Message] = []

    def send(self, message: Message) -> None:
        self.messages.append(message)

    def drain(self) -> list[Message]:
        out, self.messages = self.messages, []
        return out


class Simulation:
    def __init__(self, wait_for_observed: bool = False, compact_routines: bool = False, today: date | None = None,
                 live: bool = False, store: LiveStore | None = None, weekday: int | None = None):
        self.wait_for_observed = wait_for_observed
        self.compact_routines = compact_routines
        self.live = live  # real clock, no history, nothing scripted: the owner tests their own day
        self.weekday = weekday  # demo: simulate this weekday of the current week (0 = Monday)
        self.store = store  # live mode: the owner's real data, one file per day
        self.telegram = TelegramChannel()  # messenger channel; survives resets
        self.profile = PROFILE  # the owner's setup survives resets and replays
        self.day_overrides: dict[tuple[str, str], tuple[str, ...]] = {}
        self.meetings: list[calendar_ics.CalEvent] = []  # today's busy times; survive replays of the day
        self.calendar_connected = False  # feed fetched or meetings loaded
        self.reset(today)

    def reset(self, today: date | None = None, scenario: str = DEFAULT) -> None:
        today = today or self._default_day()
        self.scenario = SCENARIOS["own" if self.live else scenario]
        self.outbox = Outbox()
        self.assistant = Assistant(self.profile, load_contracts(), load_actions(), self.outbox,
                                   wait_for_observed=self.wait_for_observed, compact_routines=self.compact_routines)
        self.assistant.day_overrides = dict(self.day_overrides)
        if self.live:
            self.now = datetime.now().replace(second=0, microsecond=0)
            self._restore()
        else:
            seed_history(self.assistant, today, pattern=TYPICAL_WEEK)
            self.now = datetime.combine(today, time(6, 30))
        self._apply_meetings()
        self.sensors_on = True
        self.log_sources: dict[tuple[str, datetime], str] = {}
        self._msg_id = 0
        self._contracts = {c.id: c for c in self.assistant.contracts}

    # --- commands ---------------------------------------------------------------------------

    def advance(self, minutes: int, stop_on_agent: bool) -> list[dict]:
        out = []
        for _ in range(max(0, min(minutes, MAX_ADVANCE))):
            self.now += timedelta(minutes=1)
            out += self._play_script()
            self.assistant.tick(self.now)
            out += self._flush()
            if stop_on_agent and any(m["kind"] != "sensor" for m in out):  # incl. reactions to sensor data
                break
        return out

    def _default_day(self) -> date:
        today = date.today()
        return today if self.weekday is None else today + timedelta(days=self.weekday - today.weekday())

    # --- live data --------------------------------------------------------------------------

    def _restore(self) -> None:
        self.assistant.tracked_from = {}  # live: only what the owner and the agent record; nothing judged before tracking
        settings = self.store.restore(self.assistant) if self.store else {}
        self.assistant.start_tracking(self.now)
        self.profile, self.day_overrides = self.assistant.profile, dict(self.assistant.day_overrides)
        if settings.get("telegram"):
            self.telegram = TelegramChannel(**settings["telegram"])

    def persist(self) -> None:
        if self.live and self.store:
            self.store.save(self.assistant, self.assistant.ctx.logical_day(self.now), {"telegram": self.telegram.saved()})

    def set_context(self, contract_id: str, text: str) -> None:
        if contract_id not in self._contracts:
            raise ValueError(f"unknown agent '{contract_id}'")
        self.assistant.agent_context[contract_id] = text.strip()

    def recommendation_input(self, contract_id: str):
        """Everything one agent's AI recommendation needs, captured under the lock (the Claude call runs outside it)."""
        if not self.live:
            raise ValueError("AI recommendations are available in live mode only.")
        if contract_id not in RECOMMENDERS:
            raise ValueError(f"no recommender for agent '{contract_id}'")
        report = next((r for r in self.assistant.status(self.now) if r.contract.id == contract_id), None)
        if not report:
            raise ValueError("Switch this agent on to get recommendations.")
        voice = TONES[self.assistant.tone].ai_voice
        return RECOMMENDERS[contract_id], facts_for(report, self.now), self.assistant.agent_context.get(contract_id, ""), voice

    def jump(self, hhmm: str) -> list[dict]:
        """Move the simulated clock to HH:MM today; going back replays the day from the start."""
        target = self._at_today(hhmm)
        if target <= self.now:
            self.reset(self.assistant.ctx.logical_day(self.now), self.scenario.id)
            target = self._at_today(hhmm)
            self.now = min(self.now, target)  # allow starting the day earlier than 06:30
        return self.advance(int((target - self.now).total_seconds() // 60), stop_on_agent=False)

    def sync(self) -> list[dict]:
        """Live mode: catch the clock up with real time."""
        minutes = int((datetime.now() - self.now).total_seconds() // 60)
        return self.advance(minutes, stop_on_agent=False) if minutes > 0 else []

    def log(self, action: str, source: str = "you", at: datetime | None = None) -> list[dict]:
        """Record an action now, or earlier today ("I had it at…")."""
        at = at or self.now
        if not (at <= self.now and self.assistant.ctx.logical_day(at) == self.assistant.ctx.logical_day(self.now)):
            raise ValueError(f"pick a time earlier today, up to {self.now:%H:%M}")
        self.log_sources[(action, at)] = source
        self.assistant.log(action, at)
        return self._flush()

    def change_plan(self, body: dict) -> None:
        """Owner reshapes today: move an upcoming goal, take it off today, or put it back."""
        cid, rid, action = body["contract_id"], body["rule_id"], body["action"]
        if action == "move":
            self.assistant.move_goal(cid, rid, self._at_today(body["at"]), self.now)
        elif action == "skip":
            self.assistant.skip_goal(cid, rid, self.now)
        elif action == "restore":
            self.assistant.restore_goal(rid, self.now)
        else:
            raise KeyError(action)

    def change_record(self, body: dict) -> None:
        """Owner fixes their own record: move it to another time today, or delete it."""
        action, at = body["record"]["action"], self._at_today(body["record"]["at"])
        if body["action"] == "move":
            to = self._at_today(body["to"])
            self.assistant.move_record(action, at, to, self.now)
            self.log_sources[(action, to)] = self.log_sources.pop((action, at), "you")
        elif body["action"] == "delete":
            self.assistant.delete_record(action, at)
        else:
            raise KeyError(body["action"])

    def say(self, text: str) -> list[dict]:
        self.assistant.answer(text, self.now)
        return [self._wrap(Message(self.now, "user", "", "", text)), *self._flush()]

    def reply(self, op: str, contract_id: str, rule_id: str) -> list[dict]:
        rule = next(r for r in self._contracts[contract_id].rules if r.id == rule_id)
        if op == "done":
            return self.log(rule.actions[0])
        if op == "snooze":
            text = self.assistant.snooze(contract_id, rule_id, self.now, 15)
            return [self._wrap(Message(self.now, "reply", "", "", text))]
        if op == "why":
            return [self._wrap(Message(self.now, "reply", "", "", self.assistant.explain(contract_id, rule_id, self.now)))]
        return [self._wrap(Message(self.now, "reply", "", "", "👍"))]

    def edit(self, body: dict) -> None:
        """Owner corrects a past goal: what happened, when, how long, and a comment."""
        cid, rid = body["contract_id"], body["rule_id"]
        at = self._at_today(body["at"]) if body.get("at") else None
        self.assistant.correct(cid, rid, bool(body["happened"]), at, self.now)
        if at and body["happened"]:
            self.log_sources[(next(r for r in self._contracts[cid].rules if r.id == rid).actions[0], at)] = "you (edited)"
        duration = int(body["duration"]) if str(body.get("duration") or "").isdigit() else None
        self.assistant.annotate(cid, rid, self.now, duration, body.get("comment", ""))

    def set_profile(self, anchors: dict[str, str]) -> None:
        """New times for some or all anchors; the rest stay as they are."""
        current = {name: f"{a.time:%H:%M}" for name, a in self.assistant.profile.anchors.items()}
        self.assistant.set_profile(parse_times(current | anchors))
        self.profile = self.assistant.profile

    def set_days(self, contract_id: str, rule_id: str, days: list[str]) -> None:
        self.assistant.set_days(contract_id, rule_id, days)
        self.day_overrides = dict(self.assistant.day_overrides)

    # --- calendar connector ---------------------------------------------------------------

    def plan_feed(self) -> str:
        """Today's plan + tomorrow's wake-up as an iCalendar feed."""
        self.calendar_connected = True
        ctx = self.assistant.ctx
        return calendar_feed.feed(self.assistant.status(self.now), plan_wake(ctx, self.now), datetime.now(timezone.utc))

    def load_meetings(self, body: dict) -> None:
        """Busy times for today from pasted/uploaded .ics text, the bundled sample, or cleared."""
        day = self.assistant.ctx.logical_day(self.now)
        if body.get("clear"):
            text = ""
        elif body.get("sample"):
            text = calendar_feed.sample_meetings(day, datetime.now(timezone.utc))
        else:
            text = body["ics"]
        self.meetings = [m for m in calendar_ics.parse(text) if self.assistant.ctx.logical_day(m.start) == day]
        self._apply_meetings()
        self.calendar_connected = bool(self.meetings)

    def _apply_meetings(self) -> None:
        self.assistant.set_busy([(m.start, m.end) for m in self.meetings])

    def _at_today(self, hhmm: str) -> datetime:
        ctx = self.assistant.ctx
        at = datetime.combine(ctx.logical_day(self.now), time.fromisoformat(hhmm))
        return at + timedelta(days=1) if at.time() < self.assistant.profile.day_start else at

    # --- scenario script: what the owner does and what sensors report -----------------------

    def _play_script(self) -> list[dict]:
        out = []
        for step in self.scenario.script:
            if self.now.time() != step.at or not self._should_play(step):
                continue
            label = self.assistant.actions[step.action]
            text = f"👤 You logged: {label}" if step.source == YOU else f"📡 {step.source}: {label}"
            out.append(self._wrap(Message(self.now, "sensor", "", "", text)))
            out += self.log(step.action, step.source)
        return out

    def _should_play(self, step) -> bool:
        """The owner's own actions always happen; sensor readings need sensors on and no manual log already."""
        if step.source == YOU:
            return True
        return self.sensors_on and not self._recently_logged(step.action)

    def _recently_logged(self, action: str) -> bool:
        return any(e.action == action and timedelta(0) <= self.now - e.at <= timedelta(hours=3)
                   for e in self.assistant.events)

    # --- serialization ----------------------------------------------------------------------

    def _flush(self) -> list[dict]:
        messages = self.outbox.drain()
        if self.live:  # only the real day reaches the phone; a fast-forwarded demo day would spam it
            for m in messages:
                self.telegram.forward("alert" if m.meta.get("status") in ("violated", "missed") else m.kind, m.text)
        return [self._wrap(m) for m in messages]

    def _wrap(self, m: Message) -> dict:
        self._msg_id += 1
        contract = self._contracts.get(m.contract_id)
        return {
            "id": self._msg_id, "at": f"{m.at:%H:%M}", "kind": m.kind, "text": m.text,
            "contract": contract.name if contract else None, "contract_id": m.contract_id, "rule_id": m.rule_id,
            "status": m.meta.get("status"), "replies": self._quick_replies(m),
        }

    def _quick_replies(self, m: Message) -> list[dict]:
        contract = self._contracts.get(m.contract_id)
        if not contract:
            return []
        rule = next(r for r in contract.rules if r.id == m.rule_id)
        why = {"label": "Why?", "op": "why"}
        if m.kind == "feedback":
            return [why] if m.meta.get("status") in ("violated", "missed") else []
        if rule.kind is RuleKind.AVOID:
            return [{"label": "👍 Got it", "op": "ack"}, why]
        return [{"label": "✓ Done", "op": "done"}, {"label": "⏰ 15 min", "op": "snooze"}, why]

    def state(self) -> dict:
        now, ctx = self.now, self.assistant.ctx
        day = ctx.logical_day(now)
        reports = self.assistant.status(now)
        resolved = {(rep.contract.id, r.rule.id) for rep in reports for r in rep.results if r.status is not Status.PENDING}
        return {
            "now": now.isoformat(), "clock": f"{now:%H:%M}", "day": f"{now:%a %d %b}",
            "sensors": self.sensors_on,
            "contracts": [self._report(r) for r in reports],
            "recommendations": [
                {"avoid": r.avoid, "advice": r.rule.advice, "deadline": f"{r.deadline:%H:%M}",
                 "strength": r.rule.strength, "contract": r.contract.name}
                for r in self.assistant.recommend(now)
            ],
            "plan": [{"at": f"{at:%H:%M}", "title": rule.title, "contract": c.name, "past": at <= now,
                      "resolved": (c.id, rule.id) in resolved}
                     for at, c, rule in self.assistant.plan(day)],
            "events": [{"at": f"{e.at:%H:%M}", "label": self.assistant.actions[e.action],
                        "source": self.log_sources.get((e.action, e.at), "history")}
                       for e in self.assistant.events if ctx.logical_day(e.at) == day],
            "actions": [{"id": k, "label": v} for k, v in self.assistant.actions.items()],
            "records": self._records(reports, day),
            "plan_changes": {rid: minutes for (d, rid), minutes in self.assistant.plan_changes.items() if d == day},
            "timeline": self._timeline(reports, day),
            "now_card": self._now_card(reports, now),
            "today": build_today(reports, now, self.assistant.notes_for(day), plan_wake(ctx, now),
                                 datetime.combine(day, self.assistant.profile.day_start),
                                 self.assistant.late_starts.get(day),
                                 {rid for d, _, rid in self.assistant.skipped if d == day},
                                 (self.assistant.tracked_from or {}).get(day)),
            "scenario": self.scenario.id,
            "scenarios": [{"id": sc.id, "label": sc.label, "description": sc.description} for sc in SCENARIOS.values()],
            "agents": self._agents(),
            "assistant": self._assistant_settings(),
            "ai": {**KEYSTORE.public(), "sdk": find_spec("anthropic") is not None, "live_only": True},
            "week": build_week(self.assistant, now),
            "profile": self._profile(),
            "connectors": self._connectors(),
            "busy": [self._busy_block(m) for m in sorted(self.meetings, key=lambda m: m.start)],
        }

    def _agents(self) -> list[dict]:
        a = self.assistant
        return [{"id": c.id, "name": c.name, "domain": c.domain, "goal": c.goal, "enabled": a.is_enabled(c.id),
                 "goals": [{"id": r.id, "label": r.label, "kind": r.kind.value, "action": r.actions[0],
                            "days": list(a.customized(c.id, r).days), "weekly": bool(r.days), "topic": r.topic,
                            "enabled": (c.id, r.id) not in a.disabled_rules} for r in c.rules],
                 "anchors": self._agent_anchors(c), "context_hint": c.context_hint,
                 "context": a.agent_context.get(c.id, "")}
                for c in a.contracts]

    def _records(self, reports: list[ContractReport], day: date) -> list[dict]:
        """Today's records, flagged when a goal row already shows them (so the timeline never shows them twice)."""
        shown = {(a, r.hit_at) for rep in reports for r in rep.results if r.hit_at for a in r.rule.actions}
        ctx = self.assistant.ctx
        return [{"action": e.action, "label": self.assistant.actions[e.action], "at": f"{e.at:%H:%M}",
                 "sort": e.at.isoformat(), "source": self.log_sources.get((e.action, e.at), "you" if self.live else "history"),
                 "on_goal": (e.action, e.at) in shown}
                for e in self.assistant.events if ctx.logical_day(e.at) == day]

    def _assistant_settings(self) -> dict:
        """Current tone plus a preview of the same reminder in every tone."""
        sample_rule = next(r for c in self.assistant.contracts for r in c.rules if r.id == "morning_light")
        sources = next(c.sources for c in self.assistant.contracts if sample_rule in c.rules)
        return {"tone": self.assistant.tone,
                "tones": [{"id": t.id, "label": t.label, "blurb": t.blurb,
                           "sample": style(t.id, "nudge", sample_rule.nudge.text, sample_rule, sources)}
                          for t in TONES.values()]}

    def _agent_anchors(self, contract) -> list[dict]:
        """The day times this agent plans around; a time shared with other agents is edited in one place."""
        used = lambda c: {r.window.anchor for r in c.rules} | {r.reopens for r in c.rules if r.reopens}
        anchors = self.assistant.profile.anchors
        return [{"id": name, "label": LABELS[name], "time": f"{anchors[name].time:%H:%M}",
                 "shared": [c.name for c in self.assistant.contracts if c.id != contract.id and name in used(c)]}
                for name in ANCHORS if name in used(contract)]

    def _profile(self) -> dict:
        anchors = self.assistant.profile.anchors
        return {"anchors": {name: f"{anchors[name].time:%H:%M}" for name in ANCHORS},
                "warnings": list(self.assistant.profile_warnings())}

    def _connectors(self) -> list[dict]:
        """Each connector with the goals it would automate (from the actions it reads)."""
        goals = [r for c in self.assistant.contracts for r in c.rules if r.kind is not RuleKind.AVOID]
        return [{"id": k.id, "name": k.name, "icon": k.icon, "providers": k.providers, "does": k.does, "group": k.group,
                 "status": self._connector_status(k), "automates": [r.label for r in goals if set(r.actions) & set(k.reads)],
                 **({"telegram": self.telegram.public()} if k.id == "telegram" else {})}
                for k in CONNECTORS]

    def _busy_block(self, m: calendar_ics.CalEvent) -> dict:
        phase = "past" if m.end <= self.now else "now" if m.start <= self.now else "later"
        return {"start": f"{m.start:%H:%M}", "end": f"{m.end:%H:%M}", "summary": m.summary,
                "sort": m.start.isoformat(), "phase": phase}

    def _connector_status(self, k) -> str:
        if k.id == "calendar" and self.calendar_connected or k.id == "telegram" and self.telegram.ready:
            return "connected"
        return k.status

    # --- v2 read models ---------------------------------------------------------------------

    def _timeline(self, reports: list[ContractReport], day: date) -> list[dict]:
        """One row per rule, ordered by when the owner should act, merged with today's actual events."""
        rows = [self._rule_row(rep, r) for rep in reports for r in rep.results]
        rows += [{"type": "event", "sort": (e.at.isoformat(), 0), "at": f"{e.at:%H:%M}",
                  "label": self.assistant.actions[e.action],
                  "source": self.log_sources.get((e.action, e.at), "history")}
                 for e in self.assistant.events if self.assistant.ctx.logical_day(e.at) == day]
        return sorted(rows, key=lambda row: row["sort"])

    def _rule_row(self, rep: ContractReport, r: RuleResult) -> dict:
        rule, waiting = r.rule, r.status is Status.WAITING
        planned = r.anchor_at if rule.kind is RuleKind.REGULARITY else r.start
        acts_at = min(planned, r.hit_at) if r.hit_at else planned  # done early → sits where it happened
        when = f"{r.anchor_at:%H:%M}" if rule.kind is RuleKind.REGULARITY else f"{r.start:%H:%M}–{r.end:%H:%M}"
        return {
            "type": "rule", "sort": (acts_at.isoformat(), 1), "contract": rep.contract.name,
            "contract_id": rep.contract.id, "rule_id": rule.id, "title": rule.title, "kind": rule.kind.value,
            "status": "late" if r.status is Status.PENDING and self.now > r.deadline else r.status.value,
            "when": ("~" if r.estimated else "") + when,
            "waiting_for": rule.window.anchor if waiting else None,
            "reminder": self._reminder_label(rep, r, waiting),
            "due": None if rule.kind is RuleKind.AVOID else self._time_label(r, r.deadline, waiting),
            "hit_at": f"{r.hit_at:%H:%M}" if r.hit_at else None,
        }

    def _reminder_label(self, rep: ContractReport, r: RuleResult, waiting: bool) -> dict | None:
        if not r.reminder:
            return None
        day = self.assistant.ctx.logical_day(self.now)
        if (day, rep.contract.id, r.rule.id) in self.assistant.delivered:
            state = "sent"
        elif r.status not in (Status.PENDING, Status.WAITING):
            state = "not needed"
        elif r.reminder.at <= self.now:
            state = "skipped"  # covered by a recent message (e.g. the brief) or snoozed
        else:
            state = "scheduled"
        kind = "reminder" if r.reminder.kind == "nudge" else "last call"
        return {"kind": kind, "at": self._time_label(r, r.reminder.at, waiting), "state": state}

    @staticmethod
    def _time_label(r: RuleResult, at: datetime, waiting: bool) -> str:
        """Absolute time once the anchor is observed; relative ('wake +10 min') while waiting for it."""
        if not waiting:
            return f"{at:%H:%M}"
        minutes = round((at - r.anchor_at).total_seconds() / 60)
        return f"{r.rule.window.anchor} {'+' if minutes >= 0 else '−'}{abs(minutes)} min"

    def _now_card(self, reports: list[ContractReport], now: datetime) -> dict:
        """What the owner sees pinned on their phone: rules the agent has raised and that are still open."""
        raised = sorted(
            ((rep, r) for rep in reports for r in rep.results
             if r.status is Status.PENDING and r.rule.kind is not RuleKind.AVOID
             and r.reminder and r.reminder.at <= now < r.end),
            key=lambda item: (item[1].deadline, -item[1].rule.weight),
        )
        avoid = [{"title": r.rule.title, "until": f"{r.end:%H:%M}", "action": r.rule.actions[0]}
                 for rep in reports for r in rep.results
                 if r.status is Status.PENDING and r.rule.kind is RuleKind.AVOID and r.start <= now < r.end]
        items = [{"contract_id": rep.contract.id, "rule_id": r.rule.id, "title": r.rule.title,
                  "advice": r.rule.advice, "due": f"{r.deadline:%H:%M}", "action": r.rule.actions[0],
                  "minutes_left": round((r.deadline - now).total_seconds() / 60)} for rep, r in raised]
        return {"main": items[0] if items else None, "also": items[1:], "avoid": avoid}

    @staticmethod
    def _report(rep: ContractReport) -> dict:
        return {
            "id": rep.contract.id, "name": rep.contract.name, "goal": rep.contract.goal, "score": rep.score,
            "phase": rep.phase.goal if rep.phase else None,
            "rules": [{"id": r.rule.id, "title": r.rule.title, "kind": r.rule.kind.value, "status": r.status.value,
                       "detail": r.detail, "strength": r.rule.strength, "why": r.rule.why,
                       "sources": [rep.contract.sources[s] for s in r.rule.sources],
                       "window": f"{r.start:%H:%M}–{r.end:%H:%M}"} for r in rep.results],
        }


KEYSTORE = KeyStore()  # the owner's Claude API key: macOS Keychain (or a private file), shared by all modes
use_key_source(KEYSTORE.get)

SIMS = {"v1": Simulation(), "v2": Simulation(wait_for_observed=True),
        "v3": Simulation(wait_for_observed=True, compact_routines=True, weekday=2),  # Wednesday: a week with a past
        "live": Simulation(wait_for_observed=True, compact_routines=True, live=True, store=LiveStore())}
LOCK = threading.Lock()


def handle(sim: Simulation, op: str, body: dict) -> dict:
    synced = sim.sync() if sim.live else []
    if op == "reset":
        sim.reset(scenario=body.get("scenario") or DEFAULT)
        messages = []
    elif op == "advance":
        messages = sim.advance(int(body.get("minutes", 1)), bool(body.get("stop_on_agent", True)))
    elif op == "log":
        messages = sim.log(body["action"], at=sim._at_today(body["at"]) if body.get("at") else None)
    elif op == "plan":
        sim.change_plan(body)
        messages = []
    elif op == "record":
        sim.change_record(body)
        messages = []
    elif op == "say":
        messages = sim.say(body["text"])
    elif op == "reply":
        messages = sim.reply(body["op"], body["contract_id"], body["rule_id"])
    elif op == "jump":
        messages = sim.jump(body["at"])
    elif op == "sync":
        messages = []
    elif op == "edit":
        sim.edit(body)
        messages = []
    elif op == "toggle":
        sim.assistant.set_enabled(body["contract_id"], bool(body["on"]), body.get("rule_id"))
        messages = []
    elif op == "profile":
        sim.set_profile(body.get("anchors") or {})
        messages = []
    elif op == "days":
        sim.set_days(body["contract_id"], body["rule_id"], list(body.get("days") or []))
        messages = []
    elif op == "busy":
        sim.load_meetings(body)
        messages = []
    elif op == "tone":
        sim.assistant.set_tone(body["tone"])
        messages = []
    elif op == "ai_key":
        KEYSTORE.save(body.get("key", "")) if body.get("action") == "save" else KEYSTORE.remove()
        messages = []
    elif op == "context":
        sim.set_context(body["contract_id"], body.get("text", ""))
        messages = []
    elif op == "sensors":
        sim.sensors_on = bool(body["on"])
        messages = []
    elif op == "state":
        messages = []
    else:
        raise KeyError(op)
    sim.persist()
    return {"messages": synced + messages, "state": sim.state(), "live": sim.live}


CALENDAR_FEEDS = {"/calendar.ics": "v3", "/calendar/live.ics": "live"}
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def static_asset(path: str) -> Path | None:
    """/static/<name>.js|.css served from timeby/web — flat names only, no directory traversal."""
    name = path.removeprefix("/static/")
    if name == path or "/" in name or Path(name).suffix not in STATIC_TYPES:
        return None
    asset = WEB / name
    return asset if asset.is_file() else None


def recommend(sim: Simulation, contract_id: str) -> dict:
    """One agent's AI recommendation; the slow Claude call runs outside the lock so the app stays responsive."""
    with LOCK:
        recommender, facts, context, voice = sim.recommendation_input(contract_id)
    return {"contract_id": contract_id, "recommendation": recommender(facts, context, voice)}


TELEGRAM_TEST = "✅ timeby is connected. Your assistant's messages will arrive here."


def telegram_op(sim: Simulation, body: dict) -> dict:
    """Set up the Telegram channel. Network calls run outside the lock; the result is saved in live mode."""
    channel, action = sim.telegram, body["action"]
    if action == "connect":
        channel.connect(body.get("token", ""))
    elif action == "find_chat":
        channel.find_chat()
    elif action == "test":
        channel.send_now(TELEGRAM_TEST)
    elif action == "delivery":
        channel.set_delivery(body["delivery"])
    elif action == "disconnect":
        sim.telegram = TelegramChannel()
    else:
        raise KeyError(action)
    with LOCK:
        sim.persist()
        return {"messages": [], "state": sim.state(), "live": sim.live}


def calendar_text(sim: Simulation) -> str:
    with LOCK:
        if sim.live:
            sim.sync()
        return sim.plan_feed()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        page = PAGES.get(path)
        if page:
            return self._send(200, (WEB / page).read_bytes(), "text/html; charset=utf-8")
        feed = CALENDAR_FEEDS.get(path)
        if feed:
            return self._send(200, calendar_text(SIMS[feed]).encode(), "text/calendar; charset=utf-8")
        asset = static_asset(path)
        if asset:
            return self._send(200, asset.read_bytes(), STATIC_TYPES[asset.suffix])
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        parts = self.path.removeprefix("/api/").split("/", 1)
        version, op = (parts[0], parts[1]) if parts[0] in SIMS and len(parts) == 2 else ("v1", parts[0])
        try:
            if op == "recommend":
                return self._json(200, recommend(SIMS[version], body["contract_id"]))
            if op == "telegram":
                return self._json(200, telegram_op(SIMS[version], body))
            with LOCK:
                result = handle(SIMS[version], op, body)
            self._json(200, result)
        except (AiUnavailable, TelegramError) as e:
            self._json(503, {"error": str(e)})
        except (KeyError, ValueError, StopIteration) as e:
            self._json(400, {"error": str(e)})

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def main(port: int = 8765) -> None:
    print(f"timeby simulator → http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
