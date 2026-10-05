"""In-memory session store.

The browser only ever holds an opaque, HttpOnly session cookie. Strava
tokens, the activity list and fetched activity details stay on the server.
This is enough for a single-process deployment; a multi-instance deployment
would swap this for Redis or a database without touching the routes.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

SESSION_TTL_S = 12 * 3600


@dataclass
class Session:
    id: str
    created: float = field(default_factory=time.time)
    oauth_state: str | None = None
    source: Any = None  # StravaClient or DemoSource
    athlete_name: str = ""
    activities: list[dict] | None = None
    details: dict[int, dict] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.source is not None

    @property
    def is_demo(self) -> bool:
        return getattr(self.source, "is_demo", False)


class SessionStore:
    def __init__(self, ttl_s: int = SESSION_TTL_S) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._ttl_s = ttl_s

    def create(self) -> Session:
        session = Session(id=secrets.token_urlsafe(32))
        with self._lock:
            self._evict_expired()
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session and time.time() - session.created > self._ttl_s:
                del self._sessions[session_id]
                return None
            return session

    def delete(self, session_id: str | None) -> None:
        with self._lock:
            self._sessions.pop(session_id or "", None)

    def _evict_expired(self) -> None:
        now = time.time()
        for sid in [s for s, v in self._sessions.items() if now - v.created > self._ttl_s]:
            del self._sessions[sid]
