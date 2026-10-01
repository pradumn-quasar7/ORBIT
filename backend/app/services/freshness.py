"""Attribute-level freshness policy engine (spec §2.6, §10).

Deterministic by design (spec §10: "implement a deterministic policy engine rather
than a learned freshness model"). Freshness is *computed at read time* from a
version's support history, so it never rewrites history and is correct for any
``as_of`` — including past instants.

Policy lookup order: entity-level override → attribute default → MEDIUM default.
"""
from datetime import datetime, timedelta
from typing import Dict, Optional

from backend.app.domain.models import Entity, FreshnessAssessment, FreshnessPolicy, StateVersion
from backend.app.domain.types import FreshnessState, VolatilityClass

DAY = 86400.0
AGING_FRACTION = 0.75
WEAK_SUPPORT = 0.35  # supports weaker than this never refresh a claim

VOLATILITY_DEFAULTS: Dict[VolatilityClass, FreshnessPolicy] = {
    VolatilityClass.LOW: FreshnessPolicy(volatility=VolatilityClass.LOW, ttl_seconds=30 * DAY),
    VolatilityClass.MEDIUM: FreshnessPolicy(
        volatility=VolatilityClass.MEDIUM, ttl_seconds=DAY, contradiction_window_seconds=900.0
    ),
    VolatilityClass.HIGH: FreshnessPolicy(
        volatility=VolatilityClass.HIGH, ttl_seconds=300.0, contradiction_window_seconds=60.0
    ),
}


def _p(vol: VolatilityClass, ttl: Optional[float], window: Optional[float] = None, triggers=()) -> FreshnessPolicy:
    return FreshnessPolicy(
        volatility=vol, ttl_seconds=ttl, contradiction_window_seconds=window, invalidation_triggers=list(triggers)
    )


LOW, MEDIUM, HIGH = VolatilityClass.LOW, VolatilityClass.MEDIUM, VolatilityClass.HIGH

ATTRIBUTE_DEFAULTS: Dict[str, FreshnessPolicy] = {
    # where things are
    "location": _p(MEDIUM, DAY, 900.0),
    # fast-changing physical state
    "power": _p(HIGH, 300.0, 60.0),
    "temperature": _p(HIGH, 300.0, 60.0),
    "temperature_c": _p(HIGH, 300.0, 60.0),
    "screen": _p(HIGH, 300.0, 60.0),
    # slow-changing configuration (spec: configuration revalidates on change)
    "configuration": _p(LOW, 7 * DAY),
    "firmware": _p(LOW, 30 * DAY),
    "firmware_version": _p(LOW, 30 * DAY),
    "procedure_revision": _p(LOW, 30 * DAY),
    "revision": _p(LOW, 30 * DAY),
    "calibration": _p(LOW, 30 * DAY, triggers=("location", "configuration", "firmware")),
    # intrinsic properties: do not expire with age
    "color": _p(LOW, None),
    "model": _p(LOW, None),
    "model_number": _p(LOW, None),
    "brand": _p(LOW, None),
    "material": _p(LOW, None),
    "size": _p(LOW, None),
    "shape": _p(LOW, None),
    "label": _p(LOW, 30 * DAY),
}


class FreshnessPolicyRegistry:
    def __init__(self, overrides: Optional[Dict[str, FreshnessPolicy]] = None, enabled: bool = True):
        self.attribute_policies = {**ATTRIBUTE_DEFAULTS, **(overrides or {})}
        # `enabled=False` is the "no freshness tracking" ablation baseline (spec §28).
        self.enabled = enabled

    def policy_for(self, entity: Optional[Entity], attribute: str) -> FreshnessPolicy:
        if entity is not None and attribute in entity.freshness_policies:
            return entity.freshness_policies[attribute]
        return self.attribute_policies.get(attribute, VOLATILITY_DEFAULTS[VolatilityClass.MEDIUM])

    @staticmethod
    def contradiction_window(policy: FreshnessPolicy) -> Optional[float]:
        """Seconds within which cross-channel disagreement is a contradiction.

        None means "always" (intrinsic attributes cannot legitimately differ)."""
        if policy.contradiction_window_seconds is not None:
            return policy.contradiction_window_seconds
        if policy.volatility == VolatilityClass.LOW:
            return policy.ttl_seconds
        return VOLATILITY_DEFAULTS[policy.volatility].contradiction_window_seconds

    @staticmethod
    def support_time(version: StateVersion, as_of: Optional[datetime] = None) -> Optional[datetime]:
        """Latest non-weak support at or before ``as_of``."""
        times = [
            s.at for s in version.support if s.strength >= WEAK_SUPPORT and (as_of is None or s.at <= as_of)
        ]
        if times:
            return max(times)
        if version.valid_from <= (as_of or version.valid_from):
            return version.valid_from
        return None

    def assess(self, version: StateVersion, policy: FreshnessPolicy, as_of: datetime) -> FreshnessAssessment:
        supported = self.support_time(version, as_of)
        base = dict(as_of=as_of, last_supported_at=supported, ttl_seconds=policy.ttl_seconds, volatility=policy.volatility)
        if not self.enabled:
            return FreshnessAssessment(state=FreshnessState.FRESH, reason="freshness tracking disabled", **base)
        if supported is None:
            return FreshnessAssessment(state=FreshnessState.NOT_APPLICABLE, reason="no support yet", **base)

        inv = version.invalidated_at
        if inv is not None and inv <= as_of and supported < inv:
            return FreshnessAssessment(
                state=FreshnessState.INVALIDATED,
                age_seconds=(as_of - supported).total_seconds(),
                reason=version.invalidation_reason or "invalidated",
                **base,
            )

        age = (as_of - supported).total_seconds()
        if policy.ttl_seconds is None:
            return FreshnessAssessment(state=FreshnessState.FRESH, age_seconds=age, reason="does not expire", **base)
        expires = supported + timedelta(seconds=policy.ttl_seconds)
        if age > policy.ttl_seconds:
            state, reason = FreshnessState.STALE, f"last supported {age:.0f}s ago > ttl {policy.ttl_seconds:.0f}s"
        elif age > AGING_FRACTION * policy.ttl_seconds:
            state, reason = FreshnessState.AGING, f"{age / policy.ttl_seconds:.0%} of ttl used"
        else:
            state, reason = FreshnessState.FRESH, None
        return FreshnessAssessment(state=state, age_seconds=age, expires_at=expires, reason=reason, **base)
