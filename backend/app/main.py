from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.app.domain.models import (
    Entity,
    Observation,
    Evidence,
    StateVersion,
    Event,
    WorldDiff,
)
from backend.app.services.world_state_engine import WorldStateEngine
from backend.app.repositories.in_memory_repository import InMemoryRepository

app = FastAPI(title="ORBIT World Model API", version="0.1.0")

# Shared repository and engine instance
repo = InMemoryRepository()
engine = WorldStateEngine(repository=repo)

class DiffRequest(BaseModel):
    baseline_timestamp: Optional[datetime] = None
    target_timestamp: Optional[datetime] = None

class FreshnessCheckRequest(BaseModel):
    as_of: Optional[datetime] = None

@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "ORBIT — Persistent World Model Agent",
        "version": "0.1.0",
        "entities_count": len(repo.list_entities()),
    }

@app.post("/entities", response_model=Entity)
def create_entity(entity: Entity):
    return repo.save_entity(entity)

@app.get("/entities", response_model=List[Entity])
def list_entities():
    return repo.list_entities()

@app.get("/entities/{entity_id}", response_model=Entity)
def get_entity(entity_id: str):
    entity = repo.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity

@app.get("/entities/{entity_id}/history", response_model=List[StateVersion])
def get_entity_history(entity_id: str, attribute: Optional[str] = None):
    return repo.get_state_versions_for_entity(entity_id, attribute=attribute)

@app.post("/observations", response_model=List[Event])
def record_observation(obs: Observation):
    return engine.record_observation(obs)

@app.get("/observations/{observation_id}", response_model=Observation)
def get_observation(observation_id: str):
    obs = repo.get_observation(observation_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    return obs

@app.get("/events", response_model=List[Event])
def list_events():
    return repo.list_events()

@app.post("/world/diff", response_model=WorldDiff)
def compute_world_diff(req: DiffRequest):
    target_time = req.target_timestamp or datetime.now(timezone.utc)
    return engine.compute_world_diff(
        baseline_timestamp=req.baseline_timestamp,
        target_timestamp=target_time,
    )

@app.post("/entities/{entity_id}/freshness")
def check_freshness(entity_id: str, req: FreshnessCheckRequest):
    as_of = req.as_of or datetime.now(timezone.utc)
    try:
        statuses = engine.evaluate_freshness(entity_id, as_of=as_of)
        return {"entity_id": entity_id, "attribute_statuses": statuses}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
