"""Live camera endpoints (Phase 15). The browser runs detection; only detections arrive."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import SearchCoverage
from backend.app.providers.perception import Detection
from backend.app.services.camera import CameraConfig, CameraNotCalibrated, RegionSpec, SnapshotResult

router = APIRouter()


class ConfigRequest(BaseModel):
    view: str  # anchor the camera looks at, e.g. "desk"
    view_type: str = "surface"
    room: Optional[str] = None
    regions: List[RegionSpec] = Field(default_factory=list)


class SnapshotRequest(BaseModel):
    detections: List[Detection]
    session_id: Optional[str] = None
    lighting: Optional[str] = None
    timestamp: Optional[UTCDateTime] = None


class ScanRequest(SnapshotRequest):
    region: str
    coverage: float = 0.95


@router.get("/cameras/{device_id}/config", response_model=CameraConfig)
def get_config(device_id: str, svc: OrbitServices = Depends(get_services)):
    return svc.camera.config(device_id)


@router.put("/cameras/{device_id}/config", response_model=CameraConfig)
def put_config(device_id: str, body: ConfigRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.camera.configure(device_id, body.view, body.regions, svc.clock.now(), body.view_type, body.room)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/cameras/{device_id}/snapshot", response_model=SnapshotResult)
def snapshot(device_id: str, body: SnapshotRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.camera.snapshot(device_id, body.detections, body.timestamp or svc.clock.now(), body.session_id, body.lighting)
    except CameraNotCalibrated as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/cameras/{device_id}/scan", response_model=SearchCoverage)
def scan(device_id: str, body: ScanRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.camera.scan(device_id, body.region, body.detections, body.timestamp or svc.clock.now(),
                               body.session_id, body.coverage, body.lighting)
    except CameraNotCalibrated as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
