"""Read model for the inspection dashboard (spec §31): the UI exists to inspect the
research system, not to hide its uncertainty, so every row carries its status."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.app.domain.types import StepStatus, TaskStatus
from backend.app.services.belief import LOCATION


class EntityRow(BaseModel):
    entity_id: str
    name: str
    type: str
    location: Any = None
    location_note: Optional[str] = None  # e.g. "confirmed absent from bench_3 …"
    location_status: str
    freshness: Optional[str] = None
    last_supported_at: Optional[datetime] = None
    status: str
    identity_status: str
    attributes: Dict[str, Dict[str, Any]]


class StepRow(BaseModel):
    step_id: str
    step_order: int
    description: str
    status: str
    completion_status: str
    blocked_reason: Optional[str] = None


class TaskRow(BaseModel):
    task_id: str
    goal: str
    status: str
    steps: List[StepRow]


class DashboardSummary(BaseModel):
    as_of: datetime
    baseline: Optional[datetime] = None
    entities: List[EntityRow]
    changes: List[str]
    conflicts: List[Dict[str, Any]]
    tasks: List[TaskRow]
    requested_observations: List[Dict[str, Any]]
    counts: Dict[str, int]


def build_summary(svc, at: datetime) -> DashboardSummary:
    repo = svc.repo
    snapshot = svc.memory.world_snapshot(at)
    rows: List[EntityRow] = []
    for eid, snap in sorted(snapshot.entities.items()):
        loc = snap.attributes.get(LOCATION)
        entity = repo.get_entity(eid)
        rows.append(
            EntityRow(
                entity_id=eid,
                name=snap.name or eid,
                type=snap.type,
                location=(loc.value if loc.supportable else loc.last_known_value) if loc and loc.has_current_claim else None,
                location_note=None if not loc or loc.has_current_claim else f"{loc.reason} (last known: {loc.last_known_value})",
                location_status=loc.status.value if loc else "UNKNOWN",
                freshness=loc.freshness.state.value if loc and loc.freshness else None,
                last_supported_at=loc.freshness.last_supported_at if loc and loc.freshness else None,
                status=snap.status.value,
                identity_status=entity.identity_status.value if entity else "ESTABLISHED",
                attributes={
                    a: {"value": c.value if c.supportable else c.last_known_value, "status": c.status.value}
                    for a, c in snap.attributes.items()
                    if a != LOCATION
                },
            )
        )

    from backend.app.domain.models import QueryIntent
    from backend.app.domain.types import QueryKind

    baseline = svc.agent._baseline(QueryIntent(kind=QueryKind.WHAT_CHANGED, raw=""), at) or at - timedelta(hours=24)
    diff = svc.diff.diff(baseline, at, save=False)
    changes = [svc.agent.phrase_change(c) for c in diff.changes]

    conflicts = []
    for c in repo.list_conflicts():
        if c.resolved_at is None:
            a = svc.engine.claims.assess_attribute(c.entity_id, c.attribute, at)
            conflicts.append({
                "entity_id": c.entity_id,
                "attribute": c.attribute,
                "opened_at": c.opened_at,
                "sides": [{"value": s.value, "sources": s.sources} for s in a.conflicts],
            })

    tasks = [
        TaskRow(
            task_id=t.id,
            goal=t.goal,
            status=t.status.value,
            steps=[
                StepRow(
                    step_id=s.id,
                    step_order=s.step_order,
                    description=s.description,
                    status=s.status.value,
                    completion_status=svc.tasks.assess_step(t, s, at).completion_status.value
                    if s.status == StepStatus.COMPLETED
                    else s.completion_status.value,
                    blocked_reason=s.blocked_reason,
                )
                for s in t.steps
            ],
        )
        for t in repo.list_tasks()
        if t.status != TaskStatus.ABANDONED
    ]

    plan = svc.perception.plan(at, k=5)
    requests = [
        {"instruction": a.instruction, "resolves": a.resolves, "score": a.score, "action_type": a.action_type.value}
        for a in plan.actions
    ]
    return DashboardSummary(
        as_of=at,
        baseline=baseline,
        entities=rows,
        changes=changes,
        conflicts=conflicts,
        tasks=tasks,
        requested_observations=requests,
        counts={
            "entities": len(rows),
            "uncertain_claims": len(plan.uncertain_claims),
            "open_conflicts": len(conflicts),
            "open_tasks": sum(1 for t in tasks if t.status not in ("COMPLETED",)),
        },
    )
