from datetime import date, datetime, time, timedelta

import pytest

from timeby.assistant import Assistant
from timeby.demo import PROFILE
from timeby.engine import Context, evaluate
from timeby.loader import ContractError, load_actions, load_contracts, parse_contract
from timeby.model import Event, Status

DAY = date(2026, 10, 9)
ACTIONS = load_actions()
CONTRACTS = {c.id: c for c in load_contracts()}


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def results(contract_id: str, events: list[tuple[str, datetime]], now: datetime):
    ctx = Context(PROFILE, tuple(Event(a, t) for a, t in events))
    report = evaluate(CONTRACTS[contract_id], ctx, now)
    return report, {r.rule.id: r for r in report.results}


class Inbox:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def test_morning_light_window_follows_observed_wake():
    _, r = results("cortisol_rhythm", [("wake", at("08:30")), ("sunlight", at("09:15"))], at("10:00"))
    assert r["morning_light"].status is Status.DONE  # 45 min after the *actual* wake


def test_missed_do_rule_after_window_closes():
    report, r = results("cortisol_rhythm", [("wake", at("07:00"))], at("08:01"))
    assert r["morning_light"].status is Status.MISSED
    assert r["morning_light"].earned == 0


def test_avoid_rule_violated_then_clean_by_default():
    _, r = results("cortisol_rhythm", [("caffeine", at("18:00"))], at("23:30"))
    assert r["caffeine_cutoff"].status is Status.VIOLATED
    assert r["no_late_intense_exercise"].status is Status.CLEAN


def test_pending_rules_do_not_affect_score():
    report, _ = results("cortisol_rhythm", [], at("05:00"))
    assert report.score is None


def test_regularity_scores_fraction_of_days_on_time():
    events = [("meal", at("12:30", DAY - timedelta(days=i))) for i in (1, 2, 3)]
    events.append(("meal", at("13:30", DAY - timedelta(days=4))))  # 60 min off
    _, r = results("nutrition", events, at("10:00"))
    lunch = r["regular_lunch"]
    assert lunch.earned == pytest.approx(2 * (3 + 0.5) / 4)  # the 60-min-late day earns half credit
    assert lunch.status is Status.PENDING
    assert lunch.detail.startswith("3/4 days")


def test_skipped_meal_counts_as_irregular():
    _, r = results("nutrition", [("meal", at("07:45"))], at("15:00"))
    assert r["regular_lunch"].status is Status.MISSED
    assert r["regular_lunch"].earned == 0


def test_bedtime_after_midnight_belongs_to_previous_day():
    _, r = results("cortisol_rhythm", [("sleep", at("00:20", DAY + timedelta(days=1)))], at("01:00", DAY + timedelta(days=1)))
    assert r["consistent_bedtime"].status is Status.LATE  # 80 min late, judged for DAY


def test_nudge_sent_once_and_suppressed_when_already_done():
    inbox = Inbox()
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, inbox)
    a.log("wake", at("07:00"))
    nudges = lambda now: [m.rule_id for m in a.tick(now) if m.kind == "nudge"]
    assert "morning_light" in nudges(at("07:10"))
    assert "morning_light" not in nudges(at("07:11"))
    a.log("sunlight", at("07:05", DAY + timedelta(days=1)))
    assert "morning_light" not in nudges(at("07:15", DAY + timedelta(days=1)))


def test_logging_violation_gives_reasoned_feedback():
    inbox = Inbox()
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, inbox)
    [msg] = a.log("caffeine", at("18:00"))
    assert msg.meta["status"] == "violated"
    assert "Why it matters" in msg.text


def test_recommendations_list_open_rules():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())
    a.log("wake", at("07:00"))
    recs = {r.rule.id: r for r in a.recommend(at("07:15"))}
    assert "morning_light" in recs and not recs["morning_light"].avoid


def test_unknown_action_rejected():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())
    with pytest.raises(ValueError):
        a.log("teleport", at("07:00"))


