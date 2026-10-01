"""Phase 1 — spatial persistence: anchors, re-identification, relations, sessions.

Exit criterion: the same object persists across two observations/sessions.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.models import Geometry, Observation, ObservedEntity, ObservedRelation
from backend.app.domain.types import EventType, IdentityStatus, ResolutionMethod
from backend.app.services.spatial import AnchorCycleError
from backend.app.services.world_state_engine import WorldStateEngine

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def engine(repo):
    eng = WorldStateEngine(repo)
    a = eng.anchors
    a.register("lab204", T0, anchor_type="room")
    a.register("bench_3", T0, anchor_type="surface", parent_id="lab204")
    a.register("bench_3_left", T0, parent_id="bench_3")
    a.register("bench_3_right", T0, parent_id="bench_3")
    a.register("shelf_a", T0, anchor_type="surface", parent_id="lab204")
    return eng


def observe(engine, obs_id, minutes, *entities, session=None, source="camera"):
    obs = Observation(
        id=obs_id, timestamp=at(minutes), source=source, session_id=session, observed_entities=list(entities)
    )
    events = engine.record_observation(obs)
    return events, engine.repo.get_observation(obs_id)


def bottle(loc, cid=None, color="blue", **kw):
    return ObservedEntity(candidate_entity_id=cid, type="bottle", location=loc, attributes={"color": color}, **kw)


# ----------------------------------------------------------------- anchors
def test_anchor_lineage_and_proximity(engine):
    a = engine.anchors
    assert a.lineage("bench_3_left") == ["bench_3_left", "bench_3", "lab204"]
    assert a.lineage("unregistered_spot") == ["unregistered_spot"]
    assert a.is_within("bench_3_left", "lab204")
    assert not a.is_within("shelf_a", "bench_3")
    assert set(a.descendants("bench_3")) == {"bench_3", "bench_3_left", "bench_3_right"}
    assert a.proximity("bench_3_left", "bench_3_left") == 1.0
    assert a.proximity("bench_3_left", "bench_3") == 0.8
    assert a.proximity("bench_3_left", "bench_3_right") == 0.6
    assert a.proximity("bench_3_left", "shelf_a") == 0.3
    assert a.proximity("bench_3_left", "elsewhere") == 0.0


def test_anchor_cycle_is_rejected(engine):
    with pytest.raises(AnchorCycleError):
        engine.anchors.register("lab204", T0, parent_id="bench_3_left")


# ------------------------------------------------------------ exit criterion
def test_same_object_persists_across_two_sessions(engine, repo):
    """Phase 1 exit: identity survives a session boundary, a viewpoint change and a move."""
    _, o1 = observe(engine, "a1", 0, ObservedEntity(candidate_entity_id="m17", type="microscope", location="bench_3_left"), session="session_A")
    repo_session = repo.get_session("session_A")
    repo_session.ended_at = at(30)
    repo.save_session(repo_session)

    # Session B: no explicit id from the detector, different camera, object relocated.
    _, o2 = observe(
        engine, "b1", 120, ObservedEntity(type="microscope", location="bench_3_right"), session="session_B", source="phone_camera"
    )
    assert o2.resolutions[0].entity_id == "m17"
    assert o2.resolutions[0].method == ResolutionMethod.SIGNATURE
    assert [e.id for e in repo.list_entities()] == ["m17"]
    m17 = repo.get_entity("m17")
    assert m17.current_state["location"] == "bench_3_right"
    assert [s.id for s in repo.list_sessions()] == ["session_A", "session_B"]
    assert [o.id for o in repo.list_observations_for_session("session_B")] == ["b1"]
    assert repo.get_session("session_B").last_observation_at == at(120)


# ------------------------------------------------------------ re-identification
def test_strong_identifier_reidentifies_without_candidate_id(engine, repo):
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="pump_01", type="pump", location="bench_3", identifiers={"serial_number": "SN-1"}))
    observe(engine, "o2", 1, ObservedEntity(candidate_entity_id="pump_02", type="pump", location="bench_3", identifiers={"serial_number": "SN-2"}))
    _, obs = observe(engine, "o3", 5, ObservedEntity(type="pump", location="shelf_a", identifiers={"serial_number": "SN-2"}))
    assert obs.resolutions[0].entity_id == "pump_02"
    assert obs.resolutions[0].method == ResolutionMethod.STRONG_IDENTIFIER
    assert repo.get_entity("pump_02").current_state["location"] == "shelf_a"
    assert repo.get_entity("pump_01").current_state["location"] == "bench_3"


def test_identifier_learned_later_is_used_for_reidentification(engine, repo):
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="pump_01", type="pump", location="bench_3"))
    observe(engine, "o2", 1, ObservedEntity(candidate_entity_id="pump_01", type="pump", identifiers={"serial_number": "SN-9"}))
    observe(engine, "o3", 2, ObservedEntity(candidate_entity_id="pump_02", type="pump", location="bench_3"))
    _, obs = observe(engine, "o4", 3, ObservedEntity(type="pump", identifiers={"serial_number": "SN-9"}))
    assert repo.get_entity("pump_01").canonical_attributes == {"serial_number": "SN-9"}
    assert obs.resolutions[0].entity_id == "pump_01"


def test_signature_conflict_creates_new_entity(engine, repo):
    observe(engine, "o1", 0, bottle("bench_3_left", cid="bottle_01", color="blue"))
    events, obs = observe(engine, "o2", 1, bottle("bench_3_left", color="red"))
    assert obs.resolutions[0].method == ResolutionMethod.NEW
    assert obs.resolutions[0].entity_id != "bottle_01"
    assert [e.event_type for e in events] == [EventType.OBJECT_ADDED]
    assert repo.get_entity("bottle_01").current_state["color"] == "blue"


def test_same_looking_objects_are_not_merged_by_guess(engine, repo):
    observe(engine, "o1", 0, bottle("bench_3_left", cid="bottle_01"), bottle("bench_3_right", cid="bottle_02"))
    # A blue bottle seen on the shelf could be either: do not merge.
    events, obs = observe(engine, "o2", 10, bottle("shelf_a"))
    res = obs.resolutions[0]
    assert res.method == ResolutionMethod.NEW_AMBIGUOUS
    assert res.candidates == ["bottle_01", "bottle_02"]
    provisional = repo.get_entity(res.entity_id)
    assert provisional.identity_status == IdentityStatus.AMBIGUOUS
    assert provisional.identity_candidates == ["bottle_01", "bottle_02"]
    assert EventType.IDENTITY_AMBIGUOUS in [e.event_type for e in events]
    # Originals untouched — no false move.
    assert repo.get_entity("bottle_01").current_state["location"] == "bench_3_left"
    assert repo.get_entity("bottle_02").current_state["location"] == "bench_3_right"


def test_same_looking_objects_in_one_frame_resolve_spatially(engine, repo):
    observe(engine, "o1", 0, bottle("bench_3_left", cid="bottle_01"), bottle("bench_3_right", cid="bottle_02"))
    events, obs = observe(engine, "o2", 10, bottle("bench_3_right"), bottle("bench_3_left"))
    assert [r.entity_id for r in obs.resolutions] == ["bottle_02", "bottle_01"]
    # First is decided by proximity; the second is then the only remaining candidate.
    assert [r.method for r in obs.resolutions] == [ResolutionMethod.SPATIAL, ResolutionMethod.SIGNATURE]
    assert events == []  # nothing moved


def test_geometry_separates_objects_on_the_same_anchor(engine, repo):
    g = lambda x: Geometry(position={"x": x, "y": 0.0, "z": 0.0})
    observe(engine, "o1", 0, bottle("bench_3", cid="b1", geometry=g(0.1)), bottle("bench_3", cid="b2", geometry=g(0.9)))
    _, obs = observe(engine, "o2", 5, bottle("bench_3", geometry=g(0.85)))
    assert obs.resolutions[0].entity_id == "b2"


def test_one_entity_matches_at_most_one_detection_per_observation(engine, repo):
    observe(engine, "o1", 0, bottle("bench_3_left", cid="bottle_01"))
    _, obs = observe(engine, "o2", 5, bottle("bench_3_left"), bottle("bench_3_left"))
    first, second = obs.resolutions
    assert first.entity_id == "bottle_01"
    assert second.entity_id != "bottle_01"
    assert len(repo.list_entities()) == 2


def test_replaced_object_is_not_merged_into_original(engine, repo):
    """Cable C4 replaced: same claimed id, different serial → different physical object."""
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="c4", type="cable", location="bench_3", identifiers={"serial_number": "C4-A"}))
    events, obs = observe(engine, "o2", 60, ObservedEntity(candidate_entity_id="c4", type="cable", location="bench_3", identifiers={"serial_number": "C4-B"}))
    res = obs.resolutions[0]
    assert res.method == ResolutionMethod.NEW_IDENTITY_CONFLICT
    assert res.entity_id != "c4"
    replacement = repo.get_entity(res.entity_id)
    assert replacement.identity_status == IdentityStatus.POSSIBLE_REPLACEMENT
    assert replacement.identity_candidates == ["c4"]
    assert replacement.canonical_attributes == {"serial_number": "C4-B"}
    assert repo.get_entity("c4").canonical_attributes == {"serial_number": "C4-A"}
    assert [e.event_type for e in events] == [EventType.OBJECT_ADDED, EventType.IDENTITY_CONFLICT]


def test_type_mismatch_on_explicit_id_is_identity_conflict(engine, repo):
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="t1", type="tool", location="bench_3"))
    _, obs = observe(engine, "o2", 1, ObservedEntity(candidate_entity_id="t1", type="notebook", location="bench_3"))
    assert obs.resolutions[0].method == ResolutionMethod.NEW_IDENTITY_CONFLICT
    assert repo.get_entity("t1").type == "tool"


# --------------------------------------------------------------- relations
def _cable(rel_type="connected_to", target="laptop", present=True, cid="cable"):
    return ObservedEntity(
        candidate_entity_id=cid,
        type="cable",
        location="bench_3",
        relations=[ObservedRelation(relation_type=rel_type, target=target, present=present)],
    )


def test_relation_lifecycle(engine, repo):
    laptop = ObservedEntity(candidate_entity_id="laptop", type="laptop", location="bench_3")
    events, _ = observe(engine, "o1", 0, _cable(), laptop)  # target appears later in the same frame
    added = [e for e in events if e.event_type == EventType.RELATION_CHANGED]
    assert len(added) == 1 and added[0].after_state == {"connected_to": "laptop"}

    # Symmetric relation seen from the other side: corroboration, not a new relation.
    rel_from_laptop = ObservedEntity(
        candidate_entity_id="laptop", type="laptop", relations=[ObservedRelation(relation_type="connected_to", target="cable")]
    )
    events, _ = observe(engine, "o2", 5, rel_from_laptop)
    assert events == []
    active = engine.relations.active_relations("cable")
    assert len(active) == 1 and len(active[0].evidence_refs) == 2

    # Unknown ≠ absent: a frame without the relation does not end it.
    observe(engine, "o3", 10, ObservedEntity(candidate_entity_id="cable", type="cable", location="bench_3"))
    assert len(engine.relations.active_relations("cable")) == 1

    # Explicitly observed as unplugged.
    events, _ = observe(engine, "o4", 15, _cable(present=False))
    assert [e.after_state for e in events] == [{"connected_to": None}]
    assert engine.relations.active_relations("cable") == []
    hist = repo.get_relations_for_entity("cable")
    assert hist[0].valid_from == at(0) and hist[0].valid_to == at(15)
    # Historical query still sees it.
    assert len(engine.relations.active_relations("cable", as_of=at(7))) == 1


def test_exclusive_relation_retarget(engine, repo):
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="tray", type="tray", location="bench_3"), ObservedEntity(candidate_entity_id="cart", type="cart", location="bench_3"))
    observe(engine, "o2", 1, ObservedEntity(candidate_entity_id="t1", type="tool", relations=[ObservedRelation(relation_type="on", target="tray")]))
    events, _ = observe(engine, "o3", 2, ObservedEntity(candidate_entity_id="t1", type="tool", relations=[ObservedRelation(relation_type="on", target="cart")]))
    assert events[0].event_type == EventType.RELATION_CHANGED
    assert events[0].before_state == {"on": "tray"} and events[0].after_state == {"on": "cart"}
    active = engine.relations.active_relations("t1")
    assert [(r.relation_type, r.target_entity) for r in active] == [("on", "cart")]


def test_relation_targets_follow_identity_resolution(engine, repo):
    observe(engine, "o1", 0, ObservedEntity(candidate_entity_id="c4", type="cable", identifiers={"serial_number": "A"}))
    # Same frame: replacement cable claims id c4 (resolves to a new id) and a plug relates to it.
    _, obs = observe(
        engine,
        "o2",
        5,
        ObservedEntity(candidate_entity_id="c4", type="cable", identifiers={"serial_number": "B"}),
        ObservedEntity(candidate_entity_id="plug", type="plug", relations=[ObservedRelation(relation_type="connected_to", target="c4")]),
    )
    new_cable = obs.resolutions[0].entity_id
    assert [r.target_entity for r in engine.relations.active_relations("plug")] == [new_cable]


# --------------------------------------------------------------------- API
def test_spatial_api(client):
    assert client.post("/anchors", json={"id": "lab", "anchor_type": "room"}).status_code == 201
    assert client.post("/anchors", json={"id": "bench", "parent_id": "lab"}).status_code == 201
    assert client.post("/anchors", json={"id": "lab", "parent_id": "bench"}).status_code == 422
    assert client.get("/anchors/bench").json()["lineage"] == ["bench", "lab"]

    assert client.post("/sessions", json={"id": "S1", "label": "Session A"}).status_code == 201
    obs = {
        "id": "obs1",
        "timestamp": T0.isoformat(),
        "source": "camera",
        "session_id": "S1",
        "observed_entities": [
            {"candidate_entity_id": "cable", "type": "cable", "location": "bench",
             "relations": [{"relation_type": "connected_to", "target": "laptop"}]},
            {"candidate_entity_id": "laptop", "type": "laptop", "location": "bench"},
        ],
    }
    assert client.post("/observations", json=obs).status_code == 200
    resolutions = client.get("/observations/obs1").json()["resolutions"]
    assert [(r["entity_id"], r["method"]) for r in resolutions] == [("cable", "NEW"), ("laptop", "NEW")]
    rels = client.get("/entities/laptop/relations").json()
    assert [(r["source_entity"], r["target_entity"]) for r in rels] == [("cable", "laptop")]
    assert [o["id"] for o in client.get("/sessions/S1/observations").json()] == ["obs1"]
    ended = client.post("/sessions/S1/end", json={"ended_at": (T0 + timedelta(hours=1)).isoformat()}).json()
    assert ended["ended_at"].startswith("2026-09-30T10:00")
