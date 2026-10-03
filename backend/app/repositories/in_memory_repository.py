from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, TypeVar

from pydantic import BaseModel

from backend.app.domain.models import (
    ActionRequest,
    IdentityMerge,
    Anchor,
    CausalHypothesis,
    ClaimDependency,
    Conflict,
    Entity,
    Event,
    Evidence,
    Observation,
    OutcomeRecord,
    Principal,
    Relation,
    SearchCoverage,
    Session,
    StateVersion,
    Task,
    WorldDiff,
)
from backend.app.repositories.base import Repository

M = TypeVar("M", bound=BaseModel)

_STORES = (
    "entities",
    "observations",
    "evidence",
    "state_versions",
    "events",
    "relations",
    "tasks",
    "world_diffs",
    "search_coverages",
    "anchors",
    "sessions",
    "conflicts",
    "dependencies",
    "hypotheses",
    "principals",
    "actions",
    "outcomes",
    "merges",
)


def _copy(model: M) -> M:
    return model.model_copy(deep=True)


class InMemoryRepository(Repository):
    """Test/dev repository. Stores deep copies so callers cannot mutate state without save."""

    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.observations: Dict[str, Observation] = {}
        self.evidence: Dict[str, Evidence] = {}
        self.state_versions: Dict[str, StateVersion] = {}
        self.events: Dict[str, Event] = {}
        self.relations: Dict[str, Relation] = {}
        self.tasks: Dict[str, Task] = {}
        self.world_diffs: Dict[str, WorldDiff] = {}
        self.search_coverages: Dict[str, SearchCoverage] = {}
        self.anchors: Dict[str, Anchor] = {}
        self.sessions: Dict[str, Session] = {}
        self.conflicts: Dict[str, Conflict] = {}
        self.dependencies: Dict[str, ClaimDependency] = {}
        self.hypotheses: Dict[str, CausalHypothesis] = {}
        self.principals: Dict[str, Principal] = {}
        self.actions: Dict[str, ActionRequest] = {}
        self.outcomes: Dict[str, OutcomeRecord] = {}
        self.merges: Dict[str, IdentityMerge] = {}
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Stored values are never mutated in place, so a shallow copy of each store
        # is a complete snapshot to roll back to.
        snapshot = {name: dict(getattr(self, name)) for name in _STORES} if self._depth == 0 else None
        self._depth += 1
        try:
            yield
        except BaseException:
            if snapshot is not None:
                for name, store in snapshot.items():
                    setattr(self, name, store)
            raise
        finally:
            self._depth -= 1

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    def _put(self, store: Dict[str, M], model: M) -> M:
        store[model.id] = _copy(model)  # type: ignore[attr-defined]
        return model

    @staticmethod
    def _get(store: Dict[str, M], key: str) -> Optional[M]:
        found = store.get(key)
        return _copy(found) if found is not None else None

    # Entities
    def save_entity(self, entity: Entity) -> Entity:
        return self._put(self.entities, entity)

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._get(self.entities, entity_id)

    def list_entities(self) -> List[Entity]:
        return [_copy(e) for e in self.entities.values()]

    # Observations
    def save_observation(self, observation: Observation) -> Observation:
        return self._put(self.observations, observation)

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        return self._get(self.observations, observation_id)

    def list_observations(self) -> List[Observation]:
        return sorted((_copy(o) for o in self.observations.values()), key=lambda o: o.timestamp)

    def list_observations_for_session(self, session_id: str) -> List[Observation]:
        return [o for o in self.list_observations() if o.session_id == session_id]

    # Evidence
    def save_evidence(self, evidence: Evidence) -> Evidence:
        return self._put(self.evidence, evidence)

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        return self._get(self.evidence, evidence_id)

    def list_evidence(self) -> List[Evidence]:
        return sorted((_copy(e) for e in self.evidence.values()), key=lambda e: e.timestamp)

    # State versions
    def save_state_version(self, sv: StateVersion) -> StateVersion:
        return self._put(self.state_versions, sv)

    def get_state_versions_for_entity(
        self, entity_id: str, attribute: Optional[str] = None
    ) -> List[StateVersion]:
        results = [
            _copy(sv)
            for sv in self.state_versions.values()
            if sv.entity_id == entity_id and (attribute is None or sv.attribute == attribute)
        ]
        return sorted(results, key=lambda x: x.valid_from)

    # Conflicts
    def save_conflict(self, conflict: Conflict) -> Conflict:
        return self._put(self.conflicts, conflict)

    def get_conflict(self, conflict_id: str) -> Optional[Conflict]:
        return self._get(self.conflicts, conflict_id)

    def list_conflicts(self, entity_id: Optional[str] = None, attribute: Optional[str] = None) -> List[Conflict]:
        found = [
            _copy(c)
            for c in self.conflicts.values()
            if (entity_id is None or c.entity_id == entity_id) and (attribute is None or c.attribute == attribute)
        ]
        return sorted(found, key=lambda c: c.opened_at)

    # Claim dependencies
    def save_dependency(self, dependency: ClaimDependency) -> ClaimDependency:
        return self._put(self.dependencies, dependency)

    def list_dependencies(
        self, depends_on_entity_id: Optional[str] = None, depends_on_attribute: Optional[str] = None
    ) -> List[ClaimDependency]:
        found = [
            _copy(d)
            for d in self.dependencies.values()
            if (depends_on_entity_id is None or d.depends_on_entity_id == depends_on_entity_id)
            and (depends_on_attribute is None or d.depends_on_attribute == depends_on_attribute)
        ]
        return sorted(found, key=lambda d: d.created_at)

    # Events
    def save_event(self, event: Event) -> Event:
        return self._put(self.events, event)

    def list_events(self) -> List[Event]:
        return sorted((_copy(e) for e in self.events.values()), key=lambda x: x.timestamp)

    def get_events_for_entity(self, entity_id: str) -> List[Event]:
        return [e for e in self.list_events() if e.entity_id == entity_id]

    def get_events_for_task(self, task_id: str) -> List[Event]:
        return [e for e in self.list_events() if e.task_id == task_id]

    # Causal hypotheses
    def save_hypothesis(self, hypothesis: CausalHypothesis) -> CausalHypothesis:
        return self._put(self.hypotheses, hypothesis)

    def get_hypothesis(self, hypothesis_id: str) -> Optional[CausalHypothesis]:
        return self._get(self.hypotheses, hypothesis_id)

    def list_hypotheses(self) -> List[CausalHypothesis]:
        return sorted((_copy(h) for h in self.hypotheses.values()), key=lambda h: h.created_at)

    # Relations
    def save_relation(self, relation: Relation) -> Relation:
        return self._put(self.relations, relation)

    def list_relations(self) -> List[Relation]:
        return sorted((_copy(r) for r in self.relations.values()), key=lambda r: r.valid_from)

    def get_relations_for_entity(self, entity_id: str) -> List[Relation]:
        return [r for r in self.list_relations() if entity_id in (r.source_entity, r.target_entity)]

    # Anchors
    def save_anchor(self, anchor: Anchor) -> Anchor:
        return self._put(self.anchors, anchor)

    def get_anchor(self, anchor_id: str) -> Optional[Anchor]:
        return self._get(self.anchors, anchor_id)

    def list_anchors(self) -> List[Anchor]:
        return [_copy(a) for a in self.anchors.values()]

    # Sessions
    def save_session(self, session: Session) -> Session:
        return self._put(self.sessions, session)

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._get(self.sessions, session_id)

    def list_sessions(self) -> List[Session]:
        return sorted((_copy(s) for s in self.sessions.values()), key=lambda s: s.started_at)

    # Tasks
    def save_task(self, task: Task) -> Task:
        return self._put(self.tasks, task)

    @staticmethod
    def _ordered(task: Task) -> Task:
        task.steps.sort(key=lambda s: s.step_order)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        task = self._get(self.tasks, task_id)
        return self._ordered(task) if task else None

    def list_tasks(self) -> List[Task]:
        return sorted((self._ordered(_copy(t)) for t in self.tasks.values()), key=lambda t: t.created_at)

    # World diffs
    def save_world_diff(self, diff: WorldDiff) -> WorldDiff:
        return self._put(self.world_diffs, diff)

    def get_world_diff(self, diff_id: str) -> Optional[WorldDiff]:
        return self._get(self.world_diffs, diff_id)

    # Search coverage
    def save_search_coverage(self, coverage: SearchCoverage) -> SearchCoverage:
        return self._put(self.search_coverages, coverage)

    def list_search_coverage(self) -> List[SearchCoverage]:
        return sorted((_copy(c) for c in self.search_coverages.values()), key=lambda c: c.timestamp)

    # Principals
    def save_principal(self, principal: Principal) -> Principal:
        return self._put(self.principals, principal)

    def get_principal(self, principal_id: str) -> Optional[Principal]:
        return self._get(self.principals, principal_id)

    def list_principals(self) -> List[Principal]:
        return sorted((_copy(p) for p in self.principals.values()), key=lambda p: p.created_at)

    # Actions and outcomes
    def save_action(self, action: ActionRequest) -> ActionRequest:
        return self._put(self.actions, action)

    def get_action(self, action_id: str) -> Optional[ActionRequest]:
        return self._get(self.actions, action_id)

    def list_actions(self) -> List[ActionRequest]:
        return sorted((_copy(a) for a in self.actions.values()), key=lambda a: a.created_at)

    def save_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord:
        return self._put(self.outcomes, outcome)

    def list_outcomes(self, action_id: Optional[str] = None) -> List[OutcomeRecord]:
        found = [_copy(o) for o in self.outcomes.values() if action_id is None or o.action_id == action_id]
        return sorted(found, key=lambda o: o.recorded_at)

    # Identity merges
    def save_merge(self, merge: IdentityMerge) -> IdentityMerge:
        return self._put(self.merges, merge)

    def get_merge(self, merge_id: str) -> Optional[IdentityMerge]:
        return self._get(self.merges, merge_id)

    def list_merges(self) -> List[IdentityMerge]:
        return sorted((_copy(m) for m in self.merges.values()), key=lambda m: m.merged_at)
