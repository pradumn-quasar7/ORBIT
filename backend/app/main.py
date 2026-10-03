from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api import actions, counterfactual, evidence, identity, inspect, memory, queries, spatial, world
from backend.app.core.clock import Clock
from backend.app.core.container import OrbitServices, default_repository
from backend.app.repositories.base import Repository

VERSION = "0.1.0"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


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
    app.include_router(memory.router)
    app.include_router(queries.router)
    app.include_router(actions.router)
    app.include_router(inspect.router)
    app.include_router(counterfactual.router)
    app.include_router(identity.router)

    if FRONTEND.is_dir():
        app.mount("/ui", StaticFiles(directory=FRONTEND, html=True), name="ui")

        @app.get("/dashboard", include_in_schema=False)
        def dashboard():
            return RedirectResponse("/ui/")

    return app


def __getattr__(name: str):
    # `uvicorn backend.app.main:app` builds the default (SQL-backed) app lazily, so
    # importing this module (e.g. in tests) has no database side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
