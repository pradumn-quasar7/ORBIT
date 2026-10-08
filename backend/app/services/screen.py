"""Screen control by voice (Phase 19.5): scroll, page, navigate and zoom what the user
is looking at.

- Quest: the page in front of the user in the Quest browser, through its DevTools
  socket over USB (the same channel as video control). ORBIT's own AR page is skipped.
- Mac: the frontmost app, through standard keys sent by System Events (needs the
  macOS Accessibility permission once). Only navigation keys are ever sent — nothing
  that types, deletes or submits.
"""
from typing import Any, Callable, Dict, List, Optional

from backend.app.core.cdp import CDP, targets
from backend.app.services.devices import DeviceController, DeviceError
from backend.app.services.media import QUEST_PORT, MediaError

ACTIONS = ("scroll_down", "scroll_up", "scroll_left", "scroll_right", "page_down", "page_up", "top", "bottom",
           "back", "forward", "reload", "zoom_in", "zoom_out", "zoom_reset")
SAY = {"scroll_down": "Scrolled down.", "scroll_up": "Scrolled up.", "scroll_left": "Scrolled left.", "scroll_right": "Scrolled right.",
       "page_down": "Page down.", "page_up": "Page up.", "top": "At the top.", "bottom": "At the bottom.", "back": "Went back.",
       "forward": "Went forward.", "reload": "Reloaded.", "zoom_in": "Zoomed in.", "zoom_out": "Zoomed out.", "zoom_reset": "Zoom reset."}

# Mac: macOS key codes, with modifiers. Arrow keys repeat for a "scroll"; page keys jump.
MAC_KEYS = {
    "scroll_down": ("125", "", 4), "scroll_up": ("126", "", 4), "scroll_left": ("123", "", 4), "scroll_right": ("124", "", 4),
    "page_down": ("121", "", 1), "page_up": ("116", "", 1), "top": ("115", "", 1), "bottom": ("119", "", 1),
    "back": ("33", "command down", 1), "forward": ("30", "command down", 1), "reload": ("15", "command down", 1),
    "zoom_in": ("24", "command down", 1), "zoom_out": ("27", "command down", 1), "zoom_reset": ("29", "command down", 1),
}


SCROLLER = """(() => {
  const se = document.scrollingElement || document.documentElement;
  if (se.scrollHeight > innerHeight + 10 || se.scrollWidth > innerWidth + 10) return se;
  let best = null, area = 0;  // pages that scroll an inner panel instead of the window
  for (const el of document.querySelectorAll('div, main, section, article, ytd-app, [role=main]')) {
    const cs = getComputedStyle(el);
    if (/(auto|scroll)/.test(cs.overflowY + cs.overflowX) && (el.scrollHeight > el.clientHeight + 10 || el.scrollWidth > el.clientWidth + 10)) {
      const a = el.clientWidth * el.clientHeight; if (a > area) { area = a; best = el; }
    }
  }
  return best || se;
})()"""


def page_script(action: str, amount: float) -> str:
    """JavaScript for one action in a web page (Quest). Scroll actions report whether
    anything actually moved, so Orbi never claims a scroll that didn't happen."""
    moves = {
        "scroll_down": (0, f"el.clientHeight * {amount}"), "scroll_up": (0, f"-el.clientHeight * {amount}"),
        "scroll_left": (f"-el.clientWidth * {amount}", 0), "scroll_right": (f"el.clientWidth * {amount}", 0),
        "page_down": (0, "el.clientHeight * 0.9"), "page_up": (0, "-el.clientHeight * 0.9"),
        "top": (0, "-el.scrollHeight"), "bottom": (0, "el.scrollHeight"),
    }
    if action in moves:
        dx, dy = moves[action]
        return (f"(() => {{ if (document.fullscreenElement) return {{moved: false, fullscreen: true}};"
                f" const el = {SCROLLER}; const x = el.scrollLeft, y = el.scrollTop;"
                f" el.scrollBy({{left: {dx}, top: {dy}, behavior: 'instant'}});"
                f" return {{moved: el.scrollLeft !== x || el.scrollTop !== y, fullscreen: false}}; }})()")
    return {
        "back": "history.back()", "forward": "history.forward()", "reload": "location.reload()",
        "zoom_in": "document.body.style.zoom = String((parseFloat(document.body.style.zoom) || 1) * 1.15)",
        "zoom_out": "document.body.style.zoom = String((parseFloat(document.body.style.zoom) || 1) / 1.15)",
        "zoom_reset": "document.body.style.zoom = '1'",
    }[action]


def amount_of(value: Any) -> float:
    """'a little' / 'a lot' / '2' screens → a fraction of the screen."""
    v = str(value or "").lower()
    if any(w in v for w in ("little", "bit", "slight")):
        return 0.3
    if any(w in v for w in ("lot", "more", "far", "much")):
        return 1.5
    try:
        return max(0.1, min(5.0, float(v)))
    except ValueError:
        return 0.7


class ScreenController:
    def __init__(self, devices: DeviceController, pages: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 connect: Callable[[str], Any] = CDP):
        self.devices = devices
        self.pages = pages or (lambda: [t for t in targets(QUEST_PORT) if t.get("type") == "page"])
        self.connect = connect

    def control(self, action: str, value: Any, device: str) -> Dict[str, Any]:
        if action not in ACTIONS:
            raise MediaError(f"unknown screen action {action!r}")
        device = self.devices._target(device)
        return self._quest(action, value) if device == "quest" else self._mac(action, value)

    def _quest(self, action: str, value: Any) -> Dict[str, Any]:
        self.devices.run(["adb", "forward", f"tcp:{QUEST_PORT}", "localabstract:chrome_devtools_remote"])
        try:
            pages = self.pages()
        except OSError:
            pages = []
        # The page in front: the most recent tab that isn't ORBIT's own AR page.
        page = next((p for p in pages if "/ui/xr.html" not in p.get("url", "")), None)
        if page is None:
            raise MediaError("no web page is open in the Quest browser (only ORBIT's AR view)")
        cdp = self.connect(page["webSocketDebuggerUrl"])
        try:
            out = cdp.evaluate(page_script(action, amount_of(value)), gesture=True)
        finally:
            cdp.close()
        if isinstance(out, dict) and out.get("fullscreen"):
            return {"ok": False, "error": "a video is full screen, so there's nothing to scroll. Say 'escape' first"}
        if isinstance(out, dict) and not out.get("moved"):
            edge = {"scroll_down": "the bottom", "page_down": "the bottom", "bottom": "the bottom", "scroll_up": "the top",
                    "page_up": "the top", "top": "the top", "scroll_left": "the left edge", "scroll_right": "the right edge"}[action]
            return {"ok": True, "result": f"It's already at {edge}."}
        return {"ok": True, "result": SAY[action]}

    def _mac(self, action: str, value: Any) -> Dict[str, Any]:
        code, modifier, repeat = MAC_KEYS[action]
        if action.startswith("scroll_"):
            repeat = max(1, round(repeat * amount_of(value) / 0.7))
        using = f" using {{{modifier}}}" if modifier else ""
        script = "tell application \"System Events\"\n" + "\n".join(f"key code {code}{using}" for _ in range(min(repeat, 20))) + "\nend tell"
        proc = self.devices.run(["osascript", "-e", script])
        if proc.returncode != 0:
            raise DeviceError("macOS blocked it: allow ORBIT in System Settings → Privacy & Security → Accessibility "
                              "(and Automation), then try again")
        return {"ok": True, "result": SAY[action]}
