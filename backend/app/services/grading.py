"""Evidential grading of a state version from its supports (shared by the belief
updater, the as-of read path and world projection)."""
from typing import Callable, List, Optional

from backend.app.domain.models import SupportRef
from backend.app.domain.types import EpistemicStatus, EvidenceChannel
from backend.app.services.evidence_policy import STRONG_STRENGTH, channel, grade, is_verification


def status_from_supports(supports: List[SupportRef], authority_of: Callable[[str], float]) -> EpistemicStatus:
    """Replay of the grading rules in ``BeliefUpdater._new_version`` / ``_add_support``:
    the evidential status a version had after exactly these supports. Used to rebuild
    belief as it was at an earlier instant (a later corroboration must not leak back)."""
    status: Optional[EpistemicStatus] = None
    seen: List[SupportRef] = []
    for ref in supports:
        authority = authority_of(ref.evidence_id)
        quality = ref.strength / authority if authority > 0 else 0.0
        g = grade(ref.source_type, quality, authority)
        verification = is_verification(ref.source_type, quality, authority)
        seen.append(ref)
        if status is None:
            status = EpistemicStatus.VERIFIED if verification else g
        elif verification or g == EpistemicStatus.VERIFIED:
            status = EpistemicStatus.VERIFIED
        elif status == EpistemicStatus.UNKNOWN and g == EpistemicStatus.OBSERVED:
            status = EpistemicStatus.OBSERVED
        elif status == EpistemicStatus.OBSERVED and independently_corroborated(seen):
            status = EpistemicStatus.VERIFIED
    return status or EpistemicStatus.UNKNOWN


def independently_corroborated(supports: List[SupportRef]) -> bool:
    sources = {
        s.source for s in supports if s.strength >= STRONG_STRENGTH and channel(s.source_type) == EvidenceChannel.DIRECT
    }
    return len(sources) >= 2
