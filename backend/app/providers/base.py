"""Replaceable model boundaries (spec §19, §34; ADR-002).

The world-state and evidence layers never import a concrete provider. Each boundary
ships with a deterministic local default so experiments are reproducible and the
research contribution can be evaluated with a different (e.g. LLM- or
foundation-model-backed) implementation swapped in.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from backend.app.domain.models import QueryIntent, RetrievalHit


class EmbeddingProvider(ABC):
    name: str = "embedding"

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


@dataclass
class MemoryDocument:
    """A unit of semantic recall pointing back at a structured record."""

    doc_id: str
    kind: str  # event | session | task | hypothesis | observation
    ref_id: str
    text: str
    entity_ids: List[str] = field(default_factory=list)
    timestamp: Optional[datetime] = None


class RetrievalProvider(ABC):
    name: str = "retrieval"

    @abstractmethod
    def index(self, docs: Sequence[MemoryDocument]) -> None: ...

    @abstractmethod
    def search(
        self,
        query: str,
        k: int = 5,
        entity_ids: Optional[Sequence[str]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[RetrievalHit]: ...

    @abstractmethod
    def __contains__(self, doc_id: str) -> bool: ...


@dataclass
class Vocabulary:
    """What the reasoning provider may refer to; built from structured state."""

    entities: Dict[str, List[str]]  # entity id -> surface forms (id, name, type, aliases)
    entity_types: Dict[str, str]  # entity id -> type
    anchors: Dict[str, List[str]]  # anchor id -> surface forms
    attributes: List[str]
    tasks: Dict[str, List[str]]  # task id -> surface forms


class ReasoningProvider(ABC):
    """Language understanding and response phrasing only — never a source of truth."""

    name: str = "reasoning"

    @abstractmethod
    def interpret(self, query: str, vocabulary: Vocabulary, now: datetime) -> QueryIntent: ...
