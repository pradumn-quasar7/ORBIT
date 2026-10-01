"""Phase 7 — active perception (spec §13) and action safety (spec §2.10, §16)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, StateCondition
from backend.app.domain.types import ActionStatus, EventType, ObservationActionType as A, OutcomeResult, PrincipalKind, Scope
from backend.app.domain.types import EpistemicStatus as S
from backend.app.services.actions import AutonomousActuationProhibited, InvalidTransition, PermissionDenied
from backend.app.services.active_perception import FixedPolicy, InformationGainPolicy, RandomPolicy
from backend.app.services.tasks import StepSpec

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
HOUR = 60


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


class Lab:
    def __init__(self, repo):
        self.repo = repo
        self.svc = OrbitServices.build(repo)
        for a, p in (("lab204", None), ("bench_3", "lab204"), ("shelf_a", "lab204"), ("office", None)):
            self.svc.anchors.register(a, T0, parent_id=p, frame={"privacy": "high"} if a == "office" else {})
        self._n = 0

    def see(self, minutes, *entities, source="camera"):
        self._n += 1
        self.svc.engine.record_observation(Observation(id=f"o{self._n}", timestamp=at(minutes), source=source, observed_entities=list(entities)))


def ent(cid, loc=None, type="thing", **kw):
    return ObservedEntity(candidate_entity_id=cid, type=type, location=loc, **kw)


@pytest.fixture
def lab(repo):
    return Lab(repo)


# ---------------------------------------------------------- active perception
def _uncertain_world(lab):
    # Three objects on bench_3 seen two days ago (stale positions), one on the shelf.
    lab.see(0, ent("tool", "bench_3"), ent("cable", "bench_3"), ent("bottle", "bench_3"), ent("m17", "shelf_a", "microscope", attributes={"configuration": "R6"}))
    lab.see(48 * HOUR - 10, ent("m17", "shelf_a", "microscope", attributes={"configuration": "R6"}))  # m17 fresh
    lab.svc.engine.assert_claim("m17", "configuration", "R7", source="digital_registry", timestamp=at(48 * HOUR - 5))


def test_uncertain_claims_are_weighted(lab):
    _uncertain_world(lab)
    claims = lab.svc.perception.uncertain_claims(at(48 * HOUR))
    table = {(c.entity_id, c.attribute): (c.status, c.weight) for c in claims}
    assert table[("m17", "configuration")] == (S.CONTRADICTED, 1.0)
    assert table[("tool", "location")] == (S.STALE, 0.7)
    assert ("m17", "location") not in table  # fresh: nothing to resolve


def test_information_gain_prefers_one_view_that_resolves_many(lab):
    _uncertain_world(lab)
    plan = lab.svc.perception.plan(at(48 * HOUR), InformationGainPolicy())
    best = plan.actions[0]
    assert (best.action_type, best.target) == (A.LOOK_AT_ANCHOR, "bench_3")
    assert sorted(best.resolves) == ["bottle.location", "cable.location", "tool.location"]
    assert best.instruction == "Point the camera at bench_3 so I can refresh 3 item(s)."
    # Marginal accounting: a plan can never remove more uncertainty than exists.
    assert sum(a.expected_uncertainty_reduction for a in plan.actions) <= plan.total_uncertainty
    # The contradiction gets a hands-on verification candidate.
    assert any(a.action_type == A.VERIFY_WITH_PERSON and a.target == "m17" for a in lab.svc.perception.candidates(plan.uncertain_claims))


def test_experiment_f_policy_comparison(lab):
    """Expected uncertainty removed by the first observation, per policy."""
    _uncertain_world(lab)
    now = at(48 * HOUR)
    first = lambda policy: lab.svc.perception.plan(now, policy, k=1).actions[0].expected_uncertainty_reduction
    info, fixed = first(InformationGainPolicy()), first(FixedPolicy())
    randoms = [first(RandomPolicy(seed)) for seed in range(10)]
    assert info > fixed
    assert info >= max(randoms) and info > sum(randoms) / len(randoms)
    assert first(RandomPolicy(3)) == first(RandomPolicy(3))  # seeded ⇒ reproducible
    removed = lambda policy: sum(a.expected_uncertainty_reduction for a in lab.svc.perception.plan(now, policy, k=3).actions)
    assert removed(InformationGainPolicy()) > removed(FixedPolicy())
    assert removed(InformationGainPolicy()) >= max(removed(RandomPolicy(s)) for s in range(10)) - 1e-9


def test_lost_object_gets_a_coverage_search(lab):
    lab.see(0, ent("cable", "bench_3"))
    lab.svc.search.record_search("bench_3", at(10), ["cable"])
    plan = lab.svc.perception.plan(at(11))
    search = next(a for a in plan.actions if a.action_type == A.SEARCH_REGION)
    assert search.target == "lab204" and search.instruction == "Search lab204 for cable."


def test_privacy_raises_observation_cost(lab):
    lab.see(0, ent("laptop", "office"), ent("phone", "bench_3"))
    plan = lab.svc.perception.plan(at(48 * HOUR), k=10)
    office = next(a for a in plan.actions if a.target == "office" and a.action_type == A.LOOK_AT_ANCHOR)
    bench = next(a for a in plan.actions if a.target == "bench_3" and a.action_type == A.LOOK_AT_ANCHOR)
    assert office.cost.privacy > bench.cost.privacy and office.score < bench.score


def test_task_blocking_claims_count_double(lab):
    lab.see(0, ent("pump_new", "shelf_a", "pump", attributes={"model_number": "CP-200"}))
    lab.svc.tasks.create_task("install", [StepSpec(id="s7", description="install new pump", preconditions=[
        StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200", min_status=S.VERIFIED)])], at(1), task_id="T")
    [claim] = [c for c in lab.svc.perception.uncertain_claims(at(2)) if c.attribute == "model_number"]
    assert claim.status == S.OBSERVED and claim.weight == 1.0 and claim.blocking_steps == ["s7"]
    best = lab.svc.perception.plan(at(2)).actions[0]
    assert (best.action_type, best.target, best.attribute) == (A.INSPECT_ENTITY, "pump_new", "model_number")


def test_agent_requests_are_ranked(lab):
    _uncertain_world(lab)
    r = lab.svc.agent.answer("what's on bench 3", at(48 * HOUR))
    assert r.requested_observations and all(q.score is not None for q in r.requested_observations)


# --------------------------------------------------------------- action safety
@pytest.fixture
def safety(lab):
    lab.see(0, ent("m17", "bench_3", "microscope", attributes={"power": "off"}), ent("c4", "bench_3", "cable", attributes={"plugged_into": None}))
    a = lab.svc.actions
    a.register_principal("tech_ana", PrincipalKind.HUMAN, [Scope.OBSERVE, Scope.AUTHORIZE, Scope.ACTUATE], at(0))
    a.register_principal("intern", PrincipalKind.HUMAN, [Scope.OBSERVE, Scope.ACTUATE], at(0))
    return a


def _propose(safety, minutes=1, consequential=True):
    return safety.propose(
        "connect cable C4 to M17", "orbit-agent", at(minutes), ["m17", "c4"],
        prerequisites=[StateCondition(entity_id="m17", attribute="power", expected="off")],
        expected_outcome=[StateCondition(entity_id="c4", attribute="plugged_into", expected="m17")],
        consequential=consequential,
    )


def test_agent_can_recommend_but_never_authorize_or_actuate(lab, safety):
    agent = lab.repo.get_principal("orbit-agent")
    assert set(agent.scopes) == {Scope.OBSERVE, Scope.REASON, Scope.RECOMMEND}
    with pytest.raises(PermissionDenied):
        safety.register_principal("rogue-bot", PrincipalKind.AGENT, [Scope.AUTHORIZE], at(0))
    req = _propose(safety)
    with pytest.raises(PermissionDenied, match="authorize"):
        safety.authorize(req.id, "orbit-agent", True, at(2))
    with pytest.raises(PermissionDenied, match="authorize"):
        safety.authorize(req.id, "intern", True, at(2))
    with pytest.raises(AutonomousActuationProhibited):
        safety.execute_autonomously(req.id)


def test_full_action_lifecycle_with_outcome_memory(lab, safety, repo):
    req = _propose(safety)
    assert req.status == ActionStatus.AWAITING_AUTHORIZATION and req.prerequisite_checks[0].state.value == "SATISFIED"
    with pytest.raises(InvalidTransition):
        safety.report_performed(req.id, "tech_ana", at(2))  # not authorized yet
    req = safety.authorize(req.id, "tech_ana", True, at(2), reason="bench isolated")
    assert req.status == ActionStatus.AUTHORIZED
    with pytest.raises(PermissionDenied):
        safety.report_performed(req.id, "orbit-agent", at(3))
    req = safety.report_performed(req.id, "tech_ana", at(3), notes="plugged in")

    # Only pre-action evidence exists → outcome cannot be verified yet.
    req = safety.verify_outcome(req.id, at(3, ) + timedelta(seconds=30))
    assert req.status == ActionStatus.OUTCOME_UNVERIFIED
    assert req.requested_observations[0].entity_id == "c4"

    lab.see(4, ent("c4", "bench_3", "cable", attributes={"plugged_into": "m17"}))
    req = safety.verify_outcome(req.id, at(5))
    assert req.status == ActionStatus.OUTCOME_VERIFIED
    outcomes = repo.list_outcomes(req.id)
    assert [o.result for o in outcomes] == [OutcomeResult.UNVERIFIED, OutcomeResult.VERIFIED]
    assert outcomes[-1].conditions[0].condition.attribute == "power" and outcomes[-1].performed_by == "tech_ana"

    audit = [e.after_state["status"] for e in repo.list_events() if e.event_type == EventType.ACTION_STATUS_CHANGED]
    assert audit == ["AWAITING_AUTHORIZATION", "AUTHORIZED", "PERFORMED", "OUTCOME_UNVERIFIED", "OUTCOME_VERIFIED"]


def test_unsupported_prerequisites_block_and_can_be_rechecked(lab, safety):
    req = _propose(safety, minutes=30)  # m17.power (5 min TTL) is stale by now
    assert req.status == ActionStatus.PREREQUISITES_FAILED
    assert req.requested_observations[0].instruction.startswith("Show the power indicator")
    with pytest.raises(InvalidTransition):
        safety.authorize(req.id, "tech_ana", True, at(31))
    lab.see(32, ent("m17", "bench_3", "microscope", attributes={"power": "off"}))
    assert safety.recheck(req.id, at(33)).status == ActionStatus.AWAITING_AUTHORIZATION


def test_prerequisites_reverified_at_authorization(lab, safety):
    req = _propose(safety, minutes=1)
    req = safety.authorize(req.id, "tech_ana", True, at(30))  # power went stale while waiting
    assert req.status == ActionStatus.PREREQUISITES_FAILED


def test_violated_prerequisite(lab, safety):
    lab.see(1, ent("m17", "bench_3", "microscope", attributes={"power": "on"}))
    req = _propose(safety, minutes=2)
    assert req.status == ActionStatus.PREREQUISITES_FAILED and req.requested_observations == []
    assert req.prerequisite_checks[0].state.value == "VIOLATED"


def test_denied_and_failed_outcomes(lab, safety, repo):
    denied = safety.authorize(_propose(safety).id, "tech_ana", False, at(2), reason="not today")
    assert denied.status == ActionStatus.DENIED
    with pytest.raises(InvalidTransition):
        safety.report_performed(denied.id, "tech_ana", at(3))

    req = safety.authorize(_propose(safety, minutes=2).id, "tech_ana", True, at(3))
    safety.report_performed(req.id, "intern", at(4))
    lab.see(5, ent("c4", "bench_3", "cable", attributes={"plugged_into": "laptop"}))
    req = safety.verify_outcome(req.id, at(6))
    assert req.status == ActionStatus.OUTCOME_FAILED
    assert repo.list_outcomes(req.id)[-1].result == OutcomeResult.FAILED


def test_entity_scoped_authority(lab, safety):
    safety.register_principal("bench_lead", PrincipalKind.HUMAN, [Scope.AUTHORIZE], at(0), entity_scope=["m17"])
    with pytest.raises(PermissionDenied, match="not permitted"):
        safety.authorize(_propose(safety).id, "bench_lead", True, at(2))


def test_non_consequential_action_still_needs_a_person(lab, safety):
    req = _propose(safety, consequential=False)
    assert req.status == ActionStatus.AUTHORIZED and req.authorization is None
    with pytest.raises(PermissionDenied):
        safety.report_performed(req.id, "orbit-agent", at(2))


# ---------------------------------------------------------------------- API
def test_perception_and_action_api(client):
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "m17", "type": "microscope", "location": "bench_3", "attributes": {"power": "off"}}]})
    plan = client.post("/active-perception/plan", json={"at": t(60), "policy": "information_gain"}).json()
    assert plan["actions"][0]["resolves"] == ["m17.power"]
    assert client.post("/active-perception/plan", json={"policy": "telepathy"}).status_code == 422

    assert client.post("/principals", json={"id": "ana", "kind": "HUMAN", "scopes": ["authorize", "actuate"]}).status_code == 201
    assert client.post("/principals", json={"id": "bot", "kind": "AGENT", "scopes": ["actuate"]}).status_code == 403
    act = client.post("/actions", json={"action": "power on m17", "target_entity_ids": ["m17"], "at": t(1),
                                        "prerequisites": [{"entity_id": "m17", "attribute": "power", "expected": "off"}],
                                        "expected_outcome": [{"entity_id": "m17", "attribute": "power", "expected": "on"}]}).json()
    assert act["status"] == "AWAITING_AUTHORIZATION"
    assert client.post(f"/actions/{act['id']}/execute").status_code == 403
    assert client.post(f"/actions/{act['id']}/authorize", json={"principal_id": "orbit-agent", "approve": True}).status_code == 403
    ok = client.post(f"/actions/{act['id']}/authorize", json={"principal_id": "ana", "approve": True, "at": t(2)}).json()
    assert ok["status"] == "AUTHORIZED"
    client.post(f"/actions/{act['id']}/performed", json={"principal_id": "ana", "at": t(3)})
    client.post("/observations", json={"id": "o2", "timestamp": t(4), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "m17", "type": "microscope", "attributes": {"power": "on"}}]})
    done = client.post(f"/actions/{act['id']}/verify-outcome", json={"at": t(4)}).json()
    assert done["status"] == "OUTCOME_VERIFIED"
    assert client.get("/outcomes").json()[0]["result"] == "VERIFIED"
    assert client.get("/actions/nope").status_code == 404
