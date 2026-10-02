"""Storage boundary for the world state.

Every implementation must behave as a value store: objects returned by ``get_*`` /
``list_*`` are copies, and nothing is persisted until ``save_*`` is called. This keeps
the in-memory and SQL implementations interchangeable (ADR-005).
"""
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Iterator, List, Optional

from backend.app.domain.models import (
    ActionRequest,
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


class Repository(ABC):
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes atomically. Nested transactions join the outer one."""
        yield

    @property
    def in_transaction(self) -> bool:
        return False

    # Entities
    @abstractmethod
    def save_entity(self, entity: Entity) -> Entity: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[Entity]: ...

    @abstractmethod
    def list_entities(self) -> List[Entity]: ...

    # Observations
    @abstractmethod
    def save_observation(self, observation: Observation) -> Observation: ...

    @abstractmethod
    def get_observation(self, observation_id: str) -> Optional[Observation]: ...

    @abstractmethod
    def list_observations(self) -> List[Observation]: ...

    @abstractmethod
    def list_observations_for_session(self, session_id: str) -> List[Observation]: ...

    # Evidence
    @abstractmethod
    def save_evidence(self, evidence: Evidence) -> Evidence: ...

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> Optional[Evidence]: ...

    @abstractmethod
    def list_evidence(self) -> List[Evidence]: ...

    # State versions (sorted by valid_from)
    @abstractmethod
    def save_state_version(self, sv: StateVersion) -> StateVersion: ...

    @abstractmethod
    def get_state_versions_for_entity(
        self, entity_id: str, attribute: Optional[str] = None
    ) -> List[StateVersion]: ...

    # Conflicts (sorted by opened_at)
    @abstractmethod
    def save_conflict(self, conflict: Conflict) -> Conflict: ...

    @abstractmethod
    def get_conflict(self, conflict_id: str) -> Optional[Conflict]: ...

    @abstractmethod
    def list_conflicts(self, entity_id: Optional[str] = None, attribute: Optional[str] = None) -> List[Conflict]: ...

    # Claim dependencies
    @abstractmethod
    def save_dependency(self, dependency: ClaimDependency) -> ClaimDependency: ...

    @abstractmethod
    def list_dependencies(
        self, depends_on_entity_id: Optional[str] = None, depends_on_attribute: Optional[str] = None
    ) -> List[ClaimDependency]: ...

    # Events (sorted by timestamp)
    @abstractmethod
    def save_event(self, event: Event) -> Event: ...

    @abstractmethod
    def list_events(self) -> List[Event]: ...

    @abstractmethod
    def get_events_for_entity(self, entity_id: str) -> List[Event]: ...

    @abstractmethod
    def get_events_for_task(self, task_id: str) -> List[Event]: ...

    # Causal hypotheses
    @abstractmethod
    def save_hypothesis(self, hypothesis: CausalHypothesis) -> CausalHypothesis: ...

    @abstractmethod
    def get_hypothesis(self, hypothesis_id: str) -> Optional[CausalHypothesis]: ...

    @abstractmethod
    def list_hypotheses(self) -> List[CausalHypothesis]: ...

    # Relations
    @abstractmethod
    def save_relation(self, relation: Relation) -> Relation: ...

    @abstractmethod
    def list_relations(self) -> List[Relation]: ...

    @abstractmethod
    def get_relations_for_entity(self, entity_id: str) -> List[Relation]:
        """Relations where the entity is source or target, ordered by valid_from."""

    # Anchors
    @abstractmethod
    def save_anchor(self, anchor: Anchor) -> Anchor: ...

    @abstractmethod
    def get_anchor(self, anchor_id: str) -> Optional[Anchor]: ...

    @abstractmethod
    def list_anchors(self) -> List[Anchor]: ...

    # Sessions
    @abstractmethod
    def save_session(self, session: Session) -> Session: ...

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Session]: ...

    @abstractmethod
    def list_sessions(self) -> List[Session]: ...

    # Tasks (steps are stored with their task and returned ordered by step_order)
    @abstractmethod
    def save_task(self, task: Task) -> Task: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Task]: ...

    @abstractmethod
    def list_tasks(self) -> List[Task]: ...

    # World diffs
    @abstractmethod
    def save_world_diff(self, diff: WorldDiff) -> WorldDiff: ...

    @abstractmethod
    def get_world_diff(self, diff_id: str) -> Optional[WorldDiff]: ...

    # Search coverage
    @abstractmethod
    def save_search_coverage(self, coverage: SearchCoverage) -> SearchCoverage: ...

    @abstractmethod
    def list_search_coverage(self) -> List[SearchCoverage]: ...

    # Principals (who may reason / recommend / authorize / actuate)
    @abstractmethod
    def save_principal(self, principal: Principal) -> Principal: ...

    @abstractmethod
    def get_principal(self, principal_id: str) -> Optional[Principal]: ...

    @abstractmethod
    def list_principals(self) -> List[Principal]: ...

    # Actions and outcome memory
    @abstractmethod
    def save_action(self, action: ActionRequest) -> ActionRequest: ...

    @abstractmethod
    def get_action(self, action_id: str) -> Optional[ActionRequest]: ...

    @abstractmethod
    def list_actions(self) -> List[ActionRequest]: ...

    @abstractmethod
    def save_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord: ...

    @abstractmethod
    def list_outcomes(self, action_id: Optional[str] = None) -> List[OutcomeRecord]: ...
