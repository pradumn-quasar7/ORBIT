"""Live camera perception (Phase 15): calibration, snapshots and region scans.

Detection runs on the client (in the browser, next to the camera); only detections
reach ORBIT — never pixels (spec §17). A fixed camera is calibrated by drawing image
regions; each region is an ORBIT anchor whose ``frame`` stores its image box for that
camera, so calibration lives in the world model itself. The camera's field of view is
the regions' parent anchor, which lets re-identification use "the camera looked at
the old place" as evidence of relocation (ADR-038).
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.app.domain.models import Event, SearchCoverage
from backend.app.providers.perception import EXCLUDED_LABELS, Detection, DetectionPerceptionProvider, RawFrame
from backend.app.repositories.base import Repository
from backend.app.services.belief import LOCATION
from backend.app.services.perception_gateway import PerceptionGateway
from backend.app.services.search import SearchService
from backend.app.services.world_state_engine import WorldStateEngine

MODEL_ID = "coco-ssd (browser, TensorFlow.js)"


class CameraNotCalibrated(ValueError):
    pass


class RegionSpec(BaseModel):
    id: str
    name: Optional[str] = None
    bbox: Tuple[float, float, float, float]  # normalised x0, y0, x1, y1 in this camera's image


class CameraConfig(BaseModel):
    device_id: str
    view: Optional[str] = None  # parent anchor the camera looks at (its field of view)
    regions: List[RegionSpec] = Field(default_factory=list)


class SeenEntity(BaseModel):
    entity_id: str
    label: str
    location: Optional[str] = None
    method: str
    confidence: float


class SnapshotResult(BaseModel):
    observation_id: str
    events: List[Event]
    seen: List[SeenEntity]
    dropped: int  # detections not forwarded (privacy-excluded or below confidence)


class CameraService:
    def __init__(self, repository: Repository, engine: WorldStateEngine, search: SearchService, min_confidence: float = 0.5):
        self.repo = repository
        self.engine = engine
        self.search = search
        self.min_confidence = min_confidence

    # --------------------------------------------------------- calibration
    def configure(self, device_id: str, view: str, regions: List[RegionSpec], at: datetime,
                  view_type: str = "surface", room: Optional[str] = None) -> CameraConfig:
        anchors = self.engine.anchors
        with self.repo.transaction():
            if room and self.repo.get_anchor(room) is None:
                anchors.register(room, at, anchor_type="room")
            existing = self.repo.get_anchor(view)
            anchors.register(view, at, name=existing.name if existing else None, anchor_type=view_type,
                             parent_id=room or (existing.parent_id if existing else None),
                             frame={**(existing.frame if existing else {}), "camera_view": device_id})
            keep = {r.id for r in regions}
            for anchor in self.repo.list_anchors():  # regions dropped from this camera lose calibration only
                if anchor.frame.get("camera") == device_id and anchor.id not in keep:
                    anchor.frame = {k: v for k, v in anchor.frame.items() if k not in ("camera", "bbox")}
                    self.repo.save_anchor(anchor)
            for r in regions:
                if r.id == view:
                    raise ValueError("a region cannot be the camera's whole view")
                anchors.register(r.id, at, name=r.name or r.id, anchor_type="region", parent_id=view,
                                 frame={"camera": device_id, "bbox": list(r.bbox)})
        return self.config(device_id)

    def config(self, device_id: str) -> CameraConfig:
        regions = [
            RegionSpec(id=a.id, name=a.name, bbox=tuple(a.frame["bbox"]))
            for a in self.repo.list_anchors()
            if a.frame.get("camera") == device_id and "bbox" in a.frame
        ]
        view = next((a.id for a in self.repo.list_anchors() if a.frame.get("camera_view") == device_id), None)
        return CameraConfig(device_id=device_id, view=view, regions=sorted(regions, key=lambda r: r.id))

    def _provider(self, cfg: CameraConfig, detections: List[Detection]) -> Tuple[DetectionPerceptionProvider, int]:
        kept = [d for d in detections if d.label.lower() not in EXCLUDED_LABELS and d.confidence >= self.min_confidence]
        region_map: Dict[str, Tuple[float, float, float, float]] = {r.id: r.bbox for r in cfg.regions}
        provider = DetectionPerceptionProvider(lambda _f: kept, region_map, self.min_confidence, MODEL_ID)
        return provider, len(detections) - len(kept)

    def _require(self, device_id: str) -> CameraConfig:
        cfg = self.config(device_id)
        if cfg.view is None:
            raise CameraNotCalibrated(f"camera {device_id} has no calibrated view yet")
        return cfg

    # ----------------------------------------------------------- snapshots
    def snapshot(self, device_id: str, detections: List[Detection], at: datetime,
                 session_id: Optional[str] = None, lighting: Optional[str] = None) -> SnapshotResult:
        """What the camera currently sees, as one observation of its whole view."""
        cfg = self._require(device_id)
        provider, dropped = self._provider(cfg, detections)
        frame = RawFrame(device_id=device_id, timestamp=at, session_id=session_id, field_of_view=cfg.view, lighting=lighting)
        observation, events = PerceptionGateway(self.repo, self.engine).ingest(frame, provider)
        seen = [
            SeenEntity(
                entity_id=r.entity_id,
                label=observation.observed_entities[r.observed_index].type,
                location=observation.observed_entities[r.observed_index].location,
                method=r.method.value,
                confidence=observation.observed_entities[r.observed_index].confidence,
            )
            for r in observation.resolutions
        ]
        return SnapshotResult(observation_id=observation.id, events=events, seen=seen, dropped=dropped)

    def scan(self, device_id: str, region: str, detections: List[Detection], at: datetime,
             session_id: Optional[str] = None, coverage: float = 0.95, lighting: Optional[str] = None) -> SearchCoverage:
        """A deliberate look at one region: everything ORBIT believes is there but the
        camera does not see becomes a candidate for coverage-validated absence."""
        cfg = self._require(device_id)
        if region not in {r.id for r in cfg.regions} and region != cfg.view:
            raise ValueError(f"{region} is not calibrated for camera {device_id}")
        anchors = self.engine.anchors
        targets = []
        for e in self.repo.list_entities():
            if e.merged_into:
                continue
            a = self.engine.claims.assess_attribute(e.id, LOCATION, at)
            if a.has_current_claim and anchors.is_within(a.last_known_value, region):
                targets.append(e.id)
        provider, _ = self._provider(cfg, detections)
        frame = RawFrame(device_id=device_id, timestamp=at, session_id=session_id, field_of_view=region, lighting=lighting)
        observation = provider.perceive(frame)
        observation.observed_entities = [d for d in observation.observed_entities if anchors.is_within(d.location, region)]
        return self.search.record_search(
            region, at, targets, observation=observation, coverage_fraction=coverage,
            visibility_conditions={"lighting": lighting} if lighting else {}, source=device_id, session_id=session_id,
        )
