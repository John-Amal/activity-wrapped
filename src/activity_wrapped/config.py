"""Runtime settings, read once from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    client_id: str | None
    client_secret: str | None
    base_url: str
    cache_dir: Path
    cookie_secure: bool
    max_pages: int

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/auth/callback"

    @property
    def strava_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        client_id=os.getenv("STRAVA_CLIENT_ID") or None,
        client_secret=os.getenv("STRAVA_CLIENT_SECRET") or None,
        base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
        cache_dir=Path(os.getenv("CACHE_DIR", ".cache")),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        # 200 activities per page; 50 pages covers 10,000 activities.
        max_pages=int(os.getenv("MAX_PAGES", "50")),
    )
