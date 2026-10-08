"""Phase 19.5: scroll and move the screen by voice (Quest page via DevTools, Mac front app via keys)."""
import subprocess

import pytest

from backend.app.services.devices import DeviceController, DeviceError
from backend.app.services.media import MediaError
from backend.app.services.screen import ScreenController, amount_of, page_script
from backend.tests.test_live import FakeShell


class Page:
    def __init__(self, answer=None):
        self.scripts, self.gestures, self.closed = [], [], False
        self.answer = answer if answer is not None else {"moved": True, "fullscreen": False}

    def evaluate(self, js, gesture=False):
        self.scripts.append(js)
        self.gestures.append(gesture)
        return self.answer if "moved" in js else None

    def close(self):
        self.closed = True


def controller(shell=None, pages=None):
    page = Page()
    sc = ScreenController(DeviceController(runner=shell or FakeShell(), platform="darwin"),
                          pages=lambda: pages if pages is not None else [
                              {"url": "http://localhost:8765/ui/xr.html", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/xr"},
                              {"url": "https://news.example/story", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/story"}],
                          connect=lambda url: page)
    sc.page = page
    return sc


def test_quest_scrolls_the_page_in_front_not_orbit(monkeypatch):
    sc = controller()
    seen = []
    sc.connect = lambda url: seen.append(url) or sc.page
    assert sc.control("scroll_down", "a little", "quest") == {"ok": True, "result": "Scrolled down."}
    assert seen == ["ws://127.0.0.1:9222/story"]  # never ORBIT's own AR page
    assert "top: el.clientHeight * 0.3" in sc.page.scripts[-1] and "behavior: 'instant'" in sc.page.scripts[-1] and sc.page.closed
    sc.control("back", None, "quest")
    assert sc.page.scripts[-1] == "history.back()"
    with pytest.raises(MediaError, match="only ORBIT"):
        controller(pages=[{"url": "http://localhost:8765/ui/xr.html", "webSocketDebuggerUrl": "ws://x"}]).control("top", None, "quest")


def test_mac_sends_only_navigation_keys_to_the_front_app():
    shell = FakeShell()
    sc = controller(shell)
    assert sc.control("page_down", None, "mac")["result"] == "Page down."
    script = [c for c in shell.calls if c[0] == "osascript"][-1][2]
    assert "key code 121" in script and "keystroke" not in script  # keys, never typing
    sc.control("scroll_up", "a lot", "mac")
    assert [c for c in shell.calls if c[0] == "osascript"][-1][2].count("key code 126") == 9
    sc.control("zoom_in", None, "mac")
    assert "key code 24 using {command down}" in [c for c in shell.calls if c[0] == "osascript"][-1][2]


def test_mac_without_permission_says_how_to_fix():
    class Denied(FakeShell):
        def __call__(self, cmd):
            if cmd[0] == "osascript":
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "not allowed assistive access")
            return super().__call__(cmd)
    with pytest.raises(DeviceError, match="Accessibility"):
        controller(Denied()).control("scroll_down", None, "mac")


def test_amounts_and_unknown_actions():
    assert amount_of("a little bit") == 0.3 and amount_of("a lot") == 1.5 and amount_of("2") == 2.0 and amount_of(None) == 0.7
    assert "top: -el.scrollHeight" in page_script("top", 1) and "document.fullscreenElement" in page_script("top", 1)
    with pytest.raises(MediaError):
        controller().control("delete_everything", None, "quest")


def test_quest_tells_the_truth_about_scrolling():
    sc = controller()
    sc.page.answer = {"moved": False, "fullscreen": True}
    r = sc.control("scroll_down", None, "quest")
    assert r["ok"] is False and "full screen" in r["error"] and "escape" in r["error"]
    sc.page.answer = {"moved": False, "fullscreen": False}
    assert sc.control("scroll_down", None, "quest")["result"] == "It's already at the bottom."
    assert sc.control("top", None, "quest")["result"] == "It's already at the top."
