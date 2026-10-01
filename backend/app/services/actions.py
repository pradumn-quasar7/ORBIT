"""Action safety (spec §2.10, §16): observe → verify prerequisites → authorize → act →
verify outcome → record outcome.

ORBIT v0.1 has no actuator. "Act" is always a person reporting that they performed
the action; ``execute_autonomously`` exists only to refuse, making the boundary
explicit and testable. Reasoning permissions (recommend) are scoped separately from
action permissions (authorize, actuate), and only humans hold the latter.
"""
from datetime import datetime
from typing import List, Optional

from backend.app.domain.models import (
    ActionRequest,
    Authorization,
    Event,
    OutcomeRecord,
    Principal,
    StateCondition,
)
from backend.app.domain.types import ActionStatus, ConditionState, EventType, OutcomeResult, PrincipalKind, Scope
from backend.app.repositories.base import Repository
from backend.app.services.conditions import ConditionEvaluator

ORBIT_AGENT = "orbit-agent"
TERMINAL = (ActionStatus.DENIED, ActionStatus.OUTCOME_VERIFIED, ActionStatus.OUTCOME_FAILED, ActionStatus.CANCELLED)


class ActionError(ValueError):
    pass


class PermissionDenied(ActionError):
    pass


class AutonomousActuationProhibited(PermissionDenied):
    pass


class InvalidTransition(ActionError):
    pass