def test_contract_validation_rejects_uncited_rule():
    raw = {
        "id": "x", "name": "x", "domain": "x", "goal": "x", "sources": {},
        "phases": [{"id": "p", "goal": "g", "window": {"anchor": "wake", "start": 0, "end": 60}}],
        "rules": [{"id": "r", "title": "t", "phase": "p", "kind": "do", "actions": ["sunlight"],
                   "window": {"anchor": "wake", "start": 0, "end": 60}, "weight": 1, "strength": "strong",
                   "why": "w", "advice": "a", "sources": []}],
    }
    with pytest.raises(ContractError, match="cite"):
        parse_contract(raw, ACTIONS)


def new_assistant():
    return Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())


def kinds(messages):
    return [(m.kind, m.rule_id) for m in messages]


def test_morning_brief_fires_at_observed_wake_once():
    a = new_assistant()
    assert ("brief", "brief") not in kinds(a.tick(at("06:50")))
    a.log("wake", at("06:52"))
    assert ("brief", "brief") in kinds(a.tick(at("06:53")))
    assert ("brief", "brief") not in kinds(a.tick(at("06:54")))


def test_recap_waits_for_bedtime_or_grace_period():
    a = new_assistant()
    assert ("recap", "recap") not in kinds(a.tick(at("23:30")))
    assert ("recap", "recap") in kinds(a.tick(at("00:00", DAY + timedelta(days=1))))
    b = new_assistant()
    b.log("sleep", at("22:40"))
    assert ("recap", "recap") in kinds(b.tick(at("22:41")))


def test_one_reminder_per_rule_last_call_only_without_nudge():
    a = new_assistant()
    a.log("wake", at("07:00"))
    assert ("nudge", "morning_light") in kinds(a.tick(at("07:10")))
    assert ("last_call", "morning_light") not in kinds(a.tick(at("07:45")))
    assert ("last_call", "consistent_bedtime") in kinds(a.tick(at("23:15")))  # rule has no contract nudge


def test_brief_covers_focus_reminder_due_soon():
    a = Assistant(PROFILE, [CONTRACTS["cortisol_rhythm"]], ACTIONS, Inbox())
    a.log("wake", at("07:00", DAY - timedelta(days=1)))  # yesterday: no daylight → focus = morning_light
    a.log("wake", at("07:00"))
    assert ("brief", "brief") in kinds(a.tick(at("07:00")))
    assert ("nudge", "morning_light") not in kinds(a.tick(at("07:10")))
    assert ("nudge", "morning_light") not in kinds(a.tick(at("07:45")))  # dropped, not postponed


def test_new_user_brief_has_no_yesterday():
    a = new_assistant()
    a.log("wake", at("07:00"))
    [brief] = [m for m in a.tick(at("07:00")) if m.kind == "brief"]
    assert "Yesterday" not in brief.text and "focus" not in brief.text


def test_snooze_resends_after_expiry_and_refuses_past_window():
    a = new_assistant()
    a.log("wake", at("07:00"))
    a.tick(at("07:10"))
    assert "07:25" in a.snooze("cortisol_rhythm", "morning_light", at("07:10"))
    assert ("nudge", "morning_light") not in kinds(a.tick(at("07:20")))
    assert ("nudge", "morning_light") in kinds(a.tick(at("07:25")))
    assert "past the window" in a.snooze("cortisol_rhythm", "morning_light", at("07:50"))


def test_free_text_logs_every_action_and_acknowledges():
    a = new_assistant()
    msgs = a.answer("had a latte and breakfast", at("07:45"))
    assert {e.action for e in a.events} == {"caffeine", "meal"}
    assert any(m.kind == "reply" and "Noted" in m.text for m in msgs)
    assert any(m.rule_id == "regular_breakfast" for m in msgs)


