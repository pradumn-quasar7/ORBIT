from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.app.domain.models import (
    Entity,
    Event,
    Evidence,
    FreshnessPolicy,
    Observation,
    ObservedEntity,
    StateVersion,
    WorldChange,
    WorldDiff,
    generate_id,
)
from backend.app.domain.status import aggregate_status, classify_source_status
from backend.app.domain.types import EpistemicStatus, EventType, VolatilityClass
from backend.app.repositories.base import Repository
from backend.app.repositories.in_memory_repository import InMemoryRepository

LOCATION = "location"


class DuplicateObservationError(ValueError):
    pass


class EntityNotFoundError(ValueError):
    pass


def default_freshness_policies() -> Dict[str, FreshnessPolicy]:
    return {
        LOCATION: FreshnessPolicy(volatility=VolatilityClass.MEDIUM, ttl_seconds=86400.0),
        "configuration": FreshnessPolicy(volatility=VolatilityClass.LOW, ttl_seconds=604800.0),
        "power": FreshnessPolicy(volatility=VolatilityClass.HIGH, ttl_seconds=300.0),
    }


class WorldStateEngine:
    def __init__(self, repository: Optional[Repository] = None):
        self.repo: Repository = repository or InMemoryRepository()

    # ------------------------------------------------------------------ evidence
    def record_evidence(
        self,
        source_type: str,
        source_reference: str,
        timestamp: datetime,
        quality: float = 1.0,
        authority: float = 1.0,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Evidence:
        evidence = Evidence(
            id=generate_id("evi"),
            source_type=source_type,
            source_reference=source_reference,
            timestamp=timestamp,
            quality=quality,
            authority=authority,
            provenance=provenance or {},
        )
        return self.repo.save_evidence(evidence)

    # --------------------------------------------------------------- observation
    def record_observation(self, observation: Observation) -> List[Event]:
        if self.repo.get_observation(observation.id) is not None:
            raise DuplicateObservationError(f"Observation {observation.id} already recorded")

        with self.repo.transaction():
            self.repo.save_observation(observation)
            evidence = self.record_evidence(
                source_type=observation.source,
                source_reference=observation.id,
                timestamp=observation.timestamp,
                quality=observation.quality,
                authority=observation.authority,
                provenance=observation.provenance,
            )
            status = classify_source_status(observation.source, observation.authority)

            events: List[Event] = []
            for observed in observation.observed_entities:
                existing = (
                    self.repo.get_entity(observed.candidate_entity_id)
                    if observed.candidate_entity_id
                    else None
                )
                if existing is None:
                    events.append(self._create_entity(observed, observation, evidence, status))
                else:
                    events.extend(self._update_entity(existing, observed, observation, evidence, status))
            return events

    def register_entity(self, observed: ObservedEntity, observation: Observation) -> Entity:
        """Explicit registration still goes through evidence (ADR-001): it is recorded as
        an observation from its declared source so the initial state is traceable."""
        if observed.candidate_entity_id and self.repo.get_entity(observed.candidate_entity_id):
            raise ValueError(f"Entity {observed.candidate_entity_id} already exists")
        observation.observed_entities = [observed]
        events = self.record_observation(observation)
        entity = self.repo.get_entity(events[0].entity_id)  # type: ignore[arg-type]
        assert entity is not None
        return entity

    def _create_entity(
        self,
        observed: ObservedEntity,
        observation: Observation,
        evidence: Evidence,
        status: EpistemicStatus,
    ) -> Event:
        entity_id = observed.candidate_entity_id or generate_id(f"entity_{observed.type}")
        current_state: Dict[str, Any] = {}
        if observed.location:
            current_state[LOCATION] = observed.location
        current_state.update(observed.attributes)
        attribute_statuses = {attr: status for attr in current_state}

        entity = Entity(
            id=entity_id,
            type=observed.type,
            name=observed.name or observed.type,
            geometry=observed.geometry,
            anchor=observed.anchor or observed.location,
            current_state=current_state,
            attribute_statuses=attribute_statuses,
            status=aggregate_status(attribute_statuses.values()),
            observed_at=observation.timestamp,
            freshness_policies=default_freshness_policies(),
            evidence_refs=[evidence.id],
            created_at=observation.timestamp,
            updated_at=observation.timestamp,
        )
        for attr, val in current_state.items():
            sv = StateVersion(
                entity_id=entity.id,
                attribute=attr,
                value=val,
                status=status,
                valid_from=observation.timestamp,
                supported_by=[evidence.id],
            )
            self.repo.save_state_version(sv)
            entity.history_refs.append(sv.id)
        self.repo.save_entity(entity)

        event = Event(
            timestamp=observation.timestamp,
            event_type=EventType.OBJECT_ADDED,
            entity_id=entity.id,
            before_state=None,
            after_state=dict(current_state),
            evidence_refs=[evidence.id],
            description=f"Entity {entity.id} ({entity.name}) added to world state.",
        )
        return self.repo.save_event(event)

    def _update_entity(
        self,
        entity: Entity,
        observed: ObservedEntity,
        observation: Observation,
        evidence: Evidence,
        status: EpistemicStatus,
    ) -> List[Event]:
        entity.observed_at = observation.timestamp
        entity.updated_at = observation.timestamp
        if evidence.id not in entity.evidence_refs:
            entity.evidence_refs.append(evidence.id)

        claims: Dict[str, Any] = {}
        if observed.location:
            claims[LOCATION] = observed.location
        claims.update(observed.attributes)

        events: List[Event] = []
        for attr, new_val in claims.items():
            old_val = entity.current_state.get(attr)
            if old_val == new_val:
                continue
            self._close_open_version(entity.id, attr, observation.timestamp)
            sv = StateVersion(
                entity_id=entity.id,
                attribute=attr,
                value=new_val,
                status=status,
                valid_from=observation.timestamp,
                supported_by=[evidence.id],
            )
            self.repo.save_state_version(sv)
            entity.history_refs.append(sv.id)
            entity.current_state[attr] = new_val
            entity.attribute_statuses[attr] = status

            if attr == LOCATION:
                entity.anchor = observed.anchor or new_val
                event_type = EventType.OBJECT_MOVED
                description = f"Entity {entity.id} moved from {old_val} to {new_val}."
            else:
                event_type = EventType.OBJECT_STATE_CHANGED
                description = f"Entity {entity.id} attribute '{attr}' changed from {old_val} to {new_val}."
            events.append(
                self.repo.save_event(
                    Event(
                        timestamp=observation.timestamp,
                        event_type=event_type,
                        entity_id=entity.id,
                        before_state={attr: old_val},
                        after_state={attr: new_val},
                        evidence_refs=[evidence.id],
                        description=description,
                    )
                )
            )

        entity.status = aggregate_status(entity.attribute_statuses.values())
        self.repo.save_entity(entity)
        return events

    def _close_open_version(self, entity_id: str, attribute: str, at: datetime) -> None:
        for sv in self.repo.get_state_versions_for_entity(entity_id, attribute=attribute):
            if sv.valid_to is None:
                sv.valid_to = at
                self.repo.save_state_version(sv)

    # ------------------------------------------------------------- contradiction
    def record_contradiction(
        self,
        entity_id: str,
        attribute: str,
        conflicting_value: Any,
        evidence: Evidence,
    ) -> Event:
        """Rule 2.8: contradictions are retained, never silently resolved."""
        entity = self._require_entity(entity_id)
        with self.repo.transaction():
            current_val = entity.current_state.get(attribute)
            entity.attribute_statuses[attribute] = EpistemicStatus.CONTRADICTED
            entity.status = aggregate_status(entity.attribute_statuses.values())
            if evidence.id not in entity.evidence_refs:
                entity.evidence_refs.append(evidence.id)

            conflict_sv = StateVersion(
                entity_id=entity.id,
                attribute=attribute,
                value=conflicting_value,
                status=EpistemicStatus.CONTRADICTED,
                valid_from=evidence.timestamp,
                supported_by=[evidence.id],
                invalidation_reason=f"Conflicts with existing value: {current_val}",
            )
            self.repo.save_state_version(conflict_sv)
            entity.history_refs.append(conflict_sv.id)
            self.repo.save_entity(entity)

            return self.repo.save_event(
                Event(
                    timestamp=evidence.timestamp,
                    event_type=EventType.EVIDENCE_CONFLICT,
                    entity_id=entity.id,
                    before_state={attribute: current_val},
                    after_state={attribute: f"CONFLICT({current_val} vs {conflicting_value})"},
                    evidence_refs=[evidence.id],
                    description=(
                        f"Contradiction detected on {entity.id}.{attribute}: "
                        f"'{current_val}' vs '{conflicting_value}'"
                    ),
                )
            )

    # ----------------------------------------------------------------- freshness
    def evaluate_freshness(self, entity_id: str, as_of: datetime) -> Dict[str, EpistemicStatus]:
        """Rule 2.6: freshness is attribute-specific. Expired attributes become STALE."""
        entity = self._require_entity(entity_id)
        with self.repo.transaction():
            for attr, policy in entity.freshness_policies.items():
                if policy.ttl_seconds is None:
                    continue
                versions = self.repo.get_state_versions_for_entity(entity_id, attribute=attr)
                if not versions:
                    continue
                latest = versions[-1]
                age_seconds = (as_of - latest.valid_from).total_seconds()
                if age_seconds > policy.ttl_seconds:
                    entity.attribute_statuses[attr] = EpistemicStatus.STALE
                    latest.status = EpistemicStatus.STALE
                    latest.invalidation_reason = (
                        f"TTL expired ({age_seconds:.0f}s > {policy.ttl_seconds:.0f}s)"
                    )
                    self.repo.save_state_version(latest)
            entity.status = aggregate_status(entity.attribute_statuses.values())
            self.repo.save_entity(entity)
        return dict(entity.attribute_statuses)

    # ---------------------------------------------------------------- world diff
    def compute_world_diff(
        self,
        baseline_timestamp: Optional[datetime],
        target_timestamp: datetime,
    ) -> WorldDiff:
        """Events in (baseline, target]. Phase 4 replaces this with a snapshot diff."""
        changes: List[WorldChange] = []
        for evt in self.repo.list_events():
            if baseline_timestamp is not None and evt.timestamp < baseline_timestamp:
                continue
            if evt.timestamp > target_timestamp:
                continue
            attr_name = LOCATION if evt.event_type == EventType.OBJECT_MOVED else None
            before_val: Any = evt.before_state
            after_val: Any = evt.after_state
            if evt.before_state and len(evt.before_state) == 1:
                attr_name = next(iter(evt.before_state))
                before_val = evt.before_state[attr_name]
            if evt.after_state and len(evt.after_state) == 1:
                attr_name = attr_name or next(iter(evt.after_state))
                after_val = evt.after_state[attr_name]
            changes.append(
                WorldChange(
                    change_type=evt.event_type,
                    entity_id=evt.entity_id or "unknown",
                    attribute=attr_name,
                    before=before_val,
                    after=after_val,
                    evidence_refs=evt.evidence_refs,
                    timestamp=evt.timestamp,
                )
            )
        diff = WorldDiff(
            baseline_timestamp=baseline_timestamp,
            target_timestamp=target_timestamp,
            changes=changes,
            created_at=target_timestamp,
        )
        return self.repo.save_world_diff(diff)

    def _require_entity(self, entity_id: str) -> Entity:
        entity = self.repo.get_entity(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        return entity
