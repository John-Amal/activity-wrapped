"""Projections of future performance, with honest uncertainty.

Two projections are offered, and both are stated as ranges:

1. Trend: where the athlete's monthly best is heading if the last year's
   trend simply continues.
2. Plan: what the athlete's own history says about how their performance
   responds to fitness (42-day training load). Monthly performance is
   regressed on that month's average fitness; the plan's fitness at the
   end is simulated from its sessions; the projection is the current level
   scaled by the modelled change. Fitness is clamped to (just beyond) the
   range seen in the athlete's history, so the model is never used far
   outside the data it was fitted on.

If the history shows no clear link between load and performance (too few
months, too little variation in load, or a confidence interval that
includes zero), the app says so instead of projecting.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np

from .training import lower_is_better, month_index

N_BOOT = 1000


def _band(samples: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(samples, [10, 90])  # an 80% range: "likely", without false precision
    return float(lo), float(hi)


def trend_projection(series: list[dict], current: float, end: date, months: int = 12,
                     seed: int = 0) -> dict | None:
    recent = series[-months:]
    if len(recent) < 4:
        return None
    x = np.array([month_index(s["month"]) for s in recent], dtype=float)
    y = np.array([s["value"] for s in recent])
    x_end = end.year * 12 + end.month - 1 + (end.day - 1) / 30
    x_now = x.max()
    rng = np.random.default_rng(seed)
    ratios = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(x), len(x))
        if np.ptp(x[idx]) == 0:
            continue
        b, a = np.polyfit(x[idx], y[idx], 1)
        base = a + b * x_now
        if base > 0:
            ratios.append((a + b * x_end) / base)
    ratios = np.array(ratios)
    lo, hi = _band(ratios * current)
    return {"value": float(np.median(ratios) * current), "low": lo, "high": hi}


def monthly_mean(dates: list[date], values: list[float]) -> dict[str, float]:
    acc: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(dates, values, strict=True):
        acc[f"{d.year}-{d.month:02d}"].append(v)
    return {m: float(np.mean(v)) for m, v in acc.items()}


def simulate_fitness(ctl: float, atl: float, daily: list[float]) -> tuple[float, float]:
    for load in daily:
        ctl += (load - ctl) / 42
        atl += (load - atl) / 7
    return ctl, atl


def plan_projection(series: list[dict], proxy: str, fitness: dict, plan: dict, current: float,
                    now: datetime, recent_weekly_load: float, seed: int = 0) -> dict:
    """See the module docstring. Returns {"available": False, "reason": ...}
    when the athlete's history can't support a projection."""
    ctl_by_month = monthly_mean(fitness["dates"], fitness["fitness"])
    pairs = [(ctl_by_month[s["month"]], s["value"]) for s in series if s["month"] in ctl_by_month]
    pairs = [p for p in pairs if p[0] > 1]
    if len(pairs) < 6:
        return {"available": False, "reason": f"needs at least 6 months with both training and a "
                f"comparable performance; found {len(pairs)}"}
    x = np.array([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    if np.ptp(x) < 8:
        return {"available": False, "reason": "your training load has been too steady to see how "
                "performance responds to it"}

    rng = np.random.default_rng(seed)
    coefs = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(x), len(x))
        if np.ptp(x[idx]) == 0:
            continue
        coefs.append(np.polyfit(x[idx], y[idx], 1))
    coefs = np.array(coefs)  # columns: slope, intercept
    slope_lo, slope_hi = np.percentile(coefs[:, 0], [2.5, 97.5])
    better_with_load = (slope_hi < 0) if lower_is_better(proxy) else (slope_lo > 0)
    if not better_with_load:
        return {"available": False, "reason": "your history doesn't show a clear link between "
                "training load and performance, so only the trend is projected"}

    ctl_now, atl_now = fitness["fitness"][-1], fitness["fatigue"][-1]
    # Planned sessions carry nominal loads. Calibrate them so a plan week at the
    # athlete's starting volume equals what their recent weeks actually scored;
    # then "follow the plan" and "keep going as now" are measured the same way.
    first = plan["weeks"][0]
    nominal_per_unit = first["load"] / first["volume"] if first["volume"] else 0
    baseline_load = nominal_per_unit * plan["baseline"]["weekly"]
    k = recent_weekly_load / baseline_load if baseline_load > 0 and recent_weekly_load > 0 else 1.0
    daily_plan: dict[date, float] = defaultdict(float)
    for w in plan["weeks"]:
        for s in w["sessions"]:
            daily_plan[date.fromisoformat(s["date"])] += s["load"] * k
    end = max(daily_plan) if daily_plan else now.date()
    days = [now.date() + timedelta(days=i + 1) for i in range((end - now.date()).days)]
    ctl_plan, _ = simulate_fitness(ctl_now, atl_now, [daily_plan.get(d, 0.0) for d in days])
    ctl_keep, _ = simulate_fitness(ctl_now, atl_now, [recent_weekly_load / 7] * len(days))

    lo_x, hi_x = float(x.min()) * 0.9, float(x.max()) * 1.1
    clamped = not (lo_x <= ctl_plan <= hi_x)

    def scenario(ctl_end: float) -> dict:
        ctl_end = min(max(ctl_end, lo_x), hi_x)
        base = coefs[:, 1] + coefs[:, 0] * ctl_now
        ratio = (coefs[:, 1] + coefs[:, 0] * ctl_end) / np.where(base > 0, base, np.nan)
        ratio = ratio[np.isfinite(ratio)]
        lo, hi = _band(ratio * current)
        return {"value": float(np.median(ratio) * current), "low": lo, "high": hi, "fitness_end": ctl_end}

    slope = float(np.median(coefs[:, 0]))
    return {
        "available": True,
        "months": len(pairs),
        "effect_per_10_fitness_pct": 100 * slope * 10 / float(np.mean(y)),
        "fitness_now": ctl_now,
        "plan": scenario(ctl_plan),
        "keep": scenario(ctl_keep),
        "clamped": clamped,
        "end": end.isoformat(),
    }
