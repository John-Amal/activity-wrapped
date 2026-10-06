import pytest

from activity_wrapped.routes import (
    decode_polyline,
    encode_polyline,
    haversine_m,
    main_area,
    trim_ends,
)

# Google's reference example for the encoded polyline algorithm.
GOOGLE_EXAMPLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
GOOGLE_POINTS = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_decode_matches_reference():
    assert decode_polyline(GOOGLE_EXAMPLE) == pytest.approx(GOOGLE_POINTS)
    assert decode_polyline("") == [] and decode_polyline(None) == []


def test_encode_round_trips():
    assert encode_polyline(GOOGLE_POINTS) == GOOGLE_EXAMPLE
    pts = [(46.94812, 7.44741), (46.95, 7.45), (46.9512, 7.4433)]
    assert decode_polyline(encode_polyline(pts)) == pytest.approx(pts)


def test_haversine_one_degree_latitude():
    assert haversine_m((46.0, 7.0), (47.0, 7.0)) == pytest.approx(111_195, rel=1e-3)


def test_trim_ends_hides_start_and_finish():
    # A straight 2 km line north, one point every ~111 m.
    line = [(46.0 + i * 0.001, 7.0) for i in range(19)]
    trimmed = trim_ends(line, 300)
    assert haversine_m(trimmed[0], line[0]) > 300
    assert haversine_m(trimmed[-1], line[-1]) > 300
    assert trim_ends(line, 0) == line
    assert trim_ends(line[:3], 300) == []  # too short: nothing left to show


def test_main_area_drops_far_away_routes():
    home = [[(46.95 + i * 1e-3, 7.45)] * 2 for i in range(5)]
    away = [[(45.92, 6.87)] * 2]
    assert main_area(home + away) == [0, 1, 2, 3, 4]
