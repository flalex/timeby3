"""What each agent and its owner know about "right now": the agent's own status line (from the engine and the week),
the owner's feedback on what they did (how it went, a note), and the owner's situation (travelling …) that expires.

Pure and deterministic — the same facts feed the Agents tab and Claude's prompt, so they can never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .model import ContractReport, Status

QUALITY = {"good": "good", "okay": "okay", "poor": "not ideal"}  # how it went, as the owner rated it
SCOPES = {"today": "today", "week": "this week", "open": "until you clear it"}
DONE = {Status.DONE, Status.CLEAN}
WEEK_FLAGS = {"critical": "critical", "at_risk": "at risk", "out_of_reach": "out of reach"}
SHOWN = 3  # names per list in a status line; the rest is "+N more" (every goal's own status is in the facts anyway)


@dataclass(frozen=True)
class Situation:
    text: str
    scope: str
    until: date | None  # last day it applies; None = until cleared

    def active(self, today: date) -> bool:
        return bool(self.text) and (self.until is None or today <= self.until)


def situation(text: str, scope: str, today: date) -> Situation:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope '{scope}'")
    until = {"today": today, "week": today + timedelta(days=6 - today.weekday()), "open": None}[scope]
    return Situation(text.strip()[:500], scope, until)


def check_quality(quality: str | None) -> str | None:
    if quality not in (None, "", *QUALITY):
        raise ValueError(f"unknown rating '{quality}'")
    return quality or None


def feedback_lines(report: ContractReport, notes: dict[tuple[str, str], dict]) -> list[str]:
    """The owner's own words on today's goals of this agent: "Dinner — not ideal: soup with wontons at the airport"."""
    lines = []
    for r in report.results:
        note = notes.get((report.contract.id, r.rule.id), {})
        rated, comment = QUALITY.get(note.get("quality") or ""), note.get("comment", "")
        if rated or comment:
            lines.append(f"{r.rule.label} — " + ": ".join(part for part in (rated, comment) if part))
    return lines


def now_lines(report: ContractReport, week_agent: dict | None, notes: dict[tuple[str, str], dict]) -> list[str]:
    """The agent's status for this moment: today so far, the owner's feedback, and what the week needs."""
    judged = [r for r in report.results if r.earned is not None]
    on_time = sum(r.status in DONE for r in judged)
    late = [r.rule.label for r in judged if r.status is Status.LATE]
    missed = [r.rule.label for r in judged if r.status in (Status.MISSED, Status.VIOLATED)]
    today = f"Today: {on_time} of {len(judged)} on time" if judged else "Today: nothing judged yet"
    today += "".join(f" · {word} {_names(names)}" for word, names in (("late:", late), ("missed:", missed)) if names)
    lines = [today, *feedback_lines(report, notes)]
    flagged = [f"{g['label']} {WEEK_FLAGS[g['state']]}" for g in (week_agent or {}).get("goals", []) if g["state"] in WEEK_FLAGS]
    if week_agent:
        lines.append(f"This week: {week_agent['done']} of {week_agent['judged']} on time"
                     + (f" · {_names(flagged)}" if flagged else ""))
    return lines


def _names(names: list[str]) -> str:
    more = len(names) - SHOWN
    return ", ".join(names[:SHOWN]) + (f" +{more} more" if more > 0 else "")
