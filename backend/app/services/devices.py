"""Device actions (Phase 19): open apps, pages and searches on the user's own devices.

Two targets:
- ``quest``: the headset, driven from this computer over the USB cable (adb). Apps are
  launched by package; web pages open in the Quest browser.
- ``mac``: this computer (``open``).

These are *digital*, user-requested actions on the user's own devices, so opening and
searching run directly. Sending a message is outward-facing: it is only *prepared* here
(chat opened with the text filled in) and sent after the user's own spoken "yes"
(see LiveService). Commands are always argument lists (never a shell); URLs must be
http(s) or a known app scheme; package names are validated.
"""
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

Runner = Callable[[List[str]], subprocess.CompletedProcess]
DEVICES = ("quest", "mac")
PACKAGE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")
EMAIL = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}")
CONTACTS = Path(__file__).resolve().parents[3] / ".run" / "contacts.json"


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


@dataclass
class App:
    quest_package: Optional[str] = None
    quest_url: Optional[str] = None
    mac_app: Optional[str] = None
    mac_url: Optional[str] = None


# What "open X" means on each device. Instagram has no Quest app: its website opens.
APPS: Dict[str, App] = {
    "whatsapp": App(quest_package="com.whatsapp", mac_app="WhatsApp", mac_url="https://web.whatsapp.com"),
    "instagram": App(quest_url="https://www.instagram.com", mac_app="Instagram", mac_url="https://www.instagram.com"),
    "facebook": App(quest_package="com.oculus.facebook", mac_url="https://www.facebook.com"),
    "youtube": App(quest_url="https://www.youtube.com", mac_url="https://www.youtube.com"),
    "browser": App(quest_package="com.oculus.browser", mac_url="https://www.google.com"),
    "google": App(quest_url="https://www.google.com", mac_url="https://www.google.com"),
    "gmail": App(quest_url="https://mail.google.com", mac_url="https://mail.google.com"),
    "maps": App(quest_url="https://maps.google.com", mac_url="https://maps.google.com"),
    "spotify": App(quest_url="https://open.spotify.com", mac_app="Spotify", mac_url="https://open.spotify.com"),
    "orbit": App(quest_url="http://localhost:8765/ui/xr.html", mac_url="http://localhost:8765/ui/"),
}
DISPLAY = {"whatsapp": "WhatsApp", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube", "browser": "the browser",
           "google": "Google", "gmail": "Gmail", "maps": "Google Maps", "spotify": "Spotify", "orbit": "ORBIT"}
ALIASES = {"whats app": "whatsapp", "insta": "instagram", "ig": "instagram", "fb": "facebook", "yt": "youtube",
           "chrome": "browser", "safari": "browser", "web browser": "browser", "google maps": "maps", "mail": "gmail"}


# Spoken names for Quest apps whose package name doesn't say what they are.
QUEST_ALIASES = {"instagram": "com.oculus.igvr", "insta": "com.oculus.igvr", "facebook": "com.oculus.facebook",
                 "horizonworlds": "com.facebook.horizon", "worlds": "com.facebook.horizon", "measure": "com.meta.curio.ruler",
                 "ruler": "com.meta.curio.ruler", "quill": "com.facebook.arvr.quillplayer", "browser": "com.oculus.browser",
                 "store": "com.oculus.store", "metastore": "com.oculus.store", "settings": "com.android.settings",
                 "firsthand": "com.oculus.samples.firsthand", "toybox": "com.meta.curio.toybox", "whatsapp": "com.whatsapp"}
QUEST_NAMES = {"com.oculus.igvr": "Instagram", "com.oculus.facebook": "Facebook", "com.facebook.horizon": "Horizon Worlds",
               "com.meta.curio.ruler": "Measure", "com.facebook.arvr.quillplayer": "Quill", "com.whatsapp": "WhatsApp",
               "com.oculus.samples.firsthand": "First Hand", "com.meta.curio.toybox": "Toybox",
               "com.meta.handseducationmodule": "Hands Tutorial", "com.oculus.browser": "Browser"}
QUEST_HIDDEN = ("accountscenter", "helpcenter", "privacycheckup", "shell.env.")  # system bits, not apps to open


def pretty_name(package: str) -> str:
    """com.beatgames.beatsaber → Beatsaber (a readable fallback name)."""
    last = package.split(".")[-1]
    return re.sub(r"[_-]+", " ", last).title()


class DeviceError(ValueError):
    pass


def safe_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https", "whatsapp") or (p.scheme != "whatsapp" and not p.netloc):
        raise DeviceError(f"refusing to open {url!r}: only web addresses")
    return url.strip()


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote(query.strip())


