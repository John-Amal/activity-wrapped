"""Optional map backgrounds from standard web-map tiles.

Routes and tiles share one Web Mercator projection, so a route always lands
exactly on the streets it ran along. Tiles are cached on disk and in memory:
every option change re-renders the card, and re-downloading the same tiles
each time would be slow and would break the tile providers' usage policies.

If tiles can't be fetched (offline, blocked, provider down), the card falls
back to the plain background rather than failing.
"""

from __future__ import annotations

import io
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image

from . import __version__

Point = tuple[float, float]  # (lat, lon)
MAX_LAT = 85.05112878
MAX_TILES = 40  # per render; zoom drops a level rather than fetch more


@dataclass(frozen=True)
class TileStyle:
    key: str
    label: str
    url: str
    attribution: str
    tile_size: int  # pixels per tile image (512 for retina tiles)
    max_zoom: int


# OpenStreetMap's own tiles are for light, non-commercial use with attribution
# (https://operations.osmfoundation.org/policies/tiles/). CARTO's basemaps are
# free for non-commercial use with attribution. Check both before deploying
# this anywhere with real traffic, or point MAP_TILE_* at a paid provider.
STYLES: dict[str, TileStyle] = {
    "streets": TileStyle("streets", "Streets", "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                         "\u00a9 OpenStreetMap contributors", 256, 19),
    "light": TileStyle("light", "Light", "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
                       "\u00a9 OpenStreetMap contributors \u00a9 CARTO", 512, 20),
    "dark": TileStyle("dark", "Dark", "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
                      "\u00a9 OpenStreetMap contributors \u00a9 CARTO", 512, 20),
}
MAP_CHOICES = ["none", *STYLES]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def to_world(lat: float, lon: float) -> tuple[float, float]:
    """Web Mercator, normalised so the whole world is the unit square."""
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    s = math.sin(math.radians(lat))
    x = (lon + 180.0) / 360.0
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y


def tile_for(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    x, y = to_world(lat, lon)
    n = 2 ** zoom
    return int(x * n), int(y * n)


@dataclass(frozen=True)
class Viewport:
    """Maps routes into a pixel box: world origin (wx0, wy0) in normalised
    units, and `scale` pixels per normalised unit."""

    box: tuple[float, float, float, float]
    wx0: float
    wy0: float
    scale: float

    def __call__(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = to_world(lat, lon)
        return self.box[0] + (x - self.wx0) * self.scale, self.box[1] + (y - self.wy0) * self.scale


def fit_viewport(routes: Sequence[Sequence[Point]], box: tuple[float, float, float, float],
                 pad: float = 0.08, max_scale: float | None = None) -> Viewport:
    pts = [to_world(*p) for r in routes for p in r]
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if not pts:
        return Viewport(box, 0.0, 0.0, 1.0)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    span_x, span_y = max(max(xs) - min(xs), 1e-12), max(max(ys) - min(ys), 1e-12)
    scale = min(bw * (1 - 2 * pad) / span_x, bh * (1 - 2 * pad) / span_y)
    if max_scale:
        scale = min(scale, max_scale)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return Viewport(box, cx - bw / 2 / scale, cy - bh / 2 / scale, scale)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
class TileFetcher:
    def __init__(self, cache_dir: Path, memory_items: int = 256,
                 http: requests.Session | None = None) -> None:
        self.cache_dir = cache_dir
        self._http = http or requests.Session()
        self._http.headers["User-Agent"] = (
            f"activity-wrapped/{__version__} (+https://github.com/John-Amal/activity-wrapped)")
        self._memory: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._memory_items = memory_items
        self._lock = threading.Lock()

    def get(self, style: TileStyle, z: int, x: int, y: int) -> Image.Image | None:
        key = (style.key, z, x, y)
        with self._lock:
            if key in self._memory:
                self._memory.move_to_end(key)
                return self._memory[key]
        path = self.cache_dir / "tiles" / style.key / str(z) / str(x) / f"{y}.png"
        img = None
        if path.exists():
            img = Image.open(path).convert("RGB")
        else:
            try:
                resp = self._http.get(style.url.format(z=z, x=x, y=y), timeout=6)
                if resp.status_code == 200:
                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    img.save(path)
            except (requests.RequestException, OSError):
                img = None
        if img is not None:
            with self._lock:
                self._memory[key] = img
                if len(self._memory) > self._memory_items:
                    self._memory.popitem(last=False)
        return img


_fetcher: TileFetcher | None = None


def configure(cache_dir: Path) -> None:
    global _fetcher
    _fetcher = TileFetcher(cache_dir)


def fetch_tile(style: TileStyle, z: int, x: int, y: int) -> Image.Image | None:
    """Module-level hook so tests can replace tile fetching."""
    if _fetcher is None:
        return None
    return _fetcher.get(style, z, x, y)


# ---------------------------------------------------------------------------
# Basemap
# ---------------------------------------------------------------------------
def basemap(vp: Viewport, style_key: str,
            fetch: Callable[[TileStyle, int, int, int], Image.Image | None] | None = None
            ) -> tuple[Image.Image, str] | None:
    """Stitch the tiles under a viewport into an image the size of its box.
    Returns (image, attribution), or None if the map can't be built."""
    style = STYLES.get(style_key)
    if style is None:
        return None
    fetch = fetch or fetch_tile
    x0, y0, x1, y1 = vp.box
    w, h = int(round(x1 - x0)), int(round(y1 - y0))
    # The zoom whose native tile resolution is closest to the card's.
    zoom = round(math.log2(max(vp.scale / style.tile_size, 1e-9)))
    zoom = max(0, min(style.max_zoom, zoom))
    while True:
        n = 2 ** zoom
        wx1, wy1 = vp.wx0 + w / vp.scale, vp.wy0 + h / vp.scale
        tx0, tx1 = math.floor(vp.wx0 * n), math.floor(wx1 * n)
        ty0, ty1 = max(0, math.floor(vp.wy0 * n)), min(n - 1, math.floor(wy1 * n))
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= MAX_TILES or zoom == 0:
            break
        zoom -= 1

    ts = style.tile_size
    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * ts, (ty1 - ty0 + 1) * ts), (200, 200, 200))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tile = fetch(style, zoom, tx % n, ty)  # wrap across the antimeridian
            if tile is None:
                return None
            if tile.size != (ts, ts):
                tile = tile.resize((ts, ts))
            canvas.paste(tile, ((tx - tx0) * ts, (ty - ty0) * ts))
    px_per_unit = ts * n
    left = (vp.wx0 * n - tx0) * ts
    top = (vp.wy0 * n - ty0) * ts
    crop = canvas.crop((round(left), round(top),
                        round(left + w / vp.scale * px_per_unit),
                        round(top + h / vp.scale * px_per_unit)))
    return crop.resize((w, h), Image.LANCZOS), style.attribution
