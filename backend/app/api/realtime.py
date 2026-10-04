"""Realtime stream (Phase 17): Server-Sent Events of committed world changes."""
import asyncio
import json
from typing import List, Optional, Set

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices

router = APIRouter()
TOPICS = {"world", "observation", "assistant"}
HEARTBEAT_SECONDS = 15.0


def _topics(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip() in TOPICS} or None


def _frame(msg: dict) -> str:
    return f"id: {msg['seq']}\nevent: {msg['topic']}\ndata: {json.dumps(msg, separators=(',', ':'))}\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    topics: Optional[str] = None,
    conversation: Optional[str] = None,
    since: Optional[int] = None,
    limit: Optional[int] = None,
    svc: OrbitServices = Depends(get_services),
):
    """``EventSource('/stream?topics=world,observation')``. Reconnecting browsers send
    ``Last-Event-ID`` and receive what they missed (from a short replay buffer).
    ``limit`` closes the stream after that many messages (tests, scripts)."""
    last = request.headers.get("last-event-id")
    if last and last.isdigit():
        since = int(last)
    sub = svc.bus.subscribe(_topics(topics), conversation, since)

    async def events():
        sent = 0
        try:
            yield f"retry: 2000\n: connected; last seq {svc.bus.last_seq}\n\n"
            while limit is None or sent < limit:
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                if sub.dropped:
                    yield f"event: overflow\ndata: {json.dumps({'dropped': sub.dropped})}\n\n"
                    sub.dropped = 0
                yield _frame(msg)
                sent += 1
        finally:
            sub.close()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/stream/recent")
def recent(since: int = 0, topics: Optional[str] = None, svc: OrbitServices = Depends(get_services)) -> List[dict]:
    """The replay buffer as JSON (what a reconnecting client would receive)."""
    return [m for m in svc.bus.replay(since, _topics(topics)) if m.get("conversation") is None]


@router.get("/stream/status")
def status(svc: OrbitServices = Depends(get_services)) -> dict:
    return {"last_seq": svc.bus.last_seq, "subscribers": svc.bus.subscriber_count}
