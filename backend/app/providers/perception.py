"""Device adapter and perception gateway providers (spec §6.1, §6.2, §19).

ORBIT must not depend on one hardware vendor or one perception model. A device
produces ``RawFrame``s; a ``PerceptionProvider`` turns a frame into an
``Observation`` — structured *observations*, never truth. Two implementations:

* ``DetectionPerceptionProvider`` adapts any detector (YOLO, OWL-ViT, a VLM, AR
  markers …) that yields label / box / confidence / OCR text.
* ``SimulatedPerceptionProvider`` renders a scripted ground-truth scene with field of
  view, occlusion and seeded misses — the controlled input for ORBIT-BENCH.

Raw pixels are not part of the observation: only a content hash is kept by default
(spec §17 evidence minimisation).
"""
import hashlib
import random
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.app.core.time import UTCDateTime
from backend.app.domain.models import Observation, ObservedEntity, ObservedRelation, generate_id


class RawFrame(BaseModel):
    frame_id: str = Field(default_factory=lambda: generate_id("frame"))
    device_id: str
    timestamp: UTCDateTime
    session_id: Optional[str] = None
    content_hash: Optional[str] = None  # sha256 of the frame bytes
    image_ref: Optional[str] = None  # where raw pixels live, only if retained
    field_of_view: Optional[str] = None  # anchor the device is looking at
    pose: Dict[str, float] = Field(default_factory=dict)
    lighting: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def make_frame(device_id: str, timestamp: datetime, image_bytes: Optional[bytes] = None, **kw) -> RawFrame:
    digest = "sha256:" + hashlib.sha256(image_bytes).hexdigest() if image_bytes is not None else None
    return RawFrame(device_id=device_id, timestamp=timestamp, content_hash=digest, **kw)


class PerceptionProvider(ABC):
    name = "perception"
    model_id = "unknown"

    @abstractmethod
    def perceive(self, frame: RawFrame) -> Observation: ...

    def _observation(self, frame: RawFrame, entities: List[ObservedEntity], quality: float = 1.0) -> Observation:
        return Observation(
            timestamp=frame.timestamp,
            source=frame.device_id,
            session_id=frame.session_id,
            raw_reference=frame.content_hash,  # a hash, not the pixels
            observed_entities=entities,
            spatial_context={"field_of_view": frame.field_of_view, "pose": frame.pose, "lighting": frame.lighting},
            quality=quality,
            provenance={"perception_provider": self.name, "model_id": self.model_id, "frame_id": frame.frame_id},
        )


# Classes never forwarded into world memory (spec §17 privacy: no identity of people).
EXCLUDED_LABELS = frozenset({"person"})

# --------------------------------------------------------------- detector adapter
class Detection(BaseModel):
    label: str
    confidence: float
    bbox: Tuple[float, float, float, float]  # normalised x0, y0, x1, y1
    text: Optional[str] = None  # OCR'd label text, if any
    attributes: Dict[str, Any] = Field(default_factory=dict)
    track_id: Optional[str] = None  # detector-local; NOT a persistent identity
    # A fiducial marker (e.g. a printed QR code "orbit:<id>") read on the object: a
    # deliberate, stable identity, so it is used as the explicit entity id (ADR-039).
    marker_id: Optional[str] = None


IDENTIFIER_PATTERNS: Dict[str, re.Pattern] = {
    "serial_number": re.compile(r"\b(?:S/?N|SERIAL)[:\s-]*([A-Z0-9-]{3,})\b", re.I),
    "asset_tag": re.compile(r"\bASSET[:\s-]*([A-Z0-9-]{3,})\b", re.I),
    "model_number": re.compile(r"\bMODEL[:\s-]*([A-Z0-9-]{2,})\b", re.I),
}


