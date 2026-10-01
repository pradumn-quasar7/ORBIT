"""Experiment H (spec §25): counterfactual decision quality vs static replay.

World: task T12 (spec §11) interrupted after steps 5 and 6. Five possible futures may
happen during the interruption; for each, the safe decision on return is known
(ground truth). Two policies commit to a decision *before* knowing which future
happens:

* static replay — plan once from the stored state at the interruption;
* counterfactual — run ``sensitivity`` (one sandbox per fragile fact) and keep a
  contingency table: "if this fact turns out wrong / unverified, do X".

When a future occurs, the counterfactual policy looks up the entry matching the
fact that changed. We also run ORBIT's real resume in each future to confirm the
ground truth is what ORBIT itself concludes once evidence arrives.

Caveat: futures are hand-specified, so this measures the value of contingency
planning in a controlled world, not real-world foresight.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, StateCondition
from backend.app.domain.types import EpistemicStatus as S
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.services.counterfactual import sensitivity
from backend.app.services.sandbox import fork
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
INTERRUPTED_AT = 16  # minutes


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _see(svc, minutes: float, cid: str, type: str, n: List[int], **attributes) -> None:
    n[0] += 1
    svc.engine.record_observation(Observation(
        id=f"h{n[0]}", timestamp=at(minutes), source="camera",
        observed_entities=[ObservedEntity(candidate_entity_id=cid, type=type, location="bench", attributes=attributes)],
    ))


def build_t12_world() -> OrbitServices:
    svc = OrbitServices.build(InMemoryRepository())
    n = [0]
    _see(svc, 0, "valve", "valve", n, state="open")
    _see(svc, 0, "pump_old", "pump", n, installed=True)
    _see(svc, 0, "pump_new", "pump", n, model_number="CP-200")
    _see(svc, 0, "sop", "procedure", n, procedure_revision="rev3")
    svc.tasks.create_task("Replace coolant pump", [
        StepSpec(id="s5", step_order=5, description="isolate system",
                 postconditions=[StateCondition(entity_id="valve", attribute="state", expected="closed")]),
        StepSpec(id="s6", step_order=6, description="remove old pump", dependencies=["s5"],
                 postconditions=[StateCondition(entity_id="pump_old", attribute="installed", expected=False)]),
        StepSpec(id="s7", step_order=7, description="install new pump", dependencies=["s6"],
                 preconditions=[StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200", min_status=S.VERIFIED)]),
        StepSpec(id="s8", step_order=8, description="leak test", dependencies=["s7"]),
    ], at(1), task_id="T12", procedure_entity_id="sop", procedure_revision="rev3")
    _see(svc, 5, "valve", "valve", n, state="closed")
    svc.tasks.complete_step("T12", "s5", at(6), source="manual_verification", authority=0.95)
    _see(svc, 10, "pump_old", "pump", n, installed=False)
    svc.tasks.complete_step("T12", "s6", at(11))
    svc.engine.assert_claim("pump_new", "model_number", "CP-200", source="manual_verification", authority=0.95, timestamp=at(12))
    svc.tasks.interrupt_task("T12", at(15), actor="ana")
    return svc


# (name, what happens in the real world, signature of the changed fact, safe next step)
Future = Tuple[str, Callable[[Any, List[int]], None], Optional[Tuple[str, str, str]], Optional[str]]

FUTURES: List[Future] = [
    ("nothing changes", lambda svc, n: None, None, "s7"),
    ("valve reopened", lambda svc, n: _see(svc, 30, "valve", "valve", n, state="open"), ("valve", "state", "violated"), "s5"),
    ("spare pumps swapped", lambda svc, n: svc.engine.record_intervention("pump_new", at(30), "spares swapped on the cart"),
     ("pump_new", "model_number", "unverified"), None),
    ("label reads CP-150", lambda svc, n: _see(svc, 30, "pump_new", "pump", n, model_number="CP-150"),
     ("pump_new", "model_number", "violated"), None),
    ("procedure revised", lambda svc, n: _see(svc, 30, "sop", "procedure", n, procedure_revision="rev4"),
     ("sop", "procedure_revision", "violated"), None),
]


def run_experiment_h() -> Dict[str, Any]:
    world = build_t12_world()
    decided_at = at(INTERRUPTED_AT)
    static_plan = fork(world, decided_at).services.tasks.resume("T12", decided_at)
    static_next = static_plan.next_step.step_id if static_plan.next_step else None
    report = sensitivity(world, "T12", decided_at)
    contingency = {(i.entity_id, i.attribute, i.probe): i.counterfactual_next for i in report.items}

    rows, score = [], {"static_replay": [0, 0], "counterfactual": [0, 0]}
    unsafe = {"static_replay": 0, "counterfactual": 0}
    for name, happen, signature, safe in FUTURES:
        future = fork(world, decided_at)  # the world after the interruption, as it really unfolds
        happen(future.services, [100])
        realized = future.services.tasks.resume("T12", at(60))
        realized_next = realized.next_step.step_id if realized.next_step else None
        prepared = {
            "static_replay": static_next,
            "counterfactual": report.baseline_next if signature is None else contingency.get(signature, report.baseline_next),
        }
        for policy, decision in prepared.items():
            score[policy][0] += int(decision == safe)
            score[policy][1] += 1
            unsafe[policy] += int(decision is not None and decision != safe)
        rows.append({"future": name, "safe_next": safe, "orbit_on_return": realized_next,
                     "static_replay": prepared["static_replay"], "counterfactual": prepared["counterfactual"]})
    n = len(FUTURES)
    return {
        "futures": rows,
        "decision_quality": {p: s[0] / s[1] for p, s in score.items()},
        "unsafe_precommitment_rate": {p: u / n for p, u in unsafe.items()},
        "ground_truth_agrees_with_orbit_on_return": all(r["safe_next"] == r["orbit_on_return"] for r in rows),
        "decision_critical_claims": report.critical,
        "sandboxes_used": 1 + len(report.items) + 1,
    }
