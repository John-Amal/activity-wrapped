"""Turn a list of Strava summary activities into "wrapped" statistics.

Everything here is a pure function of the activity list and the chosen
options (period, sports, units), so it is fully testable without the API.
Each stat returns raw numbers alongside display strings; the renderer only
ever uses the display strings, so units and formatting live in one place.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Strava's best_efforts (named PRs such as 5k or Half-Marathon) only exist on runs.
BEST_EFFORT_SPORTS = {"Run", "TrailRun", "VirtualRun"}
# Sports where pace (time per distance) is the natural measure rather than speed.
PACE_SPORTS = BEST_EFFORT_SPORTS | {"Walk", "Hike"}
SWIM_SPORTS = {"Swim"}

UNITS = {
    "metric": {"dist_m": 1000.0, "dist": "km", "elev_m": 1.0, "elev": "m", "speed": "km/h",
               "swim_m": 100.0, "swim": "/100m"},
    "imperial": {"dist_m": 1609.344, "dist": "mi", "elev_m": 0.3048, "elev": "ft", "speed": "mph",
                 "swim_m": 91.44, "swim": "/100yd"},
}

WEEKDAYS = list(calendar.day_name)
TIME_OF_DAY = [(5, "early morning"), (9, "morning"), (12, "midday"), (14, "afternoon"),
               (18, "evening"), (22, "night")]

SPORT_LABELS = {
    "TrailRun": "Trail Run", "VirtualRun": "Virtual Run", "VirtualRide": "Virtual Ride",
    "GravelRide": "Gravel Ride", "MountainBikeRide": "MTB", "EBikeRide": "E-Bike Ride",
    "WeightTraining": "Weights", "AlpineSki": "Alpine Ski", "NordicSki": "Nordic Ski",
    "BackcountrySki": "Backcountry Ski", "StandUpPaddling": "SUP", "RockClimbing": "Climbing",
}


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Period:
    """'last12' = the current month and the 11 before it (so the monthly chart
    and the totals always cover exactly the same activities), 'year:2025' =
    one calendar year, 'all' = everything."""

    kind: str
    year: int | None = None

    @classmethod
    def parse(cls, value: str | None) -> Period:
        value = (value or "last12").strip().lower()
        if value in ("last12", "all"):
            return cls(value)
        if value.startswith("year:") and value[5:].isdigit():
            return cls("year", int(value[5:]))
        raise ValueError(f"Unknown period '{value}'. Use last12, all or year:YYYY.")

    def bounds(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        if self.kind == "year":
            return datetime(self.year, 1, 1), datetime(self.year + 1, 1, 1)
        if self.kind == "last12":
            start = add_months(date(now.year, now.month, 1), -11)
            return datetime(start.year, start.month, 1), None
        return None, None

    def label(self, now: datetime) -> str:
        if self.kind == "year":
            return str(self.year)
        if self.kind == "last12":
            return "last 12 months"
        return "all time"

    @property
    def key(self) -> str:
        return f"year:{self.year}" if self.kind == "year" else self.kind


def add_months(d: date, months: int) -> date:
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def sport_of(activity: dict) -> str:
    return activity.get("sport_type") or activity.get("type") or "Other"


def sport_label(sport: str) -> str:
    return SPORT_LABELS.get(sport, sport)


def local_start(activity: dict) -> datetime | None:
    # start_date_local carries a misleading trailing 'Z'; it is local wall time.
    raw = activity.get("start_date_local") or activity.get("start_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:19])
    except ValueError:
        return None


def filter_activities(activities: Iterable[dict], period: Period, sports: Iterable[str] | None,
                      now: datetime) -> list[dict]:
    start, end = period.bounds(now)
    wanted = set(sports or [])
    out = []
    for a in activities:
        dt = local_start(a)
        if dt is None:
            continue
        if start and dt < start:
            continue
        if end and dt >= end:
            continue
        if wanted and sport_of(a) not in wanted:
            continue
        out.append(a)
    return out


def available_years(activities: Iterable[dict]) -> list[int]:
    return sorted({dt.year for a in activities if (dt := local_start(a))}, reverse=True)


def sport_counts(activities: Iterable[dict]) -> list[dict]:
    counts = Counter(sport_of(a) for a in activities)
    return [{"sport": s, "label": sport_label(s), "count": n} for s, n in counts.most_common()]


def fmt_number(x: float, decimals: int = 0) -> str:
    return f"{x:,.{decimals}f}"


def fmt_hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours >= 10:
        return f"{fmt_number(hours)} h"
    h, rem = divmod(int(round(seconds)), 3600)
    return f"{h}h {rem // 60:02d}m"


def fmt_clock(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace_or_speed(sport: str, distance_m: float, moving_s: float, units: str) -> str | None:
    if distance_m <= 0 or moving_s <= 0:
        return None
    u = UNITS[units]
    if sport in SWIM_SPORTS:
        return f"{fmt_clock(moving_s / (distance_m / u['swim_m']))} {u['swim']}"
    if sport in PACE_SPORTS:
        return f"{fmt_clock(moving_s / (distance_m / u['dist_m']))} /{u['dist']}"
    return f"{distance_m / u['dist_m'] / (moving_s / 3600):.1f} {u['speed']}"


def eddington(daily_distances: Iterable[float]) -> int:
    """Largest E such that there are at least E days with a distance of at
    least E (in the chosen unit)."""
    e = 0
    for i, d in enumerate(sorted(daily_distances, reverse=True), start=1):
        if d >= i:
            e = i
        else:
            break
    return e


def longest_streak(days: Iterable[date]) -> tuple[int, date | None, date | None]:
    ordered = sorted(set(days))
    if not ordered:
        return 0, None, None
    best = (1, ordered[0], ordered[0])
    run_start = prev = ordered[0]
    for d in ordered[1:]:
        if d - prev != timedelta(days=1):
            run_start = d
        length = (d - run_start).days + 1
        if length > best[0]:
            best = (length, run_start, d)
        prev = d
    return best


def time_of_day(hour: int) -> str:
    label = "night"
    for start, name in TIME_OF_DAY:
        if hour >= start:
            label = name
    return label


def truncate(text: str, n: int = 40) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def _fmt_date(d: date | datetime | None) -> str:
    return f"{d.day} {d.strftime('%b %Y')}" if d else ""


# ---------------------------------------------------------------------------
# Monthly / yearly series for the bar chart
# ---------------------------------------------------------------------------
def distance_series(acts: list[dict], period: Period, now: datetime, units: str) -> dict:
    u = UNITS[units]
    if period.kind == "all":
        years = sorted({local_start(a).year for a in acts})
        keys = list(range(years[0], years[-1] + 1)) if years else []
        totals = defaultdict(float)
        for a in acts:
            totals[local_start(a).year] += a.get("distance", 0)
        labels = [f"'{y % 100:02d}" for y in keys]
        values = [totals[k] / u["dist_m"] for k in keys]
        title = "distance by year"
    else:
        if period.kind == "year":
            months = [date(period.year, m, 1) for m in range(1, 13)]
        else:
            first = add_months(date(now.year, now.month, 1), -11)
            months = [add_months(first, i) for i in range(12)]
        totals = defaultdict(float)
        for a in acts:
            dt = local_start(a)
            totals[(dt.year, dt.month)] += a.get("distance", 0)
        labels = [calendar.month_abbr[m.month][0] for m in months]
        values = [totals[(m.year, m.month)] / u["dist_m"] for m in months]
        title = "distance by month"
    return {"title": title, "labels": labels, "values": [round(v, 1) for v in values],
            "unit": u["dist"]}


# ---------------------------------------------------------------------------
# Named PRs from activity details
# ---------------------------------------------------------------------------
def best_effort_candidates(acts: list[dict]) -> list[dict]:
    """Runs whose details could hold best efforts, fastest first. Fast runs
    are the most likely to contain the period's best efforts, so scanning in
    this order finds most PRs within a limited API budget."""
    runs = [a for a in acts if sport_of(a) in BEST_EFFORT_SPORTS
            and a.get("distance", 0) >= 400 and a.get("moving_time", 0) > 0]
    return sorted(runs, key=lambda a: a["distance"] / a["moving_time"], reverse=True)


def best_efforts(details: Iterable[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for d in details:
        for e in d.get("best_efforts") or []:
            name, t = e.get("name"), e.get("elapsed_time")
            if not name or not t:
                continue
            if name not in best or t < best[name]["seconds"]:
                best[name] = {
                    "name": name,
                    "distance_m": e.get("distance") or 0,
                    "seconds": t,
                    "time": fmt_clock(t),
                    "activity": truncate(d.get("name") or "", 28),
                    "date": _fmt_date(local_start(d)),
                    "all_time_pr": e.get("pr_rank") == 1,
                }
    return sorted(best.values(), key=lambda x: x["distance_m"])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def compute_stats(activities: list[dict], *, period: Period, sports: Iterable[str] | None = None,
                  units: str = "metric", now: datetime | None = None,
                  details: dict[int, dict] | None = None) -> dict:
    if units not in UNITS:
        raise ValueError(f"Unknown units '{units}'. Use metric or imperial.")
    now = now or datetime.now()
    u = UNITS[units]
    sports = sorted(set(sports or []))
    acts = filter_activities(activities, period, sports, now)

    out: dict = {
        "period": period.key,
        "period_label": period.label(now),
        "sports": sports,
        "sports_label": ", ".join(sport_label(s) for s in sports) if sports else "all sports",
        "units": units,
        "count": len(acts),
        "empty": not acts,
    }
    if not acts:
        return out

    dist_m = sum(a.get("distance", 0) for a in acts)
    moving_s = sum(a.get("moving_time", 0) for a in acts)
    elev_m = sum(a.get("total_elevation_gain", 0) for a in acts)
    by_day: dict[date, float] = defaultdict(float)
    for a in acts:
        by_day[local_start(a).date()] += a.get("distance", 0)

    out["totals"] = {
        "distance": f"{fmt_number(dist_m / u['dist_m'])} {u['dist']}",
        "distance_value": round(dist_m / u["dist_m"], 1),
        "moving_time": fmt_hours(moving_s),
        "elevation": f"{fmt_number(elev_m / u['elev_m'])} {u['elev']}",
        "active_days": len(by_day),
        "count": len(acts),
    }

    # Per-sport breakdown. Pace/speed is only meaningful within one sport, so
    # it is reported per sport rather than averaged across them.
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in acts:
        groups[sport_of(a)].append(a)
    by_sport = []
    for sport, items in groups.items():
        d = sum(a.get("distance", 0) for a in items)
        t = sum(a.get("moving_time", 0) for a in items)
        by_sport.append({
            "sport": sport,
            "label": sport_label(sport),
            "count": len(items),
            "distance_value": round(d / u["dist_m"], 1),
            "distance": f"{fmt_number(d / u['dist_m'])} {u['dist']}",
            "moving_time": fmt_hours(t),
            "pace": pace_or_speed(sport, d, t, units),
        })
    by_sport.sort(key=lambda s: (s["distance_value"], s["count"]), reverse=True)
    out["by_sport"] = by_sport
    primary = next((s for s in by_sport if s["pace"]), None)
    out["pace"] = ({"sport": primary["label"], "value": primary["pace"],
                    "label": "avg pace" if primary["sport"] in PACE_SPORTS | SWIM_SPORTS
                    else "avg speed"}
                   if primary else None)

    out["series"] = distance_series(acts, period, now, units)

    longest = max(acts, key=lambda a: a.get("distance", 0))
    out["longest"] = {
        "value": f"{longest.get('distance', 0) / u['dist_m']:.1f} {u['dist']}",
        "name": truncate(longest.get("name", "")),
        "sport": sport_label(sport_of(longest)),
        "date": _fmt_date(local_start(longest)),
    }
    climb = max(acts, key=lambda a: a.get("total_elevation_gain", 0))
    out["climb"] = ({
        "value": f"{fmt_number(climb['total_elevation_gain'] / u['elev_m'])} {u['elev']}",
        "name": truncate(climb.get("name", "")),
        "sport": sport_label(sport_of(climb)),
        "date": _fmt_date(local_start(climb)),
    } if climb.get("total_elevation_gain", 0) > 0 else None)

    months = Counter(local_start(a).strftime("%Y-%m") for a in acts)
    busiest, busiest_n = months.most_common(1)[0]
    out["busiest_month"] = {
        "value": datetime.strptime(busiest, "%Y-%m").strftime("%B %Y"),
        "count": busiest_n,
    }

    weekday = Counter(local_start(a).weekday() for a in acts).most_common(1)[0][0]
    tod = Counter(time_of_day(local_start(a).hour) for a in acts).most_common(1)[0][0]
    out["habits"] = {"weekday": WEEKDAYS[weekday], "time_of_day": tod}

    streak, s_from, s_to = longest_streak(by_day.keys())
    out["streak"] = {"days": streak, "from": _fmt_date(s_from), "to": _fmt_date(s_to)}

    out["eddington"] = {
        "value": eddington(d / u["dist_m"] for d in by_day.values()),
        "unit": u["dist"],
    }

    out["achievements"] = {
        "achievements": sum(a.get("achievement_count", 0) or 0 for a in acts),
        "prs": sum(a.get("pr_count", 0) or 0 for a in acts),
    }

    names = Counter(a["name"].strip() for a in acts if a.get("name"))
    if names:
        name, n = names.most_common(1)[0]
        out["repeated_name"] = {"name": truncate(name), "count": n} if n > 1 else None
    else:
        out["repeated_name"] = None

    # Named PRs, from whatever run details have been scanned so far, with an
    # explicit coverage figure so the card never implies more than it knows.
    candidates = best_effort_candidates(acts)
    details = details or {}
    scanned = [details[a["id"]] for a in candidates if a.get("id") in details]
    out["best_efforts"] = {
        "efforts": best_efforts(scanned),
        "scanned": len(scanned),
        "total": len(candidates),
    }
    return out
