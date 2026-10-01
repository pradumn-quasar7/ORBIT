from datetime import datetime, timezone, timedelta
import pytest

from backend.app.domain.types import EpistemicStatus, EventType, VolatilityClass
from backend.app.domain.models import (
    Observation,
    ObservedEntity,
    Entity,
    Evidence,
    FreshnessPolicy,
)
from backend.app.services.world_state_engine import WorldStateEngine


def test_first_technical_milestone_acceptance(repo):
    """
    Acceptance test from Section 43 & 49:
    Observation A: bottle_01 is at desk_left.
    Observation B: bottle_01 is at desk_right.

    Expected:
    - persistent identity remains bottle_01;
    - current state becomes desk_right;
    - previous state remains in history;
    - an OBJECT_MOVED event is created;
    - evidence links to both observations;
    - a structured world diff can report the movement.
    """
    engine = WorldStateEngine(repository=repo)

    t0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 30, 10, 15, 0, tzinfo=timezone.utc)

    # Observation A
    obs_a = Observation(
        id="obs_001",
        timestamp=t0,
        source="camera",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="bottle_01",
                type="bottle",
                location="desk_left",
                attributes={"color": "blue"},
            )
        ],
    )
    events_a = engine.record_observation(obs_a)
    assert len(events_a) == 1
    assert events_a[0].event_type == EventType.OBJECT_ADDED

    # Verify state after Obs A
    bottle = repo.get_entity("bottle_01")
    assert bottle is not None
    assert bottle.id == "bottle_01"
    assert bottle.current_state["location"] == "desk_left"
    assert bottle.attribute_statuses["location"] == EpistemicStatus.OBSERVED

    # Observation B (same entity, moved)
    obs_b = Observation(
        id="obs_002",
        timestamp=t1,
        source="camera",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="bottle_01",
                type="bottle",
                location="desk_right",
                attributes={"color": "blue"},
            )
        ],
    )
    events_b = engine.record_observation(obs_b)
    assert len(events_b) == 1
    assert events_b[0].event_type == EventType.OBJECT_MOVED
    assert events_b[0].before_state == {"location": "desk_left"}
    assert events_b[0].after_state == {"location": "desk_right"}

    # Verify persistent identity and current state
    updated_bottle = repo.get_entity("bottle_01")
    assert updated_bottle.id == "bottle_01"
    assert updated_bottle.current_state["location"] == "desk_right"

    # Verify history is preserved
    history = repo.get_state_versions_for_entity("bottle_01", attribute="location")
    assert len(history) == 2
    assert history[0].value == "desk_left"
    assert history[0].valid_to == t1
    assert history[1].value == "desk_right"
    assert history[1].valid_from == t1
    assert history[1].valid_to is None

    # Verify evidence links to both observations
    assert len(updated_bottle.evidence_refs) == 2

    # Verify structured world diff reports the movement
    diff = engine.compute_world_diff(baseline_timestamp=t0, target_timestamp=t1)
    move_changes = [c for c in diff.changes if c.change_type == EventType.OBJECT_MOVED]
    assert len(move_changes) == 1
    assert move_changes[0].entity_id == "bottle_01"
    assert move_changes[0].before == "desk_left"
    assert move_changes[0].after == "desk_right"


def test_new_entity_creation_and_re_identification(repo):
    engine = WorldStateEngine(repository=repo)
    t = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc)

    obs = Observation(
        id="obs_m17",
        timestamp=t,
        source="overhead_camera",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="m17",
                type="microscope",
                name="Microscope M17",
                location="bench_3",
                attributes={"configuration": "R6"},
            )
        ],
    )
    events = engine.record_observation(obs)
    assert events[0].event_type == EventType.OBJECT_ADDED

    entity = repo.get_entity("m17")
    assert entity.id == "m17"
    assert entity.name == "Microscope M17"
    assert entity.current_state["configuration"] == "R6"

    # Observe again without changes
    t_next = datetime(2026, 9, 30, 9, 30, 0, tzinfo=timezone.utc)
    obs_revisit = Observation(
        id="obs_m17_revisit",
        timestamp=t_next,
        source="overhead_camera",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="m17",
                type="microscope",
                name="Microscope M17",
                location="bench_3",
                attributes={"configuration": "R6"},
            )
        ],
    )
    events_revisit = engine.record_observation(obs_revisit)
    # No changes, so no move or state change event
    assert len(events_revisit) == 0
    entity_revisit = repo.get_entity("m17")
    assert entity_revisit.id == "m17"
    assert entity_revisit.observed_at == t_next


def test_state_attribute_change(repo):
    engine = WorldStateEngine(repository=repo)

    t0 = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)
    obs1 = Observation(
        id="obs_pump_1",
        timestamp=t0,
        source="sensor",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="pump_01",
                type="pump",
                attributes={"power": "off", "temperature_c": 22.0},
            )
        ],
    )
    engine.record_observation(obs1)

    t1 = datetime(2026, 9, 30, 8, 5, 0, tzinfo=timezone.utc)
    obs2 = Observation(
        id="obs_pump_2",
        timestamp=t1,
        source="sensor",
        observed_entities=[
            ObservedEntity(
                candidate_entity_id="pump_01",
                type="pump",
                attributes={"power": "on", "temperature_c": 22.0},
            )
        ],
    )
    events = engine.record_observation(obs2)
    assert len(events) == 1
    assert events[0].event_type == EventType.OBJECT_STATE_CHANGED
    assert events[0].before_state == {"power": "off"}
    assert events[0].after_state == {"power": "on"}

    pump = repo.get_entity("pump_01")
    assert pump.current_state["power"] == "on"


