import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.time import UTCDateTime
from backend.app.domain.types import (
    AbsenceStatus,
    ActionStatus,
    ClaimDisposition,
    ConditionState,
    EpistemicStatus,
    EventType,
    FreshnessState,
    HypothesisStatus,
    IdentityStatus,
    ObservationActionType,
    OutcomeResult,
    PrincipalKind,
    QueryKind,
    ResolutionMethod,
    RiskLevel,
    Scope,
    SearchResult,
    SourceType,
    StepStatus,
    TaskStatus,
    VariationKind,
    VolatilityClass,
)


def generate_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class StrictInput(BaseModel):
    """Inputs that become evidence reject unknown fields: a misspelt field must fail
    loudly rather than silently drop information (e.g. `quality` on a detection)."""

    model_config = ConfigDict(extra="forbid")


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
    task_id: Optional[str] = None
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


class ObservedRelation(StrictInput):
    relation_type: str  # on, inside, connected_to, adjacent_to, ...
    target: str  # entity id (or candidate id within the same observation) or anchor id
    present: bool = True  # False = relation observed NOT to hold (e.g. cable seen unplugged)


class ObservedEntity(StrictInput):
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


class Observation(StrictInput):
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
    redaction: Optional[Dict[str, Any]] = None  # set when the raw reference is removed (spec §17)


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
class StateCondition(StrictInput):
    """A claim about the world a step requires (precondition) or establishes
    (postcondition), e.g. ``valve_v2.state == "closed"`` at least OBSERVED."""

    entity_id: str
    attribute: str
    expected: Any = None
    operator: str = "eq"  # eq | ne | in | exists
    min_status: EpistemicStatus = EpistemicStatus.OBSERVED  # OBSERVED or VERIFIED
    description: Optional[str] = None


class Interruption(BaseModel):
    at: UTCDateTime
    reason: Optional[str] = None
    actor: Optional[str] = None
    resumed_at: Optional[UTCDateTime] = None
    resumed_by: Optional[str] = None


class TaskStep(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("step"))
    task_id: str
    step_order: int
    description: str
    status: StepStatus = StepStatus.PENDING
    completion_status: EpistemicStatus = EpistemicStatus.UNKNOWN
    risk: RiskLevel = RiskLevel.MEDIUM
    dependencies: List[str] = Field(default_factory=list)  # step ids that must be COMPLETED first
    preconditions: List[StateCondition] = Field(default_factory=list)
    postconditions: List[StateCondition] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None
    started_at: Optional[UTCDateTime] = None
    completed_at: Optional[UTCDateTime] = None
    completed_by: Optional[str] = None
    invalidated_reason: Optional[str] = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("task"))
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    steps: List[TaskStep] = Field(default_factory=list)
    assigned_to: Optional[str] = None
    procedure_entity_id: Optional[str] = None  # entity whose `procedure_revision` governs the task
    procedure_revision: Optional[str] = None  # revision the task was planned against
    interruptions: List[Interruption] = Field(default_factory=list)
    last_verified_at: Optional[UTCDateTime] = None  # latest instant the task state was verified
    created_at: UTCDateTime
    updated_at: UTCDateTime


class CausalHypothesis(BaseModel):
    """Causal hypothesis memory (spec §2.9, §7): never a fact without causal evidence."""

    id: str = Field(default_factory=lambda: generate_id("hyp"))
    statement: str
    cause_event_id: Optional[str] = None
    effect_event_id: Optional[str] = None
    entity_ids: List[str] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.HYPOTHESIS
    epistemic_status: EpistemicStatus = EpistemicStatus.INFERRED
    evidence_refs: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


# ------------------------------------------------------------------- world diff
class WorldChange(BaseModel):
    change_type: EventType
    entity_id: str  # entity id, or task id for TASK_PROGRESS_CHANGED
    attribute: Optional[str] = None  # attribute, relation type, or step id
    before: Any = None
    after: Any = None
    status: Optional[EpistemicStatus] = None  # status of the "after" claim at the target time
    absence: Optional[AbsenceStatus] = None
    related_entity_ids: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    timestamp: UTCDateTime


class DiffUncertainty(BaseModel):
    """Not a change: a claim whose current value ORBIT cannot vouch for at the target time."""

    entity_id: str
    attribute: str
    status: EpistemicStatus
    reason: str
    last_known_value: Any = None