class ActionSafetyService:
    def __init__(self, repository: Repository, conditions: ConditionEvaluator):
        self.repo = repository
        self.conditions = conditions

    # ------------------------------------------------------------ principals
    def ensure_agent_principal(self, at: datetime) -> Principal:
        """ORBIT itself may observe, reason and recommend — never authorize or actuate."""
        existing = self.repo.get_principal(ORBIT_AGENT)
        if existing is not None:
            return existing
        return self.repo.save_principal(
            Principal(id=ORBIT_AGENT, kind=PrincipalKind.AGENT, scopes=[Scope.OBSERVE, Scope.REASON, Scope.RECOMMEND], created_at=at)
        )

    def register_principal(
        self, principal_id: str, kind: PrincipalKind, scopes: List[Scope], at: datetime, entity_scope: Optional[List[str]] = None
    ) -> Principal:
        if kind == PrincipalKind.AGENT and {Scope.AUTHORIZE, Scope.ACTUATE} & set(scopes):
            raise PermissionDenied("agents cannot hold authorize or actuate scopes in v0.1")
        return self.repo.save_principal(
            Principal(id=principal_id, kind=kind, scopes=list(scopes), entity_scope=entity_scope, created_at=at)
        )

    def _require(self, principal_id: str, scope: Scope, targets: List[str]) -> Principal:
        principal = self.repo.get_principal(principal_id)
        if principal is None:
            raise PermissionDenied(f"unknown principal {principal_id}")
        if scope not in principal.scopes:
            raise PermissionDenied(f"{principal_id} lacks the '{scope.value}' scope")
        if principal.entity_scope is not None and not set(targets) <= set(principal.entity_scope):
            raise PermissionDenied(f"{principal_id} is not permitted for {sorted(set(targets) - set(principal.entity_scope))}")
        if scope in (Scope.AUTHORIZE, Scope.ACTUATE) and principal.kind != PrincipalKind.HUMAN:
            raise PermissionDenied(f"only humans may {scope.value}")
        return principal

    # ----------------------------------------------------------------- flow
    def propose(
        self,
        action: str,
        requested_by: str,
        at: datetime,
        target_entity_ids: Optional[List[str]] = None,
        prerequisites: Optional[List[StateCondition]] = None,
        expected_outcome: Optional[List[StateCondition]] = None,
        consequential: bool = True,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> ActionRequest:
        targets = list(target_entity_ids or [])
        self._require(requested_by, Scope.RECOMMEND, targets)
        request = ActionRequest(
            action=action,
            target_entity_ids=targets,
            consequential=consequential,
            prerequisites=list(prerequisites or []),
            expected_outcome=list(expected_outcome or []),
            requested_by=requested_by,
            status=ActionStatus.AWAITING_AUTHORIZATION,
            task_id=task_id,
            step_id=step_id,
            created_at=at,
            updated_at=at,
        )
        with self.repo.transaction():
            self._verify_prerequisites(request, at)
            self.repo.save_action(request)
            self._audit(request, None, at, f"proposed by {requested_by}")
        return request

    def recheck(self, action_id: str, at: datetime) -> ActionRequest:
        request = self._get(action_id)
        if request.status not in (ActionStatus.PREREQUISITES_FAILED, ActionStatus.AWAITING_AUTHORIZATION):
            raise InvalidTransition(f"cannot re-check prerequisites in status {request.status.value}")
        with self.repo.transaction():
            before = request.status
            self._verify_prerequisites(request, at)
            self.repo.save_action(request)
            self._audit(request, before, at, "prerequisites re-checked")
        return request

    def authorize(self, action_id: str, principal_id: str, approve: bool, at: datetime, reason: Optional[str] = None) -> ActionRequest:
        request = self._get(action_id)
        self._require(principal_id, Scope.AUTHORIZE, request.target_entity_ids)
        if request.status != ActionStatus.AWAITING_AUTHORIZATION:
            raise InvalidTransition(f"cannot authorize in status {request.status.value}")
        with self.repo.transaction():
            before = request.status
            request.authorization = Authorization(principal_id=principal_id, approved=approve, at=at, reason=reason)
            if not approve:
                request.status = ActionStatus.DENIED
            else:
                # Evidence may have gone stale while the request waited: verify again.
                self._verify_prerequisites(request, at)
                if request.status == ActionStatus.AWAITING_AUTHORIZATION:
                    request.status = ActionStatus.AUTHORIZED
            request.updated_at = at
            self.repo.save_action(request)
            self._audit(request, before, at, f"{'approved' if approve else 'denied'} by {principal_id}")
        return request

    def report_performed(self, action_id: str, principal_id: str, at: datetime, notes: Optional[str] = None) -> ActionRequest:
        request = self._get(action_id)
        self._require(principal_id, Scope.ACTUATE, request.target_entity_ids)
        if request.status != ActionStatus.AUTHORIZED:
            raise InvalidTransition(f"cannot perform in status {request.status.value} (authorization required)")
        with self.repo.transaction():
            before = request.status
            request.status = ActionStatus.PERFORMED
            request.performed_by = principal_id
            request.performed_at = at
            request.notes = notes
            request.updated_at = at
            self.repo.save_action(request)
            self._audit(request, before, at, f"performed by {principal_id}")
        return request

    def verify_outcome(self, action_id: str, at: datetime) -> ActionRequest:
        """Check the expected outcome against fresh evidence and write outcome memory."""
        request = self._get(action_id)
        if request.status not in (ActionStatus.PERFORMED, ActionStatus.OUTCOME_UNVERIFIED):
            raise InvalidTransition(f"cannot verify outcome in status {request.status.value}")
        with self.repo.transaction():
            before = request.status
            checks = [
                self.conditions.check(c, at)
                for c in request.expected_outcome
            ]
            # Evidence must post-date the action: a pre-action observation can neither
            # verify nor refute it (it only shows the state before anyone acted).
            for chk in checks:
                if chk.state != ConditionState.UNSUPPORTED and not self._after(chk.evidence_refs, request.performed_at):
                    chk.state = ConditionState.UNSUPPORTED
                    chk.reason = "no evidence observed after the action was performed"
            request.outcome_checks = checks
            if any(c.state == ConditionState.VIOLATED for c in checks):
                request.status, result = ActionStatus.OUTCOME_FAILED, OutcomeResult.FAILED
            elif checks and all(c.state == ConditionState.SATISFIED for c in checks):
                request.status, result = ActionStatus.OUTCOME_VERIFIED, OutcomeResult.VERIFIED
            else:
                request.status, result = ActionStatus.OUTCOME_UNVERIFIED, OutcomeResult.UNVERIFIED
            request.requested_observations = [
                self.conditions.request_for(c) for c in checks if c.state == ConditionState.UNSUPPORTED
            ]
            request.updated_at = at
            self.repo.save_action(request)
            self.repo.save_outcome(
                OutcomeRecord(
                    action_id=request.id,
                    action=request.action,
                    conditions=request.prerequisite_checks,
                    result=result,
                    observed=checks,
                    evidence_refs=sorted({e for c in checks for e in c.evidence_refs}),
                    performed_by=request.performed_by,
                    recorded_at=at,
                )
            )
            self._audit(request, before, at, f"outcome {result.value}")
        return request

    def execute_autonomously(self, action_id: str, principal_id: str = ORBIT_AGENT) -> None:
        """There is deliberately no actuator. Spec §16/§36: no consequential autonomous
        physical actuation in the MVP."""
        raise AutonomousActuationProhibited(
            "ORBIT does not actuate physical systems; a human must perform the action and report it"
        )

    # --------------------------------------------------------------- helpers
    def _get(self, action_id: str) -> ActionRequest:
        request = self.repo.get_action(action_id)
        if request is None:
            raise ActionError(f"action {action_id} not found")
        return request

    def _verify_prerequisites(self, request: ActionRequest, at: datetime) -> None:
        checks = [self.conditions.check(c, at) for c in request.prerequisites]
        request.prerequisite_checks = checks
        failing = [c for c in checks if c.state != ConditionState.SATISFIED]
        request.requested_observations = [self.conditions.request_for(c) for c in failing if c.state == ConditionState.UNSUPPORTED]
        request.status = ActionStatus.PREREQUISITES_FAILED if failing else ActionStatus.AWAITING_AUTHORIZATION
        if request.status == ActionStatus.AWAITING_AUTHORIZATION and not request.consequential:
            request.status = ActionStatus.AUTHORIZED  # non-consequential: still performed by a person
        request.updated_at = at

    def _after(self, evidence_refs: List[str], when: Optional[datetime]) -> bool:
        if when is None:
            return False
        for ref in evidence_refs:
            ev = self.repo.get_evidence(ref)
            if ev is not None and ev.timestamp >= when:
                return True
        return False

    def _audit(self, request: ActionRequest, before: Optional[ActionStatus], at: datetime, note: str) -> Event:
        return self.repo.save_event(
            Event(
                timestamp=at,
                event_type=EventType.ACTION_STATUS_CHANGED,
                entity_id=request.target_entity_ids[0] if request.target_entity_ids else None,
                task_id=request.task_id,
                before_state={"action_id": request.id, "status": before.value if before else None},
                after_state={"action_id": request.id, "status": request.status.value},
                evidence_refs=sorted({e for c in request.prerequisite_checks + request.outcome_checks for e in c.evidence_refs}),
                description=f"Action '{request.action}': {note} → {request.status.value}",
            )
        )
