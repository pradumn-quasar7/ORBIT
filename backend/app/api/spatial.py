"""Anchors, sessions and relations (Phase 1 — spatial persistence)."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import Anchor, Observation, Relation, Session
from backend.app.services.spatial import AnchorCycleError

router = APIRouter()


class AnchorRegistration(BaseModel):
    id: str
    name: Optional[str] = None
    anchor_type: str = "region"
    parent_id: Optional[str] = None
    frame: Dict[str, Any] = Field(default_factory=dict)


class SessionStart(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    actor: Optional[str] = None
    started_at: Optional[UTCDateTime] = None


class SessionEnd(BaseModel):
    ended_at: Optional[UTCDateTime] = None


@router.post("/anchors", response_model=Anchor, status_code=201)
def register_anchor(body: AnchorRegistration, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.anchors.register(
            body.id, svc.clock.now(), body.name, body.anchor_type, body.parent_id, body.frame
        )
    except AnchorCycleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/anchors", response_model=List[Anchor])
def list_anchors(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_anchors()


@router.get("/anchors/{anchor_id}")
def get_anchor(anchor_id: str, svc: OrbitServices = Depends(get_services)):
    anchor = svc.repo.get_anchor(anchor_id)
    if anchor is None:
        raise HTTPException(status_code=404, detail="Anchor not found")
    return {"anchor": anchor, "lineage": svc.anchors.lineage(anchor_id)}


@router.post("/sessions", response_model=Session, status_code=201)
def start_session(body: SessionStart, svc: OrbitServices = Depends(get_services)):
    if body.id and svc.repo.get_session(body.id):
        raise HTTPException(status_code=409, detail="Session already exists")
    session = Session(label=body.label, actor=body.actor, started_at=body.started_at or svc.clock.now())
    if body.id:
        session.id = body.id
    return svc.repo.save_session(session)


@router.post("/sessions/{session_id}/end", response_model=Session)
def end_session(session_id: str, body: SessionEnd, svc: OrbitServices = Depends(get_services)):
    session = svc.repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.ended_at = body.ended_at or svc.clock.now()
    return svc.repo.save_session(session)


@router.get("/sessions", response_model=List[Session])
def list_sessions(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_sessions()


@router.get("/sessions/{session_id}/observations", response_model=List[Observation])
def session_observations(session_id: str, svc: OrbitServices = Depends(get_services)):
    if svc.repo.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return svc.repo.list_observations_for_session(session_id)


@router.get("/entities/{entity_id}/relations", response_model=List[Relation])
def entity_relations(entity_id: str, include_history: bool = False, svc: OrbitServices = Depends(get_services)):
    if svc.repo.get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    if include_history:
        return svc.repo.get_relations_for_entity(entity_id)
    return svc.relations.active_relations(entity_id)
