"""Perception gateway: frame → provider → observation → world state (spec §6.2),
plus evidence minimisation and redaction (spec §17)."""
from datetime import datetime
from typing import List, Optional, Tuple

from backend.app.domain.models import Event, Observation
from backend.app.providers.perception import PerceptionProvider, RawFrame
from backend.app.repositories.base import Repository
from backend.app.services.evidence_policy import canonical_hash
from backend.app.services.world_state_engine import WorldStateEngine


class PerceptionGateway:
    def __init__(self, repository: Repository, engine: WorldStateEngine, retain_raw: bool = False):
        self.repo = repository
        self.engine = engine
        self.retain_raw = retain_raw  # default: keep structured state + a hash, not pixels

    def ingest(self, frame: RawFrame, provider: PerceptionProvider) -> Tuple[Observation, List[Event]]:
        observation = provider.perceive(frame)
        if self.retain_raw and frame.image_ref:
            observation.raw_reference = frame.image_ref
        events = self.engine.record_observation(observation)
        evidence = next(e for e in self.repo.list_evidence() if e.source_reference == observation.id)
        evidence.retention_policy = "raw_retained" if self.retain_raw and frame.image_ref else "hash_only"
        self.repo.save_evidence(evidence)
        return self.repo.get_observation(observation.id), events

    def redact(self, observation_id: str, at: datetime, reason: str, actor: Optional[str] = None) -> Observation:
        """Remove the raw reference while keeping structured facts and verifiability."""
        obs = self.repo.get_observation(observation_id)
        if obs is None:
            raise ValueError(f"Observation {observation_id} not found")
        with self.repo.transaction():
            if obs.raw_reference is not None:
                obs.redaction = {
                    "at": at.isoformat(),
                    "reason": reason,
                    "actor": actor,
                    "raw_digest": canonical_hash(obs.raw_reference),
                }
                obs.raw_reference = None
                self.repo.save_observation(obs)
            for ev in self.repo.list_evidence():
                if ev.source_reference == observation_id:
                    ev.retention_policy = "redacted"
                    self.repo.save_evidence(ev)
        return obs
