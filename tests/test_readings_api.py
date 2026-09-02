"""Integration tests for POST readings and history API endpoints.

Validates PRD §4 critical path:
- Full synchronous ingress loop (readings -> SRI -> fan_command -> persistence)
- History queries with time and count filters
- Commodities listing and health check
"""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_reading_critical_path(sample_device_setup, seeded_db, async_client: AsyncClient):
    """PRD §4: POST /devices/{device_id}/readings critical path executes all steps in one request."""
    device_id = "shelf-01"
    now = datetime.now(timezone.utc)
    payload = {
        "device_seq": 1,
        "device_timestamp": now.isoformat(),
        "temp_c": 28.0,
        "humidity_pct": 75.0,
        "gas_raw": 220.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["device_id"] == device_id
    assert "reading_id" in data
    assert data["fan_command"] in ["on", "off"]
    assert 0.0 <= data["spoilage_index"] <= 1.0
    assert data["sensor_status"] == "ok"

    # Verify document was saved in readings collection
    reading_doc = await seeded_db["readings"].find_one({"reading_id": data["reading_id"]})
    assert reading_doc is not None
    assert reading_doc["device_id"] == device_id
    assert reading_doc["temp_c"] == 28.0
    assert reading_doc["humidity_pct"] == 75.0
    assert reading_doc["gas_raw"] == 220.0
    assert reading_doc["spoilage_index"] == data["spoilage_index"]


@pytest.mark.asyncio
async def test_reading_history_endpoint(sample_device_setup, async_client: AsyncClient):
    """PRD §4: GET /devices/{device_id}/history returns readings in reverse chronological order."""
    device_id = "shelf-01"
    base_time = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)

    # Push 3 readings with explicit timestamps
    for seq in range(1, 4):
        payload = {
            "device_seq": seq,
            "device_timestamp": (base_time + timedelta(minutes=seq)).isoformat(),
            "temp_c": 22.0 + seq,
            "humidity_pct": 90.0,
            "gas_raw": 100.0 + (seq * 10),
        }
        resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
        assert resp.status_code == 200

    # Query history
    hist_resp = await async_client.get(f"/devices/{device_id}/history?limit=10")
    assert hist_resp.status_code == 200
    readings = hist_resp.json()
    assert len(readings) == 3
    # Check reverse chronological order (seq 3 first)
    assert readings[0]["device_seq"] == 3
    assert readings[1]["device_seq"] == 2
    assert readings[2]["device_seq"] == 1


@pytest.mark.asyncio
async def test_commodities_endpoints(seeded_db, async_client: AsyncClient):
    """PRD §4: GET /commodities and GET /commodities/{type}."""
    resp = await async_client.get("/commodities")
    assert resp.status_code == 200
    commodities = resp.json()
    assert len(commodities) == 4
    c_names = {c["commodity_type"] for c in commodities}
    assert c_names == {"tomato", "onion", "potato", "leafy_greens"}

    # Fetch tomato details
    tomato_resp = await async_client.get("/commodities/tomato")
    assert tomato_resp.status_code == 200
    tomato_data = tomato_resp.json()
    assert tomato_data["commodity_type"] == "tomato"
    assert tomato_data["optimal_temp_min"] == 13.0
    assert tomato_data["optimal_temp_max"] == 21.0
    assert tomato_data["chilling_threshold_c"] == 13.0


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient):
    """PRD §4: GET /health returns ok status."""
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
