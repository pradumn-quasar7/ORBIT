"""Memory core (spec §7): temporal, spatial and episodic recall over structured state.

Everything here is a *read* over the append-oriented store and goes through the
evidence gate, so a remembered state is always returned with the epistemic status,
freshness and conflicts it had at the requested instant.
"""
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from backend.app.domain.models import (
    AnchorContent,
    Entity,
    EntitySnapshot,
    Event,
    LocationAnswer,
    Session,
    SessionSummary,
    WorldSnapshot,
)
from backend.app.domain.status import aggregate_status
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.claims import ClaimEvaluator
from backend.app.services.relations import RelationService
from backend.app.services.spatial import AnchorRegistry

CONTAINMENT_RELATIONS = ("on", "inside")


class MemoryService:
    def __init__(
        self,
        repository: Repository,
        claims: ClaimEvaluator,
        relations: RelationService,
        anchors: AnchorRegistry,
    ):
        self.repo = repository
        self.claims = claims
        self.relations = relations
        self.anchors = anchors

    # ------------------------------------------------------------- temporal
    def entity_snapshot(self, entity: Entity, as_of: datetime) -> EntitySnapshot:
        attrs = sorted({v.attribute for v in self.repo.get_state_versions_for_entity(entity.id)})
        assessed = {a: self.claims.assess_attribute(entity.id, a, as_of) for a in attrs}
        assessed = {a: c for a, c in assessed.items() if c.last_known_value is not None or c.conflicts}
        return EntitySnapshot(
            entity_id=entity.id,
            type=entity.type,
            name=entity.name,
            identity_status=entity.identity_status,
            status=aggregate_status(c.status for c in assessed.values()),
            attributes=assessed,
            relations=[r for r in self.relations.active_relations(entity.id, as_of) if r.source_entity == entity.id],
        )

    def world_snapshot(self, as_of: datetime, entity_ids: Optional[List[str]] = None) -> WorldSnapshot:
        """B_t — the structured belief state at ``as_of``."""
        entities = [e for e in self.repo.list_entities() if e.created_at <= as_of]
        if entity_ids is not None:
            entities = [e for e in entities if e.id in set(entity_ids)]
        return WorldSnapshot(as_of=as_of, entities={e.id: self.entity_snapshot(e, as_of) for e in entities})

    def timeline(
        self, entity_id: str, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> List[Event]:
        return [
            e
            for e in self.repo.get_events_for_entity(entity_id)
            if (start is None or e.timestamp >= start) and (end is None or e.timestamp <= end)
        ]

    # -------------------------------------------------------------- spatial
    def locate(self, entity_id: str, as_of: datetime) -> LocationAnswer:
        assessment = self.claims.assess_attribute(entity_id, LOCATION, as_of)
        where = assessment.value if assessment.supportable else assessment.last_known_value
        return LocationAnswer(
            entity_id=entity_id,
            as_of=as_of,
            location=assessment,
            anchor_lineage=self.anchors.lineage(where) if where else [],
            supported_by_relations=[
                r
                for r in self.relations.active_relations(entity_id, as_of)
                if r.source_entity == entity_id and r.relation_type in CONTAINMENT_RELATIONS
            ],
        )

    def contents(self, anchor_id: str, as_of: datetime, nested: bool = True) -> List[AnchorContent]:
        """What is (believed to be) at an anchor. Stale positions are included but carry
        their status; callers must not present them as current (spec §2.7)."""
        found: Dict[str, AnchorContent] = {}
        for entity in self.repo.list_entities():
            if entity.created_at > as_of:
                continue
            a = self.claims.assess_attribute(entity.id, LOCATION, as_of)
            where = a.value if a.supportable else a.last_known_value
            inside = self.anchors.is_within(where, anchor_id) if nested else where == anchor_id
            if where is not None and inside:
                found[entity.id] = AnchorContent(entity_id=entity.id, location=where, via="location", assessment=a)
        for rel in self.relations.active_relations(as_of=as_of):
            if rel.relation_type in CONTAINMENT_RELATIONS and (rel.target_entity in found or rel.target_entity == anchor_id):
                if rel.source_entity not in found and self.repo.get_entity(rel.source_entity) is not None:
                    a = self.claims.assess_attribute(rel.source_entity, LOCATION, as_of)
                    found[rel.source_entity] = AnchorContent(
                        entity_id=rel.source_entity,
                        location=rel.target_entity,
                        via=f"relation:{rel.relation_type}",
                        assessment=a,
                    )
        return sorted(found.values(), key=lambda c: c.entity_id)

    # ------------------------------------------------------------- episodic
    def session_summary(self, session_id: str) -> Optional[SessionSummary]:
        session = self.repo.get_session(session_id)
        if session is None:
            return None
        observations = self.repo.list_observations_for_session(session_id)
        obs_ids = [o.id for o in observations]
        evidence_ids = {e.id for e in self.repo.list_evidence() if e.source_reference in set(obs_ids)}
        events = [e for e in self.repo.list_events() if set(e.evidence_refs) & evidence_ids]
        entities = sorted({r.entity_id for o in observations for r in o.resolutions})
        return SessionSummary(
            session=session,
            observation_ids=obs_ids,
            entities_observed=entities,
            event_counts=dict(Counter(e.event_type.value for e in events)),
            events=events,
        )

    def previous_session(self, before: datetime) -> Optional[Session]:
        """The most recent session that started before ``before`` (for "since last time")."""
        earlier = [s for s in self.repo.list_sessions() if s.started_at < before]
        return earlier[-1] if earlier else None

    @staticmethod
    def session_end(session: Session) -> Optional[datetime]:
        return session.ended_at or session.last_observation_at or session.started_at
