"""Time handling. ORBIT stores and compares every timestamp as timezone-aware UTC.

Naive datetimes are interpreted as UTC (documented in DECISIONS.md, ADR-006) so that
evidence from different sources can always be ordered without ad-hoc tz alignment.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import AfterValidator


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ensure_utc_optional(value: Optional[datetime]) -> Optional[datetime]:
    return ensure_utc(value) if value is not None else None


UTCDateTime = Annotated[datetime, AfterValidator(ensure_utc)]
