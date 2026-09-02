"""Pytest fixtures and test setup for Smart Shelf backend.

Uses mongomock-motor for in-memory async MongoDB testing.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
import httpx
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

from app.db import db_manager, get_database, init_db_indexes
from app.main import create_app
from scripts.seed_commodity_profiles import seed_commodity_profiles


@pytest_asyncio.fixture(scope="function")
async def mock_db():
    """Create an isolated in-memory mock database for each test."""
    client = AsyncMongoMockClient()
    db = client["test_smart_shelf"]
    await init_db_indexes(db)
    db_manager.set_db(db)
    yield db
    db_manager.set_db(None)


@pytest_asyncio.fixture(scope="function")
async def seeded_db(mock_db):
    """Provide a mock database pre-seeded with commodity profiles from JSON."""
    json_path = Path(__file__).resolve().parent.parent / "commodity-profiles.json"
    await seed_commodity_profiles(json_path=json_path, db_manager_instance=db_manager)
    return mock_db


@pytest_asyncio.fixture(scope="function")
async def sample_device_setup(seeded_db):
    """Seed a device 'shelf-01' with an active assignment to 'tomato' and calibration baseline."""
    now = datetime.now(timezone.utc)

    # Register device
    await seeded_db["devices"].insert_one(
        {
            "device_id": "shelf-01",
            "location": "kirana-store-A",
            "installed_at": now,
        }
    )

    # Active assignment: tomato
    await seeded_db["device_assignments"].insert_one(
        {
            "assignment_id": "asg-001",
            "device_id": "shelf-01",
            "commodity_type": "tomato",
            "start_at": now,
            "end_at": None,
        }
    )

    # Calibration: baseline 100.0
    await seeded_db["device_calibration"].insert_one(
        {
            "calibration_id": "cal-001",
            "device_id": "shelf-01",
            "mq135_baseline": 100.0,
            "effective_from": now,
        }
    )

    return {"device_id": "shelf-01", "commodity_type": "tomato", "baseline": 100.0}


@pytest_asyncio.fixture(scope="function")
async def async_client(mock_db) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an async test HTTP client wired to the FastAPI app."""
    app = create_app()
    app.dependency_overrides[get_database] = lambda: mock_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
