"""Phase 5 — task continuity: dependency checks, step invalidation, resume protocol.

Exit criterion: interrupted tasks resume from verified state.
Scenario follows spec §4 / §11: T12 "Replace coolant pump", steps 5–8.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.models import Observation, ObservedEntity, StateCondition
from backend.app.domain.types import ConditionState
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType, StepStatus, TaskStatus
from backend.app.services.tasks import PostconditionViolatedError, StepNotReadyError, StepSpec, TaskService
from backend.app.services.world_state_engine import WorldStateEngine

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


class Bench:
    def __init__(self, repo):
        self.repo = repo
        self.engine = WorldStateEngine(repo)
        self.tasks = TaskService(repo, self.engine)
        self._n = 0

    def see(self, minutes, cid, type="thing", source="camera", **kw):
        self._n += 1
        self.engine.record_observation(
            Observation(id=f"o{self._n}", timestamp=at(minutes), source=source,
                        observed_entities=[ObservedEntity(candidate_entity_id=cid, type=type, **kw)])
        )

    def verify(self, minutes, cid, attr, value):
        self.engine.assert_claim(cid, attr, value, source="manual_verification", authority=0.95, timestamp=at(minutes))


@pytest.fixture
def b(repo):
    bench = Bench(repo)
    bench.see(0, "valve", type="valve", location="bench_3", attributes={"state": "open"})
    bench.see(0, "pump_old", type="pump", location="bench_3", attributes={"installed": True})
    bench.see(0, "pump_new", type="pump", location="cart", attributes={"model_number": "CP-200"})
    bench.see(0, "sop", type="procedure", attributes={"procedure_revision": "rev3"})
    bench.tasks.create_task(
        "Replace coolant pump",
        [
            StepSpec(id="s5", step_order=5, description="isolate system",
                     postconditions=[StateCondition(entity_id="valve", attribute="state", expected="closed")]),
            StepSpec(id="s6", step_order=6, description="remove old pump", dependencies=["s5"],
                     postconditions=[StateCondition(entity_id="pump_old", attribute="installed", expected=False)]),
            StepSpec(id="s7", step_order=7, description="install new pump", dependencies=["s6"],
                     preconditions=[StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200", min_status=S.VERIFIED)]),
            StepSpec(id="s8", step_order=8, description="leak test", dependencies=["s7"]),
        ],
        at(1),
        task_id="T12",
        procedure_entity_id="sop",
        procedure_revision="rev3",
    )
    return bench


def session_a(b, verify_model=True):
    """Work done before the interruption: steps 5 and 6, then the user leaves."""
    b.see(5, "valve", type="valve", attributes={"state": "closed"})
    b.tasks.complete_step("T12", "s5", at(6), source="manual_verification", authority=0.95, actor="ana")
    b.see(10, "pump_old", type="pump", attributes={"installed": False})
    b.tasks.complete_step("T12", "s6", at(11), source="user", actor="ana")
    if verify_model:
        b.verify(12, "pump_new", "model_number", "CP-200")
    b.tasks.interrupt_task("T12", at(15), reason="end of shift", actor="ana")


def assert_safe(plan):
    """No unsafe continuation: the presented step never has unmet prerequisites."""
    if plan.next_step is not None:
        assert plan.next_step.ready and not plan.next_step.blockers
        assert all(c.state == ConditionState.SATISFIED for c in plan.next_step.preconditions)


# ---------------------------------------------------------------- gating
def test_start_is_gated_on_transitive_readiness(b):
    with pytest.raises(StepNotReadyError, match="waits on step 5"):
        b.tasks.start_step("T12", "s6", at(2))
    b.see(5, "valve", type="valve", attributes={"state": "closed"})
    b.tasks.complete_step("T12", "s5", at(6))
    b.see(10, "pump_old", type="pump", attributes={"installed": False})
    b.tasks.complete_step("T12", "s6", at(11))
    # Model number only OBSERVED; step 7 needs it VERIFIED.
    with pytest.raises(StepNotReadyError, match="requires VERIFIED"):
        b.tasks.start_step("T12", "s7", at(12))
    b.verify(13, "pump_new", "model_number", "CP-200")
    assert b.tasks.start_step("T12", "s7", at(14)).steps[2].status == StepStatus.IN_PROGRESS


def test_completion_contradicted_by_world_is_refused(b):
    # Someone says "isolated" but the valve is observed open.
    with pytest.raises(PostconditionViolatedError, match="valve.state"):
        b.tasks.complete_step("T12", "s5", at(2), source="user")
    assert b.repo.get_task("T12").steps[0].status == StepStatus.PENDING


def test_outcome_verified_by_world_evidence_upgrades_completion(b):
    b.verify(5, "valve", "state", "closed")
    task = b.tasks.complete_step("T12", "s5", at(6), source="user")  # a person's word …
    assert task.steps[0].completion_status == S.VERIFIED  # … backed by verified world state
    assert task.last_verified_at == at(6)


# ------------------------------------------------------------------ resume
def test_resume_from_verified_state_without_changes(b, repo):
    """Phase 5 exit: an interrupted task resumes from its last verified state."""
    session_a(b)
    plan = b.tasks.resume("T12", at(60), actor="ana")
    assert plan.checkpoint == at(6)  # last verified state (step 5 verification)
    assert plan.invalidated_steps == [] and plan.requested_observations == []
    assert plan.can_continue and plan.next_step.step_id == "s7"
    assert [s.status for s in plan.steps] == [StepStatus.COMPLETED, StepStatus.COMPLETED, StepStatus.PENDING, StepStatus.BLOCKED]
    assert plan.task_status == TaskStatus.IN_PROGRESS
    task = repo.get_task("T12")
    assert task.interruptions[0].resumed_at == at(60)
    assert_safe(plan)


def test_world_change_invalidates_completed_step(b, repo):
    """The valve was reopened between sessions: step 5's outcome no longer holds."""
    session_a(b)
    b.see(30, "valve", type="valve", attributes={"state": "open"})
    plan = b.tasks.resume("T12", at(60))
    assert plan.invalidated_steps == ["s5"]
    # Changes since the last verified state (step 5 at 09:06): step 6's own work and the reopened valve.
    assert [(c.change_type, c.entity_id, c.after) for c in plan.world_changes] == [
        (EventType.OBJECT_STATE_CHANGED, "pump_old", False),
        (EventType.OBJECT_STATE_CHANGED, "valve", "open"),
    ]
    s7 = next(s for s in plan.steps if s.step_id == "s7")
    assert not s7.ready and "step 5 (isolate system) must be re-verified" in s7.blockers
    # Re-verification first; installing the pump is never offered.
    assert plan.next_step.step_id == "s5" and plan.next_step.status == StepStatus.NEEDS_REVERIFICATION
    assert "re-verified" in plan.message
    assert repo.get_task("T12").steps[0].status == StepStatus.NEEDS_REVERIFICATION
    assert_safe(plan)

    # Redo step 5 → resume can proceed to step 7.
    b.see(70, "valve", type="valve", attributes={"state": "closed"})
    b.tasks.complete_step("T12", "s5", at(71), source="manual_verification", authority=0.95)
    plan = b.tasks.resume("T12", at(72))
    assert plan.next_step.step_id == "s7" and plan.invalidated_steps == []


