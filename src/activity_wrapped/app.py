"""FastAPI app: OAuth, data endpoints, card rendering, and the static frontend.

The frontend is served from the same origin as the API, so the session
cookie needs no CORS configuration and the access token never reaches the
browser.

Run locally:
    uvicorn activity_wrapped.app:app --reload
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, tiles
from .config import Settings, get_settings
from .demo import DemoSource
from .insights import plan_with_projection, report
from .planner import GOALS, PlanRequest, to_ics
from .render import (
    DEFAULT_SECTIONS,
    SECTION_KEYS,
    SECTIONS,
    THEMES,
    render_activity_card,
    render_card,
)
from .routes import DEFAULT_PRIVACY_M, prepare_routes, summary_route, trim_ends
from .sessions import Session, SessionStore
from .stats import (
    Period,
    activity_detail,
    activity_summary,
    available_years,
    best_effort_candidates,
    compute_stats,
    filter_activities,
    local_start,
    personal_bests,
    sport_counts,
)
from .strava import RateLimitExceeded, StravaAPIError, StravaClient, authorize_url, exchange_code

COOKIE = "aw_session"
STATIC_DIR = Path(__file__).parent / "static"
# Keep this many requests of the 15-minute allowance in reserve during PR scans.
RATE_LIMIT_RESERVE = 10
# Radius (metres) hidden around the start and end of every route drawn.
PRIVACY_CHOICES = (0, 200, 500, 1000)
OVERLAY_MIN_PRIVACY_M = 1000

settings: Settings = get_settings()
store = SessionStore()
if settings.map_tiles:
    tiles.configure(settings.cache_dir)
if settings.cookie_secure and settings.base_url.startswith("http://"):
    logging.getLogger(__name__).warning(
        "COOKIE_SECURE=true but BASE_URL is plain http: browsers will drop the session "
        "cookie and every login will fail. Set COOKIE_SECURE=false for local use.")
BACKGROUNDS = ("solid", "transparent")
MAP_STYLES = [{"key": "none", "label": "No map"}] + (
    [{"key": k, "label": s.label} for k, s in tiles.STYLES.items()] if settings.map_tiles else [])
app = FastAPI(title="Activity Wrapped", version=__version__)


@app.exception_handler(RateLimitExceeded)
def _rate_limited(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": str(exc)}, status_code=429)


@app.exception_handler(StravaAPIError)
def _strava_error(request: Request, exc: StravaAPIError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def _set_cookie(resp: Response, session: Session) -> None:
    resp.set_cookie(COOKIE, session.id, httponly=True, samesite="lax",
                    secure=settings.cookie_secure, max_age=12 * 3600)


def current_session(request: Request) -> Session:
    session = store.get(request.cookies.get(COOKIE))
    if not session or not session.connected:
        raise HTTPException(401, "Not connected. Connect with Strava or open the demo.")
    return session


def loaded_session(session: Session = Depends(current_session)) -> Session:
    if session.activities is None:
        session.activities = session.source.list_activities()
    return session


def parse_privacy(privacy: int) -> int:
    if privacy not in PRIVACY_CHOICES:
        raise HTTPException(400, f"privacy must be one of {PRIVACY_CHOICES}")
    return privacy


def parse_look(map_style: str, bg: str) -> tuple[str, str]:
    if map_style not in tiles.MAP_CHOICES:
        raise HTTPException(400, f"map must be one of {tiles.MAP_CHOICES}")
    if bg not in BACKGROUNDS:
        raise HTTPException(400, f"bg must be one of {list(BACKGROUNDS)}")
    return map_style, bg


def find_activity(session: Session, activity_id: int) -> dict:
    # Only activities from this session's own list can be opened.
    for a in session.activities:
        if a.get("id") == activity_id:
            return a
    raise HTTPException(404, "Activity not found.")


def load_detail(session: Session, activity_id: int) -> tuple[dict | None, str | None]:
    """Detail for one activity: one API call the first time, cached after.
    If Strava's rate limit is hit, the activity is still shown without it."""
    if activity_id in session.details:
        return session.details[activity_id], None
    try:
        detail = session.source.get_activity_details(activity_id)
    except RateLimitExceeded:
        return None, "Strava rate limit reached; splits, efforts and segments will load later."
    session.details[activity_id] = detail
    return detail, None


