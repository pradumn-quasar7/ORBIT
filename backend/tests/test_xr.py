"""Phase 18: mixed-reality places, device pairing for LAN access, the dual-listener launcher."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.pairing import ALPHABET, new_code
from backend.app.domain.models import Observation, ObservedEntity
from backend.app.main import create_app
from backend.app.repositories.in_memory_repository import InMemoryRepository

ROOT = Path(__file__).resolve().parents[2]


def world(client, clock):
    svc = client.app.state.orbit
    for a, p, kind in (("lab", None, "room"), ("bench_3", "lab", "surface"), ("shelf", "lab", "surface")):
        svc.anchors.register(a, clock.now(), parent_id=p, anchor_type=kind)
    svc.engine.record_observation(Observation(id="o1", timestamp=clock.now(), source="camera", observed_entities=[
        ObservedEntity(candidate_entity_id="valve", type="valve", location="bench_3", attributes={"state": "closed"}),
        ObservedEntity(candidate_entity_id="m17", type="microscope", name="Microscope M17", location="bench_3"),
    ]))
    svc.engine.assert_claim("m17", "location", "shelf", source="digital_registry", timestamp=clock.now())  # disagreement


def test_places_carry_only_supportable_beliefs(client, clock):
    world(client, clock)
    places = {p["id"]: p for p in client.get("/xr/places").json()}
    bench = places["bench_3"]
    assert bench["anchor_type"] == "surface" and bench["xr"] is None
    items = {i["entity_id"]: i for i in bench["items"]}
    assert items["valve"]["status"] == "OBSERVED" and items["valve"]["state"] == {"state": "closed"}
    assert items["m17"]["name"] == "Microscope M17" and items["m17"]["status"] == "CONTRADICTED"
    assert items["m17"]["value"] is None  # a contested location is never presented as fact
    assert bench["conflicts"] >= 1
    assert places["shelf"]["items"] == [] or all(i["status"] != "OBSERVED" for i in places["shelf"]["items"])


def test_pin_and_unpin_a_place(client, clock):
    world(client, clock)
    pinned = client.put("/xr/places/bench_3", json={"handle": "6f1d-anchor", "device": "quest"})
    assert pinned.status_code == 200 and pinned.json()["xr"]["handle"] == "6f1d-anchor"
    anchor = client.get("/anchors/bench_3").json()["anchor"]
    assert anchor["frame"]["xr"]["device"] == "quest" and anchor["parent_id"] == "lab"  # the place itself is unchanged
    assert client.delete("/xr/places/bench_3").json()["xr"] is None
    assert client.put("/xr/places/nowhere", json={"handle": "h"}).status_code == 404
    assert client.put("/xr/places/bench_3", json={"handle": ""}).status_code == 422


# -------------------------------------------------------------------- pairing
def lan_client(code="QT6X-D47E"):
    app = create_app(InMemoryRepository(), pair_code=code, lan_url="https://10.0.0.5:8766/ui/xr.html")
    return TestClient(app, base_url="https://10.0.0.5:8766", client=("10.0.0.9", 50000)), TestClient(app, client=("127.0.0.1", 50000))


def test_network_devices_must_pair_and_the_laptop_need_not():
    remote, laptop = lan_client()
    assert laptop.get("/assistant").status_code == 200
    assert laptop.get("/xr/pairing").json()["code"] == "QT6X-D47E"
    assert remote.get("/assistant").status_code == 401
    page = remote.get("/ui/xr.html", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/pair?next=/ui/xr.html"
    assert "Pair with ORBIT" in remote.get("/pair").text

    assert remote.post("/pair", data={"code": "WRONG-WRNG"}, follow_redirects=False).status_code == 403
    ok = remote.post("/pair", data={"code": "qt6x d47e", "next": "/ui/xr.html"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/ui/xr.html"
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
    assert "QT6X" not in cookie and "D47E" not in cookie  # the cookie is derived, not the code
    assert remote.get("/assistant").status_code == 200
    assert remote.get("/xr/pairing").json()["code"] is None  # never shown to network devices


def test_pairing_rejects_offsite_redirects_and_rate_limits():
    remote, _ = lan_client()
    r = remote.post("/pair", data={"code": "QT6X-D47E", "next": "https://evil.example/"}, follow_redirects=False)
    assert r.headers["location"] == "/ui/xr.html"
    remote2, _ = lan_client()
    for _ in range(8):
        assert remote2.post("/pair", data={"code": "AAAA-AAAA"}).status_code == 403
    assert remote2.post("/pair", data={"code": "QT6X-D47E"}).status_code == 429  # locked out for a minute


def test_without_a_code_nothing_changes(client):
    assert client.get("/assistant").status_code == 200  # the test client is not loopback, and still allowed


def test_codes_are_typeable():
    code = new_code()
    assert len(code) == 9 and code[4] == "-" and all(c in ALPHABET for c in code.replace("-", ""))
    assert not set("01OI") & set(code)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl missing")
def test_self_signed_certificate_names_the_lan_address(tmp_path, monkeypatch):
    from backend.app import serve
    monkeypatch.setattr(serve, "RUN", tmp_path)
    key, crt = serve.certificate("10.0.0.5")
    text = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-text"], capture_output=True, text=True).stdout
    assert "IP Address:10.0.0.5" in text and Path(key).stat().st_mode & 0o077 == 0
    assert serve.certificate("10.0.0.5") == [key, crt]  # reused while the address is the same


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_headset_view_logic():
    proc = subprocess.run(["node", "--test", "frontend/tests/xr_core.test.js"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pairing_code_survives_restarts_until_renewed(tmp_path, monkeypatch):
    from backend.app import serve
    monkeypatch.setattr(serve, "RUN", tmp_path)
    monkeypatch.delenv("ORBIT_PAIR_CODE", raising=False)
    first = serve.pairing_code()
    assert serve.pairing_code() == first
    assert serve.pairing_code(renew=True) != first
    assert (tmp_path / "pair-code").stat().st_mode & 0o077 == 0


# ------------------------------------------------------------ server voice
@pytest.mark.skipif(not __import__("backend.app.api.speech", fromlist=["available"]).available(), reason="no macOS `say`")
def test_server_speaks_for_clients_without_a_voice(client, tmp_path, monkeypatch):
    from backend.app.api import speech
    monkeypatch.setattr(speech, "CACHE", tmp_path)
    r = client.get("/speech", params={"text": "The valve is open. [[slnc 5000]]"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert len(list(tmp_path.glob("*.wav"))) == 1
    assert client.get("/speech", params={"text": "The valve is open. [[slnc 5000]]"}).content == r.content  # cached
    assert client.get("/speech", params={"text": "x" * 401}).status_code == 422


def test_no_system_voice_is_reported(client, monkeypatch):
    from backend.app.api import speech
    monkeypatch.setattr(speech, "available", lambda: False)
    assert client.get("/speech", params={"text": "hi"}).status_code == 501
