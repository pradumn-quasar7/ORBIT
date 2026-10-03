"""Active perception (spec §13): when evidence is insufficient, seek the most useful
observation instead of guessing.

Two weightings (Phase 11): *uncertainty* (how unsure ORBIT is) and *decision value*
(how much the answer matters for the next step of an open task — value of
information). ``DecisionAwarePolicy`` ranks by the latter, so a fresh but unverified
fact that the next consequential step rests on is checked before irrelevant clutter.

    score(action) = expected_uncertainty_reduction(action) / observation_cost(action)

Uncertain claims are gathered from the evidence gate (stale, unknown, contradicted,
inferred, aging, below the status a task requires); claims that block a task step
count double. Candidate actions are generated per region (one look refreshes every
claim visible there), per claim (close-up), per contradiction (hands-on
verification) and per lost object (coverage search). The policy that orders them is
replaceable — ``InformationGainPolicy`` is the heuristic, ``FixedPolicy`` and
``RandomPolicy`` are the Experiment F baselines.
"""
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from backend.app.domain.models import ObservationCost, ObservationRequest, PerceptionPlan, PlannedObservation, UncertainClaim
from backend.app.domain.types import ConditionState, EpistemicStatus, FreshnessState, ObservationActionType, StepStatus, TaskStatus
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.claims import ClaimEvaluator
from backend.app.services.conditions import instruction_for
from backend.app.services.spatial import AnchorRegistry

A = ObservationActionType

UNCERTAINTY = {
    EpistemicStatus.CONTRADICTED: 1.0,
    EpistemicStatus.UNKNOWN: 0.9,
    EpistemicStatus.STALE: 0.7,
    EpistemicStatus.INFERRED: 0.6,
}
AGING_UNCERTAINTY = 0.3
BELOW_REQUIRED_UNCERTAINTY = 0.5  # OBSERVED where a task needs VERIFIED
TASK_RELEVANCE = 2.0
DECISION_RELEVANCE = 3.0  # claim the next step of an open task rests on
OBSERVED_CRITICAL_UNCERTAINTY = 0.4  # supported but unverified, and the plan depends on it
NON_DECISION_DISCOUNT = 0.25  # uncertainty about facts no pending decision rests on

VISIBLE_ATTRIBUTES = frozenset({"location", "state", "power", "screen", "color", "installed", "health"})
FINE_ATTRIBUTES = frozenset({"serial_number", "model_number", "model", "label", "asset_tag", "configuration", "firmware", "firmware_version", "procedure_revision", "revision", "calibration"})

BASE_COSTS: Dict[ObservationActionType, ObservationCost] = {
    A.LOOK_AT_ANCHOR: ObservationCost(time_seconds=5, effort=0.2, motion=0.2, privacy=0.3, interruption=0.1),
    A.INSPECT_ENTITY: ObservationCost(time_seconds=10, effort=0.4, motion=0.4, privacy=0.1, interruption=0.2),
    A.SEARCH_REGION: ObservationCost(time_seconds=30, effort=0.6, motion=0.6, privacy=0.4, interruption=0.3),
    A.VERIFY_WITH_PERSON: ObservationCost(time_seconds=60, effort=1.0, motion=0.5, privacy=0.0, interruption=0.6),
}


def p_resolve(action: ObservationActionType, claim: UncertainClaim) -> float:
    """Probability that this kind of observation settles this kind of claim."""
    contradicted = claim.status == EpistemicStatus.CONTRADICTED
    if action == A.VERIFY_WITH_PERSON:
        return 1.0
    if action == A.LOOK_AT_ANCHOR:
        if claim.attribute in VISIBLE_ATTRIBUTES:
            return 0.5 if contradicted else 0.9 if claim.attribute == LOCATION else 0.7
        return 0.1 if contradicted else 0.2
    if action == A.INSPECT_ENTITY:
        return 0.6 if contradicted else 0.95
    if action == A.SEARCH_REGION:
        return 0.6
    return 0.0


