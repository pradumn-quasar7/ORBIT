"""Video by voice (Phase 19.3): find and play a YouTube video, then control it.

"Play tum ho toh from Saiyaara" → the first matching video, playing. "Full screen",
"1080p", "pause", "skip 30 seconds", "exit full screen" → YouTube's own player API in
that tab, driven through the browser's debugging connection:

- Quest: the Quest browser, reached over the USB cable (``adb forward`` to its
  DevTools socket) — the same channel used to diagnose the headset.
- Mac: ORBIT's own Chrome window (separate profile, debugging port 9223), so the
  user's everyday Chrome is never touched.

Playing and controlling a video is reversible and user-requested, so it runs directly.
"""
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from backend.app.core.cdp import CDP, CDPError, new_tab, targets
from backend.app.services.devices import DeviceController, DeviceError

RUN = Path(__file__).resolve().parents[3] / ".run"
QUEST_PORT = 9222
MAC_PORT = 9223
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"

# Spoken quality → YouTube's quality names.
QUALITY = {
    "144": "tiny", "240": "small", "360": "medium", "480": "large", "sd": "large", "720": "hd720", "hd": "hd720",
    "1080": "hd1080", "full hd": "hd1080", "fhd": "hd1080", "1440": "hd1440", "2k": "hd1440", "qhd": "hd1440",
    "2160": "hd2160", "4k": "hd2160", "uhd": "hd2160", "best": "highres", "highest": "highres", "max": "highres",
    "lowest": "tiny", "low": "small", "medium": "medium", "auto": "auto",
}
LADDER = ["tiny", "small", "medium", "large", "hd720", "hd1080", "hd1440", "hd2160", "highres"]
LABEL = {"tiny": "144p", "small": "240p", "medium": "360p", "large": "480p", "hd720": "720p", "hd1080": "1080p",
         "hd1440": "1440p", "hd2160": "4K", "highres": "the highest quality", "auto": "auto"}
ACTIONS = ("pause", "resume", "fullscreen", "exit_fullscreen", "quality", "mute", "unmute", "volume",
           "forward", "back", "speed", "next", "status")

PLAYER = "document.getElementById('movie_player')"
READY = f"(() => {{ const p = {PLAYER}; return !!(p && typeof p.getPlayerState === 'function'); }})()"
STATUS = f"""(() => {{ const p = {PLAYER}; const d = p.getVideoData ? p.getVideoData() : {{}};
  return {{ title: d.title || document.title.replace(/ - YouTube$/, ''), state: p.getPlayerState(),
           quality: p.getPlaybackQuality(), qualities: p.getAvailableQualityLevels(), time: Math.round(p.getCurrentTime()),
           duration: Math.round(p.getDuration()), muted: p.isMuted(), volume: p.getVolume(), fullscreen: !!document.fullscreenElement }}; }})()"""


class MediaError(ValueError):
    pass


def parse_quality(value: str) -> str:
    v = str(value or "").lower().replace("p", "").strip()
    v = re.sub(r"\s+", " ", v)
    if v in QUALITY:
        return QUALITY[v]
    digits = re.search(r"\d{3,4}", v)
    if digits and digits.group(0) in QUALITY:
        return QUALITY[digits.group(0)]
    raise MediaError(f"I don't know the quality {value!r}: say 144p to 4K, best, or auto")


