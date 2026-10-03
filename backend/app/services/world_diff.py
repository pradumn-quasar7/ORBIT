"""Longitudinal world diff: D = Diff(B_a, B_b) over belief snapshots (spec §12).

Net semantics: the diff compares what ORBIT believed at the baseline with what it
believes at the target, so an object moved A→B→A did not move. Only changes
supported by evidence are reported. Absence is graded (``AbsenceStatus``):
confirmed by validated search coverage, inconclusive search, or simply not
re-observed — never silently "removed". Claims ORBIT can no longer vouch for
(stale, unconfirmed) are listed separately under ``uncertain``; knowledge decay is
not a world change.

``event_log_diff`` is the frame/event-replay baseline kept for ablations (spec §28).
"""
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from backend.app.domain.models import (
    ClaimAssessment,
    DiffUncertainty,
    EntitySnapshot,
    WorldChange,
    WorldDiff,
)
from backend.app.domain.types import AbsenceStatus, EpistemicStatus, EventType, IdentityStatus
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION, change_event_type
from backend.app.services.memory import MemoryService
from backend.app.services.relations import EXCLUSIVE_RELATIONS

if TYPE_CHECKING:  # pragma: no cover
    from backend.app.services.tasks import TaskService


def known_value(c: Optional[ClaimAssessment]) -> Any:
    """What ORBIT holds as the value (supportable or not), None if no current claim."""
    if c is None or not c.has_current_claim:
        return None
    return c.value if c.supportable else c.last_known_value


