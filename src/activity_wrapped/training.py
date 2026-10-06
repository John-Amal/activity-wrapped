"""Training analytics: load, fitness and fatigue, performance and trends.

All functions are pure and work on Strava summary activities (plus scanned
best efforts where available). Every model is a standard, published one,
chosen because each number it produces can be explained to the athlete:

- Training load: a TSS-style score, hours x intensity^2 x 100, so an hour at
  threshold effort scores about 100.
- Fitness / fatigue / form: exponentially weighted load over 42 and 7 days
  (the Banister impulse-response idea, as popularised by TrainingPeaks).
- Acute:chronic workload ratio from 7- and 28-day EWMAs. Spikes well above
  1.3 to 1.5 are commonly treated as a warning sign; the evidence behind
  exact thresholds is debated, so the app reports bands, not verdicts.
- Critical speed: the 2-parameter distance-time model D = CS * t + D'.
- Hilly runs are compared on flat-equivalent distance (see CLIMB_FACTOR).
- Race predictions: Riegel's formula T2 = T1 * (D2 / D1) ** 1.06.
- Trends: least-squares slopes with bootstrap confidence intervals.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from .stats import fmt_clock, local_start, sport_of

GROUPS = {
    "run": {"Run", "TrailRun", "VirtualRun"},
    "ride": {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"},
    "swim": {"Swim"},
    "hike": {"Hike", "Walk"},
}
GROUP_LABELS = {"run": "Running", "ride": "Cycling", "swim": "Swimming", "hike": "Hiking & walking",
                "all": "All activities"}
# Below these, whole-activity averages say too little to analyse.
MIN_SECONDS = {"run": 600, "ride": 1200, "swim": 300, "hike": 1200}

RIEGEL = 1.06
# Flat-equivalent distance: each metre climbed counts as 4 m on the flat. Climbing
# costs roughly 8-10x the metres on steep ground, but on a loop the descent gives
# a good part of that back, so for whole activities about 4 is a fair net value.
CLIMB_FACTOR = 4.0
RACE_DISTANCES = [("5k", 5000.0), ("10k", 10000.0), ("Half marathon", 21097.5),
                  ("Marathon", 42195.0)]
DEFAULT_INTENSITY = {"run": 0.75, "ride": 0.70, "swim": 0.75, "hike": 0.60, "other": 0.60}


def group_of(activity: dict) -> str:
    sport = sport_of(activity)
    for g, sports in GROUPS.items():
        if sport in sports:
            return g
    return "other"


def in_group(activity: dict, group: str) -> bool:
    return group == "all" or group_of(activity) == group


# ---------------------------------------------------------------------------
# Basic per-activity quantities
# ---------------------------------------------------------------------------
def flat_distance(a: dict) -> float:
    """Distance plus a climbing allowance, so hilly runs compare fairly."""
    return a.get("distance", 0) + CLIMB_FACTOR * (a.get("total_elevation_gain", 0) or 0)


def riegel(time_s: float, from_m: float, to_m: float, exponent: float = RIEGEL) -> float:
    return time_s * (to_m / from_m) ** exponent


def estimate_hr_max(activities: Iterable[dict]) -> float | None:
    """A robust estimate from the athlete's own recorded maxima (the 98th
    percentile, so one sensor glitch can't set it). Age-based formulas are
    avoided: they are wrong by 10+ bpm for many people."""
    values = [a["max_heartrate"] for a in activities if a.get("max_heartrate")]
    if len(values) < 10:
        return None
    return float(np.percentile(values, 98))


# ---------------------------------------------------------------------------
# Critical speed (running) and threshold power (cycling)
# ---------------------------------------------------------------------------
@dataclass
class CriticalSpeed:
    cs: float  # m/s
    d_prime: float  # m
    points: int
    r2: float

    @property
    def pace(self) -> str:
        return fmt_clock(1000 / self.cs) + " /km"


def critical_speed(runs: Iterable[dict], efforts: Iterable[dict] = (), now: datetime | None = None,
                   window_days: int = 120) -> CriticalSpeed | None:
    """Fit D = CS * t + D' to the athlete's best recent performances.

    Points are whole runs (on flat-equivalent distance) and scanned best
    efforts lasting 2 to 40 minutes, the range where the model holds. For
    each duration band only the fastest point is kept, so easy runs don't
    pull the fit down: the model describes what the athlete can do, not
    what they usually do."""
    now = now or datetime.now()
    since = now - timedelta(days=window_days)
    pts: list[tuple[float, float]] = []
    for a in runs:
        dt = local_start(a)
        t = a.get("moving_time", 0)
        if dt and dt >= since and 120 <= t <= 2400:
            pts.append((t, flat_distance(a)))
    for e in efforts:
        dt = local_start(e)
        t = e.get("elapsed_time", 0)
        if dt and dt >= since and 120 <= t <= 2400 and e.get("distance"):
            pts.append((t, e["distance"]))
    bands = [120, 240, 420, 660, 960, 1380, 1860, 2401]
    best: dict[int, tuple[float, float]] = {}
    for t, d in pts:
        band = next(i for i, edge in enumerate(bands[1:]) if t < edge)
        if band not in best or d / t > best[band][1] / best[band][0]:
            best[band] = (t, d)
    if len(best) < 3:
        return None
    t = np.array([p[0] for p in best.values()])
    d = np.array([p[1] for p in best.values()])
    if t.max() / t.min() < 2.5:
        return None
    cs, d_prime = np.polyfit(t, d, 1)
    if d_prime < 0:  # refit through the origin rather than report a negative D'
        cs, d_prime = float((t * d).sum() / (t * t).sum()), 0.0
    pred = cs * t + d_prime
    ss_res, ss_tot = float(((d - pred) ** 2).sum()), float(((d - d.mean()) ** 2).sum())
    if cs <= 0:
        return None
    return CriticalSpeed(float(cs), float(d_prime), len(best), 1 - ss_res / ss_tot if ss_tot else 1.0)


def ftp_estimate(rides: Iterable[dict], now: datetime | None = None, window_days: int = 120) -> float | None:
    """95% of the best weighted average power over rides of 40-90 minutes
    with a power meter. A rough estimate, labelled as one: without power
    streams the best 20-minute effort isn't available."""
    now = now or datetime.now()
    since = now - timedelta(days=window_days)
    vals = [a["weighted_average_watts"] for a in rides
            if a.get("weighted_average_watts") and a.get("device_watts")
            and 2400 <= a.get("moving_time", 0) <= 5400 and (local_start(a) or since) >= since]
    return 0.95 * max(vals) if vals else None


