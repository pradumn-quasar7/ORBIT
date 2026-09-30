from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.domain.types import EpistemicStatus, EventType, VolatilityClass
from backend.app.domain.models import (
    Entity,
    ObservedEntity,
    Observation,
    Evidence,
    StateVersion,
    Event,
    Relation,
    WorldDiff,
    WorldChange,
    FreshnessPolicy,
    generate_id,
)
from backend.app.repositories.in_memory_repository import InMemoryRepository


class WorldStateEngine:
    def __init__(self, repository: Optional[InMemoryRepository] = None):
        self.repo = repository or InMemoryRepository()

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

    def record_observation(self, observation: Observation) -> List[Event]:
        # 1. Persist observation
        self.repo.save_observation(observation)

        # 2. Create primary evidence record for this observation
        evidence = self.record_evidence(
            source_type=observation.source,
            source_reference=observation.id,
            timestamp=observation.timestamp,
            quality=observation.quality,
            authority=observation.authority,
            provenance=observation.provenance,
        )

        generated_events: List[Event] = []

        # 3. Process each observed entity
        for observed in observation.observed_entities:
            entity_id = observed.candidate_entity_id
            existing_entity = self.repo.get_entity(entity_id) if entity_id else None

            if existing_entity is None:
                # Create new Entity
                new_entity_id = entity_id or generate_id(f"entity_{observed.type}")
                current_state: Dict[str, Any] = {}
                attribute_statuses: Dict[str, EpistemicStatus] = {}

                # Authoritative sources (digital registry, verified procedure) give VERIFIED, direct observations give OBSERVED
                is_authoritative = observation.source in ["digital_registry", "manual_verification", "authoritative_record"] or observation.authority > 1.0
                initial_status = EpistemicStatus.VERIFIED if is_authoritative else EpistemicStatus.OBSERVED

                # Set initial location if present
                if observed.location:
                    current_state["location"] = observed.location
                    attribute_statuses["location"] = initial_status

                # Set other attributes
                for k, v in observed.attributes.items():
                    current_state[k] = v
                    attribute_statuses[k] = initial_status

                # Default freshness policies
                freshness_policies: Dict[str, FreshnessPolicy] = {
                    "location": FreshnessPolicy(volatility=VolatilityClass.MEDIUM, ttl_seconds=86400.0),
                    "configuration": FreshnessPolicy(volatility=VolatilityClass.LOW, ttl_seconds=604800.0),
                    "power": FreshnessPolicy(volatility=VolatilityClass.HIGH, ttl_seconds=300.0),
                }

                entity = Entity(
                    id=new_entity_id,
                    type=observed.type,
                    name=observed.name or observed.type,
                    geometry=observed.geometry,
                    anchor=observed.anchor or observed.location,
                    current_state=current_state,
                    attribute_statuses=attribute_statuses,
                    status=EpistemicStatus.VERIFIED if observation.authority >= 1.0 else EpistemicStatus.OBSERVED,
                    observed_at=observation.timestamp,
                    freshness_policies=freshness_policies,
                    evidence_refs=[evidence.id],
                    history_refs=[],
                    created_at=observation.timestamp,
                    updated_at=observation.timestamp,
                )

                # Create StateVersion for each initial attribute
                for attr, val in current_state.items():
                    sv = StateVersion(
                        entity_id=entity.id,
                        attribute=attr,
                        value=val,
                        status=attribute_statuses[attr],
                        valid_from=observation.timestamp,
                        supported_by=[evidence.id],
                    )
                    self.repo.save_state_version(sv)
                    entity.history_refs.append(sv.id)

                self.repo.save_entity(entity)

                # OBJECT_ADDED Event
                add_event = Event(
                    timestamp=observation.timestamp,
                    event_type=EventType.OBJECT_ADDED,
                    entity_id=entity.id,
                    before_state=None,
                    after_state=dict(current_state),
                    evidence_refs=[evidence.id],
                    description=f"Entity {entity.id} ({entity.name}) added to world state.",
                )
                self.repo.save_event(add_event)
                generated_events.append(add_event)

            else:
                # Entity already exists: Update state and detect transitions
                entity = existing_entity
                entity.observed_at = observation.timestamp
                entity.updated_at = observation.timestamp
                if evidence.id not in entity.evidence_refs:
                    entity.evidence_refs.append(evidence.id)

                is_authoritative = observation.source in ["digital_registry", "manual_verification", "authoritative_record"] or observation.authority > 1.0

                # Check location change
                if observed.location and observed.location != entity.current_state.get("location"):
                    old_location = entity.current_state.get("location")
                    new_location = observed.location

                    # Close previous StateVersion for location
                    prev_versions = self.repo.get_state_versions_for_entity(entity.id, attribute="location")
                    if prev_versions:
                        last_sv = prev_versions[-1]
                        if last_sv.valid_to is None:
                            last_sv.valid_to = observation.timestamp
                            self.repo.save_state_version(last_sv)

                    # Create new StateVersion
                    loc_status = EpistemicStatus.VERIFIED if is_authoritative else EpistemicStatus.OBSERVED
                    new_sv = StateVersion(
                        entity_id=entity.id,
                        attribute="location",
                        value=new_location,
                        status=loc_status,
                        valid_from=observation.timestamp,
                        supported_by=[evidence.id],
                    )
                    self.repo.save_state_version(new_sv)
                    entity.history_refs.append(new_sv.id)

                    # Update current state
                    entity.current_state["location"] = new_location
                    entity.attribute_statuses["location"] = loc_status
                    entity.anchor = observed.anchor or new_location

                    # Emit OBJECT_MOVED event
                    move_event = Event(
                        timestamp=observation.timestamp,
                        event_type=EventType.OBJECT_MOVED,
                        entity_id=entity.id,
                        before_state={"location": old_location},
                        after_state={"location": new_location},
                        evidence_refs=[evidence.id],
                        description=f"Entity {entity.id} moved from {old_location} to {new_location}.",
                    )
                    self.repo.save_event(move_event)
                    generated_events.append(move_event)

                # Check other attributes
                for attr, new_val in observed.attributes.items():
                    old_val = entity.current_state.get(attr)
                    if old_val != new_val:
                        # Close previous StateVersion
                        prev_versions = self.repo.get_state_versions_for_entity(entity.id, attribute=attr)
                        if prev_versions:
                            last_sv = prev_versions[-1]
                            if last_sv.valid_to is None:
                                last_sv.valid_to = observation.timestamp
                                self.repo.save_state_version(last_sv)

                        attr_status = (
                            EpistemicStatus.VERIFIED if is_authoritative else EpistemicStatus.OBSERVED
                        )
                        new_sv = StateVersion(
                            entity_id=entity.id,
                            attribute=attr,
                            value=new_val,
                            status=attr_status,
                            valid_from=observation.timestamp,
                            supported_by=[evidence.id],
                        )
                        self.repo.save_state_version(new_sv)
                        entity.history_refs.append(new_sv.id)

                        entity.current_state[attr] = new_val
                        entity.attribute_statuses[attr] = attr_status

                        state_event = Event(
                            timestamp=observation.timestamp,
                            event_type=EventType.OBJECT_STATE_CHANGED,
                            entity_id=entity.id,
                            before_state={attr: old_val},
                            after_state={attr: new_val},
                            evidence_refs=[evidence.id],
                            description=f"Entity {entity.id} attribute '{attr}' changed from {old_val} to {new_val}.",
                        )
                        self.repo.save_event(state_event)
                        generated_events.append(state_event)

                self.repo.save_entity(entity)

        return generated_events

    def record_contradiction(
        self,
        entity_id: str,
        attribute: str,
        conflicting_value: Any,
        evidence: Evidence,
    ) -> Event:
        """
        Record a contradiction when new evidence conflicts with current verified or observed state
        without authoritative superseding. Rule 2.8: Contradictions are retained, not silently picked.
        """
        entity = self.repo.get_entity(entity_id)
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")

        current_val = entity.current_state.get(attribute)
        entity.attribute_statuses[attribute] = EpistemicStatus.CONTRADICTED
        entity.status = EpistemicStatus.CONTRADICTED
        if evidence.id not in entity.evidence_refs:
            entity.evidence_refs.append(evidence.id)

        # Create a conflicting StateVersion
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

        conflict_event = Event(
            timestamp=evidence.timestamp,
            event_type=EventType.EVIDENCE_CONFLICT,
            entity_id=entity.id,
            before_state={attribute: current_val},
            after_state={attribute: f"CONFLICT({current_val} vs {conflicting_value})"},
            evidence_refs=[evidence.id],
            description=f"Contradiction detected on {entity.id}.{attribute}: '{current_val}' vs '{conflicting_value}'",
        )
        self.repo.save_event(conflict_event)
        return conflict_event

    def evaluate_freshness(self, entity_id: str, as_of: datetime) -> Dict[str, EpistemicStatus]:
        """
        Evaluate freshness of each attribute according to its policy (Rule 2.6).
        Marks expired attributes as STALE.
        """
        entity = self.repo.get_entity(entity_id)
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")

        updated_statuses = dict(entity.attribute_statuses)
        for attr, policy in entity.freshness_policies.items():
            if policy.ttl_seconds is None:
                continue

            # Look up the latest valid state version for this attribute
            versions = self.repo.get_state_versions_for_entity(entity_id, attribute=attr)
            if not versions:
                continue

            latest_version = versions[-1]
            # Ensure timestamps can be compared (naive or timezone-aware alignment)
            v_time = latest_version.valid_from
            if v_time.tzinfo is None and as_of.tzinfo is not None:
                as_of_cmp = as_of.replace(tzinfo=None)
            elif v_time.tzinfo is not None and as_of.tzinfo is None:
                as_of_cmp = as_of.replace(tzinfo=v_time.tzinfo)
            else:
                as_of_cmp = as_of

            age_seconds = (as_of_cmp - v_time).total_seconds()
            if age_seconds > policy.ttl_seconds:
                updated_statuses[attr] = EpistemicStatus.STALE
                latest_version.status = EpistemicStatus.STALE
                latest_version.invalidation_reason = f"TTL expired ({age_seconds:.0f}s > {policy.ttl_seconds:.0f}s)"
                self.repo.save_state_version(latest_version)

        entity.attribute_statuses = updated_statuses
        if any(s == EpistemicStatus.STALE for s in updated_statuses.values()):
            if entity.status != EpistemicStatus.CONTRADICTED:
                entity.status = EpistemicStatus.STALE

        self.repo.save_entity(entity)
        return updated_statuses

    def compute_world_diff(
        self,
        baseline_timestamp: Optional[datetime],
        target_timestamp: datetime,
    ) -> WorldDiff:
        """
        Compute structured differences between baseline and target time (Section 12 & 24).
        """
        all_events = self.repo.list_events()
        diff_changes: List[WorldChange] = []

        for evt in all_events:
            evt_time = evt.timestamp
            # Time alignment check
            if baseline_timestamp:
                b_time = baseline_timestamp
                if evt_time.tzinfo is None and b_time.tzinfo is not None:
                    b_time = b_time.replace(tzinfo=None)
                elif evt_time.tzinfo is not None and b_time.tzinfo is None:
                    evt_time = evt_time.replace(tzinfo=None)
                if evt_time < b_time:
                    continue

            t_time = target_timestamp
            if evt_time.tzinfo is None and t_time.tzinfo is not None:
                t_time = t_time.replace(tzinfo=None)
            elif evt_time.tzinfo is not None and t_time.tzinfo is None:
                evt_time = evt_time.replace(tzinfo=None)

            if evt_time <= t_time:
                attr_name = "location" if evt.event_type == EventType.OBJECT_MOVED else None
                before_val = evt.before_state
                after_val = evt.after_state
                if evt.before_state and len(evt.before_state) == 1:
                    attr_name = list(evt.before_state.keys())[0]
                    before_val = evt.before_state[attr_name]
                if evt.after_state and len(evt.after_state) == 1:
                    if not attr_name:
                        attr_name = list(evt.after_state.keys())[0]
                    after_val = evt.after_state[attr_name]

                change = WorldChange(
                    change_type=evt.event_type,
                    entity_id=evt.entity_id or "unknown",
                    attribute=attr_name,
                    before=before_val,
                    after=after_val,
                    evidence_refs=evt.evidence_refs,
                    timestamp=evt.timestamp,
                )
                diff_changes.append(change)

        world_diff = WorldDiff(
            baseline_timestamp=baseline_timestamp,
            target_timestamp=target_timestamp,
            changes=diff_changes,
            created_at=target_timestamp,
        )
        self.repo.save_world_diff(world_diff)
        return world_diff
