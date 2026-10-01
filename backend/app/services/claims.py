"""The evidence gate: may ORBIT assert ``entity.attribute = value`` at time t?

Statuses STALE and CONTRADICTED are derived here, at read time, from the stored
evidential grade plus freshness, invalidation and conflicts. Only OBSERVED or
VERIFIED claims that are fresh are *supportable*; everything else is returned with
its last known value, a reason and a recommended next action — never silently as
current fact (Phase 2 exit criterion).
"""
from datetime import datetime
from typing import List, Optional, Tuple

from backend.app.domain.models import (
    ClaimAssessment,
    Conflict,
    ConflictSide,
    Entity,
    EntityAssessment,
    FreshnessAssessment,
    StateVersion,
)
from backend.app.domain.status import aggregate_status
from backend.app.domain.types import ClaimDisposition, EpistemicStatus, FreshnessState
from backend.app.repositories.base import Repository
from backend.app.services.freshness import FreshnessPolicyRegistry

SUPPORTABLE = frozenset({EpistemicStatus.OBSERVED, EpistemicStatus.VERIFIED})


def valid_at(version: StateVersion, as_of: datetime) -> bool:
    return version.valid_from <= as_of and (version.valid_to is None or version.valid_to > as_of)


def conflict_open_at(conflict: Conflict, as_of: datetime) -> bool:
    return conflict.opened_at <= as_of and (conflict.resolved_at is None or conflict.resolved_at > as_of)


class ClaimEvaluator:
    def __init__(self, repository: Repository, freshness: FreshnessPolicyRegistry):
        self.repo = repository
        self.freshness = freshness

    # ----------------------------------------------------------------- lookups
    def versions_at(self, entity_id: str, attribute: str, as_of: datetime) -> List[StateVersion]:
        return [
            v
            for v in self.repo.get_state_versions_for_entity(entity_id, attribute)
            if v.disposition != ClaimDisposition.UNCONFIRMED and valid_at(v, as_of)
        ]

    def conflict_at(self, entity_id: str, attribute: str, as_of: datetime) -> Optional[Conflict]:
        for c in self.repo.list_conflicts(entity_id=entity_id, attribute=attribute):
            if conflict_open_at(c, as_of):
                return c
        return None

    def derive(
        self, version: StateVersion, entity: Optional[Entity], as_of: datetime, conflicted: bool
    ) -> Tuple[EpistemicStatus, FreshnessAssessment]:
        policy = self.freshness.policy_for(entity, version.attribute)
        fresh = self.freshness.assess(version, policy, as_of)
        if conflicted:
            return EpistemicStatus.CONTRADICTED, fresh
        if fresh.state in (FreshnessState.STALE, FreshnessState.INVALIDATED):
            return EpistemicStatus.STALE, fresh
        return version.status, fresh

    # -------------------------------------------------------------- assessment
    def assess_attribute(self, entity_id: str, attribute: str, as_of: datetime) -> ClaimAssessment:
        entity = self.repo.get_entity(entity_id)
        versions = self.versions_at(entity_id, attribute, as_of)
        if not versions:
            return ClaimAssessment(
                entity_id=entity_id,
                attribute=attribute,
                as_of=as_of,
                status=EpistemicStatus.UNKNOWN,
                supportable=False,
                reason="no claim on record for this time",
                recommended_action="observe",
            )

        conflict = self.conflict_at(entity_id, attribute, as_of)
        if conflict is not None:
            sides = [v for v in versions if v.id in conflict.version_ids]
            accepted = next((v for v in sides if v.disposition == ClaimDisposition.ACCEPTED), sides[0])
            _, fresh = self.derive(accepted, entity, as_of, conflicted=True)
            return ClaimAssessment(
                entity_id=entity_id,
                attribute=attribute,
                as_of=as_of,
                last_known_value=accepted.value,
                status=EpistemicStatus.CONTRADICTED,
                supportable=False,
                freshness=fresh,
                evidence_refs=sorted({e for v in sides for e in v.supported_by}),
                conflict_id=conflict.id,
                conflicts=[self._side(v, entity, as_of) for v in sides],
                reason="sources disagree: " + " vs ".join(repr(v.value) for v in sides),
                recommended_action="verify",
            )

        version = max(versions, key=lambda v: v.valid_from)
        status, fresh = self.derive(version, entity, as_of, conflicted=False)
        supportable = status in SUPPORTABLE
        if supportable:
            reason, action = f"{status.value.lower()} and {fresh.state.value.lower()}", None
            if fresh.state == FreshnessState.AGING:
                action = "observe"
        elif status == EpistemicStatus.STALE:
            reason, action = f"stale: {fresh.reason}", "observe"
        elif status == EpistemicStatus.INFERRED:
            reason, action = "inferred, not observed", "observe"
        else:
            reason, action = "insufficient evidence", "observe"
        return ClaimAssessment(
            entity_id=entity_id,
            attribute=attribute,
            as_of=as_of,
            value=version.value if supportable else None,
            last_known_value=version.value,
            status=status,
            supportable=supportable,
            freshness=fresh,
            evidence_refs=list(version.supported_by),
            reason=reason,
            recommended_action=action,
        )

    def assess_entity(self, entity_id: str, as_of: datetime) -> Optional[EntityAssessment]:
        entity = self.repo.get_entity(entity_id)
        if entity is None:
            return None
        attrs = sorted({v.attribute for v in self.repo.get_state_versions_for_entity(entity_id)})
        assessed = {a: self.assess_attribute(entity_id, a, as_of) for a in attrs}
        return EntityAssessment(
            entity_id=entity_id,
            as_of=as_of,
            status=aggregate_status(c.status for c in assessed.values()),
            identity_status=entity.identity_status,
            attributes=assessed,
        )

    def _side(self, version: StateVersion, entity: Optional[Entity], as_of: datetime) -> ConflictSide:
        status, fresh = self.derive(version, entity, as_of, conflicted=False)
        return ConflictSide(
            version_id=version.id,
            value=version.value,
            status=status,
            sources=sorted({s.source for s in version.support}),
            evidence_refs=list(version.supported_by),
            last_supported_at=fresh.last_supported_at,
        )