class PerceptionPolicy(ABC):
    name = "policy"
    greedy = False  # True: re-score by marginal gain after each pick
    weight_field = "weight"  # which claim weight the expected gain is computed from

    @abstractmethod
    def order(self, candidates: List[PlannedObservation]) -> List[PlannedObservation]: ...


class InformationGainPolicy(PerceptionPolicy):
    """Greedy: repeatedly pick the action with the best *marginal* gain / cost."""

    name = "information_gain"
    greedy = True

    def order(self, candidates):
        return sorted(candidates, key=lambda c: (-c.score, c.cost_total, c.action_type.value, c.target, c.attribute or ""))


class DecisionAwarePolicy(InformationGainPolicy):
    """Greedy on marginal *decision value* / cost (value of information)."""

    name = "decision_aware"
    weight_field = "decision_weight"


class FixedPolicy(PerceptionPolicy):
    """Baseline: inspect uncertain claims one at a time in a fixed (alphabetical) order."""

    name = "fixed"

    def order(self, candidates):
        singles = [c for c in candidates if c.action_type == A.INSPECT_ENTITY]
        return sorted(singles, key=lambda c: (c.target, c.attribute or ""))


class RandomPolicy(PerceptionPolicy):
    name = "random"

    def __init__(self, seed: int = 0):
        self.seed = seed

    def order(self, candidates):
        shuffled = sorted(candidates, key=lambda c: (c.action_type.value, c.target, c.attribute or ""))
        random.Random(self.seed).shuffle(shuffled)
        return shuffled


POLICIES = {
    "information_gain": InformationGainPolicy,
    "decision_aware": DecisionAwarePolicy,
    "fixed": FixedPolicy,
    "random": RandomPolicy,
}