class WorldDiffService:
    def __init__(
        self,
        repository: Repository,
        memory: MemoryService,
        tasks: Optional["TaskService"] = None,
        treat_unobserved_as_removed: bool = False,
    ):
        self.repo = repository
        self.memory = memory
        self.tasks = tasks
        # True = the "unobserved ⇒ removed" ablation baseline (spec §28).
        self.treat_unobserved_as_removed = treat_unobserved_as_removed

    # ----------------------------------------------------------------- public
    def diff(
        self,
        baseline: datetime,
        target: datetime,
        include_unobserved: bool = True,
        include_tasks: bool = True,
        save: bool = True,
    ) -> WorldDiff:
        a = self.memory.world_snapshot(baseline)
        b = self.memory.world_snapshot(target)
        window_events = [e for e in self.repo.list_events() if baseline < e.timestamp <= target]
        observed = self._observed_in_window(baseline, target)

        changes: List[WorldChange] = []
        for eid in sorted(b.entities):
            eb, ea = b.entities[eid], a.entities.get(eid)
            if ea is None:
                changes.append(self._added(eb, target, window_events))
                continue
            changes += self._attribute_changes(ea, eb, target, window_events)
            changes += self._relation_changes(ea, eb, target)
            if include_unobserved and eid not in observed:
                gone = self._unobserved(ea, eb, baseline, target)
                if gone is not None:
                    changes.append(gone)
        for eid in sorted(set(a.entities) - set(b.entities)):
            entity = self.repo.get_entity(eid)
            if entity is not None and entity.merged_at is not None and baseline < entity.merged_at <= target:
                when, refs = self._change_time(window_events, eid, None, target, {EventType.IDENTITY_MERGED})
                changes.append(WorldChange(
                    change_type=EventType.IDENTITY_MERGED, entity_id=eid, before=eid, after=entity.merged_into,
                    related_entity_ids=[entity.merged_into], note="identity corrected by a person",
                    evidence_refs=refs, timestamp=when,
                ))
        if include_tasks and self.tasks is not None:
            changes += self._task_changes(baseline, target)
        self._annotate_replacements(changes, a, b)

        diff = WorldDiff(
            mode="snapshot",
            baseline_timestamp=baseline,
            target_timestamp=target,
            changes=sorted(changes, key=lambda c: (c.entity_id, c.change_type.value, c.attribute or "")),
            uncertain=self._uncertain(b.entities, window_events),
            created_at=target,
        )
        return self.repo.save_world_diff(diff) if save else diff

    def diff_since_session(self, session_id: str, target: datetime, **kw) -> WorldDiff:
        session = self.repo.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        return self.diff(MemoryService.session_end(session), target, **kw)

    def event_log_diff(self, baseline: Optional[datetime], target: datetime) -> WorldDiff:
        """Ablation baseline: replay every event in (baseline, target] as a change."""
        changes = []
        for evt in self.repo.list_events():
            if (baseline is not None and evt.timestamp <= baseline) or evt.timestamp > target or evt.entity_id is None:
                continue
            attr = next(iter(evt.after_state or evt.before_state or {}), None)
            changes.append(
                WorldChange(
                    change_type=evt.event_type,
                    entity_id=evt.entity_id,
                    attribute=attr if evt.event_type != EventType.OBJECT_ADDED else None,
                    before=(evt.before_state or {}).get(attr) if attr else None,
                    after=(evt.after_state or {}).get(attr) if attr else evt.after_state,
                    evidence_refs=evt.evidence_refs,
                    timestamp=evt.timestamp,
                )
            )
        return WorldDiff(mode="event_log", baseline_timestamp=baseline, target_timestamp=target, changes=changes, created_at=target)

    # --------------------------------------------------------------- helpers
    def _observed_in_window(self, a: datetime, b: datetime) -> Set[str]:
        seen: Set[str] = set()
        for obs in self.repo.list_observations():
            if a < obs.timestamp <= b:
                seen.update(r.entity_id for r in obs.resolutions)
        for e in self.repo.list_entities():
            if any(a < s.at <= b for v in self.repo.get_state_versions_for_entity(e.id) for s in v.support):
                seen.add(e.id)
        return seen

    @staticmethod
    def _change_time(events, entity_id: str, attr: Optional[str], target: datetime, types=None) -> Tuple[datetime, List[str]]:
        hits = [
            e
            for e in events
            if e.entity_id == entity_id
            and (types is None or e.event_type in types)
            and (attr is None or attr in (e.after_state or {}))
        ]
        if not hits:
            return target, []
        last = hits[-1]
        return last.timestamp, list(last.evidence_refs)

    def _added(self, eb: EntitySnapshot, target: datetime, events) -> WorldChange:
        when, refs = self._change_time(events, eb.entity_id, None, target, {EventType.OBJECT_ADDED})
        after = {attr: known_value(c) for attr, c in eb.attributes.items()}
        change = WorldChange(
            change_type=EventType.OBJECT_ADDED,
            entity_id=eb.entity_id,
            after=after,
            status=eb.status,
            evidence_refs=refs,
            timestamp=when,
        )
        entity = self.repo.get_entity(eb.entity_id)
        if entity is not None and entity.identity_status != IdentityStatus.ESTABLISHED:
            change.related_entity_ids = list(entity.identity_candidates)
            change.note = (
                f"INFERRED possible replacement of {', '.join(entity.identity_candidates)} (identifier mismatch)"
                if entity.identity_status == IdentityStatus.POSSIBLE_REPLACEMENT
                else f"identity ambiguous between {', '.join(entity.identity_candidates)}"
            )
        return change

    def _attribute_changes(self, ea: EntitySnapshot, eb: EntitySnapshot, target: datetime, events) -> List[WorldChange]:
        out: List[WorldChange] = []
        for attr in sorted(set(ea.attributes) | set(eb.attributes)):
            ca, cb = ea.attributes.get(attr), eb.attributes.get(attr)
            va, vb = known_value(ca), known_value(cb)
            conflicted_a = ca is not None and ca.status == EpistemicStatus.CONTRADICTED
            conflicted_b = cb is not None and cb.status == EpistemicStatus.CONTRADICTED
            if conflicted_b:
                if not conflicted_a:
                    when, refs = self._change_time(events, eb.entity_id, attr, target, {EventType.EVIDENCE_CONFLICT})
                    out.append(
                        WorldChange(
                            change_type=EventType.EVIDENCE_CONFLICT,
                            entity_id=eb.entity_id,
                            attribute=attr,
                            before=va,
                            after=[side.value for side in cb.conflicts],
                            status=EpistemicStatus.CONTRADICTED,
                            note=cb.reason,
                            evidence_refs=cb.evidence_refs or refs,
                            timestamp=when,
                        )
                    )
                continue
            if attr == LOCATION and va is not None and vb is None and cb is not None and not cb.has_current_claim:
                when, refs = self._change_time(events, eb.entity_id, LOCATION, target, {EventType.OBJECT_REMOVED_OR_UNOBSERVED})
                out.append(
                    WorldChange(
                        change_type=EventType.OBJECT_REMOVED_OR_UNOBSERVED,
                        entity_id=eb.entity_id,
                        attribute=LOCATION,
                        before=va,
                        after=None,
                        status=EpistemicStatus.OBSERVED,
                        absence=AbsenceStatus.CONFIRMED_ABSENT,
                        note=cb.reason,
                        evidence_refs=refs,
                        timestamp=when,
                    )
                )
                continue
            if vb is None or va == vb:
                continue
            if va is None and not (attr == LOCATION and ca is not None and not ca.has_current_claim):
                continue  # first observation of an attribute is knowledge gained, not a change
            etype = change_event_type(attr)
            when, refs = self._change_time(events, eb.entity_id, attr, target)
            out.append(
                WorldChange(
                    change_type=etype,
                    entity_id=eb.entity_id,
                    attribute=attr,
                    before=va,
                    after=vb,
                    status=cb.status if cb else None,
                    note="re-found after confirmed absence" if va is None else None,
                    evidence_refs=(cb.evidence_refs if cb else []) or refs,
                    timestamp=when,
                )
            )
        return out

    def _relation_changes(self, ea: EntitySnapshot, eb: EntitySnapshot, target: datetime) -> List[WorldChange]:
        def index(snapshot: EntitySnapshot) -> Dict[str, Dict[str, Any]]:
            out: Dict[str, Dict[str, Any]] = {}
            for r in snapshot.relations:
                out.setdefault(r.relation_type, {})[r.target_entity] = r
            return out

        ra, rb = index(ea), index(eb)
        out: List[WorldChange] = []
        for rtype in sorted(set(ra) | set(rb)):
            ta, tb = set(ra.get(rtype, {})), set(rb.get(rtype, {}))
            if ta == tb:
                continue
            if rtype in EXCLUSIVE_RELATIONS and len(ta) == 1 and len(tb) == 1:
                rel = rb[rtype][next(iter(tb))]
                pairs = [(next(iter(ta)), next(iter(tb)), rel)]
            else:
                pairs = [(None, t, rb[rtype][t]) for t in sorted(tb - ta)] + [(t, None, ra[rtype][t]) for t in sorted(ta - tb)]
            for before, after, rel in pairs:
                out.append(
                    WorldChange(
                        change_type=EventType.RELATION_CHANGED,
                        entity_id=eb.entity_id,
                        attribute=rtype,
                        before=before,
                        after=after,
                        status=rel.status,
                        related_entity_ids=[x for x in (before, after) if x],
                        evidence_refs=list(rel.evidence_refs[-1:]),
                        timestamp=rel.valid_to if after is None and rel.valid_to else rel.valid_from,
                    )
                )
        return out

    def _unobserved(self, ea: EntitySnapshot, eb: EntitySnapshot, baseline: datetime, target: datetime) -> Optional[WorldChange]:
        before = known_value(ea.attributes.get(LOCATION))
        after = known_value(eb.attributes.get(LOCATION))
        if before is None or after is None:
            return None  # no location to lose, or already reported as confirmed absent
        searched = any(
            ea.entity_id in c.inconclusive and baseline < c.timestamp <= target for c in self.repo.list_search_coverage()
        )
        absence = AbsenceStatus.NOT_FOUND_PARTIAL_COVERAGE if searched else AbsenceStatus.NOT_REOBSERVED
        if self.treat_unobserved_as_removed:
            absence = AbsenceStatus.CONFIRMED_ABSENT
        return WorldChange(
            change_type=EventType.OBJECT_REMOVED_OR_UNOBSERVED,
            entity_id=ea.entity_id,
            attribute=LOCATION,
            before=before,
            after=None if self.treat_unobserved_as_removed else after,
            status=eb.attributes[LOCATION].status,
            absence=absence,
            note=(
                "assumed removed (ablation: unobserved ⇒ removed)"
                if self.treat_unobserved_as_removed
                else f"not re-observed since baseline; last known at {after}"
            ),
            evidence_refs=list(eb.attributes[LOCATION].evidence_refs),
            timestamp=target,
        )

    def _task_changes(self, baseline: datetime, target: datetime) -> List[WorldChange]:
        out: List[WorldChange] = []
        for task in self.repo.list_tasks():
            if task.created_at > target:
                continue
            sb = self.tasks.state_at(task.id, target)
            sa = self.tasks.state_at(task.id, baseline)
            events = [e for e in self.repo.get_events_for_task(task.id) if baseline < e.timestamp <= target]
            if sa is None:
                out.append(
                    WorldChange(
                        change_type=EventType.TASK_PROGRESS_CHANGED,
                        entity_id=task.id,
                        before=None,
                        after=sb.status.value,
                        note="task created",
                        timestamp=task.created_at,
                    )
                )
                continue
            if sa.status != sb.status:
                out.append(
                    WorldChange(
                        change_type=EventType.TASK_PROGRESS_CHANGED,
                        entity_id=task.id,
                        before=sa.status.value,
                        after=sb.status.value,
                        timestamp=events[-1].timestamp if events else target,
                    )
                )
            for step_id, status in sb.steps.items():
                if sa.steps.get(step_id) != status:
                    step_events = [e for e in events if (e.after_state or {}).get("step_id") == step_id]
                    out.append(
                        WorldChange(
                            change_type=EventType.TASK_PROGRESS_CHANGED,
                            entity_id=task.id,
                            attribute=step_id,
                            before=sa.steps.get(step_id).value if sa.steps.get(step_id) else None,
                            after=status.value,
                            evidence_refs=[r for e in step_events for r in e.evidence_refs],
                            timestamp=step_events[-1].timestamp if step_events else target,
                        )
                    )
        return out

    @staticmethod
    def _annotate_replacements(changes: List[WorldChange], a, b) -> None:
        """INFERRED hint: a new object of the same type appeared where a missing one was."""
        missing = [c for c in changes if c.change_type == EventType.OBJECT_REMOVED_OR_UNOBSERVED and c.before is not None]
        for added in (c for c in changes if c.change_type == EventType.OBJECT_ADDED and not c.note):
            loc = added.after.get(LOCATION) if isinstance(added.after, dict) else None
            new_type = b.entities[added.entity_id].type
            for gone in missing:
                old = a.entities.get(gone.entity_id)
                if loc is not None and gone.before == loc and old is not None and old.type == new_type:
                    added.related_entity_ids.append(gone.entity_id)
                    added.note = f"INFERRED possible replacement of {gone.entity_id} (same type and location, original missing)"

    @staticmethod
    def _uncertain(entities: Dict[str, EntitySnapshot], window_events) -> List[DiffUncertainty]:
        out: Dict[Tuple[str, str], DiffUncertainty] = {}
        for eid, snap in entities.items():
            for attr, c in snap.attributes.items():
                if c.has_current_claim and not c.supportable and c.status in (
                    EpistemicStatus.STALE,
                    EpistemicStatus.UNKNOWN,
                    EpistemicStatus.INFERRED,
                ):
                    out[(eid, attr)] = DiffUncertainty(
                        entity_id=eid, attribute=attr, status=c.status, reason=c.reason, last_known_value=c.last_known_value
                    )
        for e in window_events:
            if e.event_type == EventType.UNCONFIRMED_CHANGE and e.entity_id:
                attr = next(iter(e.after_state or {}), "")
                out.setdefault(
                    (e.entity_id, attr),
                    DiffUncertainty(
                        entity_id=e.entity_id,
                        attribute=attr,
                        status=EpistemicStatus.UNKNOWN,
                        reason=f"unconfirmed report: {e.description}",
                        last_known_value=(e.before_state or {}).get(attr),
                    ),
                )
        return sorted(out.values(), key=lambda u: (u.entity_id, u.attribute))
