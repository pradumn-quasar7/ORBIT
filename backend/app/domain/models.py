from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

from backend.app.core.time import UTCDateTime
from backend.app.domain.types import EpistemicStatus, EventType, VolatilityClass

def generate_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

class Geometry(BaseModel):
    position: Optional[Dict[str, float]] = None
    orientation: Optional[Dict[str, float]] = None
    bounds: Optional[Dict[str, float]] = None

class FreshnessPolicy(BaseModel):
    volatility: VolatilityClass = VolatilityClass.MEDIUM
    ttl_seconds: Optional[float] = 3600.0
    invalidation_triggers: List[str] = Field(default_factory=list)

class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("evi"))
    source_type: str  # visual, audio, user_statement, digital_registry, system_event
    source_reference: str
    timestamp: UTCDateTime
    quality: float = 1.0  # 0.0 to 1.0
    authority: float = 1.0  # 0.0 to 1.0 (authoritative digital registry = 1.0, noisy webcam = 0.5)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    retention_policy: Optional[str] = "indefinite"
    integrity_reference: Optional[str] = None

class StateVersion(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("sv"))
    entity_id: str
    attribute: str
    value: Any
    status: EpistemicStatus = EpistemicStatus.OBSERVED
    valid_from: UTCDateTime
    valid_to: Optional[UTCDateTime] = None
    supported_by: List[str] = Field(default_factory=list)  # Evidence IDs
    invalidation_reason: Optional[str] = None

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
    current_state: Dict[str, Any] = Field(default_factory=dict)
    attribute_statuses: Dict[str, EpistemicStatus] = Field(default_factory=dict)
    status: EpistemicStatus = EpistemicStatus.OBSERVED
    observed_at: UTCDateTime
    freshness_policies: Dict[str, FreshnessPolicy] = Field(default_factory=dict)
    evidence_refs: List[str] = Field(default_factory=list)
    history_refs: List[str] = Field(default_factory=list)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    created_at: UTCDateTime
    updated_at: UTCDateTime

class ObservedEntity(BaseModel):
    candidate_entity_id: Optional[str] = None
    type: str
    name: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    location: Optional[str] = None
    anchor: Optional[str] = None
    geometry: Optional[Geometry] = None
    confidence: float = 1.0

class Observation(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("obs"))
    timestamp: UTCDateTime
    source: str
    session_id: Optional[str] = None
    raw_reference: Optional[str] = None
    observed_entities: List[ObservedEntity] = Field(default_factory=list)
    spatial_context: Dict[str, Any] = Field(default_factory=dict)
    quality: float = 1.0
    authority: float = 1.0
    provenance: Dict[str, Any] = Field(default_factory=dict)

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