def test_wake_dependent_rule_waits_for_observed_wake():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)
    light = lambda now: next(r for rep in a.status(now) for r in rep.results if r.rule.id == "morning_light")
    assert light(at("07:20")).status is Status.WAITING and light(at("07:20")).estimated
    assert ("nudge", "morning_light") not in kinds(a.tick(at("07:20")))  # owner still asleep
    a.log("wake", at("07:40"))
    r = light(at("07:41"))
    assert r.status is Status.PENDING and not r.estimated
    assert r.reminder.at == at("07:50") and r.deadline == at("08:40")


def test_waiting_off_by_default_keeps_v1_behavior():
    _, r = results("cortisol_rhythm", [], at("07:20"))
    assert r["morning_light"].status is Status.PENDING


def test_brief_waits_for_observed_wake_when_enabled():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)
    assert ("brief", "brief") not in kinds(a.tick(at("06:55")))
    a.log("wake", at("07:20"))  # within the 30-min tolerance: a normal brief
    assert ("brief", "brief") in kinds(a.tick(at("07:21")))


def today_of(a, now):
    from timeby.today import build_today
    return build_today(a.status(now), now)


def test_today_places_items_earlier_now_later():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)
    t = today_of(a, at("06:40"))
    assert t["now"]["hero"]["rule_id"] == "consistent_wake"          # not up yet → "Wake up" is the moment
    assert next(i for i in t["later"] if i["rule_id"] == "morning_light")["time"] == "After waking"
    a.log("wake", at("06:50"))
    t = today_of(a, at("07:00"))
    assert [i["rule_id"] for i in t["earlier"]] == ["consistent_wake"]
    assert t["now"]["hero"]["rule_id"] == "morning_light"            # soonest deadline first
    t = today_of(a, at("08:20"))
    missed = {i["rule_id"]: i["outcome"] for i in t["earlier"]}
    assert missed["morning_light"] == "Missed"
    current = {i["rule_id"]: i for i in [t["now"]["hero"], *t["now"]["also"]] if i}
    assert current["regular_breakfast"]["late"] and current["regular_breakfast"]["by"] == "09:15"  # late, still worth it


def test_today_avoid_rules_show_as_active_limits_then_outcome():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)
    assert [i["rule_id"] for i in today_of(a, at("17:30"))["now"]["avoid"]] == ["caffeine_cutoff"]
    a.log("caffeine", at("17:45"))
    broken = {i["rule_id"]: i["outcome"] for i in today_of(a, at("18:00"))["earlier"]}
    assert broken["caffeine_cutoff"] == "Broken · 17:45"


def test_switching_off_a_goal_removes_its_reminder_and_scoring():
    a = new_assistant()
    a.log("wake", at("07:00"))
    a.set_enabled("cortisol_rhythm", False, "morning_light")
    assert ("nudge", "morning_light") not in kinds(a.tick(at("07:10")))
    rules = {r.rule.id for rep in a.status(at("07:10")) for r in rep.results}
    assert "morning_light" not in rules and "morning_movement" in rules
    a.set_enabled("cortisol_rhythm", True, "morning_light")
    assert "morning_light" in {r.rule.id for rep in a.status(at("07:10")) for r in rep.results}


def test_switching_off_an_agent_disables_all_its_goals_but_remembers_them():
    a = new_assistant()
    a.set_enabled("nutrition", False, "regular_lunch")
    a.set_enabled("nutrition", False)
    assert "nutrition" not in [rep.contract.id for rep in a.status(at("12:00"))]
    assert "regular_lunch" not in [m.rule_id for m in a.tick(at("12:15"))]  # lunch nudge silenced
    a.set_enabled("nutrition", True)
    meal = next(rep for rep in a.status(at("12:00")) if rep.contract.id == "nutrition")
    assert "regular_lunch" not in {r.rule.id for r in meal.results}  # goal-level choice kept


def v3_assistant():
    return Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)


def result_of(a, rule_id, now):
    return next(r for rep in a.status(now) for r in rep.results if r.rule.id == rule_id)


