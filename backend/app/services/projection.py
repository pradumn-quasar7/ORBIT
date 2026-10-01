"""World projection: rebuild B_T — the world exactly as ORBIT knew it at time T — as a
standalone repository (spec §33 "replay stored world states; clone a workspace").

Everything learned after T is removed or rolled back: observations and evidence
after T, version closures and invalidations after T, conflicts resolved after T
(re-opened), status upgrades from later corroboration (re-graded from the supports
known at T), identifiers first read after T, step completions and acknowledgements
after T, action transitions after T. Record ids are preserved, so a projection can be
compared one-to-one with the source. The source repository is only read.

The projection is the substrate for counterfactual sandboxes (``counterfactual.py``)
and is checked by a fidelity test: the projection's view at T equals the source's
view at T.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

from backend.app.domain.models import ActionRequest, Task
from backend.app.domain.types import (
    ActionStatus,
    ClaimDisposition,
    EpistemicStatus,
    EventType,
    HypothesisStatus,
    StepStatus,
)
from backend.app.repositories.base import Repository
from backend.app.services.belief import status_from_supports
from backend.app.services.evidence_policy import is_verification
from backend.app.services.freshness import WEAK_SUPPORT
from backend.app.services.hypotheses import qualifies_as_causal_test
from backend.app.services.tasks import replay_task_state


@dataclass
class ProjectionStats:
    as_of: datetime
    counts: Dict[str, int] = field(default_factory=dict)


class WorldProjector:
    def __init__(self, source: Repository):
        self.src = source

    def project(self, as_of: datetime, target: Repository) -> ProjectionStats:
        T = as_of
        src, dst = self.src, target
        stats = ProjectionStats(as_of=T)

        def count(kind: str) -> None:
            stats.counts[kind] = stats.counts.get(kind, 0) + 1

        evidence = {e.id: e for e in src.list_evidence() if e.timestamp <= T}

        def authority(eid: str) -> float:
            ev = evidence.get(eid) or src.get_evidence(eid)
            return ev.authority if ev else 1.0

        with dst.transaction():
            for ev in evidence.values():
                dst.save_evidence(ev)
                count("evidence")
            for a in src.list_anchors():
                if a.created_at <= T:
                    dst.save_anchor(a)
                    count("anchors")
            observations = [o for o in src.list_observations() if o.timestamp <= T]
            for o in observations:
                dst.save_observation(o)
                count("observations")
            for s in src.list_sessions():
                if s.started_at > T:
                    continue
                own = [o.timestamp for o in observations if o.session_id == s.id]
                s.last_observation_at = max(own) if own else None
                if s.ended_at and s.ended_at > T:
                    s.ended_at = None
                dst.save_session(s)
                count("sessions")

            # Conflicts open at T (re-opened if they were resolved later).
            in_open_conflict: Set[str] = set()
            for c in src.list_conflicts():
                if c.opened_at > T:
                    continue
                if c.resolved_at and c.resolved_at > T:
                    c.resolved_at = None
                    c.resolution_version_id = c.resolution_evidence = c.resolution_reason = None
                if c.resolved_at is None:
                    in_open_conflict.update(c.version_ids)
                dst.save_conflict(c)
                count("conflicts")

            versions_kept: Set[str] = set()
            for entity in src.list_entities():
                if entity.created_at > T:
                    continue
                for v in src.get_state_versions_for_entity(entity.id):
                    if v.valid_from > T:
                        continue
                    v.support = [s for s in v.support if s.at <= T]
                    v.supported_by = [s.evidence_id for s in v.support]
                    v.last_supported_at = max([v.valid_from] + [s.at for s in v.support if s.strength >= WEAK_SUPPORT])
                    verifications = [
                        s.at for s in v.support
                        if is_verification(s.source_type, s.strength / max(authority(s.evidence_id), 1e-9), authority(s.evidence_id))
                    ]
                    v.last_validated_at = max(verifications) if verifications else None
                    v.status = status_from_supports(v.support, authority)
                    if v.valid_to is not None and v.valid_to > T:
                        v.valid_to = None
                    if v.invalidated_at is not None and v.invalidated_at > T:
                        v.invalidated_at = None
                        v.invalidation_reason = None
                    if v.disposition != ClaimDisposition.UNCONFIRMED:
                        if v.id in in_open_conflict:
                            v.disposition = ClaimDisposition.CONFLICTING
                        elif v.valid_to is None:
                            v.disposition = ClaimDisposition.ACCEPTED
                    dst.save_state_version(v)
                    versions_kept.add(v.id)
                    count("state_versions")

                # Entity shell; the materialised view is rebuilt by the caller at T.
                seen = [
                    (o, r) for o in observations for r in o.resolutions if r.entity_id == entity.id
                ]
                identifiers: Dict[str, str] = {}
                geometry = None
                for o, r in seen:
                    detection = o.observed_entities[r.observed_index]
                    for k, val in detection.identifiers.items():
                        identifiers.setdefault(k, val)
                    if detection.geometry is not None:
                        geometry = detection.geometry
                entity.canonical_attributes = identifiers
                entity.geometry = geometry
                entity.evidence_refs = [e for e in entity.evidence_refs if e in evidence]
                entity.history_refs = [h for h in entity.history_refs if h in versions_kept]
                entity.observed_at = max([o.timestamp for o, _ in seen], default=entity.created_at)
                entity.updated_at = T
                entity.current_state = {}
                entity.attribute_statuses = {}
                dst.save_entity(entity)
                count("entities")

            for r in src.list_relations():
                if r.valid_from > T:
                    continue
                if r.valid_to is not None and r.valid_to > T:
                    r.valid_to = None
                r.evidence_refs = [e for e in r.evidence_refs if e in evidence] or r.evidence_refs[:1]
                dst.save_relation(r)
                count("relations")

            events = [e for e in src.list_events() if e.timestamp <= T]
            for e in events:
                dst.save_event(e)
                count("events")
            for dep in src.list_dependencies():
                if dep.created_at <= T:
                    dst.save_dependency(dep)
                    count("dependencies")
            for cov in src.list_search_coverage():
                if cov.timestamp <= T:
                    dst.save_search_coverage(cov)
                    count("search_coverage")

            for h in src.list_hypotheses():
                if h.created_at > T:
                    continue
                h.evidence_refs = [e for e in h.evidence_refs if e in evidence]
                h.status, h.epistemic_status = HypothesisStatus.HYPOTHESIS, EpistemicStatus.INFERRED
                for eid in h.evidence_refs:
                    ev = evidence[eid]
                    c = ev.content
                    if c.get("hypothesis_id") == h.id and qualifies_as_causal_test(c.get("kind", ""), ev.source_type, ev.quality, ev.authority):
                        h.status = HypothesisStatus.SUPPORTED if c.get("supports") else HypothesisStatus.REFUTED
                        h.epistemic_status = EpistemicStatus.VERIFIED
                h.updated_at = min(h.updated_at, T)
                dst.save_hypothesis(h)
                count("hypotheses")

            for task in src.list_tasks():
                if task.created_at <= T:
                    dst.save_task(self._task_at(task, src.get_events_for_task(task.id), T))
                    count("tasks")

            for p in src.list_principals():
                dst.save_principal(p)
            for action in src.list_actions():
                if action.created_at <= T:
                    dst.save_action(self._action_at(action, events, T))
                    count("actions")
            for out in src.list_outcomes():
                if out.recorded_at <= T:
                    dst.save_outcome(out)
        return stats

    # ------------------------------------------------------------------ tasks
    def _task_at(self, task: Task, all_events, T: datetime) -> Task:
        events = [e for e in all_events if e.timestamp <= T]
        view = replay_task_state(task, events, T)
        task.status = view.status
        for step in task.steps:
            step.status = view.steps[step.id]
            step_events = [e for e in events if (e.after_state or {}).get("step_id") == step.id]
            if step_events and "completion_status" in step_events[-1].after_state:
                step.completion_status = EpistemicStatus(step_events[-1].after_state["completion_status"])
            elif not step_events:
                step.completion_status = EpistemicStatus.UNKNOWN
            if step.completed_at and step.completed_at > T:
                step.completed_at = step.completed_by = None
                step.completion_status = EpistemicStatus.UNKNOWN
            if step.started_at and step.started_at > T:
                step.started_at = None
            step.evidence_refs = [e for e in step.evidence_refs if any(e in ev.evidence_refs for ev in step_events)]
            if step.status != StepStatus.BLOCKED:
                step.blocked_reason = None
            if step.status != StepStatus.NEEDS_REVERIFICATION:
                step.invalidated_reason = None
        task.interruptions = [i for i in task.interruptions if i.at <= T]
        for i in task.interruptions:
            if i.resumed_at and i.resumed_at > T:
                i.resumed_at = i.resumed_by = None
        if task.last_verified_at and task.last_verified_at > T:
            verified = [s.completed_at for s in task.steps if s.completed_at and s.completion_status == EpistemicStatus.VERIFIED]
            task.last_verified_at = max(verified) if verified else None
        task.procedure_revision = self.procedure_revision_at(task, all_events, T)
        task.updated_at = min(task.updated_at, T)
        return task

    def _action_at(self, action: ActionRequest, events, T: datetime) -> ActionRequest:
        trail = [
            e for e in events
            if e.event_type == EventType.ACTION_STATUS_CHANGED and (e.after_state or {}).get("action_id") == action.id
        ]
        if trail:
            action.status = ActionStatus(trail[-1].after_state["status"])
        if action.authorization and action.authorization.at > T:
            action.authorization = None
        if action.performed_at and action.performed_at > T:
            action.performed_at = action.performed_by = None
        if not any(e.description and "outcome" in e.description for e in trail):
            action.outcome_checks = []
        action.updated_at = min(action.updated_at, T)
        return action

    @staticmethod
    def procedure_revision_at(task: Task, all_task_events, T: datetime) -> Optional[str]:
        """Revision a task was planned against at T, undoing acknowledgements after T."""
        acks = [e for e in all_task_events if "procedure_revision" in (e.after_state or {})]
        before = [e for e in acks if e.timestamp <= T]
        if before:
            return before[-1].after_state["procedure_revision"]
        if acks:
            return acks[0].before_state.get("procedure_revision")
        return task.procedure_revision
