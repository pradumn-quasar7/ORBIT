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
    SearchCoverage,
    StateVersion,
    WorldDiff,
)
from backend.app.services.world_state_engine import (
    DuplicateObservationError,
    EntityNotFoundError,
    SimulationEvidenceRejected,
)

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
    baseline_session_id: Optional[str] = None  # "what changed since session X ended?"
    target_timestamp: Optional[UTCDateTime] = None
    mode: str = "snapshot"  # snapshot | event_log (ablation baseline)
    include_unobserved: bool = True


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
    except SimulationEvidenceRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc))
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
    except SimulationEvidenceRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc))


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
    if req.mode == "event_log":
        return svc.diff.event_log_diff(req.baseline_timestamp, target)
    if req.baseline_session_id:
        try:
            return svc.diff.diff_since_session(req.baseline_session_id, target, include_unobserved=req.include_unobserved)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    if req.baseline_timestamp is None:
        raise HTTPException(status_code=422, detail="baseline_timestamp or baseline_session_id is required")
    return svc.diff.diff(req.baseline_timestamp, target, include_unobserved=req.include_unobserved)


class SearchRequest(BaseModel):
    region: str
    searched_for: List[str]  # entity ids or "type:<type>"
    observation: Optional[Observation] = None  # what was actually seen during the search
    coverage_fraction: float = 1.0
    visibility_conditions: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    source: str = "camera"
    session_id: Optional[str] = None
    timestamp: Optional[UTCDateTime] = None


@router.post("/search", response_model=SearchCoverage)
def record_search(req: SearchRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.search.record_search(
            req.region,
            req.timestamp or (req.observation.timestamp if req.observation else svc.clock.now()),
            req.searched_for,
            req.observation,
            req.coverage_fraction,
            req.visibility_conditions,
            req.confidence,
            req.source,
            req.session_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/search-coverage", response_model=List[SearchCoverage])
def list_search_coverage(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_search_coverage()
