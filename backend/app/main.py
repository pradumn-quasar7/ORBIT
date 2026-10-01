from typing import Optional

from fastapi import FastAPI

from backend.app.api import evidence, spatial, world
from backend.app.core.clock import Clock
from backend.app.core.container import OrbitServices, default_repository
from backend.app.repositories.base import Repository

VERSION = "0.1.0"


def create_app(repository: Optional[Repository] = None, clock: Optional[Clock] = None) -> FastAPI:
    app = FastAPI(title="ORBIT World Model API", version=VERSION)
    app.state.orbit = OrbitServices.build(repository or default_repository(), clock)

    @app.get("/")
    def health_check():
        return {
            "status": "online",
            "system": "ORBIT — Persistent World Model Agent",
            "version": VERSION,
            "entities_count": len(app.state.orbit.repo.list_entities()),
        }

    app.include_router(world.router)
    app.include_router(spatial.router)
    app.include_router(evidence.router)
    return app


def __getattr__(name: str):
    # `uvicorn backend.app.main:app` builds the default (SQL-backed) app lazily, so
    # importing this module (e.g. in tests) has no database side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
