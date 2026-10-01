"""Task continuity: tasks are first-class state, never just conversation (spec §11).

Every progress change is an evidence-backed ``TASK_PROGRESS_CHANGED`` event, so the
task state at any past instant can be replayed. Readiness is checked against the
evidence gate: a step is ready only when every transitive prerequisite step is
complete with a still-supported outcome, every precondition is SATISFIED by fresh
evidence of the required status, and the governing procedure revision is unchanged.
``resume`` implements the spec §11 protocol.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.app.domain.models import (
    ConditionCheck,
    Event,
    Interruption,
    ObservationRequest,
    ResumePlan,
    StateCondition,
    StepAssessment,
    Task,
    TaskStateView,
    TaskStep,
    generate_id,
)
from backend.app.domain.types import ConditionState, EpistemicStatus, EventType, SourceType, StepStatus, TaskStatus
from backend.app.repositories.base import Repository
from backend.app.services.conditions import ConditionEvaluator
from backend.app.services.evidence_policy import grade, infer_source_type, is_verification
from backend.app.services.memory import MemoryService
from backend.app.services.world_diff import WorldDiffService
from backend.app.services.world_state_engine import WorldStateEngine

PROCEDURE_ATTRIBUTE = "procedure_revision"
DONE = (StepStatus.COMPLETED, StepStatus.SKIPPED)


class TaskError(ValueError):
    pass


class TaskNotFoundError(TaskError):
    pass


class StepNotReadyError(TaskError):
    pass


class PostconditionViolatedError(TaskError):
    pass


class StepSpec(BaseModel):
    id: Optional[str] = None
    step_order: Optional[int] = None  # defaults to position; set to keep a procedure's own numbering
    description: str
    dependencies: List[str] = Field(default_factory=list)
    preconditions: List[StateCondition] = Field(default_factory=list)
    postconditions: List[StateCondition] = Field(default_factory=list)


class TaskService:
    def __init__(self, repository: Repository, engine: WorldStateEngine):
        self.repo = repository
        self.engine = engine
        self.conditions = ConditionEvaluator(repository, engine.claims)
        memory = MemoryService(repository, engine.claims, engine.relations, engine.anchors)
        self._diff = WorldDiffService(repository, memory)

    # -------------------------------------------------------------- creation
    def create_task(
        self,
        goal: str,
        steps: List[StepSpec],
        at: datetime,
        task_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
        procedure_entity_id: Optional[str] = None,
        procedure_revision: Optional[str] = None,
    ) -> Task:
        task_id = task_id or generate_id("task")
        if self.repo.get_task(task_id) is not None:
            raise TaskError(f"Task {task_id} already exists")
        built = [
            TaskStep(
                id=spec.id or generate_id("step"),
                task_id=task_id,
                step_order=spec.step_order if spec.step_order is not None else i + 1,
                description=spec.description,
                dependencies=list(spec.dependencies),
                preconditions=list(spec.preconditions),
                postconditions=list(spec.postconditions),
            )
            for i, spec in enumerate(steps)
        ]
        if len({s.step_order for s in built}) != len(built):
            raise TaskError("duplicate step_order")
        self._validate_graph(built)
        task = Task(
            id=task_id,
            goal=goal,
            steps=built,
            assigned_to=assigned_to,
            procedure_entity_id=procedure_entity_id,
            procedure_revision=procedure_revision,
            created_at=at,
            updated_at=at,
        )
        with self.repo.transaction():
            self.repo.save_task(task)
            self._event(task, at, None, {"task_status": task.status.value, "created": True}, [], f"Task {task_id} created")
        return task

    @staticmethod
    def _validate_graph(steps: List[TaskStep]) -> None:
        ids = {s.id for s in steps}
        if len(ids) != len(steps):
            raise TaskError("duplicate step ids")
        deps = {s.id: set(s.dependencies) for s in steps}
        for s in steps:
            missing = deps[s.id] - ids
            if missing:
                raise TaskError(f"step {s.id} depends on unknown steps {sorted(missing)}")
        visiting, done = set(), set()

        def visit(node: str) -> None:
            if node in done:
                return
            if node in visiting:
                raise TaskError(f"dependency cycle through {node}")
            visiting.add(node)
            for d in deps[node]:
                visit(d)
            visiting.discard(node)
            done.add(node)

        for s in steps:
            visit(s.id)

    # -------------------------------------------------------------- progress
    def start_step(self, task_id: str, step_id: str, at: datetime, actor: Optional[str] = None) -> Task:
        task, step = self._require_step(task_id, step_id)
        assessment = self.assess_step(task, step, at)
        if not assessment.ready:
            raise StepNotReadyError(f"step {step_id} is not ready: " + "; ".join(assessment.blockers))
        with self.repo.transaction():
            step.blocked_reason = None
            self._set_step(task, step, StepStatus.IN_PROGRESS, at, [], f"started by {actor or 'unknown'}")
            step.started_at = step.started_at or at
            self._set_task(task, TaskStatus.IN_PROGRESS, at)
            self.repo.save_task(task)
        return task

    def complete_step(
        self,
        task_id: str,
        step_id: str,
        at: datetime,
        source: str = "user",
        source_type: Optional[SourceType] = None,
        quality: float = 1.0,
        authority: float = 1.0,
        actor: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Task:
        """Record completion with evidence. A person saying "done" is OBSERVED at best;
        VERIFIED needs a verification or authoritative record."""
        task, step = self._require_step(task_id, step_id)
        unmet = self.unmet_dependencies(task, step)
        if unmet:
            raise StepNotReadyError(f"step {step_id} waits on {unmet}")
        post = [self.conditions.check(c, at) for c in step.postconditions]
        violated = [c for c in post if c.state == ConditionState.VIOLATED]
        if violated:
            raise PostconditionViolatedError(
                f"step {step_id} reported complete but evidence contradicts it: " + "; ".join(
                    f"{c.condition.entity_id}.{c.condition.attribute}: {c.reason}" for c in violated
                )
            )
        stype = source_type or infer_source_type(source)
        with self.repo.transaction():
            evidence = self.engine.record_evidence(
                source_type=stype,
                source=source,
                source_reference=f"task:{task_id}/{step_id}",
                timestamp=at,
                quality=quality,
                authority=authority,
                provenance={"actor": actor} if actor else {},
                content={"kind": "step_completion", "task_id": task_id, "step_id": step_id, "note": note},
            )
            completion = EpistemicStatus.VERIFIED if is_verification(stype, quality, authority) else grade(stype, quality, authority)
            if post and all(c.state == ConditionState.SATISFIED and c.status == EpistemicStatus.VERIFIED for c in post):
                completion = EpistemicStatus.VERIFIED  # outcome verified by world evidence (spec §16)
            step.completion_status = completion
            step.completed_at = at
            step.completed_by = actor
            step.blocked_reason = None
            step.invalidated_reason = None
            step.evidence_refs.append(evidence.id)
            self._set_step(task, step, StepStatus.COMPLETED, at, [evidence.id], note or "completed")
            if completion == EpistemicStatus.VERIFIED:
                task.last_verified_at = at
            if all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in task.steps):
                self._set_task(task, TaskStatus.COMPLETED, at)
            elif task.status == TaskStatus.PENDING:
                self._set_task(task, TaskStatus.IN_PROGRESS, at)
            self.repo.save_task(task)
        return task

    def interrupt_task(self, task_id: str, at: datetime, reason: Optional[str] = None, actor: Optional[str] = None) -> Task:
        task = self._require_task(task_id)
        if task.status in (TaskStatus.COMPLETED, TaskStatus.ABANDONED):
            raise TaskError(f"task {task_id} is {task.status.value}")
        with self.repo.transaction():
            task.interruptions.append(Interruption(at=at, reason=reason, actor=actor))
            self._set_task(task, TaskStatus.INTERRUPTED, at, reason)
            self.repo.save_task(task)
        return task

    # --------------------------------------------------------------- queries
    def get(self, task_id: str) -> Task:
        return self._require_task(task_id)

    def state_at(self, task_id: str, as_of: datetime) -> Optional[TaskStateView]:
        """Replay progress events: task state as it was at ``as_of``."""
        task = self._require_task(task_id)
        if as_of < task.created_at:
            return None
        status = TaskStatus.PENDING
        steps = {s.id: StepStatus.PENDING for s in task.steps}
        for e in self.repo.get_events_for_task(task_id):
            if e.timestamp > as_of or not e.after_state:
                continue
            if "task_status" in e.after_state:
                status = TaskStatus(e.after_state["task_status"])
            if "step_id" in e.after_state and e.after_state["step_id"] in steps:
                steps[e.after_state["step_id"]] = StepStatus(e.after_state["status"])
        return TaskStateView(task_id=task_id, as_of=as_of, status=status, steps=steps)

    def history(self, task_id: str) -> List[Event]:
        self._require_task(task_id)
        return self.repo.get_events_for_task(task_id)

    @staticmethod
    def unmet_dependencies(task: Task, step: TaskStep) -> List[str]:
        by_id = {s.id: s for s in task.steps}
        return [d for d in step.dependencies if by_id[d].status not in DONE]

    # -------------------------------------------------------------- readiness
    @staticmethod
    def ancestors(task: Task, step: TaskStep) -> List[TaskStep]:
        by_id = {s.id: s for s in task.steps}
        seen, stack = set(), list(step.dependencies)
        while stack:
            sid = stack.pop()
            if sid not in seen:
                seen.add(sid)
                stack.extend(by_id[sid].dependencies)
        return sorted((by_id[s] for s in seen), key=lambda s: s.step_order)

    def revision_check(self, task: Task, as_of: datetime) -> Optional[ConditionCheck]:
        if not task.procedure_entity_id:
            return None
        return self.conditions.check(
            StateCondition(
                entity_id=task.procedure_entity_id,
                attribute=PROCEDURE_ATTRIBUTE,
                expected=task.procedure_revision,
                description="task was planned against this procedure revision",
            ),
            as_of,
        )

    def assess_step(self, task: Task, step: TaskStep, as_of: datetime, revision: Optional[ConditionCheck] = None) -> StepAssessment:
        revision = revision if revision is not None else self.revision_check(task, as_of)
        pre = [self.conditions.check(c, as_of) for c in step.preconditions]
        post = [self.conditions.check(c, as_of) for c in step.postconditions] if step.status in DONE else []
        blockers: List[str] = []
        if step.status not in DONE:
            if revision is not None and revision.state != ConditionState.SATISFIED:
                blockers.append(f"procedure revision: {revision.reason}")
            for anc in self.ancestors(task, step):
                label = f"step {anc.step_order} ({anc.description})"
                if anc.status == StepStatus.NEEDS_REVERIFICATION:
                    blockers.append(f"{label} must be re-verified")
                elif anc.status not in DONE:
                    blockers.append(f"waits on {label}")
                else:
                    for chk in (self.conditions.check(c, as_of) for c in anc.postconditions):
                        if chk.state != ConditionState.SATISFIED:
                            blockers.append(
                                f"{label} outcome not supported: {chk.condition.entity_id}.{chk.condition.attribute} {chk.reason}"
                            )
            for chk in pre:
                if chk.state != ConditionState.SATISFIED:
                    blockers.append(f"precondition {chk.condition.entity_id}.{chk.condition.attribute}: {chk.reason}")
        completion = step.completion_status
        if step.status in DONE and any(c.state != ConditionState.SATISFIED for c in post):
            completion = EpistemicStatus.STALE
        return StepAssessment(
            step_id=step.id,
            step_order=step.step_order,
            description=step.description,
            status=step.status,
            completion_status=completion,
            ready=step.status not in DONE and not blockers,
            blockers=blockers,
            preconditions=pre,
            postconditions=post,
        )

    # ---------------------------------------------------------------- resume
    def resume(self, task_id: str, at: datetime, actor: Optional[str] = None) -> ResumePlan:
        """Spec §11: (1) load last verified state, (2) load world changes since, (3)
        invalidate affected steps, (4) check dependencies and prerequisites, (5) identify
        unresolved evidence, (6) request targeted observations, (7) only then present
        the next supported step."""
        task = self._require_task(task_id)
        # (1) last verified state
        checkpoint = task.last_verified_at or (task.interruptions[-1].at if task.interruptions else task.created_at)
        # (2) relevant world changes since the checkpoint
        involved = {c.entity_id for s in task.steps for c in s.preconditions + s.postconditions}
        if task.procedure_entity_id:
            involved.add(task.procedure_entity_id)
        changes = [
            c for c in self._diff.diff(checkpoint, at, include_tasks=False, save=False).changes if c.entity_id in involved
        ]

        with self.repo.transaction():
            # (3) completed steps whose outcome the world now contradicts
            invalidated: List[str] = []
            for step in task.steps:
                if step.status != StepStatus.COMPLETED or not step.postconditions:
                    continue
                broken = [
                    chk
                    for chk in (self.conditions.check(c, at) for c in step.postconditions)
                    if chk.state == ConditionState.VIOLATED or chk.status == EpistemicStatus.CONTRADICTED
                ]
                if broken:
                    step.invalidated_reason = "; ".join(
                        f"{b.condition.entity_id}.{b.condition.attribute}: {b.reason}" for b in broken
                    )
                    step.completion_status = EpistemicStatus.STALE
                    self._set_step(task, step, StepStatus.NEEDS_REVERIFICATION, at, [], f"outcome invalidated — {step.invalidated_reason}")
                    invalidated.append(step.id)

            # (4) dependencies, prerequisites, procedure revision
            revision = self.revision_check(task, at)
            assessments = [self.assess_step(task, s, at, revision) for s in task.steps]
            blocked: List[str] = []
            for step, a in zip(task.steps, assessments):
                if step.status in (StepStatus.PENDING, StepStatus.BLOCKED):
                    new = StepStatus.BLOCKED if a.blockers else StepStatus.PENDING
                    step.blocked_reason = "; ".join(a.blockers) or None
                    if new != step.status:
                        self._set_step(task, step, new, at, [], step.blocked_reason or "prerequisites satisfied")
                        a.status = new
                if a.blockers:
                    blocked.append(step.id)

            # (5) unresolved evidence and (6) targeted observation requests
            unresolved: List[ConditionCheck] = []
            requests: Dict[tuple, ObservationRequest] = {}

            def need(check: ConditionCheck, step_id: Optional[str]) -> None:
                if check.state != ConditionState.UNSUPPORTED:
                    return
                key = (check.condition.entity_id, check.condition.attribute)
                if key not in requests:
                    unresolved.append(check)
                    requests[key] = self.conditions.request_for(check)
                if step_id and step_id not in requests[key].for_steps:
                    requests[key].for_steps.append(step_id)

            if revision is not None:
                need(revision, None)
            for step, a in zip(task.steps, assessments):
                if step.status in DONE:
                    continue
                for chk in a.preconditions:
                    need(chk, step.id)
                for anc in self.ancestors(task, step):
                    if anc.status in DONE:
                        for chk in (self.conditions.check(c, at) for c in anc.postconditions):
                            need(chk, step.id)

            # (7) only now: the next supported step (re-verification first)
            frontier = [a for a in assessments if a.ready]
            redo = [a for a in frontier if a.status == StepStatus.NEEDS_REVERIFICATION]
            next_step = (redo or frontier or [None])[0]
            can_continue = next_step is not None

            all_done = all(s.status in DONE for s in task.steps)
            if all_done:
                self._set_task(task, TaskStatus.COMPLETED, at)
            elif can_continue:
                self._set_task(task, TaskStatus.IN_PROGRESS, at, f"resumed by {actor or 'unknown'}")
                if task.interruptions and task.interruptions[-1].resumed_at is None:
                    task.interruptions[-1].resumed_at = at
                    task.interruptions[-1].resumed_by = actor
            else:
                self._set_task(task, TaskStatus.BLOCKED, at, "resume blocked: evidence or prerequisites missing")
            self.repo.save_task(task)

        rev_info = None
        if revision is not None:
            rev_info = {
                "planned": task.procedure_revision,
                "current": revision.observed_value,
                "state": revision.state.value,
                "status": revision.status.value,
            }
        return ResumePlan(
            task_id=task.id,
            as_of=at,
            checkpoint=checkpoint,
            resumed_by=actor,
            world_changes=changes,
            invalidated_steps=invalidated,
            blocked_steps=blocked,
            unresolved=unresolved,
            requested_observations=list(requests.values()),
            procedure_revision=rev_info,
            steps=assessments,
            next_step=next_step,
            can_continue=can_continue,
            task_status=task.status,
            message=self._resume_message(task, checkpoint, changes, invalidated, next_step, list(requests.values()), all_done),
        )

    @staticmethod
    def _resume_message(task, checkpoint, changes, invalidated, next_step, requests, all_done) -> str:
        parts = [f"Task '{task.goal}': last verified state {checkpoint.isoformat()}; {len(changes)} relevant change(s) since."]
        by_id = {s.id: s for s in task.steps}
        for sid in invalidated:
            parts.append(f"Step {by_id[sid].step_order} must be re-verified ({by_id[sid].invalidated_reason}).")
        if all_done:
            parts.append("All steps complete.")
        elif next_step is not None:
            verb = "Re-verify" if next_step.status == StepStatus.NEEDS_REVERIFICATION else "Next supported step"
            parts.append(f"{verb}: step {next_step.step_order} — {next_step.description}.")
        else:
            parts.append("Cannot continue yet: no step is supported by current evidence.")
        for r in requests:
            parts.append(f"Requested observation: {r.instruction}")
        return " ".join(parts)

    def acknowledge_revision(self, task_id: str, revision: str, at: datetime, actor: Optional[str] = None) -> Task:
        """A person reviews the task against a new procedure revision (human authority)."""
        task = self._require_task(task_id)
        with self.repo.transaction():
            before = task.procedure_revision
            task.procedure_revision = revision
            task.updated_at = max(task.updated_at, at)
            self._event(
                task, at, {"procedure_revision": before}, {"procedure_revision": revision}, [],
                f"Procedure revision {before} → {revision} acknowledged by {actor or 'unknown'}",
            )
            self.repo.save_task(task)
        return task

    # --------------------------------------------------------------- helpers
    def _require_task(self, task_id: str) -> Task:
        task = self.repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")
        return task

    def _require_step(self, task_id: str, step_id: str):
        task = self._require_task(task_id)
        step = next((s for s in task.steps if s.id == step_id), None)
        if step is None:
            raise TaskNotFoundError(f"Step {step_id} not found in task {task_id}")
        return task, step

    def _set_step(self, task: Task, step: TaskStep, status: StepStatus, at: datetime, evidence: List[str], note: str) -> None:
        before = step.status
        step.status = status
        task.updated_at = max(task.updated_at, at)
        self._event(
            task,
            at,
            {"step_id": step.id, "status": before.value},
            {"step_id": step.id, "status": status.value, "completion_status": step.completion_status.value},
            evidence,
            f"Step {step.step_order} ({step.description}): {before.value} → {status.value} — {note}",
        )

    def _set_task(self, task: Task, status: TaskStatus, at: datetime, note: Optional[str] = None) -> None:
        if task.status == status:
            return
        before = task.status
        task.status = status
        task.updated_at = max(task.updated_at, at)
        self._event(
            task,
            at,
            {"task_status": before.value},
            {"task_status": status.value},
            [],
            f"Task {task.id}: {before.value} → {status.value}" + (f" ({note})" if note else ""),
        )

    def _event(
        self,
        task: Task,
        at: datetime,
        before: Optional[Dict[str, Any]],
        after: Dict[str, Any],
        evidence: List[str],
        description: str,
    ) -> Event:
        return self.repo.save_event(
            Event(
                timestamp=at,
                event_type=EventType.TASK_PROGRESS_CHANGED,
                task_id=task.id,
                before_state=before,
                after_state=after,
                evidence_refs=evidence,
                description=description,
            )
        )
