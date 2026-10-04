"""Phase 17: realtime — commit notifications, the event bus, the SSE stream, and an
assistant that speaks up when the world changes."""
import asyncio
import json

import pytest

from backend.app.core.realtime import EventBus
from backend.app.domain.models import Event, Observation, ObservedEntity
from backend.app.domain.types import ActionStatus, EventType
from backend.app.services.assistant import Gesture
from backend.tests.test_assistant import Lab, at, ent


# ------------------------------------------------------------ commit semantics
def test_listeners_see_only_committed_writes_in_order(repo):
    seen = []
    repo.on_commit(lambda items: seen.append([i.id for i in items]))
    with repo.transaction():
        repo.save_event(Event(id="e1", timestamp=at(0), event_type=EventType.OBJECT_ADDED))
        repo.save_observation(Observation(id="o1", timestamp=at(0), source="camera"))
        repo.save_observation(Observation(id="o1", timestamp=at(0), source="camera", session_id="s"))  # re-saved
        repo.save_event(Event(id="e2", timestamp=at(0), event_type=EventType.OBJECT_MOVED))
        assert seen == []  # nothing is announced before commit
    assert seen == [["e1", "o1", "e2"]]

    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.save_event(Event(id="e3", timestamp=at(1), event_type=EventType.OBJECT_ADDED))
            raise RuntimeError("rolled back")
    assert seen == [["e1", "o1", "e2"]] and repo.get_observation("o1").session_id == "s"

    repo.save_event(Event(id="e4", timestamp=at(2), event_type=EventType.OBJECT_ADDED))  # no transaction: immediate
    assert seen[-1] == ["e4"]


def test_a_failing_listener_never_fails_the_write(repo):
    repo.on_commit(lambda items: 1 / 0)
    repo.save_event(Event(id="e1", timestamp=at(0), event_type=EventType.OBJECT_ADDED))
    assert [e.id for e in repo.list_events()] == ["e1"]


# ------------------------------------------------------------------------ bus
def test_bus_fans_out_with_filters_replay_and_overflow():
    async def run():
        bus = EventBus(replay=10, queue_size=2)
        bus.publish("world", "OBJECT_ADDED", {"n": 1})
        everything = bus.subscribe(since=0)  # replays what was missed
        worlds = bus.subscribe(topics={"world"})
        mine = bus.subscribe(topics={"assistant"}, conversation="c1")
        bus.publish("observation", "OBSERVATION", {"n": 2})
        bus.publish("assistant", "NOTICE", {"n": 3}, conversation="c1")
        bus.publish("assistant", "NOTICE", {"n": 4}, conversation="c2")
        bus.publish("world", "OBJECT_MOVED", {"n": 5})
        await asyncio.sleep(0)
        drain = lambda s: [s.queue.get_nowait()["data"]["n"] for _ in range(s.queue.qsize())]
        assert drain(worlds) == [5]
        assert drain(mine) == [3]  # never another conversation's notices
        assert drain(everything) == [2, 5]  # 1 (replayed) was dropped: bounded queue keeps the newest
        assert everything.dropped == 1
        for s in (everything, worlds, mine):
            s.close()
        assert bus.subscriber_count == 0 and [m["seq"] for m in bus.replay(3)] == [4, 5]
    asyncio.run(run())


def test_bus_listener_errors_are_contained():
    bus = EventBus()
    calls = []
    bus.listen(lambda m: 1 / 0)
    bus.listen(lambda m: calls.append(m["type"]))
    bus.publish("world", "OBJECT_ADDED", {})
    assert calls == ["OBJECT_ADDED"]


