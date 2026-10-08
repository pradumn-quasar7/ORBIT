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
ALIASES = {"whats app": "whatsapp", "insta": "instagram", "ig": "instagram", "fb": "facebook", "yt": "youtube",
           "chrome": "browser", "safari": "browser", "web browser": "browser", "google maps": "maps", "mail": "gmail"}


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
        device = self._target(device)
        app = APPS.get(key)
        where = "Quest" if device == "quest" else "Mac"
        if device == "quest":
            package = app.quest_package if app else self._find_package(key)
            if package:
                if not PACKAGE.match(package):
                    raise DeviceError(f"bad package {package!r}")
                self._check(self.run(["adb", "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]),
                            f"open {name} on the Quest")
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
        out = self.run(["adb", "shell", "pm", "list", "packages"]).stdout
        hits = [line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:") and word.replace(" ", "") in line.lower()]
        return min(hits, key=len) if hits else None

    # ------------------------------------------------------------ messages
    def contacts(self) -> Dict[str, str]:
        try:
            data = json.loads(self.contacts_file.read_text())
            return {k.lower(): re.sub(r"\D", "", str(v)) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            return {}

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
