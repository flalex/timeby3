"""Deterministic free-text understanding: keyword → action or query. An LLM classifier can replace this later."""
from __future__ import annotations

import re
from dataclasses import dataclass

ACTION_KEYWORDS = {
    "caffeine": ["coffee", "espresso", "latte", "cappuccino", "tea", "caffeine", "energy drink", "matcha"],
    "sunlight": ["sunlight", "daylight", "outside", "sun", "balcony"],
    "bright_light": ["light box", "lamp", "bright light"],
    "screen_bright": ["phone", "netflix", "tv", "laptop", "screen", "scrolling", "instagram", "youtube"],
    "exercise_intense": ["gym", "hiit", "run", "running", "crossfit", "lifting", "workout"],
    "exercise_light": ["walk", "walking", "stretch", "yoga", "stroll"],
    "wind_down": ["wind down", "reading", "read", "meditate", "meditation", "meditating", "breathing", "journal"],
    "meal": ["breakfast", "lunch", "dinner", "meal", "ate", "eating", "food"],
    "snack": ["snack", "chocolate", "cookie", "chips", "fruit"],
    "surf": ["surf", "surfing", "surfed"],
    "padel": ["padel"],
    "dev_work": ["coding", "coded", "development", "deep work", "programming"],
    "marketing_work": ["marketing", "campaign", "newsletter"],
    "water": ["water", "hydrated", "glass of water"],
    "vegetables": ["vegetables", "veggies", "salad", "veg"],
    "protein_meal": ["protein", "eggs", "yogurt", "cottage cheese"],
    "wake": ["woke", "wake", "awake", "got up", "good morning"],
    "sleep": ["bed", "sleep", "lights out", "good night", "goodnight"],
}
QUERY_KEYWORDS = {
    "status": ["status", "score", "how am i", "how's my", "progress", "report"],
    "recommend": ["what now", "what should", "what next", "recommend", "advice", "suggest"],
    "why": ["why"],
    "plan": ["plan", "schedule", "agenda"],
}


@dataclass(frozen=True)
class Intent:
    kind: str                    # "log" | "status" | "recommend" | "why" | "plan" | "unknown"
    actions: tuple[str, ...] = ()


def _hits(text: str, keywords: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", text) for k in keywords)


def parse(text: str) -> Intent:
    text = text.lower().strip()
    for kind, keywords in QUERY_KEYWORDS.items():
        if _hits(text, keywords):
            return Intent(kind)
    actions = tuple(a for a, keywords in ACTION_KEYWORDS.items() if _hits(text, keywords))
    return Intent("log", actions) if actions else Intent("unknown")
