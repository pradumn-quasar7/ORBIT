"""Forking a world into an isolated, counterfactual-capable sandbox."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from backend.app.core.clock import FixedClock
from backend.app.domain.models import generate_id
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.services.belief import LOCATION
from backend.app.services.projection import WorldProjector


@dataclass
class Sandbox:
    id: str
    label: Optional[str]
    as_of: datetime  # the instant of the source world it was forked from
    services: "OrbitServices"  # noqa: F821 - isolated services over an in-memory store
    clock: FixedClock
    variations: List[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def fork(source, as_of: datetime, label: Optional[str] = None) -> Sandbox:
    """Project ``source`` (OrbitServices) at ``as_of`` into a fresh in-memory store.

    The source is only read. The sandbox's engine accepts SIMULATION evidence; the
    source's never does, so premises cannot leak back (ADR-029)."""
    from backend.app.core.container import OrbitServices

    repo = InMemoryRepository()
    stats = WorldProjector(source.repo).project(as_of, repo)
    clock = FixedClock(as_of)
    services = OrbitServices.build(repo, clock, source.config, sandbox=True)
    for entity in repo.list_entities():
        services.engine.belief.materialize(entity, as_of)
        entity.anchor = entity.current_state.get(LOCATION) or entity.anchor
        repo.save_entity(entity)
    return Sandbox(id=generate_id("sbx"), label=label, as_of=as_of, services=services, clock=clock, counts=stats.counts)
