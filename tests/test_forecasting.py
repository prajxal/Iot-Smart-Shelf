"""Unit and integration tests for Spoilage Risk Index (SRI) forecasting.

Tests:
1. Overlap guard: <4 distinct readings populating >=4 buckets via window overlap -> insufficient_data=True.
2. Time-bucket moving average computation with irregular timestamps.
3. Least-squares linear trend slope calculation.
4. Extrapolation formula and 0.0 floor clamping.
5. Integration test for GET /devices/{device_id}/forecast.
"""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient

from app.models.forecast import DeviceForecastResponse
from app.services.forecasting import (
    ForecastingService,
    compute_time_moving_averages,
    fit_linear_trend_slope,
)


def test_distinct_readings_overlap_guard_unit():
    """Unit test: 2 distinct readings populating 6 overlapping buckets must trigger insufficient_data."""
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

    # 2 readings: one at -20 min (t=11:40), one at -5 min (t=11:55)
    # With bucket centers at [-25m, -20m, -15m, -10m, -5m, 0m] and +/-7.5m window:
    # - Reading 1 (11:40) falls into -25m ([11:27.5, 11:42.5]), -20m ([11:32.5, 11:47.5]), -15m ([11:37.5, 11:52.5])
    # - Reading 2 (11:55) falls into -10m ([11:42.5, 11:57.5]), -5m ([11:47.5, 12:02.5]), 0m ([11:52.5, 12:07.5])
    # Total non-empty buckets = 6! But distinct readings = 2.
    readings = [
        {"reading_id": "r1", "device_timestamp": now - timedelta(minutes=20), "spoilage_index": 0.30},
        {"reading_id": "r2", "device_timestamp": now - timedelta(minutes=5), "spoilage_index": 0.50},
    ]

    ma_buckets = compute_time_moving_averages(readings, ref_time=now)
    # Confirm bucket overlap creates 6 buckets
    assert len(ma_buckets) == 6

    # Distinct reading count is only 2
    distinct_readings = {r["reading_id"] for r in readings if r.get("spoilage_index") is not None}
    assert len(distinct_readings) == 2

    # The dual guard in generate_forecast requires distinct_count >= 4
    assert len(distinct_readings) < 4


@pytest.mark.asyncio
async def test_distinct_readings_overlap_guard_in_service(sample_device_setup, seeded_db):
    """Integration test: Service returns insufficient_data=True when <4 distinct readings populate >=4 buckets."""
    service = ForecastingService(seeded_db)
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    device_id = "shelf-01"

    # Insert only 2 distinct readings
    await seeded_db["readings"].insert_many(
        [
            {
                "reading_id": "rd-001",
                "device_id": device_id,
                "device_timestamp": now - timedelta(minutes=20),
                "spoilage_index": 0.30,
            },
            {
                "reading_id": "rd-002",
                "device_id": device_id,
                "device_timestamp": now - timedelta(minutes=5),
                "spoilage_index": 0.50,
            },
        ]
    )

    resp: DeviceForecastResponse = await service.generate_forecast(device_id=device_id, as_of=now)

    # Must be marked insufficient_data because distinct raw count = 2 < 4
    assert resp.insufficient_data is True
    assert resp.forecast is None
    assert resp.trend_slope_per_min is None
    assert resp.commodity == "tomato"
    assert resp.fan_threshold == 0.60
    assert resp.alert_threshold == 0.70


def test_moving_average_time_bucketing():
    """Verify irregular timestamps are correctly aggregated within +/- 7.5 min of bucket centers."""
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

    # Place readings at irregular intervals around center -10m (11:50, window [11:42.5, 11:57.5])
    readings = [
        {"reading_id": "r1", "device_timestamp": now - timedelta(minutes=14), "spoilage_index": 0.40},  # 11:46 -> in -10m
        {"reading_id": "r2", "device_timestamp": now - timedelta(minutes=10), "spoilage_index": 0.50},  # 11:50 -> in -10m
        {"reading_id": "r3", "device_timestamp": now - timedelta(minutes=7), "spoilage_index": 0.60},   # 11:53 -> in -10m
    ]

    buckets = compute_time_moving_averages(readings, ref_time=now)
    # Find bucket at -10m (11:50)
    center_neg_10 = now - timedelta(minutes=10)
    bucket_neg_10 = next((b for b in buckets if b[0] == center_neg_10), None)

    assert bucket_neg_10 is not None
    # Mean of 0.40, 0.50, 0.60 = 0.50
    assert bucket_neg_10[1] == pytest.approx(0.50, abs=1e-5)


