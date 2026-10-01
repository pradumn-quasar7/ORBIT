"""World State Engine: orchestrates B_t = Update(B_t-1, O_t, context, evidence, time).

For each observation it records evidence (with an integrity hash), resolves identity
for every detection, turns detections into per-attribute claims and hands each claim
to the ``BeliefUpdater``, which applies the deterministic evidence policy. Other entry
points (claims from records/people, interventions, dependencies) share that path so
every state change is evidence-backed and auditable.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Union

from backend.app.domain.models import (
    ClaimDependency,
    Entity,
    EntityResolution,
    Event,
    Evidence,
    Observation,
    ObservedEntity,
    Session,
    WorldDiff,
    generate_id,
)
from backend.app.domain.status import aggregate_status
from backend.app.domain.types import ClaimDecision, EpistemicStatus, EventType, ResolutionMethod, SourceType
from backend.app.repositories.base import Repository
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.services.belief import LOCATION, BeliefUpdater, ClaimResult
from backend.app.services.claims import ClaimEvaluator
from backend.app.services.entity_registry import EntityRegistry, Resolution
from backend.app.services.evidence_policy import Claim, EvidencePolicy, canonical_hash, evidence_integrity, grade, infer_source_type
from backend.app.services.freshness import FreshnessPolicyRegistry
from backend.app.services.relations import RelationService
from backend.app.services.spatial import AnchorRegistry

LOCATION_DECISIONS = frozenset({ClaimDecision.NEW, ClaimDecision.SUPERSEDE, ClaimDecision.RESOLVE})


class DuplicateObservationError(ValueError):
    pass


class EntityNotFoundError(ValueError):
    pass


class SimulationEvidenceRejected(ValueError):
    """Counterfactual premises must never enter real memory (ADR-029)."""


class WorldStateEngine:
    def __init__(
        self,
        repository: Optional[Repository] = None,
        anchors: Optional[AnchorRegistry] = None,
        registry: Optional[EntityRegistry] = None,
        relations: Optional[RelationService] = None,
        freshness: Optional[FreshnessPolicyRegistry] = None,
        policy: Optional[EvidencePolicy] = None,
        sandbox: bool = False,
    ):
        self.repo: Repository = repository or InMemoryRepository()
        # Only a counterfactual sandbox may accept SIMULATION evidence.
        self.sandbox = sandbox
        self.anchors = anchors or AnchorRegistry(self.repo)
        self.registry = registry or EntityRegistry(self.repo, self.anchors)
        self.relations = relations or RelationService(self.repo)
        self.freshness = freshness or FreshnessPolicyRegistry()
        self.policy = policy or EvidencePolicy(self.freshness)
        self.claims = ClaimEvaluator(self.repo, self.freshness)
        self.belief = BeliefUpdater(self.repo, self.freshness, self.policy, self.claims)

    # ------------------------------------------------------------------ evidence
    def record_evidence(
        self,
        source_type: Union[SourceType, str],
        source_reference: str,
        timestamp: datetime,
        quality: float = 1.0,
        authority: float = 1.0,
        provenance: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        subject: Any = None,
    ) -> Evidence:
        if isinstance(source_type, SourceType):
            resolved_type, source_name = source_type, source or source_type.value.lower()
        else:
            resolved_type, source_name = infer_source_type(source_type), source or source_type
        if resolved_type == SourceType.SIMULATION and not self.sandbox:
            raise SimulationEvidenceRejected("SIMULATION evidence is only accepted inside a counterfactual sandbox")
        evidence = Evidence(
            id=generate_id("evi"),
            source_type=resolved_type,
            source=source_name,
            source_reference=source_reference,
            timestamp=timestamp,
            quality=quality,
            authority=authority,
            provenance=provenance or {},
            content=content or {},
        )
        evidence.integrity_reference = evidence_integrity(evidence, subject if subject is not None else evidence.content)
        return self.repo.save_evidence(evidence)

    def verify_evidence(self, evidence_id: str) -> bool:
        """Recompute the integrity hash (security: detect tampered memory records)."""
        evidence = self.repo.get_evidence(evidence_id)
        if evidence is None:
            return False
        subject: Any = evidence.content
        if evidence.content.get("kind") == "observation":
            obs = self.repo.get_observation(evidence.source_reference)
            if obs is None:
                return False
            subject = self._observation_subject(obs, version=evidence.content.get("integrity_v", 1))
        return evidence_integrity(evidence, subject) == evidence.integrity_reference

    @staticmethod
    def _observation_subject(obs: Observation, version: int = 2) -> Dict[str, Any]:
        """What the integrity hash covers. v2 hashes a digest of the raw reference, so
        redacting raw media (spec §17) keeps the record verifiable."""
        if version == 1:
            return obs.model_dump(mode="json", exclude={"resolutions", "redaction"})
        subject = obs.model_dump(mode="json", exclude={"resolutions", "redaction", "raw_reference"})
        if obs.raw_reference is not None:
            subject["raw_digest"] = canonical_hash(obs.raw_reference)
        else:
            subject["raw_digest"] = (obs.redaction or {}).get("raw_digest")
        return subject

    # --------------------------------------------------------------- observation
    def record_observation(self, observation: Observation) -> List[Event]:
        if self.repo.get_observation(observation.id) is not None:
            raise DuplicateObservationError(f"Observation {observation.id} already recorded")

        with self.repo.transaction():
            observation.source_type = observation.source_type or infer_source_type(observation.source)
            evidence = self.record_evidence(
                source_type=observation.source_type,
                source=observation.source,
                source_reference=observation.id,
                timestamp=observation.timestamp,
                quality=observation.quality,
                authority=observation.authority,
                provenance=observation.provenance,
                content={"kind": "observation", "detections": len(observation.observed_entities), "integrity_v": 2},
                subject=self._observation_subject(observation),
            )
            self._touch_session(observation)

            events: List[Event] = []
            matched: Set[str] = set()
            resolved_ids: Dict[str, str] = {}
            resolutions: List[EntityResolution] = []
            for idx, observed in enumerate(observation.observed_entities):
                res = self.registry.resolve(observed, exclude=matched)
                if res.is_match:
                    entity_id, new_events = self._update_entity(res.entity_id, observed, observation, evidence)  # type: ignore[arg-type]
                else:
                    entity_id, new_events = self._create_entity(observed, observation, evidence, res)
                events.extend(new_events)
                matched.add(entity_id)
                if observed.candidate_entity_id:
                    resolved_ids.setdefault(observed.candidate_entity_id, entity_id)
                resolutions.append(
                    EntityResolution(
                        observed_index=idx,
                        candidate_entity_id=observed.candidate_entity_id,
                        entity_id=entity_id,
                        method=res.method,
                        confidence=res.confidence,
                        candidates=res.candidates,
                        reason=res.reason,
                    )
                )

            # Relations last, so targets can refer to detections in the same observation.
            status = grade(observation.source_type, observation.quality, observation.authority)
            for observed, resolution in zip(observation.observed_entities, resolutions):
                for rel in observed.relations:
                    events.extend(
                        self.relations.apply(
                            source=resolution.entity_id,
                            relation_type=rel.relation_type,
                            target=resolved_ids.get(rel.target, rel.target),
                            present=rel.present,
                            at=observation.timestamp,
                            evidence_id=evidence.id,
                            status=status,
                        )
                    )

            observation.resolutions = resolutions
            self.repo.save_observation(observation)
            return events

    def register_entity(self, observed: ObservedEntity, observation: Observation) -> Entity:
        """Explicit registration still goes through evidence (ADR-001): it is recorded as
        an observation from its declared source so the initial state is traceable."""
        if observed.candidate_entity_id and self.repo.get_entity(observed.candidate_entity_id):
            raise ValueError(f"Entity {observed.candidate_entity_id} already exists")
        observation.observed_entities = [observed]
        self.record_observation(observation)
        stored = self.repo.get_observation(observation.id)
        assert stored is not None
        entity = self.repo.get_entity(stored.resolutions[0].entity_id)
        assert entity is not None
        return entity

    @staticmethod
    def _claims_of(observed: ObservedEntity) -> Dict[str, Any]:
        claims: Dict[str, Any] = {}
        if observed.location:
            claims[LOCATION] = observed.location
        claims.update(observed.attributes)
        return claims

    def _touch_session(self, observation: Observation) -> None:
        if not observation.session_id:
            return
        session = self.repo.get_session(observation.session_id) or Session(
            id=observation.session_id, started_at=observation.timestamp
        )
        if session.last_observation_at is None or observation.timestamp > session.last_observation_at:
            session.last_observation_at = observation.timestamp
        self.repo.save_session(session)

    def _create_entity(
        self, observed: ObservedEntity, observation: Observation, evidence: Evidence, resolution: Resolution
    ):
        entity = Entity(
            id=resolution.entity_id or generate_id(f"entity_{observed.type}"),
            type=observed.type,
            name=observed.name or observed.type,
            canonical_attributes=dict(observed.identifiers),
            geometry=observed.geometry,
            anchor=observed.anchor or observed.location,
            status=EpistemicStatus.UNKNOWN,
            observed_at=observation.timestamp,
            evidence_refs=[evidence.id],
            identity_status=resolution.identity_status,
            identity_candidates=list(resolution.candidates),
            created_at=observation.timestamp,
            updated_at=observation.timestamp,
        )
        quality = observation.quality * observed.confidence
        for attr, value in self._claims_of(observed).items():
            self.belief.apply_claim(entity, attr, Claim(value, evidence, quality), emit=False)
        self.belief.materialize(entity, observation.timestamp)
        self.repo.save_entity(entity)

        events = [
            self.repo.save_event(
                Event(
                    timestamp=observation.timestamp,
                    event_type=EventType.OBJECT_ADDED,
                    entity_id=entity.id,
                    before_state=None,
                    after_state=dict(entity.current_state),
                    evidence_refs=[evidence.id],
                    description=f"Entity {entity.id} ({entity.name}) added to world state.",
                )
            )
        ]
        identity_event = {
            ResolutionMethod.NEW_AMBIGUOUS: EventType.IDENTITY_AMBIGUOUS,
            ResolutionMethod.NEW_IDENTITY_CONFLICT: EventType.IDENTITY_CONFLICT,
        }.get(resolution.method)
        if identity_event is not None:
            events.append(
                self.repo.save_event(
                    Event(
                        timestamp=observation.timestamp,
                        event_type=identity_event,
                        entity_id=entity.id,
                        before_state={"candidates": list(resolution.candidates)},
                        after_state={"identity_status": resolution.identity_status.value},
                        evidence_refs=[evidence.id],
                        description=f"Identity of {entity.id} unresolved: {resolution.reason}.",
                    )
                )
            )
        return entity.id, events

    def _update_entity(self, entity_id: str, observed: ObservedEntity, observation: Observation, evidence: Evidence):
        entity = self._require_entity(entity_id)
        quality = observation.quality * observed.confidence
        events: List[Event] = []
        for attr, value in self._claims_of(observed).items():
            result = self.belief.apply_claim(entity, attr, Claim(value, evidence, quality))
            events.extend(result.events)
            if attr == LOCATION and result.decision in LOCATION_DECISIONS:
                entity.anchor = observed.anchor or value
        for key, value in observed.identifiers.items():
            entity.canonical_attributes.setdefault(key, value)  # resolution guarantees no conflict
        if observed.geometry is not None:
            entity.geometry = observed.geometry
        entity.observed_at = max(entity.observed_at, observation.timestamp)
        entity.updated_at = max(entity.updated_at, observation.timestamp)
        # Materialise at the latest known time: a late-arriving old observation must not
        # rewind the cached current view.
        self.belief.materialize(entity, entity.updated_at)
        self.repo.save_entity(entity)
        return entity.id, events

    # ------------------------------------------------- claims from other sources
    def assert_claim(
        self,
        entity_id: str,
        attribute: str,
        value: Any,
        source: str,
        timestamp: datetime,
        source_type: Optional[SourceType] = None,
        quality: float = 1.0,
        authority: float = 1.0,
        source_reference: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> ClaimResult:
        """A claim from a record, a person or an inference (not a perception frame)."""
        with self.repo.transaction():
            entity = self._require_entity(entity_id)
            evidence = self.record_evidence(
                source_type=source_type or infer_source_type(source),
                source=source,
                source_reference=source_reference or f"claim:{entity_id}.{attribute}",
                timestamp=timestamp,
                quality=quality,
                authority=authority,
                provenance=provenance,
                content={"kind": "claim", "entity_id": entity_id, "attribute": attribute, "value": value},
            )
            result = self.belief.apply_claim(entity, attribute, Claim(value, evidence, quality))
            if attribute == LOCATION and result.decision in LOCATION_DECISIONS:
                entity.anchor = value
            entity.updated_at = max(entity.updated_at, timestamp)
            self.belief.materialize(entity, entity.updated_at)
            self.repo.save_entity(entity)
            return result

    def record_intervention(
        self,
        entity_id: str,
        timestamp: datetime,
        description: str,
        attributes: Optional[List[str]] = None,
        source: str = "user",
        source_type: SourceType = SourceType.USER_STATEMENT,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> List[Event]:
        """A known intervention (someone worked on the entity) invalidates the affected
        attributes and everything depending on them (spec §10 invalidation triggers)."""
        with self.repo.transaction():
            self._require_entity(entity_id)
            evidence = self.record_evidence(
                source_type=source_type,
                source=source,
                source_reference=f"intervention:{entity_id}",
                timestamp=timestamp,
                provenance=provenance,
                content={"kind": "intervention", "entity_id": entity_id, "attributes": attributes, "description": description},
            )
            return self.belief.invalidate(
                entity_id, attributes, timestamp, f"intervention: {description}", evidence_refs=[evidence.id]
            )

    def add_dependency(
        self,
        dependent_entity_id: str,
        dependent_attribute: str,
        depends_on_entity_id: str,
        depends_on_attribute: str,
        created_at: datetime,
        reason: Optional[str] = None,
    ) -> ClaimDependency:
        self._require_entity(dependent_entity_id)
        self._require_entity(depends_on_entity_id)
        return self.repo.save_dependency(
            ClaimDependency(
                dependent_entity_id=dependent_entity_id,
                dependent_attribute=dependent_attribute,
                depends_on_entity_id=depends_on_entity_id,
                depends_on_attribute=depends_on_attribute,
                reason=reason,
                created_at=created_at,
            )
        )

    # ------------------------------------------------------------- contradiction
    def record_contradiction(
        self,
        entity_id: str,
        attribute: str,
        conflicting_value: Any,
        evidence: Evidence,
    ) -> Event:
        """Record a contradiction detected outside the policy (e.g. by an auditor)."""
        with self.repo.transaction():
            entity = self._require_entity(entity_id)
            event = self.belief.force_conflict(entity, attribute, Claim(conflicting_value, evidence, evidence.quality))
            entity.updated_at = max(entity.updated_at, evidence.timestamp)
            self.belief.materialize(entity, entity.updated_at)
            self.repo.save_entity(entity)
            return event

    # ----------------------------------------------------------------- freshness
    def evaluate_freshness(self, entity_id: str, as_of: datetime) -> Dict[str, EpistemicStatus]:
        """Attribute statuses at ``as_of`` (rule 2.6). The cached view on the entity is
        refreshed only for evaluations at or after its last update."""
        entity = self._require_entity(entity_id)
        assessment = self.claims.assess_entity(entity_id, as_of)
        assert assessment is not None
        statuses = {a: c.status for a, c in assessment.attributes.items()}
        if as_of >= entity.updated_at:
            entity.attribute_statuses.update(statuses)
            entity.status = aggregate_status(entity.attribute_statuses.values())
            self.repo.save_entity(entity)
        return statuses

    # ---------------------------------------------------------------- world diff
    def compute_world_diff(self, baseline_timestamp: Optional[datetime], target_timestamp: datetime) -> WorldDiff:
        """Snapshot diff Diff(B_baseline, B_target); see ``services/world_diff.py``."""
        from backend.app.services.memory import MemoryService
        from backend.app.services.world_diff import WorldDiffService

        memory = MemoryService(self.repo, self.claims, self.relations, self.anchors)
        service = WorldDiffService(self.repo, memory)
        if baseline_timestamp is None:
            return service.event_log_diff(None, target_timestamp)
        return service.diff(baseline_timestamp, target_timestamp)

    def _require_entity(self, entity_id: str) -> Entity:
        entity = self.repo.get_entity(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        return entity
