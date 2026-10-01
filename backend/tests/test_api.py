from datetime import datetime, timezone


def _ts(hour, minute=0):
    return datetime(2026, 9, 30, hour, minute, 0, tzinfo=timezone.utc).isoformat()


def test_api_health_check(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "ORBIT" in data["system"]


def test_api_entity_and_observation_flow(client):
    obs_payload = {
        "id": "obs_api_001",
        "timestamp": _ts(10),
        "source": "camera",
        "observed_entities": [
            {
                "candidate_entity_id": "laptop_99",
                "type": "laptop",
                "location": "bench_left",
                "attributes": {"screen": "on"},
            }
        ],
    }
    obs_res = client.post("/observations", json=obs_payload)
    assert obs_res.status_code == 200
    events = obs_res.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "OBJECT_ADDED"

    ent = client.get("/entities/laptop_99").json()
    assert ent["id"] == "laptop_99"
    assert ent["current_state"]["location"] == "bench_left"
    assert ent["status"] == "OBSERVED"  # a camera sighting is not VERIFIED

    obs_payload_2 = dict(obs_payload, id="obs_api_002", timestamp=_ts(10, 30))
    obs_payload_2["observed_entities"] = [dict(obs_payload["observed_entities"][0], location="bench_right")]
    events_2 = client.post("/observations", json=obs_payload_2).json()
    assert [e["event_type"] for e in events_2] == ["OBJECT_MOVED"]

    history = client.get("/entities/laptop_99/history?attribute=location").json()
    assert [h["value"] for h in history] == ["bench_left", "bench_right"]

    diff = client.post(
        "/world/diff", json={"baseline_timestamp": _ts(9, 59), "target_timestamp": _ts(10, 35)}
    ).json()
    assert "OBJECT_MOVED" in [c["change_type"] for c in diff["changes"]]


def test_duplicate_observation_is_rejected(client):
    payload = {"id": "obs_dup", "timestamp": _ts(10), "source": "camera", "observed_entities": []}
    assert client.post("/observations", json=payload).status_code == 200
    assert client.post("/observations", json=payload).status_code == 409


def test_post_entity_is_backed_by_evidence(client):
    res = client.post(
        "/entities",
        json={
            "id": "m17",
            "type": "microscope",
            "name": "Microscope M17",
            "location": "bench_3",
            "attributes": {"configuration": "R6"},
            "source": "digital_registry",
            "authority": 1.0,
            "timestamp": _ts(9),
        },
    )
    assert res.status_code == 201
    entity = res.json()
    assert entity["status"] == "VERIFIED"
    assert len(entity["evidence_refs"]) == 1
    history = client.get("/entities/m17/history").json()
    assert {h["attribute"] for h in history} == {"location", "configuration"}
    assert all(h["supported_by"] == entity["evidence_refs"] for h in history)

    dup = client.post("/entities", json={"id": "m17", "type": "microscope"})
    assert dup.status_code == 409


def test_unknown_entity_returns_404(client):
    assert client.get("/entities/nope").status_code == 404
    assert client.get("/entities/nope/history").status_code == 404
    assert client.post("/entities/nope/freshness", json={}).status_code == 404
