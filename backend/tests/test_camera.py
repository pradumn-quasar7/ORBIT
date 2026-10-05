"""Phase 15: live webcam perception — calibration, snapshots, scans, browser core logic."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGIONS = [
    {"id": "desk_left", "name": "left of desk", "bbox": [0.0, 0.0, 0.5, 1.0]},
    {"id": "desk_right", "bbox": [0.5, 0.0, 1.0, 1.0]},
]


def det(label, x, conf=0.9, **extra):
    """A detection centred horizontally at x (normalised)."""
    return {"label": label, "confidence": conf, "bbox": [x - 0.05, 0.4, x + 0.05, 0.6], **extra}


def calibrate(client, regions=REGIONS, view="desk", **extra):
    res = client.put("/cameras/webcam/config", json={"view": view, "regions": regions, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def location(client, entity_id):
    return client.get(f"/entities/{entity_id}/location").json()["location"]


def test_calibration_lives_in_anchor_frames(client):
    cfg = calibrate(client, room="lab")
    assert cfg["view"] == "desk"
    assert [r["id"] for r in cfg["regions"]] == ["desk_left", "desk_right"]
    left = client.get("/anchors/desk_left").json()["anchor"]
    assert left["parent_id"] == "desk" and left["anchor_type"] == "region"
    assert left["frame"] == {"camera": "webcam", "bbox": [0.0, 0.0, 0.5, 1.0]}
    desk = client.get("/anchors/desk").json()["anchor"]
    assert desk["frame"]["camera_view"] == "webcam" and desk["parent_id"] == "lab"

    # dropping a region removes its calibration, not the place (memory about it stays)
    cfg = calibrate(client, regions=REGIONS[:1])
    assert [r["id"] for r in cfg["regions"]] == ["desk_left"]
    right = client.get("/anchors/desk_right").json()["anchor"]
    assert "bbox" not in right["frame"] and right["parent_id"] == "desk"
    assert client.get("/cameras/webcam/config").json()["regions"][0]["id"] == "desk_left"


def test_region_cannot_be_the_whole_view(client):
    res = client.put("/cameras/webcam/config", json={"view": "desk", "regions": [{"id": "desk", "bbox": [0, 0, 1, 1]}]})
    assert res.status_code == 422


def test_uncalibrated_camera_is_rejected(client):
    assert client.get("/cameras/cam9/config").json() == {"device_id": "cam9", "view": None, "regions": []}
    assert client.post("/cameras/cam9/snapshot", json={"detections": [det("cup", 0.2)]}).status_code == 409
    assert client.post("/cameras/cam9/scan", json={"region": "desk", "detections": []}).status_code == 409


def test_snapshot_places_objects_by_region_and_never_sends_people(client):
    calibrate(client)
    res = client.post("/cameras/webcam/snapshot", json={
        "session_id": "webcam-1",
        "detections": [
            det("cup", 0.2, attributes={"color": "red"}),
            det("laptop", 0.8),
            det("person", 0.5, conf=0.99),
            det("bottle", 0.3, conf=0.2),  # below the confidence floor
        ],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dropped"] == 2
    seen = {s["label"]: s for s in body["seen"]}
    assert set(seen) == {"cup", "laptop"}
    assert seen["cup"]["location"] == "desk_left" and seen["laptop"]["location"] == "desk_right"
    assert all(s["method"] == "NEW" for s in body["seen"])

    cup = seen["cup"]["entity_id"]
    loc = location(client, cup)
    assert loc["last_known_value"] == "desk_left" and loc["status"] == "OBSERVED"
    state = client.get(f"/entities/{cup}/state").json()
    assert state["attributes"]["color"]["last_known_value"] == "red"

    obs = client.get("/sessions/webcam-1/observations").json()
    assert len(obs) == 1 and obs[0]["spatial_context"]["field_of_view"] == "desk" and obs[0]["source"] == "webcam"
    assert all(e["type"] != "person" for e in obs[0]["observed_entities"])


def test_qr_marker_is_a_stable_identity(client, clock):
    calibrate(client)
    first = client.post("/cameras/webcam/snapshot", json={"detections": [det("cup", 0.2, marker_id="mug_7")]}).json()
    assert first["seen"][0]["entity_id"] == "mug_7"
    clock.advance(120)
    moved = client.post("/cameras/webcam/snapshot", json={"detections": [det("cup", 0.8, marker_id="mug_7")]}).json()
    assert moved["seen"][0]["entity_id"] == "mug_7"
    assert any(e["event_type"] == "OBJECT_MOVED" for e in moved["events"])
    assert location(client, "mug_7")["last_known_value"] == "desk_right"


def test_unchanged_heartbeat_corroborates_without_new_events(client, clock):
    calibrate(client)
    first = client.post("/cameras/webcam/snapshot", json={"detections": [det("laptop", 0.8)]}).json()
    laptop = first["seen"][0]["entity_id"]
    clock.advance(60)
    again = client.post("/cameras/webcam/snapshot", json={"detections": [det("laptop", 0.8)]}).json()
    assert again["seen"][0]["entity_id"] == laptop
    assert not any(e["event_type"] in ("OBJECT_ADDED", "OBJECT_MOVED") for e in again["events"])
    assert len(client.get(f"/entities/{laptop}/claims/location").json()["evidence_refs"]) == 2


def test_scan_confirms_absence_only_inside_the_region(client, clock):
    calibrate(client)
    seen = client.post("/cameras/webcam/snapshot", json={
        "detections": [det("cup", 0.2, marker_id="mug_7"), det("laptop", 0.8, marker_id="lap_1")],
    }).json()
    assert {s["entity_id"] for s in seen["seen"]} == {"mug_7", "lap_1"}
    clock.advance(300)
    # the mug was taken; a deliberate look at desk_left finds nothing there
    cov = client.post("/cameras/webcam/scan", json={"region": "desk_left", "detections": [det("laptop", 0.8, marker_id="lap_1")]})
    assert cov.status_code == 200, cov.text
    cov = cov.json()
    assert cov["confirmed_absent"] == ["mug_7"] and cov["found"] == [] and cov["source"] == "webcam"
    assert location(client, "lap_1")["last_known_value"] == "desk_right"  # outside the scan: untouched
    gone = location(client, "mug_7")
    assert (gone["status"], gone["has_current_claim"]) == ("UNKNOWN", False)
    assert "confirmed absent from desk_left" in gone["reason"]

    assert client.post("/cameras/webcam/scan", json={"region": "kitchen", "detections": []}).status_code == 422


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_camera_core():
    """The browser's tracker / marker / signature logic (frontend/camera_core.js)."""
    proc = subprocess.run(["node", "--test", "frontend/tests/camera_core.test.js"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_neighbouring_neutral_colours_do_not_split_one_object(client, clock):
    """Phase 15 live test: one black mouse read as gray, then black, became two mice."""
    calibrate(client)
    first = client.post("/cameras/webcam/snapshot", json={"detections": [det("mouse", 0.2, attributes={"color": "gray"})]}).json()
    clock.advance(30)
    again = client.post("/cameras/webcam/snapshot", json={"detections": [det("mouse", 0.2, attributes={"color": "black"})]}).json()
    assert again["seen"][0]["entity_id"] == first["seen"][0]["entity_id"]
    assert again["seen"][0]["method"] != "NEW"
    clock.advance(30)  # black vs white is a real difference: a second mouse
    other = client.post("/cameras/webcam/snapshot", json={"detections": [
        det("mouse", 0.2, attributes={"color": "black"}), det("mouse", 0.8, attributes={"color": "white"})]}).json()
    assert len({s["entity_id"] for s in other["seen"]}) == 2
