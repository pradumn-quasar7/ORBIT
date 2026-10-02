"""Memory core endpoints (Phase 3): snapshots, timelines, spatial recall, sessions,
tasks (procedural memory) and causal hypotheses."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import (
    AnchorContent,
    CausalHypothesis,
    Event,
    LocationAnswer,
    ResumePlan,
    SessionSummary,
    Task,
    TaskStateView,
    WorldSnapshot,
)
from backend.app.domain.types import SourceType
from backend.app.services.hypotheses import CausalOrderError, HypothesisNotFoundError
from backend.app.services.tasks import StepNotReadyError, StepSpec, TaskError, TaskNotFoundError

router = APIRouter()


def _task_error(exc: TaskError):
    code = 404 if isinstance(exc, TaskNotFoundError) else 409
    raise HTTPException(status_code=code, detail=str(exc))


# --------------------------------------------------------------- temporal/spatial
@router.get("/world/snapshot", response_model=WorldSnapshot)
def world_snapshot(as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    return svc.memory.world_snapshot(as_of or svc.clock.now())


@router.get("/entities/{entity_id}/timeline", response_model=List[Event])
def entity_timeline(
    entity_id: str,
    start: Optional[UTCDateTime] = None,
    end: Optional[UTCDateTime] = None,
    svc: OrbitServices = Depends(get_services),
):
    if svc.repo.get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return svc.memory.timeline(entity_id, start, end)


@router.get("/entities/{entity_id}/location", response_model=LocationAnswer)
def locate(entity_id: str, as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    if svc.repo.get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return svc.memory.locate(entity_id, as_of or svc.clock.now())


@router.get("/anchors/{anchor_id}/contents", response_model=List[AnchorContent])
def anchor_contents(
    anchor_id: str, as_of: Optional[UTCDateTime] = None, nested: bool = True, svc: OrbitServices = Depends(get_services)
):
    return svc.memory.contents(anchor_id, as_of or svc.clock.now(), nested)


@router.get("/sessions/{session_id}/summary", response_model=SessionSummary)
def session_summary(session_id: str, svc: OrbitServices = Depends(get_services)):
    summary = svc.memory.session_summary(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return summary


# -------------------------------------------------------------------------- tasks
class TaskCreate(BaseModel):
    id: Optional[str] = None
    goal: str
    steps: List[StepSpec]
    assigned_to: Optional[str] = None
    procedure_entity_id: Optional[str] = None
    procedure_revision: Optional[str] = None
    created_at: Optional[UTCDateTime] = None


class StepStart(BaseModel):
    actor: Optional[str] = None
    at: Optional[UTCDateTime] = None


class StepComplete(BaseModel):
    source: str = "user"
    source_type: Optional[SourceType] = None
    quality: float = 1.0
    authority: float = 1.0
    actor: Optional[str] = None
    note: Optional[str] = None
    at: Optional[UTCDateTime] = None


class TaskInterrupt(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None
    at: Optional[UTCDateTime] = None


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(body: TaskCreate, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.create_task(
            body.goal,
            body.steps,
            body.created_at or svc.clock.now(),
            task_id=body.id,
            assigned_to=body.assigned_to,
            procedure_entity_id=body.procedure_entity_id,
            procedure_revision=body.procedure_revision,
        )
    except TaskError as exc:
        _task_error(exc)


@router.get("/tasks", response_model=List[Task])
def list_tasks(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_tasks()


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.get(task_id)
    except TaskError as exc:
        _task_error(exc)


@router.get("/tasks/{task_id}/state", response_model=Optional[TaskStateView])
def task_state(task_id: str, as_of: Optional[UTCDateTime] = None, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.state_at(task_id, as_of or svc.clock.now())
    except TaskError as exc:
        _task_error(exc)


@router.get("/tasks/{task_id}/history", response_model=List[Event])
def task_history(task_id: str, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.history(task_id)
    except TaskError as exc:
        _task_error(exc)


@router.post("/tasks/{task_id}/steps/{step_id}/start", response_model=Task)
def start_step(task_id: str, step_id: str, body: StepStart, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.start_step(task_id, step_id, body.at or svc.clock.now(), body.actor)
    except TaskError as exc:
        _task_error(exc)


@router.post("/tasks/{task_id}/steps/{step_id}/complete", response_model=Task)
def complete_step(task_id: str, step_id: str, body: StepComplete, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.complete_step(
            task_id,
            step_id,
            body.at or svc.clock.now(),
            source=body.source,
            source_type=body.source_type,
            quality=body.quality,
            authority=body.authority,
            actor=body.actor,
            note=body.note,
        )
    except TaskError as exc:
        _task_error(exc)


@router.post("/tasks/{task_id}/interrupt", response_model=Task)
def interrupt_task(task_id: str, body: TaskInterrupt, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.interrupt_task(task_id, body.at or svc.clock.now(), body.reason, body.actor)
    except TaskError as exc:
        _task_error(exc)


class TaskResume(BaseModel):
    actor: Optional[str] = None
    at: Optional[UTCDateTime] = None


class RevisionAck(BaseModel):
    revision: str
    actor: Optional[str] = None
    at: Optional[UTCDateTime] = None


@router.post("/tasks/{task_id}/resume", response_model=ResumePlan)
def resume_task(task_id: str, body: TaskResume, svc: OrbitServices = Depends(get_services)):
    """Spec §11 resume protocol: verified state → changes → invalidation → checks →
    observation requests → next supported step (or none)."""
    try:
        return svc.tasks.resume(task_id, body.at or svc.clock.now(), body.actor)
    except TaskError as exc:
        _task_error(exc)


@router.post("/tasks/{task_id}/preview", response_model=ResumePlan)
def preview_task(task_id: str, body: TaskResume, svc: OrbitServices = Depends(get_services)):
    """What "Continue." would decide right now — nothing is changed."""
    try:
        return svc.tasks.preview(task_id, body.at or svc.clock.now())
    except TaskError as exc:
        _task_error(exc)


@router.post("/tasks/{task_id}/procedure/acknowledge", response_model=Task)
def acknowledge_revision(task_id: str, body: RevisionAck, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.tasks.acknowledge_revision(task_id, body.revision, body.at or svc.clock.now(), body.actor)
    except TaskError as exc:
        _task_error(exc)


# --------------------------------------------------------------------- hypotheses
class HypothesisCreate(BaseModel):
    statement: str
    cause_event_id: Optional[str] = None
    effect_event_id: Optional[str] = None
    entity_ids: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None


class HypothesisEvidence(BaseModel):
    source: str
    supports: bool
    description: str
    kind: str = "observation"
    source_type: Optional[SourceType] = None
    quality: float = 1.0
    authority: float = 1.0
    at: Optional[UTCDateTime] = None


class HypothesisEvidenceResult(BaseModel):
    hypothesis: CausalHypothesis
    status_changed: bool
    reason: str


@router.post("/hypotheses", response_model=CausalHypothesis, status_code=201)
def propose_hypothesis(body: HypothesisCreate, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.hypotheses.propose(
            body.statement, svc.clock.now(), body.cause_event_id, body.effect_event_id, body.entity_ids, body.created_by
        )
    except CausalOrderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/hypotheses", response_model=List[CausalHypothesis])
def list_hypotheses(entity_id: Optional[str] = None, svc: OrbitServices = Depends(get_services)):
    return svc.hypotheses.for_entity(entity_id) if entity_id else svc.repo.list_hypotheses()


@router.post("/hypotheses/{hypothesis_id}/evidence", response_model=HypothesisEvidenceResult)
def hypothesis_evidence(hypothesis_id: str, body: HypothesisEvidence, svc: OrbitServices = Depends(get_services)):
    try:
        hyp, changed, reason = svc.hypotheses.add_evidence(
            hypothesis_id,
            body.at or svc.clock.now(),
            body.source,
            body.supports,
            body.description,
            body.kind,
            body.source_type,
            body.quality,
            body.authority,
        )
    except HypothesisNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return HypothesisEvidenceResult(hypothesis=hyp, status_changed=changed, reason=reason)
