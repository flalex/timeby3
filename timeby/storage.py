"""Live-mode persistence: one human-readable JSON file per logical day plus `settings.json`.

<data dir>/2026-10-19.json  — that day's events, notes and which reminders were already sent
<data dir>/settings.json    — the owner's times, weekday overrides, switched-off agents/goals, AI context
<data dir>/tasks.json       — the backlog (books, courses …) with every progress entry
The data dir is `TIMEBY_DATA_DIR`, or `data/live` in the repo (git-ignored).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from . import tasks
from .assistant import Assistant
from .context import Situation
from .tasks import Backlog
from .model import Event
from .personal import ANCHORS, parse_times

DEFAULT_DIR = Path(os.environ.get("TIMEBY_DATA_DIR") or Path(__file__).resolve().parent.parent / "data" / "live")
SETTINGS = "settings.json"
TASKS = "tasks.json"


def _key(contract_id: str, rule_id: str) -> str:
    return f"{contract_id}/{rule_id}"


def _situation_json(s: Situation) -> dict:
    return {"text": s.text, "scope": s.scope, "until": s.until.isoformat() if s.until else None}


def _situation(v: dict) -> Situation:
    return Situation(v["text"], v["scope"], date.fromisoformat(v["until"]) if v.get("until") else None)


def _unkey(key: str) -> tuple[str, str]:
    contract_id, rule_id = key.split("/", 1)
    return contract_id, rule_id


class LiveStore:
    def __init__(self, root: Path = DEFAULT_DIR):
        self.root = Path(root)

    # --- save -----------------------------------------------------------------------------

    def save(self, assistant: Assistant, day: date, extra: dict | None = None) -> None:
        """`extra`: settings that live outside the assistant (e.g. messenger channels)."""
        self._write(SETTINGS, self._settings(assistant) | (extra or {}))
        for d in [day, *self._days_ahead(assistant, day)]:
            self._write(f"{d.isoformat()}.json", self._day(assistant, d))

    def _days_ahead(self, a: Assistant, day: date) -> set[date]:
        """Days after today the owner already changed (e.g. padel skipped while travelling), or changed before."""
        changed = {d for d, _ in a.plan_changes if d > day}
        saved = {date.fromisoformat(p.stem) for p in self.root.glob("????-??-??.json") if date.fromisoformat(p.stem) > day}
        return changed | saved

    def save_tasks(self, backlog: Backlog) -> None:
        self._write(TASKS, {"tasks": tasks.to_json(backlog)})

    def load_tasks(self) -> Backlog:
        path = self.root / TASKS
        return tasks.from_json(json.loads(path.read_text())["tasks"]) if path.is_file() else Backlog()

    def _settings(self, a: Assistant) -> dict:
        return {
            "profile": {name: f"{a.profile.anchors[name].time:%H:%M}" for name in ANCHORS},
            "day_overrides": {_key(*k): list(v) for k, v in a.day_overrides.items()},
            "disabled_agents": sorted(a.disabled_contracts),
            "disabled_goals": sorted(_key(*k) for k in a.disabled_rules),
            "agent_context": a.agent_context,
            "agent_situation": {cid: _situation_json(s) for cid, s in a.agent_situation.items()},
            "battery_on": a.battery_on,
            "battery_condition": _situation_json(a.battery_condition) if a.battery_condition else None,
            "tone": a.tone,
        }

    def _day(self, a: Assistant, day: date) -> dict:
        on_day = lambda keys: sorted(list(k[1:]) for k in keys if k[0] == day)
        records = lambda events: [{"action": e.action, "at": e.at.isoformat(timespec="minutes"), "goal": e.goal,
                                   **({"duration": e.duration} if e.duration else {}), **({"note": e.note} if e.note else {}),
                                   **({"quality": e.quality} if e.quality else {})}
                                  for e in events if a.ctx.logical_day(e.at) == day]
        return {
            "day": day.isoformat(),
            "events": records(a.events),
            "planned": records(a.planned),
            "notes": {_key(*k): note for k, note in a.notes_for(day).items()},
            "sent": on_day(a.sent),
            "delivered": on_day(a.delivered),
            "skipped": on_day(a.skipped),
            "tracking_from": a.tracked_from[day].isoformat(timespec="minutes") if day in (a.tracked_from or {}) else None,
            "plan_changes": {rid: minutes for (d, rid), minutes in a.plan_changes.items() if d == day},
        }

    def _write(self, name: str, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{name}.tmp"
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        tmp.replace(self.root / name)  # atomic: a crash never leaves a half-written day

    # --- load -----------------------------------------------------------------------------

    def restore(self, assistant: Assistant) -> dict:
        """Put saved settings and every saved day back into a fresh assistant; returns the raw settings."""
        settings = json.loads((self.root / SETTINGS).read_text()) if (self.root / SETTINGS).is_file() else {}
        if settings:
            self._restore_settings(assistant, settings)
        for path in sorted(self.root.glob("????-??-??.json")):
            self._restore_day(assistant, json.loads(path.read_text()))
        assistant.events.sort(key=lambda e: e.at)
        assistant.planned.sort(key=lambda e: e.at)
        return settings

    @staticmethod
    def _restore_settings(a: Assistant, s: dict) -> None:
        if s.get("profile"):  # saved before newer times existed (e.g. work hours): keep the defaults for those
            current = {name: f"{a.profile.anchors[name].time:%H:%M}" for name in ANCHORS}
            a.set_profile(parse_times(current | s["profile"]))
        a.day_overrides = {_unkey(k): tuple(v) for k, v in s.get("day_overrides", {}).items()}
        a.disabled_contracts = set(s.get("disabled_agents", []))
        a.disabled_rules = {_unkey(k) for k in s.get("disabled_goals", [])}
        a.agent_context = dict(s.get("agent_context", {}))
        a.agent_situation = {cid: _situation(v) for cid, v in s.get("agent_situation", {}).items()}
        a.battery_on = s.get("battery_on", True)
        a.battery_condition = _situation(s["battery_condition"]) if s.get("battery_condition") else None
        a.set_tone(s.get("tone", a.tone))

    @staticmethod
    def _restore_day(a: Assistant, d: dict) -> None:
        day = date.fromisoformat(d["day"])
        records = lambda key: [Event(e["action"], datetime.fromisoformat(e["at"]), e.get("goal"), e.get("duration"),
                                     e.get("note", ""), e.get("quality")) for e in d.get(key, [])]
        a.events += records("events")
        a.planned += records("planned")
        a.notes |= {(day, *_unkey(k)): note for k, note in d.get("notes", {}).items()}
        a.sent |= {(day, *k) for k in map(tuple, d.get("sent", []))}
        a.delivered |= {(day, *k) for k in map(tuple, d.get("delivered", []))}
        a.skipped |= {(day, *k) for k in map(tuple, d.get("skipped", []))}
        a.plan_changes |= {(day, rid): minutes for rid, minutes in d.get("plan_changes", {}).items()}
        if d.get("tracking_from") and a.tracked_from is not None:
            a.tracked_from[day] = datetime.fromisoformat(d["tracking_from"])
