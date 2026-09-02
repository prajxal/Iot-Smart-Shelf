"""Unit and integration tests for SpoilageService.

Validates PRD §5 algorithmic rules:
- §5.1: Four-step resolution chain (assignment -> profile -> calibration -> gas)
- §5.2: SRI formula against hand-computed examples using real USDA AH-66 tomato data
- §5.3: Chilling-injury safety interlock (overrides high SRI fan decision unconditionally)
- §5.3: Hysteresis state transitions (ON/OFF thresholds)
- §5.4: Alert lifecycle (opened_by_reading_id, peak tracking, auto-resolution)
- §6: Resilience (actuation command returned even if DB persistence fails)
"""

from unittest.mock import AsyncMock, patch
import pytest
from datetime import datetime, timezone
from app.config import settings
from app.models.commodity_profile import CommodityProfile
from app.models.device_assignment import DeviceAssignment
from app.models.device_calibration import DeviceCalibration
from app.models.reading import ReadingCreate
from app.services.spoilage import (
    CalibrationNotFoundError,
    NoActiveAssignmentError,
    ProfileNotFoundError,
    SpoilageService,
)


@pytest.mark.asyncio
async def test_resolution_chain_success(sample_device_setup, seeded_db):
    """PRD §5.1: Test successful four-step resolution chain."""
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)

    assignment, profile, calibration = await service.resolve_context("shelf-01", now)

    assert isinstance(assignment, DeviceAssignment)
    assert assignment.device_id == "shelf-01"
    assert assignment.commodity_type == "tomato"

    assert isinstance(profile, CommodityProfile)
    assert profile.commodity_type == "tomato"
    assert profile.optimal_temp_max == 21.0
    assert profile.chilling_threshold_c == 13.0
    assert profile.source == "AH-66 pp. 581-585"

    assert isinstance(calibration, DeviceCalibration)
    assert calibration.device_id == "shelf-01"
    assert calibration.mq135_baseline == 100.0


@pytest.mark.asyncio
async def test_resolution_chain_missing_assignment(seeded_db):
    """PRD §5.1: Missing assignment raises NoActiveAssignmentError."""
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    with pytest.raises(NoActiveAssignmentError):
        await service.resolve_context("unregistered-device", now)


@pytest.mark.asyncio
async def test_resolution_chain_missing_calibration(seeded_db):
    """PRD §5.1: Missing calibration record raises CalibrationNotFoundError."""
    now = datetime.now(timezone.utc)
    # Create assignment without calibration
    await seeded_db["device_assignments"].insert_one(
        {
            "assignment_id": "asg-test",
            "device_id": "shelf-no-cal",
            "commodity_type": "tomato",
            "start_at": now,
            "end_at": None,
        }
    )

    service = SpoilageService(seeded_db)
    with pytest.raises(CalibrationNotFoundError):
        await service.resolve_context("shelf-no-cal", now)


