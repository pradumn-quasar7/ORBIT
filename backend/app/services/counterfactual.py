"""Counterfactual sandboxes (spec §33, §35 Phase 7, Experiment H).

Fork the world as it was known at T into an isolated store, apply what-if premises
(``Variation``s) as SIMULATION evidence, and compare ORBIT's decisions with the
untouched *static replay* of the same instant. ``sensitivity`` asks the converse
question for a task: which facts, if they turned out wrong or unverified, would
change the next step? Those are the facts worth checking first.

Premises only ever enter sandbox engines; the real engine rejects SIMULATION
evidence (ADR-029).
"""
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.app.domain.models import (
    CounterfactualReport,
    DecisionComparison,
    Event,
    Observation,
    ObservationRequest,
    ObservedEntity,
    ObservedRelation,
    SandboxInfo,
    SensitivityItem,
    SensitivityReport,
    StateCondition,
    Variation,
)
from backend.app.domain.types import EpistemicStatus, SourceType, StepStatus, VariationKind
from backend.app.services.belief import LOCATION
from backend.app.services.conditions import instruction_for
from backend.app.services.sandbox import Sandbox, fork
from backend.app.services.world_state_engine import EntityNotFoundError

COUNTERFACTUAL = "counterfactual"
WEAKEST_FIRST = {EpistemicStatus.CONTRADICTED: 0, EpistemicStatus.UNKNOWN: 1, EpistemicStatus.STALE: 2,
                 EpistemicStatus.INFERRED: 3, EpistemicStatus.OBSERVED: 4, EpistemicStatus.VERIFIED: 5}


class SandboxNotFound(KeyError):
    pass


# --------------------------------------------------------------- premises
def apply_variation(sandbox: Sandbox, v: Variation) -> List[Event]:
    svc = sandbox.services
    at = sandbox.clock.advance(1)  # premises are ordered, one second apart
    provenance = {"counterfactual": True, "sandbox_id": sandbox.id, "description": v.description}
    entity = svc.repo.get_entity(v.entity_id) if v.entity_id else None
    if v.kind != VariationKind.ADVANCE_TIME and entity is None:
        raise EntityNotFoundError(f"Entity {v.entity_id} not found in sandbox")
    events: List[Event] = []
    if v.kind in (VariationKind.SET_ATTRIBUTE, VariationKind.MOVE):
        attribute = LOCATION if v.kind == VariationKind.MOVE else v.attribute
        if not attribute:
            raise ValueError("SET_ATTRIBUTE needs an attribute")
        events = svc.engine.assert_claim(
            v.entity_id, attribute, v.value, source=COUNTERFACTUAL, source_type=SourceType.SIMULATION,
            timestamp=at, provenance=provenance,
        ).events
    elif v.kind == VariationKind.REMOVE:
        region = svc.engine.claims.assess_attribute(entity.id, LOCATION, at).last_known_value or "the workspace"
        ev = svc.engine.record_evidence(
            SourceType.SIMULATION, f"{COUNTERFACTUAL}:remove:{entity.id}", at, source=COUNTERFACTUAL,
            provenance=provenance, content={"kind": "counterfactual_removal", "entity_id": entity.id},
        )
        events = svc.engine.belief.apply_absence(entity, region, at, ev.id, f"{COUNTERFACTUAL}:{sandbox.id}")
        entity.updated_at = max(entity.updated_at, at)
        svc.engine.belief.materialize(entity, entity.updated_at)
        svc.repo.save_entity(entity)
    elif v.kind == VariationKind.INVALIDATE:
        events = svc.engine.record_intervention(
            entity.id, at, v.description or "counterfactual: claim no longer trusted",
            [v.attribute] if v.attribute else None, source=COUNTERFACTUAL, source_type=SourceType.SIMULATION,
            provenance=provenance,
        )
    elif v.kind == VariationKind.SET_RELATION:
        events = svc.engine.record_observation(
            Observation(
                timestamp=at, source=COUNTERFACTUAL, source_type=SourceType.SIMULATION, provenance=provenance,
                observed_entities=[ObservedEntity(candidate_entity_id=entity.id, type=entity.type, relations=[
                    ObservedRelation(relation_type=v.relation_type, target=v.target, present=v.present)])],
            )
        )
    elif v.kind == VariationKind.ADVANCE_TIME:
        sandbox.clock.advance(v.minutes * 60)
    sandbox.variations.append({**v.model_dump(mode="json"), "applied_at": at.isoformat()})
    return events


