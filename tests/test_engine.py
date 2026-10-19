import json
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
    assert "Unchanged: Start work 09:00 · Lunch 12:30" in plan.text
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


def story_sim():
    from timeby.server import Simulation
    s = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    s.reset(DAY, "story")
    return s


def test_story_scenario_oversleeps_into_one_recovery_plan_and_a_paired_now_card():
    s = story_sim()
    morning = s.jump("08:25")
    assert [(m["at"], m["rule_id"]) for m in morning if m["kind"] == "nudge"] == [("07:00", "consistent_wake")]
    assert [m["at"] for m in morning if m["kind"] == "recovery"] == ["08:25"]
    s.jump("08:30")
    hero = s.state()["today"]["now"]["hero"]
    assert hero["rule_id"] == "morning_light" and hero["pair"]["rule_id"] == "regular_breakfast"


def test_story_scenario_cafe_payment_flags_caffeine_and_an_edit_lifts_the_score():
    from timeby.scenarios import CAFE_PAYMENT
    s = story_sim()
    [flag] = [m for m in s.jump("17:45") if m["kind"] == "feedback" and m["rule_id"] == "caffeine_cutoff"]
    assert flag["status"] == "violated" and flag["at"] == "17:45"
    assert s.state()["events"][-1]["source"] == CAFE_PAYMENT
    s.jump("18:00")
    score = lambda: next(c["score"] for c in s.state()["contracts"] if c["id"] == "cortisol_rhythm")
    before = score()
    s.edit({"contract_id": "cortisol_rhythm", "rule_id": "morning_movement", "happened": True, "at": "10:30",
            "duration": "20", "comment": "Walked to the office"})
    assert score() > before
    closing = s.jump("23:10")
    assert [m["at"] for m in closing if m["kind"] == "recap"] == ["23:10"]
    assert s.state()["today"]["tomorrow"]["at"]
# --- personal setup ---------------------------------------------------------------------------

def day_times(**changes):
    times = {"wake": "07:00", "breakfast": "07:45", "lunch": "12:30", "dinner": "19:00", "sleep": "23:00",
             "work_start": "09:00", "work_end": "17:00", **changes}
    return {name: time.fromisoformat(t) for name, t in times.items()}


def test_check_day_accepts_a_normal_day_and_a_night_owl():
    from timeby.personal import check_day
    assert check_day(day_times(), PROFILE.day_start).errors == ()
    owl = check_day(day_times(wake="09:00", breakfast="09:30", lunch="13:30", dinner="20:00", sleep="00:30"),
                    PROFILE.day_start)
    assert owl.errors == () and owl.warnings == ()  # lights out after midnight still belongs to the day


@pytest.mark.parametrize("changes, error", [
    ({"breakfast": "06:30"}, "Breakfast must come after wake-up."),
    ({"breakfast": "07:15", "lunch": "09:15", "dinner": "11:15", "sleep": "12:00"}, "at least 6 h between"),
    ({"lunch": "09:00"}, "Lunch must be at least 2 h after breakfast."),
    ({"dinner": "14:00"}, "Dinner must be at least 2 h after lunch."),
])
def test_check_day_rejects_bad_days(changes, error):
    from timeby.personal import check_day
    assert any(error in e for e in check_day(day_times(**changes), PROFILE.day_start).errors)


def test_check_day_warns_below_seven_hours_of_sleep_but_allows_it():
    from timeby.personal import check_day
    check = check_day(day_times(wake="05:30", breakfast="06:00", sleep="23:30"), PROFILE.day_start)
    assert check.errors == () and "6 h of sleep" in check.warnings[0] and "AASM" in check.warnings[0]


def test_set_profile_moves_dependent_goals_and_reminders():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())
    a.set_profile(day_times(wake="06:00", breakfast="06:45", lunch="13:30"))
    assert result_of(a, "morning_light", at("05:00")).reminder.at == at("06:10")
    assert result_of(a, "regular_lunch", at("05:00")).reminder.at == at("13:15")
    assert a.profile.anchors["wake"].observed_by == "wake" and a.profile.day_start == PROFILE.day_start


def test_set_profile_rejects_invalid_times_and_keeps_the_old_plan():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())
    with pytest.raises(ValueError, match="Lunch must be at least 2 h after breakfast"):
        a.set_profile(day_times(lunch="08:30"))
    assert a.profile == PROFILE


