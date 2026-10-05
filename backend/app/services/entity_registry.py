"""Deterministic entity re-identification (spec §2.2, §6.3).

Resolution order for one detection:

1. Explicit ``candidate_entity_id`` of a known entity → match, unless the type or a
   strong identifier contradicts it. Contradicting identifiers mean a *different
   physical object* (e.g. a replaced cable): a new entity is created and flagged
   ``POSSIBLE_REPLACEMENT``, never merged.
2. Strong identifier (serial number, asset tag) equal to exactly one known entity.
3. Explicit candidate id that is not yet known → new entity with that id. Callers
   without stable ids (raw detectors) must omit ``candidate_entity_id``.
4. Signature: same type, no conflict on stable attributes (colour, model, ...).
   A single compatible entity matches (possibly relocated). Several are separated by
   spatial proximity; if that is not decisive the detection becomes a new
   ``AMBIGUOUS`` entity listing the candidates — same-looking objects are never
   merged by guess.

An entity can be matched at most once per observation (``exclude``).
"""
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional, Set

from backend.app.domain.models import Entity, Observation, ObservedEntity
from backend.app.domain.types import IdentityStatus, ResolutionMethod
from backend.app.repositories.base import Repository
from backend.app.services.spatial import AnchorRegistry

# Attributes that describe what an object *is* and should not change while it exists.
# Mutable state (power, configuration, location) is deliberately excluded.
SIGNATURE_ATTRIBUTES = frozenset({"color", "model", "model_number", "brand", "material", "size", "shape"})
POSITION_MATCH_METERS = 0.3
SPATIAL_MATCH_MIN = 0.8
SPATIAL_MATCH_MARGIN = 0.3
RELOCATION_CHECK_SECONDS = 600.0  # a look at the old place this recent supports "it moved"

MATCH_METHODS = frozenset(
    {
        ResolutionMethod.EXPLICIT_ID,
        ResolutionMethod.STRONG_IDENTIFIER,
        ResolutionMethod.SIGNATURE,
        ResolutionMethod.SPATIAL,
    }
)


@dataclass
class Resolution:
    method: ResolutionMethod
    confidence: float
    entity_id: Optional[str] = None  # set for matches and for NEW with a caller-provided id
    candidates: List[str] = field(default_factory=list)
    identity_status: IdentityStatus = IdentityStatus.ESTABLISHED
    reason: str = ""

    @property
    def is_match(self) -> bool:
        return self.method in MATCH_METHODS


def identity_conflict(entity: Entity, observed: ObservedEntity) -> Optional[str]:
    if entity.type != observed.type:
        return f"type {observed.type!r} != {entity.type!r}"
    for key, value in observed.identifiers.items():
        known = entity.canonical_attributes.get(key)
        if known is not None and known != value:
            return f"{key} {value!r} != {known!r}"
    return None


# Camera colour estimates for neutral objects drift between neighbouring shades with
# lighting (a black mouse read as gray, then black: Phase 15 live test). Neighbouring
# neutral shades therefore do not prove two objects are different; black vs white does.
COLOR_NEIGHBOURS = frozenset({frozenset({"black", "gray"}), frozenset({"gray", "white"}),
                              frozenset({"black", "grey"}), frozenset({"grey", "white"}), frozenset({"gray", "grey"})})


def values_compatible(key: str, a, b) -> bool:
    if a == b:
        return True
    if key == "color" and isinstance(a, str) and isinstance(b, str):
        return frozenset({a.lower(), b.lower()}) in COLOR_NEIGHBOURS
    return False


def signature_conflict(entity: Entity, observed: ObservedEntity) -> Optional[str]:
    for key in SIGNATURE_ATTRIBUTES & observed.attributes.keys():
        known = entity.current_state.get(key)
        if known is not None and not values_compatible(key, known, observed.attributes[key]):
            return f"{key} {observed.attributes[key]!r} != {known!r}"
    return None


def identity_conflict_between(a: Entity, b: Entity) -> Optional[str]:
    for key, value in a.canonical_attributes.items():
        other = b.canonical_attributes.get(key)
        if other is not None and other != value:
            return f"{key} {value!r} != {other!r}"
    return None


def signature_conflict_between(a: Entity, b: Entity) -> Optional[str]:
    for key in SIGNATURE_ATTRIBUTES & a.current_state.keys() & b.current_state.keys():
        if a.current_state[key] is not None and b.current_state[key] is not None and not values_compatible(key, a.current_state[key], b.current_state[key]):
            return f"{key} {a.current_state[key]!r} != {b.current_state[key]!r}"
    return None