def search_youtube(query: str, fetch: Optional[Callable[[str], str]] = None) -> List[Dict[str, Any]]:
    """First videos YouTube shows for the query (no API key: the results page's data)."""
    url = "https://www.youtube.com/results?search_query=" + quote(query) + "&sp=EgIQAQ%253D%253D"  # videos only
    if fetch is None:
        def fetch(u):
            req = urllib.request.Request(u, headers={"user-agent": UA, "accept-language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (fixed YouTube URL)
                return r.read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", fetch(url), re.S)
    if not m:
        return []
    found: List[Dict[str, Any]] = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and re.fullmatch(r"[\w-]{11}", v.get("videoId", "")):
                found.append({"id": v["videoId"], "title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
                              "length": v.get("lengthText", {}).get("simpleText"),
                              "channel": ((v.get("ownerText") or {}).get("runs") or [{}])[0].get("text")})
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.loads(m.group(1)))
    return found


class Browser:
    """A Chromium browser ORBIT may drive through its DevTools socket."""

    def __init__(self, device: str, devices: DeviceController):
        self.device = device
        self.devices = devices
        self.port = QUEST_PORT if device == "quest" else MAC_PORT

    def _pages(self) -> List[Dict[str, Any]]:
        try:
            return [t for t in targets(self.port) if t.get("type") == "page"]
        except OSError:
            return []

    def ensure(self, first_url: str) -> None:
        if self.device == "quest":
            self.devices._target("quest")  # connected?
            self.devices.run(["adb", "forward", f"tcp:{self.port}", "localabstract:chrome_devtools_remote"])
            if not self._pages():
                raise MediaError("I can't reach the Quest browser: open the Browser app in the headset")
        elif not self._pages():  # start ORBIT's own Chrome window for videos (its own profile)
            self.devices._target("mac")
            profile = RUN / "chrome-media"
            profile.mkdir(parents=True, exist_ok=True)
            self.devices.run(["open", "-na", "Google Chrome", "--args", f"--remote-debugging-port={self.port}",
                              f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", first_url])
            for _ in range(40):
                if self._pages():
                    return
                time.sleep(0.25)
            raise MediaError("Chrome didn't start for videos (is Google Chrome installed?)")

    def youtube_tab(self) -> Optional[Dict[str, Any]]:
        tabs = [t for t in self._pages() if "youtube.com" in t.get("url", "")]
        watching = [t for t in tabs if "/watch" in t["url"]]
        return (watching or tabs or [None])[0]

    def open(self, url: str) -> CDP:
        self.ensure(url)
        tab = self.youtube_tab()
        if tab is None and self.device == "mac":
            pages = self._pages()
            tab = next((p for p in pages if p.get("url", "").startswith(("about:", "chrome://newtab"))), None) or new_tab(self.port, url)
        if tab is None:  # Quest: a new browser tab via an intent, then find it
            self.devices.open_url(url, "quest")
            for _ in range(40):
                tab = self.youtube_tab()
                if tab:
                    break
                time.sleep(0.25)
            if tab is None:
                raise MediaError("the video tab didn't open in the Quest browser")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        if url not in tab.get("url", ""):
            cdp.call("Page.navigate", {"url": url})
        try:
            cdp.call("Page.bringToFront")
        except CDPError:
            pass
        return cdp

    def current(self) -> CDP:
        tab = self.youtube_tab()
        if tab is None:
            raise MediaError("no YouTube video is open" + (" in the headset" if self.device == "quest" else " on the Mac"))
        return CDP(tab["webSocketDebuggerUrl"])


class MediaController:
    def __init__(self, devices: DeviceController, browser: Optional[Callable[[str], Any]] = None,
                 search: Callable[[str], List[Dict[str, Any]]] = search_youtube, sleep: Callable[[float], None] = time.sleep):
        self.devices = devices
        self.browser = browser or (lambda device: Browser(device, devices))
        self.search = search
        self.sleep = sleep

    def _wait_ready(self, cdp: CDP, seconds: float = 12.0) -> None:
        for _ in range(int(seconds / 0.3)):
            try:
                if cdp.evaluate(READY):
                    return
            except CDPError:
                pass  # the page is navigating
            self.sleep(0.3)
        raise MediaError("the YouTube player didn't load")

    def play(self, query: str, device: str) -> Dict[str, Any]:
        query = query.strip()
        if not query:
            raise MediaError("what should I play?")
        videos = self.search(query)
        if not videos:
            raise MediaError(f"I couldn't find a video for {query!r}")
        v = videos[0]
        cdp = self.browser(device).open(f"https://www.youtube.com/watch?v={v['id']}")
        try:
            self._wait_ready(cdp)
            cdp.evaluate(f"(() => {{ const p = {PLAYER}; p.unMute(); p.playVideo(); return true; }})()", gesture=True)
        finally:
            cdp.close()
        where = "in the headset" if device == "quest" else "on the Mac"
        return {"ok": True, "result": f"Playing “{v['title']}” {where}.", "video": v,
                "also_found": [x["title"] for x in videos[1:3]]}

    def control(self, action: str, value: Any, device: str) -> Dict[str, Any]:
        if action not in ACTIONS:
            raise MediaError(f"unknown video action {action!r}")
        cdp = self.browser(device).current()
        try:
            self._wait_ready(cdp, 4)
            return {"ok": True, **self._do(cdp, action, value)}
        finally:
            cdp.close()

    def _do(self, cdp: CDP, action: str, value: Any) -> Dict[str, Any]:
        p = PLAYER
        if action == "status":
            s = cdp.evaluate(STATUS)
            state = {1: "playing", 2: "paused", 3: "loading", 0: "finished", -1: "not started", 5: "ready"}.get(s["state"], "")
            return {"result": f"“{s['title']}”, {state}, {s['time'] // 60}:{s['time'] % 60:02d} of {s['duration'] // 60}:{s['duration'] % 60:02d},"
                              f" {LABEL.get(s['quality'], s['quality'])}" + (", full screen" if s["fullscreen"] else ""), "status": s}
        if action == "pause":
            cdp.evaluate(f"{p}.pauseVideo()")
            return {"result": "Paused."}
        if action == "resume":
            cdp.evaluate(f"{p}.playVideo()", gesture=True)
            return {"result": "Playing."}
        if action == "fullscreen":
            done = cdp.evaluate("""(() => { if (document.fullscreenElement) return 'already';
                const b = document.querySelector('.ytp-fullscreen-button'); if (b) { b.click(); return 'ok'; }
                return document.getElementById('movie_player').requestFullscreen().then(() => 'ok'); })()""", gesture=True)
            return {"result": "It's already full screen." if done == "already" else "Full screen."}
        if action == "exit_fullscreen":
            done = cdp.evaluate("(() => { if (!document.fullscreenElement) return 'not'; return document.exitFullscreen().then(() => 'ok'); })()")
            return {"result": "It isn't full screen." if done == "not" else "Exited full screen."}
        if action == "mute":
            cdp.evaluate(f"{p}.mute()")
            return {"result": "Muted."}
        if action == "unmute":
            cdp.evaluate(f"{p}.unMute()")
            return {"result": "Sound on."}
        if action == "volume":
            n = max(0, min(100, int(float(re.sub(r"[^\d.]", "", str(value)) or 50))))
            cdp.evaluate(f"(() => {{ {p}.unMute(); {p}.setVolume({n}); }})()")
            return {"result": f"Volume {n}%."}
        if action in ("forward", "back"):
            secs = int(float(re.sub(r"[^\d.]", "", str(value)) or 10))
            sign = 1 if action == "forward" else -1
            cdp.evaluate(f"(() => {{ const q = {p}; q.seekTo(Math.max(0, q.getCurrentTime() + {sign * secs}), true); }})()")
            return {"result": f"{'Forward' if sign > 0 else 'Back'} {secs} seconds."}
        if action == "speed":
            rate = max(0.25, min(2.0, float(re.sub(r"[^\d.]", "", str(value)) or 1)))
            cdp.evaluate(f"{p}.setPlaybackRate({rate})")
            return {"result": f"Speed {rate:g}×."}
        if action == "next":
            cdp.evaluate(f"{p}.nextVideo()", gesture=True)
            return {"result": "Next video."}
        # quality
        want = parse_quality(value)
        available = cdp.evaluate(f"{p}.getAvailableQualityLevels()") or []
        if want == "auto":
            cdp.evaluate(f"{p}.setPlaybackQualityRange('auto')")
            return {"result": "Quality set to auto."}
        if want == "highres":
            target = next((q for q in available if q != "auto"), want)
        elif want in available:
            target = want
        else:  # the best this video offers below what was asked
            lower = [q for q in LADDER[:LADDER.index(want)] if q in available]
            target = lower[-1] if lower else next((q for q in available if q != "auto"), want)
        cdp.evaluate(f"(() => {{ const q = {p}; q.setPlaybackQualityRange('{target}', '{target}'); if (q.setPlaybackQuality) q.setPlaybackQuality('{target}'); }})()")
        self.sleep(1.2)
        now = cdp.evaluate(f"{p}.getPlaybackQuality()")
        note = "" if target == want or want == "highres" else f" (this video doesn't have {LABEL.get(want, want)})"
        return {"result": f"Quality {LABEL.get(now if now != 'unknown' else target, target)}{note}.", "quality": now}