class ActivePerceptionPlanner:
    def __init__(self, repository: Repository, claims: ClaimEvaluator, anchors: AnchorRegistry, tasks=None):
        self.repo = repository
        self.claims = claims
        self.anchors = anchors
        self.tasks = tasks  # TaskService, optional (task relevance)

    # --------------------------------------------------------------- claims
    def uncertain_claims(
        self, as_of: datetime, entity_ids: Optional[Iterable[str]] = None, include_decision_critical: bool = False
    ) -> List[UncertainClaim]:
        """Claims worth observing. With ``include_decision_critical`` also claims that are
        supported (fresh OBSERVED) but that the next step of an open task rests on."""
        wanted = set(entity_ids) if entity_ids else None
        blocking = self._task_needs(as_of)
        critical = self._decision_critical(as_of)
        found: Dict[Tuple[str, str], UncertainClaim] = {}
        for entity in self.repo.list_entities():
            if entity.created_at > as_of or (wanted and entity.id not in wanted) or entity.merged_into:
                continue
            region = self._region(entity.id, as_of)
            attrs = sorted({v.attribute for v in self.repo.get_state_versions_for_entity(entity.id)})
            for attr in attrs:
                a = self.claims.assess_attribute(entity.id, attr, as_of)
                base = UNCERTAINTY.get(a.status)
                if a.supportable and a.freshness and a.freshness.state == FreshnessState.AGING:
                    base = AGING_UNCERTAINTY
                need = blocking.get((entity.id, attr))
                if need is not None and base is None:
                    base = BELOW_REQUIRED_UNCERTAINTY  # supportable but not good enough for the task
                # Critical: the next step rests on it, or a blocked step is waiting on it.
                crit_steps = critical.get((entity.id, attr)) or list(need or [])
                observed_critical = (
                    base is None and crit_steps and include_decision_critical and a.status == EpistemicStatus.OBSERVED
                )
                if base is None and not observed_critical:
                    continue
                steps = need or []
                weight = (base or 0.0) * (TASK_RELEVANCE if steps else 1.0)
                if crit_steps:
                    decision_weight = (base if base is not None else OBSERVED_CRITICAL_UNCERTAINTY) * DECISION_RELEVANCE
                else:
                    decision_weight = weight * NON_DECISION_DISCOUNT
                if observed_critical:
                    reason = f"the next step ({', '.join(crit_steps)}) rests on this; observed but not verified"
                elif need is not None and a.supportable:
                    reason = "task requires stronger evidence"
                else:
                    reason = a.reason
                found[(entity.id, attr)] = UncertainClaim(
                    entity_id=entity.id,
                    attribute=attr,
                    status=a.status,
                    reason=reason,
                    last_known_value=a.last_known_value,
                    region=region,
                    weight=weight,
                    blocking_steps=steps,
                    decision_critical=bool(crit_steps),
                    decision_weight=round(decision_weight, 4),
                    critical_for_steps=list(crit_steps),
                )
        return sorted(found.values(), key=lambda c: (-c.weight, -c.decision_weight, c.entity_id, c.attribute))

    def _decision_critical(self, as_of: datetime) -> Dict[Tuple[str, str], List[str]]:
        """(entity, attribute) → next steps that rest on it, via a non-mutating resume preview."""
        out: Dict[Tuple[str, str], List[str]] = {}
        if self.tasks is None or self.repo.in_transaction:
            return out
        for task in self.repo.list_tasks():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.ABANDONED) or task.created_at > as_of:
                continue
            plan = self.tasks.preview(task.id, as_of)
            if plan.next_step is None:
                continue
            for chk in plan.decision_critical:
                out.setdefault((chk.condition.entity_id, chk.condition.attribute), []).append(plan.next_step.step_id)
        return out

    def _region(self, entity_id: str, as_of: datetime) -> Optional[str]:
        a = self.claims.assess_attribute(entity_id, LOCATION, as_of)
        return a.last_known_value if a.last_known_value is not None else None

    def _task_needs(self, as_of: datetime) -> Dict[Tuple[str, str], List[str]]:
        """(entity, attribute) → step ids blocked on it, across open tasks."""
        needs: Dict[Tuple[str, str], List[str]] = {}
        if self.tasks is None:
            return needs
        for task in self.repo.list_tasks():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.ABANDONED):
                continue
            for step in task.steps:
                if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                    continue
                a = self.tasks.assess_step(task, step, as_of)
                checks = list(a.preconditions)
                for anc in self.tasks.ancestors(task, step):
                    checks += [self.tasks.conditions.check(c, as_of, step.risk) for c in anc.postconditions]
                for chk in checks:
                    if chk.state == ConditionState.UNSUPPORTED:
                        needs.setdefault((chk.condition.entity_id, chk.condition.attribute), []).append(step.id)
        return needs

    # ------------------------------------------------------------ candidates
    def _cost(self, action: ObservationActionType, region: Optional[str]) -> ObservationCost:
        cost = BASE_COSTS[action].model_copy()
        for anchor_id in self.anchors.lineage(region) if region else []:
            anchor = self.repo.get_anchor(anchor_id)
            if anchor and anchor.frame.get("privacy") == "high":
                cost.privacy = min(1.0, cost.privacy + 0.6)  # e.g. a shared office with people in view
                break
        return cost

    def _name(self, entity_id: str) -> str:
        e = self.repo.get_entity(entity_id)
        return e.name if e and e.name and e.name != e.type else entity_id

    def _plan(
        self, action, target, attribute, instruction, claims: List[UncertainClaim], region, weight_field: str = "weight"
    ) -> Optional[PlannedObservation]:
        gain = sum(getattr(c, weight_field) * p_resolve(action, c) for c in claims)
        if gain <= 0:
            return None
        cost = self._cost(action, region)
        return PlannedObservation(
            action_type=action,
            target=target,
            attribute=attribute,
            instruction=instruction,
            resolves=[f"{c.entity_id}.{c.attribute}" for c in claims],
            expected_uncertainty_reduction=round(gain, 4),
            cost=cost,
            cost_total=round(cost.total, 4),
            score=round(gain / cost.total, 4),
        )

    def candidates(self, claims: List[UncertainClaim], weight_field: str = "weight") -> List[PlannedObservation]:
        out: List[PlannedObservation] = []
        by_region: Dict[str, List[UncertainClaim]] = {}
        for c in claims:
            located = c.region is not None and not (c.attribute == LOCATION and c.status == EpistemicStatus.UNKNOWN)
            if located:
                by_region.setdefault(c.region, []).append(c)
        for region, group in sorted(by_region.items()):
            n = len({c.entity_id for c in group})
            plan = self._plan(A.LOOK_AT_ANCHOR, region, None, f"Point the camera at {region} so I can refresh {n} item(s).", group, region, weight_field)
            if plan:
                out.append(plan)
        for c in claims:
            name = self._name(c.entity_id)
            if c.attribute == LOCATION and c.status == EpistemicStatus.UNKNOWN:
                parent = self.anchors.lineage(c.last_known_value)[-1] if c.last_known_value else "the workspace"
                plan = self._plan(A.SEARCH_REGION, parent, LOCATION, f"Search {parent} for {name}.", [c], parent, weight_field)
            else:
                plan = self._plan(A.INSPECT_ENTITY, c.entity_id, c.attribute, instruction_for(name, c.attribute, c.status, c.last_known_value), [c], c.region, weight_field)
            if plan:
                out.append(plan)
            if c.status == EpistemicStatus.CONTRADICTED:
                plan = self._plan(
                    A.VERIFY_WITH_PERSON, c.entity_id, c.attribute,
                    f"Please check {name}'s {c.attribute.replace('_', ' ')} by hand and confirm the value.", [c], c.region, weight_field,
                )
                if plan:
                    out.append(plan)
        return out

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        as_of: datetime,
        policy: Optional[PerceptionPolicy] = None,
        entity_ids: Optional[Iterable[str]] = None,
        k: int = 5,
    ) -> PerceptionPlan:
        policy = policy or InformationGainPolicy()
        field = policy.weight_field
        claims = self.uncertain_claims(as_of, entity_ids, include_decision_critical=field == "decision_weight")
        by_key = {f"{c.entity_id}.{c.attribute}": c for c in claims}
        residual = {key: getattr(c, field) for key, c in by_key.items()}
        pool = policy.order(self.candidates(claims, field))
        chosen: List[PlannedObservation] = []
        while pool and len(chosen) < k:
            # Expected reduction is *marginal*: what this look still removes given the
            # looks planned before it (overlapping views must not double-count).
            for cand in pool:
                gain = sum(residual[r] * p_resolve(cand.action_type, by_key[r]) for r in cand.resolves)
                cand.expected_uncertainty_reduction = round(gain, 4)
                cand.score = round(gain / cand.cost_total, 4)
            if policy.greedy:
                pool = policy.order(pool)
            pick = pool.pop(0)
            if pick.expected_uncertainty_reduction <= 0 and policy.greedy:
                break
            chosen.append(pick)
            for r in pick.resolves:
                residual[r] *= 1.0 - p_resolve(pick.action_type, by_key[r])
        return PerceptionPlan(
            as_of=as_of,
            policy=policy.name,
            weighting="decision_value" if field == "decision_weight" else "uncertainty",
            uncertain_claims=claims,
            total_uncertainty=round(sum(c.weight for c in claims), 4),
            actions=chosen,
        )

    def rank_requests(self, requests: List[ObservationRequest]) -> List[ObservationRequest]:
        """Score single-claim requests (e.g. from the query agent) with the same heuristic."""
        for r in requests:
            claim = UncertainClaim(
                entity_id=r.entity_id, attribute=r.attribute, status=r.current_status, reason=r.reason,
                weight=UNCERTAINTY.get(r.current_status, BELOW_REQUIRED_UNCERTAINTY) * (TASK_RELEVANCE if r.for_steps else 1.0),
            )
            action = A.LOOK_AT_ANCHOR if r.attribute in VISIBLE_ATTRIBUTES else A.INSPECT_ENTITY
            r.score = round(claim.weight * p_resolve(action, claim) / BASE_COSTS[action].total, 4)
        return sorted(requests, key=lambda r: -(r.score or 0))