class WorldDiff(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("diff"))
    mode: str = "snapshot"  # snapshot | event_log (ablation baseline)
    baseline_timestamp: Optional[UTCDateTime] = None
    target_timestamp: UTCDateTime
    changes: List[WorldChange] = Field(default_factory=list)
    uncertain: List[DiffUncertainty] = Field(default_factory=list)
    created_at: UTCDateTime


class SearchCoverage(BaseModel):
    """Negative/search memory (spec §7): what was looked for, where, how well."""

    id: str = Field(default_factory=lambda: generate_id("cov"))
    region: str
    timestamp: UTCDateTime
    source: str = "unknown"
    session_id: Optional[str] = None
    visibility_conditions: Dict[str, Any] = Field(default_factory=dict)  # lighting, occlusion, ...
    coverage_fraction: float = 1.0  # share of the region actually inspected
    searched_for: List[str] = Field(default_factory=list)  # entity ids or "type:<type>"
    found: List[str] = Field(default_factory=list)
    confirmed_absent: List[str] = Field(default_factory=list)
    inconclusive: List[str] = Field(default_factory=list)
    result: SearchResult = SearchResult.ALL_FOUND
    policy: str = "coverage-v1"
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
    has_current_claim: bool = True  # False when no version is valid at as_of
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


# ----------------------------------------------------------- memory read models
class EntitySnapshot(BaseModel):
    entity_id: str
    type: str
    name: Optional[str] = None
    identity_status: IdentityStatus
    status: EpistemicStatus
    attributes: Dict[str, ClaimAssessment]
    relations: List[Relation] = Field(default_factory=list)


class WorldSnapshot(BaseModel):
    """B_t: ORBIT's belief about the world at ``as_of``, each claim with its evidence gate."""

    as_of: UTCDateTime
    entities: Dict[str, EntitySnapshot]


class LocationAnswer(BaseModel):
    entity_id: str
    as_of: UTCDateTime
    location: ClaimAssessment
    anchor_lineage: List[str] = Field(default_factory=list)
    supported_by_relations: List[Relation] = Field(default_factory=list)  # on / inside


class AnchorContent(BaseModel):
    entity_id: str
    location: Any
    via: str  # "location" | "relation:on" | ...
    assessment: ClaimAssessment


class SessionSummary(BaseModel):
    session: Session
    observation_ids: List[str]
    entities_observed: List[str]
    event_counts: Dict[str, int]
    events: List[Event]


class TaskStateView(BaseModel):
    task_id: str
    as_of: UTCDateTime
    status: TaskStatus
    steps: Dict[str, StepStatus]


# ------------------------------------------------------------- task continuity
class ConditionCheck(BaseModel):
    condition: StateCondition
    state: ConditionState
    observed_value: Any = None
    status: EpistemicStatus
    reason: str
    evidence_refs: List[str] = Field(default_factory=list)
    last_supported_at: Optional[UTCDateTime] = None
    risk: RiskLevel = RiskLevel.LOW  # the bar this check was evaluated at
    # True when the evidence would satisfy a lower-risk bar but not this one (fresh
    # OBSERVED, not verified or recent). Only such shortfalls can be waived by a person.
    risk_shortfall: bool = False


class ObservationRequest(BaseModel):
    """A targeted request for new evidence (spec §13), e.g. "Show the model label"."""

    entity_id: str
    attribute: str
    reason: str
    instruction: str
    current_status: EpistemicStatus
    last_known_value: Any = None
    for_steps: List[str] = Field(default_factory=list)
    score: Optional[float] = None  # filled by an active-perception policy (Phase 7)


class StepAssessment(BaseModel):
    step_id: str
    step_order: int
    description: str
    status: StepStatus
    completion_status: EpistemicStatus
    ready: bool
    blockers: List[str] = Field(default_factory=list)
    preconditions: List[ConditionCheck] = Field(default_factory=list)
    postconditions: List[ConditionCheck] = Field(default_factory=list)