@pytest.mark.asyncio
async def test_sri_formula_hand_computed_tomato(sample_device_setup, seeded_db):
    """PRD §5.2: Test SRI calculation with exact hand-computed values using AH-66 tomato data.

    Tomato reference values from commodity-profiles.json:
    - optimal_temp_max = 21.0 °C
    - optimal_rh_min = 90.0 %, optimal_rh_max = 95.0 % (band = 5.0)
    - q10 = 1.95 (at 15-25°C band) / 2.38 (at 10-20°C band)
    - mq135_baseline = 100.0

    Default weights: w1 = 0.50, w2 = 0.30, w3 = 0.20
    """
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    _, profile, calibration = await service.resolve_context("shelf-01", now)

    # -------------------------------------------------------------------------
    # Case A: Ideal store conditions
    # temp = 20.0 °C (within optimal 13-21)
    # humidity = 92.0 % (within optimal 90-95)
    # gas_raw = 100.0 (clean air baseline)
    #
    # temp_excess = max(0, 20.0 - 21.0) = 0.0 -> temp_term = 0.0 -> norm_temp = 0.0
    # rh_dev = max(0, 90 - 92, 92 - 95) = 0.0 -> rh_term = 0.0
    # gas_signal = 100 / 100 = 1.0 -> gas_term = 0.0
    # SRI = 0.50 * 0.0 + 0.30 * 0.0 + 0.20 * 0.0 = 0.0
    # -------------------------------------------------------------------------
    sri_ideal = service.compute_sri(
        temp_c=20.0,
        humidity_pct=92.0,
        gas_raw=100.0,
        profile=profile,
        calibration=calibration,
    )
    assert sri_ideal == pytest.approx(0.0, abs=1e-4)

    # -------------------------------------------------------------------------
    # Case B: Elevated stress conditions (Hand-computed)
    # temp = 31.0 °C
    #   optimal_temp_max = 21.0
    #   temp_excess = 31.0 - 21.0 = 10.0 °C
    #   profile.get_q10(31.0) -> uses 15_25 band: 1.95
    #   temp_term = (1.95 ** (10.0 / 10.0)) - 1.0 = 0.95
    #   norm_temp = clamp(0.95 / 2.0, 0, 1) = 0.475
    #
    # humidity = 70.0 %
    #   optimal_rh_min = 90.0, optimal_rh_max = 95.0, band = 5.0
    #   rh_dev = 90.0 - 70.0 = 20.0 %
    #   rh_term = 20.0 / 5.0 = 4.0
    #
    # gas_raw = 300.0
    #   baseline = 100.0
    #   gas_signal = 300.0 / 100.0 = 3.0
    #   gas_term = clamp((3.0 - 1.0) / 2.0, 0, 1) = 1.0
    #
    # raw_sri = (0.50 * 0.475) + (0.30 * 4.0) + (0.20 * 1.0)
    #         = 0.2375 + 1.20 + 0.20 = 1.6375
    # SRI clamped = 1.0
    # -------------------------------------------------------------------------
    sri_elevated = service.compute_sri(
        temp_c=31.0,
        humidity_pct=70.0,
        gas_raw=300.0,
        profile=profile,
        calibration=calibration,
    )
    assert sri_elevated == pytest.approx(1.0, abs=1e-4)

    # -------------------------------------------------------------------------
    # Case C: Mild heat only
    # temp = 26.0 °C (temp_excess = 5.0 °C)
    #   profile.get_q10(26.0) = 1.95 (highest measured band)
    #   temp_term = (1.95 ** 0.5) - 1.0 = 1.396424 - 1.0 = 0.396424
    #   norm_temp = 0.396424 / 2.0 = 0.198212
    # humidity = 92.0 % (rh_term = 0)
    # gas_raw = 100.0 (gas_term = 0)
    # SRI = 0.50 * 0.198212 = 0.099106
    # -------------------------------------------------------------------------
    sri_mild_heat = service.compute_sri(
        temp_c=26.0,
        humidity_pct=92.0,
        gas_raw=100.0,
        profile=profile,
        calibration=calibration,
    )
    q10_26 = profile.get_q10(26.0)
    expected_sri = 0.50 * (((q10_26 ** 0.5) - 1.0) / 2.0)
    assert sri_mild_heat == pytest.approx(expected_sri, abs=1e-4)


@pytest.mark.asyncio
async def test_chilling_injury_safety_interlock(sample_device_setup, seeded_db):
    """PRD §5.3: Chilling Injury Safety Interlock overrides SRI unconditionally.

    Tomato chilling injury threshold = 13.0 °C (from AH-66 pp. 581-585).
    Even when spoilage risk (SRI) is very high due to gas and RH deviations,
    if ambient temperature is <= 13.0 °C, the exhaust fan MUST be forced OFF
    to prevent catastrophic chilling injury.
    """
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    _, profile, calibration = await service.resolve_context("shelf-01", now)

    # High humidity deviation + elevated gas produces high SRI
    sri_value = service.compute_sri(
        temp_c=12.0,  # Below chilling threshold of 13 °C!
        humidity_pct=50.0,
        gas_raw=400.0,
        profile=profile,
        calibration=calibration,
    )
    assert sri_value >= settings.sri_on  # Would normally turn fan ON

    # Test interlock evaluation directly
    fan_cmd, interlock_triggered = service.evaluate_fan_command(
        temp_c=12.0,
        sri=sri_value,
        profile=profile,
        previous_fan_state="off",
    )

    # MUST be off because temp <= chilling threshold 13.0 °C
    assert fan_cmd == "off"
    assert interlock_triggered is True

    # Test at exact threshold boundary (13.0 °C)
    fan_cmd_boundary, interlock_boundary = service.evaluate_fan_command(
        temp_c=13.0,
        sri=sri_value,
        profile=profile,
        previous_fan_state="off",
    )
    assert fan_cmd_boundary == "off"
    assert interlock_boundary is True

    # When temperature is safe (> 13.0 °C), interlock does NOT trigger
    fan_cmd_safe, interlock_safe = service.evaluate_fan_command(
        temp_c=14.0,
        sri=sri_value,
        profile=profile,
        previous_fan_state="off",
    )
    assert fan_cmd_safe == "on"
    assert interlock_safe is False


@pytest.mark.asyncio
async def test_potato_chilling_interlock(seeded_db):
    """PRD §5.3: Test chilling injury safety interlock on potato (threshold 2.0 °C)."""
    now = datetime.now(timezone.utc)
    # Assign potato to shelf-03
    await seeded_db["devices"].insert_one({"device_id": "shelf-03", "location": "kirana-store-C", "installed_at": now})
    await seeded_db["device_assignments"].insert_one(
        {"assignment_id": "asg-03", "device_id": "shelf-03", "commodity_type": "potato", "start_at": now, "end_at": None}
    )
    await seeded_db["device_calibration"].insert_one(
        {"calibration_id": "cal-03", "device_id": "shelf-03", "mq135_baseline": 100.0, "effective_from": now}
    )

    service = SpoilageService(seeded_db)
    _, profile, calibration = await service.resolve_context("shelf-03", now)
    assert profile.chilling_threshold_c == 2.0

    # Test below chilling threshold (1.5 °C)
    fan_cmd, interlock = service.evaluate_fan_command(temp_c=1.5, sri=0.90, profile=profile)
    assert fan_cmd == "off"
    assert interlock is True

    # Test above chilling threshold (5.0 °C)
    fan_cmd_ok, interlock_ok = service.evaluate_fan_command(temp_c=5.0, sri=0.90, profile=profile)
    assert fan_cmd_ok == "on"
    assert interlock_ok is False