# ---------------------------------------------------------------------------
# Training load, fitness and fatigue
# ---------------------------------------------------------------------------
def intensity_factor(a: dict, *, cs: float | None = None, ftp: float | None = None,
                     hr_max: float | None = None) -> tuple[float, str]:
    """Effort relative to threshold, and what it was measured with."""
    g = group_of(a)
    t = a.get("moving_time", 0)
    if g == "ride" and ftp and a.get("weighted_average_watts") and a.get("device_watts"):
        return min(a["weighted_average_watts"] / ftp, 1.2), "power"
    if g == "run" and cs and t:
        return min((flat_distance(a) / t) / cs, 1.15), "pace"
    if hr_max and a.get("average_heartrate"):
        # Threshold sits near 88% of maximum heart rate for most trained people.
        return min((a["average_heartrate"] / hr_max) / 0.88, 1.15), "heart rate"
    return DEFAULT_INTENSITY.get(g, 0.6), "default"


def training_load(a: dict, **kw) -> float:
    intensity, _ = intensity_factor(a, **kw)
    return a.get("moving_time", 0) / 3600 * intensity ** 2 * 100


def daily_loads(activities: Iterable[dict], start: date, end: date, **kw) -> dict[date, float]:
    days = {start + timedelta(days=i): 0.0 for i in range((end - start).days + 1)}
    for a in activities:
        dt = local_start(a)
        if dt and start <= dt.date() <= end:
            days[dt.date()] += training_load(a, **kw)
    return days


def ewma(values: list[float], days: int) -> list[float]:
    out, level, k = [], 0.0, 1 / days
    for v in values:
        level += (v - level) * k
        out.append(level)
    return out


def fitness_series(loads: dict[date, float]) -> dict:
    """Fitness (42-day), fatigue (7-day), form (yesterday's fitness minus
    fatigue) and the 7:28 EWMA workload ratio."""
    dates = sorted(loads)
    vals = [loads[d] for d in dates]
    ctl, atl = ewma(vals, 42), ewma(vals, 7)
    acute = ewma(vals, 4)  # EWMA with lambda 2/(7+1), i.e. a 4-day time constant
    chronic = ewma(vals, 14.5)  # lambda 2/(28+1)
    tsb = [0.0] + [c - a for c, a in zip(ctl[:-1], atl[:-1], strict=True)]
    acwr = [a / c if c > 1 else None for a, c in zip(acute, chronic, strict=True)]
    return {"dates": dates, "load": vals, "fitness": ctl, "fatigue": atl, "form": tsb, "acwr": acwr}


def acwr_band(ratio: float | None) -> tuple[str, str]:
    if ratio is None:
        return "info", "not enough recent training to compare"
    if ratio < 0.8:
        return "info", "below your usual load (detraining or recovery)"
    if ratio <= 1.3:
        return "good", "in line with what you are used to"
    if ratio <= 1.5:
        return "watch", "above your usual load"
    return "warning", "a sharp spike over your usual load"


