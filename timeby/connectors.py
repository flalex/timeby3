"""Connectors: where events come from and where recommendations go. Declared only — not implemented yet."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Connector:
    id: str
    name: str
    icon: str
    providers: str
    does: str
    reads: tuple[str, ...] = ()   # actions it would log automatically
    status: str = "not connected"


CONNECTORS = (
    Connector("calendar", "Calendar", "📅", "Google · Outlook · Apple",
              "Books time for goals that take time (development, workouts, meals) and keeps reminders out of meetings."),
    Connector("fitness_tracker", "Fitness tracker", "⌚", "Apple Health · Garmin · Oura · Whoop",
              "Logs wake-up, sleep and workouts automatically, so nothing has to be ticked by hand.",
              ("wake", "sleep", "exercise_light", "exercise_intense", "surf", "padel")),
    Connector("location", "Location", "📍", "Phone, opt-in · geofences stay on the device",
              "Knows when you are outdoors, at the gym, the padel club or the beach — logs daylight and sessions, "
              "and times nudges to where you are.",
              ("sunlight", "exercise_intense", "padel", "surf"), status="proposed"),
)
