"""Phase 10 — replay, world projection, counterfactual sandboxes, sensitivity, Experiment H.

Exit criterion (spec §35 Phase 7): stored world states support controlled what-if
experiments.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.container import OrbitServices
from backend.app.domain.models import Observation, ObservedEntity, ObservedRelation, Variation
from backend.app.domain.types import ClaimDisposition, EpistemicStatus as S, EventType, SourceType, StepStatus
from backend.app.domain.types import VariationKind as K
from backend.app.evaluation.bench import VARIANTS, ScenarioRunner
from backend.app.evaluation.counterfactual_eval import INTERRUPTED_AT, at, build_t12_world, run_experiment_h
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.services.counterfactual import SandboxNotFound, SandboxRegistry, apply_variation, compare, sensitivity
from backend.app.services.grading import status_from_supports
from backend.app.services.sandbox import fork
from backend.app.services.world_state_engine import SimulationEvidenceRejected
from experiments.scenarios.catalog import SCENARIOS


def _demo(tmp_path):
    from scripts.seed_demo import seed

    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    return seed(f"sqlite:///{tmp_path / 'demo.db'}", now), now


def _counts(repo):
    return (len(repo.list_entities()), len(repo.list_events()), len(repo.list_evidence()),
            sum(len(repo.get_state_versions_for_entity(e.id)) for e in repo.list_entities()))


# ----------------------------------------------------------------- isolation
def test_real_world_rejects_simulation_evidence(repo):
    svc = OrbitServices.build(repo)
    svc.engine.record_observation(Observation(id="o1", timestamp=at(0), source="camera",
                                              observed_entities=[ObservedEntity(candidate_entity_id="x", type="thing", location="bench")]))
    with pytest.raises(SimulationEvidenceRejected):
        svc.engine.assert_claim("x", "location", "shelf", source="counterfactual", timestamp=at(1))
    with pytest.raises(SimulationEvidenceRejected):
        svc.engine.record_observation(Observation(id="o2", timestamp=at(2), source="camera", source_type=SourceType.SIMULATION))
    assert repo.get_entity("x").current_state["location"] == "bench"


def test_simulation_api_is_forbidden(client):
    t = at(0).isoformat()
    client.post("/observations", json={"id": "o1", "timestamp": t, "source": "camera",
                                        "observed_entities": [{"candidate_entity_id": "x", "type": "thing", "location": "a"}]})
    assert client.post("/claims", json={"entity_id": "x", "attribute": "location", "value": "b", "source": "counterfactual"}).status_code == 403
    assert client.post("/observations", json={"id": "o2", "timestamp": t, "source": "counterfactual", "observed_entities": []}).status_code == 403


# ------------------------------------------------------------------ grading
def test_status_replay_matches_stored_status_on_bench_worlds():
    checked = 0
    for scenario in SCENARIOS:
        runner = ScenarioRunner(scenario, VARIANTS[0])
        runner.run()
        authority = lambda eid: runner.repo.get_evidence(eid).authority  # noqa: E731
        for entity in runner.repo.list_entities():
            for v in runner.repo.get_state_versions_for_entity(entity.id):
                assert status_from_supports(v.support, authority) == v.status, (scenario.id, v.entity_id, v.attribute)
                checked += 1
    assert checked > 40


# --------------------------------------------------------------- projection
def test_projection_is_faithful_at_every_instant(tmp_path):
    src, now = _demo(tmp_path)
    before = _counts(src.repo)
    instants = sorted({e.timestamp for e in src.repo.list_events()}) + [now]
    for T in instants:
        sb = fork(src, T)
        assert sb.services.memory.world_snapshot(T) == src.memory.world_snapshot(T), T
        for task in src.repo.list_tasks():
            if task.created_at <= T:
                assert sb.services.tasks.state_at(task.id, T) == src.tasks.state_at(task.id, T)
        open_src = sorted(c.id for c in src.repo.list_conflicts() if c.opened_at <= T and (c.resolved_at is None or c.resolved_at > T))
        open_sb = sorted(c.id for c in sb.services.repo.list_conflicts() if c.resolved_at is None)
        assert open_src == open_sb
        assert sb.services.agent.answer("Where is m17?", T).summary == src.agent.answer("Where is m17?", T).summary
    assert _counts(src.repo) == before  # the source world is only read


def test_projection_rolls_back_later_knowledge(repo):
    svc = OrbitServices.build(repo)
    see = lambda i, m, **kw: svc.engine.record_observation(Observation(id=i, timestamp=at(m), source="camera", observed_entities=[ObservedEntity(**kw)]))  # noqa: E731
    see("o1", 0, candidate_entity_id="pump", type="pump", location="bench", attributes={"configuration": "R6"})
    svc.engine.assert_claim("pump", "configuration", "R7", source="digital_registry", timestamp=at(5))  # conflict
    see("o2", 10, candidate_entity_id="pump", type="pump", identifiers={"serial_number": "SN-9"})  # identifier learned later
    svc.engine.assert_claim("pump", "configuration", "R7", source="manual_verification", authority=0.95, timestamp=at(20))  # resolves

    sb = fork(svc, at(7))
    pump = sb.services.repo.get_entity("pump")
    assert pump.canonical_attributes == {}  # serial first read at 10 → unknown at 7
    [conflict] = sb.services.repo.list_conflicts()
    assert conflict.resolved_at is None and conflict.resolution_evidence is None  # re-opened
    sides = sb.services.repo.get_state_versions_for_entity("pump", "configuration")
    assert {(v.value, v.disposition, v.valid_to) for v in sides} == {("R6", ClaimDisposition.CONFLICTING, None), ("R7", ClaimDisposition.CONFLICTING, None)}
    assert sb.services.engine.claims.assess_attribute("pump", "configuration", at(7)).status == S.CONTRADICTED
    assert all(e.timestamp <= at(7) for e in sb.services.repo.list_evidence())
    # And the real world still knows everything.
    assert repo.get_entity("pump").canonical_attributes == {"serial_number": "SN-9"}
    assert svc.engine.claims.assess_attribute("pump", "configuration", at(21)).status == S.VERIFIED


def test_projection_rolls_back_task_progress():
    world = build_t12_world()
    world.tasks.resume("T12", at(30))  # resumes (and mutates) the real task after T
    sb = fork(world, at(8))  # after step 5, before step 6
    task = sb.services.repo.get_task("T12")
    assert [s.status for s in task.steps] == [StepStatus.COMPLETED, StepStatus.PENDING, StepStatus.PENDING, StepStatus.PENDING]
    assert task.steps[0].completion_status == S.VERIFIED and task.steps[1].completed_at is None
    assert task.interruptions == [] and task.last_verified_at == at(6)


def test_as_of_reads_do_not_leak_future_evidence(repo):
    """Regression found by the projection fidelity probe: a question about the past must
    not cite evidence (or a status upgrade) that arrived later."""
    svc = OrbitServices.build(repo)
    svc.engine.record_observation(Observation(id="o1", timestamp=at(0), source="camera",
                                              observed_entities=[ObservedEntity(candidate_entity_id="m", type="thing", location="bench")]))
    svc.engine.record_observation(Observation(id="o2", timestamp=at(10), source="phone_camera",
                                              observed_entities=[ObservedEntity(candidate_entity_id="m", type="thing", location="bench")]))
    past, now = svc.engine.claims.assess_attribute("m", "location", at(5)), svc.engine.claims.assess_attribute("m", "location", at(11))
    assert (past.status, len(past.evidence_refs)) == (S.OBSERVED, 1)
    assert (now.status, len(now.evidence_refs)) == (S.VERIFIED, 2)  # independent corroboration happened at 10


# ----------------------------------------------------------------- premises
@pytest.fixture
def sandbox():
    return fork(build_t12_world(), at(INTERRUPTED_AT))


def test_each_premise_kind(sandbox):
    svc = sandbox.services
    apply_variation(sandbox, Variation(kind=K.SET_ATTRIBUTE, entity_id="valve", attribute="state", value="open"))
    apply_variation(sandbox, Variation(kind=K.MOVE, entity_id="pump_new", value="shelf"))
    apply_variation(sandbox, Variation(kind=K.REMOVE, entity_id="pump_old"))
    apply_variation(sandbox, Variation(kind=K.INVALIDATE, entity_id="sop", attribute="procedure_revision"))
    apply_variation(sandbox, Variation(kind=K.SET_RELATION, entity_id="pump_new", relation_type="on", target="cart"))
    now = sandbox.clock.now()
    a = lambda e, attr: svc.engine.claims.assess_attribute(e, attr, now)  # noqa: E731
    assert (a("valve", "state").value, a("valve", "state").status) == ("open", S.VERIFIED)
    assert a("pump_new", "location").value == "shelf"
    assert not a("pump_old", "location").has_current_claim and "confirmed absent" in a("pump_old", "location").reason
    assert a("sop", "procedure_revision").status == S.STALE
    assert [r.target_entity for r in svc.relations.active_relations("pump_new")] == ["cart"]
    assert all(e.provenance.get("counterfactual") for e in svc.repo.list_evidence() if e.source_type == SourceType.SIMULATION)
    assert len(sandbox.variations) == 5

    apply_variation(sandbox, Variation(kind=K.ADVANCE_TIME, minutes=120))
    assert sandbox.clock.now() - now >= timedelta(minutes=120)
    assert a("valve", "state").status == S.VERIFIED  # time passes only for the next read
    assert svc.engine.claims.assess_attribute("valve", "state", sandbox.clock.now()).status == S.VERIFIED


def test_unknown_entity_premise_is_rejected(sandbox):
    from backend.app.services.world_state_engine import EntityNotFoundError

    with pytest.raises(EntityNotFoundError):
        apply_variation(sandbox, Variation(kind=K.SET_ATTRIBUTE, entity_id="ghost", attribute="x", value=1))


# ------------------------------------------------------------------ compare
def test_compare_reports_changed_decisions_and_effects():
    world = build_t12_world()
    before = _counts(world.repo)
    report = compare(world, at(INTERRUPTED_AT), [Variation(kind=K.SET_ATTRIBUTE, entity_id="valve", attribute="state", value="open")],
                     task_ids=["T12"], queries=["Continue."])
    resume = next(d for d in report.decisions if d.kind == "resume")
    assert (resume.baseline["next_step"], resume.counterfactual["next_step"], resume.changed) == ("s7", "s5", True)
    assert resume.counterfactual["invalidated_steps"] == ["s5"]
    query = next(d for d in report.decisions if d.kind == "query")
    assert query.baseline["answer"] == "Next: step 7 — install new pump."
    assert query.counterfactual["answer"] == "Re-verify: step 5 — isolate system."
    assert [(c.change_type, c.entity_id, c.after) for c in report.effects] == [(EventType.OBJECT_STATE_CHANGED, "valve", "open")]
    assert report.decision_changed
    assert _counts(world.repo) == before and world.repo.get_task("T12").steps[0].status == StepStatus.COMPLETED


def test_compare_time_premise_against_static_replay():
    world = build_t12_world()
    report = compare(world, at(INTERRUPTED_AT), [Variation(kind=K.ADVANCE_TIME, minutes=60 * 24 * 40)], task_ids=["T12"])
    assert report.counterfactual_at - report.baseline_at == timedelta(days=40)
    resume = next(d for d in report.decisions if d.kind == "resume")
    # 40 days later the valve state, the removal of the old pump and the procedure revision
    # are stale; the model number is intrinsic and does not expire.
    assert resume.baseline["next_step"] == "s7" and resume.counterfactual["next_step"] is None
    assert resume.counterfactual["requested_observations"] == [
        "Confirm the current revision of sop.", "Show me valve so I can check its state.", "Show me pump_old so I can check its installed."]


def test_no_premise_means_no_change():
    report = compare(build_t12_world(), at(INTERRUPTED_AT), [], task_ids=["T12"], queries=["Where is the valve?"])
    assert not report.decision_changed and report.effects == []


# -------------------------------------------------------------- sensitivity
def test_sensitivity_finds_decision_critical_claims():
    world = build_t12_world()
    report = sensitivity(world, "T12", at(INTERRUPTED_AT))
    assert report.baseline_next == "s7"
    assert set(report.critical) == {"valve.state", "pump_old.installed", "pump_new.model_number", "sop.procedure_revision"}
    flip = {(i.entity_id, i.attribute, i.probe): i.counterfactual_next for i in report.items}
    assert flip[("valve", "state", "violated")] == "s5"
    assert flip[("pump_new", "model_number", "violated")] is None
    # Only the critical claims that are not already VERIFIED are recommended for checking.
    assert {(r.entity_id, r.attribute) for r in report.recommended_checks} == {("valve", "state"), ("pump_old", "installed"), ("sop", "procedure_revision")}
    assert report.items[0].decision_changed


# ------------------------------------------------------------------- replay
def test_replay_frames(tmp_path):
    src, now = _demo(tmp_path)
    frames = src.replay.frames(now - timedelta(hours=4), now)
    assert [f.at for f in frames] == sorted({e.timestamp for e in src.repo.list_events()})
    moved = [c for f in frames for c in f.changes if c.change_type == EventType.OBJECT_MOVED and c.entity_id == "m17"]
    assert [(c.before, c.after) for c in moved] == [("bench_3", "bench_4")]
    only_m17 = src.replay.frames(now - timedelta(hours=4), now, entity_id="m17")
    assert only_m17 and all(e.entity_id == "m17" for f in only_m17 for e in f.events)


# ------------------------------------------------------------- Experiment H
def test_experiment_h_counterfactual_beats_static_replay():
    h = run_experiment_h()
    assert h["ground_truth_agrees_with_orbit_on_return"]
    assert h["decision_quality"] == {"static_replay": 0.2, "counterfactual": 1.0}
    assert h["unsafe_precommitment_rate"] == {"static_replay": 0.8, "counterfactual": 0.0}


# ----------------------------------------------------------------- registry
def test_registry_is_bounded():
    world = build_t12_world()
    reg = SandboxRegistry(max_sandboxes=2)
    a, b, c = (reg.create(world, at(INTERRUPTED_AT)) for _ in range(3))
    assert [s.id for s in reg.list()] == [b.id, c.id]
    with pytest.raises(SandboxNotFound):
        reg.get(a.id)


# ---------------------------------------------------------------------- API
def test_counterfactual_api(client):
    t = lambda m: at(m).isoformat()  # noqa: E731
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "observed_entities": [
        {"candidate_entity_id": "valve", "type": "valve", "location": "bench", "attributes": {"state": "closed"}}]})
    client.post("/tasks", json={"id": "T", "goal": "g", "created_at": t(1), "steps": [
        {"id": "a", "description": "isolate", "postconditions": [{"entity_id": "valve", "attribute": "state", "expected": "closed"}]},
        {"id": "b", "description": "work", "dependencies": ["a"]}]})
    client.post("/tasks/T/steps/a/complete", json={"at": t(2), "source": "manual_verification", "authority": 0.95})

    sb = client.post("/sandboxes", json={"as_of": t(3), "label": "what if"}).json()
    assert sb["forked_from"].startswith("2026-09-30T09:03")
    events = client.post(f"/sandboxes/{sb['id']}/variations", json={"variations": [
        {"kind": "SET_ATTRIBUTE", "entity_id": "valve", "attribute": "state", "value": "open"}]}).json()
    assert events[0]["event_type"] == "OBJECT_STATE_CHANGED"
    assert client.get(f"/sandboxes/{sb['id']}/snapshot").json()["entities"]["valve"]["attributes"]["state"]["value"] == "open"
    assert client.post(f"/sandboxes/{sb['id']}/tasks/T/resume").json()["next_step"]["step_id"] == "a"
    assert "valve" in client.post(f"/sandboxes/{sb['id']}/queries", json={"query": "where is the valve"}).json()["summary"]
    # The real world is unaffected.
    assert client.get("/entities/valve").json()["current_state"]["state"] == "closed"
    assert client.post(f"/sandboxes/{sb['id']}/variations", json={"variations": [{"kind": "MOVE", "entity_id": "ghost", "value": "x"}]}).status_code == 422
    assert len(client.get("/sandboxes").json()) == 1
    assert client.delete(f"/sandboxes/{sb['id']}").status_code == 204
    assert client.get(f"/sandboxes/{sb['id']}").status_code == 404

    cmp = client.post("/counterfactuals/compare", json={"as_of": t(3), "task_ids": ["T"],
                                                        "variations": [{"kind": "INVALIDATE", "entity_id": "valve", "attribute": "state"}]}).json()
    assert cmp["decision_changed"] and cmp["decisions"][0]["counterfactual"]["next_step"] is None
    sens = client.post("/tasks/T/sensitivity", json={"at": t(3)}).json()
    assert sens["baseline_next"] == "b" and sens["critical"] == ["valve.state"]
    assert len(client.get("/replay", params={"start": t(-1), "end": t(5)}).json()) == 3
    assert client.post("/tasks/nope/sensitivity", json={}).status_code == 404
