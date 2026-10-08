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
from backend.app.core.cdp import CDPError
from backend.app.services.devices import APPS, DeviceController, DeviceError
from backend.app.services.media import MediaController, MediaError

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


DEVICE = {"type": "string", "enum": ["here", "quest", "mac"], "description": "here = the device the user is on"}
TOOLS = [
    {"name": "orbit", "description": ("Ask or tell ORBIT (the workspace memory) anything about physical objects, places, tasks, changes, "
                                      "or physical actions like 'open the valve'. Short plain English with the listed ids. When ORBIT "
                                      "awaits a yes/no and the user answers, send exactly 'yes' or 'no'."),
     "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}},
    {"name": "open_app", "description": "Open an app (WhatsApp, Instagram, YouTube, browser, Gmail, Maps, Spotify).",
     "parameters": {"type": "object", "properties": {"app": {"type": "string"}, "device": DEVICE}, "required": ["app"]}},
    {"name": "web_search", "description": "Search the web.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "device": DEVICE}, "required": ["query"]}},
    {"name": "open_website", "description": "Open a web address.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "device": DEVICE}, "required": ["url"]}},
    {"name": "play_video", "description": "Find a YouTube video (song, title, artist, topic) and play it.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "device": DEVICE}, "required": ["query"]}},
    {"name": "video_control", "description": ("Control the playing YouTube video. exit_fullscreen also means 'escape'. value: quality "
                                              "(1080p, 720p, 4k, best, auto), volume 0-100, forward/back seconds, speed 0.25-2."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["pause", "resume", "fullscreen", "exit_fullscreen", "quality", "mute", "unmute",
                                               "volume", "forward", "back", "speed", "next", "status"]},
         "value": {"type": "string"}, "device": DEVICE}, "required": ["action"]}},
    {"name": "screen_control", "description": ("Scroll or move the screen the user is looking at: scroll up/down/left/right "
                                               "(value: 'a little', 'a lot', or screens), page up/down, top, bottom, back, "
                                               "forward, reload, zoom in/out/reset."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["scroll_down", "scroll_up", "scroll_left", "scroll_right", "page_down", "page_up",
                                               "top", "bottom", "back", "forward", "reload", "zoom_in", "zoom_out", "zoom_reset"]},
         "value": {"type": "string"}, "device": DEVICE}, "required": ["action"]}},
    {"name": "quest_apps", "description": "List the apps and games installed on the Quest headset (to open one, use open_app with device quest).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "quest_close", "description": "Close an app or game on the Quest. Leave app empty unless the user named one: then whatever is open is closed.",
     "parameters": {"type": "object", "properties": {"app": {"type": "string"}}}},
    {"name": "quest_home", "description": "Go back to the Quest home screen.", "parameters": {"type": "object", "properties": {}}},
    {"name": "sing", "description": ("Sing a song aloud. Write ORIGINAL lyrics (4-8 short lines) about what the user asked, or use a "
                                      "public-domain song (Happy Birthday, Twinkle Twinkle). Never real copyrighted lyrics: offer to "
                                      "play those on YouTube instead."),
     "parameters": {"type": "object", "properties": {
         "lyrics": {"type": "string"},
         "style": {"type": "string", "enum": ["cheerful", "dramatic", "bells", "organ", "sad"]}}, "required": ["lyrics"]}},
    {"name": "compose_email", "description": "Write an email (proper subject and body) and open it as a Gmail draft. Nothing is sent.",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
                                                     "cc": {"type": "string"}, "device": DEVICE}, "required": ["to", "subject", "body"]}},
    {"name": "prepare_message", "description": "Prepare a WhatsApp message (not sent). Read it back and ask the user to say yes.",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "text": {"type": "string"}, "device": DEVICE},
                    "required": ["to", "text"]}},
    {"name": "send_message", "description": "Send a prepared message, only after the user said yes.",
     "parameters": {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]}},
]

