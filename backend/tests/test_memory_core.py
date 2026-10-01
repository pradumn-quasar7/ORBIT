"""Phase 3 — memory core: temporal, spatial, episodic, procedural, causal-hypothesis.

Exit criterion: history and task state are queryable.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.models import Observation, ObservedEntity, ObservedRelation, StateCondition
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType, HypothesisStatus, SourceType, StepStatus, TaskStatus
from backend.app.services.hypotheses import CausalOrderError, HypothesisService
from backend.app.services.memory import MemoryService
from backend.app.services.tasks import StepNotReadyError, StepSpec, TaskError, TaskService
from backend.app.services.world_state_engine import WorldStateEngine

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def engine(repo):
    eng = WorldStateEngine(repo)
    eng.anchors.register("lab204", T0, anchor_type="room")
    eng.anchors.register("bench_3", T0, parent_id="lab204")
    eng.anchors.register("bench_3_left", T0, parent_id="bench_3")
    eng.anchors.register("shelf_a", T0, parent_id="lab204")
    return eng


@pytest.fixture
def memory(engine, repo):
    return MemoryService(repo, engine.claims, engine.relations, engine.anchors)


@pytest.fixture
def tasks(engine, repo):
    return TaskService(repo, engine)


_n = {"i": 0}


def observe(engine, minutes, *entities, session=None, source="camera"):
    _n["i"] += 1
    return engine.record_observation(
        Observation(id=f"o{_n['i']}", timestamp=at(minutes), source=source, session_id=session, observed_entities=list(entities))
    )


def ent(cid, loc=None, type="thing", **kw):
    return ObservedEntity(candidate_entity_id=cid, type=type, location=loc, **kw)


# ------------------------------------------------------------------ temporal
def test_world_snapshot_as_of(engine, memory):
    observe(engine, 0, ent("bottle", "bench_3_left", attributes={"power": "n/a"}))
    observe(engine, 30, ent("bottle", "shelf_a"), ent("laptop", "bench_3"))

    past = memory.world_snapshot(at(10))
    assert set(past.entities) == {"bottle"}  # laptop did not exist yet
    assert past.entities["bottle"].attributes["location"].value == "bench_3_left"

    now = memory.world_snapshot(at(31))
    assert now.entities["bottle"].attributes["location"].value == "shelf_a"
    assert now.entities["laptop"].attributes["location"].status == S.OBSERVED


def test_snapshot_preserves_epistemic_state_at_that_time(engine, memory):
    observe(engine, 0, ent("pump", "bench_3", attributes={"power": "on"}), source="sensor")
    engine.assert_claim("pump", "configuration", "R6", source="digital_registry", timestamp=at(1))
    observe(engine, 2, ent("pump", attributes={"configuration": "R7"}))  # contradiction
    engine.assert_claim("pump", "configuration", "R7", source="manual_verification", timestamp=at(20), authority=0.95)

    during = memory.world_snapshot(at(3)).entities["pump"]
    assert during.attributes["configuration"].status == S.CONTRADICTED
    assert during.attributes["power"].status == S.OBSERVED
    after = memory.world_snapshot(at(21)).entities["pump"]
    assert after.attributes["configuration"].status == S.VERIFIED
    assert after.attributes["power"].status == S.STALE  # 21 min > 5 min TTL


def test_timeline_and_attribute_history(engine, memory, repo):
    observe(engine, 0, ent("bottle", "bench_3_left"))
    observe(engine, 10, ent("bottle", "shelf_a"))
    observe(engine, 20, ent("bottle", "bench_3_left"))
    assert [e.event_type for e in memory.timeline("bottle")] == [EventType.OBJECT_ADDED, EventType.OBJECT_MOVED, EventType.OBJECT_MOVED]
    assert len(memory.timeline("bottle", start=at(5), end=at(15))) == 1
    hist = repo.get_state_versions_for_entity("bottle", "location")
    assert [(v.value, v.valid_from, v.valid_to) for v in hist] == [
        ("bench_3_left", at(0), at(10)),
        ("shelf_a", at(10), at(20)),
        ("bench_3_left", at(20), None),
    ]
    # "Where was the bottle at 09:15?"
    assert engine.claims.assess_attribute("bottle", "location", at(15)).value == "shelf_a"


# ------------------------------------------------------------------- spatial
def test_locate_with_lineage_and_relations(engine, memory):
    observe(engine, 0, ent("tray", "bench_3_left"), ent("tool", "bench_3_left", relations=[ObservedRelation(relation_type="on", target="tray")]))
    answer = memory.locate("tool", at(1))
    assert answer.location.value == "bench_3_left"
    assert answer.anchor_lineage == ["bench_3_left", "bench_3", "lab204"]
    assert [r.target_entity for r in answer.supported_by_relations] == ["tray"]


def test_anchor_contents_nested_and_status_aware(engine, memory):
    observe(engine, 0, ent("m17", "bench_3_left"), ent("cable", "bench_3"), ent("bottle", "shelf_a"))
    observe(engine, 0, ent("probe", relations=[ObservedRelation(relation_type="inside", target="m17")]))
    contents = memory.contents("bench_3", at(1))
    assert [(c.entity_id, c.via) for c in contents] == [("cable", "location"), ("m17", "location"), ("probe", "relation:inside")]
    assert [c.entity_id for c in memory.contents("bench_3", at(1), nested=False)] == ["cable"]
    # Two days later the remembered positions are stale and say so.
    stale = memory.contents("bench_3", at(60 * 48))
    assert {c.assessment.status for c in stale if c.via == "location"} == {S.STALE}


# ------------------------------------------------------------------ episodic
def test_session_summary_and_previous_session(engine, memory, repo):
    observe(engine, 0, ent("bottle", "bench_3_left"), ent("cable", "bench_3"), session="A")
    observe(engine, 5, ent("bottle", "shelf_a"), session="A")
    observe(engine, 120, ent("bottle", "bench_3_left"), session="B")
    summary = memory.session_summary("A")
    assert summary.observation_ids == [o.id for o in repo.list_observations()[:2]]
    assert summary.entities_observed == ["bottle", "cable"]
    assert summary.event_counts == {"OBJECT_ADDED": 2, "OBJECT_MOVED": 1}
    assert memory.previous_session(at(120)).id == "A"
    assert memory.session_end(repo.get_session("A")) == at(5)
    assert memory.session_summary("missing") is None


# ---------------------------------------------------------------- procedural
def _replace_pump(tasks):
    return tasks.create_task(
        "Replace coolant pump",
        [
            StepSpec(id="s5", description="isolate system",
                     postconditions=[StateCondition(entity_id="valve", attribute="state", expected="closed")]),
            StepSpec(id="s6", description="remove old pump", dependencies=["s5"]),
            StepSpec(id="s7", description="install new pump", dependencies=["s6"],
                     preconditions=[StateCondition(entity_id="pump_new", attribute="model_number", expected="CP-200", min_status=S.VERIFIED)]),
            StepSpec(id="s8", description="leak test", dependencies=["s7"]),
        ],
        at(0),
        task_id="T12",
    )


def test_task_graph_validation(tasks):
    with pytest.raises(TaskError, match="unknown"):
        tasks.create_task("x", [StepSpec(id="a", description="a", dependencies=["zzz"])], at(0))
    with pytest.raises(TaskError, match="cycle"):
        tasks.create_task("x", [StepSpec(id="a", description="a", dependencies=["b"]), StepSpec(id="b", description="b", dependencies=["a"])], at(0))
    _replace_pump(tasks)
    with pytest.raises(TaskError, match="exists"):
        _replace_pump(tasks)


def test_task_progress_is_evidence_backed_and_replayable(tasks, repo):
    """Phase 3 exit: task state is persisted and queryable at any past instant."""
    task = _replace_pump(tasks)
    assert [s.status for s in task.steps] == [StepStatus.PENDING] * 4
    with pytest.raises(StepNotReadyError):
        tasks.start_step("T12", "s6", at(1))

    tasks.start_step("T12", "s5", at(1), actor="ana")
    tasks.complete_step("T12", "s5", at(5), source="manual_verification", authority=0.95, actor="ana")
    tasks.complete_step("T12", "s6", at(10), source="user", actor="ana", note="old pump out")
    task = tasks.interrupt_task("T12", at(12), reason="shift change", actor="ana")

    s5, s6 = task.steps[0], task.steps[1]
    assert (s5.status, s5.completion_status) == (StepStatus.COMPLETED, S.VERIFIED)
    assert (s6.status, s6.completion_status) == (StepStatus.COMPLETED, S.OBSERVED)  # a person's word ≠ verified
    assert task.last_verified_at == at(5)
    assert task.status == TaskStatus.INTERRUPTED and task.interruptions[0].reason == "shift change"
    assert repo.get_evidence(s6.evidence_refs[0]).content["note"] == "old pump out"

    view = tasks.state_at("T12", at(7))
    assert view.status == TaskStatus.IN_PROGRESS
    assert view.steps == {"s5": StepStatus.COMPLETED, "s6": StepStatus.PENDING, "s7": StepStatus.PENDING, "s8": StepStatus.PENDING}
    assert tasks.state_at("T12", at(13)).status == TaskStatus.INTERRUPTED
    assert tasks.state_at("T12", at(-1)) is None
    assert all(e.event_type == EventType.TASK_PROGRESS_CHANGED for e in tasks.history("T12"))

    # Persisted with structure intact.
    loaded = repo.get_task("T12")
    assert loaded.steps[2].preconditions[0].min_status == S.VERIFIED
    assert loaded.steps[0].postconditions[0].expected == "closed"


def test_completing_all_steps_completes_task(tasks):
    tasks.create_task("tiny", [StepSpec(id="a", description="a"), StepSpec(id="b", description="b", dependencies=["a"])], at(0), task_id="t")
    tasks.complete_step("t", "a", at(1))
    assert tasks.complete_step("t", "b", at(2)).status == TaskStatus.COMPLETED
    with pytest.raises(TaskError):
        tasks.interrupt_task("t", at(3))


# ------------------------------------------------------------------ causal
def _firmware_then_failure(engine):
    observe(engine, 0, ent("motor", "bench_3", attributes={"firmware": "1.0", "health": "ok"}))
    e_fw = observe(engine, 10, ent("motor", attributes={"firmware": "1.1"}))[0]
    e_fail = observe(engine, 40, ent("motor", attributes={"health": "failed"}))[0]
    return e_fw, e_fail


def test_causal_hypothesis_is_never_a_fact_without_causal_evidence(engine, repo):
    hyps = HypothesisService(repo, engine)
    e_fw, e_fail = _firmware_then_failure(engine)
    h = hyps.propose("firmware 1.1 may explain motor failure", at(41), e_fw.id, e_fail.id)
    assert (h.status, h.epistemic_status) == (HypothesisStatus.HYPOTHESIS, S.INFERRED)
    assert h.entity_ids == ["motor"] and len(h.evidence_refs) == 2  # co-occurrence kept as context

    with pytest.raises(CausalOrderError):
        hyps.propose("failure caused the firmware change", at(41), e_fail.id, e_fw.id)

    # More co-occurrence and a confident person do not make it a fact.
    for source, kind in (("camera", "observation"), ("user", "causal_test")):
        h, changed, _ = hyps.add_evidence(h.id, at(50), source, supports=True, description="looks related", kind=kind)
        assert not changed and h.status == HypothesisStatus.HYPOTHESIS

    h, changed, reason = hyps.add_evidence(
        h.id, at(60), "diagnostic_rig", supports=True, kind="causal_test",
        description="rollback to 1.0 restores motor; 1.1 reproduces failure", source_type=SourceType.TOOL_OUTPUT, authority=0.95,
    )
    assert changed and h.status == HypothesisStatus.SUPPORTED and h.epistemic_status == S.VERIFIED
    assert [x.id for x in hyps.for_entity("motor")] == [h.id]


# ---------------------------------------------------------------------- API
def test_memory_api(client):
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()
    client.post("/anchors", json={"id": "bench_3"})
    client.post("/observations", json={"id": "o1", "timestamp": t(0), "source": "camera", "session_id": "A",
                                        "observed_entities": [{"candidate_entity_id": "m17", "type": "microscope", "location": "bench_3"}]})
    client.post("/observations", json={"id": "o2", "timestamp": t(10), "source": "camera", "session_id": "A",
                                        "observed_entities": [{"candidate_entity_id": "m17", "type": "microscope", "location": "shelf"}]})
    snap = client.get("/world/snapshot", params={"as_of": t(5)}).json()
    assert snap["entities"]["m17"]["attributes"]["location"]["value"] == "bench_3"
    assert [c["entity_id"] for c in client.get("/anchors/bench_3/contents", params={"as_of": t(5)}).json()] == ["m17"]
    assert client.get("/entities/m17/location", params={"as_of": t(11)}).json()["location"]["value"] == "shelf"
    assert len(client.get("/entities/m17/timeline").json()) == 2
    assert client.get("/sessions/A/summary").json()["event_counts"] == {"OBJECT_ADDED": 1, "OBJECT_MOVED": 1}

    body = {"id": "T1", "goal": "demo", "created_at": t(0), "steps": [{"id": "a", "description": "first"}, {"id": "b", "description": "second", "dependencies": ["a"]}]}
    assert client.post("/tasks", json=body).status_code == 201
    assert client.post("/tasks/T1/steps/b/start", json={"at": t(1)}).status_code == 409
    done = client.post("/tasks/T1/steps/a/complete", json={"at": t(2), "source": "manual_verification", "authority": 0.95}).json()
    assert done["steps"][0]["completion_status"] == "VERIFIED"
    assert client.get("/tasks/T1/state", params={"as_of": t(1)}).json()["steps"]["a"] == "PENDING"
    assert client.post("/tasks/T1/interrupt", json={"at": t(3), "reason": "lunch"}).json()["status"] == "INTERRUPTED"
    assert client.get("/tasks/missing").status_code == 404

    events = client.get("/events").json()
    hyp = client.post("/hypotheses", json={"statement": "move caused x", "cause_event_id": events[1]["id"]}).json()
    res = client.post(f"/hypotheses/{hyp['id']}/evidence", json={"source": "camera", "supports": True, "description": "seen"}).json()
    assert res["status_changed"] is False and res["hypothesis"]["status"] == "HYPOTHESIS"


def test_migration_0004_splits_step_status(tmp_path):
    import sqlalchemy as sa
    from alembic import command

    from database.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0003")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO task_steps (id, seq, task_id, step_order, description, status, dependencies, preconditions, evidence_refs) "
                          "VALUES ('s1', 1, 't', 1, 'd', 'VERIFIED', '[]', '{}', '[]'), ('s2', 2, 't', 2, 'd', 'UNKNOWN', '[]', '{}', '[]')"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        rows = c.execute(sa.text("SELECT id, status, completion_status, preconditions FROM task_steps ORDER BY id")).fetchall()
    assert [tuple(r) for r in rows] == [("s1", "COMPLETED", "VERIFIED", "[]"), ("s2", "PENDING", "UNKNOWN", "[]")]
