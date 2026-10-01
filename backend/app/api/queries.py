"""Grounded query endpoint (spec §15 response contract) and recall-only memory search."""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.app.api.deps import get_services
from backend.app.core.container import OrbitServices
from backend.app.core.time import UTCDateTime
from backend.app.domain.models import GroundedResponse, RetrievalHit

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    at: Optional[UTCDateTime] = None


@router.post("/queries", response_model=GroundedResponse)
def ask(body: QueryRequest, svc: OrbitServices = Depends(get_services)):
    return svc.agent.answer(body.query, body.at or svc.clock.now())


@router.get("/memory/search", response_model=List[RetrievalHit])
def memory_search(q: str, k: int = 8, svc: OrbitServices = Depends(get_services)):
    """Semantic recall over memory records. Scores are recall aids, not evidence."""
    return svc.agent.retriever.recall(q, k=k)