# ---------------------------------------------------------------- registry
class SandboxRegistry:
    """Process-local, bounded: sandboxes are disposable experiments, not memory."""

    def __init__(self, max_sandboxes: int = 20):
        self.max = max_sandboxes
        self._items: "OrderedDict[str, Sandbox]" = OrderedDict()

    def create(self, source, as_of: datetime, label: Optional[str] = None) -> Sandbox:
        sandbox = fork(source, as_of, label)
        self._items[sandbox.id] = sandbox
        while len(self._items) > self.max:
            self._items.popitem(last=False)
        return sandbox

    def get(self, sandbox_id: str) -> Sandbox:
        if sandbox_id not in self._items:
            raise SandboxNotFound(sandbox_id)
        return self._items[sandbox_id]

    def list(self) -> List[Sandbox]:
        return list(self._items.values())

    def delete(self, sandbox_id: str) -> None:
        self.get(sandbox_id)
        del self._items[sandbox_id]


def info(sandbox: Sandbox) -> SandboxInfo:
    return SandboxInfo(id=sandbox.id, label=sandbox.label, forked_from=sandbox.as_of, now=sandbox.clock.now(),
                       variations=sandbox.variations, counts=sandbox.counts)


# ----------------------------------------------------------------- compare
def _resume_summary(sandbox: Sandbox, task_id: str, at: datetime) -> Dict[str, Any]:
    plan = sandbox.services.tasks.resume(task_id, at)
    return {
        "next_step": plan.next_step.step_id if plan.next_step else None,
        "can_continue": plan.can_continue,
        "blocked_steps": plan.blocked_steps,
        "invalidated_steps": plan.invalidated_steps,
        "requested_observations": [r.instruction for r in plan.requested_observations],
        "message": plan.message,
    }


def compare(
    source,
    as_of: datetime,
    variations: List[Variation],
    task_ids: Optional[List[str]] = None,
    queries: Optional[List[str]] = None,
    include_perception: bool = True,
) -> CounterfactualReport:
    """Static replay of T vs T under the premises. Elapsed time from ADVANCE_TIME is
    part of the premise, so the baseline is evaluated without it."""
    baseline, cf = fork(source, as_of, "static replay"), fork(source, as_of, "counterfactual")
    for v in variations:
        apply_variation(cf, v)
    cf_at = cf.clock.now()
    advanced = sum(v.minutes for v in variations if v.kind == VariationKind.ADVANCE_TIME)
    base_at = cf_at - timedelta(minutes=advanced)
    baseline.clock.set(base_at)

    decisions: List[DecisionComparison] = []
    for task_id in task_ids or []:
        b, c = _resume_summary(baseline, task_id, base_at), _resume_summary(cf, task_id, cf_at)
        keys = ("next_step", "can_continue", "blocked_steps", "invalidated_steps")
        decisions.append(DecisionComparison(kind="resume", subject=task_id, baseline=b, counterfactual=c,
                                            changed=any(b[k] != c[k] for k in keys)))
    for q in queries or []:
        rb, rc = baseline.services.agent.answer(q, base_at), cf.services.agent.answer(q, cf_at)
        b = {"answer": rb.answer, "abstained": rb.abstained, "summary": rb.summary}
        c = {"answer": rc.answer, "abstained": rc.abstained, "summary": rc.summary}
        decisions.append(DecisionComparison(kind="query", subject=q, baseline=b, counterfactual=c,
                                            changed=(b["answer"], b["abstained"]) != (c["answer"], c["abstained"])))
    if include_perception:
        pb, pc = baseline.services.perception.plan(base_at, k=1), cf.services.perception.plan(cf_at, k=1)
        b = {"first_look": pb.actions[0].instruction if pb.actions else None, "uncertain_claims": len(pb.uncertain_claims)}
        c = {"first_look": pc.actions[0].instruction if pc.actions else None, "uncertain_claims": len(pc.uncertain_claims)}
        decisions.append(DecisionComparison(kind="perception", subject="next observation", baseline=b, counterfactual=c,
                                            changed=b != c))
    effects = cf.services.diff.diff(as_of, cf_at, include_unobserved=False, include_tasks=False, save=False).changes
    return CounterfactualReport(
        forked_from=as_of, baseline_at=base_at, counterfactual_at=cf_at, variations=variations, effects=effects,
        decisions=decisions, decision_changed=any(d.changed for d in decisions if d.kind != "perception"),
    )


