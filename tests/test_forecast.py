from datetime import date, datetime, timedelta

import numpy as np

from activity_wrapped.forecast import plan_projection, trend_projection

NOW = datetime(2026, 10, 5)


def months(n):
    out, y, m = [], 2025, 1
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fitness_for(ms, ctls):
    dates, fit = [], []
    for mo, c in zip(ms, ctls, strict=True):
        y, m = map(int, mo.split("-"))
        for d in range(1, 28):
            dates.append(date(y, m, d))
            fit.append(c)
    return {"dates": dates, "fitness": fit, "fatigue": fit, "load": fit}


def plan_of(weekly_load, weeks=8, growth=1.0):
    """A plan whose first week sits at the baseline volume (40), then grows."""
    start = NOW.date() + timedelta(days=1)
    out = []
    for w in range(weeks):
        load = weekly_load * growth ** w
        sessions = [{"date": (start + timedelta(days=7 * w + i)).isoformat(), "load": load / 7} for i in range(7)]
        out.append({"load": load, "volume": 40 * growth ** w, "sessions": sessions})
    return {"weeks": out, "baseline": {"weekly": 40}}


def test_projection_refuses_with_too_little_history():
    ms = months(4)
    series = [{"month": m, "value": 250.0} for m in ms]
    out = plan_projection(series, "run", fitness_for(ms, [30, 40, 50, 60]), plan_of(300), 250, NOW, 300)
    assert out["available"] is False and "6 months" in out["reason"]


def test_projection_follows_the_athletes_own_load_response():
    rng = np.random.default_rng(1)
    ms = months(14)
    ctls = rng.uniform(20, 60, len(ms))
    series = [{"month": m, "value": 300 - 0.8 * c + rng.normal(0, 1)} for m, c in zip(ms, ctls, strict=True)]
    fit = fitness_for(ms, ctls)
    fit["fitness"][-1] = fit["fatigue"][-1] = 40.0
    more = plan_projection(series, "run", fit, plan_of(280, growth=1.08), 260, NOW, 280)  # plan builds load
    assert more["available"]
    assert more["plan"]["value"] < more["keep"]["value"]  # more load -> faster (lower pace)
    assert more["plan"]["low"] <= more["plan"]["value"] <= more["plan"]["high"]


def test_projection_refuses_when_load_and_performance_are_unrelated():
    rng = np.random.default_rng(2)
    ms = months(14)
    ctls = rng.uniform(20, 60, len(ms))
    series = [{"month": m, "value": 250 + rng.normal(0, 5)} for m in ms]
    out = plan_projection(series, "run", fitness_for(ms, ctls), plan_of(300), 250, NOW, 300)
    assert out["available"] is False


def test_trend_projection_extends_a_clean_trend():
    ms = months(12)
    series = [{"month": m, "value": 300 - 2 * i} for i, m in enumerate(ms)]
    out = trend_projection(series, current=278, end=date(2026, 3, 1))
    assert out["value"] < 278 and out["low"] <= out["value"] <= out["high"]
