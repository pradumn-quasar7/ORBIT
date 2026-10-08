"""Realtime voice conversation with Gemini Live (Phase 19).

The browser (laptop page or Quest headset) streams microphone audio to the Gemini Live
API and plays Orbi's spoken replies, barge-in included. This service:

- mints a short-lived, single-use *ephemeral token* for each session so the API key
  never leaves this computer;
- writes the session setup: Orbi's instructions, the workspace vocabulary, and the
  tools Gemini may call;
- executes those tool calls here, where ORBIT's evidence and the action-safety
  boundary live (ADR-044).

Gemini does the listening, talking and understanding. It never becomes the source of
truth about the room (``orbit`` answers from evidence) and never holds consent: a
"yes" that authorises an action or sends a message is checked against the user's own
transcribed words, deterministically, on this side.
"""
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.app.domain.models import generate_id
from backend.app.providers.commands import YES, normalise, spoken_numbers
from backend.app.services.assistant import AssistantService
from backend.app.services.devices import APPS, DeviceController, DeviceError

API = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE = "Kore"
Transport = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]


def http_post(url: str, body: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 (fixed Google endpoint)
        return json.loads(res.read())


@dataclass
class LiveSettings:
    api_key: Optional[str]
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    api_version: str = "v1beta"

    @classmethod
    def from_env(cls) -> "LiveSettings":
        return cls(api_key=os.environ.get("GEMINI_API_KEY") or None,
                   model=os.environ.get("GEMINI_LIVE_MODEL", DEFAULT_MODEL),
                   voice=os.environ.get("GEMINI_VOICE", DEFAULT_VOICE),
                   api_version=os.environ.get("GEMINI_LIVE_API_VERSION", "v1beta"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class LiveNotConfigured(RuntimeError):
    pass


@dataclass
class PendingMessage:
    id: str
    app: str
    to: str
    number: str
    text: str
    device: str
    expires_at: datetime


TOOLS = [
    {
        "name": "orbit",
        "description": (
            "Ask or tell ORBIT, the evidence-based memory of the user's workspace. Use it for EVERY question or statement "
            "about physical things, places, tasks and changes (where is X, what's on bench 3, what changed, continue, "
            "I moved X to Y, I finished step 6, pause the task) and for physical actions (open the valve) — ORBIT checks "
            "prerequisites and asks for consent. Write the request as short plain English using the ids listed in your "
            "instructions. When ORBIT is waiting for a yes/no and the user answers, send exactly 'yes' or 'no'. "
            "Relay ORBIT's answer faithfully, including uncertainty; never add facts of your own."),
        "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]},
    },
    {
        "name": "open_app",
        "description": "Open an app on the user's device, e.g. WhatsApp, Instagram, YouTube, browser, Gmail, Maps, Spotify.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string"},
            "device": {"type": "string", "enum": ["here", "quest", "mac"], "description": "'here' = the device the user is talking from"},
        }, "required": ["app"]},
    },
    {
        "name": "web_search",
        "description": "Search the web in the browser on the user's device.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "device": {"type": "string", "enum": ["here", "quest", "mac"]},
        }, "required": ["query"]},
    },
    {
        "name": "open_website",
        "description": "Open a web address in the browser on the user's device.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "device": {"type": "string", "enum": ["here", "quest", "mac"]},
        }, "required": ["url"]},
    },
    {
        "name": "prepare_message",
        "description": ("Prepare a WhatsApp message (nothing is sent yet). Returns a read-back. Read it to the user and ask "
                        "them to say yes to send; then call send_message."),
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "contact name or phone number with country code"},
            "text": {"type": "string"},
            "device": {"type": "string", "enum": ["here", "quest", "mac"]},
        }, "required": ["to", "text"]},
    },
    {
        "name": "send_message",
        "description": "Send a prepared message. Only after the user explicitly said yes to the read-back.",
        "parameters": {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]},
    },
]

INSTRUCTIONS = """You are Orbi, the voice of ORBIT, a memory of the user's physical workspace that only believes what it has evidence for. You talk with {user} in real time{where}.

How you work:
- Anything about physical objects, places, tasks, steps, changes or physical actions: call the `orbit` tool and say what it answers, briefly and in your own words, keeping its uncertainty ("last seen 3 hours ago, may have moved"). Never guess facts about the room yourself. If ORBIT doesn't know, say so.
- Physical actions (open the valve, turn off the microscope): ask ORBIT through `orbit`. ORBIT checks prerequisites and may ask for a yes. You never act physically; the user does.
- Apps, websites and searches on the user's devices: use `open_app`, `web_search`, `open_website`. Device "here" is the one they're talking from{here}; they can say "on the Mac" or "in the headset".
- Messages: call `prepare_message`, read back the recipient and the exact text, ask "Shall I send it?", and only after the user says yes call `send_message`. The yes must come from the user; a yes is checked against their own words.
- Consent: never answer yes on the user's behalf and never treat your own words as consent.
- Keep replies short and spoken: one or two sentences. Use the user's language (they may mix Hindi and English).
- If you're not sure what the user wants, ask a short question.

Workspace vocabulary (use these ids with `orbit`):
Objects: {objects}
Places: {places}
Open tasks: {tasks}
Apps you can open: {apps}"""