def test_set_days_moves_padel_to_another_weekday():
    a = Assistant(PROFILE, list(CONTRACTS.values()), ACTIONS, Inbox())
    planned = lambda: {rule.id for _, _, rule in a.plan(DAY)}  # DAY is a Friday; padel is tue/thu
    assert "padel" not in planned()
    a.set_days("fitness", "padel", ["fri", "mon"])
    assert "padel" in planned() and a.day_overrides[("fitness", "padel")] == ("mon", "fri")
    with pytest.raises(ValueError, match="at least one day"):
        a.set_days("fitness", "padel", [])


def test_server_profile_and_days_ops():
    from timeby.server import Simulation, handle
    sim = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    short_night = {"wake": "06:00", "breakfast": "06:30", "lunch": "12:00", "dinner": "18:30", "sleep": "23:30"}
    state = handle(sim, "profile", {"anchors": short_night})["state"]
    assert state["profile"]["anchors"]["wake"] == "06:00" and "AASM" in state["profile"]["warnings"][0]
    with pytest.raises(ValueError, match="Dinner must come after lunch"):
        handle(sim, "profile", {"anchors": {**short_night, "dinner": "11:00"}})
    with pytest.raises(ValueError, match="HH:MM"):
        handle(sim, "profile", {"anchors": {**short_night, "wake": "6am"}})
    state = handle(sim, "days", {"contract_id": "fitness", "rule_id": "padel", "days": ["fri"]})["state"]
    padel = next(g for a in state["agents"] if a["id"] == "fitness" for g in a["goals"] if g["id"] == "padel")
    assert padel["days"] == ["fri"] and padel["weekly"]
    state = handle(sim, "jump", {"at": "06:00"})["state"]  # replaying the day keeps the owner's setup
    padel = next(g for a in state["agents"] if a["id"] == "fitness" for g in a["goals"] if g["id"] == "padel")
    assert state["profile"]["anchors"]["wake"] == "06:00" and padel["days"] == ["fri"]
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


# --- live data on disk ----------------------------------------------------------------------

def live_sim(store_dir, today=DAY):
    from timeby.server import Simulation
    from timeby.storage import LiveStore
    s = Simulation(wait_for_observed=True, compact_routines=True, today=today, live=True, store=LiveStore(store_dir))
    s.now = at("09:00", today)
    return s


def test_live_data_is_saved_one_file_per_day_and_restored(tmp_path):
    import json
    from timeby.server import handle
    s = live_sim(tmp_path)
    s.sync = lambda: []  # freeze the real clock for the test
    handle(s, "log", {"action": "caffeine"})
    handle(s, "edit", {"contract_id": "nutrition", "rule_id": "regular_breakfast", "happened": True,
                       "at": "08:10", "duration": "25", "comment": "porridge"})
    handle(s, "context", {"contract_id": "fitness", "text": "Sore left knee"})
    handle(s, "toggle", {"contract_id": "business", "on": False})
    day_file = json.loads((tmp_path / f"{DAY.isoformat()}.json").read_text())
    assert [e["action"] for e in day_file["events"]] == ["meal", "caffeine"]
    assert day_file["notes"]["nutrition/regular_breakfast"]["comment"] == "porridge"
    settings = json.loads((tmp_path / "settings.json").read_text())
    assert settings["agent_context"]["fitness"] == "Sore left knee" and settings["disabled_agents"] == ["business"]

    again = live_sim(tmp_path)  # a server restart
    assert {e.action for e in again.assistant.events} == {"meal", "caffeine"}
    assert again.assistant.agent_context["fitness"] == "Sore left knee"
    assert not again.assistant.is_enabled("business")
    assert result_of(again.assistant, "regular_breakfast", at("09:00")).hit_at == at("08:10")


def test_recommendations_live_only_and_explain_missing_sdk(tmp_path, monkeypatch):
    import sys
    from timeby.ai import AiUnavailable
    from timeby.server import Simulation, recommend
    demo = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    with pytest.raises(ValueError, match="live mode only"):
        recommend(demo, "fitness")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # SDK not installed
    with pytest.raises(AiUnavailable, match="pip install"):
        recommend(live_sim(tmp_path), "fitness")


