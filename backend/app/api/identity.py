"""Identity curation endpoints (Phase 13): suggestions, merge, undo, distinct."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import Entity, IdentityMerge, MergeSuggestion
from backend.app.services.identity import CurationDenied, IdentityError
from backend.app.services.world_state_engine import EntityNotFoundError

router = APIRouter()


class MergeRequest(BaseModel):
    source_id: str
    target_id: str
    principal_id: str
    reason: str
    at: Optional[UTCDateTime] = None


class UndoRequest(BaseModel):
    principal_id: str
    reason: str
    at: Optional[UTCDateTime] = None


class DistinctRequest(BaseModel):
    entity_a: str
    entity_b: str
    principal_id: str
    reason: str
    at: Optional[UTCDateTime] = None


def _fail(exc: Exception):
    if isinstance(exc, CurationDenied):
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=409, detail=str(exc))


@router.get("/identity/suggestions", response_model=List[MergeSuggestion])
def suggestions(as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    """Possible duplicates for a person to review; ORBIT never merges on its own."""
    return svc.identity.suggestions(as_of or svc.clock.now())


@router.post("/identity/merge", response_model=IdentityMerge, status_code=201)
def merge(body: MergeRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.identity.merge(body.source_id, body.target_id, body.principal_id, body.at or svc.clock.now(), body.reason)
    except (IdentityError, EntityNotFoundError) as exc:
        _fail(exc)


@router.get("/identity/merges", response_model=List[IdentityMerge])
def list_merges(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_merges()


@router.post("/identity/merges/{merge_id}/undo", response_model=IdentityMerge)
def undo(merge_id: str, body: UndoRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.identity.undo(merge_id, body.principal_id, body.at or svc.clock.now(), body.reason)
    except (IdentityError, EntityNotFoundError) as exc:
        _fail(exc)


@router.post("/identity/distinct", response_model=List[Entity])
def distinct(body: DistinctRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return list(svc.identity.confirm_distinct(body.entity_a, body.entity_b, body.principal_id,
                                                  body.at or svc.clock.now(), body.reason))
    except (IdentityError, EntityNotFoundError) as exc:
        _fail(exc)
