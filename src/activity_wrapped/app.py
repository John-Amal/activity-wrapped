"""FastAPI app: OAuth, data endpoints, card rendering, and the static frontend.

The frontend is served from the same origin as the API, so the session
cookie needs no CORS configuration and the access token never reaches the
browser.

Run locally:
    uvicorn activity_wrapped.app:app --reload
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings, get_settings
from .demo import DemoSource
from .render import DEFAULT_SECTIONS, SECTION_KEYS, SECTIONS, THEMES, render_card
from .sessions import Session, SessionStore
from .stats import (
    Period,
    available_years,
    best_effort_candidates,
    compute_stats,
    filter_activities,
    sport_counts,
)
from .strava import RateLimitExceeded, StravaAPIError, StravaClient, authorize_url, exchange_code

COOKIE = "aw_session"
STATIC_DIR = Path(__file__).parent / "static"
# Keep this many requests of the 15-minute allowance in reserve during PR scans.
RATE_LIMIT_RESERVE = 10

settings: Settings = get_settings()
store = SessionStore()
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
        raise HTTPException(400, "OAuth state mismatch. Please start the login again.")
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
         download: bool = False):
    p, sp, u = parse_options(period, sports, units)
    chosen = [s for s in sections.split(",") if s in SECTION_KEYS] or DEFAULT_SECTIONS
    data = compute_stats(session.activities, period=p, sports=sp, units=u,
                         details=session.details)
    png = render_card(data, chosen, theme=theme, athlete=session.athlete_name)
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


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
