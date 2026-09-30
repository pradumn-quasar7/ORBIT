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

class VolatilityClass(str, Enum):
    LOW = "LOW"        # e.g., wall color, equipment serial
    MEDIUM = "MEDIUM"  # e.g., tool/cable location on a bench
    HIGH = "HIGH"      # e.g., device power, temperature, active worker
