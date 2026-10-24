"""Tasks (backlog): things the owner wants to get through.

- ongoing: a book, a course — progress in its own unit (pages, lessons) and a weekly rhythm (sessions a week), so
  "regularly" is something we can check.
- once: do it and it's done (order the robot) — with how long it takes and, optionally, a deadline.
Any task can have a deadline.

Not a science contract: no score, no reminders. Pure and deterministic; storage lives in `storage.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

DEFAULT_PER_WEEK = 3
ONGOING, ONCE = "ongoing", "once"
SOON_DAYS = 2  # a deadline this close reads "due soon"
PACE_DAYS = 14  # pace = progress per session over the last two weeks


@dataclass(frozen=True)
class Entry:
    day: date
    amount: int  # in the task's unit; 0 = a session without a number ("worked on it")


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    unit: str = ""                 # "pages", "lessons" … empty = sessions only
    total: int | None = None       # e.g. 320 pages; None = open-ended
    per_week: int = DEFAULT_PER_WEEK
    entries: tuple[Entry, ...] = ()
    done: bool = False
    kind: str = ONGOING
    estimate: int | None = None    # once: minutes it takes
    deadline: date | None = None

    @property
    def progress(self) -> int:
        return sum(e.amount for e in self.entries)


@dataclass
class Backlog:
    tasks: list[Task] = field(default_factory=list)

    def add(self, title: str, unit: str = "", total: int | None = None, per_week: int = DEFAULT_PER_WEEK,
            kind: str = ONGOING, estimate: int | None = None, deadline: date | None = None) -> Task:
        task = Task(self._new_id(), "")
        self.tasks.append(task)
        try:
            self.edit(task.id, title, unit, total, per_week, kind, estimate, deadline)
        except ValueError:
            self.tasks.remove(task)
            raise
        return self.get(task.id)

    def edit(self, task_id: str, title: str, unit: str = "", total: int | None = None, per_week: int = DEFAULT_PER_WEEK,
             kind: str = ONGOING, estimate: int | None = None, deadline: date | None = None) -> None:
        if kind not in (ONGOING, ONCE):
            raise ValueError(f"unknown task kind '{kind}'")
        once = kind == ONCE  # a one-off has no unit, total or weekly rhythm
        self._replace(task_id, title=_clean_title(title), kind=kind, deadline=deadline,
                      unit="" if once else unit.strip(), total=None if once else _positive(total, "Total"),
                      per_week=DEFAULT_PER_WEEK if once else _per_week(per_week),
                      estimate=_positive(estimate, "Time it takes") if once else None)

    def log(self, task_id: str, day: date, amount: int = 0) -> None:
        """One session on `day`, with how much got done (pages read …)."""
        if amount < 0:
            raise ValueError("Progress can't be negative.")
        task = self.get(task_id)
        self._replace(task_id, entries=task.entries + (Entry(day, amount),))
        if task.total and task.progress + amount >= task.total:
            self._replace(task_id, done=True)

    def undo_last(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task.entries:
            raise ValueError("Nothing to undo.")
        self._replace(task_id, entries=task.entries[:-1], done=False)

    def set_done(self, task_id: str, done: bool) -> None:
        self._replace(task_id, done=done)

    def delete(self, task_id: str) -> None:
        self.tasks.remove(self.get(task_id))

    def get(self, task_id: str) -> Task:
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task is None:
            raise ValueError("That task no longer exists.")
        return task

    def _replace(self, task_id: str, **changes) -> None:
        i = self.tasks.index(self.get(task_id))
        self.tasks[i] = replace(self.tasks[i], **changes)

    def _new_id(self) -> str:
        used = {t.id for t in self.tasks}
        return next(f"t{n}" for n in range(1, len(used) + 2) if f"t{n}" not in used)


# --- view: progress, this week's rhythm, pace -------------------------------------------------

def view(backlog: Backlog, today: date) -> list[dict]:
    """Open tasks first (most in need of a session first), then finished ones."""
    rows = [_row(t, today) for t in backlog.tasks]
    return sorted(rows, key=lambda r: (r["done"], STATE_ORDER.index(r["state"]), r["title"].lower()))


STATE_ORDER = ("overdue", "behind", "due_today", "today", "due_soon", "on_track", "open", "met", "done")


def _row(t: Task, today: date) -> dict:
    week_start = today - timedelta(days=today.weekday())
    sessions = len({e.day for e in t.entries if week_start <= e.day <= today})
    state, note = _state(t, sessions, today)
    return {"id": t.id, "title": t.title, "kind": t.kind, "unit": t.unit, "total": t.total, "per_week": t.per_week,
            "done": t.done, "estimate": t.estimate, "deadline": t.deadline.isoformat() if t.deadline else None,
            "due": _due(t.deadline, today) if t.deadline else "",
            "progress": t.progress, "percent": round(100 * t.progress / t.total) if t.total else None,
            "sessions": sessions, "state": state, "note": note, "last": _last(t, today),
            "today": any(e.day == today for e in t.entries), "pace": _pace(t, today)}


def _state(t: Task, sessions: int, today: date) -> tuple[str, str]:
    """A deadline that's passed or close beats the weekly rhythm — it's the more urgent fact."""
    if t.done:
        return "done", "Finished."
    days = (t.deadline - today).days if t.deadline else None
    if days is not None and days < 0:
        return "overdue", f"The deadline passed {_due(t.deadline, today)}."
    if days == 0:
        return "due_today", "Due today."
    if days is not None and days <= SOON_DAYS:
        return "due_soon", f"Due {_due(t.deadline, today)}."
    if t.kind == ONCE:
        return "open", f"Due {_due(t.deadline, today)}." if t.deadline else "No deadline — do it when it fits."
    return _rhythm(sessions, t.per_week, today)  # ongoing: the pace line says whether the deadline is in reach


def _due(deadline: date, today: date) -> str:
    days = (deadline - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    if days < 0:
        return f"{-days} days ago"
    return f"{deadline:%a %d %b} · in {days} days"


def _rhythm(sessions: int, per_week: int, today: date) -> tuple[str, str]:
    """Same idea as the weekly goals: is this week's target still safe?"""
    need, days_left = per_week - sessions, 7 - today.weekday()  # today counts
    if need <= 0:
        return "met", f"{sessions} of {per_week} sessions this week — rhythm kept."
    if need > days_left:
        return "behind", f"{sessions} of {per_week} this week — a fresh week starts Monday."
    if need == days_left:
        return "today", f"{sessions} of {per_week} this week — every day left counts, today too."
    return "on_track", f"{sessions} of {per_week} this week — {need} more in {days_left} days."


