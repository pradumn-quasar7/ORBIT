from typing import Dict, List, Optional
from datetime import datetime

from backend.app.domain.models import (
    Entity,
    Observation,
    Evidence,
    StateVersion,
    Event,
    Relation,
    Task,
    WorldDiff,
    SearchCoverage,
)

class InMemoryRepository:
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

    # Entities
    def save_entity(self, entity: Entity) -> Entity:
        self.entities[entity.id] = entity
        return entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self.entities.get(entity_id)

    def list_entities(self) -> List[Entity]:
        return list(self.entities.values())

    # Observations
    def save_observation(self, observation: Observation) -> Observation:
        self.observations[observation.id] = observation
        return observation

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        return self.observations.get(observation_id)

    def list_observations(self) -> List[Observation]:
        return list(self.observations.values())

    # Evidence
    def save_evidence(self, evidence: Evidence) -> Evidence:
        self.evidence[evidence.id] = evidence
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        return self.evidence.get(evidence_id)

    # State Versions
    def save_state_version(self, sv: StateVersion) -> StateVersion:
        self.state_versions[sv.id] = sv
        return sv

    def get_state_versions_for_entity(self, entity_id: str, attribute: Optional[str] = None) -> List[StateVersion]:
        results = [
            sv for sv in self.state_versions.values()
            if sv.entity_id == entity_id and (attribute is None or sv.attribute == attribute)
        ]
        return sorted(results, key=lambda x: x.valid_from)

    # Events
    def save_event(self, event: Event) -> Event:
        self.events[event.id] = event
        return event

    def list_events(self) -> List[Event]:
        return sorted(list(self.events.values()), key=lambda x: x.timestamp)

    def get_events_for_entity(self, entity_id: str) -> List[Event]:
        return [e for e in self.events.values() if e.entity_id == entity_id]

    # Relations
    def save_relation(self, relation: Relation) -> Relation:
        self.relations[relation.id] = relation
        return relation

    def list_relations(self) -> List[Relation]:
        return list(self.relations.values())

    # Tasks
    def save_task(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    # World Diffs
    def save_world_diff(self, diff: WorldDiff) -> WorldDiff:
        self.world_diffs[diff.id] = diff
        return diff

    # Search Coverage
    def save_search_coverage(self, coverage: SearchCoverage) -> SearchCoverage:
        self.search_coverages[coverage.id] = coverage
        return coverage

    def list_search_coverage(self) -> List[SearchCoverage]:
        return list(self.search_coverages.values())
