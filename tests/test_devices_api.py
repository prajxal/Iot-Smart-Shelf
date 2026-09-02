"""Integration tests for device management and assignment endpoints.

Validates PRD §3.3, §3.7.2, and §4:
- Assignment overlap invariant (closing prior open assignment)
- Rejection of unknown commodity types
- Device registration, calibration, status, and alert queries
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_assignment_overlap_invariant(sample_device_setup, seeded_db, async_client: AsyncClient):
    """PRD §3.3 & §3.7.2: Reassigning a shelf must close prior open assignment.

    There must never be two open (end_at: null) assignments for the same device.
    """
    device_id = "shelf-01"

    # Verify initial assignment is tomato (end_at is None)
    resp_init = await async_client.get(f"/devices/{device_id}/assignment")
    assert resp_init.status_code == 200
    assert resp_init.json()["commodity_type"] == "tomato"
    assert resp_init.json()["end_at"] is None

    # Reassign device to 'onion'
    reassign_payload = {"commodity_type": "onion"}
    resp_reassign = await async_client.put(f"/devices/{device_id}/assignment", json=reassign_payload)
    assert resp_reassign.status_code == 200
    new_asg = resp_reassign.json()
    assert new_asg["commodity_type"] == "onion"
    assert new_asg["end_at"] is None

    # Check MongoDB directly: exactly ONE assignment with end_at: None
    open_assignments = await seeded_db["device_assignments"].find({"device_id": device_id, "end_at": None}).to_list(10)
    assert len(open_assignments) == 1
    assert open_assignments[0]["commodity_type"] == "onion"

    # Prior tomato assignment must now have end_at set
    closed_assignments = await seeded_db["device_assignments"].find(
        {"device_id": device_id, "end_at": {"$ne": None}}
    ).to_list(10)
    assert len(closed_assignments) == 1
    assert closed_assignments[0]["commodity_type"] == "tomato"
    assert closed_assignments[0]["end_at"] is not None


@pytest.mark.asyncio
async def test_reassign_rejects_unknown_commodity(sample_device_setup, async_client: AsyncClient):
    """PRD §4: PUT /devices/{id}/assignment rejects unknown commodity_type not in commodity_profiles."""
    device_id = "shelf-01"
    payload = {"commodity_type": "nonexistent_fruit"}
    resp = await async_client.put(f"/devices/{device_id}/assignment", json=payload)
    assert resp.status_code == 400
    assert "does not exist in commodity_profiles" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_device_status_endpoint(sample_device_setup, async_client: AsyncClient):
    """PRD §4: GET /devices/{device_id}/status returns active commodity and latest reading."""
    device_id = "shelf-01"

    # Push a reading first
    reading_payload = {
        "device_seq": 101,
        "temp_c": 25.0,
        "humidity_pct": 85.0,
        "gas_raw": 150.0,
    }
    post_resp = await async_client.post(f"/devices/{device_id}/readings", json=reading_payload)
    assert post_resp.status_code == 200

    # Query status
    status_resp = await async_client.get(f"/devices/{device_id}/status")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["device_id"] == device_id
    assert data["active_commodity"] == "tomato"
    assert data["latest_reading"] is not None
    assert data["latest_reading"]["device_seq"] == 101
    assert "spoilage_index" in data
    assert "fan_command" in data


@pytest.mark.asyncio
async def test_device_lifecycle_and_calibration(seeded_db, async_client: AsyncClient):
    """Test device registration and calibration baseline creation."""
    new_device = {"device_id": "shelf-02", "location": "kirana-store-B"}
    reg_resp = await async_client.post("/devices", json=new_device)
    assert reg_resp.status_code == 201
    assert reg_resp.json()["device_id"] == "shelf-02"

    # Add calibration
    cal_payload = {"mq135_baseline": 88.5}
    cal_resp = await async_client.post("/devices/shelf-02/calibration", json=cal_payload)
    assert cal_resp.status_code == 201
    assert cal_resp.json()["mq135_baseline"] == 88.5

    # Get calibration
    get_cal = await async_client.get("/devices/shelf-02/calibration")
    assert get_cal.status_code == 200
    assert get_cal.json()["mq135_baseline"] == 88.5