def test_each_agent_has_its_own_recommender_with_grounded_prompt(tmp_path, monkeypatch):
    from timeby import ai
    sent = {}
    monkeypatch.setattr(ai, "_ask_claude", lambda system, prompt: sent.update(system=system, prompt=prompt) or "• Walk")
    s = live_sim(tmp_path)
    s.assistant.agent_context["fitness"] = "Sore left knee"
    recommender, facts, context, voice = s.recommendation_input("fitness")
    assert recommender is ai.recommend_fitness and recommender(facts, context) == "• Walk"
    assert "fitness coach" in sent["system"] and "Sore left knee" in sent["prompt"] and "Workout" in sent["prompt"]  # Friday: workout day
    assert set(ai.RECOMMENDERS) == {c.id for c in load_contracts()}  # one function per agent


# --- assistant tone ---------------------------------------------------------------------------

def test_tone_changes_voice_not_facts():
    a = v3_assistant()
    a.log("wake", at("07:00"))
    plain = next(m.text for m in a.tick(at("07:10")) if m.rule_id == "morning_light")
    for tone_id, marker in [("buddy", "Yo!"), ("mentor", "Do it now"), ("caring", "gentle reminder"),
                            ("scientific", "[Evidence: moderate; Leproult 2001; Thorn 2004]")]:
        b = v3_assistant()
        b.set_tone(tone_id)
        b.log("wake", at("07:00"))
        styled = next(m.text for m in b.tick(at("07:10")) if m.rule_id == "morning_light")
        assert plain in styled and marker in styled, tone_id


def test_tone_frames_brief_and_warnings_and_rejects_unknown():
    a = v3_assistant()
    a.set_tone("buddy")
    a.log("wake", at("07:00"))
    assert next(m.text for m in a.tick(at("07:00")) if m.kind == "brief").startswith("Morning, legend")
    [warning] = a.log("caffeine", at("18:00"))
    assert warning.text.startswith("Bro, heads up") and "Why it matters" in warning.text
    with pytest.raises(ValueError):
        a.set_tone("pirate")


def test_tone_is_saved_in_live_settings_and_reaches_claude(tmp_path):
    from timeby.server import handle
    s = live_sim(tmp_path)
    s.sync = lambda: []
    handle(s, "tone", {"tone": "mentor"})
    assert live_sim(tmp_path).assistant.tone == "mentor"
    *_, voice = s.recommendation_input("fitness")
    assert "mentor" in voice


# --- Telegram messenger -------------------------------------------------------------------

class FakeTelegram:
    """Stands in for the Bot API: records sent messages, answers getMe / getUpdates."""
    def __init__(self, updates=None):
        self.sent, self.updates = [], updates if updates is not None else [{"message": {"chat": {"id": 42, "first_name": "Alex"}}}]

    def __call__(self, token, method, params=None):
        from timeby.telegram import TelegramError
        if token != "good-token":
            raise TelegramError("Telegram didn't accept this token — copy it again from @BotFather.")
        if method == "sendMessage":
            self.sent.append(params)
        return {"getMe": {"username": "timeby_alex_bot"}, "getUpdates": self.updates}.get(method, {})


def test_telegram_setup_flow_and_token_never_reaches_the_ui(monkeypatch):
    from timeby import telegram
    from timeby.telegram import TelegramChannel, TelegramError
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_call", fake)
    ch = TelegramChannel()
    with pytest.raises(TelegramError, match="didn't accept"):
        ch.connect("bad-token")
    ch.connect("good-token")
    ch.find_chat()
    ch.send_now("hi")
    assert ch.ready and ch.chat_name == "Alex" and fake.sent == [{"chat_id": 42, "text": "hi"}]
    assert "good-token" not in json.dumps(ch.public())


def test_telegram_needs_start_pressed_before_finding_chat(monkeypatch):
    from timeby import telegram
    from timeby.telegram import TelegramChannel, TelegramError
    monkeypatch.setattr(telegram, "_call", FakeTelegram(updates=[]))
    ch = TelegramChannel()
    ch.connect("good-token")
    with pytest.raises(TelegramError, match="press Start"):
        ch.find_chat()


