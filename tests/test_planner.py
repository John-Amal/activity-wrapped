from datetime import date, datetime, timedelta

import pytest

from activity_wrapped.planner import (
    GROWTH,
    WEEKDAYS,
    Context,
    PlanRequest,
    build_plan,
    choose_days,
    to_ics,
    volumes,
)
from activity_wrapped.training import CriticalSpeed

from .conftest import act

NOW = datetime(2026, 10, 5, 12)


def history(weeks: int = 16, km_per_week: float = 40.0) -> list[dict]:
    acts, i = [], 0
    for w in range(weeks):
        monday = NOW.date() - timedelta(weeks=w + 1, days=NOW.weekday())
        for d, share in ((1, 0.25), (3, 0.25), (6, 0.5)):
            i += 1
            km = km_per_week * share
            acts.append(act(i, (monday + timedelta(days=d)).isoformat(), km=km, minutes=km * 5.5, climb=20))
    return acts


BASE = {"unit": "km", "recent": 40.0, "prior": 40.0, "longest_km": 20.0, "longest_h": 2.0}


def test_build_weeks_grow_gradually_with_recovery_every_fourth():
    vols, kinds, _ = volumes(PlanRequest(goal="endurance", weeks=12), BASE, acwr=1.0)
    assert kinds[3] == kinds[7] == kinds[11] == "recovery"
    for prev, cur, kind in zip(vols, vols[1:], kinds[1:], strict=False):
        if kind == "build" and prev:
            assert cur <= prev * GROWTH["endurance"] / 0.8 + 0.1  # never a jump beyond growth (after recovery)
    assert max(vols) <= 40 * 1.5 + 0.1


def test_spiking_load_holds_week_one():
    vols, _, notes = volumes(PlanRequest(goal="faster", weeks=6), BASE, acwr=1.6)
    assert vols[0] <= 40 and vols[1] <= vols[0] + 0.1
    assert any("holds steady" in n for n in notes)


def test_race_date_tapers():
    req = PlanRequest(goal="faster", target="10k", race_date=date(2026, 12, 6))
    plan = build_plan(req, history(), Context(cs=CriticalSpeed(4.0, 200, 5, 0.99), hr_max=185), NOW)
    kinds = [w["kind"] for w in plan["weeks"]]
    assert kinds[-1] == "race week" and kinds[-2] == "taper"
    race = [s for s in plan["weeks"][-1]["sessions"] if s["title"] == "Race day"]
    assert race and race[0]["amount"] == pytest.approx(10.0)


def test_choose_days_keeps_hard_days_apart():
    days, quality = choose_days(4, long_day=6)
    assert len(days) == 4 and 6 in days
    for q in quality[:2]:
        assert (q + 1) % 7 != 6  # never the day before the long session
    assert abs(quality[0] - quality[1]) > 1


@pytest.mark.parametrize("group,goal", [("run", "faster"), ("run", "maintain"), ("ride", "faster"),
                                        ("swim", "endurance"), ("hike", "endurance")])
def test_plans_are_well_formed(group, goal):
    sports = {"run": "Run", "ride": "Ride", "swim": "Swim", "hike": "Hike"}
    acts = [dict(a, sport_type=sports[group], type=sports[group]) for a in history()]
    plan = build_plan(PlanRequest(group=group, goal=goal, weeks=8, days=4), acts,
                      Context(hr_max=185), NOW)
    assert len(plan["weeks"]) == 8
    for w in plan["weeks"]:
        assert len(w["sessions"]) == 4
        assert {s["day"] for s in w["sessions"]} <= set(WEEKDAYS)
        long = [s for s in w["sessions"] if s["kind"] == "long"][0]
        assert all(s["amount"] <= long["amount"] + 1e-6 for s in w["sessions"] if s["kind"] == "easy")
        hard = sorted(WEEKDAYS.index(s["day"]) for s in w["sessions"] if s["kind"] in ("quality", "steady"))
        assert all(b - a > 1 for a, b in zip(hard, hard[1:], strict=False))


def test_ics_export():
    plan = build_plan(PlanRequest(weeks=4, days=3), history(), Context(), NOW)
    ics = to_ics(plan)
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == 12
    assert "\r\n" in ics
