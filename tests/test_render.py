import io

import pytest
from PIL import Image

from activity_wrapped.demo import DemoSource
from activity_wrapped.render import SECTION_KEYS, THEMES, render_card
from activity_wrapped.stats import Period, compute_stats


@pytest.fixture(scope="module")
def stats():
    from .conftest import NOW
    src = DemoSource(now=NOW)
    acts = src.list_activities()
    details = {a["id"]: src.get_activity_details(a["id"]) for a in acts[:40] if a["sport_type"] == "Run"}
    return compute_stats(acts, period=Period("last12"), now=NOW, details=details)


@pytest.mark.parametrize("theme", list(THEMES))
def test_every_section_renders_in_every_theme(stats, theme):
    img = Image.open(io.BytesIO(render_card(stats, SECTION_KEYS, theme=theme, athlete="Test")))
    assert img.width == 1080 and img.height > 1500


def test_card_height_tracks_selection(stats):
    small = Image.open(io.BytesIO(render_card(stats, ["headline"])))
    big = Image.open(io.BytesIO(render_card(stats, ["headline", "series", "by_sport"])))
    assert big.height > small.height


def test_empty_selection_and_empty_stats_still_render():
    empty = {"empty": True, "period_label": "2020", "count": 0}
    assert render_card(empty, ["headline"]).startswith(b"\x89PNG")


def test_transparent_card_has_transparent_background(stats):
    img = Image.open(io.BytesIO(render_card(stats, ["headline", "totals"], background="transparent")))
    assert img.mode == "RGBA"
    assert img.getpixel((5, 5))[3] == 0  # outside everything: fully transparent
    alphas = [a for *_, a in img.getdata()]
    assert max(alphas) == 255  # text is fully opaque


def test_activity_card_with_map_tiles(monkeypatch):
    from activity_wrapped import tiles
    from activity_wrapped.render import render_activity_card
    from activity_wrapped.stats import activity_detail

    from .conftest import act
    monkeypatch.setattr(tiles, "fetch_tile", lambda *a: Image.new("RGB", (512, 512), (90, 90, 90)))
    route = [(46.948, 7.447), (46.955, 7.46), (46.962, 7.45), (46.949, 7.448)]
    a = activity_detail(act(1, "2026-05-01"), None)
    for style in ("streets", "dark"):
        img = Image.open(io.BytesIO(render_activity_card(a, route, map_style=style)))
        assert img.width == 1080
    # with no tiles available it falls back to the plain panel instead of failing
    monkeypatch.setattr(tiles, "fetch_tile", lambda *a: None)
    assert render_activity_card(a, route, map_style="light").startswith(b"\x89PNG")