def parse_identifiers(text: Optional[str]) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Split OCR text into strong identifiers (serials, asset tags) and attributes."""
    identifiers: Dict[str, str] = {}
    attributes: Dict[str, Any] = {}
    if not text:
        return identifiers, attributes
    for key, rx in IDENTIFIER_PATTERNS.items():
        m = rx.search(text)
        if m:
            (attributes if key == "model_number" else identifiers)[key] = m.group(1).upper()
    attributes.setdefault("label", text.strip())
    return identifiers, attributes


class DetectionPerceptionProvider(PerceptionProvider):
    name = "detector-adapter-v1"

    def __init__(
        self,
        detector: Callable[[RawFrame], List[Detection]],
        region_map: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        min_confidence: float = 0.3,
        model_id: str = "external-detector",
    ):
        self.detector = detector
        self.region_map = region_map or {}  # calibrated image regions → anchors, per device view
        self.min_confidence = min_confidence
        self.model_id = model_id

    def _anchor(self, bbox, frame: RawFrame) -> Optional[str]:
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        hits = [
            ((x1 - x0) * (y1 - y0), anchor)
            for anchor, (x0, y0, x1, y1) in self.region_map.items()
            if x0 <= cx <= x1 and y0 <= cy <= y1
        ]
        return min(hits)[1] if hits else frame.field_of_view

    def perceive(self, frame: RawFrame) -> Observation:
        entities = []
        for d in self.detector(frame):
            if d.confidence < self.min_confidence or d.label.lower() in EXCLUDED_LABELS:
                continue
            identifiers, attrs = parse_identifiers(d.text)
            if d.marker_id:
                identifiers["marker"] = d.marker_id
            entities.append(
                ObservedEntity(
                    candidate_entity_id=d.marker_id,
                    type=d.label,
                    location=self._anchor(d.bbox, frame),
                    identifiers=identifiers,
                    attributes={**attrs, **d.attributes},
                    confidence=d.confidence,
                )
            )
        quality = 0.6 if (frame.lighting or "").lower() in ("poor", "dark") else 1.0
        return self._observation(frame, entities, quality)


# ------------------------------------------------------------- simulated scene
class SimObject(BaseModel):
    type: str
    location: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    identifiers: Dict[str, str] = Field(default_factory=dict)
    relations: List[ObservedRelation] = Field(default_factory=list)
    occluded: bool = False


class SimulatedScene(BaseModel):
    """Ground truth W_t for controlled experiments. ORBIT never reads it directly."""

    objects: Dict[str, SimObject] = Field(default_factory=dict)

    def place(self, oid: str, type: str, location: str, **kw) -> None:
        self.objects[oid] = SimObject(type=type, location=location, **kw)

    def move(self, oid: str, location: str) -> None:
        self.objects[oid].location = location

    def set(self, oid: str, **attributes) -> None:
        self.objects[oid].attributes.update(attributes)

    def remove(self, oid: str) -> None:
        self.objects.pop(oid, None)


class SimulatedPerceptionProvider(PerceptionProvider):
    name = "simulated-v1"
    model_id = "scripted-scene"

    def __init__(
        self,
        scene: SimulatedScene,
        is_within: Callable[[str, str], bool],
        seed: int = 0,
        miss_rate: float = 0.0,
        confidence: float = 0.95,
        stable_ids: bool = True,  # e.g. fiducial markers; False = anonymous detections
    ):
        self.scene = scene
        self.is_within = is_within
        self.rng = random.Random(seed)
        self.miss_rate = miss_rate
        self.confidence = confidence
        self.stable_ids = stable_ids

    def perceive(self, frame: RawFrame) -> Observation:
        entities = []
        for oid, obj in sorted(self.scene.objects.items()):
            if frame.field_of_view and not self.is_within(obj.location, frame.field_of_view):
                continue
            if obj.occluded or self.rng.random() < self.miss_rate:
                continue
            entities.append(
                ObservedEntity(
                    candidate_entity_id=oid if self.stable_ids else None,
                    type=obj.type,
                    location=obj.location,
                    attributes=dict(obj.attributes),
                    identifiers=dict(obj.identifiers),
                    relations=list(obj.relations),
                    confidence=self.confidence,
                )
            )
        quality = 0.6 if (frame.lighting or "").lower() in ("poor", "dark") else 1.0
        return self._observation(frame, entities, quality)
