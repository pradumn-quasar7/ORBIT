from fastapi import Request

from backend.app.core.container import OrbitServices


def get_services(request: Request) -> OrbitServices:
    return request.app.state.orbit
