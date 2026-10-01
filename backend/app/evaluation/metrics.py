"""Research metrics (spec §27). Pure functions over ORBIT outputs and ground truth."""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel

from backend.app.domain.models import WorldChange
from backend.app.domain.types import AbsenceStatus, EventType

_UNSET = object()


class ExpectedChange(BaseModel):
    """A ground-truth change. Unset optional fields are not checked."""

    change_type: EventType
    entity_id: str
    attribute: Optional[str] = None
    after: Any = None
    check_after: bool = False
    absence: Optional[AbsenceStatus] = None


@dataclass
class DiffScore:
    precision: float
    recall: float
    true_positives: int
    false_positives: List[WorldChange] = field(default_factory=list)
    false_negatives: List[ExpectedChange] = field(default_factory=list)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _matches(reported: WorldChange, expected: ExpectedChange) -> bool:
    if (reported.change_type, reported.entity_id) != (expected.change_type, expected.entity_id):
        return False
    if expected.attribute is not None and reported.attribute != expected.attribute:
        return False
    if expected.absence is not None and reported.absence != expected.absence:
        return False
    if expected.check_after and reported.after != expected.after:
        return False
    return True


def diff_precision_recall(reported: List[WorldChange], truth: List[ExpectedChange]) -> DiffScore:
    """World-diff precision = correct / reported; recall = correct / ground truth.

    Matching is one-to-one (each ground-truth change can be matched once)."""
    unmatched_truth = list(truth)
    false_positives: List[WorldChange] = []
    tp = 0
    for change in reported:
        hit = next((t for t in unmatched_truth if _matches(change, t)), None)
        if hit is None:
            false_positives.append(change)
        else:
            unmatched_truth.remove(hit)
            tp += 1
    precision = tp / len(reported) if reported else (1.0 if not truth else 0.0)
    recall = tp / len(truth) if truth else 1.0
    return DiffScore(precision, recall, tp, false_positives, unmatched_truth)
