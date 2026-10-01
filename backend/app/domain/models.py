import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from backend.app.core.time import UTCDateTime
from backend.app.domain.types import (
    ClaimDisposition,
    EpistemicStatus,
    EventType,
    FreshnessState,
    IdentityStatus,
    ResolutionMethod,
    SourceType,
    VolatilityClass,
)


def generate_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Geometry(BaseModel):
    position: Optional[Dict[str, float]] = None
    orientation: Optional[Dict[str, float]] = None
    bounds: Optional[Dict[str, float]] = None


class FreshnessPolicy(BaseModel):
    volatility: VolatilityClass = VolatilityClass.MEDIUM
    ttl_seconds: Optional[float] = 3600.0  # None = does not expire with age
    # Cross-channel claims closer together than this contradict each other rather than
    # describing a change. None = use the volatility default.
    contradiction_window_seconds: Optional[float] = None
    # Attributes of the same entity whose change invalidates this attribute.
    invalidation_triggers: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------- evidence
class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("evi"))
    source_type: SourceType
    source: str = "unknown"  # device / system / person that produced it
    source_reference: str  # e.g. observation id, registry record id
    timestamp: UTCDateTime
    quality: float = 1.0  # 0..1 signal quality (blur, occlusion, OCR confidence)
    authority: float = 1.0  # 0..1 trust in the source for this kind of claim
    provenance: Dict[str, Any] = Field(default_factory=dict)  # actor, provider/model ids, ...
    content: Dict[str, Any] = Field(default_factory=dict)  # compact structured claim payload
    retention_policy: Optional[str] = "indefinite"
    integrity_reference: Optional[str] = None  # sha256 over the canonical record


class SupportRef(BaseModel):
    """One piece of evidence supporting a state version (denormalised for policy checks)."""

    evidence_id: str
    at: UTCDateTime
    source: str
    source_type: SourceType
    strength: float


class StateVersion(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("sv"))
    entity_id: str
    attribute: str
    value: Any
    # Evidential grade when asserted (OBSERVED / VERIFIED / INFERRED / UNKNOWN). STALE and
    # CONTRADICTED are *derived* at read time from freshness, invalidation and conflicts.
    status: EpistemicStatus = EpistemicStatus.OBSERVED
    disposition: ClaimDisposition = ClaimDisposition.ACCEPTED
    valid_from: UTCDateTime
    valid_to: Optional[UTCDateTime] = None
    supported_by: List[str] = Field(default_factory=list)  # Evidence IDs
    support: List[SupportRef] = Field(default_factory=list)
    last_supported_at: Optional[UTCDateTime] = None
    last_validated_at: Optional[UTCDateTime] = None
    volatility_class: Optional[VolatilityClass] = None
    invalidated_at: Optional[UTCDateTime] = None
    invalidation_reason: Optional[str] = None

    @model_validator(mode="after")
    def _default_support_time(self) -> "StateVersion":
        if self.last_supported_at is None:
            self.last_supported_at = self.valid_from
        return self


class Conflict(BaseModel):
    """An explicit, retained contradiction between current claims (rule 2.8)."""

    id: str = Field(default_factory=lambda: generate_id("conf"))
    entity_id: str
    attribute: str
    version_ids: List[str] = Field(default_factory=list)
    opened_at: UTCDateTime
    resolved_at: Optional[UTCDateTime] = None
    resolution_version_id: Optional[str] = None
    resolution_evidence: Optional[str] = None
    resolution_reason: Optional[str] = None


class ClaimDependency(BaseModel):
    """`dependent` is only valid while `depends_on` is unchanged (spec §10)."""

    id: str = Field(default_factory=lambda: generate_id("dep"))
    dependent_entity_id: str
    dependent_attribute: str
    depends_on_entity_id: str
    depends_on_attribute: str
    reason: Optional[str] = None
    created_at: UTCDateTime


# ---------------------------------------------------------------- world state
class Event(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("evt"))
    timestamp: UTCDateTime
    event_type: EventType
    entity_id: Optional[str] = None
    relation_id: Optional[str] = None
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    evidence_refs: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class Relation(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("rel"))
    source_entity: str
    relation_type: str  # on, connected_to, inside, adjacent
    target_entity: str
    valid_from: UTCDateTime
    valid_to: Optional[UTCDateTime] = None
    status: EpistemicStatus = EpistemicStatus.OBSERVED
    evidence_refs: List[str] = Field(default_factory=list)


class Entity(BaseModel):
    id: str
    type: str
    name: Optional[str] = None
    canonical_attributes: Dict[str, Any] = Field(default_factory=dict)
    geometry: Optional[Geometry] = None
    anchor: Optional[str] = None
    # Materialised view of the last accepted value per attribute. During a conflict the
    # pre-conflict value stays here with status CONTRADICTED; claims API lists all sides.
    current_state: Dict[str, Any] = Field(default_factory=dict)
    attribute_statuses: Dict[str, EpistemicStatus] = Field(default_factory=dict)
    status: EpistemicStatus = EpistemicStatus.OBSERVED
    observed_at: UTCDateTime
    freshness_policies: Dict[str, FreshnessPolicy] = Field(default_factory=dict)  # overrides
    evidence_refs: List[str] = Field(default_factory=list)
    history_refs: List[str] = Field(default_factory=list)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    identity_status: IdentityStatus = IdentityStatus.ESTABLISHED
    identity_candidates: List[str] = Field(default_factory=list)
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ObservedRelation(BaseModel):
    relation_type: str  # on, inside, connected_to, adjacent_to, ...
    target: str  # entity id (or candidate id within the same observation) or anchor id
    present: bool = True  # False = relation observed NOT to hold (e.g. cable seen unplugged)