# ---------------------------------------------------------------------------
# Weekly volume
# ---------------------------------------------------------------------------
def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_volume(activities: Iterable[dict], weeks: int, now: datetime, **kw) -> list[dict]:
    first = week_start(now.date()) - timedelta(weeks=weeks - 1)
    out = {first + timedelta(weeks=i): {"distance": 0.0, "time": 0.0, "count": 0, "load": 0.0,
                                        "longest": 0.0}
           for i in range(weeks)}
    for a in activities:
        dt = local_start(a)
        if not dt:
            continue
        wk = week_start(dt.date())
        if wk in out:
            o = out[wk]
            o["distance"] += a.get("distance", 0)
            o["time"] += a.get("moving_time", 0)
            o["count"] += 1
            o["load"] += training_load(a, **kw)
            o["longest"] = max(o["longest"], a.get("distance", 0))
    return [{"week": wk.isoformat(), **v} for wk, v in sorted(out.items())]


# ---------------------------------------------------------------------------
# Performance over time
# ---------------------------------------------------------------------------
PROXY = {
    "run": ("Equivalent 10k pace", "pace", "lower is faster"),
    "ride_power": ("Best weighted power", "watts", "higher is stronger"),
    "ride": ("Typical flat-ish speed", "km/h", "higher is faster"),
    "swim": ("Best pace", "pace100", "lower is faster"),
    "hike": ("Climbing rate", "m/h", "higher is stronger"),
}


def performance_points(activities: Iterable[dict], group: str) -> tuple[str, list[tuple[datetime, float]]]:
    """One performance number per qualifying activity, comparable across
    activities of different length. Returns (proxy key, [(when, value)])."""
    acts = [a for a in activities if group_of(a) == group and local_start(a)]
    pts = []
    if group == "run":
        for a in acts:
            d, t = flat_distance(a), a.get("moving_time", 0)
            if 3000 <= d <= 30000 and t > 0:
                pts.append((local_start(a), riegel(t, d, 10000) / 10))  # seconds per km at 10k
        return "run", pts
    if group == "ride":
        powered = [a for a in acts if a.get("weighted_average_watts") and a.get("device_watts")
                   and a.get("moving_time", 0) >= 1800]
        if len(powered) >= 10:
            return "ride_power", [(local_start(a), float(a["weighted_average_watts"])) for a in powered]
        for a in acts:
            d, t = a.get("distance", 0), a.get("moving_time", 0)
            if t >= 1800 and d > 0 and (a.get("total_elevation_gain", 0) or 0) / (d / 1000) <= 10:
                pts.append((local_start(a), d / t * 3.6))
        return "ride", pts
    if group == "swim":
        for a in acts:
            d, t = a.get("distance", 0), a.get("moving_time", 0)
            if d >= 400 and t > 0:
                pts.append((local_start(a), t / (d / 100)))
        return "swim", pts
    if group == "hike":
        for a in acts:
            gain, t = a.get("total_elevation_gain", 0) or 0, a.get("moving_time", 0)
            if gain >= 300 and t > 0:
                pts.append((local_start(a), gain / (t / 3600)))
        return "hike", pts
    return "", []


def lower_is_better(proxy: str) -> bool:
    return proxy in ("run", "swim")


def monthly_best(points: list[tuple[datetime, float]], proxy: str, min_count: int = 2) -> list[dict]:
    """Best value per month (what the athlete could do that month). Months
    with too few qualifying activities are left out rather than guessed."""
    by_month: dict[str, list[float]] = defaultdict(list)
    for dt, v in points:
        by_month[dt.strftime("%Y-%m")].append(v)
    pick = min if lower_is_better(proxy) else max
    if proxy == "ride":  # speed depends on wind and company; use a high percentile, not the max
        def pick(vals):
            return float(np.percentile(vals, 90))
    return [{"month": m, "value": float(pick(v)), "count": len(v)}
            for m, v in sorted(by_month.items()) if len(v) >= min_count]


def month_index(month: str) -> float:
    y, m = month.split("-")
    return int(y) * 12 + int(m) - 1


def trend(series: list[dict], months: int = 12, n_boot: int = 1000, seed: int = 0) -> dict | None:
    """Relative change per month over the last `months`, with a 95% bootstrap
    interval. Relative units make the trend comparable across sports."""
    recent = series[-months:]
    if len(recent) < 4:
        return None
    x = np.array([month_index(s["month"]) for s in recent])
    y = np.array([s["value"] for s in recent])
    x = x - x.mean()
    slope = float(np.polyfit(x, y, 1)[0])
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(x), len(x))
        if np.ptp(x[idx]) == 0:
            continue
        boots.append(np.polyfit(x[idx], y[idx], 1)[0])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    mean = float(y.mean())
    return {"slope": slope, "pct_per_month": 100 * slope / mean, "ci_pct": (100 * lo / mean, 100 * hi / mean),
            "months": len(recent)}


