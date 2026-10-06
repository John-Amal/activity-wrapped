from datetime import date, datetime, timedelta

import pytest

from activity_wrapped import training as tr

from .conftest import act

NOW = datetime(2026, 10, 5, 12)


def day(n: int) -> str:
    return (NOW - timedelta(days=n)).strftime("%Y-%m-%dT07:00:00")


def test_riegel_known_value():
    # A 20:00 5k predicts 41:42 for 10k with the standard exponent.
    assert tr.riegel(1200, 5000, 10000) == pytest.approx(2501.9, abs=0.1)


def test_critical_speed_recovers_known_parameters():
    cs, d_prime = 4.0, 200.0  # 4:10 /km and 200 m
    runs = []
    for i, t in enumerate([180, 300, 480, 720, 1100, 1500, 2100]):
        d = cs * t + d_prime
        runs.append(act(i, day(i * 3), km=d / 1000, minutes=t / 60, climb=0))
    # plus slower easy runs, which the "best per duration band" rule must ignore
    runs += [act(100 + i, day(i), km=8, minutes=48, climb=0) for i in range(5)]
    fit = tr.critical_speed(runs, now=NOW)
    assert fit.cs == pytest.approx(cs, rel=1e-6) and fit.d_prime == pytest.approx(d_prime, rel=1e-4)
    assert fit.r2 == pytest.approx(1.0)


def test_critical_speed_needs_a_spread_of_durations():
    runs = [act(i, day(i), km=5, minutes=25, climb=0) for i in range(6)]
    assert tr.critical_speed(runs, now=NOW) is None


def test_flat_distance_adds_climbing_allowance():
    assert tr.flat_distance(act(1, day(1), km=10, climb=100)) == 10_000 + tr.CLIMB_FACTOR * 100


def test_fitness_converges_to_constant_load_and_ratio_to_one():
    loads = {date(2026, 1, 1) + timedelta(days=i): 50.0 for i in range(400)}
    f = tr.fitness_series(loads)
    assert f["fitness"][-1] == pytest.approx(50, rel=1e-3)
    assert f["fatigue"][-1] == pytest.approx(50, rel=1e-6)
    assert f["acwr"][-1] == pytest.approx(1.0, rel=1e-3)
    assert abs(f["form"][-1]) < 0.1


def test_acwr_bands():
    assert tr.acwr_band(None)[0] == "info"
    assert tr.acwr_band(0.6)[0] == "info"
    assert tr.acwr_band(1.1)[0] == "good"
    assert tr.acwr_band(1.4)[0] == "watch"
    assert tr.acwr_band(1.8)[0] == "warning"


def test_trend_recovers_slope_and_flags_noise():
    series = [{"month": f"2026-{m:02d}", "value": 300 - 3 * m} for m in range(1, 11)]
    t = tr.trend(series)
    assert t["slope"] == pytest.approx(-3)
    assert t["ci_pct"][0] <= t["pct_per_month"] <= t["ci_pct"][1]
    noisy = [{"month": f"2026-{m:02d}", "value": 300 + (5 if m % 2 else -5)} for m in range(1, 11)]
    lo, hi = tr.trend(noisy)["ci_pct"]
    assert lo < 0 < hi
    assert tr.trend(series[:3]) is None


def test_intensity_factor_prefers_power_then_pace_then_heart_rate():
    ride = act(1, day(1), "Ride", km=30, minutes=60, weighted_average_watts=250, device_watts=True,
               average_heartrate=150)
    assert tr.intensity_factor(ride, ftp=250, hr_max=190) == (1.0, "power")
    run = act(2, day(1), km=12, minutes=60, climb=0, average_heartrate=150)
    f, source = tr.intensity_factor(run, cs=4.0, hr_max=190)
    assert source == "pace" and f == pytest.approx((12000 / 3600) / 4.0)
    assert tr.intensity_factor(run, hr_max=190)[1] == "heart rate"
    assert tr.intensity_factor(run) == (tr.DEFAULT_INTENSITY["run"], "default")


def test_one_hour_at_threshold_scores_100():
    run = act(1, day(1), km=14.4, minutes=60, climb=0)
    assert tr.training_load(run, cs=4.0) == pytest.approx(100)


def test_hr_max_needs_enough_data():
    few = [act(i, day(i), max_heartrate=180) for i in range(5)]
    assert tr.estimate_hr_max(few) is None
    many = [act(i, day(i), max_heartrate=170 + i % 10) for i in range(40)]
    assert 175 <= tr.estimate_hr_max(many) <= 179


def test_race_predictions_flag_marathon_without_long_runs():
    runs = [act(1, day(10), km=10, minutes=45, climb=0), act(2, day(20), km=5, minutes=21, climb=0)]
    preds = {p["name"]: p for p in tr.race_predictions(runs, [], NOW)}
    assert preds["10k"]["time"] in ("44:42", "44:43", "45:00", "44:50") or preds["10k"]["seconds"] < 2750
    assert preds["Marathon"]["note"]


def test_pace_zones_are_ordered():
    zones = tr.pace_zones(4.0)
    assert [z["zone"] for z in zones][0] == "Easy"
    assert all(z["low"] < z["high"] for z in zones)