def parse_options(period: str, sports: str, units: str) -> tuple[Period, list[str], str]:
    try:
        p = Period.parse(period)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if units not in ("metric", "imperial"):
        raise HTTPException(400, "units must be metric or imperial")
    return p, [s for s in sports.split(",") if s], units


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.get("/auth/login")
def login(request: Request):
    if not settings.strava_configured:
        raise HTTPException(500, "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET are not set. See README.")
    # The session cookie belongs to the host the browser is on, and Strava
    # always sends the user back to BASE_URL. If those differ (127.0.0.1 vs
    # localhost, say), the cookie would be missing on return and the login
    # would fail, so move the user to BASE_URL's host before starting.
    if request.url.netloc != urlsplit(settings.base_url).netloc:
        return RedirectResponse(f"{settings.base_url}/auth/login")
    session = store.get(request.cookies.get(COOKIE)) or store.create()
    session.oauth_state = secrets.token_urlsafe(24)
    resp = RedirectResponse(authorize_url(settings, session.oauth_state))
    _set_cookie(resp, session)
    return resp


@app.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None,
             error: str | None = None, scope: str = ""):
    session = store.get(request.cookies.get(COOKIE))
    if error:
        return RedirectResponse("/?error=access_denied")
    if not session or not state or not secrets.compare_digest(state, session.oauth_state or ""):
        # Usually a server restart mid-login (sessions live in memory) or a
        # stale tab. Send the user back to start again rather than a raw error.
        return RedirectResponse("/?error=login_expired")
    if not code:
        raise HTTPException(400, "Missing authorisation code.")
    tokens = exchange_code(settings, code)
    session.oauth_state = None
    session.source = StravaClient(tokens, settings)
    session.athlete_name = session.source.athlete_name
    session.activities = None
    session.details = {}
    note = "" if "activity:read_all" in scope else "?note=public_only"
    return RedirectResponse(f"/{note}")


@app.get("/demo")
def demo(request: Request):
    session = store.get(request.cookies.get(COOKIE)) or store.create()
    session.source = DemoSource()
    session.athlete_name = DemoSource.athlete_name
    session.activities = None
    session.details = {}
    resp = RedirectResponse("/")
    _set_cookie(resp, session)
    return resp


