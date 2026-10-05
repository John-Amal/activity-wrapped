from fastapi.testclient import TestClient

from activity_wrapped.app import app


def demo_client() -> TestClient:
    client = TestClient(app)
    client.get("/demo", follow_redirects=False)
    return client


def test_requires_connection():
    assert TestClient(app).get("/api/meta").status_code == 401


def test_demo_flow_end_to_end():
    c = demo_client()
    assert c.get("/api/me").json()["demo"] is True
    meta = c.get("/api/meta").json()
    assert meta["years"] and meta["sports"] and meta["sections"]

    stats = c.get("/api/stats", params={"period": "all", "sports": "Run", "units": "imperial"}).json()
    assert stats["sports"] == ["Run"] and stats["totals"]["distance"].endswith("mi")

    scan = c.post("/api/best-efforts/scan", params={"max_calls": 5}).json()
    assert scan["api_calls"] == 5 and scan["scanned"] == 5 and not scan["complete"]
    again = c.post("/api/best-efforts/scan", params={"max_calls": 5}).json()
    assert again["scanned"] == 10  # continues where the last scan stopped

    png = c.get("/api/card.png", params={"sections": "headline,best_efforts", "theme": "paper",
                                          "download": "true"})
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert "attachment" in png.headers["content-disposition"]


def test_bad_options_are_rejected():
    c = demo_client()
    assert c.get("/api/stats", params={"period": "fortnight"}).status_code == 400
    assert c.get("/api/stats", params={"units": "furlongs"}).status_code == 400


def test_frontend_is_served_from_same_origin():
    c = TestClient(app)
    assert "Activity Wrapped" in c.get("/").text
    assert c.get("/app.js").status_code == 200
