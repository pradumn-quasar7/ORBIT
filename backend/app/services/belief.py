"""Applies evidence-policy decisions to stored belief (B_t = Update(B_t-1, O_t, ...)).

Responsibilities: create / corroborate / close state versions, open and resolve
conflicts, record unconfirmed claims, invalidate claims (interventions, revisions)
and propagate invalidation to dependent claims. It mutates the in-flight ``Entity``
passed by the caller; the caller calls ``materialize`` and saves it.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional, Set, Tuple

from backend.app.domain.models import Conflict, Entity, Event, StateVersion, SupportRef
from backend.app.domain.status import aggregate_status
from backend.app.domain.types import ClaimDecision, ClaimDisposition, EpistemicStatus, EventType
from backend.app.repositories.base import Repository
from backend.app.services.claims import ClaimEvaluator
from backend.app.services.evidence_policy import WEAK_STRENGTH, Claim, EvidencePolicy
from backend.app.services.grading import independently_corroborated, status_from_supports  # noqa: F401 (re-export)
from backend.app.services.freshness import FreshnessPolicyRegistry

LOCATION = "location"
PROCEDURE_REVISION_ATTRIBUTES = frozenset({"procedure_revision", "revision"})
OPEN_DISPOSITIONS = (ClaimDisposition.ACCEPTED, ClaimDisposition.CONFLICTING)


def change_event_type(attribute: str) -> EventType:
    if attribute == LOCATION:
        return EventType.OBJECT_MOVED
    if attribute in PROCEDURE_REVISION_ATTRIBUTES:
        return EventType.PROCEDURE_REVISION_DETECTED
    return EventType.OBJECT_STATE_CHANGED


@dataclass
class ClaimResult:
    decision: ClaimDecision
    reason: str
    events: List[Event] = field(default_factory=list)
    version_id: Optional[str] = None


class BeliefUpdater:
    def __init__(
        self,
        repository: Repository,
        freshness: FreshnessPolicyRegistry,
        policy: EvidencePolicy,
        evaluator: ClaimEvaluator,
    ):
        self.repo = repository
        self.freshness = freshness
        self.policy = policy
        self.evaluator = evaluator

    # ----------------------------------------------------------------- queries
    def open_versions(self, entity_id: str, attribute: str) -> List[StateVersion]:
        return [
            v
            for v in self.repo.get_state_versions_for_entity(entity_id, attribute)
            if v.valid_to is None and v.disposition in OPEN_DISPOSITIONS
        ]

    def open_conflict(self, entity_id: str, attribute: str) -> Optional[Conflict]:
        for c in self.repo.list_conflicts(entity_id=entity_id, attribute=attribute):
            if c.resolved_at is None:
                return c
        return None

    # ------------------------------------------------------------- apply claim
    def apply_claim(self, entity: Entity, attribute: str, claim: Claim, emit: bool = True) -> ClaimResult:
        if claim.evidence.id not in entity.evidence_refs:
            entity.evidence_refs.append(claim.evidence.id)
        current = self.open_versions(entity.id, attribute)
        conflict = self.open_conflict(entity.id, attribute)
        d = self.policy.decide(entity, attribute, claim, current, conflict)
        if d.decision == ClaimDecision.NEW:
            events, version_id = self._new(entity, attribute, claim, emit)
            return ClaimResult(d.decision, d.reason, events, version_id)
        handler = {
            ClaimDecision.CORROBORATE: self._corroborate,
            ClaimDecision.SUPERSEDE: self._supersede,
            ClaimDecision.CONTRADICT: self._contradict,
            ClaimDecision.CONFLICT_UPDATE: self._conflict_update,
            ClaimDecision.RESOLVE: self._resolve,
            ClaimDecision.UNCONFIRMED: self._unconfirmed,
        }[d.decision]
        events, version_id = handler(entity, attribute, claim, current, conflict, d)
        return ClaimResult(d.decision, d.reason, events, version_id)

    def _new(self, entity: Entity, attr: str, claim: Claim, emit: bool) -> Tuple[List[Event], Optional[str]]:
        v = self._new_version(entity, attr, claim, ClaimDisposition.ACCEPTED)
        before = entity.current_state.get(attr)
        entity.current_state[attr] = claim.value
        return ([self._change_event(entity, attr, before, claim)] if emit else []), v.id

    def _corroborate(self, entity, attr, claim, current, conflict, d):
        self._add_support(d.matching, claim)
        return [], d.matching.id

    def _supersede(self, entity, attr, claim, current, conflict, d):
        before = entity.current_state.get(attr)
        for c in current:
            self._close(c, claim.at)
        v = self._new_version(entity, attr, claim, ClaimDisposition.ACCEPTED)
        events: List[Event] = []
        if conflict is not None:
            events.append(self._resolve_conflict(entity, conflict, claim.at, v, d.reason, claim.evidence.id))
        entity.current_state[attr] = claim.value
        if before != claim.value:
            events.append(self._change_event(entity, attr, before, claim))
            events += self.propagate(entity.id, attr, claim.at, f"dependency {entity.id}.{attr} changed", inflight=entity)
        return events, v.id

    def _contradict(self, entity, attr, claim, current, conflict, d):
        cur = current[0]
        cur.disposition = ClaimDisposition.CONFLICTING
        self.repo.save_state_version(cur)
        v = self._new_version(entity, attr, claim, ClaimDisposition.CONFLICTING)
        new_conflict = self.repo.save_conflict(
            Conflict(entity_id=entity.id, attribute=attr, version_ids=[cur.id, v.id], opened_at=claim.at)
        )
        events = [self._conflict_event(entity, attr, cur.value, claim, new_conflict, d.reason)]
        events += self.propagate(entity.id, attr, claim.at, f"dependency {entity.id}.{attr} contradicted", inflight=entity)
        return events, v.id

    def _conflict_update(self, entity, attr, claim, current, conflict, d):
        for s in d.superseded:
            self._close(s, claim.at)
        events: List[Event] = []
        version_id = None
        if d.matching is not None and d.matching not in d.superseded:
            self._add_support(d.matching, claim)
            version_id = d.matching.id
        else:
            v = self._new_version(entity, attr, claim, ClaimDisposition.CONFLICTING)
            conflict.version_ids.append(v.id)
            version_id = v.id
        remaining = self.open_versions(entity.id, attr)
        if len({repr(v.value) for v in remaining}) == 1:
            winner = max(remaining, key=lambda v: v.valid_from)
            for other in remaining:
                if other.id != winner.id:
                    self._close(other, claim.at)
            before = entity.current_state.get(attr)
            events.append(
                self._resolve_conflict(entity, conflict, claim.at, winner, "remaining sources agree", claim.evidence.id)
            )
            entity.current_state[attr] = winner.value
            if before != winner.value:
                events.append(self._change_event(entity, attr, before, claim))
        else:
            self.repo.save_conflict(conflict)
            if d.matching is None:
                events.append(
                    self._conflict_event(entity, attr, entity.current_state.get(attr), claim, conflict, d.reason)
                )
        return events, version_id

    def _resolve(self, entity, attr, claim, current, conflict, d):
        if d.matching is not None:
            winner = d.matching
            self._add_support(winner, claim)
        else:
            winner = self._new_version(entity, attr, claim, ClaimDisposition.ACCEPTED)
        for side in current:
            if side.id != winner.id:
                self._close(side, claim.at, ClaimDisposition.REJECTED)
        before = entity.current_state.get(attr)
        events = [self._resolve_conflict(entity, conflict, claim.at, winner, d.reason, claim.evidence.id)]
        entity.current_state[attr] = winner.value
        if before != winner.value:
            events.append(self._change_event(entity, attr, before, claim))
        return events, winner.id

    def _unconfirmed(self, entity, attr, claim, current, conflict, d):
        v = self._new_version(entity, attr, claim, ClaimDisposition.UNCONFIRMED, valid_to=claim.at)
        v.invalidation_reason = d.reason
        self.repo.save_state_version(v)
        event = self.repo.save_event(
            Event(
                timestamp=claim.at,
                event_type=EventType.UNCONFIRMED_CHANGE,
                entity_id=entity.id,
                before_state={attr: entity.current_state.get(attr)},
                after_state={attr: claim.value},
                evidence_refs=[claim.evidence.id],
                description=f"Unconfirmed claim {entity.id}.{attr}={claim.value!r}: {d.reason}.",
            )
        )
        return [event], v.id

    # ------------------------------------------------------------ invalidation
    def invalidate(
        self,
        entity_id: str,
        attributes: Optional[Iterable[str]],
        at: datetime,
        reason: str,
        evidence_refs: Optional[List[str]] = None,
        inflight: Optional[Entity] = None,
        _visited: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[Event]:
        visited = _visited if _visited is not None else set()
        if attributes is None:
            attributes = sorted({v.attribute for v in self.repo.get_state_versions_for_entity(entity_id)})
        events: List[Event] = []
        for attr in attributes:
            if (entity_id, attr) in visited:
                continue
            visited.add((entity_id, attr))
            opens = self.open_versions(entity_id, attr)
            if not opens:
                continue
            for v in opens:
                v.invalidated_at = at
                v.invalidation_reason = reason
                self.repo.save_state_version(v)
            conflict = self.open_conflict(entity_id, attr)
            if conflict is not None:
                conflict.resolved_at = at
                conflict.resolution_reason = f"invalidated: {reason}"
                self.repo.save_conflict(conflict)
            events.append(
                self.repo.save_event(
                    Event(
                        timestamp=at,
                        event_type=EventType.STATE_INVALIDATED,
                        entity_id=entity_id,
                        before_state={attr: opens[0].value},
                        after_state={attr: opens[0].value, "invalidated": True},
                        evidence_refs=list(evidence_refs or []),
                        description=f"{entity_id}.{attr} invalidated: {reason}.",
                    )
                )
            )
            events += self.propagate(entity_id, attr, at, f"dependency {entity_id}.{attr} invalidated", inflight, visited)
        if inflight is None or inflight.id != entity_id:
            entity = self.repo.get_entity(entity_id)
            if entity is not None:
                entity.updated_at = max(entity.updated_at, at)
                self.materialize(entity, entity.updated_at)
                self.repo.save_entity(entity)
        return events

    def propagate(
        self,
        entity_id: str,
        attribute: str,
        at: datetime,
        reason: str,
        inflight: Optional[Entity] = None,
        visited: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[Event]:
        visited = visited if visited is not None else {(entity_id, attribute)}
        targets = [
            (d.dependent_entity_id, d.dependent_attribute)
            for d in self.repo.list_dependencies(depends_on_entity_id=entity_id, depends_on_attribute=attribute)
        ]
        entity = inflight if inflight is not None and inflight.id == entity_id else self.repo.get_entity(entity_id)
        if entity is not None:
            attrs = {v.attribute for v in self.repo.get_state_versions_for_entity(entity_id)}
            targets += [
                (entity_id, a)
                for a in sorted(attrs)
                if attribute in self.freshness.policy_for(entity, a).invalidation_triggers
            ]
        events: List[Event] = []
        for dep_entity, dep_attr in targets:
            events += self.invalidate(dep_entity, [dep_attr], at, reason, inflight=inflight, _visited=visited)
        return events

    # ----------------------------------------------------------------- absence
    def apply_absence(self, entity: Entity, region: str, at: datetime, evidence_id: str, coverage_id: str) -> List[Event]:
        """Coverage-validated absence: close the location claim without inventing a new
        location. Whereabouts become UNKNOWN; the closed claim records why."""
        opens = self.open_versions(entity.id, LOCATION)
        if not opens:
            return []
        reason = f"confirmed absent from {region} by search {coverage_id}"
        before = entity.current_state.get(LOCATION)
        for v in opens:
            v.valid_to = at
            v.invalidated_at = at
            v.invalidation_reason = reason
            self.repo.save_state_version(v)
        conflict = self.open_conflict(entity.id, LOCATION)
        if conflict is not None:
            conflict.resolved_at = at
            conflict.resolution_reason = reason
            self.repo.save_conflict(conflict)
        if evidence_id not in entity.evidence_refs:
            entity.evidence_refs.append(evidence_id)
        event = self.repo.save_event(
            Event(
                timestamp=at,
                event_type=EventType.OBJECT_REMOVED_OR_UNOBSERVED,
                entity_id=entity.id,
                before_state={LOCATION: before},
                after_state={LOCATION: None, "absence": "CONFIRMED_ABSENT", "region": region, "coverage_id": coverage_id},
                evidence_refs=[evidence_id],
                description=f"{entity.id} {reason}; current location unknown.",
            )
        )
        return [event] + self.propagate(entity.id, LOCATION, at, f"dependency {entity.id}.location: {reason}", inflight=entity)

    # ------------------------------------------------------------ materialise
    def materialize(self, entity: Entity, as_of: datetime) -> None:
        """Refresh the entity's cached current_state / statuses from its versions."""
        attrs = sorted({v.attribute for v in self.repo.get_state_versions_for_entity(entity.id)})
        for attr in attrs:
            assessment = self.evaluator.assess_attribute(entity.id, attr, as_of)
            if assessment.last_known_value is None and assessment.status == EpistemicStatus.UNKNOWN and attr not in entity.current_state:
                continue  # only unconfirmed claims exist; nothing is believed yet
            entity.attribute_statuses[attr] = assessment.status
            entity.current_state[attr] = assessment.last_known_value if assessment.has_current_claim else None
        entity.status = aggregate_status(entity.attribute_statuses.values())

    def force_conflict(self, entity: Entity, attr: str, claim: Claim) -> Event:
        """Record an externally detected contradiction regardless of policy."""
        current = self.open_versions(entity.id, attr)
        conflict = self.open_conflict(entity.id, attr)
        v = self._new_version(entity, attr, claim, ClaimDisposition.CONFLICTING)
        if conflict is None:
            for c in current:
                c.disposition = ClaimDisposition.CONFLICTING
                self.repo.save_state_version(c)
            conflict = Conflict(
                entity_id=entity.id, attribute=attr, version_ids=[c.id for c in current] + [v.id], opened_at=claim.at
            )
        else:
            conflict.version_ids.append(v.id)
        self.repo.save_conflict(conflict)
        if claim.evidence.id not in entity.evidence_refs:
            entity.evidence_refs.append(claim.evidence.id)
        return self._conflict_event(entity, attr, entity.current_state.get(attr), claim, conflict, "reported contradiction")

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _support_ref(claim: Claim) -> SupportRef:
        return SupportRef(
            evidence_id=claim.evidence.id,
            at=claim.at,
            source=claim.evidence.source,
            source_type=claim.evidence.source_type,
            strength=claim.strength,
        )

    def _new_version(
        self,
        entity: Entity,
        attr: str,
        claim: Claim,
        disposition: ClaimDisposition,
        valid_to: Optional[datetime] = None,
    ) -> StateVersion:
        status = EpistemicStatus.VERIFIED if claim.is_verification else claim.grade
        v = StateVersion(
            entity_id=entity.id,
            attribute=attr,
            value=claim.value,
            status=status,
            disposition=disposition,
            valid_from=claim.at,
            valid_to=valid_to,
            supported_by=[claim.evidence.id],
            support=[self._support_ref(claim)],
            last_supported_at=claim.at,
            last_validated_at=claim.at if claim.is_verification else None,
            volatility_class=self.freshness.policy_for(entity, attr).volatility,
        )
        self.repo.save_state_version(v)
        entity.history_refs.append(v.id)
        return v

    def _add_support(self, version: StateVersion, claim: Claim) -> None:
        if claim.evidence.id not in version.supported_by:
            version.supported_by.append(claim.evidence.id)
            version.support.append(self._support_ref(claim))
        if claim.strength >= WEAK_STRENGTH:
            version.last_supported_at = max(version.last_supported_at or claim.at, claim.at)
        if claim.is_verification:
            version.status = EpistemicStatus.VERIFIED
            version.last_validated_at = claim.at
        elif claim.grade == EpistemicStatus.VERIFIED:
            version.status = EpistemicStatus.VERIFIED
        elif version.status == EpistemicStatus.UNKNOWN and claim.grade == EpistemicStatus.OBSERVED:
            version.status = EpistemicStatus.OBSERVED
        elif version.status == EpistemicStatus.OBSERVED and self._independently_corroborated(version):
            version.status = EpistemicStatus.VERIFIED
        self.repo.save_state_version(version)

    @staticmethod
    def _independently_corroborated(version: StateVersion) -> bool:
        """Two distinct strong direct sources agreeing count as strong evidence."""
        return independently_corroborated(version.support)

    def _close(self, version: StateVersion, at: datetime, disposition: Optional[ClaimDisposition] = None) -> None:
        version.valid_to = at
        if disposition is not None:
            version.disposition = disposition
        self.repo.save_state_version(version)

    def _resolve_conflict(
        self,
        entity: Entity,
        conflict: Conflict,
        at: datetime,
        winner: Optional[StateVersion],
        reason: str,
        evidence_id: Optional[str],
    ) -> Event:
        conflict.resolved_at = at
        conflict.resolution_reason = reason
        conflict.resolution_evidence = evidence_id
        if winner is not None:
            conflict.resolution_version_id = winner.id
            fresh = next(v for v in self.repo.get_state_versions_for_entity(entity.id, conflict.attribute) if v.id == winner.id)
            fresh.disposition = ClaimDisposition.ACCEPTED
            self.repo.save_state_version(fresh)
        self.repo.save_conflict(conflict)
        return self.repo.save_event(
            Event(
                timestamp=at,
                event_type=EventType.CONFLICT_RESOLVED,
                entity_id=entity.id,
                before_state={conflict.attribute: "CONTRADICTED"},
                after_state={conflict.attribute: winner.value if winner else None},
                evidence_refs=[evidence_id] if evidence_id else [],
                description=f"Conflict {conflict.id} on {entity.id}.{conflict.attribute} resolved: {reason}.",
            )
        )

    def _change_event(self, entity: Entity, attr: str, before: Any, claim: Claim) -> Event:
        etype = change_event_type(attr)
        verb = "moved from" if etype == EventType.OBJECT_MOVED else f"'{attr}' changed from"
        return self.repo.save_event(
            Event(
                timestamp=claim.at,
                event_type=etype,
                entity_id=entity.id,
                before_state={attr: before},
                after_state={attr: claim.value},
                evidence_refs=[claim.evidence.id],
                description=f"Entity {entity.id} {verb} {before} to {claim.value}.",
            )
        )

    def _conflict_event(self, entity: Entity, attr: str, existing: Any, claim: Claim, conflict: Conflict, reason: str) -> Event:
        return self.repo.save_event(
            Event(
                timestamp=claim.at,
                event_type=EventType.EVIDENCE_CONFLICT,
                entity_id=entity.id,
                before_state={attr: existing},
                after_state={attr: f"CONFLICT({existing} vs {claim.value})"},
                evidence_refs=[claim.evidence.id],
                description=(
                    f"Contradiction on {entity.id}.{attr}: {existing!r} vs {claim.value!r} "
                    f"({claim.evidence.source}; {reason}). Conflict {conflict.id}."
                ),
            )
        )