@app.post("/auth/logout")
def logout(request: Request):
    store.delete(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/me")
def me(request: Request):
    session = store.get(request.cookies.get(COOKIE))
    return {
        "connected": bool(session and session.connected),
        "demo": bool(session and session.is_demo),
        "athlete": session.athlete_name if session and session.connected else None,
        "strava_configured": settings.strava_configured,
    }


@app.get("/api/meta")
def meta(session: Session = Depends(loaded_session)):
    """Everything the options panel needs, derived from the cached activity list."""
    rl = session.source.rate_limit
    return {
        "athlete": session.athlete_name,
        "demo": session.is_demo,
        "activity_count": len(session.activities),
        "years": available_years(session.activities),
        "sports": sport_counts(session.activities),
        "sections": SECTIONS,
        "themes": list(THEMES),
        "privacy_choices": list(PRIVACY_CHOICES),
        "map_styles": MAP_STYLES,
        "privacy_default": DEFAULT_PRIVACY_M,
        "rate_limit": rl.as_dict() if rl else None,
    }


@app.post("/api/sync")
def sync(session: Session = Depends(current_session)):
    session.activities = session.source.list_activities()
    return {"activity_count": len(session.activities)}


@app.get("/api/stats")
def stats(session: Session = Depends(loaded_session), period: str = "last12", sports: str = "",
          units: str = "metric"):
    p, sp, u = parse_options(period, sports, units)
    return compute_stats(session.activities, period=p, sports=sp, units=u,
                         details=session.details)


@app.get("/api/card.png")
def card(session: Session = Depends(loaded_session), period: str = "last12", sports: str = "",
         units: str = "metric", theme: str = "midnight", sections: str = "",
         privacy: int = DEFAULT_PRIVACY_M, map: str = "none", bg: str = "solid",
         download: bool = False):
    p, sp, u = parse_options(period, sports, units)
    privacy = parse_privacy(privacy)
    map_style, bg = parse_look(map, bg)
    chosen = [s for s in sections.split(",") if s in SECTION_KEYS] or DEFAULT_SECTIONS
    data = compute_stats(session.activities, period=p, sports=sp, units=u,
                         details=session.details)
    routes = None
    if "routes" in chosen:
        # Overlaid routes all funnel through the athlete's own streets, so the
        # busiest point of the map sits close to home even when every route is
        # trimmed. The overlay therefore hides at least 1 km unless the user
        # has switched privacy off entirely.
        overlay_privacy = max(privacy, OVERLAY_MIN_PRIVACY_M) if privacy else 0
        routes = prepare_routes(filter_activities(session.activities, p, sp, datetime.now()),
                                overlay_privacy)
    png = render_card(data, chosen, theme=theme, athlete=session.athlete_name, routes=routes,
                      map_style=map_style, background=bg)
    headers = {"Cache-Control": "no-store"}
    if download:
        stamp = p.key.replace(":", "-")
        headers["Content-Disposition"] = f'attachment; filename="activity-wrapped-{stamp}.png"'
    return Response(png, media_type="image/png", headers=headers)


@app.post("/api/best-efforts/scan")
def scan_best_efforts(session: Session = Depends(loaded_session), period: str = "last12",
                      sports: str = "", max_calls: int = Query(40, ge=1, le=200)):
    """Fetch details for not-yet-scanned runs in the current filter, fastest
    first, within a call budget and the remaining rate-limit allowance.
    Calling it again continues where the last scan stopped."""
    p, sp, _ = parse_options(period, sports, "metric")
    acts = filter_activities(session.activities, p, sp, datetime.now())
    candidates = best_effort_candidates(acts)
    pending = [a for a in candidates if a["id"] not in session.details]

    api_calls, stopped = 0, None
    started = time.time()
    for a in pending:
        source = session.source
        cached = source.is_cached(a["id"])
        if not cached:
            rl = source.rate_limit
            if api_calls >= max_calls:
                stopped = "call budget for this scan used"
                break
            if rl and (rl.short_remaining <= RATE_LIMIT_RESERVE or rl.daily_remaining <= RATE_LIMIT_RESERVE):
                stopped = "close to Strava's rate limit; try again in 15 minutes"
                break
            if time.time() - started > 60:
                stopped = "time limit for this scan reached"
                break
        session.details[a["id"]] = source.get_activity_details(a["id"])
        api_calls += 0 if cached else 1

    scanned = sum(1 for a in candidates if a["id"] in session.details)
    rl = session.source.rate_limit
    return {"scanned": scanned, "total": len(candidates), "api_calls": api_calls,
            "complete": scanned == len(candidates), "stopped": stopped,
            "rate_limit": rl.as_dict() if rl else None}


@app.get("/api/activities")
def activities(session: Session = Depends(loaded_session), period: str = "last12", sports: str = "",
               units: str = "metric", q: str = "", sort: str = "date",
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    p, sp, u = parse_options(period, sports, units)
    acts = filter_activities(session.activities, p, sp, datetime.now())
    if q.strip():
        needle = q.strip().lower()
        acts = [a for a in acts if needle in (a.get("name") or "").lower()]
    keys = {
        "date": lambda a: local_start(a),
        "distance": lambda a: a.get("distance", 0),
        "time": lambda a: a.get("moving_time", 0),
        "elevation": lambda a: a.get("total_elevation_gain", 0),
    }
    if sort not in keys:
        raise HTTPException(400, f"sort must be one of {list(keys)}")
    acts = sorted(acts, key=keys[sort], reverse=True)
    return {"total": len(acts), "offset": offset,
            "items": [activity_summary(a, u) for a in acts[offset:offset + limit]]}


@app.get("/api/activity/{activity_id}")
def activity(activity_id: int, session: Session = Depends(loaded_session), units: str = "metric"):
    _, _, u = parse_options("last12", "", units)
    a = find_activity(session, activity_id)
    detail, warning = load_detail(session, activity_id)
    return activity_detail(a, detail, u) | {"warning": warning}


@app.get("/api/activity/{activity_id}/card.png")
def activity_card(activity_id: int, session: Session = Depends(loaded_session),
                  units: str = "metric", theme: str = "midnight",
                  privacy: int = DEFAULT_PRIVACY_M, map: str = "none", bg: str = "solid",
                  download: bool = False):
    _, _, u = parse_options("last12", "", units)
    privacy = parse_privacy(privacy)
    map_style, bg = parse_look(map, bg)
    a = find_activity(session, activity_id)
    detail, _ = load_detail(session, activity_id)
    data = activity_detail(a, detail, u)
    route = trim_ends(summary_route(a), privacy)
    png = render_activity_card(data, route, theme=theme, athlete=session.athlete_name, units=u,
                               map_style=map_style, background=bg)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="activity-{activity_id}.png"'
    return Response(png, media_type="image/png", headers=headers)


@app.get("/api/personal-bests")
def personal_bests_view(session: Session = Depends(loaded_session), period: str = "all",
                        sports: str = ""):
    """Personal bests from the runs scanned so far, with how each one improved
    over time and how much of the selection the scan has covered."""
    p, sp, _ = parse_options(period, sports, "metric")
    candidates = best_effort_candidates(filter_activities(session.activities, p, sp, datetime.now()))
    scanned = [session.details[a["id"]] for a in candidates if a["id"] in session.details]
    return {"bests": personal_bests(scanned), "scanned": len(scanned), "total": len(candidates)}


# ---------------------------------------------------------------------------
# Training intelligence
# ---------------------------------------------------------------------------
TRAINING_GROUPS = ("run", "ride", "swim", "hike", "all")
PLAN_TARGETS = ("5k", "10k", "Half marathon", "Marathon")


def parse_group(group: str) -> str:
    if group not in TRAINING_GROUPS:
        raise HTTPException(400, f"group must be one of {TRAINING_GROUPS}")
    return group


@app.get("/api/training/report")
def training_report(session: Session = Depends(loaded_session), group: str = "run"):
    return report(session.activities, session.details, parse_group(group))


def plan_request(group: str, goal: str, target: str, weeks: int, days: int, race_date: str | None) -> PlanRequest:
    if goal not in GOALS:
        raise HTTPException(400, f"goal must be one of {GOALS}")
    if target not in PLAN_TARGETS:
        raise HTTPException(400, f"target must be one of {PLAN_TARGETS}")
    race = None
    if race_date:
        try:
            race = datetime.strptime(race_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(400, "race_date must be YYYY-MM-DD") from e
        if race <= datetime.now().date() + timedelta(days=7):
            raise HTTPException(400, "race_date must be more than a week away")
    return PlanRequest(group=parse_group(group), goal=goal, target=target, weeks=weeks, days=days,
                       race_date=race)


@app.get("/api/training/plan")
def training_plan(session: Session = Depends(loaded_session), group: str = "run", goal: str = "faster",
                  target: str = "10k", weeks: int = Query(8, ge=2, le=20), days: int = Query(4, ge=2, le=7),
                  race_date: str | None = None):
    req = plan_request(group, goal, target, weeks, days, race_date)
    return plan_with_projection(session.activities, session.details, req)


@app.get("/api/training/plan.ics")
def training_plan_ics(session: Session = Depends(loaded_session), group: str = "run", goal: str = "faster",
                      target: str = "10k", weeks: int = Query(8, ge=2, le=20), days: int = Query(4, ge=2, le=7),
                      race_date: str | None = None):
    req = plan_request(group, goal, target, weeks, days, race_date)
    plan = plan_with_projection(session.activities, session.details, req)
    return Response(to_ics(plan), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="training-plan-{group}.ics"'})


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
