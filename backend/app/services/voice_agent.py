"""Hosted speech-to-speech on Groq's free plan (Phase 19.1).

One conversational turn: the user's recorded utterance → Whisper (speech to text) →
an open-weight chat model with Orbi's tools (thinking and doing) → the reply text, which
the client speaks through ``/speech`` (Groq's Orpheus voice, or the system voice).
Nothing runs on this computer except ORBIT itself.

The tools, instructions and consent rules are exactly those of the Gemini Live engine
(``LiveService``): the model plans, ORBIT executes, and a "yes" counts only if it is the
user's own transcribed words (ADR-044, ADR-045).
"""
import json
import os
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.services.live import TOOLS, LiveService

GROQ = "https://api.groq.com/openai/v1"
CHAT_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b")  # both verified to call tools correctly
STT_MODEL = "whisper-large-v3-turbo"
MAX_TOOL_ROUNDS = 4
HISTORY = 16  # messages kept per conversation (the world itself lives in ORBIT, not here)

# (url, body, headers, files?) -> (status, bytes)
Transport = Callable[[str, Any, Dict[str, str], Optional[Tuple[str, bytes, str]]], Tuple[int, bytes]]

AUDIO_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}


def http(url: str, body: Any, headers: Dict[str, str], file: Optional[Tuple[str, bytes, str]] = None) -> Tuple[int, bytes]:
    if file is not None:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in body.items()]
        name, data, ctype = file
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
        payload, ctype_header = b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
    else:
        payload, ctype_header = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={**headers, "content-type": ctype_header, "user-agent": "orbit/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 (fixed Groq endpoint)
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# What Whisper "hears" in silence or noise: never treat these as the user speaking.
WHISPER_SILENCE = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye", "okay", "so",
                   "[blank_audio]", "(silence)", "[music]", "uh", "um"}


class VoiceError(RuntimeError):
    pass


def groq_error(status: int, data: bytes) -> str:
    try:
        msg = json.loads(data)["error"]["message"]
    except Exception:
        msg = data[:200].decode("utf-8", "replace")
    if status == 429:
        return "Groq's free limit is reached for the moment. Wait a minute and try again."
    if status in (401, 403):
        return "Groq refused the key. Check GROQ_API_KEY in the .env file."
    return f"Groq error {status}: {msg}"


@dataclass
class VoiceTurn:
    heard: str
    reply: str
    tools: List[Dict[str, Any]] = field(default_factory=list)
    language: Optional[str] = None
    timings: Dict[str, float] = field(default_factory=dict)
    model: str = ""


class VoiceAgent:
    def __init__(self, live: LiveService, api_key: Optional[str] = None, transport: Transport = http,
                 models: Tuple[str, ...] = CHAT_MODELS):
        self.live = live
        self.api_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        self.transport = transport
        self.models = models
        self.history: Dict[str, List[Dict[str, Any]]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    # -------------------------------------------------------------- hearing
    def transcribe(self, audio: bytes, content_type: str) -> Tuple[str, Optional[str]]:
        ext = AUDIO_TYPES.get(content_type.split(";")[0].strip().lower(), "webm")
        status, data = self.transport(f"{GROQ}/audio/transcriptions",
                                      {"model": STT_MODEL, "response_format": "verbose_json", "temperature": "0"},
                                      self._headers(), (f"speech.{ext}", audio, content_type.split(";")[0] or "audio/webm"))
        if status != 200:
            raise VoiceError(groq_error(status, data))
        out = json.loads(data)
        return (out.get("text") or "").strip(), out.get("language")

    # ------------------------------------------------------------- thinking
    def _chat(self, messages: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
        tools = [{"type": "function", "function": t} for t in TOOLS]
        last = ""
        for model in self.models:  # fall back if a model is unavailable to this account
            status, data = self.transport(f"{GROQ}/chat/completions", {
                "model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                "temperature": 0.3, "max_completion_tokens": 400,
            }, self._headers(), None)
            if status == 200:
                return json.loads(data)["choices"][0]["message"], model
            last = groq_error(status, data)
            if status not in (400, 404):  # rate limit / auth: another model won't help
                break
        raise VoiceError(last)

    def respond(self, conversation_id: str, user_id: str, device: str, heard: str, at: datetime) -> VoiceTurn:
        """Think about what the user said and act on it through ORBIT's tools."""
        turn = VoiceTurn(heard=heard, reply="")
        history = self.history.setdefault(conversation_id, [])
        messages = [{"role": "system", "content": self.live.instructions(user_id, device)}] + history + [{"role": "user", "content": heard}]
        t0 = time.time()
        for _ in range(MAX_TOOL_ROUNDS):
            message, turn.model = self._chat(messages)
            calls = message.get("tool_calls") or []
            if not calls:
                turn.reply = (message.get("content") or "").strip()
                break
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                # Consent is judged on the user's own words (heard), never on the model's.
                result = self.live.call(fn.get("name", ""), args, conversation_id, device, heard, at)
                turn.tools.append({"name": fn.get("name"), "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result)})
        else:
            turn.reply = "Sorry, that took too many steps. Could you say it more simply?"
        if not turn.reply:
            turn.reply = "Done." if turn.tools and all(t["result"].get("ok") for t in turn.tools) else "Sorry, I couldn't do that."
        turn.timings["think"] = round(time.time() - t0, 2)
        history.extend([{"role": "user", "content": heard}, {"role": "assistant", "content": turn.reply}])
        del history[:-HISTORY]
        return turn

    def turn(self, conversation_id: str, user_id: str, device: str, audio: bytes, content_type: str, at: datetime) -> VoiceTurn:
        t0 = time.time()
        heard, language = self.transcribe(audio, content_type)
        hear = round(time.time() - t0, 2)
        if not heard or heard.strip(" .").lower() in WHISPER_SILENCE:
            return VoiceTurn(heard="", reply="", timings={"hear": hear})  # silence or noise, not words
        out = self.respond(conversation_id, user_id, device, heard, at)
        out.language = language
        out.timings["hear"] = hear
        return out
