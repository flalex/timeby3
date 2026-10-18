"""iCalendar (RFC 5545) in and out, stdlib only.

Writer: VCALENDAR of VEVENTs with floating local times (no TZID, no Z), so a subscribed calendar shows them at
the owner's wall-clock time. Parser: just enough to read busy times — VEVENT DTSTART/DTEND/SUMMARY.
v1 limits: no recurrence (RRULE/RDATE/EXDATE are ignored, only the first occurrence counts), no DURATION,
all-day (DATE) events are skipped, TZID times are taken as the owner's local time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

CRLF = "\r\n"
MAX_OCTETS = 75  # per physical line, excluding CRLF
LOCAL = "%Y%m%dT%H%M%S"
PRODID = "-//timeby//contract-based assistant//EN"


@dataclass(frozen=True)
class CalEvent:
    start: datetime  # naive local time
    end: datetime
    summary: str
    description: str = ""
    uid: str = ""


# --- writer ---------------------------------------------------------------------------------

def write(events: list[CalEvent], stamp: datetime, name: str = "timeby") -> str:
    """A full VCALENDAR; `stamp` (aware or UTC-naive) is the DTSTAMP of every event."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{escape(name)}"]
    for event in events:
        lines += _vevent(event, _utc(stamp))
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + CRLF for line in lines)


def _vevent(event: CalEvent, stamp: str) -> list[str]:
    return ["BEGIN:VEVENT", f"UID:{escape(event.uid)}", f"DTSTAMP:{stamp}",
            f"DTSTART:{event.start:{LOCAL}}", f"DTEND:{event.end:{LOCAL}}",
            f"SUMMARY:{escape(event.summary)}", f"DESCRIPTION:{escape(event.description)}", "END:VEVENT"]


def _utc(stamp: datetime) -> str:
    utc = stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp
    return f"{utc:{LOCAL}}Z"


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Split into chunks of at most 75 octets (continuations start with a space), never inside a UTF-8 char."""
    chunks, current, size, limit = [], "", 0, MAX_OCTETS
    for char in line:
        octets = len(char.encode())
        if size + octets > limit:
            chunks.append(current)
            current, size, limit = "", 0, MAX_OCTETS - 1  # the leading space of a continuation counts
        current, size = current + char, size + octets
    chunks.append(current)
    return (CRLF + " ").join(chunks)


# --- parser ---------------------------------------------------------------------------------

def parse(text: str) -> list[CalEvent]:
    """Timed VEVENTs of a calendar, in file order. Events without DTSTART/DTEND or all-day are skipped."""
    events, props = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            props = {}
        elif line == "END:VEVENT" and props is not None:
            event = _event(props)
            if event:
                events.append(event)
            props = None
        elif props is not None and ":" in line:
            name, params, value = _split(line)
            props.setdefault(name, (params, value))  # first wins: no recurrence overrides in v1
    return events


def unfold(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    head, value = line.split(":", 1)  # params with quoted ':' are not needed for v1
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _event(props: dict) -> CalEvent | None:
    start, end = _time(props.get("DTSTART")), _time(props.get("DTEND"))
    if not start or not end or end <= start:
        return None
    summary = unescape(props.get("SUMMARY", ({}, "Busy"))[1])
    return CalEvent(start, end, summary, unescape(props.get("DESCRIPTION", ({}, ""))[1]), props.get("UID", ({}, ""))[1])


def _time(prop: tuple[dict, str] | None) -> datetime | None:
    """UTC ('Z') → local time of this machine; floating and TZID times → as written. DATE → None (all-day)."""
    if not prop:
        return None
    params, value = prop
    if params.get("VALUE", "").upper() == "DATE" or "T" not in value:
        return None
    if value.endswith("Z"):
        utc = datetime.strptime(value[:-1], LOCAL).replace(tzinfo=timezone.utc)
        return utc.astimezone().replace(tzinfo=None)
    return datetime.strptime(value, LOCAL)


def unescape(text: str) -> str:
    out, chars = [], iter(text)
    for char in chars:
        if char == "\\":
            nxt = next(chars, "")
            out.append("\n" if nxt in ("n", "N") else nxt)
        else:
            out.append(char)
    return "".join(out)