def test_correct_marks_missed_goal_done_and_back():
    a = v3_assistant()
    assert result_of(a, "regular_lunch", at("15:00")).status is Status.MISSED
    a.correct("nutrition", "regular_lunch", True, at("12:40"), at("15:00"))
    assert result_of(a, "regular_lunch", at("15:00")).status is Status.DONE
    a.correct("nutrition", "regular_lunch", False, None, at("15:00"))
    assert result_of(a, "regular_lunch", at("15:00")).status is Status.MISSED


def test_caffeine_limit_reopens_at_tomorrows_wake_and_holds_until_asleep():
    a = v3_assistant()
    assert result_of(a, "caffeine_cutoff", at("18:00")).until == at("07:00", DAY + timedelta(days=1))
    a.log("caffeine", at("23:30"))  # still awake after the planned bedtime
    assert result_of(a, "caffeine_cutoff", at("23:31")).status is Status.VIOLATED


def test_wake_plan_keeps_usual_time_unless_sleep_drops_below_7h():
    from timeby.sleep_plan import plan_wake
    a = v3_assistant()
    assert plan_wake(a.ctx, at("22:00")).at == at("07:00", DAY + timedelta(days=1))
    a.log("sleep", at("00:50", DAY + timedelta(days=1)))
    late = plan_wake(a.ctx, at("01:00", DAY + timedelta(days=1)))
    assert late.at == at("07:50", DAY + timedelta(days=1))   # +50 min → 7 h of sleep
    a2 = v3_assistant()
    a2.log("sleep", at("02:30", DAY + timedelta(days=1)))
    assert plan_wake(a2.ctx, at("02:31", DAY + timedelta(days=1))).at == at("08:00", DAY + timedelta(days=1))  # capped at +60


def test_v3_recap_waits_for_actual_lights_out():
    a = v3_assistant()
    a.log("screen_bright", at("23:35"))
    assert ("recap", "recap") not in kinds(a.tick(at("00:00", DAY + timedelta(days=1))))
    a.log("sleep", at("00:50", DAY + timedelta(days=1)))
    assert ("recap", "recap") in kinds(a.tick(at("00:51", DAY + timedelta(days=1))))


def test_correct_accepts_late_task_but_rejects_future_and_limit_outside_window():
    a = v3_assistant()
    a.correct("nutrition", "regular_lunch", True, at("15:00"), at("15:30"))  # late lunch is still lunch
    lunch = result_of(a, "regular_lunch", at("15:30"))
    assert lunch.status is Status.LATE and lunch.hit_at == at("15:00")
    assert lunch.earned is not None and lunch.earned > 0
    a.log("wake", at("07:00"))
    with pytest.raises(ValueError, match="between"):
        a.correct("cortisol_rhythm", "morning_light", True, at("07:35"), at("07:30"))  # in the future
    with pytest.raises(ValueError, match="between"):
        a.correct("cortisol_rhythm", "caffeine_cutoff", True, at("10:00"), at("18:00"))  # limit not active then


def test_late_breakfast_tied_to_goal_is_not_read_as_lunch_but_breaks_limits():
    a = v3_assistant()
    a.correct("nutrition", "regular_breakfast", True, at("11:15"), at("11:30"))
    assert result_of(a, "regular_breakfast", at("11:30")).hit_at == at("11:15")
    assert result_of(a, "regular_lunch", at("11:30")).hit_at is None  # 11:15 is inside lunch's window
    a.correct("nutrition", "regular_dinner", True, at("20:30"), at("21:00"))
    assert result_of(a, "no_late_eating", at("21:00")).status is Status.VIOLATED


def test_simulation_jump_forward_back_and_early_start():
    from timeby.server import Simulation
    s = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    s.jump("14:00")
    assert s.now == at("14:00")
    s.jump("05:30")
    assert s.now == at("05:30") and not any(e.at.date() == DAY for e in s.assistant.events)