# ------------------------------------------------------------------- stream
def test_camera_snapshot_streams_world_and_observation_messages(client):
    client.put("/cameras/webcam/config", json={"view": "desk", "regions": []})
    client.post("/cameras/webcam/snapshot", json={"detections": [{"label": "cup", "confidence": 0.9, "bbox": [0.1, 0.1, 0.2, 0.2], "marker_id": "mug_7"}]})
    recent = client.get("/stream/recent").json()
    kinds = [(m["topic"], m["type"]) for m in recent]
    assert ("world", "OBJECT_ADDED") in kinds and ("observation", "OBSERVATION") in kinds
    obs = next(m for m in recent if m["topic"] == "observation")
    assert obs["data"]["entities"] == ["mug_7"] and obs["data"]["field_of_view"] == "desk"
    assert [m["seq"] for m in recent] == sorted(m["seq"] for m in recent)

    n_world = sum(1 for m in recent if m["topic"] == "world")
    with client.stream("GET", f"/stream?since=0&limit={n_world}&topics=world") as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        body = "".join(res.iter_text())
    frames = [f for f in body.split("\n\n") if f.startswith("id: ")]
    assert len(frames) == n_world and all("event: world" in f for f in frames)
    first = json.loads(frames[0].split("data: ", 1)[1])
    assert first["data"]["event_type"] and first["seq"] >= 1

    resumed = client.get("/stream/recent", params={"since": recent[-1]["seq"]}).json()
    assert resumed == []
    assert client.get("/stream/status").json()["last_seq"] == recent[-1]["seq"]


# --------------------------------------------------------- proactive assistant
@pytest.fixture
def live(repo):
    return Lab(repo, realtime=True)


def notices(lab):
    return lab.svc.assistant.get(lab.conv.id).notices


def test_outcome_is_verified_the_moment_the_camera_sees_it(live, repo):
    ask = live.say("open the valve", 2)
    live.say("yes", 2.5)
    done = live.say("done", 4)
    assert done.action.status == ActionStatus.OUTCOME_UNVERIFIED and notices(live) == []
    published = []
    live.svc.bus.listen(lambda m: published.append(m) if m["topic"] == "assistant" else None)

    live.see(5, ent("valve", "bench_3", "valve", attributes={"state": "open"}))  # the camera, not the user
    action = repo.get_action(ask.pending.action_id)
    assert action.status == ActionStatus.OUTCOME_VERIFIED
    [notice] = notices(live)
    assert notice.kind == "outcome_verified" and notice.gesture == Gesture.NOD and "open" in notice.reply
    assert live.svc.assistant.get(live.conv.id).awaiting_performance is None
    assert published and published[0]["conversation"] == live.conv.id and published[0]["data"]["id"] == notice.id
    assert "outcome_checked" in [d.kind for d in live.svc.assistant.get(live.conv.id).delegated]


def test_wrong_outcome_is_announced_as_an_alert(live, repo):
    live.say("open the valve", 2)
    live.say("yes", 2.5)
    live.say("done", 4)
    live.see(5, ent("valve", "bench_3", "valve", attributes={"state": "closed"}))
    [notice] = notices(live)
    assert notice.kind == "outcome_failed" and notice.gesture == Gesture.ALERT


def test_changes_to_what_we_just_discussed_are_announced_once(live):
    live.say("where is the microscope?", 2)
    live.see(3, ent("m17", "bench_3", "microscope", name="Microscope M17"))
    [moved] = notices(live)
    assert moved.kind == "focus_changed" and "Microscope M17" in moved.reply and "bench_3" in moved.reply
    live.see(3.1, ent("m17", "bench_4", "microscope", name="Microscope M17"))  # within the cooldown
    assert len(notices(live)) == 1
    live.see(10, ent("n1", "shelf_a", "notebook", name="Notebook N1"))  # not under discussion
    assert len(notices(live)) == 1


def test_the_users_own_changes_are_answered_not_announced(live):
    live.say("where is the notebook?", 2)
    turn = live.say("I moved the notebook to bench 3", 3)
    assert turn.gesture == Gesture.NOD and notices(live) == []


def test_no_bus_no_notices(repo):
    quiet = Lab(repo)  # built without realtime: the benchmark and sandboxes stay silent
    quiet.say("where is the microscope?", 2)
    quiet.see(3, ent("m17", "bench_3", "microscope"))
    assert quiet.svc.assistant.get(quiet.conv.id).notices == []


def test_ui_files_are_revalidated_but_api_is_untouched(client):
    page = client.get("/ui/assistant.html")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/stream/status").headers
