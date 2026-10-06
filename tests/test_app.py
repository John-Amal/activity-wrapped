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


def test_activities_list_detail_and_card():
    c = demo_client()
    listing = c.get("/api/activities", params={"sports": "Run", "sort": "distance", "limit": 5}).json()
    assert len(listing["items"]) == 5 and listing["total"] > 5
    first = listing["items"][0]
    assert first["sport"] == "Run" and first["has_map"]

    detail = c.get(f"/api/activity/{first['id']}").json()
    assert detail["details_loaded"] and detail["splits"] and detail["best_efforts"]

    png = c.get(f"/api/activity/{first['id']}/card.png", params={"privacy": 500, "theme": "dusk"})
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert c.get("/api/activity/1/card.png").status_code == 404  # not this athlete's activity
    assert c.get(f"/api/activity/{first['id']}/card.png", params={"privacy": 333}).status_code == 400


def test_personal_bests_follow_the_scan():
    c = demo_client()
    before = c.get("/api/personal-bests").json()
    assert before["bests"] == [] and before["total"] > 0
    c.post("/api/best-efforts/scan", params={"period": "all", "max_calls": 20})
    after = c.get("/api/personal-bests").json()
    assert after["scanned"] == 20 and after["bests"]
    assert all("progression" in b for b in after["bests"])


def test_route_map_section_renders():
    c = demo_client()
    png = c.get("/api/card.png", params={"sections": "headline,routes", "privacy": 0})
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")


def test_map_and_background_options_are_validated():
    c = demo_client()
    meta = c.get("/api/meta").json()
    assert {m["key"] for m in meta["map_styles"]} >= {"none"}
    assert c.get("/api/card.png", params={"map": "satellite"}).status_code == 400
    assert c.get("/api/card.png", params={"bg": "glass"}).status_code == 400
    png = c.get("/api/card.png", params={"bg": "transparent"})
    assert png.status_code == 200


def test_login_moves_to_base_url_host_and_bad_state_restarts(monkeypatch):
    import dataclasses

    from activity_wrapped import app as app_module
    configured = dataclasses.replace(app_module.settings, client_id="1", client_secret="s",
                                     base_url="http://localhost:8000")
    monkeypatch.setattr(app_module, "settings", configured)
    c = TestClient(app, base_url="http://127.0.0.1:8000")
    r = c.get("/auth/login", follow_redirects=False)
    assert r.headers["location"] == "http://localhost:8000/auth/login"

    c = TestClient(app, base_url="http://localhost:8000")
    r = c.get("/auth/login", follow_redirects=False)
    assert r.headers["location"].startswith("https://www.strava.com/oauth/authorize")
    r = c.get("/auth/callback", params={"code": "x", "state": "wrong"}, follow_redirects=False)
    assert r.headers["location"] == "/?error=login_expired"


def test_training_report_plan_and_calendar():
    c = demo_client()
    for group in ("run", "ride", "swim", "hike", "all"):
        r = c.get("/api/training/report", params={"group": group})
        assert r.status_code == 200 and r.json()["findings"]
    run = c.get("/api/training/report", params={"group": "run"}).json()
    assert run["predictions"] and run["thresholds"]["critical_speed"] and run["zones"]

    plan = c.get("/api/training/plan", params={"group": "run", "goal": "faster", "target": "5k", "weeks": 6,
                                               "days": 5}).json()
    assert len(plan["weeks"]) == 6 and all(len(w["sessions"]) == 5 for w in plan["weeks"])
    assert "projection" in plan and plan["projection"].get("trend")

    ics = c.get("/api/training/plan.ics", params={"group": "ride", "goal": "endurance"})
    assert ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR")
    assert "attachment" in ics.headers["content-disposition"]


def test_training_inputs_are_validated():
    c = demo_client()
    assert c.get("/api/training/report", params={"group": "chess"}).status_code == 400
    assert c.get("/api/training/plan", params={"goal": "win"}).status_code == 400
    assert c.get("/api/training/plan", params={"target": "50k"}).status_code == 400
    assert c.get("/api/training/plan", params={"race_date": "2020-01-01"}).status_code == 400
    assert c.get("/api/training/plan", params={"weeks": 40}).status_code == 422