def test_weekday_goals_only_apply_on_their_days():
    sunday = date(2026, 10, 18)
    monday = sunday + timedelta(days=1)
    a = new_assistant()
    ids = lambda day: {r.rule.id for rep in a.status(at("12:00", day)) for r in rep.results}
    assert "surfing" in ids(sunday) and "workout" not in ids(sunday)
    assert "workout" in ids(monday) and "surfing" not in ids(monday)


def test_late_wake_gives_one_recovery_plan_and_no_reminders_while_asleep():
    a = v3_assistant()
    early = kinds(a.tick(at("07:00")) + a.tick(at("07:30")) + a.tick(at("08:00")))
    assert early == [("nudge", "consistent_wake")]  # only the wake-up call; breakfast waits
    a.log("wake", at("08:25"))
    [plan] = [m for m in a.tick(at("08:25")) if m.kind == "recovery"]
    assert "85 min after your usual 07:00" in plan.text and "Late but worth it: Breakfast" in plan.text
    assert "Unchanged: Lunch 12:30" in plan.text
    assert ("nudge", "regular_breakfast") not in kinds(a.tick(at("08:26")))  # covered by the plan


def test_late_meal_close_to_next_meal_is_merged_not_pushed():
    a = v3_assistant()
    a.log("wake", at("10:00"))
    plan_text = next(m.text for m in a.tick(at("10:00")) if m.kind == "recovery")
    assert "Breakfast — window closed before you woke up" in plan_text
    b = v3_assistant()
    b.log("wake", at("09:10"))  # breakfast window open until 09:15, lunch at 12:30 is > 3 h away
    assert "Late but worth it: Breakfast" in next(m.text for m in b.tick(at("09:10")) if m.kind == "recovery")
    from dataclasses import replace
    from timeby.model import Anchor
    early_lunch = replace(PROFILE, anchors={**PROFILE.anchors, "lunch": Anchor(time(11, 30))})
    c = Assistant(early_lunch, list(CONTRACTS.values()), ACTIONS, Inbox(), wait_for_observed=True)
    c.log("wake", at("09:00"))  # breakfast still open, but lunch is only 2.5 h away -> merge into lunch
    plan = next(m for m in c.tick(at("09:00")) if m.kind == "recovery")
    assert "Breakfast — too close to lunch — merge into it" in plan.text
    assert ("nudge", "regular_breakfast") not in kinds(c.tick(at("09:05")))


def test_late_credit_is_partial():
    a = v3_assistant()
    a.log("meal", at("12:30", DAY - timedelta(days=1)))
    a.log("meal", at("13:10"))  # 40 min late today
    lunch = result_of(a, "regular_lunch", at("15:00"))
    assert lunch.status is Status.LATE and lunch.earned == pytest.approx(2 * (1 + 0.5) / 2)


def test_static_assets_are_flat_and_whitelisted():
    from timeby.server import static_asset
    assert static_asset("/static/../server.py") is None
    assert static_asset("/static/v3.html") is None  # pages are served by route, not as static files
    assert static_asset("/static/missing.js") is None


# --- calendar connector ----------------------------------------------------------------------

def test_ics_writer_escapes_folds_and_uses_crlf():
    from timeby.calendar_ics import CalEvent, write
    long = "Évidence; a, b\\c\n" + "é" * 80
    text = write([CalEvent(at("12:00"), at("12:30"), "Lunch, early; ok", long, "u1@timeby")], at("10:00"))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    lines = text.split("\r\n")[:-1]
    assert all(len(line.encode()) <= 75 for line in lines)
    assert "SUMMARY:Lunch\\, early\\; ok" in lines and "DTSTART:20261009T120000" in lines
    assert "DTSTAMP:20261009T100000Z" in lines
    assert any(line.startswith("DESCRIPTION:Évidence\\; a\\, b\\\\c\\n") for line in lines)
    assert any(line.startswith(" é") for line in lines)  # continuation, never split inside a UTF-8 char