@pytest.mark.asyncio
async def test_hysteresis_fan_control(sample_device_setup, seeded_db):
    """PRD §5.3: Test hysteresis state logic (sri_on = 0.60, sri_off = 0.40)."""
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    _, profile, _ = await service.resolve_context("shelf-01", now)

    # From OFF: SRI = 0.50 (< sri_on) -> stays OFF
    cmd, _ = service.evaluate_fan_command(temp_c=20.0, sri=0.50, profile=profile, previous_fan_state="off")
    assert cmd == "off"

    # From OFF: SRI = 0.65 (>= sri_on) -> turns ON
    cmd, _ = service.evaluate_fan_command(temp_c=20.0, sri=0.65, profile=profile, previous_fan_state="off")
    assert cmd == "on"

    # From ON: SRI = 0.50 (>= sri_off) -> stays ON (hysteresis band)
    cmd, _ = service.evaluate_fan_command(temp_c=20.0, sri=0.50, profile=profile, previous_fan_state="on")
    assert cmd == "on"

    # From ON: SRI = 0.35 (< sri_off) -> turns OFF
    cmd, _ = service.evaluate_fan_command(temp_c=20.0, sri=0.35, profile=profile, previous_fan_state="on")
    assert cmd == "off"


@pytest.mark.asyncio
async def test_alert_lifecycle(sample_device_setup, seeded_db):
    """PRD §5.4: Test alert opening, peak tracking, and auto-resolution with audit link."""
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    device_id = "shelf-01"

    # Step 1: Trigger reading with high SRI (>= alert_threshold 0.70)
    high_reading = ReadingCreate(
        device_seq=1,
        device_timestamp=now,
        temp_c=25.0,
        humidity_pct=80.0,
        gas_raw=200.0,
    )
    response_1 = await service.process_reading(device_id, high_reading)
    assert response_1.spoilage_index >= settings.alert_threshold

    # Verify open alert was created with opened_by_reading_id
    alert_doc = await seeded_db["alerts"].find_one({"device_id": device_id, "status": "open"})
    assert alert_doc is not None
    assert alert_doc["opened_by_reading_id"] == response_1.reading_id
    assert alert_doc["peak_risk_value"] == pytest.approx(response_1.spoilage_index, abs=1e-4)

    # Step 2: Higher SRI reading updates peak_risk_value
    higher_reading = ReadingCreate(
        device_seq=2,
        device_timestamp=now,
        temp_c=28.0,
        humidity_pct=75.0,
        gas_raw=250.0,
    )
    response_2 = await service.process_reading(device_id, higher_reading)
    assert response_2.spoilage_index > response_1.spoilage_index

    alert_doc_updated = await seeded_db["alerts"].find_one({"device_id": device_id, "status": "open"})
    assert alert_doc_updated["peak_risk_value"] == pytest.approx(response_2.spoilage_index, abs=1e-4)
    # Original audit link remains unchanged
    assert alert_doc_updated["opened_by_reading_id"] == response_1.reading_id

    # Step 3: Low SRI reading (< alert_resolve_threshold 0.40) resolves alert
    low_reading = ReadingCreate(
        device_seq=3,
        device_timestamp=now,
        temp_c=20.0,
        humidity_pct=92.0,
        gas_raw=100.0,
    )
    response_3 = await service.process_reading(device_id, low_reading)
    assert response_3.spoilage_index < settings.alert_resolve_threshold

    # Verify alert is resolved
    open_alert = await seeded_db["alerts"].find_one({"device_id": device_id, "status": "open"})
    assert open_alert is None

    resolved_alert = await seeded_db["alerts"].find_one({"device_id": device_id, "status": "resolved"})
    assert resolved_alert is not None
    assert resolved_alert["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resilience_on_database_write_failure(sample_device_setup, seeded_db):
    """PRD §6: If MongoDB write fails, still return computed fan command without throwing."""
    service = SpoilageService(seeded_db)
    reading_payload = ReadingCreate(
        device_seq=99,
        temp_c=26.0,
        humidity_pct=85.0,
        gas_raw=120.0,
    )

    # Mock insert_one to simulate database write error
    with patch.object(seeded_db["readings"], "insert_one", side_effect=RuntimeError("DB Write Timeout")):
        response = await service.process_reading("shelf-01", reading_payload)
        # Must still return valid actuation response
        assert response is not None
        assert response.device_id == "shelf-01"
        assert response.fan_command in ["on", "off"]
        assert response.spoilage_index is not None
