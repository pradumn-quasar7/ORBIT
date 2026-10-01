"""Replay (spec §33): step through stored history frame by frame. Each frame holds the
events at one instant and the belief change they caused since the previous frame."""
from datetime import datetime
from typing import List, Optional

from backend.app.domain.models import ReplayFrame
from backend.app.repositories.base import Repository


class ReplayService:
    def __init__(self, repository: Repository, diff):
        self.repo = repository
        self.diff = diff  # WorldDiffService

    def frames(
        self, start: datetime, end: datetime, entity_id: Optional[str] = None, max_frames: int = 500
    ) -> List[ReplayFrame]:
        events = [
            e for e in self.repo.list_events()
            if start < e.timestamp <= end and (entity_id is None or e.entity_id == entity_id)
        ]
        times = sorted({e.timestamp for e in events})[:max_frames]
        frames, previous = [], start
        for t in times:
            changes = self.diff.diff(previous, t, include_unobserved=False, save=False).changes
            if entity_id is not None:
                changes = [c for c in changes if c.entity_id == entity_id]
            frames.append(ReplayFrame(at=t, events=[e for e in events if e.timestamp == t], changes=changes))
            previous = t
        return frames
