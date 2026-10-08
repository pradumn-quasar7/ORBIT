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
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.services.live import TOOLS, LiveService

GROQ = "https://api.groq.com/openai/v1"
# Verified to call tools correctly on the user's account; each has its own free-plan
# limits, so a busy one hands over to the next.
CHAT_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")
STT_MODEL = "whisper-large-v3-turbo"
MAX_TOOL_ROUNDS = 4
HISTORY_TURNS = 4  # whole turns kept per conversation (the world itself lives in ORBIT, not here)
# A reply that says something was done: it must be backed by a tool call in that turn.
CLAIMS_ACTION = re.compile(r"\b(paused|resumed|playing|now play|skipp?ed|forwarded|rewound|full ?screen|exited|muted|unmuted|"
                           r"volume (set|is|to)|quality (set|is|to|changed)|opened|opening|searching|searched|sent|draft is (ready|open)|"
                           r"set to \d)\b", re.I)

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


def spoken(text: str) -> str:
    """Replies are heard, not read: drop markdown, fix run-together sentences, and drop
    sentences the model repeated ("Paused. Let me know… Paused. Let me know…")."""
    text = re.sub(r"\*\*|__|`|\*|^#+\s*|^\s*[-*]\s+", "", text or "", flags=re.M)
    text = re.sub(r"([.!?])(?=[A-Z])", r"\1 ", re.sub(r"\s+", " ", text)).strip()
    seen, out = set(), []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        key = re.sub(r"\W+", " ", sentence.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(sentence)
    return " ".join(out)


def say_result(result: Dict[str, Any]) -> str:
    """What to say for one tool result (already written for speech)."""
    if not result.get("ok", True):
        return f"Sorry: {result.get('error', 'that did not work')}."
    return str(result.get("speak") or result.get("read_back") or result.get("result") or result.get("answer") or "Done.")


def compact(content: str) -> str:
    """Tool results kept in memory: just the gist, to save tokens."""
    try:
        r = json.loads(content)
    except ValueError:
        return content[:200]
    return json.dumps({"ok": r.get("ok", True), "said": say_result(r)[:200], **({"message_id": r["message_id"]} if "message_id" in r else {})})


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
    def _chat(self, messages: List[Dict[str, Any]], require_tool: bool = False) -> Tuple[Dict[str, Any], str]:
        tools = [{"type": "function", "function": t} for t in TOOLS]
        last = ""
        for model in self.models:  # fall back if a model is unavailable to this account
            status, data = self.transport(f"{GROQ}/chat/completions", {
                "model": model, "messages": messages, "tools": tools, "tool_choice": "required" if require_tool else "auto",
                "temperature": 0.3, "max_completion_tokens": 400,
                **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
            }, self._headers(), None)
            if status == 200:
                return json.loads(data)["choices"][0]["message"], model
            last = groq_error(status, data)
            if status in (401, 403):  # a bad key: no other model will help
                break
        raise VoiceError(last)

    def respond(self, conversation_id: str, user_id: str, device: str, heard: str, at: datetime) -> VoiceTurn:
        """Think about what the user said and act on it through ORBIT's tools."""
        turn = VoiceTurn(heard=heard, reply="")
        history = self.history.setdefault(conversation_id, [])
        system = {"role": "system", "content": self.live.instructions(user_id, device)}
        messages = [system] + [m for t in history for m in t] + [{"role": "user", "content": heard}]
        start = len(messages) - 1
        t0 = time.time()
        nudged = False
        for _ in range(MAX_TOOL_ROUNDS + 1):
            message, turn.model = self._chat(messages, require_tool=nudged and not turn.tools)
            calls = message.get("tool_calls") or []
            if not calls:
                text = (message.get("content") or "").strip()
                if not turn.tools and not nudged and CLAIMS_ACTION.search(text):
                    # It says it acted but called no tool: never let a claimed action stand
                    # unexecuted (found in a real Groq run). Make it act, or say it can't.
                    nudged = True
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "system", "content": "You described an action without calling a tool, so nothing "
                                     "happened. Call the right tool now for what the user just asked."})
                    continue
                turn.reply = text
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
            # Device, video and email results are written to be spoken: say them directly,
            # one model call per command instead of two (the free plan allows ~8k tokens a
            # minute per model). ORBIT's answers about the room go back to the model, which
            # turns statuses and timestamps into a natural sentence.
            if all(c.get("function", {}).get("name") != "orbit" for c in calls):
                turn.reply = " ".join(say_result(t["result"]) for t in turn.tools[-len(calls):])
                break
        else:
            turn.reply = turn.reply or "Sorry, that took too many steps. Could you say it more simply?"
        turn.reply = spoken(turn.reply)
        if not turn.reply:
            turn.reply = "Done." if turn.tools and all(t["result"].get("ok") for t in turn.tools) else "Sorry, I couldn't do that."
        turn.timings["think"] = round(time.time() - t0, 2)
        # Keep the turn *with* its tool calls and results, so the model sees that actions
        # are done by calling tools (text-only history taught it to just claim them).
        kept = [m if m["role"] != "tool" else {**m, "content": compact(m["content"])} for m in messages[start:]
                if not (m["role"] == "system" or (m["role"] == "assistant" and not m.get("tool_calls")))]
        history.append(kept + [{"role": "assistant", "content": turn.reply}])
        del history[:-HISTORY_TURNS]
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
