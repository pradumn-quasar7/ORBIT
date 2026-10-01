"""Evidence engine endpoints (Phase 2): assessed state, claims, conflicts, interventions."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import (
    ClaimAssessment,
    ClaimDependency,
    Conflict,
    EntityAssessment,
    Event,
    Evidence,
)
from backend.app.domain.types import ClaimDecision, SourceType
from backend.app.services.world_state_engine import EntityNotFoundError, SimulationEvidenceRejected

router = APIRouter()


class ClaimRequest(BaseModel):
    entity_id: str
    attribute: str
    value: Any
    source: str
    source_type: Optional[SourceType] = None
    quality: float = 1.0
    authority: float = 1.0
    timestamp: Optional[UTCDateTime] = None
    source_reference: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


class ClaimResponse(BaseModel):
    decision: ClaimDecision
    reason: str
    events: List[Event]
    assessment: ClaimAssessment


class InterventionRequest(BaseModel):
    description: str
    attributes: Optional[List[str]] = None  # None = every attribute of the entity
    source: str = "user"
    timestamp: Optional[UTCDateTime] = None


class DependencyRequest(BaseModel):
    dependent_entity_id: str
    dependent_attribute: str
    depends_on_entity_id: str
    depends_on_attribute: str
    reason: Optional[str] = None


class EvidenceView(BaseModel):
    evidence: Evidence
    integrity_verified: bool


def _not_found(exc: Exception):
    raise HTTPException(status_code=404, detail=str(exc))


@router.get("/entities/{entity_id}/state", response_model=EntityAssessment)
def entity_state(entity_id: str, as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    """Current (or as-of) state, gated by evidence and freshness."""
    assessment = svc.engine.claims.assess_entity(entity_id, as_of or svc.clock.now())
    if assessment is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return assessment


@router.get("/entities/{entity_id}/claims/{attribute}", response_model=ClaimAssessment)
def assess_claim(
    entity_id: str, attribute: str, as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)
):
    if svc.repo.get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return svc.engine.claims.assess_attribute(entity_id, attribute, as_of or svc.clock.now())


@router.post("/claims", response_model=ClaimResponse)
def assert_claim(body: ClaimRequest, svc: OrbitServices = Depends(get_services)):
    at = body.timestamp or svc.clock.now()
    try:
        result = svc.engine.assert_claim(
            body.entity_id,
            body.attribute,
            body.value,
            source=body.source,
            timestamp=at,
            source_type=body.source_type,
            quality=body.quality,
            authority=body.authority,
            source_reference=body.source_reference,
            provenance=body.provenance,
        )
    except EntityNotFoundError as exc:
        _not_found(exc)
    except SimulationEvidenceRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ClaimResponse(
        decision=result.decision,
        reason=result.reason,
        events=result.events,
        assessment=svc.engine.claims.assess_attribute(body.entity_id, body.attribute, at),
    )


@router.post("/entities/{entity_id}/interventions", response_model=List[Event])
def record_intervention(entity_id: str, body: InterventionRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.engine.record_intervention(
            entity_id, body.timestamp or svc.clock.now(), body.description, body.attributes, source=body.source
        )
    except EntityNotFoundError as exc:
        _not_found(exc)


@router.post("/dependencies", response_model=ClaimDependency, status_code=201)
def add_dependency(body: DependencyRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.engine.add_dependency(
            body.dependent_entity_id,
            body.dependent_attribute,
            body.depends_on_entity_id,
            body.depends_on_attribute,
            svc.clock.now(),
            body.reason,
        )
    except EntityNotFoundError as exc:
        _not_found(exc)


@router.get("/conflicts", response_model=List[Conflict])
def list_conflicts(entity_id: Optional[str] = None, open_only: bool = True, svc: OrbitServices = Depends(get_services)):
    conflicts = svc.repo.list_conflicts(entity_id=entity_id)
    return [c for c in conflicts if c.resolved_at is None] if open_only else conflicts


@router.get("/evidence/{evidence_id}", response_model=EvidenceView)
def get_evidence(evidence_id: str, svc: OrbitServices = Depends(get_services)):
    evidence = svc.repo.get_evidence(evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return EvidenceView(evidence=evidence, integrity_verified=svc.engine.verify_evidence(evidence_id))
