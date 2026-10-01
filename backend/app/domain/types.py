from enum import Enum

class EpistemicStatus(str, Enum):
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    STALE = "STALE"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"

class EventType(str, Enum):
    OBJECT_ADDED = "OBJECT_ADDED"
    OBJECT_MOVED = "OBJECT_MOVED"
    OBJECT_STATE_CHANGED = "OBJECT_STATE_CHANGED"
    OBJECT_REMOVED_OR_UNOBSERVED = "OBJECT_REMOVED_OR_UNOBSERVED"
    RELATION_CHANGED = "RELATION_CHANGED"
    TASK_PROGRESS_CHANGED = "TASK_PROGRESS_CHANGED"
    PROCEDURE_REVISION_DETECTED = "PROCEDURE_REVISION_DETECTED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    # Identity events (Phase 1): re-identification decisions that need surfacing.
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    # Belief events (Phase 2): changes to what ORBIT believes, not to the world itself.
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    UNCONFIRMED_CHANGE = "UNCONFIRMED_CHANGE"
    STATE_INVALIDATED = "STATE_INVALIDATED"

class VolatilityClass(str, Enum):
    LOW = "LOW"        # e.g., wall color, equipment serial
    MEDIUM = "MEDIUM"  # e.g., tool/cable location on a bench
    HIGH = "HIGH"      # e.g., device power, temperature, active worker


class IdentityStatus(str, Enum):
    ESTABLISHED = "ESTABLISHED"                    # resolved by id, identifier or unique signature
    AMBIGUOUS = "AMBIGUOUS"                        # may be one of identity_candidates; not merged
    POSSIBLE_REPLACEMENT = "POSSIBLE_REPLACEMENT"  # identifiers contradict the claimed id


class ResolutionMethod(str, Enum):
    EXPLICIT_ID = "EXPLICIT_ID"
    STRONG_IDENTIFIER = "STRONG_IDENTIFIER"
    SIGNATURE = "SIGNATURE"
    SPATIAL = "SPATIAL"
    NEW = "NEW"
    NEW_AMBIGUOUS = "NEW_AMBIGUOUS"
    NEW_IDENTITY_CONFLICT = "NEW_IDENTITY_CONFLICT"


class SourceType(str, Enum):
    """Spec §9 evidence source types."""

    VISUAL_OBSERVATION = "VISUAL_OBSERVATION"
    AUDIO_OBSERVATION = "AUDIO_OBSERVATION"
    SENSOR_READING = "SENSOR_READING"
    USER_STATEMENT = "USER_STATEMENT"
    EXTERNAL_RECORD = "EXTERNAL_RECORD"
    PROCEDURE = "PROCEDURE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    MANUAL_VERIFICATION = "MANUAL_VERIFICATION"
    INFERENCE = "INFERENCE"
    OTHER = "OTHER"


class EvidenceChannel(str, Enum):
    """Independent ways of knowing. Disagreement *across* channels is a contradiction
    candidate; a later claim on the *same* channel is temporal succession."""

    DIRECT = "DIRECT"  # perception and hands-on verification
    RECORD = "RECORD"  # registries, procedures, system/tool output
    TESTIMONY = "TESTIMONY"  # what a person says
    INFERENCE = "INFERENCE"  # derived by reasoning


class ClaimDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"  # part of current belief (or was, until valid_to)
    CONFLICTING = "CONFLICTING"  # one side of a conflict
    UNCONFIRMED = "UNCONFIRMED"  # retained as evidence, never current belief
    REJECTED = "REJECTED"  # losing side of a resolved conflict


class ClaimDecision(str, Enum):
    NEW = "NEW"
    CORROBORATE = "CORROBORATE"
    SUPERSEDE = "SUPERSEDE"
    CONTRADICT = "CONTRADICT"
    CONFLICT_UPDATE = "CONFLICT_UPDATE"
    RESOLVE = "RESOLVE"
    UNCONFIRMED = "UNCONFIRMED"


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"  # > 75% of TTL used: good target for a refresh observation
    STALE = "STALE"  # TTL exceeded
    INVALIDATED = "INVALIDATED"  # explicitly invalidated (intervention, dependency, revision)
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    INTERRUPTED = "INTERRUPTED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class StepStatus(str, Enum):
    """Progress of a step. How well its completion is supported is a separate
    epistemic status (``TaskStep.completion_status``)."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NEEDS_REVERIFICATION = "NEEDS_REVERIFICATION"  # was completed; its outcome was invalidated
    SKIPPED = "SKIPPED"


class HypothesisStatus(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
