from datetime import date

import pytest

from activity_wrapped.stats import (
    Period,
    best_effort_candidates,
    best_efforts,
    compute_stats,
    eddington,
    filter_activities,
    fmt_clock,
    longest_streak,
    pace_or_speed,
)

from .conftest import act


def test_eddington_known_values():
    assert eddington([]) == 0
    assert eddington([1, 1, 1]) == 1
    assert eddington([5, 5, 5, 5, 5]) == 5
    assert eddington([10, 9, 8, 3, 2]) == 3
    assert eddington([100]) == 1  # one long day only proves E=1


def test_longest_streak_handles_gaps_and_duplicates():
    days = [date(2026, 1, d) for d in (1, 2, 3, 3, 5, 6, 7, 8)]
    assert longest_streak(days) == (4, date(2026, 1, 5), date(2026, 1, 8))
    assert longest_streak([]) == (0, None, None)


def test_period_parse_and_bounds(now):
    assert Period.parse("year:2025").year == 2025
    assert Period.parse(None).kind == "last12"
    start, end = Period.parse("last12").bounds(now)
    assert start.isoformat() == "2025-11-01T00:00:00" and end is None
    with pytest.raises(ValueError):
        Period.parse("last-week")


def test_filter_by_period_and_sport(now):
    acts = [act(1, "2025-10-31", "Run"), act(2, "2025-11-01", "Run"), act(3, "2026-03-01", "Ride"),
            act(4, "2024-06-01", "Run")]
    ids = lambda xs: sorted(a["id"] for a in xs)  # noqa: E731
    assert ids(filter_activities(acts, Period("last12"), None, now)) == [2, 3]
    assert ids(filter_activities(acts, Period("last12"), ["Ride"], now)) == [3]
    assert ids(filter_activities(acts, Period("year", 2024), None, now)) == [4]
    assert ids(filter_activities(acts, Period("all"), ["Run"], now)) == [1, 2, 4]


def test_pace_and_speed_formats():
    assert pace_or_speed("Run", 10_000, 50 * 60, "metric") == "5:00 /km"
    assert pace_or_speed("Ride", 30_000, 3600, "metric") == "30.0 km/h"
    assert pace_or_speed("Swim", 1000, 20 * 60, "metric") == "2:00 /100m"
    assert pace_or_speed("Run", 1609.344, 8 * 60, "imperial") == "8:00 /mi"
    assert pace_or_speed("Run", 0, 100, "metric") is None
    assert fmt_clock(3725) == "1:02:05"


def test_compute_stats_totals_and_series_agree(now):
    acts = [act(1, "2026-01-10", "Run", km=10, minutes=50), act(2, "2026-01-11", "Run", km=12, minutes=60),
            act(3, "2026-03-02", "Ride", km=40, minutes=90, climb=400), act(4, "2024-01-01", "Run", km=5)]
    s = compute_stats(acts, period=Period("last12"), now=now)
    assert s["count"] == 3
    assert s["totals"]["distance_value"] == 62.0
    assert sum(s["series"]["values"]) == pytest.approx(62.0)  # chart covers exactly the totals
    assert len(s["series"]["labels"]) == 12
    assert [x["sport"] for x in s["by_sport"]] == ["Ride", "Run"]
    assert s["by_sport"][1]["pace"] == "5:00 /km"
    assert s["streak"]["days"] == 2
    assert s["climb"]["value"] == "400 m"
    assert s["eddington"]["value"] == 3
    assert s["pace"] == {"sport": "Ride", "value": "26.7 km/h", "label": "avg speed"}
    runs_only = compute_stats(acts, period=Period("last12"), sports=["Run"], now=now)
    assert runs_only["pace"]["label"] == "avg pace"


def test_compute_stats_imperial_and_empty(now):
    acts = [act(1, "2026-02-01", "Run", km=16.09344, minutes=80)]
    s = compute_stats(acts, period=Period("last12"), units="imperial", now=now)
    assert s["totals"]["distance"] == "10 mi"
    assert compute_stats(acts, period=Period("year", 2020), now=now)["empty"] is True


def test_best_efforts_takes_fastest_and_reports_coverage(now):
    acts = [act(1, "2026-05-01", km=10, minutes=45), act(2, "2026-06-01", km=10, minutes=55),
            act(3, "2026-06-02", "Ride", km=30, minutes=60)]
    assert [a["id"] for a in best_effort_candidates(acts)] == [1, 2]  # runs only, fastest first
    details = {
        1: {"id": 1, "name": "Race", "start_date_local": "2026-05-01T07:00:00Z",
            "best_efforts": [{"name": "5k", "distance": 5000, "elapsed_time": 1300, "pr_rank": 1}]},
    }
    s = compute_stats(acts, period=Period("last12"), now=now, details=details)
    assert s["best_efforts"]["scanned"] == 1 and s["best_efforts"]["total"] == 2
    assert s["best_efforts"]["efforts"][0]["time"] == "21:40"
    details[2] = {"id": 2, "name": "Tempo", "start_date_local": "2026-06-01T07:00:00Z",
                  "best_efforts": [{"name": "5k", "distance": 5000, "elapsed_time": 1250}]}
    assert best_efforts(details.values())[0]["activity"] == "Tempo"


def test_pb_progression_and_personal_bests():
    from activity_wrapped.stats import pb_progression, personal_bests
    details = [
        {"id": 1, "name": "A", "start_date_local": "2026-01-01T07:00:00Z",
         "best_efforts": [{"name": "5k", "distance": 5000, "elapsed_time": 1500}]},
        {"id": 2, "name": "B", "start_date_local": "2026-02-01T07:00:00Z",
         "best_efforts": [{"name": "5k", "distance": 5000, "elapsed_time": 1550}]},
        {"id": 3, "name": "C", "start_date_local": "2026-03-01T07:00:00Z",
         "best_efforts": [{"name": "5k", "distance": 5000, "elapsed_time": 1440}]},
    ]
    steps = pb_progression(reversed(details))  # input order must not matter
    assert [s["activity_id"] for s in steps["5k"]] == [1, 3]
    (pb,) = personal_bests(details)
    assert pb["time"] == "24:00" and pb["improvements"] == 1 and pb["first_time"] == "25:00"
    assert pb["activity_id"] == 3


def test_activity_detail_formats_splits_efforts_and_segments():
    from activity_wrapped.stats import activity_detail
    a = act(9, "2026-04-01", km=2.1, minutes=10.5, average_heartrate=150.0)
    detail = {
        "max_heartrate": 171, "average_cadence": 88, "calories": 200,
        "splits_metric": [{"distance": 1000, "moving_time": 300}, {"distance": 1000, "moving_time": 290},
                          {"distance": 100, "moving_time": 40}],
        "best_efforts": [{"name": "1k", "elapsed_time": 285, "pr_rank": 1},
                         {"name": "1 mile", "elapsed_time": 480, "pr_rank": None}],
        "segment_efforts": [{"name": "Flat", "elapsed_time": 100, "distance": 400, "pr_rank": None},
                            {"name": "Hill", "elapsed_time": 200, "distance": 600, "pr_rank": 2}],
    }
    d = activity_detail(a, detail, "metric")
    assert [s["pace"] for s in d["splits"]] == ["5:00 /km", "4:50 /km"]  # 100 m fragment dropped
    assert {e["label"]: e["value"] for e in d["extras"]}["cadence"] == "176 spm"
    assert d["best_efforts"][0]["badge"] == "PR"
    assert [s["name"] for s in d["segments"]] == ["Hill", "Flat"]  # ranked efforts first
    assert activity_detail(a, None)["details_loaded"] is False
