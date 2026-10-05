"""Thin Strava API client.

Responsibilities kept here and nowhere else:
- the OAuth code exchange and access-token refresh (tokens last six hours),
- paginated activity listing,
- per-activity detail fetches, cached on disk because a recorded activity
  does not change, so each one only ever costs a single API call,
- reading Strava's rate-limit headers so callers can stop before hitting them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import Settings

API_BASE = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:read_all"
PAGE_SIZE = 200


class StravaAPIError(RuntimeError):
    pass


class RateLimitExceeded(StravaAPIError):
    pass


@dataclass
class RateLimit:
    """Strava reports limits and usage as '15-minute,daily' pairs."""

    short_limit: int
    short_usage: int
    daily_limit: int
    daily_usage: int

    @property
    def short_remaining(self) -> int:
        return self.short_limit - self.short_usage

    @property
    def daily_remaining(self) -> int:
        return self.daily_limit - self.daily_usage

    def as_dict(self) -> dict:
        return {
            "short_remaining": self.short_remaining,
            "daily_remaining": self.daily_remaining,
            "short_limit": self.short_limit,
            "daily_limit": self.daily_limit,
        }

    @classmethod
    def from_headers(cls, headers) -> RateLimit | None:
        # Read-specific limits are stricter where Strava sends them; prefer those.
        for prefix in ("X-ReadRateLimit", "X-RateLimit"):
            limit, usage = headers.get(f"{prefix}-Limit"), headers.get(f"{prefix}-Usage")
            if limit and usage:
                try:
                    sl, dl = (int(x) for x in limit.split(","))
                    su, du = (int(x) for x in usage.split(","))
                except ValueError:
                    continue
                return cls(sl, su, dl, du)
        return None


def authorize_url(settings: Settings, state: str) -> str:
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(settings: Settings, code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise StravaAPIError(f"Token exchange failed ({resp.status_code}).")
    return resp.json()


class StravaClient:
    is_demo = False

    def __init__(self, tokens: dict, settings: Settings, http: requests.Session | None = None):
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")
        self._expires_at = tokens.get("expires_at", 0)
        self._settings = settings
        self._http = http or requests.Session()
        self.rate_limit: RateLimit | None = None
        athlete = tokens.get("athlete") or {}
        self.athlete_id = athlete.get("id")
        self.athlete_name = (
            f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
            or athlete.get("username")
            or "athlete"
        )

    # -- auth -----------------------------------------------------------------
    def _ensure_token(self) -> None:
        if not self._refresh_token or self._expires_at - time.time() > 60:
            return
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            raise StravaAPIError("Could not refresh the Strava token; please reconnect.")
        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        self._expires_at = data.get("expires_at", 0)

    def _get(self, path: str, params: dict | None = None):
        self._ensure_token()
        resp = self._http.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            params=params,
            timeout=20,
        )
        self.rate_limit = RateLimit.from_headers(resp.headers) or self.rate_limit
        if resp.status_code == 429:
            raise RateLimitExceeded("Strava rate limit reached. Try again in 15 minutes.")
        if resp.status_code == 401:
            raise StravaAPIError("Strava rejected the token; please reconnect.")
        if resp.status_code != 200:
            raise StravaAPIError(f"Strava API error {resp.status_code} on {path}.")
        return resp.json()

    # -- data -----------------------------------------------------------------
    def list_activities(self) -> list[dict]:
        """All activities, newest first. Filtering by period and sport happens
        locally, so changing a filter never costs another API call."""
        activities: list[dict] = []
        for page in range(1, self._settings.max_pages + 1):
            batch = self._get("/athlete/activities", {"per_page": PAGE_SIZE, "page": page})
            activities.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return activities

    def _cache_path(self, activity_id: int) -> Path:
        owner = str(self.athlete_id or "unknown")
        return self._settings.cache_dir / "details" / owner / f"{activity_id}.json"

    def get_activity_details(self, activity_id: int) -> dict:
        path = self._cache_path(activity_id)
        if path.exists():
            return json.loads(path.read_text())
        detail = self._get(f"/activities/{activity_id}", {"include_all_efforts": "false"})
        trimmed = trim_detail(detail)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(trimmed))
        return trimmed

    def is_cached(self, activity_id: int) -> bool:
        return self._cache_path(activity_id).exists()


def trim_detail(detail: dict) -> dict:
    """Keep only what the app uses, so the on-disk cache holds no GPS
    polylines, gear, photos or other personal detail."""
    return {
        "id": detail.get("id"),
        "name": detail.get("name"),
        "start_date_local": detail.get("start_date_local"),
        "best_efforts": [
            {
                "name": e.get("name"),
                "distance": e.get("distance"),
                "elapsed_time": e.get("elapsed_time"),
                "pr_rank": e.get("pr_rank"),
            }
            for e in detail.get("best_efforts") or []
        ],
    }
