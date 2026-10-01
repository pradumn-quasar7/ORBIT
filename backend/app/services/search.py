"""Negative / search memory and coverage-validated absence (spec §2.7, §7, §12).

"Not observed" never becomes "removed" on its own. A target is CONFIRMED_ABSENT only
when (1) it was explicitly searched for, (2) its last known location lies inside the
searched region, and (3) the search policy validates coverage: enough of the region
inspected, adequate visibility, and a confident search. Everything else is
inconclusive and leaves belief unchanged.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backend.app.domain.models import Observation, SearchCoverage
from backend.app.domain.types import SearchResult, SourceType
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.evidence_policy import infer_source_type
from backend.app.services.world_state_engine import EntityNotFoundError, WorldStateEngine

POOR_LIGHTING = frozenset({"poor", "dark", "very_low"})


@dataclass
class SearchPolicy:
    min_coverage: float = 0.9
    min_confidence: float = 0.7
    max_occlusion: float = 0.2
    # enabled=False is the "unobserved ⇒ removed" ablation baseline (spec §28):
    # any missing target is declared absent regardless of coverage.
    enabled: bool = True
    name: str = "coverage-v1"

    def validates_absence(self, coverage: SearchCoverage) -> Tuple[bool, str]:
        if not self.enabled:
            return True, "coverage policy disabled (ablation)"
        if coverage.coverage_fraction < self.min_coverage:
            return False, f"coverage {coverage.coverage_fraction:.0%} < {self.min_coverage:.0%}"
        if coverage.confidence < self.min_confidence:
            return False, f"search confidence {coverage.confidence:.2f} < {self.min_confidence:.2f}"
        vis = coverage.visibility_conditions
        if str(vis.get("lighting", "")).lower() in POOR_LIGHTING:
            return False, f"lighting {vis['lighting']}"
        if float(vis.get("occlusion", 0.0)) > self.max_occlusion:
            return False, f"occlusion {vis['occlusion']} > {self.max_occlusion}"
        return True, "coverage validated"


class SearchService:
    def __init__(self, repository: Repository, engine: WorldStateEngine, policy: Optional[SearchPolicy] = None):
        self.repo = repository
        self.engine = engine
        self.policy = policy or SearchPolicy()

    def _last_location(self, entity_id: str, at: datetime) -> Optional[str]:
        a = self.engine.claims.assess_attribute(entity_id, LOCATION, at)
        return a.last_known_value if a.has_current_claim else None

    def _targets(self, region: str, searched_for: List[str], at: datetime) -> List[str]:
        targets: List[str] = []
        for item in searched_for:
            if item.startswith("type:"):
                wanted = item.split(":", 1)[1]
                for e in self.repo.list_entities():
                    loc = self._last_location(e.id, at)
                    if e.type == wanted and e.created_at <= at and self.engine.anchors.is_within(loc, region):
                        targets.append(e.id)
            else:
                if self.repo.get_entity(item) is None:
                    raise EntityNotFoundError(f"Entity {item} not found")
                targets.append(item)
        return list(dict.fromkeys(targets))

    def record_search(
        self,
        region: str,
        at: datetime,
        searched_for: List[str],
        observation: Optional[Observation] = None,
        coverage_fraction: float = 1.0,
        visibility_conditions: Optional[Dict] = None,
        confidence: float = 1.0,
        source: str = "camera",
        session_id: Optional[str] = None,
    ) -> SearchCoverage:
        with self.repo.transaction():
            seen = set()
            if observation is not None:
                observation.session_id = observation.session_id or session_id
                self.engine.record_observation(observation)
                stored = self.repo.get_observation(observation.id)
                seen = {r.entity_id for r in stored.resolutions} if stored else set()

            targets = self._targets(region, searched_for, at)
            coverage = SearchCoverage(
                region=region,
                timestamp=at,
                source=source,
                session_id=session_id,
                visibility_conditions=visibility_conditions or {},
                coverage_fraction=coverage_fraction,
                searched_for=list(searched_for),
                found=[t for t in targets if t in seen],
                confidence=confidence,
                policy=self.policy.name,
            )
            validated, why = self.policy.validates_absence(coverage)
            evidence = self.engine.record_evidence(
                source_type=infer_source_type(source) if source else SourceType.VISUAL_OBSERVATION,
                source=source,
                source_reference=coverage.id,
                timestamp=at,
                quality=confidence,
                content={
                    "kind": "search",
                    "region": region,
                    "searched_for": list(searched_for),
                    "coverage_fraction": coverage_fraction,
                    "visibility": visibility_conditions or {},
                    "policy": self.policy.name,
                    "validated": validated,
                    "reason": why,
                },
            )
            coverage.evidence_refs = [evidence.id]
            for target in targets:
                if target in seen:
                    continue
                inside = self.engine.anchors.is_within(self._last_location(target, at), region)
                if validated and (inside or not self.policy.enabled):
                    coverage.confirmed_absent.append(target)
                    entity = self.repo.get_entity(target)
                    self.engine.belief.apply_absence(entity, region, at, evidence.id, coverage.id)
                    entity.updated_at = max(entity.updated_at, at)
                    self.engine.belief.materialize(entity, entity.updated_at)
                    self.repo.save_entity(entity)
                else:
                    coverage.inconclusive.append(target)
            if coverage.confirmed_absent:
                coverage.result = SearchResult.NOT_FOUND_IN_COVERAGE
            elif coverage.inconclusive:
                coverage.result = SearchResult.INCONCLUSIVE
            return self.repo.save_search_coverage(coverage)
