"""Evidence policy: how a new claim relates to current belief (spec §2.8, §9).

``EvidencePolicy.decide`` is a pure function of (current versions, open conflict, new
claim, evidence). It never consults model confidence and never "picks a winner"
between disagreeing sources — only explicit verification evidence resolves a
conflict, or a channel replacing its *own* earlier claim. The full decision table is
ADR-011 in DECISIONS.md.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from backend.app.domain.models import Conflict, Entity, Evidence, StateVersion
from backend.app.domain.types import ClaimDecision, EpistemicStatus, EvidenceChannel, FreshnessState, SourceType
from backend.app.services.freshness import WEAK_SUPPORT, FreshnessPolicyRegistry

WEAK_STRENGTH = WEAK_SUPPORT
STRONG_STRENGTH = 0.7
VERIFIED_AUTHORITY = 0.9
SIMULTANEITY_SECONDS = 30.0

SOURCE_ALIASES: Dict[str, SourceType] = {
    "camera": SourceType.VISUAL_OBSERVATION,
    "webcam": SourceType.VISUAL_OBSERVATION,
    "phone_camera": SourceType.VISUAL_OBSERVATION,
    "overhead_camera": SourceType.VISUAL_OBSERVATION,
    "visual_label": SourceType.VISUAL_OBSERVATION,
    "sensor": SourceType.SENSOR_READING,
    "microphone": SourceType.AUDIO_OBSERVATION,
    "digital_registry": SourceType.EXTERNAL_RECORD,
    "authoritative_record": SourceType.EXTERNAL_RECORD,
    "asset_registry": SourceType.EXTERNAL_RECORD,
    "manual_verification": SourceType.MANUAL_VERIFICATION,
    "manual_registration": SourceType.USER_STATEMENT,
    "user": SourceType.USER_STATEMENT,
    "user_statement": SourceType.USER_STATEMENT,
    "procedure": SourceType.PROCEDURE,
    "system": SourceType.SYSTEM_EVENT,
    "inference": SourceType.INFERENCE,
    "counterfactual": SourceType.SIMULATION,
}
_SOURCE_HINTS = (
    ("verif", SourceType.MANUAL_VERIFICATION),
    ("registry", SourceType.EXTERNAL_RECORD),
    ("record", SourceType.EXTERNAL_RECORD),
    ("cam", SourceType.VISUAL_OBSERVATION),
    ("vision", SourceType.VISUAL_OBSERVATION),
    ("sensor", SourceType.SENSOR_READING),
    ("mic", SourceType.AUDIO_OBSERVATION),
    ("user", SourceType.USER_STATEMENT),
    ("procedure", SourceType.PROCEDURE),
)

CHANNEL_OF: Dict[SourceType, EvidenceChannel] = {
    SourceType.VISUAL_OBSERVATION: EvidenceChannel.DIRECT,
    SourceType.AUDIO_OBSERVATION: EvidenceChannel.DIRECT,
    SourceType.SENSOR_READING: EvidenceChannel.DIRECT,
    SourceType.MANUAL_VERIFICATION: EvidenceChannel.DIRECT,
    SourceType.OTHER: EvidenceChannel.DIRECT,
    SourceType.EXTERNAL_RECORD: EvidenceChannel.RECORD,
    SourceType.PROCEDURE: EvidenceChannel.RECORD,
    SourceType.SYSTEM_EVENT: EvidenceChannel.RECORD,
    SourceType.TOOL_OUTPUT: EvidenceChannel.RECORD,
    SourceType.USER_STATEMENT: EvidenceChannel.TESTIMONY,
    SourceType.INFERENCE: EvidenceChannel.INFERENCE,
    SourceType.SIMULATION: EvidenceChannel.DIRECT,  # a premise stands in for direct ground truth
}


def infer_source_type(source: str) -> SourceType:
    key = source.strip().lower()
    if key in SOURCE_ALIASES:
        return SOURCE_ALIASES[key]
    try:
        return SourceType(source.upper())
    except ValueError:
        pass
    for hint, source_type in _SOURCE_HINTS:
        if hint in key:
            return source_type
    return SourceType.OTHER


def channel(source_type: SourceType) -> EvidenceChannel:
    return CHANNEL_OF[source_type]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def strength(quality: float, authority: float) -> float:
    return _clamp(quality) * _clamp(authority)


def grade(source_type: SourceType, quality: float, authority: float) -> EpistemicStatus:
    """Evidential grade of a single piece of evidence (rule 2.3)."""
    if source_type == SourceType.INFERENCE:
        return EpistemicStatus.INFERRED
    if strength(quality, authority) < WEAK_STRENGTH:
        return EpistemicStatus.UNKNOWN
    if (
        source_type in (SourceType.MANUAL_VERIFICATION, SourceType.EXTERNAL_RECORD, SourceType.SIMULATION)
        and authority >= VERIFIED_AUTHORITY
        and quality >= 0.5
    ):
        return EpistemicStatus.VERIFIED
    return EpistemicStatus.OBSERVED


def is_verification(source_type: SourceType, quality: float, authority: float) -> bool:
    # A counterfactual premise is, by definition, how the sandbox world *is*.
    return (
        source_type in (SourceType.MANUAL_VERIFICATION, SourceType.SIMULATION)
        and authority >= VERIFIED_AUTHORITY
        and quality >= 0.5
    )


def canonical_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def evidence_integrity(evidence: Evidence, subject: Any) -> str:
    """Integrity reference over the evidence record and the claim payload it supports."""
    return canonical_hash(
        {
            "source_type": evidence.source_type.value,
            "source": evidence.source,
            "source_reference": evidence.source_reference,
            "timestamp": evidence.timestamp.isoformat(),
            "quality": evidence.quality,
            "authority": evidence.authority,
            "provenance": evidence.provenance,
            "content": evidence.content,
            "subject": subject,
        }
    )


@dataclass
class PolicyDecision:
    decision: ClaimDecision
    reason: str
    matching: Optional[StateVersion] = None
    superseded: List[StateVersion] = field(default_factory=list)


@dataclass
class Claim:
    """A single attribute claim derived from one piece of evidence."""

    value: Any
    evidence: Evidence
    quality: float  # evidence quality × detection confidence

    @property
    def at(self) -> datetime:
        return self.evidence.timestamp

    @property
    def strength(self) -> float:
        return strength(self.quality, self.evidence.authority)

    @property
    def grade(self) -> EpistemicStatus:
        return grade(self.evidence.source_type, self.quality, self.evidence.authority)

    @property
    def channel(self) -> EvidenceChannel:
        return channel(self.evidence.source_type)

    @property
    def is_verification(self) -> bool:
        return is_verification(self.evidence.source_type, self.quality, self.evidence.authority)


class EvidencePolicy:
    def __init__(self, freshness: FreshnessPolicyRegistry, detect_contradictions: bool = True):
        self.freshness = freshness
        # `detect_contradictions=False` is the last-writer-wins ablation baseline (spec §28).
        self.detect_contradictions = detect_contradictions

    def unsupported(self, version: StateVersion, entity: Optional[Entity], at: datetime) -> bool:
        if version.status == EpistemicStatus.UNKNOWN:
            return True
        policy = self.freshness.policy_for(entity, version.attribute)
        state = self.freshness.assess(version, policy, at).state
        return state in (FreshnessState.STALE, FreshnessState.INVALIDATED)

    @staticmethod
    def channels(version: StateVersion) -> Set[EvidenceChannel]:
        return {channel(s.source_type) for s in version.support if s.strength >= WEAK_STRENGTH}

    def decide(
        self,
        entity: Optional[Entity],
        attribute: str,
        claim: Claim,
        current: List[StateVersion],
        conflict: Optional[Conflict],
    ) -> PolicyDecision:
        D = ClaimDecision
        if not current:
            return PolicyDecision(D.NEW, "first claim for attribute")

        latest_support = max(self.freshness.support_time(c) or c.valid_from for c in current)
        if claim.at < latest_support:
            return PolicyDecision(D.UNCONFIRMED, "out-of-order evidence older than current support")

        matching = next((c for c in current if c.value == claim.value), None)

        if claim.is_verification:
            if conflict is not None:
                return PolicyDecision(D.RESOLVE, "verification evidence resolves conflict", matching=matching)
            if matching is not None:
                return PolicyDecision(D.CORROBORATE, "verified", matching=matching)
            return PolicyDecision(D.SUPERSEDE, "verification evidence supersedes")

        if claim.strength < WEAK_STRENGTH:
            if matching is not None:
                return PolicyDecision(D.CORROBORATE, "weak agreeing evidence (no refresh)", matching=matching)
            return PolicyDecision(D.UNCONFIRMED, f"insufficient evidence strength ({claim.strength:.2f})")

        if claim.grade == EpistemicStatus.INFERRED and matching is None:
            return PolicyDecision(D.UNCONFIRMED, "an inference cannot override an observed claim")

        all_unsupported = all(self.unsupported(c, entity, claim.at) for c in current)
        if conflict is None and matching is not None:
            return PolicyDecision(D.CORROBORATE, "same value", matching=matching)
        if all_unsupported:
            return PolicyDecision(D.SUPERSEDE, "previous claims stale, invalidated or unknown")

        if not self.detect_contradictions:
            return PolicyDecision(D.SUPERSEDE, "last writer wins (contradiction detection disabled)")

        if conflict is not None:
            superseded = [
                c for c in current if c.value != claim.value and self.channels(c) <= {claim.channel}
            ]
            return PolicyDecision(
                D.CONFLICT_UPDATE, "claim during open conflict", matching=matching, superseded=superseded
            )

        cur = current[0]
        strong = [s for s in cur.support if s.strength >= WEAK_STRENGTH]
        if strong and max(strong, key=lambda s: s.at).source == claim.evidence.source:
            return PolicyDecision(D.SUPERSEDE, "temporal succession from the same source")

        if self.channels(cur) <= {claim.channel}:
            rivals = [s for s in strong if s.source != claim.evidence.source]
            if rivals and (claim.at - max(s.at for s in rivals)).total_seconds() <= SIMULTANEITY_SECONDS:
                return PolicyDecision(D.CONTRADICT, "simultaneous disagreement between sources")
            return PolicyDecision(D.SUPERSEDE, "temporal succession within channel")

        others = [s.at for s in strong if channel(s.source_type) != claim.channel]
        window = self.freshness.contradiction_window(self.freshness.policy_for(entity, attribute))
        gap = (claim.at - max(others)).total_seconds() if others else 0.0
        if window is None or gap <= window:
            return PolicyDecision(D.CONTRADICT, f"cross-channel disagreement {gap:.0f}s apart")
        if claim.strength < max(s.strength for s in strong):
            return PolicyDecision(D.UNCONFIRMED, "weaker cross-channel claim; verification needed")
        return PolicyDecision(D.SUPERSEDE, "cross-channel succession outside contradiction window")
