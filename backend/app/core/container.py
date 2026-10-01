"""Composition root: wires repository, clock and services together.

Services are constructed once per app (or per test) and receive their collaborators
explicitly, so any provider can be swapped for ablations (ADR-002).
"""
import os
from dataclasses import dataclass
from typing import Optional

from backend.app.core.clock import Clock, SystemClock
from backend.app.repositories.base import Repository
from backend.app.services.world_state_engine import WorldStateEngine

DEFAULT_DATABASE_URL = "sqlite:///./orbit.db"


@dataclass
class OrbitServices:
    repo: Repository
    clock: Clock
    engine: WorldStateEngine

    @classmethod
    def build(cls, repo: Repository, clock: Optional[Clock] = None) -> "OrbitServices":
        clock = clock or SystemClock()
        return cls(repo=repo, clock=clock, engine=WorldStateEngine(repository=repo))


def default_repository() -> Repository:
    """SQL repository from ORBIT_DATABASE_URL, migrated to head."""
    from backend.app.repositories.sql import SqlRepository
    from database.migrate import upgrade_to_head

    url = os.environ.get("ORBIT_DATABASE_URL", DEFAULT_DATABASE_URL)
    upgrade_to_head(url)
    return SqlRepository(url)
