"""Risk-graded verification (spec §2.10, §16): the more consequential a step or
action, the stronger and more recent the evidence its prerequisites need.

LOW/MEDIUM: a prerequisite must hold on supportable (fresh OBSERVED/VERIFIED) evidence.
HIGH: additionally *recent* — observed within ``high_observed_seconds`` (10 min) or
verified within ``high_verified_seconds`` (60 min). Verification is stronger
evidence, so it lasts longer, but it ages too: a three-hour-old verification does not
license cutting a coolant line. A prerequisite that holds but misses the HIGH bar
becomes UNSUPPORTED with ``risk_shortfall=True`` — it yields an observation request,
and only such shortfalls may be explicitly waived by an authorising person.
"""
from dataclasses import dataclass
from datetime import datetime

from backend.app.domain.models import ConditionCheck
from backend.app.domain.types import ConditionState, EpistemicStatus, RiskLevel


@dataclass(frozen=True)
class RiskPolicy:
    high_observed_seconds: float = 600.0
    high_verified_seconds: float = 3600.0
    enabled: bool = True  # False: the "no risk grading" ablation

    def apply(self, check: ConditionCheck, risk: RiskLevel, as_of: datetime) -> ConditionCheck:
        check.risk = risk
        if not self.enabled or risk != RiskLevel.HIGH or check.state != ConditionState.SATISFIED:
            return check
        age = (as_of - check.last_supported_at).total_seconds() if check.last_supported_at else None
        window = self.high_verified_seconds if check.status == EpistemicStatus.VERIFIED else self.high_observed_seconds
        if age is not None and age <= window:
            return check
        check.state = ConditionState.UNSUPPORTED
        check.risk_shortfall = True
        when = f"{age / 60:.0f} min ago" if age is not None else "at an unknown time"
        check.reason = (
            f"high-risk: needs a re-observation within {self.high_observed_seconds / 60:.0f} min or a verification "
            f"within {self.high_verified_seconds / 60:.0f} min (last {check.status.value.lower()} {when})"
        )
        return check