def efficiency_points(runs: Iterable[dict], hr_max: float | None) -> list[tuple[datetime, float]]:
    """Aerobic efficiency: flat-equivalent metres per minute per heartbeat,
    on easy runs only (average HR below 85% of max), where it reflects
    aerobic fitness rather than effort."""
    if not hr_max:
        return []
    out = []
    for a in runs:
        hr, t = a.get("average_heartrate"), a.get("moving_time", 0)
        if hr and t >= 1200 and hr < 0.85 * hr_max:
            out.append((local_start(a), (flat_distance(a) / (t / 60)) / hr))
    return out


def intensity_mix(activities: Iterable[dict], *, months: int, now: datetime, **kw) -> list[dict]:
    """Share of training time per month at low, moderate and high intensity,
    from each activity's average. Intervals average out to 'moderate', so
    this undercounts hard work; it is a guide to the overall balance."""
    first = date(now.year, now.month, 1)
    keys = []
    for i in range(months - 1, -1, -1):
        idx = first.year * 12 + first.month - 1 - i
        keys.append(f"{idx // 12}-{idx % 12 + 1:02d}")
    mix = {k: {"low": 0.0, "moderate": 0.0, "high": 0.0} for k in keys}
    for a in activities:
        dt = local_start(a)
        if not dt or dt.strftime("%Y-%m") not in mix:
            continue
        f, _ = intensity_factor(a, **kw)
        zone = "low" if f < 0.85 else "moderate" if f < 0.95 else "high"
        mix[dt.strftime("%Y-%m")][zone] += a.get("moving_time", 0) / 3600
    return [{"month": k, **{z: round(v, 2) for z, v in mix[k].items()}} for k in keys]


# ---------------------------------------------------------------------------
# Race predictions
# ---------------------------------------------------------------------------
def race_predictions(runs: list[dict], efforts: list[dict], now: datetime,
                     window_days: int = 120) -> list[dict]:
    """Predicted times from the athlete's three strongest recent
    performances (whole runs and scanned best efforts), via Riegel. The
    spread between anchors is reported as the range."""
    since = now - timedelta(days=window_days)
    anchors = []
    for a in runs:
        dt = local_start(a)
        d, t = flat_distance(a), a.get("moving_time", 0)
        if dt and dt >= since and d >= 3000 and t > 0:
            anchors.append((riegel(t, d, 10000), d, t, a.get("name", "")))
    for e in efforts:
        dt = local_start(e)
        d, t = e.get("distance") or 0, e.get("elapsed_time") or 0
        if dt and dt >= since and d >= 1609 and t > 0:
            anchors.append((riegel(t, d, 10000), d, t, e.get("name", "")))
    anchors.sort()
    anchors = anchors[:3]
    if not anchors:
        return []
    longest_recent = max((a.get("distance", 0) for a in runs
                          if (local_start(a) or since) >= now - timedelta(days=56)), default=0)
    out = []
    for name, metres in RACE_DISTANCES:
        times = [riegel(t, d, metres) for _, d, t, _ in anchors]
        stretch = max(metres / d for _, d, _, _ in anchors)
        note = None
        if stretch > 4:
            note = "extrapolated a long way from your recent efforts"
        if metres >= 30000 and longest_recent < 25000:
            note = "optimistic: no run over 25 km in the last 8 weeks"
        out.append({"name": name, "distance": metres, "seconds": float(np.median(times)),
                    "time": fmt_clock(float(np.median(times))),
                    "range": [fmt_clock(min(times)), fmt_clock(max(times))],
                    "pace": fmt_clock(float(np.median(times)) / (metres / 1000)) + " /km",
                    "note": note})
    return out


def pace_zones(cs: float) -> list[dict]:
    """Running zones as fractions of critical speed. CS sits close to the
    pace an athlete can hold for about 30-60 minutes."""
    zones = [("Easy", 0.70, 0.82), ("Steady", 0.82, 0.90), ("Threshold", 0.90, 0.98),
             ("Critical speed", 0.98, 1.04), ("VO2max", 1.04, 1.12)]
    return [{"zone": z, "from": fmt_clock(1000 / (cs * hi)), "to": fmt_clock(1000 / (cs * lo)),
             "low": lo, "high": hi} for z, lo, hi in zones]


def fmt_proxy(value: float, proxy: str) -> str:
    if proxy == "run":
        return fmt_clock(value) + " /km"
    if proxy == "swim":
        return fmt_clock(value) + " /100m"
    if proxy == "ride_power":
        return f"{value:.0f} W"
    if proxy == "ride":
        return f"{value:.1f} km/h"
    if proxy == "hike":
        return f"{value:.0f} m/h"
    return f"{value:.2f}"


def is_finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))
