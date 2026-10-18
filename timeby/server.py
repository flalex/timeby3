"""Interactive simulator: a simulated clock + simulated sensors driving the real Assistant. Run: python -m timeby.server"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .assistant import Assistant
from .demo import PROFILE, seed_history
from .loader import load_actions, load_contracts
from .model import ContractReport, Message, RuleKind, RuleResult, Status
from .connectors import CONNECTORS
from .personal import DAY_ORDER, parse_times
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
                 live: bool = False):
        self.wait_for_observed = wait_for_observed
        self.compact_routines = compact_routines
        self.live = live  # real clock, no history, nothing scripted: the owner tests their own day
        self.profile = PROFILE  # the owner's setup survives resets and replays
        self.day_overrides: dict[tuple[str, str], tuple[str, ...]] = {}
        self.reset(today)

    def reset(self, today: date | None = None, scenario: str = DEFAULT) -> None:
        today = today or date.today()
        self.scenario = SCENARIOS["own" if self.live else scenario]
        self.outbox = Outbox()
        self.assistant = Assistant(self.profile, load_contracts(), load_actions(), self.outbox,
                                   wait_for_observed=self.wait_for_observed, compact_routines=self.compact_routines)
        self.assistant.day_overrides = dict(self.day_overrides)
        if self.live:
            self.now = datetime.now().replace(second=0, microsecond=0)
        else:
            seed_history(self.assistant, today, pattern=TYPICAL_WEEK)
            self.now = datetime.combine(today, time(6, 30))
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

    def log(self, action: str, source: str = "you") -> list[dict]:
        self.log_sources[(action, self.now)] = source
        self.assistant.log(action, self.now)
        return self._flush()

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
        self.assistant.set_profile(parse_times(anchors))
        self.profile = self.assistant.profile

    def set_days(self, contract_id: str, rule_id: str, days: list[str]) -> None:
        self.assistant.set_days(contract_id, rule_id, days)
        self.day_overrides = dict(self.assistant.day_overrides)

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
        return [self._wrap(m) for m in self.outbox.drain()]

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
            "timeline": self._timeline(reports, day),
            "now_card": self._now_card(reports, now),
            "today": build_today(reports, now, self.assistant.notes_for(day), plan_wake(ctx, now),
                                 datetime.combine(day, self.assistant.profile.day_start),
                                 self.assistant.late_starts.get(day),
                                 {rid for d, _, rid in self.assistant.skipped if d == day}),
            "scenario": self.scenario.id,
            "scenarios": [{"id": sc.id, "label": sc.label, "description": sc.description} for sc in SCENARIOS.values()],
            "agents": self._agents(),
            "profile": self._profile(),
            "connectors": self._connectors(),
        }

    def _agents(self) -> list[dict]:
        a = self.assistant
        return [{"id": c.id, "name": c.name, "domain": c.domain, "goal": c.goal, "enabled": a.is_enabled(c.id),
                 "goals": [{"id": r.id, "label": r.label, "kind": r.kind.value, "action": r.actions[0],
                            "days": list(a.customized(c.id, r).days), "weekly": bool(r.days), "topic": r.topic,
                            "enabled": (c.id, r.id) not in a.disabled_rules} for r in c.rules]}
                for c in a.contracts]

    def _profile(self) -> dict:
        anchors = self.assistant.profile.anchors
        return {"anchors": {name: f"{anchors[name].time:%H:%M}" for name in DAY_ORDER},
                "warnings": list(self.assistant.profile_warnings())}

    def _connectors(self) -> list[dict]:
        """Each connector with the goals it would automate (from the actions it reads)."""
        goals = [r for c in self.assistant.contracts for r in c.rules if r.kind is not RuleKind.AVOID]
        return [{"id": k.id, "name": k.name, "icon": k.icon, "providers": k.providers, "does": k.does,
                 "status": k.status, "automates": [r.label for r in goals if set(r.actions) & set(k.reads)]}
                for k in CONNECTORS]

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


SIMS = {"v1": Simulation(), "v2": Simulation(wait_for_observed=True),
        "v3": Simulation(wait_for_observed=True, compact_routines=True),
        "live": Simulation(wait_for_observed=True, compact_routines=True, live=True)}
LOCK = threading.Lock()


def handle(sim: Simulation, op: str, body: dict) -> dict:
    synced = sim.sync() if sim.live else []
    if op == "reset":
        sim.reset(scenario=body.get("scenario") or DEFAULT)
        messages = []
    elif op == "advance":
        messages = sim.advance(int(body.get("minutes", 1)), bool(body.get("stop_on_agent", True)))
    elif op == "log":
        messages = sim.log(body["action"])
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
    elif op == "sensors":
        sim.sensors_on = bool(body["on"])
        messages = []
    elif op == "state":
        messages = []
    else:
        raise KeyError(op)
    return {"messages": synced + messages, "state": sim.state(), "live": sim.live}


STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def static_asset(path: str) -> Path | None:
    """/static/<name>.js|.css served from timeby/web — flat names only, no directory traversal."""
    name = path.removeprefix("/static/")
    if name == path or "/" in name or Path(name).suffix not in STATIC_TYPES:
        return None
    asset = WEB / name
    return asset if asset.is_file() else None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        page = PAGES.get(path)
        if page:
            return self._send(200, (WEB / page).read_bytes(), "text/html; charset=utf-8")
        asset = static_asset(path)
        if asset:
            return self._send(200, asset.read_bytes(), STATIC_TYPES[asset.suffix])
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            with LOCK:
                parts = self.path.removeprefix("/api/").split("/", 1)
                version, op = (parts[0], parts[1]) if parts[0] in SIMS and len(parts) == 2 else ("v1", parts[0])
                result = handle(SIMS[version], op, body)
            self._send(200, json.dumps(result).encode(), "application/json")
        except (KeyError, ValueError, StopIteration) as e:
            self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")

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
