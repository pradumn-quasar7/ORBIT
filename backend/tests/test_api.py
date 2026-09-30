from fastapi.testclient import TestClient
from datetime import datetime, timezone
import pytest

from backend.app.main import app

client = TestClient(app)

def test_api_health_check():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "ORBIT" in data["system"]

def test_api_entity_and_observation_flow():
    # 1. Post Observation
    obs_payload = {
        "id": "obs_api_001",
        "timestamp": datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc).isoformat(),
        "source": "camera",
        "observed_entities": [
            {
                "candidate_entity_id": "laptop_99",
                "type": "laptop",
                "location": "bench_left",
                "attributes": {"screen": "on"}
            }
        ]
    }
    obs_res = client.post("/observations", json=obs_payload)
    assert obs_res.status_code == 200
    events = obs_res.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "OBJECT_ADDED"

    # 2. Get Entity
    ent_res = client.get("/entities/laptop_99")
    assert ent_res.status_code == 200
    ent = ent_res.json()
    assert ent["id"] == "laptop_99"
    assert ent["current_state"]["location"] == "bench_left"

    # 3. Post Observation with Movement
    obs_payload_2 = {
        "id": "obs_api_002",
        "timestamp": datetime(2026, 9, 30, 10, 30, 0, tzinfo=timezone.utc).isoformat(),
        "source": "camera",
        "observed_entities": [
            {
                "candidate_entity_id": "laptop_99",
                "type": "laptop",
                "location": "bench_right",
                "attributes": {"screen": "on"}
            }
        ]
    }
    obs_res_2 = client.post("/observations", json=obs_payload_2)
    assert obs_res_2.status_code == 200
    events_2 = obs_res_2.json()
    assert len(events_2) == 1
    assert events_2[0]["event_type"] == "OBJECT_MOVED"

    # 4. Check Entity History
    hist_res = client.get("/entities/laptop_99/history?attribute=location")
    assert hist_res.status_code == 200
    history = hist_res.json()
    assert len(history) == 2
    assert history[0]["value"] == "bench_left"
    assert history[1]["value"] == "bench_right"

    # 5. Compute World Diff
    diff_res = client.post(
        "/world/diff",
        json={
            "baseline_timestamp": datetime(2026, 9, 30, 9, 59, 0, tzinfo=timezone.utc).isoformat(),
            "target_timestamp": datetime(2026, 9, 30, 10, 35, 0, tzinfo=timezone.utc).isoformat(),
        }
    )
    assert diff_res.status_code == 200
    diff = diff_res.json()
    change_types = [c["change_type"] for c in diff["changes"]]
    assert "OBJECT_MOVED" in change_types
