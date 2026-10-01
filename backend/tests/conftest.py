from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.clock import FixedClock
from backend.app.main import create_app
from backend.app.repositories.in_memory_repository import InMemoryRepository
from backend.app.repositories.sql import SqlRepository


@pytest.fixture(params=["memory", "sql"])
def repo(request):
    """Every engine behaviour must hold for both storage implementations."""
    if request.param == "memory":
        return InMemoryRepository()
    return SqlRepository("sqlite://", create_schema=True)


@pytest.fixture
def clock():
    return FixedClock(datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc))


@pytest.fixture
def client(repo, clock):
    return TestClient(create_app(repository=repo, clock=clock))
