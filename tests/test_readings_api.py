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
    assert 0.0 <= data["sri"] <= 1.0
    assert data["sensor_status"] == "ok"

    # Verify document was saved in readings collection
    reading_doc = await seeded_db["readings"].find_one({"reading_id": data["reading_id"]})
    assert reading_doc is not None
    assert reading_doc["device_id"] == device_id
    assert reading_doc["temp_c"] == 28.0
    assert reading_doc["humidity_pct"] == 75.0
    assert reading_doc["gas_raw"] == 220.0
    assert reading_doc["sri"] == data["sri"]


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
async def test_reading_history_utc_timezone_fidelity(sample_device_setup, async_client: AsyncClient):
    """Regression test: device_timestamp in GET /history must serialize with UTC timezone ('Z' / offset).

    Verifies that the returned JSON timestamp string round-trips to the exact same UTC instant
    when parsed by ISO-8601 parsers (e.g. JavaScript new Date(...) in the browser),
    preventing timezone offset misalignment between history and forecast.
    """
    device_id = "shelf-01"
    original_utc = datetime(2026, 9, 3, 5, 50, 0, tzinfo=timezone.utc)

    payload = {
        "device_seq": 101,
        "device_timestamp": original_utc.isoformat(),
        "temp_c": 24.5,
        "humidity_pct": 89.0,
        "gas_raw": 120.0,
    }

    # Ingress reading
    post_res = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    assert post_res.status_code == 200

    # Fetch history
    hist_res = await async_client.get(f"/devices/{device_id}/history?limit=1")
    assert hist_res.status_code == 200
    readings = hist_res.json()
    assert len(readings) == 1

    raw_ts_str = readings[0]["device_timestamp"]
    raw_rx_str = readings[0]["server_received_at"]

    # Must end with 'Z' or '+00:00' to denote UTC explicitly
    assert raw_ts_str.endswith("Z") or "+00:00" in raw_ts_str
    assert raw_rx_str.endswith("Z") or "+00:00" in raw_rx_str

    # Parse using fromisoformat and assert exact instant equality
    parsed_dt = datetime.fromisoformat(raw_ts_str)
    assert parsed_dt.tzinfo is not None
    assert parsed_dt == original_utc


