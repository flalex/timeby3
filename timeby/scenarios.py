"""Demo scenarios: what the owner does today (logs, skips, adds), on top of the same typical week of history.
Wake/sleep come from the wearable; everything else is logged by the owner ("you")."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

YOU = "you"


@dataclass(frozen=True)
class ScriptedEvent:
    at: time
    action: str
    source: str = YOU


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    description: str
    script: tuple[ScriptedEvent, ...]


def _s(*steps: tuple) -> tuple[ScriptedEvent, ...]:
    return tuple(ScriptedEvent(time.fromisoformat(at), action, *source) for at, action, *source in steps)


TYPICAL_WEEK = (  # (action, planned HH:MM, jitter minutes, probability it happens)
    ("wake", "07:00", 50, 1.0), ("sunlight", "07:40", 30, 0.4), ("meal", "07:45", 45, 0.7),
    ("caffeine", "08:00", 15, 1.0), ("meal", "12:45", 50, 0.9), ("caffeine", "16:30", 60, 0.6),
    ("meal", "19:30", 60, 1.0), ("screen_bright", "22:00", 40, 0.9), ("sleep", "23:15", 50, 1.0),
)
WEARABLE = "wearable"

SCENARIOS = {s.id: s for s in (
    Scenario("model_day", "Model day", "Does everything on time and logs it.",
             _s(("06:58", "wake", WEARABLE), ("07:10", "sunlight"), ("07:45", "meal"), ("08:00", "caffeine"),
                ("08:20", "exercise_light"), ("09:05", "dev_work"), ("12:30", "meal"), ("13:35", "marketing_work"),
                ("19:00", "meal"), ("22:00", "wind_down"),
                ("22:58", "sleep", WEARABLE))),
    Scenario("skips_meals", "Skips breakfast, late lunch", "Coffee instead of breakfast, lunch at 13:40.",
             _s(("07:00", "wake", WEARABLE), ("07:30", "caffeine"), ("13:40", "meal"), ("19:10", "meal"),
                ("23:05", "sleep", WEARABLE))),
    Scenario("late_coffee", "Afternoon coffee, late dinner", "Good morning, then coffee at 17:45 and dinner at 21:00.",
             _s(("06:55", "wake", WEARABLE), ("07:20", "sunlight"), ("07:50", "meal"), ("08:10", "caffeine"),
                ("12:40", "meal"), ("17:45", "caffeine"), ("21:00", "meal"), ("23:10", "sleep", WEARABLE))),
    Scenario("overslept", "Overslept", "Wakes at 08:25, rushes coffee and a late breakfast.",
             _s(("08:25", "wake", WEARABLE), ("08:30", "caffeine"), ("08:40", "meal"), ("12:30", "meal"),
                ("19:00", "meal"), ("23:00", "sleep", WEARABLE))),
    Scenario("night_owl", "Workout and screens late", "Hard workout at 22:20, phone until 00:20, asleep at 00:30.",
             _s(("07:00", "wake", WEARABLE), ("07:15", "sunlight"), ("07:45", "meal"), ("12:30", "meal"),
                ("19:00", "meal"), ("22:20", "exercise_intense"), ("22:45", "screen_bright"),
                ("00:30", "sleep", WEARABLE))),
    Scenario("extras", "Adds extras", "Extra coffees and snacks, including one after the kitchen closed.",
             _s(("07:00", "wake", WEARABLE), ("07:45", "meal"), ("08:00", "caffeine"), ("11:00", "caffeine"),
                ("12:30", "meal"), ("15:30", "snack"), ("16:00", "caffeine"), ("19:00", "meal"),
                ("21:30", "snack"), ("23:00", "sleep", WEARABLE))),
    Scenario("own", "Your own (nothing scripted)", "Only you act: log, mark done, skip, edit. Use 'Jump to' to move the clock.",
             ()),
    Scenario("forgot_to_log", "Did it, logged nothing", "Only the wearable reports. Everything shows as missed — fix it by editing past items.",
             _s(("07:00", "wake", WEARABLE), ("23:00", "sleep", WEARABLE))),
)}
DEFAULT = "model_day"
