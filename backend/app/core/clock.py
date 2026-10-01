"""Injectable clock so freshness and diff behaviour are deterministic under test."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.core.time import ensure_utc


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    def __init__(self, at: Optional[datetime] = None):
        self._now = ensure_utc(at) if at else datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, at: datetime) -> None:
        self._now = ensure_utc(at)

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now