@pytest.mark.asyncio
async def test_post_reading_no_timestamp_is_server_stamped(sample_device_setup, seeded_db, async_client: AsyncClient):
    """Firmware contract: an ESP32 with no RTC omits device_timestamp; the server stamps it."""
    device_id = "shelf-01"
    before = datetime.now(timezone.utc)
    payload = {
        "device_seq": 1,
        "temp_c": 24.0,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    after = datetime.now(timezone.utc)

    assert resp.status_code == 200
    data = resp.json()
    assert data["fan_command"] in ["on", "off"]

    reading_doc = await seeded_db["readings"].find_one({"reading_id": data["reading_id"]})
    stamped_ts = reading_doc["device_timestamp"]
    if stamped_ts.tzinfo is None:
        stamped_ts = stamped_ts.replace(tzinfo=timezone.utc)
    assert before <= stamped_ts <= after


@pytest.mark.asyncio
async def test_post_reading_rejects_nan_temp(sample_device_setup, async_client: AsyncClient):
    """Firmware contract: a non-finite temp_c (e.g. a bare NaN token in the body) is a clean 422.

    Sent as raw bytes because httpx's `json=` helper refuses to serialize NaN itself
    (allow_nan=False) -- we need the literal bare `NaN` token on the wire, which is
    what stdlib json.loads (used by Starlette's Request.json()) accepts as a
    non-standard extension.
    """
    device_id = "shelf-01"
    raw_body = (
        b'{"device_seq": 1, "temp_c": NaN, "humidity_pct": 80.0, '
        b'"gas_raw": 110.0, "sensor_status": "dht22_error"}'
    )

    resp = await async_client.post(
        f"/devices/{device_id}/readings",
        content=raw_body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_reading_rejects_null_temp(sample_device_setup, async_client: AsyncClient):
    """ArduinoJson serializes a NaN float as JSON null, not a bare NaN token; that must 422 too."""
    device_id = "shelf-01"
    payload = {
        "device_seq": 1,
        "temp_c": None,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "dht22_error",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_reading_epoch_device_timestamp_replaced_with_server_time(
    sample_device_setup, seeded_db, async_client: AsyncClient
):
    """A device_timestamp before DEVICE_TIMESTAMP_FLOOR is an unset clock and is replaced."""
    device_id = "shelf-01"
    epoch_1970 = datetime(1970, 1, 1, tzinfo=timezone.utc)
    before = datetime.now(timezone.utc)
    payload = {
        "device_seq": 1,
        "device_timestamp": epoch_1970.isoformat(),
        "temp_c": 24.0,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    after = datetime.now(timezone.utc)

    assert resp.status_code == 200
    reading_doc = await seeded_db["readings"].find_one({"reading_id": resp.json()["reading_id"]})
    stamped_ts = reading_doc["device_timestamp"]
    if stamped_ts.tzinfo is None:
        stamped_ts = stamped_ts.replace(tzinfo=timezone.utc)
    assert stamped_ts != epoch_1970
    assert before <= stamped_ts <= after


@pytest.mark.asyncio
async def test_post_reading_future_device_timestamp_replaced_with_server_time(
    sample_device_setup, seeded_db, async_client: AsyncClient
):
    """A device_timestamp more than DEVICE_CLOCK_FORWARD_SKEW_LIMIT ahead of now is replaced."""
    device_id = "shelf-01"
    future_ts = datetime.now(timezone.utc) + timedelta(minutes=10)
    before = datetime.now(timezone.utc)
    payload = {
        "device_seq": 1,
        "device_timestamp": future_ts.isoformat(),
        "temp_c": 24.0,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    after = datetime.now(timezone.utc)

    assert resp.status_code == 200
    reading_doc = await seeded_db["readings"].find_one({"reading_id": resp.json()["reading_id"]})
    stamped_ts = reading_doc["device_timestamp"]
    if stamped_ts.tzinfo is None:
        stamped_ts = stamped_ts.replace(tzinfo=timezone.utc)
    assert stamped_ts != future_ts
    assert before <= stamped_ts <= after


@pytest.mark.asyncio
async def test_post_reading_past_device_timestamp_preserved(
    sample_device_setup, seeded_db, async_client: AsyncClient
):
    """A plausible past device_timestamp (buffered/replayed reading, PRD.md:123) passes through unchanged."""
    device_id = "shelf-01"
    # microsecond=0: BSON datetimes are millisecond-precision, so a value with
    # microsecond fidelity would spuriously fail equality after the DB round-trip.
    past_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
    payload = {
        "device_seq": 1,
        "device_timestamp": past_ts.isoformat(),
        "temp_c": 24.0,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    assert resp.status_code == 200

    reading_doc = await seeded_db["readings"].find_one({"reading_id": resp.json()["reading_id"]})
    stamped_ts = reading_doc["device_timestamp"]
    if stamped_ts.tzinfo is None:
        stamped_ts = stamped_ts.replace(tzinfo=timezone.utc)
    assert stamped_ts == past_ts


@pytest.mark.asyncio
async def test_post_reading_response_shape_matches_firmware_contract(
    sample_device_setup, async_client: AsyncClient
):
    """Pins the exact ReadingResponse field set so refactors can't silently break the device wire protocol."""
    device_id = "shelf-01"
    payload = {
        "device_seq": 1,
        "temp_c": 24.0,
        "humidity_pct": 80.0,
        "gas_raw": 110.0,
        "sensor_status": "ok",
    }

    resp = await async_client.post(f"/devices/{device_id}/readings", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {
        "reading_id",
        "device_id",
        "fan_command",
        "sri",
        "interlock_triggered",
        "gas_override_triggered",
        "sensor_status",
    }


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
