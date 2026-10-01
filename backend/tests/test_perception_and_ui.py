"""Phase 8 — device/perception adapters, evidence minimisation, inspection dashboard."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.container import OrbitServices
from backend.app.domain.types import ResolutionMethod
from backend.app.main import create_app
from backend.app.providers.perception import (
    Detection,
    DetectionPerceptionProvider,
    SimulatedPerceptionProvider,
    SimulatedScene,
    make_frame,
    parse_identifiers,
)
from backend.app.services.dashboard import build_summary
from backend.app.services.perception_gateway import PerceptionGateway

T0 = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


def at(minutes=0):
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def svc(repo):
    s = OrbitServices.build(repo)
    for a, p in (("lab", None), ("bench", "lab"), ("bench_left", "bench"), ("bench_right", "bench"), ("shelf", "lab")):
        s.anchors.register(a, T0, parent_id=p)
    return s


# ------------------------------------------------------------- detector adapter
def test_ocr_text_splits_identifiers_from_attributes():
    ids, attrs = parse_identifiers("ACME pump  S/N: c4-b  MODEL CP-200")
    assert ids == {"serial_number": "C4-B"}
    assert attrs["model_number"] == "CP-200" and attrs["label"].startswith("ACME")


def test_detector_adapter_maps_boxes_to_anchors(svc, repo):
    detections = [
        Detection(label="bottle", confidence=0.9, bbox=(0.1, 0.4, 0.2, 0.6), track_id="trk-1"),
        Detection(label="pump", confidence=0.8, bbox=(0.7, 0.4, 0.9, 0.6), text="S/N CP-77"),
        Detection(label="cup", confidence=0.1, bbox=(0.5, 0.5, 0.6, 0.6)),  # below threshold
    ]
    provider = DetectionPerceptionProvider(lambda f: detections, {"bench_left": (0, 0, 0.5, 1), "bench_right": (0.5, 0, 1, 1)}, model_id="yolo-test")
    frame = make_frame("webcam", at(0), b"\x89PNG...", field_of_view="bench")
    obs, events = PerceptionGateway(repo, svc.engine).ingest(frame, provider)
    assert [(e.type, e.location, e.candidate_entity_id) for e in obs.observed_entities] == [("bottle", "bench_left", None), ("pump", "bench_right", None)]
    assert obs.observed_entities[0].confidence == 0.9
    assert obs.raw_reference == frame.content_hash and obs.raw_reference.startswith("sha256:")
    assert obs.provenance == {"perception_provider": "detector-adapter-v1", "model_id": "yolo-test", "frame_id": frame.frame_id}
    ev = next(e for e in repo.list_evidence() if e.source_reference == obs.id)
    assert ev.retention_policy == "hash_only"

    # Next frame: the detector's track ids changed, but identity persists via serial / signature.
    frame2 = make_frame("webcam", at(10), b"other", field_of_view="bench")
    moved = [Detection(label="pump", confidence=0.8, bbox=(0.1, 0.4, 0.2, 0.6), text="S/N CP-77"),
             Detection(label="bottle", confidence=0.9, bbox=(0.7, 0.4, 0.8, 0.6), track_id="trk-99")]
    obs2, _ = PerceptionGateway(repo, svc.engine).ingest(frame2, DetectionPerceptionProvider(lambda f: moved, provider.region_map))
    methods = {r.entity_id: r.method for r in obs2.resolutions}
    assert set(methods) == {r.entity_id for r in obs.resolutions}  # same two entities
    assert ResolutionMethod.STRONG_IDENTIFIER in methods.values() and ResolutionMethod.SIGNATURE in methods.values()


def test_poor_lighting_lowers_observation_quality(svc, repo):
    provider = DetectionPerceptionProvider(lambda f: [Detection(label="tool", confidence=0.9, bbox=(0, 0, 1, 1))])
    obs, _ = PerceptionGateway(repo, svc.engine).ingest(make_frame("cam", at(0), b"x", field_of_view="bench", lighting="poor"), provider)
    assert obs.quality == 0.6


# ------------------------------------------------------------ simulated scene
def test_simulated_perception_respects_view_occlusion_and_seed(svc, repo):
    scene = SimulatedScene()
    scene.place("m17", "microscope", "bench_left")
    scene.place("bottle", "bottle", "shelf")
    scene.place("cable", "cable", "bench_right", occluded=True)
    cam = SimulatedPerceptionProvider(scene, svc.anchors.is_within)
    obs = cam.perceive(make_frame("cam", at(0), field_of_view="bench"))
    assert [e.candidate_entity_id for e in obs.observed_entities] == ["m17"]  # bottle out of view, cable occluded

    for i in range(20):
        scene.place(f"part_{i}", "part", "shelf")

    def run(seed):
        cam = SimulatedPerceptionProvider(scene, svc.anchors.is_within, seed=seed, miss_rate=0.5)
        return [tuple(e.candidate_entity_id for e in cam.perceive(make_frame("cam", at(m), field_of_view="lab")).observed_entities)
                for m in range(5)]

    assert run(1) == run(1)  # seeded noise is reproducible across frames
    assert run(1) != run(2)
    assert 0 < sum(map(len, run(1))) < 5 * 22  # misses actually happen


def test_simulated_anonymous_camera_relies_on_reidentification(svc, repo):
    scene = SimulatedScene()
    scene.place("m17", "microscope", "bench_left")
    cam = SimulatedPerceptionProvider(scene, svc.anchors.is_within)
    gw = PerceptionGateway(repo, svc.engine)
    gw.ingest(make_frame("cam", at(0), field_of_view="lab"), cam)
    scene.move("m17", "shelf")
    cam.stable_ids = False
    obs, events = gw.ingest(make_frame("cam", at(30), field_of_view="lab"), cam)
    assert obs.resolutions[0].entity_id == "m17" and obs.resolutions[0].method == ResolutionMethod.SIGNATURE
    assert [e.event_type.value for e in events] == ["OBJECT_MOVED"]


# -------------------------------------------------------------------- privacy
def test_redaction_keeps_structured_facts_and_integrity(svc, repo):
    provider = DetectionPerceptionProvider(lambda f: [Detection(label="tool", confidence=0.9, bbox=(0, 0, 1, 1))])
    gw = PerceptionGateway(repo, svc.engine, retain_raw=True)
    frame = make_frame("cam", at(0), b"pixels", field_of_view="bench", image_ref="s3://frames/0001.png")
    obs, _ = gw.ingest(frame, provider)
    ev = next(e for e in repo.list_evidence() if e.source_reference == obs.id)
    assert obs.raw_reference == "s3://frames/0001.png" and ev.retention_policy == "raw_retained"
    assert svc.engine.verify_evidence(ev.id)

    red = gw.redact(obs.id, at(5), reason="contains a person", actor="privacy_officer")
    assert red.raw_reference is None and red.redaction["reason"] == "contains a person"
    assert repo.get_evidence(ev.id).retention_policy == "redacted"
    assert svc.engine.verify_evidence(ev.id)  # still verifiable without the raw media
    assert repo.get_entity(red.resolutions[0].entity_id).current_state["location"] == "bench"  # facts kept

    tampered = repo.get_observation(obs.id)
    tampered.observed_entities[0].type = "weapon"
    repo.save_observation(tampered)
    assert not svc.engine.verify_evidence(ev.id)


def test_legacy_v1_integrity_still_verifies(svc, repo):
    from backend.app.domain.models import Observation

    obs = Observation(id="legacy", timestamp=at(0), source="camera", raw_reference="sha256:abc")
    repo.save_observation(obs)
    ev = svc.engine.record_evidence("camera", "legacy", at(0), content={"kind": "observation"},
                                    subject=svc.engine._observation_subject(obs, version=1))
    assert svc.engine.verify_evidence(ev.id)


# ------------------------------------------------------------------ dashboard
def _seeded(tmp_path):
    from scripts.seed_demo import seed

    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    return seed(f"sqlite:///{tmp_path / 'demo.db'}", now), now


def test_flagship_seed_scenario_end_to_end(tmp_path):
    """Spec §4/§18: Session A → changes → Session B, through simulated perception."""
    svc, now = _seeded(tmp_path)
    changed = svc.agent.answer("What changed?", now)
    text = changed.summary
    assert "Microscope M17 moved from bench_3 to bench_4." in text
    assert "Cable C4 is confirmed absent from bench_3" in text
    assert "INFERRED possible replacement of c4" in text
    assert "bottle was not re-observed" in text and "not assumed removed" in text
    assert "Sources now disagree about Microscope M17's configuration" in text

    cont = svc.agent.answer("Continue.", now)
    assert cont.abstained and cont.requested_observation.instruction == "Move closer so I can read the model number on pump_new."

    summary = build_summary(svc, now)
    c4 = next(e for e in summary.entities if e.entity_id == "c4")
    assert c4.location is None and "confirmed absent" in c4.location_note
    assert summary.counts["open_conflicts"] == 1 and summary.conflicts[0]["attribute"] == "configuration"
    assert [s.status for s in summary.tasks[0].steps] == ["COMPLETED", "COMPLETED", "BLOCKED", "BLOCKED"]
    assert summary.requested_observations and summary.changes


def test_ui_and_perception_api(client):
    page = client.get("/ui/")
    assert page.status_code == 200 and "ORBIT Inspector" in page.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/dashboard", follow_redirects=False).status_code == 307

    body = {
        "frame": {"device_id": "webcam", "timestamp": T0.isoformat(), "content_hash": "sha256:00", "field_of_view": "bench"},
        "detections": [{"label": "tool", "confidence": 0.9, "bbox": [0.1, 0.1, 0.2, 0.2]}],
        "region_map": {"bench_left": [0, 0, 0.5, 1]},
    }
    res = client.post("/perception/frames", json=body).json()
    assert res["observation"]["observed_entities"][0]["location"] == "bench_left"
    assert res["events"][0]["event_type"] == "OBJECT_ADDED"
    obs_id = res["observation"]["id"]
    red = client.post(f"/observations/{obs_id}/redact", json={"reason": "privacy"}).json()
    assert red["raw_reference"] is None
    assert client.post("/observations/nope/redact", json={"reason": "x"}).status_code == 404
    summary = client.get("/inspect/summary").json()
    assert summary["counts"]["entities"] == 1 and summary["entities"][0]["location"] == "bench_left"
