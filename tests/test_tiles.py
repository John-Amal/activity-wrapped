import math

import pytest
from PIL import Image

from activity_wrapped import tiles
from activity_wrapped.tiles import basemap, fit_viewport, tile_for, to_world


def test_tile_numbers_match_known_references():
    assert tile_for(51.5072, -0.1276, 10) == (511, 340)  # central London
    assert tile_for(47.3769, 8.5417, 13) == (4290, 2868)  # Zurich
    assert tile_for(0, 0, 1) == (1, 1)


def test_viewport_fits_routes_and_preserves_shape():
    # ~1 km square near Bern; Mercator is conformal, so it stays square.
    lat, lon, d = 46.95, 7.45, 0.009
    square = [(lat, lon), (lat + d, lon), (lat + d, lon + d / math.cos(math.radians(lat))),
              (lat, lon + d / math.cos(math.radians(lat)))]
    vp = fit_viewport([square], (0, 0, 1000, 500), pad=0)
    pts = [vp(*p) for p in square]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert max(xs) - min(xs) == pytest.approx(max(ys) - min(ys), rel=0.01)
    assert min(xs) >= -1e-6 and max(xs) <= 1000 + 1e-6 and min(ys) >= -1e-6 and max(ys) <= 500 + 1e-6


def coordinate_tile(style, z, x, y):
    """A fake tile whose pixel (i, j) has colour (i, j, 0): lets a test read
    back exactly which tile pixel ended up where."""
    img = Image.new("RGB", (256, 256))
    img.putdata([(i, j, 0) for j in range(256) for i in range(256)])
    return img


def test_basemap_puts_each_point_on_the_right_tile_pixel():
    route = [(46.9480, 7.4474), (46.9530, 7.4600), (46.9600, 7.4500)]
    vp = fit_viewport([route], (0, 0, 800, 600))
    img, attribution = basemap(vp, "streets", fetch=coordinate_tile)
    assert img.size == (800, 600) and "OpenStreetMap" in attribution
    zoom = round(math.log2(vp.scale / 256))
    for lat, lon in route:
        wx, wy = to_world(lat, lon)
        expected_i = int(wx * 256 * 2 ** zoom) % 256
        expected_j = int(wy * 256 * 2 ** zoom) % 256
        if not (8 < expected_i < 248 and 8 < expected_j < 248):
            continue  # too close to a tile edge for the resampled colour to be exact
        px, py = vp(lat, lon)
        r, g, _ = img.getpixel((int(px), int(py)))
        assert abs(r - expected_i) <= 4 and abs(g - expected_j) <= 4


def test_basemap_gives_up_cleanly_when_tiles_are_unavailable():
    vp = fit_viewport([[(46.95, 7.45), (46.96, 7.46)]], (0, 0, 400, 300))
    assert basemap(vp, "streets", fetch=lambda *a: None) is None
    assert basemap(vp, "none") is None


def test_fetcher_caches_on_disk(tmp_path):
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self):
            import io
            buf = io.BytesIO()
            Image.new("RGB", (256, 256), (1, 2, 3)).save(buf, format="PNG")
            self.content = buf.getvalue()

    class FakeHTTP:
        headers = {}

        def get(self, url, timeout):
            calls.append(url)
            return FakeResponse()

    f = tiles.TileFetcher(tmp_path, memory_items=1, http=FakeHTTP())
    style = tiles.STYLES["streets"]
    assert f.get(style, 3, 1, 2).getpixel((0, 0)) == (1, 2, 3)
    f.get(style, 3, 2, 2)  # evicts the first tile from memory
    f.get(style, 3, 1, 2)  # ...so this one must come from disk
    assert len(calls) == 2
    assert "activity-wrapped" in FakeHTTP.headers["User-Agent"]
