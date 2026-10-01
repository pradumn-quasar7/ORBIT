"""Composition root: wires repository, clock and services together.

Services are constructed once per app (or per test) and receive their collaborators
explicitly, so any provider can be swapped for ablations (ADR-002).
"""
import os
from dataclasses import dataclass
from typing import Optional

from backend.app.core.clock import Clock, SystemClock
from backend.app.repositories.base import Repository
from backend.app.services.hypotheses import HypothesisService
from backend.app.services.memory import MemoryService
from backend.app.services.relations import RelationService
from backend.app.services.search import SearchService
from backend.app.services.spatial import AnchorRegistry
from backend.app.services.tasks import TaskService
from backend.app.services.world_diff import WorldDiffService
from backend.app.services.world_state_engine import WorldStateEngine

DEFAULT_DATABASE_URL = "sqlite:///./orbit.db"


@dataclass
class OrbitServices:
    repo: Repository
    clock: Clock
    anchors: AnchorRegistry
    relations: RelationService
    engine: WorldStateEngine
    memory: MemoryService
    tasks: TaskService
    hypotheses: HypothesisService
    search: SearchService
    diff: WorldDiffService

    @classmethod
    def build(cls, repo: Repository, clock: Optional[Clock] = None) -> "OrbitServices":
        clock = clock or SystemClock()
        anchors = AnchorRegistry(repo)
        relations = RelationService(repo)
        engine = WorldStateEngine(repository=repo, anchors=anchors, relations=relations)
        memory = MemoryService(repo, engine.claims, relations, anchors)
        tasks = TaskService(repo, engine)
        return cls(
            repo=repo,
            clock=clock,
            anchors=anchors,
            relations=relations,
            engine=engine,
            memory=memory,
            tasks=tasks,
            hypotheses=HypothesisService(repo, engine),
            search=SearchService(repo, engine),
            diff=WorldDiffService(repo, memory, tasks),
        )


def default_repository() -> Repository:
    """SQL repository from ORBIT_DATABASE_URL, migrated to head."""
    from backend.app.repositories.sql import SqlRepository
    from database.migrate import upgrade_to_head

    url = os.environ.get("ORBIT_DATABASE_URL", DEFAULT_DATABASE_URL)
    upgrade_to_head(url)
    return SqlRepository(url)
