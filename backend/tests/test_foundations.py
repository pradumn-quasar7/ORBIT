"""Phase 0 foundations: time, status classification, storage parity, migrations."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.app.domain.models import Observation, ObservedEntity, Task, TaskStep
from backend.app.domain.status import aggregate_status, classify_source_status
from backend.app.domain.types import EpistemicStatus as S
from backend.app.domain.types import EventType
from backend.app.repositories.sql import metadata
from backend.app.services.world_state_engine import DuplicateObservationError, WorldStateEngine

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def _obs(obs_id, ts, entities, source="camera", authority=1.0):
    return Observation(id=obs_id, timestamp=ts, source=source, authority=authority, observed_entities=entities)


def test_naive_and_offset_timestamps_normalise_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    a = Observation(timestamp=datetime(2026, 9, 30, 15, 30, tzinfo=ist), source="camera")
    b = Observation(timestamp=datetime(2026, 9, 30, 10, 0), source="camera")
    assert a.timestamp == b.timestamp
    assert a.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "source,authority,expected",
    [
        ("camera", 1.0, S.OBSERVED),  # Phase 0 bug: was VERIFIED
        ("digital_registry", 1.0, S.VERIFIED),
        ("digital_registry", 0.5, S.OBSERVED),
        ("manual_verification", 0.95, S.VERIFIED),
    ],
)
def test_source_status_classification(source, authority, expected):
    assert classify_source_status(source, authority) == expected


def test_aggregate_status_is_weakest_link():
    assert aggregate_status([]) == S.UNKNOWN
    assert aggregate_status([S.VERIFIED, S.VERIFIED]) == S.VERIFIED
    assert aggregate_status([S.VERIFIED, S.OBSERVED]) == S.OBSERVED
    assert aggregate_status([S.VERIFIED, S.STALE]) == S.STALE
    assert aggregate_status([S.STALE, S.CONTRADICTED]) == S.CONTRADICTED


def test_camera_entity_is_not_verified(repo):
    engine = WorldStateEngine(repo)
    engine.record_observation(_obs("o1", T0, [ObservedEntity(candidate_entity_id="b", type="bottle", location="desk")]))
    entity = repo.get_entity("b")
    assert entity.status == S.OBSERVED
    assert entity.attribute_statuses == {"location": S.OBSERVED}


def test_evidence_links_back_to_each_observation(repo):
    engine = WorldStateEngine(repo)
    engine.record_observation(_obs("obs_001", T0, [ObservedEntity(candidate_entity_id="b", type="bottle", location="l")]))
    engine.record_observation(
        _obs("obs_002", T0 + timedelta(minutes=5), [ObservedEntity(candidate_entity_id="b", type="bottle", location="r")])
    )
    entity = repo.get_entity("b")
    refs = [repo.get_evidence(e).source_reference for e in entity.evidence_refs]
    assert refs == ["obs_001", "obs_002"]
    move = [e for e in repo.list_events() if e.event_type == EventType.OBJECT_MOVED][0]
    assert repo.get_evidence(move.evidence_refs[0]).timestamp == T0 + timedelta(minutes=5)


def test_returned_models_are_copies(repo):
    engine = WorldStateEngine(repo)
    engine.record_observation(_obs("o1", T0, [ObservedEntity(candidate_entity_id="b", type="bottle", location="desk")]))
    fetched = repo.get_entity("b")
    fetched.current_state["location"] = "mutated"
    assert repo.get_entity("b").current_state["location"] == "desk"


def test_duplicate_observation_rejected(repo):
    engine = WorldStateEngine(repo)
    obs = _obs("o1", T0, [ObservedEntity(candidate_entity_id="b", type="bottle", location="desk")])
    engine.record_observation(obs)
    with pytest.raises(DuplicateObservationError):
        engine.record_observation(obs)
    assert len(repo.list_events()) == 1


def test_transaction_rolls_back_on_failure(repo):
    engine = WorldStateEngine(repo)
    engine.record_observation(_obs("o1", T0, [ObservedEntity(candidate_entity_id="b", type="bottle", location="desk")]))
    with pytest.raises(RuntimeError):
        with repo.transaction():
            engine.record_observation(
                _obs("o2", T0 + timedelta(minutes=1), [ObservedEntity(candidate_entity_id="b", type="bottle", location="shelf")])
            )
            raise RuntimeError("boom")
    assert repo.get_entity("b").current_state["location"] == "desk"
    assert repo.get_observation("o2") is None
    assert len(repo.get_state_versions_for_entity("b")) == 1


def test_task_round_trip_with_steps(repo):
    task = Task(id="t12", goal="Replace coolant pump", created_at=T0, updated_at=T0)
    task.steps = [
        TaskStep(task_id="t12", step_order=2, description="remove old pump", dependencies=["s1"]),
        TaskStep(id="s1", task_id="t12", step_order=1, description="isolate system"),
    ]
    repo.save_task(task)
    loaded = repo.get_task("t12")
    assert [s.step_order for s in loaded.steps] == [1, 2]
    loaded.steps = loaded.steps[:1]
    repo.save_task(loaded)
    assert len(repo.get_task("t12").steps) == 1


def test_migrations_match_schema(tmp_path):
    """The Alembic head must create exactly the schema the repository uses."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from database.migrate import upgrade_to_head

    url = f"sqlite:///{tmp_path / 'orbit.db'}"
    upgrade_to_head(url)
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []
