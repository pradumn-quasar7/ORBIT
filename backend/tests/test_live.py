"""Phase 19: realtime voice with Gemini Live — device actions, session setup, tools, consent."""
import json
import subprocess
from datetime import timedelta

import pytest

from backend.app.core.secrets import load_env
from backend.app.domain.types import ActionStatus
from backend.app.services.devices import DeviceController, DeviceError
from backend.app.services.live import LiveService, LiveSettings
from backend.tests.test_assistant import Lab, at


class FakeShell:
    """Records commands instead of running them; answers like adb/open would."""

    def __init__(self, quest=True, mac_apps=("WhatsApp",), packages=("com.whatsapp", "com.oculus.browser")):
        self.calls = []
        self.quest, self.mac_apps, self.packages = quest, set(mac_apps), packages

    def __call__(self, cmd):
        self.calls.append(cmd)
        out, code = "", 0
        if cmd[:2] == ["adb", "devices"]:
            out = "List of devices attached\n" + ("340YC10G9S0Z4T\tdevice\n" if self.quest else "")
        elif cmd[:4] == ["adb", "shell", "pm", "list"]:
            out = "\n".join(f"package:{p}" for p in self.packages)
        elif cmd[:2] == ["open", "-Ra"]:
            code = 0 if cmd[2] in self.mac_apps else 1
        return subprocess.CompletedProcess(cmd, code, out, "")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


@pytest.fixture
def shell():
    return FakeShell()


@pytest.fixture
def devices(shell, tmp_path):
    (tmp_path / "contacts.json").write_text(json.dumps({
        "Mom": "+91 98765 43210",
        "Prof Rao": {"phone": "+91 90000 11111", "email": "rao@uni.edu"},
        "Asha": "asha@example.com",
    }))
    return DeviceController(runner=shell, platform="darwin", contacts_file=tmp_path / "contacts.json", sleep=lambda s: None)


