"""Synthetic athlete for demo mode.

Lets anyone try the app (and lets the tests and CI exercise the full stack)
without Strava credentials. The generator is seeded, so the demo card is
identical on every run. It mirrors the shape of Strava's summary and
detailed activity payloads: summary polylines, best efforts with PR ranks,
segment efforts, splits and heart rate.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from .routes import encode_polyline

# (sport, sessions per week, typical distance km, spread, speed km/h, metres climbed per km)
PROFILE = [
    ("Run", 3.0, 9.0, 0.45, 11.5, 9),
    ("TrailRun", 0.6, 15.0, 0.5, 8.5, 45),
    ("Ride", 1.2, 45.0, 0.5, 26.0, 11),
    ("Swim", 0.8, 1.8, 0.3, 3.0, 0),
    ("Hike", 0.3, 12.0, 0.4, 4.2, 70),
]

# Standard Strava best-effort distances for runs.
EFFORTS = [("400m", 400), ("1/2 mile", 805), ("1k", 1000), ("1 mile", 1609), ("2 mile", 3219),
           ("5k", 5000), ("10k", 10000), ("15k", 15000), ("10 mile", 16090),
           ("20k", 20000), ("Half-Marathon", 21097), ("Marathon", 42195)]


HOME = (46.948, 7.447)  # the demo athlete lives in Bern
AWAY = (45.924, 6.869)  # and goes to Chamonix now and then
GPS_SPORTS = {"Run", "TrailRun", "Ride", "Hike"}
SPLIT_SPORTS = {"Run", "TrailRun", "Hike"}
SEGMENTS = {
    "Run": [("Aare riverside", 1800, 0.2), ("Gurten climb", 2600, 6.8), ("Bridge sprint", 400, 0.5),
            ("Park loop", 1200, 1.1)],
    "TrailRun": [("Gurten climb", 2600, 6.8), ("Forest single-track", 3100, 3.9)],
    "Ride": [("Lake road", 8200, 0.4), ("Belpberg climb", 4300, 5.6), ("Valley sprint", 900, 0.8)],
}


def _route(distance_m: float, rng: random.Random) -> list[tuple[float, float]]:
    """A smooth, irregular loop of roughly the right length that starts and
    ends at home (or, now and then, somewhere else)."""
    origin = AWAY if rng.random() < 0.04 else HOME
    radius = distance_m / (2 * math.pi * 1.15)
    harmonics = [(k, rng.uniform(0.05, 0.25) / k, rng.uniform(0, 2 * math.pi)) for k in (2, 3, 5)]
    rotation = rng.uniform(0, 2 * math.pi)
    n = 140
    xy = []
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        r = radius * (1 + sum(a * math.sin(k * t + ph) for k, a, ph in harmonics))
        xy.append((r * math.cos(t + rotation) - radius * math.cos(rotation),
                   r * math.sin(t + rotation) - radius * math.sin(rotation)))
    x0, y0 = xy[0]
    lat0, lon0 = origin
    m_per_deg_lon = 111_320 * math.cos(math.radians(lat0))
    return [(lat0 + (y - y0) / 111_320, lon0 + (x - x0) / m_per_deg_lon) for x, y in xy]


def _enrich(a: dict) -> None:
    """Route and heart rate, drawn from a per-activity seed so they don't
    disturb the main generator (and so the demo stays identical run to run)."""
    rng = random.Random(a["id"] * 7919)
    sport = a["sport_type"]
    a["map"] = {"summary_polyline": encode_polyline(_route(a["distance"], rng))
                if sport in GPS_SPORTS else ""}
    effort, fitness = a.pop("_effort", 1.0), a.pop("_fitness", 1.0)
    if sport != "Swim":
        # Heart rate follows effort, and drops a little for the same effort as fitness improves.
        hr = 112 + 52 * (effort - 0.8) / 0.4 - 10 * (fitness - 1) + rng.uniform(-4, 4)
        a["average_heartrate"] = round(min(182, max(100, hr)), 1)
        a["max_heartrate"] = round(min(193, a["average_heartrate"] + rng.uniform(9, 24)), 1)
        a["has_heartrate"] = True
    if sport in ("Run", "TrailRun"):
        a["average_cadence"] = round(rng.uniform(83, 89) + 3 * (effort - 1), 1)
    if sport == "Ride":
        watts = 175 * fitness * (effort ** 2.2) * rng.uniform(0.95, 1.05)
        a["average_watts"] = round(watts * 0.93, 1)
        a["weighted_average_watts"] = round(watts)
        a["device_watts"] = True


def _name(sport: str, dt: datetime, rng: random.Random) -> str:
    if rng.random() < 0.15:
        return rng.choice(["Lake loop", "Hill reps", "Long one", "Commute", "Sunday social",
                           "Intervals", "Recovery", "Uetliberg"])
    part = "Morning" if dt.hour < 12 else "Afternoon" if dt.hour < 17 else "Evening"
    noun = {"TrailRun": "Trail Run"}.get(sport, sport)
    return f"{part} {noun}"


def generate_activities(now: datetime | None = None, years: float = 3.0,
                        seed: int = 7) -> list[dict]:
    now = now or datetime.now()
    rng = random.Random(seed)
    start = now - timedelta(days=int(365 * years))
    acts: list[dict] = []
    next_id = 10_000_000
    day = start
    while day <= now:
        season = 1.0 + 0.35 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 100) / 365)
        weekend = day.weekday() >= 5
        for sport, per_week, km, spread, speed, climb in PROFILE:
            p = per_week / 7 * season * (1.6 if weekend and sport != "Swim" else 0.85)
            if rng.random() > p:
                continue
            dist_km = max(0.4, rng.lognormvariate(math.log(km), spread))
            if weekend and sport == "Run" and rng.random() < 0.2:
                dist_km *= 1.8  # the weekend long run
            # Slow improvement over the years, plus being sharper in the
            # high-volume part of the season, so fitness and form relate to
            # training load the way they do for real athletes.
            fitness = (1 + 0.04 * (day - start).days / 365) * (1 + 0.03 * (season - 1) / 0.35)
            effort = rng.uniform(0.88, 1.12) * (0.93 if dist_km > 2 * km else 1)
            if sport in ("Run", "TrailRun"):
                effort *= (dist_km / km) ** -0.06  # shorter runs are run faster (as in Riegel's law)
            if sport in ("Run", "Ride") and rng.random() < 0.18:  # a quality session
                effort *= 1.09
                dist_km *= 0.75
            v = speed * fitness * effort
            moving = dist_km / v * 3600
            hour = rng.choice([6, 7, 7, 8, 12, 17, 18, 18, 19]) if not weekend else rng.choice([8, 9, 10])
            dt = day.replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)
            if dt > now:
                continue
            next_id += 1
            acts.append({
                "id": next_id,
                "name": _name(sport, dt, rng),
                "type": "Run" if sport == "TrailRun" else sport,
                "sport_type": sport,
                "start_date_local": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "distance": round(dist_km * 1000, 1),
                "moving_time": int(moving),
                "elapsed_time": int(moving * rng.uniform(1.0, 1.15)),
                "total_elevation_gain": round(dist_km * climb * rng.uniform(0.5, 1.6), 1),
                "achievement_count": rng.choice([0, 0, 0, 1, 2, 3]) if sport != "Swim" else 0,
                "pr_count": rng.choice([0, 0, 0, 0, 1, 2]) if sport in ("Run", "TrailRun") else 0,
                "_effort": effort,
                "_fitness": fitness,
            })
        day += timedelta(days=1)
    for a in acts:
        _enrich(a)
    acts.sort(key=lambda a: a["start_date_local"], reverse=True)
    return acts


def _best_efforts(a: dict) -> list[dict]:
    rng = random.Random(a["id"])
    avg_speed = a["distance"] / a["moving_time"]  # m/s
    efforts = []
    for name, metres in EFFORTS:
        if metres > a["distance"]:
            break
        # Shorter efforts are run faster than the activity average.
        boost = 1 + 0.12 * max(0.0, 1 - metres / a["distance"]) * rng.uniform(0.6, 1.2)
        efforts.append({"name": name, "distance": metres,
                        "elapsed_time": int(metres / (avg_speed * boost)), "pr_rank": None})
    return efforts


class DemoSource:
    """Same interface as StravaClient, backed by generated data."""

    is_demo = True
    athlete_name = "Demo Athlete"
    rate_limit = None

    def __init__(self, now: datetime | None = None) -> None:
        self._activities = generate_activities(now)
        self._by_id = {a["id"]: a for a in self._activities}
        self._efforts = self._rank_efforts()

    def _rank_efforts(self) -> dict[int, list[dict]]:
        """Best efforts for every run, with PR ranks assigned the way Strava
        does: 1, 2 or 3 if the effort was among the athlete's three fastest
        at that distance up to that day."""
        ranked: dict[int, list[dict]] = {}
        history: dict[str, list[int]] = {}
        runs = [a for a in self._activities if a["sport_type"] in ("Run", "TrailRun")]
        for a in sorted(runs, key=lambda x: x["start_date_local"]):
            efforts = _best_efforts(a)
            for e in efforts:
                times = sorted(history.setdefault(e["name"], []) + [e["elapsed_time"]])
                rank = times.index(e["elapsed_time"]) + 1
                e["pr_rank"] = rank if rank <= 3 else None
                history[e["name"]] = times
            ranked[a["id"]] = efforts
        return ranked

    def list_activities(self) -> list[dict]:
        return list(self._activities)

    def is_cached(self, activity_id: int) -> bool:
        return False

    def get_activity_details(self, activity_id: int) -> dict:
        a = self._by_id[activity_id]
        rng = random.Random(activity_id + 1)
        sport = a["sport_type"]
        speed = a["distance"] / a["moving_time"]
        detail = {
            "id": a["id"], "name": a["name"], "start_date_local": a["start_date_local"],
            "average_heartrate": a.get("average_heartrate"),
            "max_heartrate": round(a["average_heartrate"] + rng.uniform(12, 28))
            if a.get("average_heartrate") else None,
            "average_watts": round(rng.uniform(170, 230)) if sport == "Ride" else None,
            "average_cadence": round(rng.uniform(82, 90)) if sport in SPLIT_SPORTS else None,
            "calories": round(a["moving_time"] / 60 * rng.uniform(9, 13)),
            "best_efforts": self._efforts.get(activity_id, []),
            "segment_efforts": [],
            "splits_metric": [],
            "splits_standard": [],
        }
        for name, metres, grade in SEGMENTS.get(sport, []):
            if metres < a["distance"] and rng.random() < 0.6:
                seg_speed = speed * rng.uniform(0.9, 1.15) * (1 - grade / 30)
                detail["segment_efforts"].append({
                    "name": name, "distance": metres, "elapsed_time": int(metres / seg_speed),
                    "pr_rank": rng.choice([None, None, None, None, 1, 2, 3]),
                    "kom_rank": None, "average_grade": grade})
        if sport in SPLIT_SPORTS:
            for unit, key in ((1000.0, "splits_metric"), (1609.344, "splits_standard")):
                remaining, n = a["distance"], 0
                while remaining > 50:
                    d = min(unit, remaining)
                    s = speed * rng.uniform(0.93, 1.07) * (0.97 if n > a["distance"] / unit * 0.7 else 1)
                    detail[key].append({"distance": round(d, 1), "moving_time": int(d / s),
                                        "elapsed_time": int(d / s), "elevation_difference":
                                        round(rng.uniform(-12, 12), 1),
                                        "average_heartrate": round(a["average_heartrate"]
                                                                   + rng.uniform(-6, 8), 1)})
                    remaining -= d
                    n += 1
        return detail
