"""Mixed-reality client endpoints (Phase 18): ORBIT places pinned to the real room.

A headset pins an ORBIT anchor (``bench_3``) to a spot in the room with a WebXR
*persistent anchor*; the opaque handle it gets back is stored in the anchor's frame
(``frame["xr"]``), like a camera region's image box (ADR-039). Each place is served
with what ORBIT believes is there — status, freshness, conflicts — so the label floating
over the real bench says exactly what the evidence supports, nothing more.
"""
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.pairing import LOOPBACK
from backend.app.core.time import UTCDateTime

router = APIRouter()


class PlaceItem(BaseModel):
    entity_id: str
    name: str
    type: str
    status: str
    value: Any = None  # the supportable location; None when ORBIT cannot assert one
    last_known: Any = None
    freshness: Optional[str] = None
    last_supported_at: Optional[UTCDateTime] = None
    reason: str = ""
    state: dict = Field(default_factory=dict)  # other attributes ORBIT can assert, e.g. {"state": "closed"}


class Place(BaseModel):
    id: str
    name: str
    anchor_type: str
    parent_id: Optional[str] = None
    xr: Optional[dict] = None  # {"handle", "device", "placed_at"} once pinned in a room
    items: List[PlaceItem] = Field(default_factory=list)
    conflicts: int = 0


class PinRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=200)  # WebXR persistent anchor handle
    device: str = Field(default="quest", max_length=64)


def _place(svc: OrbitServices, anchor) -> Place:
    at = svc.clock.now()
    items = []
    conflicts = 0
    for c in svc.memory.contents(anchor.id, at, nested=True):
        e = svc.repo.get_entity(c.entity_id)
        if e is None:
            continue
        a = c.assessment
        conflicts += 1 if a.status.value == "CONTRADICTED" else 0
        state = {}
        for attr in sorted(e.current_state):
            if attr == "location":
                continue
            s = svc.engine.claims.assess_attribute(e.id, attr, at)
            if s.supportable and s.value is not None:
                state[attr] = s.value
            if s.status.value == "CONTRADICTED":
                conflicts += 1
        items.append(PlaceItem(
            entity_id=e.id, name=e.name if e.name and e.name != e.type else e.id, type=e.type,
            status=a.status.value, value=a.value, last_known=a.last_known_value,
            freshness=a.freshness.state.value if a.freshness else None,
            last_supported_at=a.freshness.last_supported_at if a.freshness else None,
            reason=a.reason, state=state,
        ))
    return Place(id=anchor.id, name=anchor.name or anchor.id, anchor_type=anchor.anchor_type, parent_id=anchor.parent_id,
                 xr=anchor.frame.get("xr"), items=items, conflicts=conflicts)


@router.get("/xr/places", response_model=List[Place])
def places(svc: OrbitServices = Depends(get_services)):
    return [_place(svc, a) for a in sorted(svc.repo.list_anchors(), key=lambda a: a.id)]


@router.put("/xr/places/{anchor_id}", response_model=Place)
def pin(anchor_id: str, body: PinRequest, svc: OrbitServices = Depends(get_services)):
    anchor = svc.repo.get_anchor(anchor_id)
    if anchor is None:
        raise HTTPException(status_code=404, detail="Place not found")
    anchor.frame = {**anchor.frame, "xr": {"handle": body.handle, "device": body.device, "placed_at": svc.clock.now().isoformat()}}
    svc.repo.save_anchor(anchor)
    return _place(svc, anchor)


@router.delete("/xr/places/{anchor_id}", response_model=Place)
def unpin(anchor_id: str, svc: OrbitServices = Depends(get_services)):
    anchor = svc.repo.get_anchor(anchor_id)
    if anchor is None:
        raise HTTPException(status_code=404, detail="Place not found")
    anchor.frame = {k: v for k, v in anchor.frame.items() if k != "xr"}
    svc.repo.save_anchor(anchor)
    return _place(svc, anchor)


@router.get("/xr/pairing")
def pairing(request: Request):
    """How to connect a headset. The code is shown only to the laptop itself."""
    lan = request.app.state.lan
    local = (request.client.host if request.client else "") in LOOPBACK
    return {
        "enabled": bool(lan["code"]),
        "url": lan["url"],
        "code": lan["code"] if local else None,
        "secure_context_needed": "WebXR only runs on https:// pages (or localhost)",
    }