INSTRUCTIONS = """You are Orbi, the voice of ORBIT, a memory of the user's physical workspace that only believes what it has evidence for. You talk with {user} in real time{where}.

How you work:
- Anything about physical objects, places, tasks, steps, changes or physical actions: call the `orbit` tool and say what it answers, briefly and in your own words, keeping its uncertainty ("last seen 3 hours ago, may have moved"). Never guess facts about the room yourself. If ORBIT doesn't know, say so.
- Physical actions (open the valve, turn off the microscope): ask ORBIT through `orbit`. ORBIT checks prerequisites and may ask for a yes. You never act physically; the user does.
- Apps, websites and searches on the user's devices: use `open_app`, `web_search`, `open_website`. Device "here" is the one they're talking from{here}; they can say "on the Mac" or "in the headset".
- Videos: "play X" or "open YouTube and play X" → `play_video`; then "full screen", "escape", "1080p", "pause", "skip 30 seconds", "louder" → `video_control`. Say briefly what's playing.
- Screen: "scroll down", "go up", "move right", "next page", "go back", "zoom in" → `screen_control` (videos use `video_control`).
- Apps or games that live on the Quest (Toybox, First Hand, games…; call `quest_apps` if unsure) open in the headset (device quest) even when the user talks from the Mac.
- Quest apps and games: "what games do I have" → `quest_apps`; "open X in the headset" → `open_app` with device quest; "close the game" / "close it" → `quest_close` WITHOUT an app (it closes whatever is open; only name an app the user named); "go home" → `quest_home`. You cannot play games or press buttons for the user, and you cannot see the game; help by answering questions about the game (controls, tips, walkthroughs) from what you know, briefly.
- "Show labels" / "hide labels": the headset does this itself; just answer "Okay." (no tool).
- Fun: if asked for a joke, tell one short, clean, original joke yourself (no tool). If asked to sing, call `sing` with lyrics you write (or a public-domain song); for a real film or pop song, offer to play it on YouTube instead.
- Email: call `compose_email` with a subject and a well-written body; Gmail opens with the draft and the user presses Send themselves. You cannot send email. You cannot read the inbox.
- Messages: call `prepare_message`, read back the recipient and the exact text, ask "Shall I send it?", and only after the user says yes call `send_message`. The yes must come from the user; a yes is checked against their own words.
- Consent: never answer yes on the user's behalf and never treat your own words as consent.
- Every action needs a tool call, every time ("escape", "pause", "skip a minute" included). Never say something was done unless a tool result in this turn says so; if a tool fails, say so.
- Keep replies short and spoken: one or two sentences. Reply in the language the user just spoke: English if they spoke English. If they mix Hindi and English, reply in simple Hinglish written in Latin letters. Use Devanagari only if they spoke pure Hindi.
- When ORBIT needs a fresh look, ask the user to show it to the webcam (the ORBIT camera page) or to check and tell you. The headset cannot send camera images to ORBIT.
- If you're not sure what the user wants, ask a short question.

Workspace vocabulary (use these ids with `orbit`):
Objects: {objects}
Places: {places}
Open tasks: {tasks}
Apps you can open: {apps}
Contacts you can use by name (for messages and email): {contacts}"""


