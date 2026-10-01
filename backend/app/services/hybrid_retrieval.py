"""Hybrid retrieval (spec §8): semantic recall finds candidate memories; structured
state decides what is true. Indexed documents always point back at a structured
record (event, task, hypothesis) that the agent re-reads before using it."""
from datetime import datetime
from typing import List, Optional, Sequence

from backend.app.domain.models import RetrievalHit
from backend.app.providers.base import MemoryDocument, RetrievalProvider
from backend.app.repositories.base import Repository


def _humanise(event_type: str) -> str:
    return event_type.lower().replace("_", " ")


class HybridRetriever:
    def __init__(self, repository: Repository, retrieval: RetrievalProvider):
        self.repo = repository
        self.retrieval = retrieval

    def refresh(self) -> None:
        """Index records not yet in the semantic index (append-only store ⇒ incremental)."""
        docs: List[MemoryDocument] = []
        names = {e.id: (e.name or e.type, e.type) for e in self.repo.list_entities()}
        for e in self.repo.list_events():
            doc_id = f"event:{e.id}"
            if doc_id in self.retrieval:
                continue
            ids = [x for x in (e.entity_id, e.task_id) if x]
            label = " ".join(" ".join(names.get(x, (x, ""))) for x in ids)
            docs.append(
                MemoryDocument(
                    doc_id=doc_id,
                    kind="event",
                    ref_id=e.id,
                    text=f"{_humanise(e.event_type.value)} {label} {e.description or ''}",
                    entity_ids=ids,
                    timestamp=e.timestamp,
                )
            )
        for t in self.repo.list_tasks():
            if f"task:{t.id}" not in self.retrieval:
                text = f"task {t.goal} " + " ".join(s.description for s in t.steps)
                entity_ids = sorted({c.entity_id for s in t.steps for c in s.preconditions + s.postconditions})
                docs.append(MemoryDocument(f"task:{t.id}", "task", t.id, text, [t.id] + entity_ids, t.created_at))
        for h in self.repo.list_hypotheses():
            if f"hypothesis:{h.id}" not in self.retrieval:
                docs.append(MemoryDocument(f"hypothesis:{h.id}", "hypothesis", h.id, f"hypothesis cause {h.statement}", h.entity_ids, h.created_at))
        self.retrieval.index(docs)

    def recall(
        self,
        query: str,
        entity_ids: Optional[Sequence[str]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        k: int = 8,
    ) -> List[RetrievalHit]:
        self.refresh()
        return self.retrieval.search(query, k=k, entity_ids=entity_ids, since=since, until=until)
