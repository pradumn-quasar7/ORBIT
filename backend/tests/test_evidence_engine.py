"""Phase 2 — evidence engine: provenance, policy, freshness, invalidation, contradiction.

Exit criterion: unsupported current-state claims are blocked or downgraded.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.models import FreshnessPolicy, Observation, ObservedEntity
from backend.app.domain.types import (
    ClaimDecision,
    ClaimDisposition,
    EpistemicStatus as S,
    EventType,
    EvidenceChannel,
    FreshnessState,
    SourceType,
    VolatilityClass,
)
from backend.app.services.evidence_policy import EvidencePolicy, channel, grade, infer_source_type
from backend.app.services.freshness import FreshnessPolicyRegistry
from backend.app.services.world_state_engine import WorldStateEngine

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(seconds=0, minutes=0, hours=0):
    return T0 + timedelta(seconds=seconds, minutes=minutes, hours=hours)


@pytest.fixture
def engine(repo):
    return WorldStateEngine(repo)


_counter = {"n": 0}


def observe(engine, when, source="camera", quality=1.0, authority=1.0, **detection):
    _counter["n"] += 1
    detection.setdefault("type", "equipment")
    obs = Observation(
        id=f"obs_{_counter['n']}",
        timestamp=when,
        source=source,
        quality=quality,
        authority=authority,
        observed_entities=[ObservedEntity(**detection)],
    )
    return engine.record_observation(obs)


def claim(engine, entity_id, attr, value, when, source, **kw):
    return engine.assert_claim(entity_id, attr, value, source=source, timestamp=when, **kw)


def assess(engine, entity_id, attr, when):
    return engine.claims.assess_attribute(entity_id, attr, when)


def versions(repo, entity_id, attr):
    return repo.get_state_versions_for_entity(entity_id, attr)


# ------------------------------------------------------------ classification
@pytest.mark.parametrize(
    "source,expected_type,expected_channel",
    [
        ("camera", SourceType.VISUAL_OBSERVATION, EvidenceChannel.DIRECT),
        ("lab_webcam_2", SourceType.VISUAL_OBSERVATION, EvidenceChannel.DIRECT),
        ("digital_registry", SourceType.EXTERNAL_RECORD, EvidenceChannel.RECORD),
        ("manual_verification", SourceType.MANUAL_VERIFICATION, EvidenceChannel.DIRECT),
        ("user", SourceType.USER_STATEMENT, EvidenceChannel.TESTIMONY),
        ("mystery_box", SourceType.OTHER, EvidenceChannel.DIRECT),
    ],
)
def test_source_inference_and_channels(source, expected_type, expected_channel):
    assert infer_source_type(source) == expected_type
    assert channel(expected_type) == expected_channel


def test_grades():
    assert grade(SourceType.VISUAL_OBSERVATION, 1.0, 1.0) == S.OBSERVED
    assert grade(SourceType.VISUAL_OBSERVATION, 0.2, 1.0) == S.UNKNOWN  # weak evidence
    assert grade(SourceType.EXTERNAL_RECORD, 1.0, 0.95) == S.VERIFIED
    assert grade(SourceType.INFERENCE, 1.0, 1.0) == S.INFERRED


def test_evidence_has_provenance_and_integrity(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3", attributes={"configuration": "R6"})
    ev = repo.list_evidence()[0]
    assert ev.source == "camera" and ev.source_type == SourceType.VISUAL_OBSERVATION
    assert ev.integrity_reference.startswith("sha256:")
    assert engine.verify_evidence(ev.id)
    sv = versions(repo, "m17", "configuration")[0]
    assert sv.support[0].evidence_id == ev.id and sv.support[0].source == "camera"
    assert sv.volatility_class == VolatilityClass.LOW


def test_tampered_evidence_fails_integrity(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    ev = repo.list_evidence()[0]
    ev.authority = 0.1  # someone rewrites the trust level of a memory record
    repo.save_evidence(ev)
    assert not engine.verify_evidence(ev.id)

    engine.assert_claim("m17", "configuration", "R6", source="digital_registry", timestamp=at(minutes=1))
    claim_ev = repo.list_evidence()[-1]
    assert engine.verify_evidence(claim_ev.id)
    claim_ev.content["value"] = "R9"
    repo.save_evidence(claim_ev)
    assert not engine.verify_evidence(claim_ev.id)


# ------------------------------------------------------------------ freshness
def test_reobservation_refreshes_freshness(engine, repo):
    """Phase 0 bug: re-observing an unchanged value never refreshed it."""
    observe(engine, at(), candidate_entity_id="pump", attributes={"power": "on"}, source="sensor")
    observe(engine, at(seconds=250), candidate_entity_id="pump", attributes={"power": "on"}, source="sensor")
    a = assess(engine, "pump", "power", at(seconds=400))
    assert a.status == S.OBSERVED and a.freshness.state == FreshnessState.FRESH
    assert a.freshness.last_supported_at == at(seconds=250)
    assert len(versions(repo, "pump", "power")) == 1  # corroboration, not a new version


def test_freshness_is_read_time_and_history_is_not_rewritten(engine, repo):
    observe(engine, at(), candidate_entity_id="pump", attributes={"power": "on"}, source="sensor")
    assert assess(engine, "pump", "power", at(seconds=600)).status == S.STALE
    assert assess(engine, "pump", "power", at(seconds=100)).status == S.OBSERVED  # past instant
    assert assess(engine, "pump", "power", at(seconds=240)).freshness.state == FreshnessState.AGING
    engine.evaluate_freshness("pump", at(seconds=600))
    assert versions(repo, "pump", "power")[0].status == S.OBSERVED  # stored grade untouched
    assert repo.get_entity("pump").attribute_statuses["power"] == S.STALE  # cached view refreshed


def test_freshness_is_attribute_specific(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3", attributes={"power": "on", "color": "grey", "configuration": "R6"})
    later = at(hours=2)
    assert assess(engine, "m17", "power", later).status == S.STALE  # HIGH volatility, 5 min
    assert assess(engine, "m17", "location", later).status == S.OBSERVED  # MEDIUM, 24 h
    assert assess(engine, "m17", "configuration", at(hours=24 * 8)).status == S.STALE  # LOW, 7 days
    assert assess(engine, "m17", "color", at(hours=24 * 365)).status == S.OBSERVED  # intrinsic: never


def test_entity_level_freshness_override(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    m17 = repo.get_entity("m17")
    m17.freshness_policies["location"] = FreshnessPolicy(volatility=VolatilityClass.HIGH, ttl_seconds=60)
    repo.save_entity(m17)
    assert assess(engine, "m17", "location", at(seconds=120)).status == S.STALE


def test_freshness_ablation_never_goes_stale(repo):
    eng = WorldStateEngine(repo, freshness=FreshnessPolicyRegistry(enabled=False))
    observe(eng, at(), candidate_entity_id="pump", attributes={"power": "on"}, source="sensor")
    assert assess(eng, "pump", "power", at(hours=10)).status == S.OBSERVED


# ------------------------------------------------------------- contradiction
def _registry_then_camera(engine):
    claim(engine, "m17", "configuration", "R6", at(), "digital_registry")
    return observe(engine, at(minutes=10), candidate_entity_id="m17", attributes={"configuration": "R7"})


def test_cross_channel_disagreement_is_contradiction_not_overwrite(engine, repo):
    """Spec §2.8: registry R6 vs visual label R7 → CONTRADICTED, both retained."""
    observe(engine, at(seconds=-1), candidate_entity_id="m17", type="equipment", location="bench_3")
    events = _registry_then_camera(engine)
    assert [e.event_type for e in events] == [EventType.EVIDENCE_CONFLICT]

    m17 = repo.get_entity("m17")
    assert m17.current_state["configuration"] == "R6"  # not silently overwritten
    assert m17.attribute_statuses["configuration"] == S.CONTRADICTED
    assert m17.status == S.CONTRADICTED

    a = assess(engine, "m17", "configuration", at(minutes=11))
    assert a.status == S.CONTRADICTED and not a.supportable and a.value is None
    assert a.recommended_action == "verify"
    assert {(c.value, tuple(c.sources)) for c in a.conflicts} == {("R6", ("digital_registry",)), ("R7", ("camera",))}
    assert len(repo.list_conflicts(entity_id="m17")) == 1


def test_conflict_persists_until_sources_agree(engine, repo):
    observe(engine, at(seconds=-1), candidate_entity_id="m17", location="bench_3")
    _registry_then_camera(engine)
    # The camera repeating itself corroborates its side; the registry still disagrees.
    observe(engine, at(minutes=20), candidate_entity_id="m17", attributes={"configuration": "R7"})
    assert assess(engine, "m17", "configuration", at(minutes=21)).status == S.CONTRADICTED
    # The registry is updated: its own old claim is superseded, sources now agree.
    result = claim(engine, "m17", "configuration", "R7", at(minutes=30), "digital_registry")
    assert result.decision == ClaimDecision.CONFLICT_UPDATE
    assert EventType.CONFLICT_RESOLVED in [e.event_type for e in result.events]
    assert EventType.OBJECT_STATE_CHANGED in [e.event_type for e in result.events]
    a = assess(engine, "m17", "configuration", at(minutes=31))
    assert a.supportable and a.value == "R7"
    assert repo.list_conflicts(entity_id="m17")[0].resolution_reason == "remaining sources agree"
    # History keeps the whole story, and the conflict is visible as of the past.
    assert assess(engine, "m17", "configuration", at(minutes=15)).status == S.CONTRADICTED
    assert {v.value for v in versions(repo, "m17", "configuration")} == {"R6", "R7"}


def test_verification_resolves_conflict(engine, repo):
    observe(engine, at(seconds=-1), candidate_entity_id="m17", location="bench_3")
    _registry_then_camera(engine)
    result = claim(engine, "m17", "configuration", "R6", at(minutes=15), "manual_verification", authority=0.95)
    assert result.decision == ClaimDecision.RESOLVE
    a = assess(engine, "m17", "configuration", at(minutes=16))
    assert a.status == S.VERIFIED and a.value == "R6"
    rejected = [v for v in versions(repo, "m17", "configuration") if v.value == "R7"][0]
    assert rejected.disposition == ClaimDisposition.REJECTED and rejected.valid_to == at(minutes=15)
    conflict = repo.list_conflicts(entity_id="m17")[0]
    assert conflict.resolution_evidence is not None and conflict.resolution_version_id is not None


def test_same_channel_succession_is_change_but_simultaneous_disagreement_is_conflict(engine, repo):
    observe(engine, at(), candidate_entity_id="bottle", type="bottle", location="desk_left", source="camera")
    events = observe(engine, at(minutes=5), candidate_entity_id="bottle", type="bottle", location="desk_right", source="camera")
    assert [e.event_type for e in events] == [EventType.OBJECT_MOVED]
    # A second camera disagrees 10 s later: two sensors cannot both be right.
    events = observe(engine, at(minutes=5, seconds=10), candidate_entity_id="bottle", type="bottle", location="shelf", source="phone_camera")
    assert [e.event_type for e in events] == [EventType.EVIDENCE_CONFLICT]


def test_cross_channel_outside_window(engine, repo):
    observe(engine, at(), candidate_entity_id="bottle", type="bottle", location="desk_left")
    # A person reports a location two hours later with equal strength: plausible change.
    r = claim(engine, "bottle", "location", "shelf", at(hours=2), "user")
    assert r.decision == ClaimDecision.SUPERSEDE
    # Misleading low-authority evidence later on: retained, never overrides.
    r = claim(engine, "bottle", "location", "trash", at(hours=4), "camera", authority=0.5)
    assert r.decision == ClaimDecision.UNCONFIRMED
    assert repo.get_entity("bottle").current_state["location"] == "shelf"


def test_weak_evidence_does_not_overwrite(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    events = observe(engine, at(minutes=5), candidate_entity_id="m17", location="shelf_a", quality=0.2)
    assert [e.event_type for e in events] == [EventType.UNCONFIRMED_CHANGE]
    assert repo.get_entity("m17").current_state["location"] == "bench_3"
    unconfirmed = [v for v in versions(repo, "m17", "location") if v.disposition == ClaimDisposition.UNCONFIRMED]
    assert len(unconfirmed) == 1 and "insufficient evidence" in unconfirmed[0].invalidation_reason
    assert assess(engine, "m17", "location", at(minutes=6)).value == "bench_3"


def test_detection_confidence_scales_strength(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    events = observe(engine, at(minutes=5), candidate_entity_id="m17", location="shelf_a", confidence=0.2)
    assert [e.event_type for e in events] == [EventType.UNCONFIRMED_CHANGE]


def test_weak_only_evidence_is_unknown(engine, repo):
    observe(engine, at(), candidate_entity_id="ghost", type="cup", location="desk", quality=0.2)
    a = assess(engine, "ghost", "location", at(seconds=1))
    assert a.status == S.UNKNOWN and not a.supportable and a.last_known_value == "desk"
    # A good observation of the same value upgrades it.
    observe(engine, at(seconds=10), candidate_entity_id="ghost", type="cup", location="desk")
    assert assess(engine, "ghost", "location", at(seconds=11)).status == S.OBSERVED


def test_out_of_order_evidence_is_not_applied(engine, repo):
    observe(engine, at(minutes=10), candidate_entity_id="m17", location="bench_3")
    events = observe(engine, at(minutes=1), candidate_entity_id="m17", location="shelf_a")
    assert [e.event_type for e in events] == [EventType.UNCONFIRMED_CHANGE]
    assert repo.get_entity("m17").current_state["location"] == "bench_3"


def test_inference_is_labelled_and_cannot_override_observation(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    r = claim(engine, "m17", "firmware", "2.1", at(minutes=1), "inference")
    assert r.decision == ClaimDecision.NEW
    a = assess(engine, "m17", "firmware", at(minutes=2))
    assert a.status == S.INFERRED and not a.supportable and a.reason == "inferred, not observed"
    r = claim(engine, "m17", "location", "shelf_a", at(minutes=3), "inference")
    assert r.decision == ClaimDecision.UNCONFIRMED


def test_independent_corroboration_verifies(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3", source="camera")
    assert assess(engine, "m17", "location", at(seconds=1)).status == S.OBSERVED
    observe(engine, at(seconds=30), candidate_entity_id="m17", location="bench_3", source="camera")
    assert assess(engine, "m17", "location", at(seconds=31)).status == S.OBSERVED  # same source: not independent
    observe(engine, at(seconds=60), candidate_entity_id="m17", location="bench_3", source="phone_camera")
    assert assess(engine, "m17", "location", at(seconds=61)).status == S.VERIFIED


def test_last_writer_wins_ablation(repo):
    eng = WorldStateEngine(repo, policy=EvidencePolicy(FreshnessPolicyRegistry(), detect_contradictions=False))
    observe(eng, at(seconds=-1), candidate_entity_id="m17", location="bench_3")
    events = _registry_then_camera(eng)
    assert [e.event_type for e in events] == [EventType.OBJECT_STATE_CHANGED]
    assert repo.list_conflicts() == []


# -------------------------------------------------------------- invalidation
def test_intervention_invalidates_and_reobservation_revalidates(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3", attributes={"configuration": "R6"})
    events = engine.record_intervention("m17", at(minutes=10), "technician serviced M17", attributes=["configuration"])
    assert [e.event_type for e in events] == [EventType.STATE_INVALIDATED]
    a = assess(engine, "m17", "configuration", at(minutes=11))
    assert a.status == S.STALE and not a.supportable and "intervention" in a.reason
    assert assess(engine, "m17", "location", at(minutes=11)).status == S.OBSERVED  # untouched
    assert repo.get_entity("m17").attribute_statuses["configuration"] == S.STALE

    # Same value re-observed → revalidated; a different value would be a plain change.
    observe(engine, at(minutes=20), candidate_entity_id="m17", attributes={"configuration": "R6"})
    assert assess(engine, "m17", "configuration", at(minutes=21)).status == S.OBSERVED


def test_intervention_then_new_value_is_a_change_not_a_conflict(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3")
    claim(engine, "m17", "configuration", "R6", at(seconds=1), "digital_registry")
    engine.record_intervention("m17", at(minutes=5), "reconfigured", attributes=["configuration"])
    events = observe(engine, at(minutes=6), candidate_entity_id="m17", attributes={"configuration": "R7"})
    assert [e.event_type for e in events] == [EventType.OBJECT_STATE_CHANGED]
    assert repo.list_conflicts() == []


def test_policy_trigger_invalidates_same_entity_attribute(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", location="bench_3", attributes={"calibration": "ok"})
    events = observe(engine, at(minutes=30), candidate_entity_id="m17", location="bench_4")
    assert [e.event_type for e in events] == [EventType.OBJECT_MOVED, EventType.STATE_INVALIDATED]
    a = assess(engine, "m17", "calibration", at(minutes=31))
    assert a.status == S.STALE and "m17.location" in a.reason


def test_dependency_propagation_is_transitive_and_cycle_safe(engine, repo):
    observe(engine, at(), candidate_entity_id="m17", attributes={"configuration": "R6"})
    observe(engine, at(), candidate_entity_id="c4", type="cable", attributes={"compatible": True})
    observe(engine, at(), candidate_entity_id="t12", type="task_claim", attributes={"step7_ready": True})
    engine.add_dependency("c4", "compatible", "m17", "configuration", at())
    engine.add_dependency("t12", "step7_ready", "c4", "compatible", at())
    engine.add_dependency("m17", "configuration", "t12", "step7_ready", at())  # cycle

    events = observe(engine, at(minutes=5), candidate_entity_id="m17", attributes={"configuration": "R7"})
    invalidated = [(e.entity_id, next(iter(e.before_state))) for e in events if e.event_type == EventType.STATE_INVALIDATED]
    assert invalidated == [("c4", "compatible"), ("t12", "step7_ready")]
    assert assess(engine, "c4", "compatible", at(minutes=6)).status == S.STALE
    assert assess(engine, "t12", "step7_ready", at(minutes=6)).status == S.STALE
    assert assess(engine, "m17", "configuration", at(minutes=6)).status == S.OBSERVED
    assert repo.get_entity("c4").attribute_statuses["compatible"] == S.STALE


def test_contradiction_propagates_to_dependents(engine, repo):
    observe(engine, at(seconds=-1), candidate_entity_id="m17", location="bench_3")
    observe(engine, at(seconds=-1), candidate_entity_id="c4", type="cable", attributes={"compatible": True})
    engine.add_dependency("c4", "compatible", "m17", "configuration", at())
    _registry_then_camera(engine)
    assert assess(engine, "c4", "compatible", at(minutes=11)).status == S.STALE


def test_intervention_closes_open_conflict(engine, repo):
    observe(engine, at(seconds=-1), candidate_entity_id="m17", location="bench_3")
    _registry_then_camera(engine)
    engine.record_intervention("m17", at(minutes=20), "replaced configuration board", attributes=["configuration"])
    conflict = repo.list_conflicts(entity_id="m17")[0]
    assert conflict.resolved_at == at(minutes=20) and conflict.resolution_reason.startswith("invalidated")
    assert assess(engine, "m17", "configuration", at(minutes=21)).status == S.STALE


# -------------------------------------------------------------- exit criterion
def test_unsupported_current_state_claims_are_blocked_or_downgraded(engine, repo):
    """Phase 2 exit: only fresh OBSERVED/VERIFIED claims are asserted as current."""
    observe(engine, at(), candidate_entity_id="e1", location="bench_3", attributes={"power": "on", "configuration": "R6"})
    claim(engine, "e1", "configuration", "R7", at(minutes=1), "digital_registry")  # → contradiction
    claim(engine, "e1", "firmware", "2.1", at(minutes=1), "inference")
    observe(engine, at(minutes=1), candidate_entity_id="e1", attributes={"label": "?"}, quality=0.1)

    now = at(minutes=10)
    table = {a: assess(engine, "e1", a, now) for a in ("location", "power", "configuration", "firmware", "label", "serial")}
    assert table["location"].supportable and table["location"].value == "bench_3"
    expected_blocked = {
        "power": S.STALE,
        "configuration": S.CONTRADICTED,
        "firmware": S.INFERRED,
        "label": S.UNKNOWN,
        "serial": S.UNKNOWN,
    }
    for attr, status in expected_blocked.items():
        a = table[attr]
        assert (a.status, a.supportable, a.value) == (status, False, None), attr
        assert a.recommended_action in ("observe", "verify"), attr


# ---------------------------------------------------------------------- API
def test_evidence_api(client):
    obs = {
        "id": "o1",
        "timestamp": T0.isoformat(),
        "source": "camera",
        "observed_entities": [{"candidate_entity_id": "m17", "type": "microscope", "location": "bench_3", "attributes": {"configuration": "R6"}}],
    }
    client.post("/observations", json=obs)
    res = client.post(
        "/claims",
        json={"entity_id": "m17", "attribute": "configuration", "value": "R7", "source": "digital_registry",
              "timestamp": (T0 + timedelta(minutes=1)).isoformat()},
    ).json()
    assert res["decision"] == "CONTRADICT" and res["assessment"]["status"] == "CONTRADICTED"
    assert len(client.get("/conflicts").json()) == 1

    state = client.get("/entities/m17/state", params={"as_of": (T0 + timedelta(minutes=2)).isoformat()}).json()
    assert state["attributes"]["configuration"]["supportable"] is False
    assert state["attributes"]["location"]["supportable"] is True

    inv = client.post("/entities/m17/interventions", json={"description": "moved by tech", "attributes": ["location"],
                                                            "timestamp": (T0 + timedelta(minutes=3)).isoformat()})
    assert [e["event_type"] for e in inv.json()] == ["STATE_INVALIDATED"]
    loc = client.get("/entities/m17/claims/location", params={"as_of": (T0 + timedelta(minutes=4)).isoformat()}).json()
    assert loc["status"] == "STALE"

    ev_id = state["attributes"]["location"]["evidence_refs"][0]
    view = client.get(f"/evidence/{ev_id}").json()
    assert view["integrity_verified"] is True and view["evidence"]["source_type"] == "VISUAL_OBSERVATION"
    assert client.post("/claims", json={"entity_id": "nope", "attribute": "a", "value": 1, "source": "user"}).status_code == 404


# ---------------------------------------------------------------- migration
def test_migration_0003_backfills_existing_data(tmp_path):
    import sqlalchemy as sa
    from alembic import command

    from database.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO evidence (id, seq, source_type, source_reference, timestamp, quality, authority, provenance) "
            "VALUES ('e1', 1, 'digital_registry', 'r1', '2026-09-30 09:00:00', 1.0, 1.0, '{}')"))
        c.execute(sa.text(
            "INSERT INTO state_versions (id, seq, entity_id, attribute, value, status, valid_from, supported_by) "
            "VALUES ('sv1', 1, 'm17', 'configuration', '\"R6\"', 'CONTRADICTED', '2026-09-30 09:00:00', '[\"e1\"]')"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        ev = c.execute(sa.text("SELECT source_type, source FROM evidence")).one()
        sv = c.execute(sa.text("SELECT status, disposition, support, last_supported_at FROM state_versions")).one()
    assert tuple(ev) == ("EXTERNAL_RECORD", "digital_registry")
    assert (sv.status, sv.disposition) == ("OBSERVED", "CONFLICTING")
    assert '"evidence_id": "e1"' in sv.support and sv.last_supported_at is not None