def test_unknown_is_not_absent(repo):
    """
    Rule 2.7: Unknown is not absent.
    If an object is outside the camera/search coverage, it must NOT automatically become REMOVED.
    """
    engine = WorldStateEngine(repository=repo)

    t0 = datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc)
    # Place two items on the desk
    engine.record_observation(
        Observation(
            id="obs_full",
            timestamp=t0,
            source="camera",
            observed_entities=[
                ObservedEntity(candidate_entity_id="tool_1", type="tool", location="bench_3"),
                ObservedEntity(candidate_entity_id="cable_4", type="cable", location="bench_3"),
            ],
        )
    )

    # Later observation only observes tool_1 (e.g. camera panned away from cable)
    t1 = datetime(2026, 9, 30, 11, 10, 0, tzinfo=timezone.utc)
    engine.record_observation(
        Observation(
            id="obs_partial",
            timestamp=t1,
            source="camera",
            observed_entities=[
                ObservedEntity(candidate_entity_id="tool_1", type="tool", location="bench_3"),
            ],
            spatial_context={"field_of_view": "bench_3_left_quadrant"},
        )
    )

    # cable_4 must still exist in the world state and must NOT be marked REMOVED
    cable = repo.get_entity("cable_4")
    assert cable is not None
    # Location claim is retained with its original evidential status — not dropped,
    # not downgraded, and no removal event was emitted.
    assert cable.current_state["location"] == "bench_3"
    assert cable.attribute_statuses["location"] == EpistemicStatus.OBSERVED
    cable_events = [e.event_type for e in repo.get_events_for_entity("cable_4")]
    assert cable_events == [EventType.OBJECT_ADDED]
    assert EventType.OBJECT_REMOVED_OR_UNOBSERVED not in cable_events


def test_contradiction_retention(repo):
    """
    Rule 2.8: Contradictions are retained.
    When conflicting evidence appears, state is marked CONTRADICTED and both evidence items retained.
    """
    engine = WorldStateEngine(repository=repo)

    t0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    engine.record_observation(
        Observation(
            id="obs_reg",
            timestamp=t0,
            source="digital_registry",
            authority=1.0,
            observed_entities=[
                ObservedEntity(
                    candidate_entity_id="m17",
                    type="equipment",
                    attributes={"configuration": "R6"},
                )
            ],
        )
    )

    m17 = repo.get_entity("m17")
    assert m17.attribute_statuses["configuration"] == EpistemicStatus.VERIFIED

    # Conflicting visual observation
    t1 = datetime(2026, 9, 30, 12, 10, 0, tzinfo=timezone.utc)
    conflicting_evidence = engine.record_evidence(
        source_type="visual_label",
        source_reference="camera_frame_442",
        timestamp=t1,
        quality=0.9,
        authority=0.9,
    )

    conflict_event = engine.record_contradiction(
        entity_id="m17",
        attribute="configuration",
        conflicting_value="R7",
        evidence=conflicting_evidence,
    )

    assert conflict_event.event_type == EventType.EVIDENCE_CONFLICT
    m17 = repo.get_entity("m17")
    assert m17.attribute_statuses["configuration"] == EpistemicStatus.CONTRADICTED
    assert m17.status == EpistemicStatus.CONTRADICTED
    assert conflicting_evidence.id in m17.evidence_refs
    # Both claims are retained in history: the registry value and the visual value.
    values = {sv.value for sv in repo.get_state_versions_for_entity("m17", "configuration")}
    assert values == {"R6", "R7"}


def test_freshness_invalidation(repo):
    """
    Rule 2.6: Freshness is contextual. High volatility attributes expire quickly.
    """
    engine = WorldStateEngine(repository=repo)

    t0 = datetime(2026, 9, 30, 14, 0, 0, tzinfo=timezone.utc)
    engine.record_observation(
        Observation(
            id="obs_machine",
            timestamp=t0,
            source="sensor",
            observed_entities=[
                ObservedEntity(
                    candidate_entity_id="machine_1",
                    type="machine",
                    location="bay_2",
                    attributes={"power": "on"},
                )
            ],
        )
    )

    # Power has a 300s TTL (high volatility), location has 86400s (medium)
    # Check 10 minutes later (600s)
    t_later = t0 + timedelta(seconds=600)
    statuses = engine.evaluate_freshness("machine_1", as_of=t_later)

    assert statuses["power"] == EpistemicStatus.STALE
    assert statuses["location"] == EpistemicStatus.OBSERVED
    machine = repo.get_entity("machine_1")
    assert machine.attribute_statuses["power"] == EpistemicStatus.STALE