def test_ics_parser_reads_utc_floating_and_tzid_and_skips_all_day():
    from datetime import timezone
    from timeby.calendar_ics import parse
    text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261009T120000\nDTEND:20261009T130000\nSUMMARY:Floating\n"
            "END:VEVENT\nBEGIN:VEVENT\nDTSTART:20261009T100000Z\nDTEND:20261009T103000Z\nSUMMARY:Utc\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART;TZID=Europe/Lisbon:20261009T150000\nDTEND;TZID=Europe/Lisbon:20261009T153000\n"
            "SUMMARY:Long \n name\\, folded\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261009\nDTEND;VALUE=DATE:20261010\nSUMMARY:Holiday\nEND:VEVENT\n"
            "END:VCALENDAR\n")
    floating, utc, tzid = parse(text)
    assert (floating.start, floating.end, floating.summary) == (at("12:00"), at("13:00"), "Floating")
    local = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert utc.start == local and utc.end == local + timedelta(minutes=30)
    assert (tzid.start, tzid.summary) == (at("15:00"), "Long name, folded")


def test_ics_round_trip():
    from timeby.calendar_ics import CalEvent, parse, write
    event = CalEvent(at("09:00"), at("11:00"), "💻 Development", "Two hours; focus, no\nnotifications. " * 5, "dev@t")
    assert parse(write([event], at("08:00"))) == [event]


def test_calendar_feed_has_goals_limits_and_tomorrows_wake():
    from timeby.calendar_ics import parse
    from timeby.server import Simulation
    s = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)  # a Friday
    events = {e.summary: e for e in parse(s.plan_feed())}
    dev = events["💻 Development"]
    assert (dev.start, dev.end) == (at("09:00"), at("11:00"))
    assert "Two hours of development" in dev.description and "Mark G" in dev.description  # advice + evidence
    assert (events["🍽️ Lunch"].start, events["🍽️ Lunch"].end) == (at("12:30"), at("13:00"))
    cutoff = events["☕ Caffeine cut-off until 07:00"]
    assert (cutoff.start, cutoff.end) == (at("17:00"), at("07:00", DAY + timedelta(days=1)))
    assert events["⏰ Wake up"].start == at("07:00", DAY + timedelta(days=1))
    assert "🏋️ Workout" in events and "🏄 Surfing" not in events  # weekday goals only on their days
    assert "Veg with lunch" not in " ".join(events)  # no duration: not a calendar block


def test_meeting_holds_lunch_nudge_until_it_ends():
    a = new_assistant()
    a.set_busy([(at("12:00"), at("13:00"))])
    held = [m for minute in range(60) for m in a.tick(at("12:00") + timedelta(minutes=minute))]
    assert ("nudge", "regular_lunch") not in kinds(held)
    assert ("nudge", "regular_lunch") in kinds(a.tick(at("13:00")))


def test_reminder_whose_validity_ends_during_a_meeting_is_not_sent_late():
    a = new_assistant()
    a.log("wake", at("07:00"))
    a.set_busy([(at("07:05"), at("08:30"))])  # daylight nudge 07:10, valid until 08:00
    sent = [m for minute in range(120) for m in a.tick(at("07:05") + timedelta(minutes=minute))]
    assert "morning_light" not in {m.rule_id for m in sent}


def test_busy_op_loads_sample_meetings_and_marks_calendar_connected():
    from timeby.server import Simulation, handle
    s = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    state = handle(s, "busy", {"sample": True})["state"]
    assert (state["busy"][0]["start"], state["busy"][0]["end"]) == ("12:00", "13:00")
    assert next(c for c in state["connectors"] if c["id"] == "calendar")["status"] == "connected"
    s.jump("05:00")  # replaying the day keeps the meetings
    assert s.assistant.busy and handle(s, "busy", {"clear": True})["state"]["busy"] == []