def test_stale_outcome_blocks_and_requests_observation(b):
    """Valve was serviced (intervention) → its state is STALE, not known to have changed."""
    session_a(b)
    b.engine.record_intervention("valve", at(30), "maintenance crew worked on the valve", attributes=["state"])
    plan = b.tasks.resume("T12", at(60))
    assert plan.invalidated_steps == []  # not contradicted, only unverified
    s5 = plan.steps[0]
    assert s5.status == StepStatus.COMPLETED and s5.completion_status == S.STALE
    assert not plan.can_continue and plan.next_step is None and plan.task_status == TaskStatus.BLOCKED
    [req] = plan.requested_observations
    assert (req.entity_id, req.attribute, req.current_status) == ("valve", "state", S.STALE)
    assert req.instruction == "Show me valve so I can check its state."
    assert req.for_steps == ["s7", "s8"]

    b.see(65, "valve", type="valve", attributes={"state": "closed"})
    plan = b.tasks.resume("T12", at(66))
    assert plan.can_continue and plan.next_step.step_id == "s7"


def test_unverified_precondition_requests_targeted_observation(b):
    """Spec §15 uncertainty contract: 'Show the model label on the replacement pump.'"""
    session_a(b, verify_model=False)
    plan = b.tasks.resume("T12", at(60))
    assert plan.next_step is None and not plan.can_continue
    [req] = plan.requested_observations
    assert (req.entity_id, req.attribute) == ("pump_new", "model_number")
    assert req.instruction == "Move closer so I can read the model number on pump_new."
    assert "requires VERIFIED" in req.reason and req.for_steps == ["s7"]
    assert "Requested observation" in plan.message
    assert_safe(plan)


