"""Replay, counterfactual sandboxes and decision sensitivity (Phase 10)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import (
    CounterfactualReport,
    Event,
    GroundedResponse,
    ReplayFrame,
    ResumePlan,
    SandboxInfo,
    SensitivityReport,
    Variation,
    WorldSnapshot,
)
from backend.app.services.counterfactual import SandboxNotFound, apply_variation, compare, info, sensitivity
from backend.app.services.tasks import TaskError
from backend.app.services.world_state_engine import EntityNotFoundError

router = APIRouter()


def _sandbox(svc: OrbitServices, sandbox_id: str):
    try:
        return svc.sandboxes.get(sandbox_id)
    except SandboxNotFound:
        raise HTTPException(status_code=404, detail="Sandbox not found")


@router.get("/replay", response_model=List[ReplayFrame])
def replay(start: UTCDateTime, end: Optional[UTCDateTime] = None, entity_id: Optional[str] = None,
           svc: OrbitServices = Depends(get_services)):
    return svc.replay.frames(start, end or svc.clock.now(), entity_id)


class SandboxCreate(BaseModel):
    as_of: Optional[UTCDateTime] = None
    label: Optional[str] = None


class VariationsBody(BaseModel):
    variations: List[Variation]


class SandboxQuery(BaseModel):
    query: str


class CompareRequest(BaseModel):
    as_of: Optional[UTCDateTime] = None
    variations: List[Variation]
    task_ids: List[str] = Field(default_factory=list)
    queries: List[str] = Field(default_factory=list)


class SensitivityRequest(BaseModel):
    at: Optional[UTCDateTime] = None


@router.post("/sandboxes", response_model=SandboxInfo, status_code=201)
def create_sandbox(body: SandboxCreate, svc: OrbitServices = Depends(get_services)):
    return info(svc.sandboxes.create(svc, body.as_of or svc.clock.now(), body.label))


@router.get("/sandboxes", response_model=List[SandboxInfo])
def list_sandboxes(svc: OrbitServices = Depends(get_services)):
    return [info(s) for s in svc.sandboxes.list()]


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxInfo)
def get_sandbox(sandbox_id: str, svc: OrbitServices = Depends(get_services)):
    return info(_sandbox(svc, sandbox_id))


@router.delete("/sandboxes/{sandbox_id}", status_code=204)
def delete_sandbox(sandbox_id: str, svc: OrbitServices = Depends(get_services)):
    _sandbox(svc, sandbox_id)
    svc.sandboxes.delete(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/variations", response_model=List[Event])
def vary(sandbox_id: str, body: VariationsBody, svc: OrbitServices = Depends(get_services)):
    sandbox = _sandbox(svc, sandbox_id)
    events: List[Event] = []
    try:
        for v in body.variations:
            events += apply_variation(sandbox, v)
    except (EntityNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return events


@router.get("/sandboxes/{sandbox_id}/snapshot", response_model=WorldSnapshot)
def sandbox_snapshot(sandbox_id: str, svc: OrbitServices = Depends(get_services)):
    sandbox = _sandbox(svc, sandbox_id)
    return sandbox.services.memory.world_snapshot(sandbox.clock.now())


@router.post("/sandboxes/{sandbox_id}/queries", response_model=GroundedResponse)
def sandbox_query(sandbox_id: str, body: SandboxQuery, svc: OrbitServices = Depends(get_services)):
    sandbox = _sandbox(svc, sandbox_id)
    return sandbox.services.agent.answer(body.query, sandbox.clock.now())


@router.post("/sandboxes/{sandbox_id}/tasks/{task_id}/resume", response_model=ResumePlan)
def sandbox_resume(sandbox_id: str, task_id: str, svc: OrbitServices = Depends(get_services)):
    sandbox = _sandbox(svc, sandbox_id)
    try:
        return sandbox.services.tasks.resume(task_id, sandbox.clock.now())
    except TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/counterfactuals/compare", response_model=CounterfactualReport)
def counterfactual_compare(body: CompareRequest, svc: OrbitServices = Depends(get_services)):
    try:
        return compare(svc, body.as_of or svc.clock.now(), body.variations, body.task_ids, body.queries)
    except (EntityNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/tasks/{task_id}/sensitivity", response_model=SensitivityReport)
def task_sensitivity(task_id: str, body: SensitivityRequest, svc: OrbitServices = Depends(get_services)):
    if svc.repo.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return sensitivity(svc, task_id, body.at or svc.clock.now())
