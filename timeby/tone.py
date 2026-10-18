"""Assistant tone: the same facts, framed in the owner's preferred voice. Fixed templates — never changes what is
advised, only how it is said, so behaviour stays deterministic."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Rule


@dataclass(frozen=True)
class Tone:
    id: str
    label: str
    blurb: str
    hello: str     # replaces the morning brief's greeting
    nudge: str     # reminders; {text} is the contract's own wording
    warn: str      # a broken limit or a missed goal
    plan: str      # opens the late-start recovery plan
    close: str     # closes the evening recap
    ai_voice: str  # the same voice for Claude's recommendations


TONES = {t.id: t for t in (
    Tone("balanced", "Balanced", "Clear and neutral — the facts and the next step.",
         hello="Good morning ☀️", nudge="{text}", warn="{text}", plan="", close="",
         ai_voice="Clear, neutral and concise."),
    Tone("buddy", "Buddy", "Your friendly bro — casual, a bit of slang, always in your corner.",
         hello="Morning, legend ☀️", nudge="Yo! {text} 🤙", warn="Bro, heads up — {text}",
         plan="No stress, we got this.", close="Solid day, bro. Rest up 🤙",
         ai_voice="A friendly buddy: casual, light slang, upbeat, never preachy."),
    Tone("scientific", "Scientific", "Precise and reasoned — every message cites its evidence.",
         hello="Good morning. Daily summary follows.", nudge="{text} [Evidence: {strength}; {cite}]",
         warn="{text} [Evidence: {strength}; {cite}]", plan="Adjusted plan, based on the observed wake time.",
         close="End of day. Data recorded for tomorrow's plan.",
         ai_voice="Scientific and precise: give the mechanism and the evidence behind each point, no hype."),
    Tone("mentor", "Mentor", "Motivating and demanding — holds you to your standards.",
         hello="Rise and win ☀️", nudge="{text} Do it now — standards are built on days like this.",
         warn="Not good enough. {text} Own it and win the next one.", plan="Late start. No excuses — here's how we win the day back.",
         close="Review it, learn from it, own tomorrow.",
         ai_voice="A demanding mentor: motivating, direct, holds the owner to high standards."),
    Tone("caring", "Caring", "Gentle and supportive — progress over perfection.",
         hello="Good morning, take it easy ☀️", nudge="Just a gentle reminder 💛 {text}",
         warn="That's okay, it happens. {text} Be kind to yourself 💛", plan="No pressure — let's simply pick the day up from here 💛",
         close="You did what you could today. Sleep well 💛",
         ai_voice="Gentle and caring: warm, reassuring, progress over perfection."),
)}
DEFAULT = "balanced"
REMINDERS = {"nudge", "last_call"}


def short_cite(source: str) -> str:
    """'Leproult R, Colecchia EF, ... J Clin Endocrinol Metab, 2001.' -> 'Leproult 2001'."""
    year = re.findall(r"(?:19|20)\d\d", source)
    return f"{source.split()[0]} {year[-1] if year else ''}".strip()


def style(tone_id: str, kind: str, text: str, rule: Rule | None = None, sources: dict[str, str] | None = None,
          status: str | None = None) -> str:
    """The message in the chosen tone. Unknown kinds (replies, sensors) pass through unchanged."""
    tone = TONES.get(tone_id, TONES[DEFAULT])
    if kind in REMINDERS or kind == "feedback" and status in ("violated", "missed"):
        template = tone.nudge if kind in REMINDERS else tone.warn
        return template.format(text=text, **_evidence(rule, sources))
    if kind == "brief":
        return text.replace(TONES[DEFAULT].hello, tone.hello, 1)
    if kind == "recovery" and tone.plan:
        return f"{tone.plan}\n{text}"
    if kind == "recap" and tone.close:
        return f"{text}\n{tone.close}"
    return text


def _evidence(rule: Rule | None, sources: dict[str, str] | None) -> dict[str, str]:
    if not rule or not sources:
        return {"strength": "n/a", "cite": "n/a"}
    return {"strength": rule.strength, "cite": "; ".join(short_cite(sources[s]) for s in rule.sources[:2])}
