"""Deterministic epistemic-status rules shared by every service."""
from typing import Iterable

from backend.app.domain.types import EpistemicStatus

# Sources whose records count as authoritative (rule 2.3: VERIFIED = strong or
# authoritative evidence). Everything else is direct perception at best (OBSERVED).
AUTHORITATIVE_SOURCES = frozenset(
    {"digital_registry", "manual_verification", "authoritative_record"}
)
VERIFIED_AUTHORITY_THRESHOLD = 0.9


def classify_source_status(source: str, authority: float) -> EpistemicStatus:
    if source in AUTHORITATIVE_SOURCES and authority >= VERIFIED_AUTHORITY_THRESHOLD:
        return EpistemicStatus.VERIFIED
    return EpistemicStatus.OBSERVED


def aggregate_status(statuses: Iterable[EpistemicStatus]) -> EpistemicStatus:
    """Entity-level status is the weakest link of its attribute statuses.

    A single contradicted or stale attribute must be visible at entity level; an
    entity is VERIFIED only when every attribute is VERIFIED.
    """
    values = list(statuses)
    if not values:
        return EpistemicStatus.UNKNOWN
    for worst in (EpistemicStatus.CONTRADICTED, EpistemicStatus.STALE, EpistemicStatus.UNKNOWN):
        if worst in values:
            return worst
    if all(s == EpistemicStatus.VERIFIED for s in values):
        return EpistemicStatus.VERIFIED
    if EpistemicStatus.INFERRED in values and EpistemicStatus.OBSERVED not in values:
        return EpistemicStatus.INFERRED
    return EpistemicStatus.OBSERVED
