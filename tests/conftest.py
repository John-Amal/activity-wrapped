from datetime import datetime

import pytest

NOW = datetime(2026, 10, 5, 12, 0)


def act(id, when, sport="Run", km=10.0, minutes=50.0, climb=50.0, name=None, **extra):
    return {
        "id": id,
        "name": name or f"Morning {sport}",
        "sport_type": sport,
        "type": sport,
        "start_date_local": when + "Z" if "T" in when else when + "T07:00:00Z",
        "distance": km * 1000,
        "moving_time": minutes * 60,
        "total_elevation_gain": climb,
        **extra,
    }


@pytest.fixture
def now():
    return NOW