def _last(t: Task, today: date) -> str:
    if not t.entries:
        return "not started"
    days = (today - max(e.day for e in t.entries)).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def _pace(t: Task, today: date) -> str:
    """At your recent pace (and weekly rhythm), when you'll finish — only when there's a total and a pace."""
    if t.done or t.kind == ONCE or not t.total or not t.unit:
        return ""
    recent = [e for e in t.entries if (today - e.day).days < PACE_DAYS and e.amount]
    if not recent:
        return ""
    per_session = sum(e.amount for e in recent) / len({e.day for e in recent})
    sessions_left = -(-(t.total - t.progress) // per_session)  # ceil
    weeks = max(1, round(sessions_left / t.per_week))
    pace = f"~{round(per_session)} {t.unit} a session · about {weeks} week{'s' if weeks > 1 else ''} to finish"
    if t.deadline:
        finish = today + timedelta(weeks=sessions_left / t.per_week)
        pace += " — in time for the deadline" if finish <= t.deadline else " — that's after the deadline: more sessions a week"
    return pace


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the task a name.")
    return title[:120]


def _positive(value: int | None, what: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError(f"{what} must be more than 0.")
    return value


def _per_week(value: int) -> int:
    if not 1 <= value <= 7:
        raise ValueError("Sessions a week: 1 to 7.")
    return value


# --- (de)serialisation for storage ------------------------------------------------------------

def to_json(backlog: Backlog) -> list[dict]:
    return [{"id": t.id, "title": t.title, "kind": t.kind, "unit": t.unit, "total": t.total, "per_week": t.per_week,
             "estimate": t.estimate, "deadline": t.deadline.isoformat() if t.deadline else None, "done": t.done,
             "entries": [{"day": e.day.isoformat(), "amount": e.amount} for e in t.entries]} for t in backlog.tasks]


def from_json(rows: list[dict]) -> Backlog:
    return Backlog([Task(r["id"], r["title"], r.get("unit", ""), r.get("total"), r.get("per_week", DEFAULT_PER_WEEK),
                         tuple(Entry(date.fromisoformat(e["day"]), e["amount"]) for e in r.get("entries", [])),
                         r.get("done", False), r.get("kind", ONGOING), r.get("estimate"),
                         date.fromisoformat(r["deadline"]) if r.get("deadline") else None) for r in rows])


def sample(today: date) -> Backlog:
    """The demo's backlog: a book halfway through and a course not started this week."""
    backlog = Backlog()
    book = backlog.add("Atomic Habits — James Clear", "pages", 320, 4)
    for days_ago, pages in ((9, 22), (7, 30), (5, 18), (2, 25)):
        backlog.log(book.id, today - timedelta(days=days_ago), pages)
    backlog.add("Spanish course", "lessons", 40, 3)
    backlog.add("Book the car service", kind=ONCE, estimate=20, deadline=today + timedelta(days=2))
    return backlog