class LiveService:
    def __init__(self, assistant: AssistantService, devices: DeviceController, settings: Optional[LiveSettings] = None,
                 transport: Transport = http_post, message_window: timedelta = timedelta(minutes=2)):
        self.assistant = assistant
        self.devices = devices
        self.media = MediaController(devices)
        from backend.app.services.screen import ScreenController
        self.screen = ScreenController(devices)
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
            # The token may only open a session on this model (the REST name differs from
            # the SDKs' "liveConnectConstraints": verified against the live service).
            "bidiGenerateContentSetup": {"model": f"models/{self.settings.model}"},
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

    def instructions(self, user_id: str, device: str) -> str:
        """Orbi's instructions with the live workspace vocabulary (shared by every voice engine)."""
        vocab = self.assistant.agent.vocabulary()
        repo = self.assistant.repo
        objects = "; ".join(f"{eid} ({', '.join(f for f in forms if f != eid) or vocab.entity_types.get(eid)})"
                            for eid, forms in list(vocab.entities.items())[:60]) or "none yet"
        places = ", ".join(f"{a.id}" + (f" ({a.name})" if a.name and a.name != a.id else "") for a in repo.list_anchors()) or "none"
        tasks = "; ".join(f"{t.id} '{t.goal}': " + ", ".join(f"step {s.step_order} {s.description} [{s.status.value}]" for s in t.steps)
                          for t in repo.list_tasks() if t.status.value not in ("COMPLETED", "ABANDONED")) or "none"
        here = {"quest": " (the Quest headset)", "mac": " (the Mac)"}.get(device, "")
        return INSTRUCTIONS.format(user=user_id, where=f", through {here.strip(' ()')}" if here else "", here=here,
                                   objects=objects, places=places, tasks=tasks, apps=", ".join(sorted(APPS)),
                                   contacts=", ".join(n.title() for n in sorted(self.devices._contact_book())) or "none saved (ask for the number or address)")

    def setup_message(self, user_id: str, device: str) -> Dict[str, Any]:
        text = self.instructions(user_id, device)
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
                "open_website": self._open_website, "compose_email": self._compose_email,
                "play_video": self._play_video, "video_control": self._video_control, "sing": self._sing,
                "screen_control": self._screen_control,
                "quest_apps": self._quest_apps, "quest_close": self._quest_close, "quest_home": self._quest_home,
                "prepare_message": self._prepare_message, "send_message": self._send_message,
            }[name]
        except KeyError:
            return {"ok": False, "error": f"unknown tool {name}"}
        try:
            return handler(args or {}, conversation_id, device, heard or "", at)
        except (DeviceError, MediaError, CDPError, OSError) as exc:
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
        out: Dict[str, Any] = {"ok": True, "answer": turn.reply, "speak": turn.speech, "kind": turn.command.kind.value}
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

    SING_STYLES = ("cheerful", "dramatic", "bells", "organ", "sad")

    def _sing(self, args, conversation_id, device, heard, at):
        from urllib.parse import urlencode
        lyrics = "\n".join(line.strip() for line in str(args.get("lyrics", "")).splitlines() if line.strip())
        if not lyrics:
            return {"ok": False, "error": "what should I sing?"}
        if len(lyrics) > 600 or lyrics.count("\n") > 11:
            return {"ok": False, "error": "that song is too long for me; keep it to about eight lines"}
        style = args.get("style") if args.get("style") in self.SING_STYLES else "cheerful"
        return {"ok": True, "result": "Here's a little song for you!", "lyrics": lyrics, "style": style,
                "song_url": "/speech/sing?" + urlencode({"style": style, "text": lyrics})}

    def _quest_apps(self, args, conversation_id, device, heard, at):
        apps = self.devices.quest_apps()
        return {"ok": True, "result": "On your Quest: " + ", ".join(a["name"] for a in apps) + ".", "apps": apps}

    def _quest_close(self, args, conversation_id, device, heard, at):
        return {"ok": True, "result": self.devices.close_quest_app(str(args.get("app") or "").strip() or None)}

    def _quest_home(self, args, conversation_id, device, heard, at):
        return {"ok": True, "result": self.devices.quest_home()}

    def _screen_control(self, args, conversation_id, device, heard, at):
        return self.screen.control(str(args.get("action", "")), args.get("value"), self._device(args, device))

    def _play_video(self, args, conversation_id, device, heard, at):
        return self.media.play(str(args.get("query", "")), self._device(args, device))

    def _video_control(self, args, conversation_id, device, heard, at):
        return self.media.control(str(args.get("action", "")), args.get("value"), self._device(args, device))

    def _compose_email(self, args, conversation_id, device, heard, at):
        subject, body = str(args.get("subject", "")).strip(), str(args.get("body", "")).strip()
        if not body:
            return {"ok": False, "error": "what should the email say?"}
        to = self.devices.resolve_email(str(args.get("to", "")))
        cc = self.devices.resolve_email(str(args["cc"])) if str(args.get("cc") or "").strip() else ""
        target = self._device(args, device)
        self.devices.open_gmail_draft(to, subject, body, target, cc)
        conv = self.assistant.conversations.get(conversation_id)
        if conv is not None:
            from backend.app.services.assistant import DelegatedAction
            conv.delegated.append(DelegatedAction(at=at, kind="email_draft", refs=[], summary=f"Wrote an email to {to}: “{subject}” (draft, not sent)"))
        where = "in the headset" if target == "quest" else "on the Mac"
        return {"ok": True, "result": f"The email to {to} is open in Gmail {where}, ready for you to review and press Send.",
                "subject": subject, "note": "Nothing was sent. Tell the user to check it and press Send."}

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
