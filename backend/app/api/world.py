"""Entity, observation, history and world-diff endpoints (spec §23)."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import (
    Entity,
    Event,
    Geometry,
    Observation,
    ObservedEntity,
    StateVersion,
    WorldDiff,
)
from backend.app.services.world_state_engine import DuplicateObservationError, EntityNotFoundError

router = APIRouter()


class EntityRegistration(BaseModel):
    """Registering an entity is a claim, so it must declare its evidence source."""

    id: Optional[str] = None
    type: str
    name: Optional[str] = None
    location: Optional[str] = None
    anchor: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    geometry: Optional[Geometry] = None
    source: str = "manual_registration"
    source_reference: Optional[str] = None
    quality: float = 1.0
    authority: float = 1.0
    timestamp: Optional[UTCDateTime] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


class DiffRequest(BaseModel):
    baseline_timestamp: Optional[UTCDateTime] = None
    target_timestamp: Optional[UTCDateTime] = None


class FreshnessCheckRequest(BaseModel):
    as_of: Optional[UTCDateTime] = None


@router.post("/entities", response_model=Entity, status_code=201)
def create_entity(body: EntityRegistration, svc: OrbitServices = Depends(get_services)):
    observed = ObservedEntity(
        candidate_entity_id=body.id,
        type=body.type,
        name=body.name,
        location=body.location,
        anchor=body.anchor,
        attributes=body.attributes,
        geometry=body.geometry,
    )
    observation = Observation(
        timestamp=body.timestamp or svc.clock.now(),
        source=body.source,
        raw_reference=body.source_reference,
        quality=body.quality,
        authority=body.authority,
        provenance={"via": "POST /entities", **body.provenance},
    )
    try:
        return svc.engine.register_entity(observed, observation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/entities", response_model=List[Entity])
def list_entities(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_entities()


@router.get("/entities/{entity_id}", response_model=Entity)
def get_entity(entity_id: str, svc: OrbitServices = Depends(get_services)):
    entity = svc.repo.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.get("/entities/{entity_id}/history", response_model=List[StateVersion])
def get_entity_history(
    entity_id: str, attribute: Optional[str] = None, svc: OrbitServices = Depends(get_services)
):
    if svc.repo.get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return svc.repo.get_state_versions_for_entity(entity_id, attribute=attribute)


@router.post("/entities/{entity_id}/freshness")
def check_freshness(
    entity_id: str, req: FreshnessCheckRequest, svc: OrbitServices = Depends(get_services)
):
    as_of = req.as_of or svc.clock.now()
    try:
        statuses = svc.engine.evaluate_freshness(entity_id, as_of=as_of)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"entity_id": entity_id, "as_of": as_of, "attribute_statuses": statuses}


@router.post("/observations", response_model=List[Event])
def record_observation(obs: Observation, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.engine.record_observation(obs)
    except DuplicateObservationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/observations/{observation_id}", response_model=Observation)
def get_observation(observation_id: str, svc: OrbitServices = Depends(get_services)):
    obs = svc.repo.get_observation(observation_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    return obs


@router.get("/events", response_model=List[Event])
def list_events(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_events()


@router.post("/world/diff", response_model=WorldDiff)
def compute_world_diff(req: DiffRequest, svc: OrbitServices = Depends(get_services)):
    target: datetime = req.target_timestamp or svc.clock.now()
    return svc.engine.compute_world_diff(baseline_timestamp=req.baseline_timestamp, target_timestamp=target)
