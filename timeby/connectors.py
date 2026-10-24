"""Connectors: where events come from and where messages and recommendations go, grouped by kind."""
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
    group: str = ""               # section in the Connectors tab


CONNECTORS = (
    Connector("calendar", "Calendar", "📅", "Google · Outlook · Apple",
              "Books time for goals that take time (development, workouts, meals) and keeps reminders out of meetings.",
              group="Calendar"),
    Connector("fitness_tracker", "Fitness tracker", "⌚", "Apple Health · Garmin · Oura · Whoop",
              "Logs wake-up, sleep and workouts automatically, so nothing has to be ticked by hand.",
              ("wake", "sleep", "exercise_light", "exercise_intense", "running", "surf", "padel"), group="Health data"),
    Connector("location", "Location", "📍", "Phone, opt-in · geofences stay on the device",
              "Knows when you are outdoors, at the gym, the padel club or the beach — logs daylight and sessions, "
              "and times nudges to where you are.",
              ("sunlight", "exercise_intense", "running", "padel", "surf"), status="proposed", group="Location"),
    Connector("apple_notes", "Apple Notes", "🗒️", "Mac · iPhone notes arrive through iCloud",
              "Turns a note you keep (e.g. a \"Books to read\" checklist) into Tasks, and saves your evening recap "
              "as a note. Runs on your Mac; notes written on the iPhone sync there through iCloud.",
              status="proposed", group="Notes"),
    Connector("telegram", "Telegram", "✈️", "Your own bot · set up in a minute with @BotFather",
              "Your assistant in your pocket: plans, warnings and the evening recap as Telegram messages.",
              group="Messenger"),
)
