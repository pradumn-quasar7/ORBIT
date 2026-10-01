"""Procedural memory: tasks are first-class state, never just conversation (spec §11).

Every progress change is an evidence-backed ``TASK_PROGRESS_CHANGED`` event, so the
task state at any past instant can be replayed. Dependency/precondition reasoning
and the resume protocol build on this in Phase 5.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.app.domain.models import Event, Interruption, StateCondition, Task, TaskStateView, TaskStep, generate_id
from backend.app.domain.types import EpistemicStatus, EventType, SourceType, StepStatus, TaskStatus
from backend.app.repositories.base import Repository
from backend.app.services.evidence_policy import grade, infer_source_type, is_verification
from backend.app.services.world_state_engine import WorldStateEngine


class TaskError(ValueError):
    pass


class TaskNotFoundError(TaskError):
    pass


class StepNotReadyError(TaskError):
    pass


class StepSpec(BaseModel):
    id: Optional[str] = None
    description: str
    dependencies: List[str] = Field(default_factory=list)
    preconditions: List[StateCondition] = Field(default_factory=list)
    postconditions: List[StateCondition] = Field(default_factory=list)


class TaskService:
    def __init__(self, repository: Repository, engine: WorldStateEngine):
        self.repo = repository
        self.engine = engine

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
                step_order=i + 1,
                description=spec.description,
                dependencies=list(spec.dependencies),
                preconditions=list(spec.preconditions),
                postconditions=list(spec.postconditions),
            )
            for i, spec in enumerate(steps)
        ]
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
        unmet = self.unmet_dependencies(task, step)
        if unmet:
            raise StepNotReadyError(f"step {step_id} waits on {unmet}")
        with self.repo.transaction():
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
        return [d for d in step.dependencies if by_id[d].status not in (StepStatus.COMPLETED, StepStatus.SKIPPED)]

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