class ObservedEntity(BaseModel):
    candidate_entity_id: Optional[str] = None
    type: str
    name: Optional[str] = None
    # Strong identity markers (serial_number, asset_tag, ...). A mismatch means a
    # different physical object, never an attribute change.
    identifiers: Dict[str, str] = Field(default_factory=dict)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    relations: List[ObservedRelation] = Field(default_factory=list)
    location: Optional[str] = None
    anchor: Optional[str] = None
    geometry: Optional[Geometry] = None
    confidence: float = 1.0  # detector confidence; multiplies observation quality


class EntityResolution(BaseModel):
    """Audit record of which entity a detection was assigned to, and why."""

    observed_index: int
    candidate_entity_id: Optional[str] = None
    entity_id: str
    method: ResolutionMethod
    confidence: float
    candidates: List[str] = Field(default_factory=list)
    reason: str = ""


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("obs"))
    timestamp: UTCDateTime
    source: str
    source_type: Optional[SourceType] = None  # inferred from `source` when omitted
    session_id: Optional[str] = None
    raw_reference: Optional[str] = None
    observed_entities: List[ObservedEntity] = Field(default_factory=list)
    spatial_context: Dict[str, Any] = Field(default_factory=dict)
    quality: float = 1.0
    authority: float = 1.0
    provenance: Dict[str, Any] = Field(default_factory=dict)
    resolutions: List[EntityResolution] = Field(default_factory=list)  # filled by the engine


class Anchor(BaseModel):
    """A spatial reference frame (room, bench, shelf region). Anchors form a tree."""

    id: str
    name: Optional[str] = None
    anchor_type: str = "region"  # room, surface, region, fixture
    parent_id: Optional[str] = None
    frame: Dict[str, Any] = Field(default_factory=dict)  # coordinate-frame description
    created_at: UTCDateTime


class Session(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("ses"))
    label: Optional[str] = None
    actor: Optional[str] = None
    started_at: UTCDateTime
    ended_at: Optional[UTCDateTime] = None
    last_observation_at: Optional[UTCDateTime] = None


# ----------------------------------------------------------------------- tasks
class TaskStep(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("step"))
    task_id: str
    step_order: int
    description: str
    status: EpistemicStatus = EpistemicStatus.UNKNOWN  # VERIFIED, BLOCKED, etc.
    dependencies: List[str] = Field(default_factory=list)
    preconditions: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None
    completed_at: Optional[UTCDateTime] = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("task"))
    goal: str
    status: str = "IN_PROGRESS"  # PENDING, IN_PROGRESS, COMPLETED, BLOCKED
    steps: List[TaskStep] = Field(default_factory=list)
    created_at: UTCDateTime
    updated_at: UTCDateTime


# ------------------------------------------------------------------- world diff
class WorldChange(BaseModel):
    change_type: EventType
    entity_id: str
    attribute: Optional[str] = None
    before: Any = None
    after: Any = None
    evidence_refs: List[str] = Field(default_factory=list)
    timestamp: UTCDateTime


class WorldDiff(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("diff"))
    baseline_timestamp: Optional[UTCDateTime] = None
    target_timestamp: UTCDateTime
    changes: List[WorldChange] = Field(default_factory=list)
    created_at: UTCDateTime


class SearchCoverage(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("cov"))
    region: str
    timestamp: UTCDateTime
    visibility_conditions: Dict[str, Any] = Field(default_factory=dict)
    searched_for: List[str] = Field(default_factory=list)
    result: str  # FOUND, NOT_FOUND_IN_COVERAGE
    confidence: float = 1.0
    evidence_refs: List[str] = Field(default_factory=list)


# ------------------------------------------------------- read-side assessments
class FreshnessAssessment(BaseModel):
    state: FreshnessState
    as_of: UTCDateTime
    last_supported_at: Optional[UTCDateTime] = None
    age_seconds: Optional[float] = None
    ttl_seconds: Optional[float] = None
    expires_at: Optional[UTCDateTime] = None
    volatility: Optional[VolatilityClass] = None
    reason: Optional[str] = None


class ConflictSide(BaseModel):
    version_id: str
    value: Any
    status: EpistemicStatus
    sources: List[str]
    evidence_refs: List[str]
    last_supported_at: Optional[UTCDateTime] = None


class ClaimAssessment(BaseModel):
    """Can ORBIT currently assert `entity.attribute = value`? (the evidence gate)"""

    entity_id: str
    attribute: str
    as_of: UTCDateTime
    value: Any = None  # None when unknown or contradicted
    last_known_value: Any = None  # the most recent accepted value, even if not supportable
    status: EpistemicStatus
    supportable: bool
    freshness: Optional[FreshnessAssessment] = None
    evidence_refs: List[str] = Field(default_factory=list)
    conflict_id: Optional[str] = None
    conflicts: List[ConflictSide] = Field(default_factory=list)
    reason: str = ""
    recommended_action: Optional[str] = None  # "observe" | "verify" | None


class EntityAssessment(BaseModel):
    entity_id: str
    as_of: UTCDateTime
    status: EpistemicStatus
    identity_status: IdentityStatus
    attributes: Dict[str, ClaimAssessment]
