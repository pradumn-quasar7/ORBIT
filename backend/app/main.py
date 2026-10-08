from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api import actions, assistant, camera, counterfactual, evidence, identity, inspect, live, memory, queries, realtime, speech, spatial, world, xr
from backend.app.core.clock import Clock
from backend.app.core.container import OrbitServices, default_repository
from backend.app.core.pairing import Pairing
from backend.app.repositories.base import Repository

VERSION = "0.1.0"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class RevalidateUI:
    """Static UI files carry ``Cache-Control: no-cache``: browsers keep using their cache
    but check the ETag first, so an update is never masked by a stale script. Plain ASGI
    (not BaseHTTPMiddleware) so the /stream responses are untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/ui"):
            return await self.app(scope, receive, send)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": headers + [(b"cache-control", b"no-cache")]}
            await send(message)

        await self.app(scope, receive, send_with_header)


def create_app(repository: Optional[Repository] = None, clock: Optional[Clock] = None,
               pair_code: Optional[str] = None, lan_url: Optional[str] = None) -> FastAPI:
    """``pair_code`` (Phase 18) requires devices on the network to pair before using the
    API; ``lan_url`` is the address they use (shown to the laptop user)."""
    app = FastAPI(title="ORBIT World Model API", version=VERSION)
    app.state.lan = {"code": pair_code, "url": lan_url}
    app.state.orbit = OrbitServices.build(repository or default_repository(), clock, realtime=True)

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
    app.include_router(camera.router)
    app.include_router(assistant.router)
    app.include_router(realtime.router)
    app.include_router(xr.router)
    app.include_router(speech.router)
    app.include_router(live.router)

    if FRONTEND.is_dir():
        app.mount("/ui", StaticFiles(directory=FRONTEND, html=True), name="ui")
        app.add_middleware(RevalidateUI)

        @app.get("/dashboard", include_in_schema=False)
        def dashboard():
            return RedirectResponse("/ui/")

    app.add_middleware(Pairing, code=pair_code)  # outermost: unpaired network devices stop here
    return app


def __getattr__(name: str):
    # `uvicorn backend.app.main:app` builds the default (SQL-backed) app lazily, so
    # importing this module (e.g. in tests) has no database side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
