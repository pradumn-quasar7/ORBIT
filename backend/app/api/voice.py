"""Hosted speech-to-speech turns on Groq (Phase 19.1)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.api.deps import get_services
from backend.app.api.speech import voice_status
from backend.app.core.container import OrbitServices
from backend.app.services.voice_agent import VoiceError

router = APIRouter()
MAX_AUDIO = 8 * 1024 * 1024  # ~ several minutes of compressed speech


@router.get("/voice/status")
def status(svc: OrbitServices = Depends(get_services)):
    return {"engines": {"groq": svc.voice.configured, "gemini": svc.live.settings.configured},
            "voice": voice_status(), "devices": svc.live.devices.available()}


@router.post("/voice/turn")
async def turn(request: Request, conversation_id: Optional[str] = Query(None), user_id: str = Query("operator", max_length=64),
               device: str = Query("mac", pattern="^(mac|quest)$"), svc: OrbitServices = Depends(get_services)):
    """Body: the recorded utterance (audio/webm, audio/ogg, audio/mp4 or audio/wav)."""
    if not svc.voice.configured:
        raise HTTPException(status_code=503, detail="No Groq key: put GROQ_API_KEY in the .env file and restart ORBIT")
    audio = await request.body()
    if not audio or len(audio) > MAX_AUDIO:
        raise HTTPException(status_code=422, detail="send one recorded utterance (up to 8 MB)")
    at = svc.clock.now()
    conv = svc.assistant.conversations.get(conversation_id or "")
    if conv is None:
        conv = svc.assistant.start(user_id, at)
    try:
        # Groq calls block on the network: run them off the event loop.
        from starlette.concurrency import run_in_threadpool
        out = await run_in_threadpool(svc.voice.turn, conv.id, conv.user_id, device, audio,
                                      request.headers.get("content-type", "audio/webm"), at)
    except VoiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"conversation_id": conv.id, "heard": out.heard, "reply": out.reply, "tools": out.tools,
            "language": out.language, "timings": out.timings, "model": out.model,
            "pending": conv.pending.summary if conv.pending else None}
