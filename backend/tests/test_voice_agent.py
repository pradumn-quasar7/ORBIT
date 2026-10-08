"""Phase 19.1: hosted speech-to-speech on Groq — hearing, thinking with ORBIT's tools, speaking."""
import json

import pytest

from backend.app.domain.types import ActionStatus
from backend.app.services.live import LiveService, LiveSettings
from backend.app.services.voice_agent import VoiceAgent, VoiceError
from backend.tests.test_assistant import Lab, at
from backend.tests.test_live import FakeShell
from backend.app.services.devices import DeviceController


class FakeGroq:
    """Scripted Groq: a transcript, then chat replies in order (tool calls or text)."""

    def __init__(self, heard="where is the microscope", replies=(), status=None):
        self.heard, self.replies, self.status = heard, list(replies), status or {}
        self.calls = []

    def __call__(self, url, body, headers, file=None):
        self.calls.append((url.rsplit("/", 2)[-2:], json.loads(json.dumps(body)), headers, file))  # a snapshot
        if "transcriptions" in url:
            return 200, json.dumps({"text": self.heard, "language": "english"}).encode()
        model = body["model"]
        if model in self.status:
            return self.status[model], json.dumps({"error": {"message": f"{model} unavailable"}}).encode()
        return 200, json.dumps({"choices": [{"message": self.replies.pop(0)}]}).encode()


def tool_call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def lab(repo):
    return Lab(repo)


def agent(lab, groq, shell=None):
    devices = DeviceController(runner=shell or FakeShell(), platform="darwin", sleep=lambda s: None)
    live = LiveService(lab.svc.assistant, devices, LiveSettings(api_key=None))
    return VoiceAgent(live, api_key="gsk_test", transport=groq)


def test_a_turn_hears_thinks_with_orbit_and_answers(lab):
    groq = FakeGroq("where's the microscope?", [tool_call("orbit", request="where is m17"), say("It's on bench 4, seen a minute ago.")])
    va = agent(lab, groq)
    conv = lab.svc.assistant.start("ana", at(1))
    out = va.turn(conv.id, "ana", "quest", b"RIFF....", "audio/wav", at(2))
    assert out.heard == "where's the microscope?" and out.reply.startswith("It's on bench 4")
    assert out.tools[0]["name"] == "orbit" and "bench_4" in out.tools[0]["result"]["answer"]  # the fact came from ORBIT
    (_, stt_body, stt_headers, stt_file) = groq.calls[0]
    assert stt_body["model"] == "whisper-large-v3-turbo" and stt_headers["Authorization"] == "Bearer gsk_test"
    assert stt_file[0] == "speech.wav" and stt_file[2] == "audio/wav"
    chat = groq.calls[1][1]
    assert chat["model"] == "openai/gpt-oss-120b" and {t["function"]["name"] for t in chat["tools"]} >= {"orbit", "open_app", "send_message"}
    assert "m17" in chat["messages"][0]["content"] and "the Quest headset" in chat["messages"][0]["content"]
    assert groq.calls[2][1]["messages"][-1]["role"] == "tool"  # the tool result went back to the model


def test_history_carries_the_conversation(lab):
    groq = FakeGroq("and the notebook?", [say("First."), say("Second.")])
    va = agent(lab, groq)
    conv = lab.svc.assistant.start("ana", at(1))
    va.turn(conv.id, "ana", "mac", b"x", "audio/wav", at(2))
    va.turn(conv.id, "ana", "mac", b"x", "audio/wav", at(3))
    msgs = groq.calls[-1][1]["messages"]
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"] and msgs[2]["content"] == "First."


def test_tool_calls_stay_in_history(lab):
    groq = FakeGroq("pause it", [tool_call("open_app", app="youtube"), say("Hello there.")])  # the action is spoken directly
    va = agent(lab, groq)
    cid = lab.svc.assistant.start("ana", at(1)).id
    va.respond(cid, "ana", "mac", "open youtube", at(2))
    va.respond(cid, "ana", "mac", "hello", at(3))
    roles = [m["role"] for m in groq.calls[-1][1]["messages"][1:]]
    assert roles == ["user", "assistant", "tool", "assistant", "user"]  # the model sees how actions were done