def test_replaced_spare_pump_makes_precondition_stale(b):
    session_a(b)
    b.engine.record_intervention("pump_new", at(30), "spare pumps were swapped on the cart")
    plan = b.tasks.resume("T12", at(60))
    [req] = plan.requested_observations
    assert (req.entity_id, req.current_status) == ("pump_new", S.STALE)
    assert plan.next_step is None


def test_contradicted_precondition_asks_for_verification(b):
    session_a(b)
    b.engine.assert_claim("pump_new", "model_number", "CP-150", source="digital_registry", timestamp=at(30))
    plan = b.tasks.resume("T12", at(31))
    [req] = plan.requested_observations
    assert req.current_status == S.CONTRADICTED
    assert req.instruction.startswith("Sources disagree about pump_new's model number.")


def test_procedure_revision_change_blocks_until_acknowledged(b):
    session_a(b)
    b.see(30, "sop", type="procedure", attributes={"procedure_revision": "rev4"})
    plan = b.tasks.resume("T12", at(60))
    assert plan.procedure_revision == {"planned": "rev3", "current": "rev4", "state": "VIOLATED", "status": "OBSERVED"}
    assert plan.next_step is None
    assert all("procedure revision" in s.blockers[0] for s in plan.steps if s.status != StepStatus.COMPLETED)
    assert (EventType.PROCEDURE_REVISION_DETECTED, "sop") in [(c.change_type, c.entity_id) for c in plan.world_changes]

    b.tasks.acknowledge_revision("T12", "rev4", at(61), actor="supervisor")
    assert b.tasks.resume("T12", at(62)).next_step.step_id == "s7"


def test_multi_user_handoff(b, repo):
    session_a(b)  # ana interrupted
    plan = b.tasks.resume("T12", at(60), actor="bob")
    assert plan.resumed_by == "bob" and plan.next_step.step_id == "s7"
    assert repo.get_task("T12").interruptions[0].resumed_by == "bob"
    descriptions = [e.description for e in b.tasks.history("T12")]
    assert any("resumed by bob" in d for d in descriptions)


def test_branching_graph_offers_any_supported_frontier_step(repo):
    b = Bench(repo)
    b.see(0, "gauge", attributes={"reading": "ok"})
    b.see(0, "label", attributes={"text": "A"}, confidence=0.2)  # weak evidence only
    b.tasks.create_task(
        "parallel prep",
        [
            StepSpec(id="x", description="check label", preconditions=[StateCondition(entity_id="label", attribute="text", expected="A")]),
            StepSpec(id="y", description="check gauge", preconditions=[StateCondition(entity_id="gauge", attribute="reading", expected="ok")]),
        ],
        at(1),
        task_id="P",
    )
    plan = b.tasks.resume("P", at(2))
    assert plan.next_step.step_id == "y"  # x is blocked on insufficient evidence, y is supported
    assert [r.entity_id for r in plan.requested_observations] == ["label"]


# ----------------------------------------------------------------------- API
def test_resume_api(client):
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera",
                                        "observed_entities": [{"candidate_entity_id": "valve", "type": "valve", "attributes": {"state": "closed"}}]})
    client.post("/tasks", json={"id": "T", "goal": "g", "created_at": t(0), "steps": [
        {"id": "a", "description": "isolate", "postconditions": [{"entity_id": "valve", "attribute": "state", "expected": "closed"}]},
        {"id": "b", "description": "work", "dependencies": ["a"]}]})
    client.post("/tasks/T/steps/a/complete", json={"at": t(1), "source": "manual_verification", "authority": 0.95})
    client.post("/tasks/T/interrupt", json={"at": t(2)})
    client.post("/observations", json={"id": "o2", "timestamp": t(30), "source": "camera",
                                        "observed_entities": [{"candidate_entity_id": "valve", "type": "valve", "attributes": {"state": "open"}}]})
    plan = client.post("/tasks/T/resume", json={"at": t(31), "actor": "bob"}).json()
    assert plan["invalidated_steps"] == ["a"] and plan["next_step"]["step_id"] == "a"
    assert client.post("/tasks/T/procedure/acknowledge", json={"revision": "r2"}).json()["procedure_revision"] == "r2"
    assert client.post("/tasks/nope/resume", json={}).status_code == 404
