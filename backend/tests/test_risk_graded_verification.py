"""Phase 12 — risk-graded verification before consequential steps and actions.

Exit criterion: a HIGH-risk step or action never proceeds on old unverified evidence
unless a person explicitly waives that specific shortfall with a reason; stale,
contradicted or violated prerequisites can never be waived.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import ConditionCheck, Observation, ObservedEntity, StateCondition
from backend.app.domain.types import ActionStatus, ConditionState, EventType, PrincipalKind, RiskLevel, Scope, StepStatus
from backend.app.domain.types import EpistemicStatus as S
from backend.app.services.actions import InvalidTransition
from backend.app.services.risk import RiskPolicy
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


# ------------------------------------------------------------------- policy
def _check(state, status, minutes_ago):
    return ConditionCheck(condition=StateCondition(entity_id="v", attribute="state", expected="closed"), state=state,
                          status=status, reason="holds", last_supported_at=at(-minutes_ago))


@pytest.mark.parametrize(
    "risk,state,status,age,expected_state,shortfall",
    [
        (RiskLevel.HIGH, ConditionState.SATISFIED, S.OBSERVED, 120, ConditionState.UNSUPPORTED, True),
        (RiskLevel.HIGH, ConditionState.SATISFIED, S.OBSERVED, 5, ConditionState.SATISFIED, False),
        (RiskLevel.HIGH, ConditionState.SATISFIED, S.VERIFIED, 30, ConditionState.SATISFIED, False),
        (RiskLevel.HIGH, ConditionState.SATISFIED, S.VERIFIED, 120, ConditionState.UNSUPPORTED, True),  # verification ages too
        (RiskLevel.HIGH, ConditionState.VIOLATED, S.OBSERVED, 120, ConditionState.VIOLATED, False),
        (RiskLevel.MEDIUM, ConditionState.SATISFIED, S.OBSERVED, 120, ConditionState.SATISFIED, False),
        (RiskLevel.LOW, ConditionState.SATISFIED, S.OBSERVED, 120, ConditionState.SATISFIED, False),
    ],
)
def test_risk_bar(risk, state, status, age, expected_state, shortfall):
    out = RiskPolicy().apply(_check(state, status, age), risk, at(0))
    assert (out.state, out.risk_shortfall, out.risk) == (expected_state, shortfall, risk)
    if shortfall:
        assert out.reason.startswith("high-risk: needs a re-observation within 10 min or a verification within 60 min")
        assert out.reason.endswith(f"(last {status.value.lower()} 120 min ago)")


def test_ablation_disables_the_bar():
    out = RiskPolicy(enabled=False).apply(_check(ConditionState.SATISFIED, S.OBSERVED, 120), RiskLevel.HIGH, at(0))
    assert out.state == ConditionState.SATISFIED


# -------------------------------------------------------------------- tasks
def _world(repo, risk=RiskLevel.HIGH):
    svc = OrbitServices.build(repo)
    svc.n = 0

    def see(minutes, state, source="camera"):
        svc.n += 1
        svc.engine.record_observation(Observation(id=f"o{svc.n}", timestamp=at(minutes), source=source, observed_entities=[
            ObservedEntity(candidate_entity_id="iso_valve", type="valve", location="bench", attributes={"state": state})]))

    svc.see = see
    see(0, "closed")
    svc.tasks.create_task("Replace coolant line", [
        StepSpec(id="c1", description="close isolation valve",
                 postconditions=[StateCondition(entity_id="iso_valve", attribute="state", expected="closed")]),
        StepSpec(id="c2", description="cut the coolant line", dependencies=["c1"], risk=risk)], at(1), task_id="T3")
    svc.tasks.complete_step("T3", "c1", at(2))
    return svc


def test_risk_is_persisted(repo):
    _world(repo)
    assert [s.risk for s in repo.get_task("T3").steps] == [RiskLevel.MEDIUM, RiskLevel.HIGH]


def test_high_risk_step_waits_for_a_fresh_check(repo):
    svc = _world(repo)
    plan = svc.tasks.resume("T3", at(120))
    assert plan.next_step is None and plan.blocked_steps == ["c2"]
    [req] = plan.requested_observations
    assert (req.entity_id, req.attribute) == ("iso_valve", "state") and req.reason.startswith("high-risk")
    svc.see(125, "closed", source="phone_camera")  # re-checked a moment ago (two cameras agree → VERIFIED)
    assert svc.tasks.resume("T3", at(130)).next_step.step_id == "c2"
    assert svc.tasks.resume("T3", at(190)).next_step is None  # even a verification ages out of the window


def test_verification_lasts_longer_than_observation(repo):
    svc = _world(repo)
    svc.engine.assert_claim("iso_valve", "state", "closed", source="manual_verification", authority=0.95, timestamp=at(80))
    assert svc.tasks.resume("T3", at(120)).next_step.step_id == "c2"  # verified 40 min ago: within 60
    assert svc.tasks.resume("T3", at(150)).next_step is None  # 70 min ago: too old for a HIGH-risk step


def test_medium_risk_only_recommends(repo):
    svc = _world(repo, risk=RiskLevel.MEDIUM)
    plan = svc.tasks.resume("T3", at(120))
    assert plan.next_step.step_id == "c2" and [r.attribute for r in plan.recommended_checks] == ["state"]


def test_planner_prioritises_the_required_check(repo):
    svc = _world(repo)
    [claim] = [c for c in svc.perception.uncertain_claims(at(120)) if c.entity_id == "iso_valve"]
    assert claim.blocking_steps == ["c2"] and claim.decision_critical


# ------------------------------------------------------------------ actions
@pytest.fixture
def svc(repo):
    s = _world(repo)
    s.actions.register_principal("lead", PrincipalKind.HUMAN, [Scope.AUTHORIZE, Scope.ACTUATE], at(0))
    return s


def test_action_inherits_step_prerequisites_and_risk(svc):
    req = svc.actions.propose("cut coolant line", "orbit-agent", at(120), ["iso_valve"], task_id="T3", step_id="c2")
    assert req.risk == RiskLevel.HIGH
    assert [(c.entity_id, c.attribute, c.expected) for c in req.prerequisites] == [("iso_valve", "state", "closed")]
    assert req.status == ActionStatus.PREREQUISITES_FAILED
    assert req.prerequisite_checks[0].risk_shortfall and req.requested_observations[0].entity_id == "iso_valve"


def test_waiver_must_be_explicit_named_and_justified(svc, repo):
    req = svc.actions.propose("cut coolant line", "orbit-agent", at(120), ["iso_valve"], task_id="T3", step_id="c2")
    with pytest.raises(InvalidTransition, match="explicitly waive: iso_valve.state"):
        svc.actions.authorize(req.id, "lead", True, at(121))
    with pytest.raises(InvalidTransition, match="requires a reason"):
        svc.actions.authorize(req.id, "lead", True, at(121), waive=["iso_valve.state"])
    ok = svc.actions.authorize(req.id, "lead", True, at(121), reason="valve is locked out, tag LO-17", waive=["iso_valve.state"])
    assert ok.status == ActionStatus.AUTHORIZED and ok.authorization.waived == ["iso_valve.state"]
    audit = [e for e in repo.list_events() if e.event_type == EventType.ACTION_STATUS_CHANGED][-1]
    assert "waived iso_valve.state: valve is locked out, tag LO-17" in audit.description


def test_fresh_check_removes_the_need_for_a_waiver(svc):
    req = svc.actions.propose("cut coolant line", "orbit-agent", at(120), ["iso_valve"], task_id="T3", step_id="c2")
    svc.see(121, "closed", source="phone_camera")
    svc.actions.recheck(req.id, at(122))
    ok = svc.actions.authorize(req.id, "lead", True, at(123))
    assert ok.status == ActionStatus.AUTHORIZED and ok.authorization.waived == []


def test_waiting_too_long_drops_back_unless_waived(svc):
    svc.see(119, "closed", source="phone_camera")
    req = svc.actions.propose("cut coolant line", "orbit-agent", at(120), ["iso_valve"], task_id="T3", step_id="c2")
    assert req.status == ActionStatus.AWAITING_AUTHORIZATION
    late = svc.actions.authorize(req.id, "lead", True, at(185))  # over an hour later: below the bar again
    assert late.status == ActionStatus.PREREQUISITES_FAILED and late.authorization is None


@pytest.mark.parametrize("what", ["violated", "stale"])
def test_hard_failures_can_never_be_waived(svc, what):
    if what == "violated":
        svc.see(118, "open")
        when = 120
    else:
        when = 60 * 30  # 30 h: the valve state (24 h freshness) is stale, not just old
    req = svc.actions.propose("cut coolant line", "orbit-agent", at(when), ["iso_valve"], task_id="T3", step_id="c2")
    assert req.status == ActionStatus.PREREQUISITES_FAILED and not req.prerequisite_checks[0].risk_shortfall
    with pytest.raises(InvalidTransition, match="cannot authorize: iso_valve.state"):
        svc.actions.authorize(req.id, "lead", True, at(when + 1), reason="trust me", waive=["iso_valve.state"])


# ------------------------------------------------------------- agent + API
def test_safety_question(svc):
    r = svc.agent.answer("Is it safe to cut the coolant line?", at(120))
    assert r.abstained and r.summary.startswith("Not yet for step 2 (cut the coolant line, HIGH risk): ")
    assert r.requested_observation.instruction == "Show me iso_valve so I can check its state."
    svc.see(121, "closed", source="phone_camera")
    r = svc.agent.answer("Is it safe to cut the coolant line?", at(122))
    assert r.answer.startswith("All prerequisites ORBIT tracks for step 2 (cut the coolant line, HIGH risk) are met")
    assert "does not authorise physical actions" in r.summary
    assert svc.agent.answer("Is it safe to juggle chainsaws?", at(122)).abstained


def test_risk_api(client):
    t = lambda m: at(m).isoformat()  # noqa: E731
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "v", "type": "valve", "location": "bench", "attributes": {"state": "closed"}}]})
    client.post("/tasks", json={"id": "T", "goal": "g", "created_at": t(1), "steps": [
        {"id": "a", "description": "close valve", "postconditions": [{"entity_id": "v", "attribute": "state", "expected": "closed"}]},
        {"id": "b", "description": "cut line", "dependencies": ["a"], "risk": "HIGH"}]})
    client.post("/tasks/T/steps/a/complete", json={"at": t(2)})
    client.post("/principals", json={"id": "lead", "kind": "HUMAN", "scopes": ["authorize"]})
    act = client.post("/actions", json={"action": "cut line", "target_entity_ids": ["v"], "task_id": "T", "step_id": "b", "at": t(120)}).json()
    assert act["risk"] == "HIGH" and act["status"] == "PREREQUISITES_FAILED"
    assert client.post(f"/actions/{act['id']}/authorize", json={"principal_id": "lead", "approve": True, "at": t(121)}).status_code == 409
    ok = client.post(f"/actions/{act['id']}/authorize", json={"principal_id": "lead", "approve": True, "at": t(121),
                                                              "reason": "locked out", "waive": ["v.state"]}).json()
    assert ok["status"] == "AUTHORIZED" and ok["authorization"]["waived"] == ["v.state"]
    assert client.get("/tasks/T").json()["steps"][1]["risk"] == "HIGH"


def test_safety_question_is_about_the_step_not_an_ambiguous_object(svc):
    """Regression (found in the live demo): with two valves, "the valve" is ambiguous,
    but a safety question is about a task step and must not stop to ask which one."""
    svc.engine.record_observation(Observation(id="other", timestamp=at(5), source="camera", observed_entities=[
        ObservedEntity(candidate_entity_id="spare_valve", type="valve", location="shelf")]))
    r = svc.agent.answer("Is it safe to cut the coolant line next to the valve?", at(120))
    assert r.summary.startswith("Not yet for step 2 (cut the coolant line, HIGH risk)")
