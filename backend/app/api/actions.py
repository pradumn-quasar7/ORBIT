"""Active perception planning and the action-safety boundary (Phase 7)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import ActionRequest, OutcomeRecord, PerceptionPlan, Principal, StateCondition
from backend.app.domain.types import PrincipalKind, Scope
from backend.app.services.actions import ActionError, InvalidTransition, PermissionDenied
from backend.app.services.active_perception import POLICIES, RandomPolicy

router = APIRouter()


def _action_error(exc: ActionError):
    code = 403 if isinstance(exc, PermissionDenied) else 409 if isinstance(exc, InvalidTransition) else 404
    raise HTTPException(status_code=code, detail=str(exc))


class PlanRequest(BaseModel):
    policy: str = "information_gain"  # information_gain | fixed | random
    seed: int = 0
    k: int = 5
    entity_ids: Optional[List[str]] = None
    at: Optional[UTCDateTime] = None


@router.post("/active-perception/plan", response_model=PerceptionPlan)
def plan(body: PlanRequest, svc: OrbitServices = Depends(get_services)):
    if body.policy not in POLICIES:
        raise HTTPException(status_code=422, detail=f"unknown policy {body.policy}")
    policy = RandomPolicy(body.seed) if body.policy == "random" else POLICIES[body.policy]()
    return svc.perception.plan(body.at or svc.clock.now(), policy, body.entity_ids, body.k)


class PrincipalCreate(BaseModel):
    id: str
    kind: PrincipalKind
    scopes: List[Scope]
    entity_scope: Optional[List[str]] = None


@router.post("/principals", response_model=Principal, status_code=201)
def register_principal(body: PrincipalCreate, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.register_principal(body.id, body.kind, body.scopes, svc.clock.now(), body.entity_scope)
    except ActionError as exc:
        _action_error(exc)


@router.get("/principals", response_model=List[Principal])
def list_principals(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_principals()


class ActionPropose(BaseModel):
    action: str
    requested_by: str = "orbit-agent"
    target_entity_ids: List[str] = Field(default_factory=list)
    prerequisites: List[StateCondition] = Field(default_factory=list)
    expected_outcome: List[StateCondition] = Field(default_factory=list)
    consequential: bool = True
    task_id: Optional[str] = None
    step_id: Optional[str] = None
    at: Optional[UTCDateTime] = None


class ActionDecision(BaseModel):
    principal_id: str
    approve: bool
    reason: Optional[str] = None
    at: Optional[UTCDateTime] = None


class ActionPerformed(BaseModel):
    principal_id: str
    notes: Optional[str] = None
    at: Optional[UTCDateTime] = None


class AtOnly(BaseModel):
    at: Optional[UTCDateTime] = None


@router.post("/actions", response_model=ActionRequest, status_code=201)
def propose_action(body: ActionPropose, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.propose(
            body.action, body.requested_by, body.at or svc.clock.now(), body.target_entity_ids,
            body.prerequisites, body.expected_outcome, body.consequential, body.task_id, body.step_id,
        )
    except ActionError as exc:
        _action_error(exc)


@router.get("/actions", response_model=List[ActionRequest])
def list_actions(svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_actions()


@router.get("/actions/{action_id}", response_model=ActionRequest)
def get_action(action_id: str, svc: OrbitServices = Depends(get_services)):
    action = svc.repo.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.post("/actions/{action_id}/recheck", response_model=ActionRequest)
def recheck(action_id: str, body: AtOnly, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.recheck(action_id, body.at or svc.clock.now())
    except ActionError as exc:
        _action_error(exc)


@router.post("/actions/{action_id}/authorize", response_model=ActionRequest)
def authorize(action_id: str, body: ActionDecision, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.authorize(action_id, body.principal_id, body.approve, body.at or svc.clock.now(), body.reason)
    except ActionError as exc:
        _action_error(exc)


@router.post("/actions/{action_id}/performed", response_model=ActionRequest)
def performed(action_id: str, body: ActionPerformed, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.report_performed(action_id, body.principal_id, body.at or svc.clock.now(), body.notes)
    except ActionError as exc:
        _action_error(exc)


@router.post("/actions/{action_id}/verify-outcome", response_model=ActionRequest)
def verify_outcome(action_id: str, body: AtOnly, svc: OrbitServices = Depends(get_services)):
    try:
        return svc.actions.verify_outcome(action_id, body.at or svc.clock.now())
    except ActionError as exc:
        _action_error(exc)


@router.post("/actions/{action_id}/execute")
def execute(action_id: str, svc: OrbitServices = Depends(get_services)):
    """Always refused: ORBIT never actuates physical systems (spec §16, §36)."""
    try:
        svc.actions.execute_autonomously(action_id)
    except ActionError as exc:
        _action_error(exc)


@router.get("/outcomes", response_model=List[OutcomeRecord])
def list_outcomes(action_id: Optional[str] = None, svc: OrbitServices = Depends(get_services)):
    return svc.repo.list_outcomes(action_id)
