"""Phase 19.3: play YouTube videos by voice and control them (quality, full screen, …)."""
import json

import pytest

from backend.app.services.devices import DeviceController
from backend.app.services.live import LiveService, LiveSettings
from backend.app.services.media import MediaController, MediaError, parse_quality, search_youtube
from backend.tests.test_assistant import Lab, at
from backend.tests.test_live import FakeShell


class FakePlayer:
    """Stands in for a YouTube tab: answers the player API the way YouTube does."""

    def __init__(self):
        self.scripts, self.gestures, self.closed = [], [], 0
        self.quality, self.available, self.fullscreen = "hd720", ["hd1080", "hd720", "large", "medium", "auto"], False

    def evaluate(self, js, gesture=False):
        self.scripts.append(js)
        self.gestures.append(gesture)
        if "typeof p.getPlayerState" in js:
            return True
        if "getAvailableQualityLevels()" in js and js.strip().endswith("getAvailableQualityLevels()"):
            return self.available
        if "setPlaybackQualityRange('" in js:
            self.quality = js.split("setPlaybackQualityRange('")[1].split("'")[0]
            return None
        if js.strip().endswith("getPlaybackQuality()"):
            return self.quality
        if "ytp-fullscreen-button" in js:
            was, self.fullscreen = self.fullscreen, True
            return "already" if was else "ok"
        if "exitFullscreen" in js:
            was, self.fullscreen = self.fullscreen, False
            return "ok" if was else "not"
        if "getVideoData" in js:
            return {"title": "Tum Ho Toh", "state": 1, "quality": self.quality, "qualities": self.available, "time": 75,
                    "duration": 318, "muted": False, "volume": 80, "fullscreen": self.fullscreen}
        return None

    def close(self):
        self.closed += 1


class FakeBrowser:
    def __init__(self, player):
        self.player, self.opened = player, []

    def open(self, url):
        self.opened.append(url)
        return self.player

    def current(self):
        return self.player


VIDEOS = [{"id": "rOUuGvJkBrQ", "title": "Tum Ho Toh Song | Saiyaara", "length": "3:18", "channel": "YRF"},
          {"id": "8SYPKQMW_2Q", "title": "Lyrical | Tum Ho Toh", "length": "5:17", "channel": "YRF"}]


@pytest.fixture
def media():
    player = FakePlayer()
    browsers = {}
    mc = MediaController(DeviceController(runner=FakeShell(), platform="darwin"),
                         browser=lambda d: browsers.setdefault(d, FakeBrowser(player)), search=lambda q: VIDEOS, sleep=lambda s: None)
    mc.player, mc.browsers = player, browsers
    return mc


def test_play_finds_and_plays_the_first_video(media):
    r = media.play("tum ho toh saiyaara", "quest")
    assert r["ok"] and r["result"] == "Playing “Tum Ho Toh Song | Saiyaara” in the headset."
    assert media.browsers["quest"].opened == ["https://www.youtube.com/watch?v=rOUuGvJkBrQ"]
    play = media.player.scripts[-1]
    assert "unMute()" in play and "playVideo()" in play and media.player.gestures[-1] is True  # a user gesture: sound allowed
    assert media.player.closed == 1
    with pytest.raises(MediaError):
        media.play("  ", "mac")


@pytest.mark.parametrize("spoken, expected", [("1080p", "hd1080"), ("720", "hd720"), ("4K", "hd2160"), ("full HD", "hd1080"),
                                              ("best", "highres"), ("auto", "auto"), ("144p", "tiny"), ("quality 480", "large")])
def test_spoken_quality(spoken, expected):
    assert parse_quality(spoken) == expected


def test_quality_full_screen_and_escape(media):
    assert media.control("quality", "1080p", "mac")["result"] == "Quality 1080p."
    r = media.control("quality", "4k", "mac")  # not offered by this video: the best below it
    assert r["result"] == "Quality 1080p (this video doesn't have 4K)."
    assert media.control("quality", "best", "mac")["result"] == "Quality 1080p."
    assert media.control("fullscreen", None, "mac")["result"] == "Full screen."
    assert media.player.gestures[-1] is True  # full screen needs a user gesture
    assert media.control("fullscreen", None, "mac")["result"] == "It's already full screen."
    assert media.control("exit_fullscreen", None, "mac")["result"] == "Exited full screen."
    assert media.control("exit_fullscreen", None, "mac")["result"] == "It isn't full screen."


def test_other_controls(media):
    assert media.control("pause", None, "quest")["result"] == "Paused."
    assert media.control("volume", "70 percent", "quest")["result"] == "Volume 70%."
    assert media.control("forward", "30 seconds", "quest")["result"] == "Forward 30 seconds."
    assert "getCurrentTime() + 30" in media.player.scripts[-1]
    assert media.control("back", None, "quest")["result"] == "Back 10 seconds."
    assert media.control("speed", "1.5", "quest")["result"] == "Speed 1.5×."
    status = media.control("status", None, "quest")["result"]
    assert status.startswith("“Tum Ho Toh”, playing, 1:15 of 5:18")
    with pytest.raises(MediaError):
        media.control("delete_account", None, "quest")


def test_search_reads_youtube_results():
    data = {"contents": {"x": [{"videoRenderer": {"videoId": "rOUuGvJkBrQ", "title": {"runs": [{"text": "Tum Ho Toh "}, {"text": "Song"}]},
                                                  "lengthText": {"simpleText": "3:18"}, "ownerText": {"runs": [{"text": "YRF"}]}}},
                               {"videoRenderer": {"videoId": "bad"}}, {"channelRenderer": {"channelId": "c"}}]}}
    html = f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"
    found = search_youtube("tum ho toh", fetch=lambda url: html)
    assert found == [{"id": "rOUuGvJkBrQ", "title": "Tum Ho Toh Song", "length": "3:18", "channel": "YRF"}]
    assert search_youtube("x", fetch=lambda url: "<html>nothing</html>") == []


def test_video_tools_through_orbi(repo, media):
    lab = Lab(repo)
    live = LiveService(lab.svc.assistant, media.devices, LiveSettings(api_key=None))
    live.media = media
    cid = lab.svc.assistant.start("ana", at(1)).id
    r = live.call("play_video", {"query": "tum ho toh", "device": "here"}, cid, "quest", "open youtube and play tum ho toh", at(2))
    assert r["ok"] and "in the headset" in r["result"]
    assert live.call("video_control", {"action": "fullscreen"}, cid, "quest", "full screen", at(3))["result"] == "Full screen."
    assert live.call("video_control", {"action": "quality", "value": "eleventy"}, cid, "quest", "", at(4))["ok"] is False