class DeviceController:
    def __init__(self, runner: Runner = _run, platform: str = sys.platform, contacts_file: Path = CONTACTS,
                 sleep: Callable[[float], None] = time.sleep):
        self.run = runner
        self.sleep = sleep
        self.platform = platform
        self.contacts_file = contacts_file

    # ------------------------------------------------------------- status
    def available(self) -> Dict[str, bool]:
        return {"quest": self._quest_connected(), "mac": self.platform == "darwin"}

    def _quest_connected(self) -> bool:
        if shutil.which("adb") is None and self.run is _run:
            return False
        try:
            out = self.run(["adb", "devices"]).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return any(line.endswith("\tdevice") for line in out.splitlines())

    def _target(self, device: str) -> str:
        if device not in DEVICES:
            raise DeviceError(f"unknown device {device!r}")
        if device == "quest" and not self._quest_connected():
            raise DeviceError("the Quest isn't connected over USB (plug in the cable and allow USB debugging)")
        if device == "mac" and self.platform != "darwin":
            raise DeviceError("this computer isn't a Mac")
        return device

    def _check(self, proc: subprocess.CompletedProcess, what: str) -> None:
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 or "Error" in out or "No activities found" in out:
            raise DeviceError(f"could not {what}: {(proc.stderr or proc.stdout or '').strip()[:200]}")

    # ------------------------------------------------------------- actions
    def open_url(self, url: str, device: str) -> str:
        url = safe_url(url)
        device = self._target(device)
        if device == "quest":
            self._check(self.run(["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, "com.oculus.browser"]),
                        f"open {url} on the Quest")
        else:
            self._check(self.run(["open", url]), f"open {url} on the Mac")
        return f"Opened {url} on the {'Quest' if device == 'quest' else 'Mac'}."

    def search(self, query: str, device: str) -> str:
        if not query.strip():
            raise DeviceError("nothing to search for")
        self.open_url(search_url(query), device)
        return f"Searching the web for “{query.strip()}” on the {'Quest' if device == 'quest' else 'Mac'}."

    def open_app(self, name: str, device: str) -> str:
        key = ALIASES.get(name.strip().lower(), name.strip().lower())
        name = DISPLAY.get(key, name.strip())
        device = self._target(device)
        app = APPS.get(key)
        where = "Quest" if device == "quest" else "Mac"
        if device == "quest":
            # An installed Quest app wins over its website (Instagram VR over instagram.com).
            package = (app.quest_package if app and app.quest_package else None) or self._find_package(key)
            if package:
                if not PACKAGE.match(package):
                    raise DeviceError(f"bad package {package!r}")
                self._check(self.run(["adb", "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]),
                            f"open {name} on the Quest")
                name = QUEST_NAMES.get(package) or (name if key in APPS or key in QUEST_ALIASES else pretty_name(package))
                return f"Opened {name} on the Quest."
            if app and app.quest_url:
                self.open_url(app.quest_url, "quest")
                return f"Opened {name} in the Quest browser."
        else:
            mac_app = app.mac_app if app else name.strip()
            if mac_app and self.run(["open", "-Ra", mac_app]).returncode == 0:  # installed?
                self._check(self.run(["open", "-a", mac_app]), f"open {mac_app}")
                return f"Opened {mac_app} on the Mac."
            if app and app.mac_url:
                self.open_url(app.mac_url, "mac")
                return f"Opened {name} in the Mac's browser."
        raise DeviceError(f"I don't know how to open {name!r} on the {where}")

    def _find_package(self, word: str) -> Optional[str]:
        """An installed Quest app by its spoken name: "beat saber" → com.beatgames.beatsaber."""
        key = re.sub(r"[^a-z0-9]", "", word.lower())
        if not key:
            return None
        if key in QUEST_ALIASES:
            return QUEST_ALIASES[key]
        out = self.run(["adb", "shell", "pm", "list", "packages"]).stdout
        hits = [line.split(":", 1)[1].strip() for line in out.splitlines()
                if line.startswith("package:") and key in re.sub(r"[^a-z0-9]", "", line.lower())]
        return min(hits, key=len) if hits else None

    # ------------------------------------------------------- Quest home
    def quest_apps(self) -> List[Dict[str, str]]:
        """Apps and games the user installed on the Quest (plus Meta's own apps they open)."""
        self._target("quest")
        out = self.run(["adb", "shell", "pm", "list", "packages", "-3"]).stdout
        apps = []
        for line in out.splitlines():
            if not line.startswith("package:"):
                continue
            pkg = line.split(":", 1)[1].strip()
            if any(skip in pkg for skip in QUEST_HIDDEN):
                continue
            apps.append({"package": pkg, "name": QUEST_NAMES.get(pkg) or pretty_name(pkg)})
        return sorted(apps, key=lambda a: a["name"].lower())

    def current_quest_app(self) -> Optional[str]:
        out = self.run(["adb", "shell", "dumpsys", "activity", "activities"]).stdout
        m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/", out)
        return m.group(1) if m else None

    def close_quest_app(self, name: Optional[str]) -> str:
        self._target("quest")
        pkg = self._find_package(name) if name else self.current_quest_app()
        if not pkg or pkg.startswith(("com.oculus.vrshell", "com.oculus.systemux")):
            raise DeviceError("nothing is open to close" if not name else f"I can't find {name!r} on the Quest")
        if not PACKAGE.match(pkg):
            raise DeviceError(f"bad package {pkg!r}")
        self._check(self.run(["adb", "shell", "am", "force-stop", pkg]), f"close {pkg}")
        return f"Closed {QUEST_NAMES.get(pkg) or pretty_name(pkg)}."

    def quest_home(self) -> str:
        self._target("quest")
        self._check(self.run(["adb", "shell", "am", "start", "-n", "com.oculus.vrshell/.HomeActivity"]), "go to the Quest home")
        return "Back on the Quest home screen."

    # ------------------------------------------------------------ messages
    def _contact_book(self) -> Dict[str, Dict[str, str]]:
        """.run/contacts.json: {"Mom": "+91 98765 43210"} or {"Prof Rao": {"phone": "...", "email": "rao@uni.edu"}}."""
        try:
            data = json.loads(self.contacts_file.read_text())
        except (OSError, ValueError):
            return {}
        book = {}
        for name, v in (data.items() if isinstance(data, dict) else []):
            entry = v if isinstance(v, dict) else ({"email": v} if "@" in str(v) else {"phone": str(v)})
            book[name.strip().lower()] = {k: str(x).strip() for k, x in entry.items()}
        return book

    def contacts(self) -> Dict[str, str]:
        return {k: re.sub(r"\D", "", v["phone"]) for k, v in self._contact_book().items() if v.get("phone")}

    def resolve_email(self, to: str) -> str:
        addresses = []
        for part in re.split(r"[,;]| and ", to):
            part = part.strip()
            if not part:
                continue
            if EMAIL.fullmatch(part):
                addresses.append(part)
                continue
            email = self._contact_book().get(part.lower(), {}).get("email")
            if not email or not EMAIL.fullmatch(email):
                raise DeviceError(f"I don't have an email address for {part!r}. Say the address, or add it to .run/contacts.json")
            addresses.append(email)
        if not addresses:
            raise DeviceError("who should the email go to?")
        return ",".join(addresses)

    def open_gmail_draft(self, to: str, subject: str, body: str, device: str, cc: str = "") -> str:
        """Open Gmail's compose window with everything filled in. Nothing is sent: the user
        reviews it and presses Send (a browser keypress could land in the wrong window)."""
        params = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
        if cc:
            params["cc"] = cc
        url = "https://mail.google.com/mail/?" + "&".join(f"{k}={quote(v)}" for k, v in params.items())
        return self.open_url(url, device)

    def resolve_number(self, to: str) -> str:
        digits = re.sub(r"\D", "", to)
        if len(digits) >= 8:
            return digits
        number = self.contacts().get(to.strip().lower())
        if not number:
            raise DeviceError(f"I don't have a WhatsApp number for {to!r}. Say the number, or add it to .run/contacts.json")
        return number

    def open_whatsapp_chat(self, number: str, text: str, device: str) -> bool:
        """Open the chat with the message typed in, ready to send. Returns True when the
        Mac's WhatsApp app (not the website) holds the chat."""
        device = self._target(device)
        if device == "quest":
            url = f"https://wa.me/{number}?text={quote(text)}"
            self._check(self.run(["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, "com.whatsapp"]),
                        "open the WhatsApp chat on the Quest")
        else:
            native = self.run(["open", "-Ra", "WhatsApp"]).returncode == 0
            url = (f"whatsapp://send?phone={number}&text={quote(text)}" if native
                   else f"https://web.whatsapp.com/send?phone={number}&text={quote(text)}")
            self._check(self.run(["open", url]), "open the WhatsApp chat on the Mac")
            return native
        return False

    def press_send(self, device: str, native: bool) -> bool:
        """Press Return in the Mac's WhatsApp app — brought to the front first, so the key
        can never land in another app. Needs Accessibility permission; returns False when
        it cannot, and never on the Quest or WhatsApp Web (the user presses send)."""
        if device != "mac" or not native:
            return False
        self.sleep(2.5)  # let the chat open with the text
        script = 'tell application "WhatsApp" to activate\ndelay 0.5\ntell application "System Events" to tell process "WhatsApp" to key code 36'
        return self.run(["osascript", "-e", script]).returncode == 0
