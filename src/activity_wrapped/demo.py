"""Synthetic athlete for demo mode.

Lets anyone try the app (and lets the tests and CI exercise the full stack)
without Strava credentials. The generator is seeded, so the demo card is
identical on every run. It mirrors the shape of Strava's summary and
detailed activity payloads, including best_efforts on runs.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

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
            fitness = 1 + 0.04 * (day - start).days / 365  # slow improvement over time
            v = speed * fitness * rng.uniform(0.88, 1.12) * (0.93 if dist_km > 2 * km else 1)
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
            })
        day += timedelta(days=1)
    acts.sort(key=lambda a: a["start_date_local"], reverse=True)
    return acts


class DemoSource:
    """Same interface as StravaClient, backed by generated data."""

    is_demo = True
    athlete_name = "Demo Athlete"
    rate_limit = None

    def __init__(self, now: datetime | None = None) -> None:
        self._activities = generate_activities(now)
        self._by_id = {a["id"]: a for a in self._activities}

    def list_activities(self) -> list[dict]:
        return list(self._activities)

    def is_cached(self, activity_id: int) -> bool:
        return False

    def get_activity_details(self, activity_id: int) -> dict:
        a = self._by_id[activity_id]
        rng = random.Random(activity_id)
        avg_speed = a["distance"] / a["moving_time"]  # m/s
        efforts = []
        for name, metres in EFFORTS:
            if metres > a["distance"]:
                break
            # Shorter efforts are run faster than the activity average.
            boost = 1 + 0.12 * max(0.0, 1 - metres / a["distance"]) * rng.uniform(0.6, 1.2)
            efforts.append({"name": name, "distance": metres,
                            "elapsed_time": int(metres / (avg_speed * boost)), "pr_rank": None})
        return {"id": a["id"], "name": a["name"], "start_date_local": a["start_date_local"],
                "best_efforts": efforts}
