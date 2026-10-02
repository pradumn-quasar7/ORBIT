"""Phase 11 — decision-aware active perception (value of information).

Exit criterion: before a consequential step, ORBIT asks to check the facts that step
rests on — even when they are fresh — and this measurably averts unsafe continuation.
"""
import pytest

from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import ObservationActionType as A
from backend.app.evaluation.counterfactual_eval import INTERRUPTED_AT, at, build_t12_world
from backend.app.evaluation.decision_perception_eval import DECIDE_AT, build_world, run_experiment_f2
from backend.app.services.active_perception import DecisionAwarePolicy, InformationGainPolicy
from backend.app.services.counterfactual import sensitivity


def _state(repo):
    return (len(repo.list_events()), [(t.status, [s.status for s in t.steps], [i.resumed_at for i in t.interruptions]) for t in repo.list_tasks()])


# ------------------------------------------------------------------ preview
def test_preview_changes_nothing_on_either_backend(repo):
    world = build_t12_world(repo)
    before = _state(repo)
    plan = world.tasks.preview("T12", at(INTERRUPTED_AT))
    assert plan.next_step.step_id == "s7"
    assert _state(repo) == before


def test_preview_refuses_inside_a_transaction():
    world = build_t12_world()
    with pytest.raises(RuntimeError):
        with world.repo.transaction():
            world.tasks.preview("T12", at(INTERRUPTED_AT))


# --------------------------------------------------------- decision-critical
def test_resume_lists_decision_critical_facts_and_checks():
    plan = build_t12_world().tasks.preview("T12", at(INTERRUPTED_AT))
    critical = {(c.condition.entity_id, c.condition.attribute): c.status for c in plan.decision_critical}
    assert critical == {("pump_new", "model_number"): S.VERIFIED, ("valve", "state"): S.OBSERVED,
                        ("pump_old", "installed"): S.OBSERVED, ("sop", "procedure_revision"): S.OBSERVED}
    checks = {(r.entity_id, r.attribute) for r in plan.recommended_checks}
    assert checks == {("valve", "state"), ("pump_old", "installed"), ("sop", "procedure_revision")}  # VERIFIED excluded
    assert all(r.for_steps == ["s7"] and "not verified" in r.reason for r in plan.recommended_checks)


def test_structural_criticality_matches_sandbox_sensitivity():
    world = build_t12_world()
    structural = {f"{c.condition.entity_id}.{c.condition.attribute}" for c in world.tasks.preview("T12", at(INTERRUPTED_AT)).decision_critical}
    assert structural == set(sensitivity(world, "T12", at(INTERRUPTED_AT)).critical)


# ------------------------------------------------------------------ planner
def test_decision_weights():
    world = build_world()
    claims = {(c.entity_id, c.attribute): c for c in world.perception.uncertain_claims(at(DECIDE_AT), include_decision_critical=True)}
    valve = claims[("valve", "state")]
    assert (valve.status, valve.weight, valve.decision_weight, valve.critical_for_steps) == (S.OBSERVED, 0.0, 1.2, ["s7"])
    clutter = claims[("part_0", "location")]
    assert (clutter.status, clutter.weight, clutter.decision_weight, clutter.decision_critical) == (S.STALE, 0.7, 0.175, False)
    plain = {(c.entity_id, c.attribute) for c in world.perception.uncertain_claims(at(DECIDE_AT))}
    assert ("valve", "state") not in plain  # without decision awareness a fresh fact is invisible


def test_decision_aware_looks_where_the_next_step_depends():
    world = build_world()
    da = world.perception.plan(at(DECIDE_AT), DecisionAwarePolicy(), k=2)
    ig = world.perception.plan(at(DECIDE_AT), InformationGainPolicy(), k=2)
    assert da.weighting == "decision_value" and ig.weighting == "uncertainty"
    assert (da.actions[0].action_type, da.actions[0].target) == (A.LOOK_AT_ANCHOR, "bench")
    assert {"valve.state", "pump_old.installed"} <= set(da.actions[0].resolves)
    assert (ig.actions[0].action_type, ig.actions[0].target) == (A.LOOK_AT_ANCHOR, "shelf")
    assert not any("valve.state" in a.resolves for a in ig.actions)


# --------------------------------------------------------------- Experiment F2
def test_experiment_f2_decision_awareness_averts_unsafe_continuation():
    r = run_experiment_f2()["summary"]
    for k in (1, 2, 3):
        assert r["decision_aware"][k]["unsafe_continuation"] == 0.0
        assert r["information_gain"][k]["unsafe_continuation"] == 1.0
        assert all(r[p][k]["safe_work_kept"] == 1.0 for p in r)  # caution never blocked safe work
    # The honest trade-off: one look spent on the decision costs general awareness at k=1…
    assert r["information_gain"][1]["stale_refreshed"] > r["decision_aware"][1]["stale_refreshed"]
    # …which decision-awareness recovers by the second look.
    assert r["decision_aware"][2]["stale_refreshed"] == r["information_gain"][2]["stale_refreshed"]


# --------------------------------------------------------- user-facing paths
def test_continue_adds_a_pre_check_without_withholding_the_answer():
    world = build_t12_world()
    r = world.agent.answer("Continue.", at(INTERRUPTED_AT))
    assert r.answer == "Next: step 7 — install new pump."
    assert "Before you start, confirm: Show me valve so I can check its state." in r.summary
    assert {(q.entity_id, q.attribute) for q in r.requested_observations} == {("valve", "state"), ("pump_old", "installed"), ("sop", "procedure_revision")}


def test_api_preview_policy_and_dashboard(client):
    t = lambda m: at(m).isoformat()  # noqa: E731
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "valve", "type": "valve", "location": "bench", "attributes": {"state": "closed"}}]})
    client.post("/tasks", json={"id": "T", "goal": "g", "created_at": t(1), "steps": [
        {"id": "a", "description": "isolate", "postconditions": [{"entity_id": "valve", "attribute": "state", "expected": "closed"}]},
        {"id": "b", "description": "work", "dependencies": ["a"]}]})
    client.post("/tasks/T/steps/a/complete", json={"at": t(2)})
    events_before = len(client.get("/events").json())
    preview = client.post("/tasks/T/preview", json={"at": t(3)}).json()
    assert preview["next_step"]["step_id"] == "b" and preview["recommended_checks"][0]["entity_id"] == "valve"
    assert len(client.get("/events").json()) == events_before
    plan = client.post("/active-perception/plan", json={"at": t(3), "policy": "decision_aware"}).json()
    assert plan["weighting"] == "decision_value" and plan["actions"][0]["resolves"] == ["valve.state"]
    summary = client.get("/inspect/summary", params={"as_of": t(3)}).json()
    assert summary["counts"]["decision_critical_checks"] == 1
    assert summary["requested_observations"][0]["decision_critical"] is True