def test_a_claimed_action_without_a_tool_is_made_real(lab):
    groq = FakeGroq("pause it", [say("Paused. Let me know when to resume."), tool_call("video_control", action="pause"), say("Paused.")])
    va = agent(lab, groq)
    va.live.media.control = lambda action, value, device: {"ok": True, "result": "Paused."}
    out = va.respond(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", "pause it", at(2))
    assert [t["name"] for t in out.tools] == ["video_control"] and out.reply == "Paused."
    chats = [c for c in groq.calls if c[0][-1] == "completions"]
    assert chats[1][1]["tool_choice"] == "required" and "without calling a tool" in chats[1][1]["messages"][-1]["content"]


def test_device_actions_through_groq(lab):
    shell = FakeShell()
    groq = FakeGroq("open whatsapp in the headset", [tool_call("open_app", app="whatsapp", device="quest")])
    out = agent(lab, groq, shell).turn(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", b"x", "audio/wav", at(2))
    assert out.tools[0]["result"] == {"ok": True, "result": "Opened WhatsApp on the Quest."}
    assert shell.ran("adb", "shell", "monkey", "-p", "com.whatsapp")


@pytest.mark.parametrize("heard, approved", [("yes", True), ("um so", False)])
def test_the_model_cannot_give_consent(lab, repo, heard, approved):
    conv = lab.svc.assistant.start("ana", at(1))
    first = FakeGroq("open the valve", [tool_call("orbit", request="open valve"), say("Shall I go ahead?")])
    agent(lab, first).turn(conv.id, "ana", "mac", b"x", "audio/wav", at(2))
    action_id = lab.svc.assistant.get(conv.id).pending.action_id
    # Whatever the user said, the model answers "yes" for them:
    second = FakeGroq(heard, [tool_call("orbit", request="yes"), say("Done.")])
    agent(lab, second).turn(conv.id, "ana", "mac", b"x", "audio/wav", at(2.5)) if heard != "um so" else \
        agent(lab, second).respond(conv.id, "ana", "mac", heard, at(2.5))
    assert (repo.get_action(action_id).status == ActionStatus.AUTHORIZED) is approved


def test_silence_and_noise_are_not_words(lab):
    for noise in ("", " Thank you.", "you"):
        groq = FakeGroq(noise, [])
        out = agent(lab, groq).turn(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", b"x", "audio/wav", at(2))
        assert out.heard == "" and out.reply == "" and len(groq.calls) == 1  # no thinking, no reply


def test_unavailable_model_falls_back_and_limits_are_explained(lab):
    groq = FakeGroq("hello", [say("Hi!")], status={"openai/gpt-oss-120b": 404})
    out = agent(lab, groq).turn(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", b"x", "audio/wav", at(2))
    assert out.reply == "Hi!" and out.model == "qwen/qwen3.8-27b"
    busy = FakeGroq("hello", [say("Hi from Qwen.")], status={"openai/gpt-oss-120b": 429})  # one model at its limit
    assert agent(lab, busy).turn(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", b"x", "audio/wav", at(2)).reply == "Hi from Qwen."
    limited = FakeGroq("hello", [], status={m: 429 for m in ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")})
    with pytest.raises(VoiceError, match="free limit"):
        agent(lab, limited).turn(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", b"x", "audio/wav", at(2))


# ------------------------------------------------------------------------ API
def test_voice_api(client, monkeypatch):
    svc = client.app.state.orbit
    st = client.get("/voice/status").json()
    assert set(st["engines"]) == {"groq", "gemini"}
    svc.voice.api_key = None
    assert client.post("/voice/turn", content=b"x", headers={"content-type": "audio/wav"}).status_code == 503
    svc.voice.api_key = "gsk_test"
    svc.voice.transport = FakeGroq("hello orbi", [say("Hi! What do you need?")])
    assert client.post("/voice/turn", content=b"", headers={"content-type": "audio/wav"}).status_code == 422
    r = client.post("/voice/turn?device=quest&user_id=ana", content=b"RIFFxxxx", headers={"content-type": "audio/wav"})
    assert r.status_code == 200
    body = r.json()
    assert body["heard"] == "hello orbi" and body["reply"].startswith("Hi") and body["conversation_id"] in svc.assistant.conversations
    assert client.post("/voice/turn?device=phone", content=b"x").status_code == 422


def test_groq_voice_with_system_fallback(client, monkeypatch, tmp_path):
    from backend.app.api import speech
    monkeypatch.setattr(speech, "CACHE", tmp_path)
    monkeypatch.setattr(speech, "_groq_paused_until", 0.0)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    sent = []

    def fake_http(url, body, headers, file=None):
        sent.append(body)
        return 200, b"RIFF" + b"\0" * 40
    monkeypatch.setattr(speech, "http", fake_http)
    r = client.get("/speech", params={"text": "Opening WhatsApp."})
    assert r.status_code == 200 and sent[0]["model"] == "canopylabs/orpheus-v1-english" and sent[0]["voice"] == "troy"

    def refused(url, body, headers, file=None):
        return 400, json.dumps({"error": {"message": "requires terms acceptance"}}).encode()
    monkeypatch.setattr(speech, "http", refused)
    if speech.available():  # falls back to the Mac voice and remembers why
        assert client.get("/speech", params={"text": "Second sentence."}).status_code == 200
        assert "terms" in speech.voice_status()["groq_problem"] and speech.voice_status()["groq_ready"] is False


def test_replies_are_spoken_text_and_contacts_are_known(lab, tmp_path):
    from backend.app.services.voice_agent import spoken
    assert spoken("The draft is ready. Press **Send**.\n- `check` it") == "The draft is ready. Press Send. check it"
    (tmp_path / "c.json").write_text('{"Prof Rao": {"email": "rao@uni.edu", "phone": "+91 900"}}')
    devices = DeviceController(runner=FakeShell(), platform="darwin", contacts_file=tmp_path / "c.json", sleep=lambda s: None)
    text = LiveService(lab.svc.assistant, devices, LiveSettings(api_key=None)).instructions("ana", "mac")
    assert "Prof Rao" in text and "rao@uni.edu" not in text and "900" not in text  # names only, never addresses


def test_actions_are_spoken_from_their_results_in_one_call(lab):
    from backend.app.services.voice_agent import spoken
    groq = FakeGroq("open youtube", [tool_call("open_app", app="youtube")])
    out = agent(lab, groq).respond(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", "open youtube", at(2))
    assert out.reply == "Opened YouTube in the Mac's browser." and len([c for c in groq.calls if c[0][-1] == "completions"]) == 1
    assert spoken("Paused. Let me know when you want to resume. Paused. Let me know when you want to resume.") == \
        "Paused. Let me know when you want to resume."
    assert spoken("Full-screen mode on.Anything else?") == "Full-screen mode on. Anything else?"


# ------------------------------------------------------------ songs and jokes
def test_orbi_sings_its_own_lyrics(lab, client):
    groq = FakeGroq("sing me a song about my lab", [tool_call("sing", lyrics="Benches shine at break of day\nOrbi keeps the parts in play", style="cheerful")])
    out = agent(lab, groq).respond(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", "sing me a song about my lab", at(2))
    assert out.reply == "Here's a little song for you!" and out.song_url.startswith("/speech/sing?style=cheerful&text=Benches")
    from backend.app.api import speech
    if speech.available():
        r = client.get(out.song_url)
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
    bad = agent(lab, FakeGroq("x", [tool_call("sing", lyrics="la\n" * 20)])).respond(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", "sing", at(2))
    assert "too long" in bad.reply


def test_jokes_are_just_talk_not_claimed_actions(lab):
    joke = "Why did the robot go on vacation? It needed to recharge. I opened my circuits to relax!"
    groq = FakeGroq("tell me a joke", [say(joke)])
    out = agent(lab, groq).respond(lab.svc.assistant.start("ana", at(1)).id, "ana", "mac", "tell me a joke", at(2))
    assert out.reply.startswith("Why did the robot") and out.tools == []
    assert len([c for c in groq.calls if c[0][-1] == "completions"]) == 1  # "opened" in a joke is not a claimed action