class ResumePlan(BaseModel):
    """Output of the resume protocol (spec §11): what is still verified, what changed,
    what must be observed, and only then the next supported step."""

    task_id: str
    as_of: UTCDateTime
    checkpoint: UTCDateTime
    resumed_by: Optional[str] = None
    world_changes: List[WorldChange] = Field(default_factory=list)
    invalidated_steps: List[str] = Field(default_factory=list)
    blocked_steps: List[str] = Field(default_factory=list)
    unresolved: List[ConditionCheck] = Field(default_factory=list)
    requested_observations: List[ObservationRequest] = Field(default_factory=list)
    procedure_revision: Optional[Dict[str, Any]] = None
    steps: List[StepAssessment] = Field(default_factory=list)
    next_step: Optional[StepAssessment] = None
    # Facts the next step rests on, and those worth confirming before acting because
    # they are supported but not verified (decision-aware perception, Phase 11).
    decision_critical: List[ConditionCheck] = Field(default_factory=list)
    recommended_checks: List[ObservationRequest] = Field(default_factory=list)
    can_continue: bool
    task_status: TaskStatus
    message: str


# ------------------------------------------------------------- grounded queries
class QueryIntent(BaseModel):
    kind: QueryKind
    raw: str
    entity_ids: List[str] = Field(default_factory=list)
    ambiguous: Dict[str, List[str]] = Field(default_factory=dict)  # mention -> candidate ids
    anchor_id: Optional[str] = None
    attribute: Optional[str] = None
    as_of: Optional[UTCDateTime] = None  # point-in-time question ("where was X at 10:15")
    since: Optional[UTCDateTime] = None  # window start ("what changed since …", "yesterday")
    until: Optional[UTCDateTime] = None
    task_id: Optional[str] = None


class GroundedClaim(BaseModel):
    """One claim in a response, with the evidence gate's verdict (spec §15)."""

    claim: str
    entity_id: Optional[str] = None
    attribute: Optional[str] = None
    value: Any = None
    status: EpistemicStatus
    supportable: bool
    freshness: Optional[FreshnessAssessment] = None
    evidence_refs: List[str] = Field(default_factory=list)


class RetrievalHit(BaseModel):
    """Semantic recall result. The score is a recall aid, never evidence of truth."""

    doc_id: str
    kind: str
    ref_id: str
    text: str
    score: float
    entity_ids: List[str] = Field(default_factory=list)
    timestamp: Optional[UTCDateTime] = None


class GroundedResponse(BaseModel):
    """Spec §15 response contract. `answer` is set only when supported by evidence;
    otherwise ORBIT abstains and says what it would need to observe."""

    query: str
    as_of: UTCDateTime
    intent: QueryIntent
    answer: Optional[str] = None
    summary: str
    abstained: bool
    claims: List[GroundedClaim] = Field(default_factory=list)
    conflicts: List[ConflictSide] = Field(default_factory=list)
    requested_observation: Optional[ObservationRequest] = None
    requested_observations: List[ObservationRequest] = Field(default_factory=list)
    changes: List[WorldChange] = Field(default_factory=list)
    resume_plan: Optional[ResumePlan] = None
    retrieval: List[RetrievalHit] = Field(default_factory=list)
    providers: Dict[str, str] = Field(default_factory=dict)


# ----------------------------------------------------------- active perception
class ObservationCost(BaseModel):
    time_seconds: float
    effort: float  # user effort 0..1
    motion: float  # camera / body movement 0..1
    privacy: float  # exposure of unrelated people/things 0..1
    interruption: float  # disruption of ongoing work 0..1

    @property
    def total(self) -> float:
        return max(0.05, self.time_seconds / 60.0 + self.effort + self.motion + self.privacy + self.interruption)


class UncertainClaim(BaseModel):
    entity_id: str
    attribute: str
    status: EpistemicStatus
    reason: str
    last_known_value: Any = None
    region: Optional[str] = None  # where looking would help
    weight: float  # uncertainty × task relevance (information gain)
    blocking_steps: List[str] = Field(default_factory=list)
    decision_critical: bool = False  # the next step of an open task rests on this claim
    decision_weight: float = 0.0  # value of information for the next decision
    critical_for_steps: List[str] = Field(default_factory=list)


class PlannedObservation(BaseModel):
    action_type: ObservationActionType
    target: str  # anchor id or entity id
    attribute: Optional[str] = None
    instruction: str
    resolves: List[str] = Field(default_factory=list)  # "entity.attribute"
    expected_uncertainty_reduction: float
    cost: ObservationCost
    cost_total: float
    score: float


