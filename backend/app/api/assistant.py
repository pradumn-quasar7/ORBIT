"""Conversational assistant endpoints (Phase 16)."""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.services.assistant import AssistantTurn, Conversation, ConversationNotFound

router = APIRouter()


class AssistantInfo(BaseModel):
    parser: str
    llm: bool 
    confirm_window_seconds: int
    can: List[str]
    cannot: List[str]


class ConversationStart(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    id: Optional[str] = None


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    timestamp: Optional[UTCDateTime] = None


@router.get("/assistant", response_model=AssistantInfo)
def info(svc: OrbitServices = Depends(get_services)):
    parser = svc.assistant.parser
    return AssistantInfo(
        parser=parser.name,
        llm=parser.name.startswith("anthropic:"),
        confirm_window_seconds=int(svc.assistant.confirm_window.total_seconds()),
        can=["answer questions from evidence", "record what you tell me", "mark task steps started or done",
             "pause a task", "suggest what to check", "prepare actions and record your approval"],
        cannot=["perform physical actions", "approve anything without your explicit yes", "overwrite stronger evidence"],
    )


@router.post("/assistant/conversations", response_model=Conversation, status_code=201)
def start(body: ConversationStart, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.assistant.start(body.user_id, svc.clock.now(), body.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/assistant/conversations/{conversation_id}", response_model=Conversation)
def get(conversation_id: str, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.assistant.get(conversation_id)
    except ConversationNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/assistant/conversations/{conversation_id}/messages", response_model=AssistantTurn)
def say(conversation_id: str, body: Message, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.assistant.say(conversation_id, body.text.strip(), body.timestamp or svc.clock.now())
    except ConversationNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")
