"""Anchor hierarchy: the spatial reference frames entity locations are relative to.

ORBIT is open-world: a location string that is not a registered anchor is still
accepted (it simply has no known parent), so perception never has to be blocked on
workspace configuration.
"""
from datetime import datetime
from typing import List, Optional

from backend.app.domain.models import Anchor
from backend.app.repositories.base import Repository


class AnchorCycleError(ValueError):
    pass


class AnchorRegistry:
    def __init__(self, repository: Repository):
        self.repo = repository

    def register(
        self,
        anchor_id: str,
        at: datetime,
        name: Optional[str] = None,
        anchor_type: str = "region",
        parent_id: Optional[str] = None,
        frame: Optional[dict] = None,
    ) -> Anchor:
        if parent_id is not None and anchor_id in self.lineage(parent_id):
            raise AnchorCycleError(f"Anchor {anchor_id} cannot be its own ancestor")
        existing = self.repo.get_anchor(anchor_id)
        anchor = Anchor(
            id=anchor_id,
            name=name or (existing.name if existing else anchor_id),
            anchor_type=anchor_type,
            parent_id=parent_id,
            frame=frame or {},
            created_at=existing.created_at if existing else at,
        )
        return self.repo.save_anchor(anchor)

    def lineage(self, anchor_id: str) -> List[str]:
        """[anchor, parent, grandparent, ...]; unknown anchors are their own root."""
        chain: List[str] = []
        current: Optional[str] = anchor_id
        while current is not None and current not in chain:
            chain.append(current)
            anchor = self.repo.get_anchor(current)
            current = anchor.parent_id if anchor else None
        return chain

    def is_within(self, anchor_id: Optional[str], region_id: str) -> bool:
        return anchor_id is not None and region_id in self.lineage(anchor_id)

    def descendants(self, region_id: str) -> List[str]:
        """The region and every anchor nested under it."""
        children: dict = {}
        for a in self.repo.list_anchors():
            children.setdefault(a.parent_id, []).append(a.id)
        out, stack = [], [region_id]
        while stack:
            node = stack.pop()
            if node in out:
                continue
            out.append(node)
            stack.extend(children.get(node, []))
        return out

    def proximity(self, a: Optional[str], b: Optional[str]) -> float:
        """1.0 same anchor, 0.8 nested, 0.6 sibling regions of one surface, 0.3 same room
        (or other shared ancestor), 0.0 unrelated/unknown. Two surfaces in a room are
        separate places, not "near" each other."""
        if a is None or b is None:
            return 0.0
        if a == b:
            return 1.0
        la, lb = self.lineage(a), self.lineage(b)
        if a in lb or b in la:
            return 0.8
        if len(la) > 1 and len(lb) > 1 and la[1] == lb[1]:
            parent = self.repo.get_anchor(la[1])
            if parent is None or parent.anchor_type != "room":
                return 0.6
            return 0.3
        if set(la) & set(lb):
            return 0.3
        return 0.0
