"""User-facing text built only from contract data and computed results — templated, never generated."""
from __future__ import annotations

from datetime import datetime, timedelta

from .model import NEGATIVE, POSITIVE, ContractReport, Recommendation, RuleKind, RuleResult, Status

STRENGTH_ORDER = {"strong": 0, "moderate": 1, "emerging": 2}


def scores_line(reports: list[ContractReport]) -> str:
    return " · ".join(f"{r.contract.name} {'—' if r.score is None else r.score}" for r in reports)


def focus(reports: list[ContractReport]) -> RuleResult | None:
    """The single most important miss: highest weight, then strongest evidence."""
    misses = [r for rep in reports for r in rep.results if r.status in NEGATIVE]
    return min(misses, key=lambda r: (-r.rule.weight, STRENGTH_ORDER[r.rule.strength]), default=None)


def key_times(reports: list[ContractReport], skip_anchor: str) -> list[str]:
    """Planned times and cut-offs for the day; skips rules anchored to the moment already passed."""
    items = []
    for rep in reports:
        for r in rep.results:
            if r.rule.window.anchor == skip_anchor:
                continue
            if r.rule.kind is RuleKind.REGULARITY:
                items.append((r.deadline - timedelta(minutes=r.rule.tolerance), r.rule.title))
            elif r.rule.kind is RuleKind.AVOID:
                items.append((r.start, r.rule.title))
    return [f"{at:%H:%M} {title}" for at, title in sorted(items)]


def morning_brief(yesterday: list[ContractReport], today: list[ContractReport], wake_anchor: str,
                  with_key_times: bool = True) -> str:
    lines = ["Good morning ☀️"]
    if any(r.score is not None for r in yesterday):
        lines.append(f"Yesterday: {scores_line(yesterday)}.")
    miss = focus(yesterday)
    if miss:
        lines.append(f"Today's focus: {miss.rule.title.lower()} — {miss.rule.advice}")
    if with_key_times:
        lines.append("Key times today:")
        lines += [f"• {item}" for item in key_times(today, wake_anchor)]
    return "\n".join(lines)


def evening_recap(reports: list[ContractReport], itemized: bool = True) -> str:
    wins = [r.rule.title for rep in reports for r in rep.results if r.status in POSITIVE]
    misses = [r.rule.title for rep in reports for r in rep.results if r.status in NEGATIVE]
    lines = [f"Day closed 🌙 {scores_line(reports)}."]
    if not itemized:
        lines.append(f"{len(wins)} kept, {len(misses)} missed.")
    if itemized and wins:
        lines.append(f"✓ Wins ({len(wins)}): " + ", ".join(wins) + ".")
    if itemized and misses:
        lines.append(f"✗ Missed ({len(misses)}): " + ", ".join(misses) + ".")
    miss = focus(reports)
    lines.append(f"Tomorrow's focus: {miss.rule.title.lower()} — {miss.rule.why}" if miss
                 else "Perfect day. Same again tomorrow.")
    return "\n".join(lines)


def status_reply(reports: list[ContractReport]) -> str:
    lines = []
    for rep in reports:
        phase = f" — {rep.phase.goal}" if rep.phase else ""
        lines.append(f"{rep.contract.name}: {'—' if rep.score is None else f'{rep.score}/100'}{phase}")
        lines += [f"  {_ICON[r.status]} {r.rule.title}" + (f" ({r.detail})" if r.detail else "")
                  for r in rep.results if r.status is not Status.PENDING]
    return "\n".join(lines)


def recommend_reply(recs: list[Recommendation], now: datetime) -> str:
    if not recs:
        return "Nothing open right now. I'll ping you when something matters."
    return "\n".join(
        f"{'Avoid' if r.avoid else 'Do'}: {r.rule.advice} (until {r.deadline:%H:%M}, evidence: {r.rule.strength})"
        for r in recs
    )


def why_reply(result: RuleResult, sources: dict[str, str]) -> str:
    refs = "\n".join(f"  – {sources[s]}" for s in result.rule.sources)
    return f"{result.rule.title} ({result.rule.strength} evidence): {result.rule.why}\nSources:\n{refs}"


_ICON = {Status.DONE: "✓", Status.CLEAN: "✓", Status.MISSED: "✗", Status.VIOLATED: "⚠", Status.PENDING: "·", Status.WAITING: "…"}


def feedback(result: RuleResult) -> str:
    text = f"{_ICON[result.status]} {result.rule.title}"
    if result.detail:
        text += f" — {result.detail}"
    if result.status is Status.VIOLATED:
        text += f". Why it matters: {result.rule.why} {result.rule.advice}"
    return text