class EntityRegistry:
    def __init__(self, repository: Repository, anchors: AnchorRegistry):
        self.repo = repository
        self.anchors = anchors

    def resolve(self, observed: ObservedEntity, exclude: Set[str], context: Optional[Observation] = None) -> Resolution:
        cid = observed.candidate_entity_id
        claimed = self.repo.get_entity(cid) if cid else None
        hops = 0
        while claimed is not None and claimed.merged_into and hops < 16:  # a merged id is an alias (Phase 13)
            claimed = self.repo.get_entity(claimed.merged_into)
            hops += 1

        if claimed is not None:
            if claimed.id in exclude:
                return Resolution(
                    ResolutionMethod.NEW_IDENTITY_CONFLICT,
                    0.5,
                    candidates=[claimed.id],
                    identity_status=IdentityStatus.AMBIGUOUS,
                    reason=f"{cid} already matched by another detection in this observation",
                )
            conflict = identity_conflict(claimed, observed)
            if conflict is None:
                why = "explicit id" if claimed.id == cid else f"explicit id {cid} (alias of {claimed.id})"
                return Resolution(ResolutionMethod.EXPLICIT_ID, 1.0, entity_id=claimed.id, reason=why)
            return Resolution(
                ResolutionMethod.NEW_IDENTITY_CONFLICT,
                0.9,
                candidates=[claimed.id],
                identity_status=IdentityStatus.POSSIBLE_REPLACEMENT,
                reason=f"claimed id {cid} but {conflict}",
            )

        pool = [
            e for e in self.repo.list_entities()
            if e.id not in exclude and e.type == observed.type and not e.merged_into
        ]

        if observed.identifiers:
            hits = [
                e
                for e in pool
                if identity_conflict(e, observed) is None
                and any(e.canonical_attributes.get(k) == v for k, v in observed.identifiers.items())
            ]
            if len(hits) == 1:
                return Resolution(
                    ResolutionMethod.STRONG_IDENTIFIER, 1.0, entity_id=hits[0].id, reason="identifier match"
                )

        if cid:
            return Resolution(ResolutionMethod.NEW, 1.0, entity_id=cid, reason="new explicit id")

        compatible = [
            e for e in pool if identity_conflict(e, observed) is None and signature_conflict(e, observed) is None
        ]
        if not compatible:
            return Resolution(ResolutionMethod.NEW, 1.0, reason="no compatible entity")

        if len(compatible) == 1:
            only = compatible[0]
            near = self._spatial_score(only, observed) >= 0.6
            if not near and not self._left_old_place(only, context):
                # "The same object, moved" needs evidence that it left its old place;
                # otherwise it is just as likely a new look-alike (Phase 14, ADR-038).
                return Resolution(
                    ResolutionMethod.NEW_AMBIGUOUS,
                    0.0,
                    candidates=[only.id],
                    identity_status=IdentityStatus.AMBIGUOUS,
                    reason=f"could be {only.id} relocated or a new {observed.type}: its old place was not checked",
                )
            return Resolution(
                ResolutionMethod.SIGNATURE,
                0.9 if near else 0.7,
                entity_id=only.id,
                reason="unique compatible signature" + ("" if near else " (relocated; old place checked)"),
            )

        scored = sorted(((self._spatial_score(e, observed), e.id) for e in compatible), reverse=True)
        best, second = scored[0][0], scored[1][0]
        if best >= SPATIAL_MATCH_MIN and best - second >= SPATIAL_MATCH_MARGIN:
            return Resolution(ResolutionMethod.SPATIAL, 0.8, entity_id=scored[0][1], reason="spatially decisive")
        return Resolution(
            ResolutionMethod.NEW_AMBIGUOUS,
            0.0,
            candidates=sorted(e.id for e in compatible),
            identity_status=IdentityStatus.AMBIGUOUS,
            reason=f"{len(compatible)} indistinguishable candidates",
        )

    def _left_old_place(self, entity: Entity, context: Optional[Observation]) -> bool:
        """Did a recent look at the entity's last known place fail to see it there?"""
        if context is None:
            return True  # no observation context (direct API use): keep the permissive rule
        old = entity.current_state.get("location") or entity.anchor
        if old is None:
            return True
        recent = [o for o in self.repo.list_observations()
                  if context.timestamp - timedelta(seconds=RELOCATION_CHECK_SECONDS) <= o.timestamp <= context.timestamp]
        for obs in recent + [context]:
            if any(r.entity_id == entity.id for r in obs.resolutions):
                continue  # it was seen in that observation, so it says nothing about absence
            view = obs.spatial_context.get("field_of_view")
            views = view if isinstance(view, list) else [view] if view else []
            looked = any(self.anchors.is_within(old, v) for v in views) or any(
                d.location is not None and self.anchors.is_within(d.location, old) for d in obs.observed_entities
            )
            if looked:
                return True
        return False

    def _spatial_score(self, entity: Entity, observed: ObservedEntity) -> float:
        observed_loc = observed.location or observed.anchor
        entity_loc = entity.current_state.get("location") or entity.anchor
        score = self.anchors.proximity(observed_loc, entity_loc)
        if (
            score == 1.0
            and observed.geometry
            and entity.geometry
            and observed.geometry.position
            and entity.geometry.position
        ):
            a, b = observed.geometry.position, entity.geometry.position
            dist = math.sqrt(sum((a.get(k, 0.0) - b.get(k, 0.0)) ** 2 for k in ("x", "y", "z")))
            return 1.0 if dist <= POSITION_MATCH_METERS else 0.6
        return score