def test_least_squares_linear_slope():
    """Test least-squares slope calculation over 4 points with known rate."""
    t0 = datetime(2026, 9, 3, 11, 45, 0, tzinfo=timezone.utc)
    # Rate of +0.002 SRI per minute:
    # t=0 min -> 0.30
    # t=5 min -> 0.31
    # t=10 min -> 0.32
    # t=15 min -> 0.33
    points = [
        (t0, 0.30),
        (t0 + timedelta(minutes=5), 0.31),
        (t0 + timedelta(minutes=10), 0.32),
        (t0 + timedelta(minutes=15), 0.33),
    ]

    slope = fit_linear_trend_slope(points)
    assert slope == pytest.approx(0.002, abs=1e-6)

    # Falling rate of -0.004 per minute
    falling_points = [
        (t0, 0.80),
        (t0 + timedelta(minutes=5), 0.78),
        (t0 + timedelta(minutes=10), 0.76),
        (t0 + timedelta(minutes=15), 0.74),
    ]
    falling_slope = fit_linear_trend_slope(falling_points)
    assert falling_slope == pytest.approx(-0.004, abs=1e-6)


@pytest.mark.asyncio
async def test_extrapolation_and_floor_clamp(sample_device_setup, seeded_db):
    """Test forecast extrapolation with steep falling slope clamps predicted SRI to floor 0.0."""
    service = ForecastingService(seeded_db)
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    device_id = "shelf-01"

    # Insert 5 distinct readings with steep falling SRI (starts at 0.10, drops rapidly)
    # Timestamps at -25m, -20m, -15m, -10m, -5m
    await seeded_db["readings"].insert_many(
        [
            {"reading_id": "rd-01", "device_id": device_id, "device_timestamp": now - timedelta(minutes=25), "spoilage_index": 0.20},
            {"reading_id": "rd-02", "device_id": device_id, "device_timestamp": now - timedelta(minutes=20), "spoilage_index": 0.15},
            {"reading_id": "rd-03", "device_id": device_id, "device_timestamp": now - timedelta(minutes=15), "spoilage_index": 0.10},
            {"reading_id": "rd-04", "device_id": device_id, "device_timestamp": now - timedelta(minutes=10), "spoilage_index": 0.05},
            {"reading_id": "rd-05", "device_id": device_id, "device_timestamp": now - timedelta(minutes=5), "spoilage_index": 0.02},
        ]
    )

    resp = await service.generate_forecast(device_id=device_id, horizon_minutes=60, step_minutes=5, as_of=now)

    assert resp.insufficient_data is False
    assert resp.trend_slope_per_min < 0.0
    assert resp.forecast is not None
    assert len(resp.forecast) == 12  # 60 / 5 = 12 steps

    # Later points must clamp to 0.0, never going negative
    for pt in resp.forecast:
        assert pt.predicted_sri >= 0.0

    last_pt = resp.forecast[-1]
    assert last_pt.predicted_sri == 0.0


@pytest.mark.asyncio
async def test_forecast_api_endpoint(sample_device_setup, seeded_db, async_client: AsyncClient):
    """Test GET /devices/{device_id}/forecast end-to-end integration via HTTP client."""
    now = datetime.now(timezone.utc)
    device_id = "shelf-01"

    # Insert 6 distinct readings over the past 30 minutes with rising SRI
    readings = []
    for i in range(6):
        ts = now - timedelta(minutes=25 - (i * 5))
        readings.append(
            {
                "reading_id": f"rd-api-{i}",
                "device_id": device_id,
                "device_timestamp": ts,
                "temp_c": 24.0,
                "humidity_pct": 70.0,
                "gas_raw": 100.0 + (i * 20.0),
                "spoilage_index": 0.30 + (i * 0.05),
            }
        )
    await seeded_db["readings"].insert_many(readings)

    # Call endpoint
    resp = await async_client.get(f"/devices/{device_id}/forecast?horizon_minutes=30&step_minutes=5")
    assert resp.status_code == 200
    data = resp.json()

    assert data["device_id"] == device_id
    assert data["commodity"] == "tomato"
    assert data["fan_threshold"] == 0.60
    assert data["alert_threshold"] == 0.70
    assert data["insufficient_data"] is False
    assert data["trend_slope_per_min"] > 0.0
    assert "forecast" in data
    assert len(data["forecast"]) == 6  # 30 / 5 = 6 points

    # Verify first point is immediately after generated_at
    first_pt = data["forecast"][0]
    assert "timestamp" in first_pt
    assert "predicted_sri" in first_pt
    assert first_pt["predicted_sri"] > 0.0