class PerceptionPlan(BaseModel):
    as_of: UTCDateTime
    policy: str
    weighting: str = "uncertainty"  # uncertainty | decision_value
    uncertain_claims: List[UncertainClaim]
    total_uncertainty: float
    actions: List[PlannedObservation]


# ---------------------------------------------------------------- action safety
class Principal(BaseModel):
    id: str
    kind: PrincipalKind
    scopes: List[Scope] = Field(default_factory=list)
    entity_scope: Optional[List[str]] = None  # None = all entities
    created_at: UTCDateTime


class Authorization(BaseModel):
    principal_id: str
    approved: bool
    at: UTCDateTime
    reason: Optional[str] = None
    # Prerequisites ("entity.attribute") the authoriser knowingly accepted below the
    # risk bar. Recorded for audit; only risk shortfalls can be waived.
    waived: List[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    """A consequential physical action routed through observe → verify prerequisites →
    authorize → act (by a person) → verify outcome → outcome memory (spec §16)."""

    id: str = Field(default_factory=lambda: generate_id("act"))
    action: str
    target_entity_ids: List[str] = Field(default_factory=list)
    consequential: bool = True
    risk: RiskLevel = RiskLevel.MEDIUM
    prerequisites: List[StateCondition] = Field(default_factory=list)
    expected_outcome: List[StateCondition] = Field(default_factory=list)
    requested_by: str
    status: ActionStatus
    prerequisite_checks: List[ConditionCheck] = Field(default_factory=list)
    outcome_checks: List[ConditionCheck] = Field(default_factory=list)
    requested_observations: List[ObservationRequest] = Field(default_factory=list)
    authorization: Optional[Authorization] = None
    performed_by: Optional[str] = None
    performed_at: Optional[UTCDateTime] = None
    task_id: Optional[str] = None
    step_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class OutcomeRecord(BaseModel):
    """Outcome memory (spec §7): action A under conditions C produced outcome O."""

    id: str = Field(default_factory=lambda: generate_id("out"))
    action_id: str
    action: str
    conditions: List[ConditionCheck]
    result: OutcomeResult
    observed: List[ConditionCheck]
    evidence_refs: List[str] = Field(default_factory=list)
    performed_by: Optional[str] = None
    recorded_at: UTCDateTime


# -------------------------------------------------- replay and counterfactuals
class ReplayFrame(BaseModel):
    at: UTCDateTime
    events: List[Event]
    changes: List[WorldChange]  # belief change since the previous frame


class Variation(StrictInput):
    kind: VariationKind
    entity_id: Optional[str] = None
    attribute: Optional[str] = None
    value: Any = None
    relation_type: Optional[str] = None
    target: Optional[str] = None
    present: bool = True
    minutes: float = 0.0
    description: Optional[str] = None


class SandboxInfo(BaseModel):
    id: str
    label: Optional[str] = None
    forked_from: UTCDateTime  # instant of the source world
    now: UTCDateTime  # sandbox clock
    variations: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)


class DecisionComparison(BaseModel):
    kind: str  # resume | query | perception
    subject: str
    baseline: Dict[str, Any]
    counterfactual: Dict[str, Any]
    changed: bool


class CounterfactualReport(BaseModel):
    """Static replay (the stored world) vs the same world under a what-if premise."""

    forked_from: UTCDateTime
    baseline_at: UTCDateTime
    counterfactual_at: UTCDateTime
    variations: List[Variation]
    effects: List[WorldChange]  # what the premise changed in belief, incl. propagation
    decisions: List[DecisionComparison]
    decision_changed: bool


class SensitivityItem(BaseModel):
    entity_id: str
    attribute: str
    expected: Any = None
    probe: str  # violated | unverified
    current_status: EpistemicStatus
    baseline_next: Optional[str] = None
    counterfactual_next: Optional[str] = None
    decision_changed: bool
    blocked_steps: List[str] = Field(default_factory=list)


class SensitivityReport(BaseModel):
    """Which facts, if wrong, would change what ORBIT tells you to do next?"""

    task_id: str
    as_of: UTCDateTime
    baseline_next: Optional[str] = None
    items: List[SensitivityItem]
    critical: List[str]  # "entity.attribute" whose failure changes the decision
    recommended_checks: List[ObservationRequest]
