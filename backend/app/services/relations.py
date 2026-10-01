"""Relation maintenance with validity intervals (spec §21 Relation).

* Exclusive relation types (``on``, ``inside``, ``held_by``): a source has at most one
  active target, so a new target closes the old interval → ``RELATION_CHANGED``.
* Non-exclusive types (``connected_to``, ``adjacent_to``, …) accumulate.
* Symmetric types match regardless of direction.
* Unknown ≠ absent: not seeing a relation never ends it. Only an explicit
  ``present=False`` observation closes a relation.
"""
from datetime import datetime
from typing import List, Optional

from backend.app.domain.models import Event, Relation
from backend.app.domain.types import EpistemicStatus, EventType
from backend.app.repositories.base import Repository

EXCLUSIVE_RELATIONS = frozenset({"on", "inside", "held_by"})
SYMMETRIC_RELATIONS = frozenset({"connected_to", "adjacent_to"})


class RelationService:
    def __init__(self, repository: Repository):
        self.repo = repository

    def active_relations(self, entity_id: Optional[str] = None, as_of: Optional[datetime] = None) -> List[Relation]:
        rels = self.repo.get_relations_for_entity(entity_id) if entity_id else self.repo.list_relations()
        return [r for r in rels if _active_at(r, as_of)]

    def apply(
        self,
        source: str,
        relation_type: str,
        target: str,
        present: bool,
        at: datetime,
        evidence_id: str,
        status: EpistemicStatus,
    ) -> List[Event]:
        active = [r for r in self.active_relations(source) if r.relation_type == relation_type]
        same = [r for r in active if _matches(r, source, relation_type, target)]

        if not present:
            events = []
            for rel in same:
                rel.valid_to = at
                rel.evidence_refs.append(evidence_id)
                self.repo.save_relation(rel)
                events.append(self._event(rel, at, evidence_id, before=target, after=None))
            return events

        if same:
            rel = same[0]
            if evidence_id not in rel.evidence_refs:
                rel.evidence_refs.append(evidence_id)
                self.repo.save_relation(rel)
            return []

        before: Optional[str] = None
        if relation_type in EXCLUSIVE_RELATIONS:
            for old in (r for r in active if r.source_entity == source):
                old.valid_to = at
                self.repo.save_relation(old)
                before = old.target_entity
        rel = self.repo.save_relation(
            Relation(
                source_entity=source,
                relation_type=relation_type,
                target_entity=target,
                valid_from=at,
                status=status,
                evidence_refs=[evidence_id],
            )
        )
        return [self._event(rel, at, evidence_id, before=before, after=target)]

    def _event(self, rel: Relation, at: datetime, evidence_id: str, before, after) -> Event:
        verb = "now" if after is not None else "no longer"
        return self.repo.save_event(
            Event(
                timestamp=at,
                event_type=EventType.RELATION_CHANGED,
                entity_id=rel.source_entity,
                relation_id=rel.id,
                before_state={rel.relation_type: before},
                after_state={rel.relation_type: after},
                evidence_refs=[evidence_id],
                description=f"{rel.source_entity} {verb} {rel.relation_type} {after or before}.",
            )
        )


def _active_at(rel: Relation, as_of: Optional[datetime]) -> bool:
    if as_of is None:
        return rel.valid_to is None
    return rel.valid_from <= as_of and (rel.valid_to is None or rel.valid_to > as_of)


def _matches(rel: Relation, source: str, relation_type: str, target: str) -> bool:
    if rel.relation_type != relation_type:
        return False
    if rel.source_entity == source and rel.target_entity == target:
        return True
    return relation_type in SYMMETRIC_RELATIONS and rel.source_entity == target and rel.target_entity == source
