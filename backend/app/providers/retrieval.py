"""In-memory cosine-similarity index. A pgvector-backed provider can replace it."""
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from backend.app.domain.models import RetrievalHit
from backend.app.providers.base import EmbeddingProvider, MemoryDocument, RetrievalProvider


class InMemoryVectorIndex(RetrievalProvider):
    name = "in-memory-cosine-v1"

    def __init__(self, embedding: EmbeddingProvider):
        self.embedding = embedding
        self._docs: Dict[str, MemoryDocument] = {}
        self._vectors: Dict[str, List[float]] = {}

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._docs

    def index(self, docs: Sequence[MemoryDocument]) -> None:
        fresh = [d for d in docs if d.doc_id not in self._docs]
        for doc, vec in zip(fresh, self.embedding.embed([d.text for d in fresh])):
            self._docs[doc.doc_id] = doc
            self._vectors[doc.doc_id] = vec

    def search(
        self,
        query: str,
        k: int = 5,
        entity_ids: Optional[Sequence[str]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[RetrievalHit]:
        [q] = self.embedding.embed([query])
        wanted = set(entity_ids or [])
        scored = []
        for doc_id, doc in self._docs.items():
            if wanted and not wanted & set(doc.entity_ids):
                continue
            if doc.timestamp is not None and ((since and doc.timestamp < since) or (until and doc.timestamp > until)):
                continue
            score = sum(a * b for a, b in zip(q, self._vectors[doc_id]))
            scored.append((score, doc.timestamp.isoformat() if doc.timestamp else "", doc_id))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [
            RetrievalHit(
                doc_id=doc_id,
                kind=self._docs[doc_id].kind,
                ref_id=self._docs[doc_id].ref_id,
                text=self._docs[doc_id].text,
                score=round(score, 4),
                entity_ids=self._docs[doc_id].entity_ids,
                timestamp=self._docs[doc_id].timestamp,
            )
            for score, _, doc_id in scored[:k]
        ]
