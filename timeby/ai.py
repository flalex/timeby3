"""Claude-written recommendations, one function per agent (live mode only).

The deterministic engine stays the source of truth: Claude only sees today's computed facts and the owner's own
context, and may only advise within the agent's goals. The `anthropic` SDK is optional — without it (or without
credentials) every call raises `AiUnavailable` with a message the UI shows as-is.
"""
from __future__ import annotations

from datetime import datetime

from .model import ContractReport, RuleKind

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # on a refusal the API retries on a fallback model in the same call

SYSTEM = """You are the {role} inside timeby, a personal assistant built on science-based contracts.
Scope: {focus}
Rules:
- Base every recommendation on the facts given; never contradict a goal, its time window or its evidence.
- Recommend only within this agent's goals. No medical diagnoses; suggest a professional if something sounds clinical.
- Use the owner's context to personalise; if it conflicts with a goal, say so briefly and offer the closest safe option.
- Reply with at most 3 recommendations, each one short line starting with "• ", for the rest of today or tomorrow.
- Voice: {voice}"""


class AiUnavailable(RuntimeError):
    """Recommendations can't be produced right now; the message is safe to show to the owner."""


# --- one function per agent ---------------------------------------------------------------

def recommend_cortisol(facts: str, context: str, voice: str = "") -> str:
    return _recommend("cortisol rhythm coach",
                      "morning light, wake-up consistency, movement timing, caffeine cut-off and evening wind-down.",
                      facts, context, voice)


def recommend_nutrition(facts: str, context: str, voice: str = "") -> str:
    return _recommend("nutrition coach",
                      "regular meal times, hydration, vegetables and a protein-rich breakfast.",
                      facts, context, voice)


def recommend_fitness(facts: str, context: str, voice: str = "") -> str:
    return _recommend("fitness coach",
                      "the planned surfing, padel and strength sessions, recovery and timing around sleep.",
                      facts, context, voice)


def recommend_business(facts: str, context: str, voice: str = "") -> str:
    return _recommend("focus and business coach",
                      "the protected development block and the daily marketing slot.",
                      facts, context, voice)


RECOMMENDERS = {
    "cortisol_rhythm": recommend_cortisol,
    "nutrition": recommend_nutrition,
    "fitness": recommend_fitness,
    "business": recommend_business,
}


# --- shared plumbing ------------------------------------------------------------------------

def facts_for(report: ContractReport, now: datetime) -> str:
    """Today's computed state of one agent, as plain lines for the prompt."""
    lines = [f"Time now: {now:%A %H:%M}. Agent: {report.contract.name}. Goal: {report.contract.goal}",
             f"Score today: {'n/a' if report.score is None else f'{report.score}/100'}."]
    for r in report.results:
        rule = r.rule
        when = f"{r.start:%H:%M}–{r.end:%H:%M}" if rule.kind is not RuleKind.REGULARITY else f"planned {r.anchor_at:%H:%M}"
        done = f", happened {r.hit_at:%H:%M}" if r.hit_at else ""
        history = f", history: {r.detail}" if r.detail else ""
        lines.append(f"- {rule.label} ({rule.kind.value}, {when}): {r.status.value}{done}{history}. "
                     f"Advice: {rule.advice} Why: {rule.why}")
    return "\n".join(lines)


def _recommend(role: str, focus: str, facts: str, context: str, voice: str) -> str:
    prompt = (f"<today>\n{facts}\n</today>\n<owner_context>\n{context.strip() or 'none given'}\n</owner_context>\n"
              "What should the owner do next?")
    return _ask_claude(SYSTEM.format(role=role, focus=focus, voice=voice or "Clear, neutral and concise."), prompt)


def _ask_claude(system: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise AiUnavailable("AI recommendations need the Claude SDK: pip install -r requirements-ai.txt") from None
    try:
        client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY or an `ant auth login` profile
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=[FALLBACK_BETA], fallbacks="default",
            output_config={"effort": "low"}, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise AiUnavailable("No Claude API key yet — set ANTHROPIC_API_KEY and restart the server.") from None
    except anthropic.RateLimitError:
        raise AiUnavailable("Claude is rate-limited right now — try again in a minute.") from None
    except anthropic.APIConnectionError:
        raise AiUnavailable("Can't reach Claude — check the internet connection.") from None
    except anthropic.APIStatusError as e:
        raise AiUnavailable(f"Claude API error ({e.status_code}) — try again later.") from None
    except anthropic.AnthropicError:  # e.g. no credentials configured at all
        raise AiUnavailable("No Claude API key yet — set ANTHROPIC_API_KEY and restart the server.") from None
    if response.stop_reason == "refusal":
        raise AiUnavailable("Claude declined this request.")
    return "\n".join(block.text for block in response.content if block.type == "text").strip()
