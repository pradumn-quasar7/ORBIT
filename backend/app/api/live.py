"""Realtime voice (Phase 19): Gemini Live sessions and the tools Orbi may use."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.services.live import LiveNotConfigured

router = APIRouter()


class SessionRequest(BaseModel):
    user_id: str = Field(default="operator", min_length=1, max_length=64)
    device: str = Field(default="mac", pattern="^(mac|quest)$")
    conversation_id: Optional[str] = None


class ToolRequest(BaseModel):
    conversation_id: str
    device: str = Field(default="mac", pattern="^(mac|quest)$")
    name: str = Field(max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)
    heard: str = Field(default="", max_length=2000)  # the user's own transcribed words this turn


@router.get("/live/status")
def status(svc: OrbitServices = Depends(get_services)):
    return svc.live.status()


@router.post("/live/session")
def session(body: SessionRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.live.start(body.user_id, body.device, svc.clock.now(), body.conversation_id)
    except LiveNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/live/tool")
def tool(body: ToolRequest, svc: OrbitServices = Depends(get_services)):
    if body.conversation_id not in svc.assistant.conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return svc.live.call(body.name, body.args, body.conversation_id, body.device, body.heard, svc.clock.now())
