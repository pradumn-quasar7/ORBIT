from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, TypeVar

from pydantic import BaseModel

from backend.app.domain.models import (
    Entity,
    Event,
    Evidence,
    Observation,
    Relation,
    SearchCoverage,
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

    # Events
    def save_event(self, event: Event) -> Event:
        return self._put(self.events, event)

    def list_events(self) -> List[Event]:
        return sorted((_copy(e) for e in self.events.values()), key=lambda x: x.timestamp)

    def get_events_for_entity(self, entity_id: str) -> List[Event]:
        return [e for e in self.list_events() if e.entity_id == entity_id]

    # Relations
    def save_relation(self, relation: Relation) -> Relation:
        return self._put(self.relations, relation)

    def list_relations(self) -> List[Relation]:
        return sorted((_copy(r) for r in self.relations.values()), key=lambda r: r.valid_from)

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
