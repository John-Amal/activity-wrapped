"""GPS route handling: polyline decoding, privacy trimming and main-area selection.

Strava returns routes as Google encoded polylines (a summary version on
every activity in the list, a full-resolution one in the activity detail),
so drawing maps costs no extra API calls beyond what the app already makes.

Projection and the optional map background live in tiles.py.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence

Point = tuple[float, float]  # (lat, lon)

EARTH_RADIUS_M = 6_371_000.0
DEFAULT_PRIVACY_M = 200


def decode_polyline(encoded: str | None, precision: int = 5) -> list[Point]:
    if not encoded:
        return []
    points: list[Point] = []
    index = lat = lon = 0
    factor = 10 ** precision
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / factor, lon / factor))
    return points


def encode_polyline(points: Iterable[Point], precision: int = 5) -> str:
    factor = 10 ** precision
    out = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat, ilon = round(lat * factor), round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def haversine_m(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def trim_ends(points: Sequence[Point], metres: float) -> list[Point]:
    """Drop every point within `metres` of the start or the end of the route,
    measured as straight-line distance. Most activities start and finish at
    home, so this hides the exact location on a shared card. It works like
    Strava's own privacy zones, but is applied to every route the app draws."""
    if metres <= 0 or len(points) < 2:
        return list(points)
    start, end = points[0], points[-1]
    kept = [p for p in points if haversine_m(p, start) > metres and haversine_m(p, end) > metres]
    return kept if len(kept) >= 2 else []


def main_area(routes: Sequence[Sequence[Point]], radius_km: float = 25.0) -> list[int]:
    """Indices of the routes in the athlete's main area.

    One holiday route far from home would shrink every other route to a dot
    if all were drawn at the same scale. Start points are binned on a coarse
    grid; the busiest cell is taken as the main area, and only routes that
    start within `radius_km` of its centre are kept."""
    starts = [(i, r[0]) for i, r in enumerate(routes) if r]
    if not starts:
        return []
    cells = Counter((round(p[0] * 4), round(p[1] * 4)) for _, p in starts)  # ~0.25 degree cells
    (clat, clon), _ = cells.most_common(1)[0]
    centre = (clat / 4, clon / 4)
    return [i for i, p in starts if haversine_m(p, centre) <= radius_km * 1000]


def summary_route(activity: dict) -> list[Point]:
    return decode_polyline((activity.get("map") or {}).get("summary_polyline"))


def prepare_routes(activities: Iterable[dict], privacy_m: float = DEFAULT_PRIVACY_M) -> dict:
    """Decoded, privacy-trimmed routes for the year card, limited to the main area."""
    routes = [r for r in (trim_ends(summary_route(a), privacy_m) for a in activities) if r]
    keep = main_area(routes)
    return {"routes": [routes[i] for i in keep], "shown": len(keep), "total": len(routes)}
