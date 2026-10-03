"""Identity curation (Phase 13): human-confirmed merge, undo and "distinct" judgments.

ORBIT never merges identities on a guess (ADR-009). When a person with the
``curate`` scope confirms that record S *is* entity T, ORBIT re-derives T's belief by
replaying every piece of evidence about S and T, in time order, through the normal
evidence policy. Disagreements between the two histories therefore surface as
conflicts instead of one side silently winning, and invalidations and confirmed
absences are carried over. The original versions are *retired* (not deleted) and
the rebuilt ones are *recorded* at the merge instant, so any "as known at t" answer
for t before the merge is unchanged (bitemporal, ADR-035). Undo replays both
original partitions back into two entities; evidence that arrived after the merge
stays with T.

A merge is refused when the two records were seen in the same observation (two
physical objects at once), when their strong identifiers conflict, or when their
types differ.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.domain.models import (
    Entity,
    Event,
    IdentityMerge,
    MergeSuggestion,
    Relation,
    StateVersion,
)
from backend.app.domain.types import EventType, IdentityStatus, PrincipalKind, Scope, SourceType
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.claims import known_at
from backend.app.services.entity_registry import identity_conflict_between, signature_conflict_between
from backend.app.services.evidence_policy import Claim
from backend.app.services.world_state_engine import AMBIGUITY_TAG, EntityNotFoundError, WorldStateEngine

ABSENCE = re.compile(r"^confirmed absent from (?P<region>.+) by search (?P<coverage>\S+)$")
SUGGESTION_THRESHOLD = 0.5


class IdentityError(ValueError):
    pass


class CurationDenied(IdentityError):
    pass


@dataclass(order=True)
class _Item:
    at: datetime
    order: int
    attribute: str
    kind: str = field(compare=False)  # claim | invalidate | absence
    claim: Optional[Claim] = field(default=None, compare=False)
    reason: str = field(default="", compare=False)
    region: str = field(default="", compare=False)
    coverage: str = field(default="", compare=False)
    evidence_id: str = field(default="", compare=False)


class IdentityService:
    def __init__(self, repository: Repository, engine: WorldStateEngine):
        self.repo = repository
        self.engine = engine

    # ------------------------------------------------------------- helpers
    def resolve_alias(self, entity_id: str) -> str:
        seen: Set[str] = set()
        entity = self.repo.get_entity(entity_id)
        while entity is not None and entity.merged_into and entity.id not in seen:
            seen.add(entity.id)
            entity = self.repo.get_entity(entity.merged_into)
        return entity.id if entity is not None else entity_id

    def _curator(self, principal_id: str, entity_ids: List[str]) -> None:
        p = self.repo.get_principal(principal_id)
        if p is None:
            raise CurationDenied(f"unknown principal {principal_id}")
        if p.kind != PrincipalKind.HUMAN or Scope.CURATE not in p.scopes:
            raise CurationDenied(f"{principal_id} may not correct identities (needs a human with the 'curate' scope)")
        if p.entity_scope is not None and not set(entity_ids) <= set(p.entity_scope):
            raise CurationDenied(f"{principal_id} is not permitted for {sorted(set(entity_ids) - set(p.entity_scope))}")

    def _require(self, entity_id: str) -> Entity:
        e = self.repo.get_entity(entity_id)
        if e is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        if e.merged_into:
            raise IdentityError(f"{entity_id} is already merged into {e.merged_into}")
        return e

    def co_observed(self, a: str, b: str) -> bool:
        """Seen in one observation ⇒ two physical objects (hard evidence against identity)."""
        return any({a, b} <= {r.entity_id for r in o.resolutions} for o in self.repo.list_observations())

    def _known_versions(self, entity_id: str, at: datetime) -> List[StateVersion]:
        return [v for v in self.repo.get_state_versions_for_entity(entity_id) if known_at(v, at) and v.valid_from <= at]

    def _timeline(
        self, entity_id_hint: str, versions: List[StateVersion], since: Optional[datetime] = None, lift: Optional[str] = None
    ) -> List[_Item]:
        """Evidence timeline of versions. ``lift`` drops invalidations caused by that
        ambiguous record (the doubt it cast is resolved)."""
        items: Dict[Tuple, _Item] = {}
        for v in versions:
            for s in v.support:
                if since is not None and s.at < since:
                    continue
                ev = self.repo.get_evidence(s.evidence_id)
                if ev is None:
                    continue
                quality = s.strength / ev.authority if ev.authority else 0.0
                items.setdefault(("claim", s.evidence_id, v.attribute), _Item(
                    s.at, 0, v.attribute, "claim", claim=Claim(v.value, ev, quality)))
            if v.invalidated_at is not None and (since is None or v.invalidated_at >= since):
                m = ABSENCE.match(v.invalidation_reason or "")
                if m and v.attribute == LOCATION:
                    removed = [
                        e for e in self.repo.get_events_for_entity(v.entity_id)
                        if e.event_type == EventType.OBJECT_REMOVED_OR_UNOBSERVED and e.timestamp == v.invalidated_at
                    ]
                    items.setdefault(("absence", v.attribute, v.invalidated_at), _Item(
                        v.invalidated_at, 1, v.attribute, "absence", region=m.group("region"),
                        coverage=m.group("coverage"), evidence_id=removed[0].evidence_refs[0] if removed and removed[0].evidence_refs else ""))
                elif lift and f"({AMBIGUITY_TAG}{lift})" in (v.invalidation_reason or ""):
                    pass
                else:
                    items.setdefault(("invalidate", v.attribute, v.invalidated_at), _Item(
                        v.invalidated_at, 1, v.attribute, "invalidate", reason=v.invalidation_reason or "invalidated"))
        return sorted(items.values())

    def _replay(self, entity: Entity, items: List[_Item], recorded_at: datetime) -> None:
        belief = self.engine.belief
        saved = (belief.silent, belief.recorded_at)
        belief.silent, belief.recorded_at = True, recorded_at
        try:
            for it in items:
                if it.kind == "claim":
                    belief.apply_claim(entity, it.attribute, it.claim)
                elif it.kind == "invalidate":
                    belief.invalidate(entity.id, [it.attribute], it.at, it.reason, inflight=entity)
                elif it.kind == "absence":
                    belief.apply_absence(entity, it.region, it.at, it.evidence_id, it.coverage, within=self.engine.anchors.is_within)
        finally:
            belief.silent, belief.recorded_at = saved

    def _retire(self, entity_id: str, versions: List[StateVersion], at: datetime, why: str) -> None:
        for v in versions:
            v.retired_at = at
            self.repo.save_state_version(v)
        for c in self.repo.list_conflicts(entity_id=entity_id):
            if c.resolved_at is None:
                c.resolved_at = at
                c.resolution_reason = why
                self.repo.save_conflict(c)

    def _rebuild(self, entity: Entity, items: List[_Item], at: datetime) -> Entity:
        entity.current_state, entity.attribute_statuses = {}, {}
        self._replay(entity, items, at)
        entity.updated_at = max(entity.updated_at, at)
        self.engine.belief.materialize(entity, entity.updated_at)
        entity.anchor = entity.current_state.get(LOCATION) or entity.anchor
        self.repo.save_entity(entity)
        return entity

    def rebuild(self, entity_id: str, at: datetime, lift: Optional[str] = None) -> Entity:
        """Re-derive one entity's belief from its own evidence; ``lift`` removes the doubt
        cast by a now-resolved ambiguous record."""
        with self.repo.transaction():
            entity = self._require(entity_id)
            versions = self._known_versions(entity_id, at)
            items = self._timeline(entity_id, versions, lift=lift)
            self._retire(entity_id, versions, at, "superseded by rebuild")
            return self._rebuild(entity, items, at)

    def _move_relations(self, old: str, new: str, at: datetime, evidence_id: str, only: Optional[Set[str]] = None) -> List[str]:
        moved = []
        for r in self.engine.relations.active_relations(old):
            if only is not None and r.id not in only:
                continue
            r.valid_to = at
            self.repo.save_relation(r)
            copy = Relation(
                source_entity=new if r.source_entity == old else r.source_entity,
                relation_type=r.relation_type,
                target_entity=new if r.target_entity == old else r.target_entity,
                valid_from=at,
                status=r.status,
                evidence_refs=[evidence_id],
            )
            self.repo.save_relation(copy)
            moved.append(copy.id)
        return moved

    def _event(self, entity_id: str, etype: EventType, at: datetime, before, after, evidence_id: str, text: str) -> Event:
        return self.repo.save_event(Event(timestamp=at, event_type=etype, entity_id=entity_id, before_state=before,
                                          after_state=after, evidence_refs=[evidence_id], description=text))

    def _evidence(self, principal_id: str, at: datetime, content: Dict[str, Any], ref: str):
        return self.engine.record_evidence(
            SourceType.USER_STATEMENT, ref, at, source=principal_id, content=content,
            provenance={"principal": principal_id, "kind": "identity_curation"},
        )

    # --------------------------------------------------------------- merge
    def check_mergeable(self, source: Entity, target: Entity) -> None:
        if source.id == target.id:
            raise IdentityError("cannot merge an entity into itself")
        if source.type != target.type:
            raise IdentityError(f"types differ: {source.type} vs {target.type}")
        conflict = identity_conflict_between(source, target)
        if conflict:
            raise IdentityError(f"strong identifiers conflict: {conflict}")
        if self.co_observed(source.id, target.id):
            raise IdentityError(f"{source.id} and {target.id} were seen in the same observation: two different objects")
        if target.id in source.distinct_from:
            raise IdentityError(f"{source.id} and {target.id} were confirmed distinct by a person")

    def merge(self, source_id: str, target_id: str, principal_id: str, at: datetime, reason: str) -> IdentityMerge:
        if not (reason or "").strip():
            raise IdentityError("a merge needs a reason")
        self._curator(principal_id, [source_id, target_id])
        source, target = self._require(source_id), self._require(target_id)
        self.check_mergeable(source, target)
        with self.repo.transaction():
            ev = self._evidence(principal_id, at, {"kind": "identity_merge", "source": source_id, "target": target_id,
                                                   "reason": reason}, f"identity:merge:{source_id}->{target_id}")
            s_versions, t_versions = self._known_versions(source_id, at), self._known_versions(target_id, at)
            # The ambiguous sighting *was* the target, so its own doubt is replaced by the evidence.
            items = self._timeline(target_id, s_versions + t_versions, lift=source_id)
            record = IdentityMerge(
                source_id=source_id, target_id=target_id, merged_at=at, principal_id=principal_id, reason=reason,
                evidence_id=ev.id, source_version_ids=[v.id for v in s_versions], target_version_ids=[v.id for v in t_versions],
                source_identity_status=source.identity_status, source_identity_candidates=list(source.identity_candidates),
            )
            why = f"superseded by identity merge {record.id}"
            self._retire(source_id, s_versions, at, why)
            self._retire(target_id, t_versions, at, why)

            for k, v in source.canonical_attributes.items():
                target.canonical_attributes.setdefault(k, v)
            target.identity_status = IdentityStatus.ESTABLISHED
            target.identity_candidates = [c for c in target.identity_candidates if c != source_id]
            target.evidence_refs = list(dict.fromkeys(target.evidence_refs + source.evidence_refs + [ev.id]))
            target.created_at = min(target.created_at, source.created_at)
            target.observed_at = max(target.observed_at, source.observed_at)
            target.distinct_from = sorted(set(target.distinct_from) | set(source.distinct_from))
            self._rebuild(target, items, at)

            source.merged_into, source.merged_at = target_id, at
            source.updated_at = max(source.updated_at, at)
            self.repo.save_entity(source)
            record.moved_relation_ids = self._move_relations(source_id, target_id, at, ev.id)
            for other in record.source_identity_candidates:  # the others were never involved
                if other != target_id and self.repo.get_entity(other) and not self.repo.get_entity(other).merged_into:
                    self.rebuild(other, at, lift=source_id)
            self._event(source_id, EventType.IDENTITY_MERGED, at, {"entity": source_id}, {"merged_into": target_id}, ev.id,
                        f"{source_id} confirmed to be {target_id} by {principal_id}: {reason}")
            self._event(target_id, EventType.IDENTITY_MERGED, at, {"absorbed": source_id}, {"entity": target_id}, ev.id,
                        f"{target_id} absorbed the history of {source_id}")
            return self.repo.save_merge(record)

    def undo(self, merge_id: str, principal_id: str, at: datetime, reason: str) -> IdentityMerge:
        record = self.repo.get_merge(merge_id)
        if record is None:
            raise IdentityError(f"merge {merge_id} not found")
        if record.status != "ACTIVE":
            raise IdentityError(f"merge {merge_id} is already {record.status.lower()}")
        if not (reason or "").strip():
            raise IdentityError("undoing a merge needs a reason")
        self._curator(principal_id, [record.source_id, record.target_id])
        source, target = self.repo.get_entity(record.source_id), self._require(record.target_id)
        with self.repo.transaction():
            ev = self._evidence(principal_id, at, {"kind": "identity_unmerge", "merge": merge_id, "reason": reason},
                                f"identity:unmerge:{merge_id}")
            by_id = {v.id: v for e in (record.source_id, record.target_id) for v in self.repo.get_state_versions_for_entity(e)}
            current = self._known_versions(record.target_id, at)
            after_merge = self._timeline(record.target_id, current, since=record.merged_at)
            self._retire(record.target_id, current, at, f"superseded by undo of identity merge {merge_id}")

            src_items = self._timeline(record.source_id, [by_id[i] for i in record.source_version_ids])
            tgt_items = sorted(self._timeline(record.target_id, [by_id[i] for i in record.target_version_ids]) + after_merge)

            source.merged_into = source.merged_at = None
            source.identity_status = record.source_identity_status
            source.identity_candidates = list(record.source_identity_candidates)
            source.evidence_refs = list(dict.fromkeys(source.evidence_refs + [ev.id]))
            self._rebuild(source, src_items, at)
            # Identifiers learned only from the source go back with it.
            for k, v in list(target.canonical_attributes.items()):
                if source.canonical_attributes.get(k) == v and not self._identifier_seen(record.target_id, k, v, record.merged_at):
                    target.canonical_attributes.pop(k)
            target.evidence_refs = list(dict.fromkeys(target.evidence_refs + [ev.id]))
            self._rebuild(target, tgt_items, at)
            self._move_relations(record.target_id, record.source_id, at, ev.id, only=set(record.moved_relation_ids))
            for other in record.source_identity_candidates:  # ambiguity is back: so is the doubt
                if other != record.target_id and self.repo.get_entity(other) is not None:
                    self.engine.belief.invalidate(
                        other, [LOCATION], at, f"identity of {record.source_id} unresolved again ({AMBIGUITY_TAG}{record.source_id})",
                        evidence_refs=[ev.id])

            for eid in (record.source_id, record.target_id):
                self._event(eid, EventType.IDENTITY_UNMERGED, at, {"merge": merge_id}, {"entity": eid}, ev.id,
                            f"identity merge {merge_id} undone by {principal_id}: {reason}")
            record.status, record.undone_at, record.undone_by, record.undo_reason = "UNDONE", at, principal_id, reason
            return self.repo.save_merge(record)

    def _identifier_seen(self, entity_id: str, key: str, value: str, before: datetime) -> bool:
        for o in self.repo.list_observations():
            if o.timestamp > before:
                continue
            for r in o.resolutions:
                if r.entity_id == entity_id and o.observed_entities[r.observed_index].identifiers.get(key) == value:
                    return True
        return False

    # ------------------------------------------------------------ distinct
    def confirm_distinct(self, a_id: str, b_id: str, principal_id: str, at: datetime, reason: str) -> Tuple[Entity, Entity]:
        if not (reason or "").strip():
            raise IdentityError("confirming distinct objects needs a reason")
        self._curator(principal_id, [a_id, b_id])
        a, b = self._require(a_id), self._require(b_id)
        if a.id == b.id:
            raise IdentityError("an entity is not distinct from itself")
        with self.repo.transaction():
            ev = self._evidence(principal_id, at, {"kind": "identity_distinct", "entities": [a_id, b_id], "reason": reason},
                                f"identity:distinct:{a_id}|{b_id}")
            # Decide before mutating: if one is an ambiguous record listing the other as a
            # candidate, the doubt it cast on that candidate is lifted.
            lifted = [(me.id, other.id) for me, other in ((a, b), (b, a)) if me.id in other.identity_candidates]
            for me, other in ((a, b), (b, a)):
                me.distinct_from = sorted(set(me.distinct_from) | {other.id})
                if other.id in me.identity_candidates:
                    me.identity_candidates = [c for c in me.identity_candidates if c != other.id]
                    if not me.identity_candidates and me.identity_status == IdentityStatus.AMBIGUOUS:
                        me.identity_status = IdentityStatus.ESTABLISHED
                me.evidence_refs = list(dict.fromkeys(me.evidence_refs + [ev.id]))
                self.repo.save_entity(me)
                self._event(me.id, EventType.IDENTITY_DISTINCT, at, {"maybe": other.id}, {"distinct_from": other.id}, ev.id,
                            f"{me.id} confirmed distinct from {other.id} by {principal_id}: {reason}")
            for entity_id, ambiguous_id in lifted:
                self.rebuild(entity_id, at, lift=ambiguous_id)
            return self.repo.get_entity(a_id), self.repo.get_entity(b_id)

    # --------------------------------------------------------- suggestions
    def suggestions(self, at: datetime) -> List[MergeSuggestion]:
        """Candidate duplicates for a person to review — never applied automatically."""
        live = [e for e in self.repo.list_entities() if not e.merged_into and e.created_at <= at]
        seen_in: Dict[str, Set[str]] = {}
        first_last: Dict[str, Tuple[datetime, datetime]] = {}
        for o in self.repo.list_observations():
            if o.timestamp > at:
                continue
            for r in o.resolutions:
                seen_in.setdefault(r.entity_id, set()).add(o.id)
                lo, hi = first_last.get(r.entity_id, (o.timestamp, o.timestamp))
                first_last[r.entity_id] = (min(lo, o.timestamp), max(hi, o.timestamp))
        out: List[MergeSuggestion] = []
        for s in live:
            for t in live:
                if s.id == t.id or s.type != t.type or t.id in s.distinct_from:
                    continue
                if seen_in.get(s.id, set()) & seen_in.get(t.id, set()):
                    continue  # co-observed: two objects
                if identity_conflict_between(s, t) or signature_conflict_between(s, t):
                    continue
                score, reasons = 0.0, []
                if s.identity_status == IdentityStatus.AMBIGUOUS and t.id in s.identity_candidates:
                    score += 0.5
                    reasons.append(f"ORBIT could not decide whether {s.id} is {t.id}")
                shared = [k for k, v in s.canonical_attributes.items() if t.canonical_attributes.get(k) == v]
                if shared:
                    score += 0.5
                    reasons.append(f"same {', '.join(shared)}")
                if s.id in first_last and t.id in first_last and first_last[t.id][1] < first_last[s.id][0]:
                    score += 0.2
                    reasons.append(f"{t.id} was last seen before {s.id} first appeared")
                if score >= SUGGESTION_THRESHOLD and (s.created_at, s.id) > (t.created_at, t.id):
                    out.append(MergeSuggestion(source_id=s.id, target_id=t.id, score=round(score, 2), reasons=reasons))
        return sorted(out, key=lambda m: (-m.score, m.source_id, m.target_id))