# -------------------------------------------------------------------- devices
def test_open_apps_on_each_device(devices, shell):
    assert devices.open_app("WhatsApp", "quest") == "Opened WhatsApp on the Quest."
    assert shell.ran("adb", "shell", "monkey", "-p", "com.whatsapp")
    assert "Quest browser" in devices.open_app("instagram", "quest")  # no Quest app: its website
    assert ["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "https://www.instagram.com", "com.oculus.browser"] in shell.calls
    assert devices.open_app("whats app", "mac") == "Opened WhatsApp on the Mac."
    assert ["open", "-a", "WhatsApp"] in shell.calls
    assert "browser" in devices.open_app("insta", "mac")  # not installed on the Mac: the website
    assert ["open", "https://www.instagram.com"] in shell.calls


def test_search_and_websites_are_safe(devices, shell):
    devices.search("best 3d printer under 20000", "mac")
    assert ["open", "https://www.google.com/search?q=best%203d%20printer%20under%2020000"] in shell.calls
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x", "not a url"):
        with pytest.raises(DeviceError):
            devices.open_url(bad, "mac")
    with pytest.raises(DeviceError):
        devices.open_app("photoshop", "quest")  # not installed, no website known


def test_quest_must_be_connected():
    d = DeviceController(runner=FakeShell(quest=False), platform="darwin")
    with pytest.raises(DeviceError, match="isn't connected"):
        d.open_app("whatsapp", "quest")
    assert d.available() == {"quest": False, "mac": True}


def test_numbers_and_contacts(devices):
    assert devices.resolve_number("+91 98765 43210") == "919876543210"
    assert devices.resolve_number("mom") == "919876543210"
    with pytest.raises(DeviceError, match="contacts.json"):
        devices.resolve_number("Rahul")


# ----------------------------------------------------------------------- live
class FakeGoogle:
    def __init__(self):
        self.requests = []

    def __call__(self, url, body, headers):
        self.requests.append((url, body, headers))
        return {"name": "auth_tokens/ephemeral-123"}


@pytest.fixture
def live(repo, devices):
    lab = Lab(repo)
    google = FakeGoogle()
    svc = LiveService(lab.svc.assistant, devices, LiveSettings(api_key="AIza-test", model="gemini-3.8-live"), transport=google)
    svc.lab, svc.google = lab, google
    return svc


def test_not_configured_without_a_key(repo, devices):
    lab = Lab(repo)
    svc = LiveService(lab.svc.assistant, devices, LiveSettings(api_key=None))
    assert svc.status()["configured"] is False
    with pytest.raises(Exception, match=".env"):
        svc.start("ana", "mac", at(1))


def test_session_uses_a_single_use_token_and_never_the_key(live):
    s = live.start("ana", "quest", at(1))
    url, body, headers = live.google.requests[0]
    assert url.endswith("/v1beta/auth_tokens") and headers == {"x-goog-api-key": "AIza-test"}
    assert body["uses"] == 1 and body["bidiGenerateContentSetup"]["model"] == "models/gemini-3.8-live"
    assert "BidiGenerateContentConstrained?access_token=auth_tokens/ephemeral-123" in s["ws_url"]
    assert "AIza" not in json.dumps(s)  # the browser never sees the API key
    setup = s["setup"]["setup"]
    assert {t["name"] for t in setup["tools"][0]["functionDeclarations"]} == {
        "orbit", "open_app", "web_search", "open_website", "compose_email", "prepare_message", "send_message",
        "play_video", "video_control"}
    text = setup["systemInstruction"]["parts"][0]["text"]
    assert "m17" in text and "bench_4" in text and "the Quest headset" in text and "Never guess facts" in text
    assert setup["inputAudioTranscription"] == {} and setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert s["conversation_id"] in live.assistant.conversations


def test_orbit_tool_answers_from_evidence(live):
    s = live.start("ana", "mac", at(1))
    r = live.call("orbit", {"request": "where is m17"}, s["conversation_id"], "mac", "where's the microscope", at(2))
    assert r["ok"] and "bench_4" in r["answer"]
    turn = live.assistant.get(s["conversation_id"]).turns[-1]
    assert turn.user_text == "where is m17" and turn.heard == "where's the microscope"


@pytest.mark.parametrize("heard, approved", [("yes", True), ("Yes please.", True), ("ok so like", False), ("", False)])
def test_consent_comes_only_from_the_users_own_words(live, repo, heard, approved):
    s = live.start("ana", "mac", at(1))
    cid = s["conversation_id"]
    ask = live.call("orbit", {"request": "open valve"}, cid, "mac", "can you open the valve", at(2))
    assert "waiting_for_user_consent" in ask
    action_id = live.assistant.get(cid).pending.action_id
    r = live.call("orbit", {"request": "yes"}, cid, "mac", heard, at(2.5))  # the model says "yes" either way
    status = repo.get_action(action_id).status
    assert (status == ActionStatus.AUTHORIZED) is approved
    if not approved:
        assert "need to hear the yes from you" in r["answer"] and live.assistant.get(cid).pending is not None


def test_device_tools(live, shell):
    s = live.start("ana", "quest", at(1))
    r = live.call("open_app", {"app": "WhatsApp", "device": "here"}, s["conversation_id"], "quest", "open whatsapp", at(2))
    assert r == {"ok": True, "result": "Opened WhatsApp on the Quest."}
    r = live.call("web_search", {"query": "orbit world model", "device": "mac"}, s["conversation_id"], "quest", "", at(2))
    assert r["ok"] and ["open", "https://www.google.com/search?q=orbit%20world%20model"] in shell.calls
    r = live.call("open_website", {"url": "youtube.com"}, s["conversation_id"], "mac", "", at(2))
    assert ["open", "https://youtube.com"] in shell.calls
    assert live.call("open_website", {"url": "javascript:alert(1)"}, s["conversation_id"], "mac", "", at(2))["ok"] is False
    assert live.call("rm_rf", {}, s["conversation_id"], "mac", "", at(2))["ok"] is False


def test_messages_are_sent_only_after_the_users_yes(live, shell):
    s = live.start("ana", "mac", at(1))
    cid = s["conversation_id"]
    prep = live.call("prepare_message", {"to": "Mom", "text": "Reached the lab"}, cid, "mac", "tell mom I reached the lab", at(2))
    assert prep["ok"] and "Shall I send it?" in prep["read_back"]
    assert not [c for c in shell.calls if c[0] in ("osascript",) or (c[0] == "open" and "whatsapp" in c[-1])]  # nothing sent yet

    refused = live.call("send_message", {"message_id": prep["message_id"]}, cid, "mac", "hmm wait", at(2.2))
    assert refused["ok"] is False and "hasn't said yes" in refused["error"]

    sent = live.call("send_message", {"message_id": prep["message_id"]}, cid, "mac", "yes send it", at(2.4))
    assert sent == {"ok": True, "result": "Sent to Mom."}
    assert ["open", "whatsapp://send?phone=919876543210&text=Reached%20the%20lab"] in shell.calls
    assert any(c[0] == "osascript" and 'tell application "WhatsApp" to activate' in c[2] for c in shell.calls)
    assert live.call("send_message", {"message_id": prep["message_id"]}, cid, "mac", "yes", at(2.5))["ok"] is False  # once
    assert "message" in [d.kind for d in live.assistant.get(cid).delegated]


def test_quest_messages_are_opened_for_the_user_to_send(live, shell):
    s = live.start("ana", "quest", at(1))
    prep = live.call("prepare_message", {"to": "+91 98765 43210", "text": "hi"}, s["conversation_id"], "quest", "", at(2))
    r = live.call("send_message", {"message_id": prep["message_id"]}, s["conversation_id"], "quest", "yes", at(2.2))
    assert r["ok"] and "Tap send" in r["result"]
    assert ["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "https://wa.me/919876543210?text=hi", "com.whatsapp"] in shell.calls
    assert not shell.ran("osascript")


def test_prepared_messages_expire(live):
    s = live.start("ana", "mac", at(1))
    prep = live.call("prepare_message", {"to": "Mom", "text": "x"}, s["conversation_id"], "mac", "", at(2))
    late = live.call("send_message", {"message_id": prep["message_id"]}, s["conversation_id"], "mac", "yes", at(2) + timedelta(minutes=3))
    assert late["ok"] is False and "expired" in late["error"]


# ------------------------------------------------------------------------ API
def test_live_api(client):
    st = client.get("/live/status").json()
    assert st["configured"] in (True, False) and st["model"]
    r = client.post("/live/tool", json={"conversation_id": "nope", "name": "open_app", "args": {"app": "x"}})
    assert r.status_code == 404
    client.app.state.orbit.live.settings = LiveSettings(api_key=None)
    assert client.post("/live/session", json={"device": "mac"}).status_code == 503
    assert client.post("/live/session", json={"device": "phone"}).status_code == 422


def test_env_file_is_loaded_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# key\nGEMINI_API_KEY=AIza-from-file\nexport GEMINI_VOICE='Puck'\nEMPTY=\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_VOICE", "Kore")
    assert load_env(env) == 1
    import os
    assert os.environ["GEMINI_API_KEY"] == "AIza-from-file" and os.environ["GEMINI_VOICE"] == "Kore"
    monkeypatch.delenv("GEMINI_API_KEY")


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not installed")
def test_live_audio_core():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(["node", "--test", "frontend/tests/live_core.test.js"], cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------- email
def test_email_is_written_as_a_gmail_draft_never_sent(live, shell):
    from urllib.parse import parse_qs, urlparse
    s = live.start("ana", "mac", at(1))
    r = live.call("compose_email", {"to": "Prof Rao", "subject": "Running late today",
                                    "body": "Dear Prof. Rao,\n\nI'll be 15 minutes late to the lab meeting.\n\nBest,\nAna"},
                  s["conversation_id"], "mac", "email professor rao that I'll be late", at(2))
    assert r["ok"] and "ready for you to review and press Send" in r["result"]
    opened = [c for c in shell.calls if c[0] == "open" and "mail.google.com" in c[-1]]
    assert len(opened) == 1
    q = parse_qs(urlparse(opened[0][-1]).query)
    assert q["view"] == ["cm"] and q["to"] == ["rao@uni.edu"] and q["su"] == ["Running late today"]
    assert q["body"][0].startswith("Dear Prof. Rao,\n\nI'll be 15 minutes late")
    assert not shell.ran("osascript")  # nothing is ever sent automatically
    assert "email_draft" in [d.kind for d in live.assistant.get(s["conversation_id"]).delegated]


def test_email_recipients_and_devices(live, shell):
    s = live.start("ana", "quest", at(1))
    r = live.call("compose_email", {"to": "Asha, boss@corp.com", "subject": "Hi", "body": "Hello", "cc": "mom2@example.com"},
                  s["conversation_id"], "quest", "", at(2))
    assert r["ok"] and "in the headset" in r["result"]
    url = [c for c in shell.calls if c[:2] == ["adb", "shell"] and "mail.google.com" in " ".join(c)][0][-2]
    assert "to=asha%40example.com%2Cboss%40corp.com" in url and "cc=mom2%40example.com" in url
    bad = live.call("compose_email", {"to": "Rahul", "subject": "x", "body": "y"}, s["conversation_id"], "mac", "", at(2))
    assert bad["ok"] is False and "contacts.json" in bad["error"]
    assert live.call("compose_email", {"to": "asha@example.com", "subject": "x", "body": ""}, s["conversation_id"], "mac", "", at(2))["ok"] is False
    assert live.devices.resolve_number("prof rao") == "919000011111"  # phone still found in the richer format