class LiveService:
    def __init__(self, assistant: AssistantService, devices: DeviceController, settings: Optional[LiveSettings] = None,
                 transport: Transport = http_post, message_window: timedelta = timedelta(minutes=2)):
        self.assistant = assistant
        self.devices = devices
        self.settings = settings or LiveSettings.from_env()
        self.transport = transport
        self.message_window = message_window
        self.pending: Dict[str, PendingMessage] = {}

    # ------------------------------------------------------------- session
    def status(self) -> Dict[str, Any]:
        return {"configured": self.settings.configured, "model": self.settings.model, "voice": self.settings.voice,
                "devices": self.devices.available()}

    def start(self, user_id: str, device: str, at: datetime, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.settings.configured:
            raise LiveNotConfigured("No Gemini API key: put GEMINI_API_KEY in the .env file and restart ORBIT")
        conv = None
        if conversation_id:
            conv = self.assistant.conversations.get(conversation_id)
        if conv is None:
            conv = self.assistant.start(user_id, at)
        token = self._token(at)
        v = self.settings.api_version
        ws = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.{v}.GenerativeService.BidiGenerateContentConstrained?access_token={t}"
        return {
            "conversation_id": conv.id,
            "ws_url": ws.format(v=v, t=token),
            "ws_url_fallback": ws.format(v="v1alpha" if v != "v1alpha" else "v1beta", t=token),
            "setup": self.setup_message(conv.user_id, device),
            "model": self.settings.model,
        }

    def _token(self, at: datetime) -> str:
        now = at.astimezone(timezone.utc)
        body = {
            "uses": 1,
            "expireTime": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "newSessionExpireTime": (now + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "liveConnectConstraints": {"model": f"models/{self.settings.model}",
                                       "config": {"responseModalities": ["AUDIO"], "sessionResumption": {}}},
        }
        try:
            res = self.transport(f"{API}/{self.settings.api_version}/auth_tokens", body, {"x-goog-api-key": self.settings.api_key})
        except Exception as exc:  # urllib.error.HTTPError carries Google's reason
            detail = ""
            if hasattr(exc, "read"):
                try:
                    detail = json.loads(exc.read()).get("error", {}).get("message", "")
                except Exception:
                    pass
            raise LiveNotConfigured(f"Gemini refused to start a session: {detail or exc}") from exc
        token = res.get("name")
        if not token:
            raise LiveNotConfigured("Gemini returned no session token")
        return token

    def setup_message(self, user_id: str, device: str) -> Dict[str, Any]:
        vocab = self.assistant.agent.vocabulary()
        repo = self.assistant.repo
        objects = "; ".join(f"{eid} ({', '.join(f for f in forms if f != eid) or vocab.entity_types.get(eid)})"
                            for eid, forms in list(vocab.entities.items())[:60]) or "none yet"
        places = ", ".join(f"{a.id}" + (f" ({a.name})" if a.name and a.name != a.id else "") for a in repo.list_anchors()) or "none"
        tasks = "; ".join(f"{t.id} '{t.goal}': " + ", ".join(f"step {s.step_order} {s.description} [{s.status.value}]" for s in t.steps)
                          for t in repo.list_tasks() if t.status.value not in ("COMPLETED", "ABANDONED")) or "none"
        here = {"quest": " (the Quest headset)", "mac": " (the Mac)"}.get(device, "")
        text = INSTRUCTIONS.format(user=user_id, where=f", through {here.strip(' ()')}" if here else "", here=here,
                                   objects=objects, places=places, tasks=tasks, apps=", ".join(sorted(APPS)))
        return {"setup": {
            "model": f"models/{self.settings.model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.settings.voice}}},
            },
            "systemInstruction": {"parts": [{"text": text}]},
            "tools": [{"functionDeclarations": TOOLS}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}

    # --------------------------------------------------------------- tools
    def call(self, name: str, args: Dict[str, Any], conversation_id: str, device: str, heard: str, at: datetime) -> Dict[str, Any]:
        """Run one tool call. ``heard`` is the user's own transcribed words this turn."""
        try:
            handler = {
                "orbit": self._orbit, "open_app": self._open_app, "web_search": self._web_search,
                "open_website": self._open_website, "prepare_message": self._prepare_message, "send_message": self._send_message,
            }[name]
        except KeyError:
            return {"ok": False, "error": f"unknown tool {name}"}
        try:
            return handler(args or {}, conversation_id, device, heard or "", at)
        except DeviceError as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _device(args: Dict[str, Any], here: str) -> str:
        d = (args.get("device") or "here").lower()
        return here if d == "here" else d

    @staticmethod
    def consented(heard: str) -> bool:
        """The user's own words, not the model's: the whole utterance must be a yes."""
        from backend.app.providers.commands import ADDRESS
        t = ADDRESS.sub("", normalise(spoken_numbers(heard)), count=1)
        return bool(YES.match(t) or re.fullmatch(r"(yes |yeah |ok |okay )?(send|send it|please send|send it please|go ahead and send)( it)?( please)?", t))

    def _orbit(self, args, conversation_id, device, heard, at) -> Dict[str, Any]:
        request = str(args.get("request", "")).strip()
        if not request:
            return {"ok": False, "error": "empty request"}
        turn = self.assistant.say(conversation_id, request, at, heard=heard)
        out: Dict[str, Any] = {"ok": True, "answer": turn.reply, "kind": turn.command.kind.value}
        if turn.pending:
            out["waiting_for_user_consent"] = turn.pending.summary + ". Ask the user to say yes or no."
        if turn.observation_requests:
            out["to_settle_it"] = turn.observation_requests[0].instruction
        if turn.done:
            out["done"] = [d.summary for d in turn.done]
        return out

    def _open_app(self, args, conversation_id, device, heard, at):
        return {"ok": True, "result": self.devices.open_app(str(args.get("app", "")), self._device(args, device))}

    def _web_search(self, args, conversation_id, device, heard, at):
        return {"ok": True, "result": self.devices.search(str(args.get("query", "")), self._device(args, device))}

    def _open_website(self, args, conversation_id, device, heard, at):
        url = str(args.get("url", "")).strip()
        if url and "://" not in url:
            if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?", url):
                return {"ok": False, "error": f"{url!r} isn't a web address"}
            url = "https://" + url
        return {"ok": True, "result": self.devices.open_url(url, self._device(args, device))}

    def _prepare_message(self, args, conversation_id, device, heard, at):
        to, text = str(args.get("to", "")).strip(), str(args.get("text", "")).strip()
        if not to or not text:
            return {"ok": False, "error": "need a recipient and the message text"}
        number = self.devices.resolve_number(to)
        target = self._device(args, device)
        self.devices._target(target)  # fail now if the device isn't reachable
        msg = PendingMessage(id=generate_id("msg"), app="whatsapp", to=to, number=number, text=text,
                             device=target, expires_at=at + self.message_window)
        self.pending = {k: v for k, v in self.pending.items() if v.expires_at > at}
        self.pending[msg.id] = msg
        return {"ok": True, "message_id": msg.id,
                "read_back": f"WhatsApp to {to}: “{text}”. Shall I send it?",
                "note": "Nothing has been sent. Wait for the user's own yes, then call send_message."}

    def _send_message(self, args, conversation_id, device, heard, at):
        msg = self.pending.get(str(args.get("message_id", "")))
        if msg is None or msg.expires_at < at:
            return {"ok": False, "error": "That message isn't prepared any more (or it expired). Prepare it again."}
        if not self.consented(heard):
            return {"ok": False, "error": "Not sent: the user hasn't said yes in their own words. Read it back and ask again."}
        del self.pending[msg.id]
        native = self.devices.open_whatsapp_chat(msg.number, msg.text, msg.device)
        sent = self.devices.press_send(msg.device, native)
        conv = self.assistant.conversations.get(conversation_id)
        if conv is not None:
            from backend.app.services.assistant import DelegatedAction
            conv.delegated.append(DelegatedAction(at=at, kind="message", refs=[msg.id],
                                                  summary=f"{'Sent' if sent else 'Opened, ready to send,'} a WhatsApp message to {msg.to}"))
        if sent:
            return {"ok": True, "result": f"Sent to {msg.to}."}
        return {"ok": True, "result": f"The chat with {msg.to} is open with the message typed in. Tap send to send it.",
                "note": "Automatic sending isn't possible on this device; tell the user to press send."}
