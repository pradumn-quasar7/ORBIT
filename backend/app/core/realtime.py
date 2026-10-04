"""Realtime fan-out (Phase 17): committed world changes pushed to every open view.

The repository reports what a transaction *committed* (never what was rolled back);
the bus numbers each message, keeps a short replay buffer (so a reconnecting browser
receives what it missed via ``Last-Event-ID``), and hands messages to subscribers:

- async subscribers (Server-Sent Events streams) get a bounded queue each, filled
  thread-safely from whichever worker thread committed the write; a subscriber that
  falls behind loses its oldest messages and is told so (``overflow``) rather than
  slowing writers down;
- sync listeners (the assistant) are called in the committing thread, after commit.

Messages are plain dicts: ``{"seq", "topic", "type", "at", "data", "conversation"?}``.
Topics: ``world`` (events), ``observation`` (every sensor/person observation, even when
it changes nothing — freshness changed), ``assistant`` (proactive notices).
"""
import asyncio
import itertools
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set

log = logging.getLogger("orbit.realtime")

Message = Dict[str, Any]


class Subscription:
    def __init__(self, bus: "EventBus", loop: asyncio.AbstractEventLoop, topics: Optional[Set[str]],
                 conversation: Optional[str], maxsize: int):
        self.bus = bus
        self.loop = loop
        self.topics = topics
        self.conversation = conversation
        self.queue: "asyncio.Queue[Message]" = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def wants(self, msg: Message) -> bool:
        if self.topics is not None and msg["topic"] not in self.topics:
            return False
        if msg.get("conversation") is not None and msg["conversation"] != self.conversation:
            return False  # a conversation's notices go only to that conversation
        return True

    def _offer(self, msg: Message) -> None:  # runs on the subscriber's loop
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(msg)

    def close(self) -> None:
        self.bus._remove(self)


class EventBus:
    def __init__(self, replay: int = 500, queue_size: int = 1000):
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._buffer: Deque[Message] = deque(maxlen=replay)
        self._subs: List[Subscription] = []
        self._listeners: List[Callable[[Message], None]] = []
        self.queue_size = queue_size

    # ---------------------------------------------------------------- publish
    def publish(self, topic: str, type_: str, data: Any, conversation: Optional[str] = None,
                at: Optional[datetime] = None) -> Message:
        with self._lock:
            msg: Message = {
                "seq": next(self._seq), "topic": topic, "type": type_,
                "at": (at or datetime.now(timezone.utc)).isoformat(), "data": data,
            }
            if conversation is not None:
                msg["conversation"] = conversation
            self._buffer.append(msg)
            subs = list(self._subs)
            listeners = list(self._listeners)
        for sub in subs:
            if sub.wants(msg):
                try:
                    sub.loop.call_soon_threadsafe(sub._offer, msg)
                except RuntimeError:  # the subscriber's loop has closed
                    self._remove(sub)
        for fn in listeners:
            try:
                fn(msg)
            except Exception:  # a listener must never break the writer that triggered it
                log.exception("realtime listener failed")
        return msg

    # -------------------------------------------------------------- subscribe
    def subscribe(self, topics: Optional[Set[str]] = None, conversation: Optional[str] = None,
                  since: Optional[int] = None) -> Subscription:
        """Call from inside the event loop that will consume the queue."""
        sub = Subscription(self, asyncio.get_running_loop(), topics, conversation, self.queue_size)
        with self._lock:
            missed = [m for m in self._buffer if since is not None and m["seq"] > since]
            self._subs.append(sub)
        for msg in missed:
            if sub.wants(msg):
                sub._offer(msg)
        return sub

    def replay(self, since: int = 0, topics: Optional[Set[str]] = None) -> List[Message]:
        with self._lock:
            return [m for m in self._buffer if m["seq"] > since and (topics is None or m["topic"] in topics)]

    def listen(self, fn: Callable[[Message], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._buffer[-1]["seq"] if self._buffer else 0

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


def publish_committed(bus: EventBus) -> Callable[[list], None]:
    """Repository commit listener → bus messages (events and observations)."""
    from backend.app.domain.models import Event, Observation

    def on_commit(items: list) -> None:
        for item in items:
            if isinstance(item, Event):
                bus.publish("world", item.event_type.value, item.model_dump(mode="json"), at=item.timestamp)
            elif isinstance(item, Observation):
                bus.publish("observation", "OBSERVATION", {
                    "id": item.id, "timestamp": item.timestamp.isoformat(), "source": item.source,
                    "source_type": item.source_type.value if item.source_type else None,
                    "session_id": item.session_id,
                    "field_of_view": item.spatial_context.get("field_of_view"),
                    "entities": [r.entity_id for r in item.resolutions],
                    "types": [o.type for o in item.observed_entities],
                }, at=item.timestamp)
    return on_commit
