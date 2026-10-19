"""Client demo: one simulated day on top of a week of history. Run: python -m timeby"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from .assistant import Assistant
from .loader import load_actions, load_contracts
from .model import Anchor, ContractReport, Event, Message, Profile, Status
from .scenarios import TYPICAL_WEEK

PROFILE = Profile(anchors={
    "wake": Anchor(time(7, 0), observed_by="wake"),
    "breakfast": Anchor(time(7, 45)),
    "lunch": Anchor(time(12, 30)),
    "dinner": Anchor(time(19, 0)),
    "sleep": Anchor(time(23, 0), observed_by="sleep"),
    "work_start": Anchor(time(9, 0)),
    "work_end": Anchor(time(17, 0)),
})

HISTORY_PATTERN = TYPICAL_WEEK  # (action, planned HH:MM, jitter minutes, probability it happens)

TODAY_SCRIPT = [
    ("06:58", "wake"), ("07:20", "sunlight"), ("07:50", "meal"), ("08:05", "caffeine"),
    ("08:30", "exercise_light"), ("12:35", "meal"), ("17:40", "caffeine"), ("19:10", "meal"),
    ("21:30", "screen_bright"), ("22:05", "wind_down"), ("23:05", "sleep"),
]
SNAPSHOTS = {"09:30", "20:30", "23:59"}


class ConsoleNotifier:
    def send(self, message: Message) -> None:
        if message.kind in ("feedback", "reply"):
            print(f"       ↳ {message.text}")
        else:
            body = message.text.replace("\n", "\n              ")
            print(f"\n{message.at:%H:%M}  🔔 {message.kind.upper():9} {body}")


def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def seed_history(assistant: Assistant, today: date, days: int = 6, seed: int = 7, pattern=HISTORY_PATTERN) -> None:
    rng = random.Random(seed)
    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        for action, hhmm, jitter, prob in pattern:
            if rng.random() < prob:
                at = _at(day, hhmm) + timedelta(minutes=rng.randint(-jitter, jitter))
                if at.time() < assistant.profile.day_start:  # after midnight belongs to that logical day
                    at += timedelta(days=1)
                assistant.events.append(Event(action, at))
    assistant.events.sort(key=lambda e: e.at)


def print_status(assistant: Assistant, now: datetime) -> None:
    print(f"\n  ┌─ STATUS @ {now:%H:%M}")
    for report in assistant.status(now):
        _print_report(report)
    recs = assistant.recommend(now)
    if recs:
        print("  │ Right now:")
        for rec in recs:
            verb = "AVOID" if rec.avoid else "DO"
            print(f"  │   {verb:5} {rec.rule.advice}  (until {rec.deadline:%H:%M}, evidence: {rec.rule.strength})")
    print("  └─")


def _print_report(report: ContractReport) -> None:
    score = "—" if report.score is None else f"{report.score}/100"
    phase = f" · phase: {report.phase.goal}" if report.phase else ""
    print(f"  │ {report.contract.name}: {score}{phase}")
    for r in report.results:
        if r.status is Status.PENDING and not r.detail:
            continue
        detail = f" ({r.detail})" if r.detail else ""
        print(f"  │   {r.status.value:8} {r.rule.title}{detail}")


def run(today: date | None = None) -> None:
    today = today or date.today()
    assistant = Assistant(PROFILE, load_contracts(), load_actions(), ConsoleNotifier())
    seed_history(assistant, today)

    print(f"=== timeby demo · {today} · {len(assistant.contracts)} contracts ===\n")
    print("Today's proactive plan (pushable to calendar):")
    for at, contract, rule in assistant.plan(today):
        print(f"  {at:%H:%M}  [{contract.id}] {rule.title}")

    script = {hhmm: action for hhmm, action in TODAY_SCRIPT}
    clock = _at(today, "06:00")
    while clock <= _at(today, "23:59"):
        hhmm = f"{clock:%H:%M}"
        if hhmm in script:
            print(f"\n{hhmm}  owner logged: {assistant.actions[script[hhmm]]}")
            assistant.log(script[hhmm], clock)
        assistant.tick(clock)
        if hhmm in SNAPSHOTS:
            print_status(assistant, clock)
        clock += timedelta(minutes=1)


if __name__ == "__main__":
    run()
