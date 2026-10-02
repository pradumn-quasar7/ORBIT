"""Experiment F2 — decision-aware vs uncertainty-driven active perception.

World: task T12 interrupted (spec §11) and resumed a day later. The facts the next
step rests on (valve closed, old pump removed, procedure revision) were re-observed
recently, so they are *fresh* — not uncertain. Meanwhile eight unrelated objects on
the shelf were last seen two days ago (stale, irrelevant clutter).

Hidden truth (the simulator's world, never shown to ORBIT): in future A the valve
was secretly reopened; in future B nothing changed. Each policy gets k looks in a
closed loop (plan one look → it reveals the true state of what it covers → replan).
Then ORBIT's resume preview decides the next step.

Metrics: unsafe continuation (A: still told to install the pump), safe work kept
(B: still allowed to proceed), stale claims refreshed (general situational
awareness), looks spent on decision-critical facts.
"""
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from backend.app.domain.models import Observation, ObservedEntity
from backend.app.domain.types import EpistemicStatus
from backend.app.evaluation.counterfactual_eval import _see, at, build_t12_world
from backend.app.services.active_perception import (
    DecisionAwarePolicy,
    FixedPolicy,
    InformationGainPolicy,
    PerceptionPolicy,
    RandomPolicy,
)
from backend.app.services.belief import LOCATION
from backend.app.services.sandbox import fork

CLUTTER = [f"part_{i}" for i in range(8)]
REFRESH_AT = 25 * 60  # minutes: critical facts re-observed a day later
DECIDE_AT = REFRESH_AT + 20


def build_world():
    world = build_t12_world()
    n = [500]
    for part in CLUTTER:
        n[0] += 1
        world.engine.record_observation(Observation(
            id=f"c{n[0]}", timestamp=at(0), source="camera",
            observed_entities=[ObservedEntity(candidate_entity_id=part, type="part", location="shelf")],
        ))
    _see(world, REFRESH_AT, "valve", "valve", n, state="closed")
    _see(world, REFRESH_AT, "pump_old", "pump", n, installed=False)
    _see(world, REFRESH_AT, "sop", "procedure", n, procedure_revision="rev3")
    return world


def _truth(world, valve_reopened: bool) -> Dict[Tuple[str, str], Any]:
    truth: Dict[Tuple[str, str], Any] = {}
    for e in world.repo.list_entities():
        for attr, value in e.current_state.items():
            truth[(e.id, attr)] = value
    if valve_reopened:
        truth[("valve", "state")] = "open"
    return truth


def _execute(sandbox, action, truth, step: int) -> None:
    """A look reveals the true values of exactly the claims it covers."""
    by_entity: Dict[str, Dict[str, Any]] = {}
    for key in action.resolves:
        entity_id, attr = key.split(".", 1)
        by_entity.setdefault(entity_id, {})[attr] = truth[(entity_id, attr)]
    detections = []
    for entity_id, attrs in sorted(by_entity.items()):
        entity = sandbox.services.repo.get_entity(entity_id)
        location = attrs.pop(LOCATION, None)
        detections.append(ObservedEntity(candidate_entity_id=entity_id, type=entity.type, location=location, attributes=attrs))
    at_ = sandbox.clock.advance(30)
    sandbox.services.engine.record_observation(Observation(
        id=f"look_{step}_{action.target}", timestamp=at_, source="inspection_camera", observed_entities=detections,
    ))


def run_policy(world, policy: PerceptionPolicy, valve_reopened: bool, k: int) -> Dict[str, Any]:
    sandbox = fork(world, at(DECIDE_AT))
    truth = _truth(world, valve_reopened)
    planner = sandbox.services.perception
    stale_before = {
        (c.entity_id, c.attribute) for c in planner.uncertain_claims(at(DECIDE_AT)) if c.status == EpistemicStatus.STALE
    }
    looks: List[str] = []
    critical_looks = 0
    for i in range(k):
        plan = planner.plan(sandbox.clock.now(), policy, k=1)
        if not plan.actions:
            break
        action = plan.actions[0]
        critical = {f"{c.entity_id}.{c.attribute}" for c in plan.uncertain_claims if c.decision_critical}
        critical_looks += int(bool(critical & set(action.resolves)))
        looks.append(f"{action.action_type.value}:{action.target}")
        _execute(sandbox, action, truth, i)
    now = sandbox.clock.now()
    still_stale = {
        (c.entity_id, c.attribute) for c in planner.uncertain_claims(now) if c.status == EpistemicStatus.STALE
    }
    preview = sandbox.services.tasks.preview("T12", now)
    return {
        "looks": looks,
        "next_step": preview.next_step.step_id if preview.next_step else None,
        "stale_refreshed": len(stale_before - still_stale),
        "critical_looks": critical_looks,
    }


def run_experiment_f2(ks=(1, 2, 3), random_seeds=range(5)) -> Dict[str, Any]:
    world = build_world()
    policies: List[Tuple[str, Any]] = [
        ("decision_aware", lambda: DecisionAwarePolicy()),
        ("information_gain", lambda: InformationGainPolicy()),
        ("fixed", lambda: FixedPolicy()),
    ] + [(f"random_{s}", (lambda s=s: RandomPolicy(s))) for s in random_seeds]
    rows: Dict[str, Dict[str, Any]] = {}
    for name, make in policies:
        row: Dict[str, Any] = {}
        for k in ks:
            a = run_policy(world, make(), valve_reopened=True, k=k)
            b = run_policy(world, make(), valve_reopened=False, k=k)
            row[k] = {
                "unsafe_continuation": int(a["next_step"] == "s7"),  # future A: valve open, pump install offered
                "safe_work_kept": int(b["next_step"] == "s7"),  # future B: still allowed to proceed
                "stale_refreshed": a["stale_refreshed"],
                "critical_looks": a["critical_looks"],
                "first_look": a["looks"][0] if a["looks"] else None,
            }
        rows[name] = row

    def summarise(prefix: str, k: int, key: str) -> float:
        vals = [rows[n][k][key] for n in rows if n.startswith(prefix)]
        return sum(vals) / len(vals)

    summary = {
        name: {k: {m: summarise(name, k, m) for m in ("unsafe_continuation", "safe_work_kept", "stale_refreshed", "critical_looks")}
               for k in ks}
        for name in ("decision_aware", "information_gain", "fixed", "random")
    }
    return {"per_policy": rows, "summary": summary, "ks": list(ks), "clutter_objects": len(CLUTTER)}
