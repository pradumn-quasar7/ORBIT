"""Perception ingestion, redaction and the inspection dashboard (Phase 8)."""
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import Event, Observation
from backend.app.providers.perception import Detection, DetectionPerceptionProvider, RawFrame
from backend.app.services.dashboard import DashboardSummary, build_summary
from backend.app.services.perception_gateway import PerceptionGateway

router = APIRouter()


class FrameIngest(BaseModel):
    """Detections from any external detector for one frame (vendor-neutral, spec §6.1)."""

    frame: RawFrame
    detections: List[Detection]
    region_map: Dict[str, Tuple[float, float, float, float]] = Field(default_factory=dict)
    min_confidence: float = 0.3
    model_id: str = "external-detector"


class FrameResult(BaseModel):
    observation: Observation
    events: List[Event]


class RedactRequest(BaseModel):
    reason: str
    actor: Optional[str] = None
    at: Optional[UTCDateTime] = None


@router.post("/perception/frames", response_model=FrameResult)
def ingest_frame(body: FrameIngest, svc: OrbitServices = Depends(get_services)):
    provider = DetectionPerceptionProvider(lambda _f: body.detections, body.region_map, body.min_confidence, body.model_id)
    observation, events = PerceptionGateway(svc.repo, svc.engine).ingest(body.frame, provider)
    return FrameResult(observation=observation, events=events)


@router.post("/observations/{observation_id}/redact", response_model=Observation)
def redact(observation_id: str, body: RedactRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return PerceptionGateway(svc.repo, svc.engine).redact(observation_id, body.at or svc.clock.now(), body.reason, body.actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/inspect/summary", response_model=DashboardSummary)
def dashboard_summary(as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    return build_summary(svc, as_of or svc.clock.now())
