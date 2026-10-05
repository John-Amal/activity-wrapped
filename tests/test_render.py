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