# ------------------------------------------------------------- sensitivity
def _violating(condition: StateCondition) -> Optional[Any]:
    if condition.operator == "eq":
        return (not condition.expected) if isinstance(condition.expected, bool) else f"NOT({condition.expected})"
    if condition.operator == "ne":
        return condition.expected
    if condition.operator == "in":
        return "__not_in_allowed_set__"
    return None  # "exists" can only be probed by invalidation


def _task_conditions(source_task, procedure_condition: Optional[StateCondition]) -> List[StateCondition]:
    done = (StepStatus.COMPLETED, StepStatus.SKIPPED)
    by_id = {s.id: s for s in source_task.steps}
    out: List[StateCondition] = []
    for step in source_task.steps:
        if step.status in done:
            continue
        out += step.preconditions
        stack, seen = list(step.dependencies), set()
        while stack:
            sid = stack.pop()
            if sid in seen:
                continue
            seen.add(sid)
            if by_id[sid].status in done:
                out += by_id[sid].postconditions
            stack += by_id[sid].dependencies
    if procedure_condition is not None:
        out.append(procedure_condition)
    unique: Dict[Tuple[str, str], StateCondition] = {}
    for c in out:
        unique.setdefault((c.entity_id, c.attribute), c)
    return list(unique.values())


def sensitivity(source, task_id: str, at: datetime) -> SensitivityReport:
    base = fork(source, at, "sensitivity baseline")
    task = base.services.repo.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    baseline_next = _resume_summary(base, task_id, at)["next_step"]
    revision = base.services.tasks.revision_check(task, at)
    conditions = _task_conditions(task, revision.condition if revision else None)

    items: List[SensitivityItem] = []
    for cond in conditions:
        current = source.engine.claims.assess_attribute(cond.entity_id, cond.attribute, at).status
        probes = [("unverified", Variation(kind=VariationKind.INVALIDATE, entity_id=cond.entity_id, attribute=cond.attribute,
                                           description=f"what if {cond.entity_id}.{cond.attribute} were unverified?"))]
        wrong = _violating(cond)
        if wrong is not None:
            probes.insert(0, ("violated", Variation(kind=VariationKind.SET_ATTRIBUTE, entity_id=cond.entity_id,
                                                    attribute=cond.attribute, value=wrong,
                                                    description=f"what if {cond.entity_id}.{cond.attribute} were {wrong!r}?")))
        for probe, variation in probes:
            sb = fork(source, at, f"probe {probe} {cond.entity_id}.{cond.attribute}")
            apply_variation(sb, variation)
            summary = _resume_summary(sb, task_id, sb.clock.now())
            items.append(SensitivityItem(
                entity_id=cond.entity_id, attribute=cond.attribute, expected=cond.expected, probe=probe,
                current_status=current, baseline_next=baseline_next, counterfactual_next=summary["next_step"],
                decision_changed=summary["next_step"] != baseline_next, blocked_steps=summary["blocked_steps"],
            ))
    items.sort(key=lambda i: (not i.decision_changed, WEAKEST_FIRST.get(i.current_status, 9), i.entity_id, i.attribute, i.probe))
    critical = list(dict.fromkeys(f"{i.entity_id}.{i.attribute}" for i in items if i.decision_changed))
    checks = []
    for key in critical:
        entity_id, attribute = key.split(".", 1)
        item = next(i for i in items if i.entity_id == entity_id and i.attribute == attribute)
        if item.current_status != EpistemicStatus.VERIFIED:
            a = source.engine.claims.assess_attribute(entity_id, attribute, at)
            entity = source.repo.get_entity(entity_id)
            name = entity.name if entity and entity.name and entity.name != entity.type else entity_id
            checks.append(ObservationRequest(
                entity_id=entity_id, attribute=attribute,
                reason=f"decision-critical: the next step changes if this is wrong (currently {item.current_status.value})",
                instruction=instruction_for(name, attribute, item.current_status, a.last_known_value),
                current_status=item.current_status, last_known_value=a.last_known_value,
            ))
    return SensitivityReport(task_id=task_id, as_of=at, baseline_next=baseline_next, items=items,
                             critical=critical, recommended_checks=checks)