class SyncThread:
    """Runs a background send immediately, so the test can see it."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_live_forwards_by_delivery_choice_and_demo_never_sends(tmp_path, monkeypatch):
    from timeby import telegram
    from timeby.server import Simulation
    from timeby.telegram import TelegramChannel
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_call", fake)
    monkeypatch.setattr(telegram.threading, "Thread", SyncThread)
    s = live_sim(tmp_path)
    s.telegram = TelegramChannel(token="good-token", bot="b", chat_id=42, delivery="important")
    s.log("caffeine")  # 09:00: allowed, nothing to send
    s.now = at("18:00")
    s.log("caffeine")  # breaks the cut-off -> one alert
    demo = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    demo.telegram = TelegramChannel(token="good-token", bot="b", chat_id=42, delivery="all")
    demo.now = at("18:00")
    demo.log("caffeine")
    assert len(fake.sent) == 1 and "caffeine" in fake.sent[0]["text"].lower()


def test_telegram_settings_survive_restart(tmp_path):
    from timeby.telegram import TelegramChannel
    s = live_sim(tmp_path)
    s.telegram = TelegramChannel(token="good-token", bot="b", chat_id=42, chat_name="Alex", delivery="all")
    s.persist()
    assert live_sim(tmp_path).telegram.ready and live_sim(tmp_path).telegram.delivery == "all"


# --- weekly view --------------------------------------------------------------------------

WED = date(2026, 10, 21)  # a Wednesday: Mon and Tue are past, Thu–Sun ahead


def week_of(a, now):
    from timeby.week import build_week
    w = build_week(a, now)
    return w, {g["id"]: g for agent in w["agents"] for g in agent["goals"]}


def test_week_progress_counts_done_out_of_judged_like_2_of_3():
    a = v3_assistant()
    a.log("wake", at("07:00", WED - timedelta(days=2)))  # Mon on time
    a.log("wake", at("08:40", WED - timedelta(days=1)))  # Tue overslept: late, not a proper wake-up
    a.log("wake", at("07:05", WED))                      # Wed on time
    w, goals = week_of(a, at("10:00", WED))
    wake = goals["consistent_wake"]
    assert [d["label"] for d in w["days"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] and w["days"][2]["today"]
    assert wake["cells"][:3] == ["done", "late", "done"] and (wake["done"], wake["judged"]) == (2, 3)
    assert wake["cells"][3:] == ["planned"] * 4


def test_week_target_states_explain_why_today_or_tomorrow_matters():
    from timeby.week import Progress, _verdict
    mon, wed, fri = WED - timedelta(days=2), WED, WED + timedelta(days=2)
    assert _verdict(Progress(2, 2, [fri], 2), wed)[0] == "met"
    state, note = _verdict(Progress(0, 1, [wed, fri], 2), wed)  # workout: missed Mon, need Wed and Fri
    assert state == "critical" and "today, Fri" in note
    state, note = _verdict(Progress(1, 1, [wed, fri], 1), wed)
    assert state == "met"
    state, note = _verdict(Progress(0, 0, [wed, fri], 1), wed)  # one of two left: do it today or Fri becomes critical
    assert state == "at_risk" and note == "Do it today — otherwise Fri becomes critical."
    assert _verdict(Progress(0, 2, [fri], 2), wed)[0] == "out_of_reach"


def test_week_uses_weekly_targets_and_weekday_plans():
    a = v3_assistant()
    w, goals = week_of(a, at("10:00", WED))
    workout = goals["workout"]  # Mon, Wed, Fri; WHO: 2 a week
    assert workout["target"] == 2 and [c is not None for c in workout["cells"]] == [True, False, True, False, True, False, False]
    assert "WHO" in workout["why"]
    assert goals["regular_lunch"]["target"] == 5  # daily goal: most days
    assert any(f["id"] == "workout" for f in w["focus"])  # critical and doable today


# --- live: only the owner's own records; nothing judged before tracking started -------------

def live_at(store_dir, hhmm, day=DAY):
    from timeby.server import Simulation
    from timeby.storage import LiveStore
    from unittest import mock
    with mock.patch("timeby.server.datetime") as fake_dt:  # the server reads the real clock at start
        fake_dt.now.return_value = at(hhmm, day)
        fake_dt.combine = datetime.combine
        s = Simulation(wait_for_observed=True, compact_routines=True, today=day, live=True, store=LiveStore(store_dir))
    s.sync = lambda: []
    return s


def test_live_fresh_start_invents_no_outcomes(tmp_path):
    s = live_at(tmp_path, "15:28")
    statuses = {r.rule.id: r.status for rep in s.assistant.status(s.now) for r in rep.results}
    for goal in ("consistent_wake", "morning_light", "regular_breakfast", "regular_lunch", "plants_lunch"):
        assert statuses[goal] is Status.UNTRACKED, goal
    assert Status.MISSED not in statuses.values() and Status.CLEAN not in statuses.values()
    assert all(rep.score is None for rep in s.assistant.status(s.now))  # no fake "0" scores
    today = s.state()["today"]
    assert today["tracking"] == {"from": "15:28", "untracked": sum(i["tone"] == "none" for i in today["earlier"])}
    assert not any(i["tone"] == "bad" for i in today["earlier"])


def test_live_judges_normally_after_tracking_started_and_keeps_own_records(tmp_path):
    s = live_at(tmp_path, "15:28")
    s.now = at("21:00")  # dinner window (17:30–20:30) closed after tracking started, nothing logged
    dinner = result_of(s.assistant, "regular_dinner", s.now)
    assert dinner.status is Status.MISSED
    s.log("meal")  # the owner's own record at 21:00 (late) breaks "kitchen closed"
    assert result_of(s.assistant, "no_late_eating", s.now).status is Status.VIOLATED


def test_live_tracking_start_is_saved_in_the_day_file_and_restored(tmp_path):
    import json
    s = live_at(tmp_path, "15:28")
    s.persist()
    saved = json.loads((tmp_path / f"{DAY.isoformat()}.json").read_text())
    assert saved["tracking_from"] == at("15:28").isoformat(timespec="minutes") and saved["events"] == []
    again = live_at(tmp_path, "16:00")  # restart later the same day: tracking still counts from 15:28
    assert again.assistant.tracked_from[DAY] == at("15:28")


def test_first_record_of_the_day_means_awake_and_reminders_stop_waiting(tmp_path):
    a = v3_assistant()
    assert a._asleep(DAY, at("08:00"))
    a.log("water", at("08:05"))  # any record today: the owner is up
    assert not a._asleep(DAY, at("08:10"))
    b = v3_assistant()
    assert not b._asleep(DAY, at("10:30"))  # 3 h past the planned wake-up: the plan stands


def test_demo_still_judges_the_whole_day(tmp_path):
    a = v3_assistant()
    assert result_of(a, "regular_lunch", at("15:28")).status is Status.MISSED


# --- owner edits upcoming goals and own records ------------------------------------------------

def test_move_an_upcoming_goal_today_moves_its_reminder_and_judging():
    a = v3_assistant()
    a.move_goal("nutrition", "regular_lunch", at("14:00"), at("10:00"))
    lunch = result_of(a, "regular_lunch", at("10:00"))
    assert lunch.anchor_at == at("14:00")
    assert ("nudge", "regular_lunch") not in kinds(a.tick(at("12:15")))
    assert ("nudge", "regular_lunch") in kinds(a.tick(at("13:45")))
    a.log("meal", at("14:05"))
    assert result_of(a, "regular_lunch", at("14:10")).status is Status.DONE  # on time for the moved plan
    assert result_of(a, "regular_lunch", at("12:00", DAY + timedelta(days=1))).anchor_at == at("12:30", DAY + timedelta(days=1))


def test_skip_and_restore_an_upcoming_goal_and_refuse_settled_or_past():
    a = v3_assistant()
    a.skip_goal("fitness", "workout", at("10:00"))
    assert "workout" not in {r.rule.id for rep in a.status(at("10:00")) for r in rep.results}
    assert not [m for m in a.tick(at("15:45")) if m.rule_id == "workout"]
    a.restore_goal("workout", at("10:00"))
    assert "workout" in {r.rule.id for rep in a.status(at("10:00")) for r in rep.results}
    with pytest.raises(ValueError, match="pick a time"):
        a.move_goal("nutrition", "regular_dinner", at("09:00"), at("10:00"))
    a.log("wake", at("07:00"))
    with pytest.raises(ValueError, match="already settled"):
        a.skip_goal("cortisol_rhythm", "consistent_wake", at("10:00"))


def test_records_can_be_moved_or_deleted_and_are_listed_for_the_timeline(tmp_path):
    from timeby.server import handle
    s = live_at(tmp_path, "15:40")
    handle(s, "log", {"action": "surf"})                      # matches no goal on a Friday
    handle(s, "log", {"action": "meal", "at": "12:40"})       # "I had it at…": lunch, on time
    records = {r["action"]: r for r in s.state()["records"]}
    assert records["surf"]["on_goal"] is False and records["meal"]["on_goal"] is True
    handle(s, "record", {"action": "move", "record": {"action": "surf", "at": "15:40"}, "to": "15:00"})
    handle(s, "record", {"action": "delete", "record": {"action": "meal", "at": "12:40"}})
    assert [(r["action"], r["at"]) for r in s.state()["records"]] == [("surf", "15:00")]
    with pytest.raises(ValueError, match="earlier today"):
        handle(s, "log", {"action": "meal", "at": "18:00"})  # can't log the future


def test_plan_changes_are_saved_with_the_day(tmp_path):
    from timeby.server import handle
    s = live_at(tmp_path, "10:00")
    handle(s, "plan", {"contract_id": "nutrition", "rule_id": "regular_lunch", "action": "move", "at": "13:30"})
    handle(s, "plan", {"contract_id": "fitness", "rule_id": "workout", "action": "skip"})
    again = live_at(tmp_path, "10:05")
    assert again.assistant.plan_changes == {(DAY, "regular_lunch"): 60, (DAY, "workout"): None}


# --- Claude API key via the UI ------------------------------------------------------------------

class MemoryBackend:
    where = "test memory"

    def __init__(self):
        self.value, self.reads = None, 0

    def get(self):
        self.reads += 1
        return self.value

    def put(self, key):
        self.value = key

    def delete(self):
        self.value = None


def test_api_key_is_validated_masked_and_cached():
    from timeby.keystore import KeyStore
    backend = MemoryBackend()
    store = KeyStore(backend)
    with pytest.raises(ValueError, match="sk-ant-"):
        store.save("hello")
    store.save("  sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234  ")
    assert backend.value == "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234"
    assert store.public() == {"configured": True, "masked": "sk-ant-…1234", "where": "test memory"}
    store.public(); store.public()
    assert backend.reads == 0  # cached after save: the keychain isn't asked on every UI refresh
    store.remove()
    assert store.public()["configured"] is False and backend.value is None


def test_private_file_backend_is_created_owner_only(tmp_path):
    import stat
    from timeby.keystore import PrivateFile
    f = PrivateFile(tmp_path / "timeby" / "key")
    f.put("sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234")
    assert stat.S_IMODE((tmp_path / "timeby" / "key").stat().st_mode) == 0o600
    assert f.get() == "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234"


def test_saved_key_is_what_claude_is_called_with(monkeypatch):
    import sys, types
    from timeby import ai
    seen = {}

    class FakeClient:
        def __init__(self, api_key=None):
            seen["key"] = api_key
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: types.SimpleNamespace(
                stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text="• Walk")])))

    fake = types.SimpleNamespace(Anthropic=FakeClient, AuthenticationError=type("A", (Exception,), {}),
                                 RateLimitError=type("R", (Exception,), {}), APIConnectionError=type("C", (Exception,), {}),
                                 APIStatusError=type("S", (Exception,), {}), AnthropicError=type("E", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setattr(ai, "_key_source", lambda: "sk-ant-api03-saved-key-0000000000")
    assert ai.recommend_fitness("facts", "") == "• Walk" and seen["key"] == "sk-ant-api03-saved-key-0000000000"


@pytest.mark.parametrize("changes, error", [
    ({"work_end": "11:00"}, "Work end must be 4–12 h after work start."),
    ({"work_start": "06:00", "work_end": "14:00"}, "between wake-up and lights out"),
])
def test_check_day_rejects_bad_working_days(changes, error):
    from timeby.personal import check_day
    assert any(error in e for e in check_day(day_times(**changes), PROFILE.day_start).errors)


def test_full_time_job_topic_runs_on_weekdays_with_its_own_hours():
    a = v3_assistant()
    goals = {r.rule.id: r for r in result_of_all(a, at("08:00"))}
    assert {"start_work", "work_break", "finish_work", "switch_off"} <= goals.keys()  # DAY is a Friday
    assert goals["start_work"].anchor_at == at("09:00") and goals["finish_work"].anchor_at == at("17:00")
    a.log("work_after_hours", at("19:30"))
    assert result_of(a, "switch_off", at("19:31")).status is Status.VIOLATED
    saturday = DAY + timedelta(days=1)
    assert "start_work" not in {r.rule.id for r in result_of_all(a, at("08:00", saturday))}


def test_work_hours_are_set_in_the_business_agent_and_partial_updates_keep_the_rest():
    from timeby.server import Simulation, handle
    sim = Simulation(wait_for_observed=True, compact_routines=True, today=DAY)
    business = next(x for x in sim.state()["agents"] if x["id"] == "business")
    assert [t["id"] for t in business["anchors"]] == ["wake", "lunch", "work_start", "work_end"]
    state = handle(sim, "profile", {"anchors": {"work_start": "08:00", "work_end": "16:30"}})["state"]
    assert state["profile"]["anchors"]["work_start"] == "08:00" and state["profile"]["anchors"]["lunch"] == "12:30"


def result_of_all(a, now):
    return [r for rep in a.status(now) for r in rep.results]
