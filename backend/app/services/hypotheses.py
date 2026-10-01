"""Causal hypothesis memory (spec §2.9): causality is conservative.

"Firmware changed, then the motor failed" may be stored as a *hypothesis* (INFERRED).
It becomes SUPPORTED or REFUTED only through causal-test evidence from a qualifying
source (diagnostic tool output or hands-on verification). Co-occurrence, more
observations, or anyone's confident statement are retained as context but never
change the hypothesis status.
"""
from datetime import datetime
from typing import List, Optional, Tuple

from backend.app.domain.models import CausalHypothesis
from backend.app.domain.types import EpistemicStatus, HypothesisStatus, SourceType
from backend.app.repositories.base import Repository
from backend.app.services.evidence_policy import VERIFIED_AUTHORITY, infer_source_type
from backend.app.services.world_state_engine import WorldStateEngine

CAUSAL_EVIDENCE_SOURCES = frozenset({SourceType.TOOL_OUTPUT, SourceType.MANUAL_VERIFICATION})


def qualifies_as_causal_test(kind: str, source_type: SourceType, quality: float, authority: float) -> bool:
    return (
        kind == "causal_test"
        and source_type in CAUSAL_EVIDENCE_SOURCES
        and authority >= VERIFIED_AUTHORITY
        and quality >= 0.5
    )


class CausalOrderError(ValueError):
    pass


class HypothesisNotFoundError(ValueError):
    pass


class HypothesisService:
    def __init__(self, repository: Repository, engine: WorldStateEngine):
        self.repo = repository
        self.engine = engine

    def propose(
        self,
        statement: str,
        at: datetime,
        cause_event_id: Optional[str] = None,
        effect_event_id: Optional[str] = None,
        entity_ids: Optional[List[str]] = None,
        created_by: Optional[str] = None,
    ) -> CausalHypothesis:
        events = {e.id: e for e in self.repo.list_events()}
        for ref in (cause_event_id, effect_event_id):
            if ref is not None and ref not in events:
                raise ValueError(f"event {ref} not found")
        if cause_event_id and effect_event_id and events[cause_event_id].timestamp > events[effect_event_id].timestamp:
            raise CausalOrderError("a cause cannot happen after its effect")
        involved = set(entity_ids or [])
        for ref in (cause_event_id, effect_event_id):
            if ref and events[ref].entity_id:
                involved.add(events[ref].entity_id)
        context = [r for ref in (cause_event_id, effect_event_id) if ref for r in events[ref].evidence_refs]
        hypothesis = CausalHypothesis(
            statement=statement,
            cause_event_id=cause_event_id,
            effect_event_id=effect_event_id,
            entity_ids=sorted(involved),
            evidence_refs=context,
            created_by=created_by,
            created_at=at,
            updated_at=at,
        )
        return self.repo.save_hypothesis(hypothesis)

    def add_evidence(
        self,
        hypothesis_id: str,
        at: datetime,
        source: str,
        supports: bool,
        description: str,
        kind: str = "observation",  # "causal_test" for a controlled test / diagnostic
        source_type: Optional[SourceType] = None,
        quality: float = 1.0,
        authority: float = 1.0,
    ) -> Tuple[CausalHypothesis, bool, str]:
        """Attach evidence. Returns (hypothesis, status_changed, reason)."""
        hypothesis = self.repo.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise HypothesisNotFoundError(f"Hypothesis {hypothesis_id} not found")
        stype = source_type or infer_source_type(source)
        with self.repo.transaction():
            evidence = self.engine.record_evidence(
                source_type=stype,
                source=source,
                source_reference=f"hypothesis:{hypothesis_id}",
                timestamp=at,
                quality=quality,
                authority=authority,
                content={"kind": kind, "hypothesis_id": hypothesis_id, "supports": supports, "description": description},
            )
            hypothesis.evidence_refs.append(evidence.id)
            hypothesis.updated_at = max(hypothesis.updated_at, at)
            qualifies = qualifies_as_causal_test(kind, stype, quality, authority)
            if qualifies:
                hypothesis.status = HypothesisStatus.SUPPORTED if supports else HypothesisStatus.REFUTED
                hypothesis.epistemic_status = EpistemicStatus.VERIFIED
                reason = "causal test evidence"
            else:
                reason = "retained as context; only a qualifying causal test changes a hypothesis"
            self.repo.save_hypothesis(hypothesis)
        return hypothesis, qualifies, reason

    def for_entity(self, entity_id: str) -> List[CausalHypothesis]:
        return [h for h in self.repo.list_hypotheses() if entity_id in h.entity_ids]
