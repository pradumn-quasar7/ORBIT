"""Evaluate task pre/postconditions against the evidence gate, and phrase the
targeted observation that would resolve an unsupported one (spec §13)."""
from datetime import datetime
from typing import Any

from backend.app.domain.models import ConditionCheck, ObservationRequest, StateCondition
from backend.app.domain.types import ConditionState, EpistemicStatus
from backend.app.repositories.base import Repository
from backend.app.services.claims import ClaimEvaluator

STATUS_RANK = {EpistemicStatus.OBSERVED: 1, EpistemicStatus.VERIFIED: 2}


def _holds(condition: StateCondition, value: Any) -> bool:
    op = condition.operator
    if op == "eq":
        return value == condition.expected
    if op == "ne":
        return value != condition.expected
    if op == "in":
        return value in (condition.expected or [])
    if op == "exists":
        return value is not None
    raise ValueError(f"unknown operator {op!r}")


class ConditionEvaluator:
    def __init__(self, repository: Repository, claims: ClaimEvaluator):
        self.repo = repository
        self.claims = claims

    def check(self, condition: StateCondition, as_of: datetime) -> ConditionCheck:
        a = self.claims.assess_attribute(condition.entity_id, condition.attribute, as_of)
        base = dict(condition=condition, status=a.status, evidence_refs=a.evidence_refs)
        if not a.supportable:
            return ConditionCheck(state=ConditionState.UNSUPPORTED, observed_value=a.last_known_value, reason=a.reason, **base)
        if STATUS_RANK.get(a.status, 0) < STATUS_RANK.get(condition.min_status, 1):
            return ConditionCheck(
                state=ConditionState.UNSUPPORTED,
                observed_value=a.value,
                reason=f"requires {condition.min_status.value}, evidence is {a.status.value}",
                **base,
            )
        holds = _holds(condition, a.value)
        return ConditionCheck(
            state=ConditionState.SATISFIED if holds else ConditionState.VIOLATED,
            observed_value=a.value,
            reason="holds" if holds else f"{condition.attribute} is {a.value!r}, expected {condition.operator} {condition.expected!r}",
            **base,
        )

    def request_for(self, check: ConditionCheck, step_ids=()) -> ObservationRequest:
        c = check.condition
        entity = self.repo.get_entity(c.entity_id)
        # An entity named only by its type ("pump") is ambiguous; fall back to its id.
        name = entity.name if entity and entity.name and entity.name != entity.type else c.entity_id
        return ObservationRequest(
            entity_id=c.entity_id,
            attribute=c.attribute,
            reason=check.reason,
            instruction=instruction_for(name, c.attribute, check.status, check.observed_value),
            current_status=check.status,
            last_known_value=check.observed_value,
            for_steps=list(step_ids),
        )


def instruction_for(name: str, attribute: str, status: EpistemicStatus, last_known: Any = None) -> str:
    """Deterministic, attribute-aware phrasing of what to look at."""
    if attribute == "location":
        where = f"near {last_known} " if last_known else ""
        text = f"Point the camera {where}so I can find {name}."
    elif attribute in ("serial_number", "model_number", "model", "label", "asset_tag"):
        text = f"Move closer so I can read the {attribute.replace('_', ' ')} on {name}."
    elif attribute in ("configuration", "firmware", "firmware_version", "calibration"):
        text = f"Show the {attribute.replace('_', ' ')} label or display of {name}."
    elif attribute in ("procedure_revision", "revision"):
        text = f"Confirm the current revision of {name}."
    elif attribute in ("power", "screen"):
        text = f"Show the power indicator of {name}."
    else:
        text = f"Show me {name} so I can check its {attribute.replace('_', ' ')}."
    if status == EpistemicStatus.CONTRADICTED:
        return f"Sources disagree about {name}'s {attribute.replace('_', ' ')}. {text}"
    return text
