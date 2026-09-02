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
    normalize_temp_term,
)


def test_normalize_temp_term():
    """Test saturating temperature term normalization x / (1 + x)."""
    # 1. temp_term = 0 -> 0.0
    assert normalize_temp_term(0.0) == 0.0
    assert normalize_temp_term(0) == 0.0

    # 2. Small positive values: monotonic growth
    assert normalize_temp_term(0.1) == pytest.approx(0.1 / 1.1, abs=1e-6)
    assert normalize_temp_term(0.5) == pytest.approx(0.5 / 1.5, abs=1e-6)
    assert normalize_temp_term(1.0) == pytest.approx(0.5, abs=1e-6)
    assert normalize_temp_term(2.0) == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert normalize_temp_term(0.1) < normalize_temp_term(0.5) < normalize_temp_term(1.0) < normalize_temp_term(2.0)

    # 3. Large values approach but never equal 1.0
    val_100 = normalize_temp_term(100.0)
    assert val_100 == pytest.approx(100.0 / 101.0, abs=1e-6)
    assert val_100 < 1.0

    val_1000 = normalize_temp_term(1000.0)
    assert val_1000 == pytest.approx(1000.0 / 1001.0, abs=1e-6)
    assert val_1000 < 1.0
    assert val_1000 > 0.999

    val_large = normalize_temp_term(1e6)
    assert val_large < 1.0
    assert val_large > 0.99999

    # 4. Negative input -> 0.0
    assert normalize_temp_term(-0.001) == 0.0
    assert normalize_temp_term(-1.0) == 0.0
    assert normalize_temp_term(-100.0) == 0.0



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
    #   norm_temp = clamp(0.95 / (1.0 + 0.95), 0, 1) = 0.487179
    #
    # humidity = 70.0 %
    #   optimal_rh_min = 90.0, optimal_rh_max = 95.0, band = 5.0
    #   rh_dev = 90.0 - 70.0 = 20.0 %
    #   rh_term = clamp(20.0 / 5.0, 0.0, 1.0) = 1.0
    #
    # gas_raw = 300.0
    #   baseline = 100.0
    #   gas_signal = 300.0 / 100.0 = 3.0
    #   gas_term = clamp((3.0 - 1.0) / 2.0, 0, 1) = 1.0
    #
    # raw_sri = (0.50 * 0.487179) + (0.30 * 1.0) + (0.20 * 1.0)
    #         = 0.24359 + 0.30 + 0.20 = 0.74359
    # -------------------------------------------------------------------------
    sri_elevated = service.compute_sri(
        temp_c=31.0,
        humidity_pct=70.0,
        gas_raw=300.0,
        profile=profile,
        calibration=calibration,
    )
    assert sri_elevated == pytest.approx(0.7436, abs=1e-4)

    # -------------------------------------------------------------------------
    # Case C: Mild heat only
    # temp = 26.0 °C (temp_excess = 5.0 °C)
    #   profile.get_q10(26.0) = 1.95 (highest measured band)
    #   temp_term = (1.95 ** 0.5) - 1.0 = 1.396424 - 1.0 = 0.396424
    #   norm_temp = 0.396424 / (1.0 + 0.396424) = 0.283885
    # humidity = 92.0 % (rh_term = 0)
    # gas_raw = 100.0 (gas_term = 0)
    # SRI = 0.50 * 0.283885 = 0.141943
    # -------------------------------------------------------------------------
    sri_mild_heat = service.compute_sri(
        temp_c=26.0,
        humidity_pct=92.0,
        gas_raw=100.0,
        profile=profile,
        calibration=calibration,
    )
    q10_26 = profile.get_q10(26.0)
    temp_term_26 = (q10_26 ** 0.5) - 1.0
    expected_sri = 0.50 * (temp_term_26 / (1.0 + temp_term_26))
    assert sri_mild_heat == pytest.approx(expected_sri, abs=1e-4)


@pytest.mark.asyncio
async def test_rh_term_clamping(seeded_db):
    """Unit test: RH-term clamping to [0.0, 1.0].

    Verifies that when rh_dev / rh_band exceeds 1.0 (e.g. leafy greens with a 3-point
    band [95-98%] at 80% RH where unclamped ratio is 15.0 / 3.0 = 5.0), rh_term is
    strictly capped at 1.0 rather than producing an unbounded term.
    """
    service = SpoilageService(seeded_db)
    calibration = DeviceCalibration(
        calibration_id="cal-test-rh",
        device_id="shelf-test-rh",
        mq135_baseline=100.0,
        effective_from=datetime.now(timezone.utc),
    )

    leafy_doc = await seeded_db["commodity_profiles"].find_one({"commodity_type": "leafy_greens"})
    assert leafy_doc is not None
    leafy_profile = CommodityProfile(**leafy_doc)

    # Test with temp_c = 0.0 (temp_excess = 0 -> norm_temp = 0.0) and gas_raw = 100.0 (gas_term = 0.0)
    # 1. Optimal RH: 96.0% -> rh_dev = 0.0 -> rh_term = 0.0 -> SRI = 0.0
    sri_opt = service.compute_sri(0.0, 96.0, 100.0, leafy_profile, calibration)
    assert sri_opt == pytest.approx(0.0, abs=1e-6)

    # 2. Within band ratio (< 1.0): 93.5% -> rh_dev = 1.5, rh_band = 3.0 -> rh_term = 0.5
    # SRI = 0.30 * 0.5 = 0.15
    sri_half = service.compute_sri(0.0, 93.5, 100.0, leafy_profile, calibration)
    assert sri_half == pytest.approx(0.15, abs=1e-6)

    # 3. Exactly at band edge (ratio = 1.0): 92.0% -> rh_dev = 3.0, rh_band = 3.0 -> rh_term = 1.0
    # SRI = 0.30 * 1.0 = 0.30
    sri_edge = service.compute_sri(0.0, 92.0, 100.0, leafy_profile, calibration)
    assert sri_edge == pytest.approx(0.30, abs=1e-6)

    # 4. Large deviation exceeding band width (unclamped ratio = 5.0): 80.0% -> rh_dev = 15.0, rh_band = 3.0
    # Unclamped would give: SRI = 0.30 * 5.0 = 1.50 -> clamped to 1.0 at composite level
    # Clamped gives: rh_term = 1.0 -> SRI = 0.30 * 1.0 = 0.30 (not truncated to 1.0)
    sri_clamped_80 = service.compute_sri(0.0, 80.0, 100.0, leafy_profile, calibration)
    assert sri_clamped_80 == pytest.approx(0.30, abs=1e-6)

    # 5. Extreme deviation (unclamped ratio = 28.33): 10.0% -> rh_dev = 85.0
    # Clamped gives: rh_term = 1.0 -> SRI = 0.30 * 1.0 = 0.30
    sri_extreme = service.compute_sri(0.0, 10.0, 100.0, leafy_profile, calibration)
    assert sri_extreme == pytest.approx(0.30, abs=1e-6)


@pytest.mark.asyncio
async def test_chilling_injury_safety_interlock(sample_device_setup, seeded_db):
    """PRD §5.3: Chilling Injury Safety Interlock overrides SRI unconditionally.

    Tomato chilling injury threshold = 13.0 °C (from AH-66 pp. 581-585).
    Even when spoilage risk (SRI) is very high (e.g. 0.85 >= sri_on 0.60),
    if ambient temperature is <= 13.0 °C, the exhaust fan MUST be forced OFF
    to prevent catastrophic chilling injury.
    """
    service = SpoilageService(seeded_db)
    now = datetime.now(timezone.utc)
    _, profile, calibration = await service.resolve_context("shelf-01", now)

    # High SRI value (>= sri_on 0.60)
    sri_high = 0.85
    assert sri_high >= settings.sri_on  # Would normally turn fan ON

    # Test interlock evaluation directly below chilling threshold (12.0 °C)
    fan_cmd, interlock_triggered = service.evaluate_fan_command(
        temp_c=12.0,
        sri=sri_high,
        profile=profile,
        previous_fan_state="off",
    )

    # MUST be off because temp <= chilling threshold 13.0 °C
    assert fan_cmd == "off"
    assert interlock_triggered is True

    # Test at exact threshold boundary (13.0 °C)
    fan_cmd_boundary, interlock_boundary = service.evaluate_fan_command(
        temp_c=13.0,
        sri=sri_high,
        profile=profile,
        previous_fan_state="off",
    )
    assert fan_cmd_boundary == "off"
    assert interlock_boundary is True

    # When temperature is safe (> 13.0 °C), interlock does NOT trigger
    fan_cmd_safe, interlock_safe = service.evaluate_fan_command(
        temp_c=14.0,
        sri=sri_high,
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
        temp_c=31.0,
        humidity_pct=70.0,
        gas_raw=300.0,
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
        temp_c=35.0,
        humidity_pct=65.0,
        gas_raw=300.0,
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


@pytest.mark.asyncio
async def test_sri_cross_commodity_regression(seeded_db, capsys):
    """Regression test: SRI values across all 4 commodities under shared ambient conditions.

    Evaluates tomato, onion, potato, and leafy_greens at ambient temperatures of
    20°C, 30°C, and 35°C with a fixed mid-band relative humidity and baseline gas signal.
    Verifies that the saturating normalization x / (1 + x) preserves temperature resolution
    across commodities and prevents leafy greens from immediately clamping to 1.0.
    """
    service = SpoilageService(seeded_db)
    calibration = DeviceCalibration(
        calibration_id="cal-regression",
        device_id="shelf-reg",
        mq135_baseline=100.0,
        effective_from=datetime.now(timezone.utc),
    )

    commodity_names = ["tomato", "onion", "potato", "leafy_greens"]
    profiles = {}
    for name in commodity_names:
        doc = await seeded_db["commodity_profiles"].find_one({"commodity_type": name})
        assert doc is not None, f"Profile for '{name}' must exist in seeded DB"
        profiles[name] = CommodityProfile(**doc)

    temps = [20.0, 30.0, 35.0]
    humidity_pct = 90.0
    gas_raw = 100.0  # Clean air baseline -> gas_term = 0.0

    results = {}
    print(f"\n--- Cross-Commodity SRI Regression Test (RH={humidity_pct}%, Gas={gas_raw}) ---")
    for name in commodity_names:
        prof = profiles[name]
        results[name] = {}
        row = []
        for t in temps:
            sri = service.compute_sri(
                temp_c=t,
                humidity_pct=humidity_pct,
                gas_raw=gas_raw,
                profile=prof,
                calibration=calibration,
            )
            results[name][t] = sri
            row.append(f"{t:.0f}°C: {sri:.4f}")
        print(f"  {name:13s} | " + " | ".join(row))

    # -------------------------------------------------------------------------
    # Assertions for each commodity across temperatures:
    # -------------------------------------------------------------------------

    # 1. Tomato: optimal_max = 21°C
    # 20°C: within optimal temp band (temp_term=0, rh_term=0) -> SRI = 0.0
    # 30°C: temp_excess=9°C, q10=1.95, temp_term=0.8240, norm_temp=0.4518 -> SRI = 0.2259
    # 35°C: temp_excess=14°C, q10=1.95, temp_term=1.5471, norm_temp=0.6074 -> SRI = 0.3037
    assert results["tomato"][20.0] == pytest.approx(0.0000, abs=1e-4)
    assert results["tomato"][30.0] == pytest.approx(0.2259, abs=1e-4)
    assert results["tomato"][35.0] == pytest.approx(0.3037, abs=1e-4)
    assert results["tomato"][20.0] < results["tomato"][30.0] < results["tomato"][35.0]

    # 2. Onion: optimal_max = 0°C, optimal_rh = 65-75% (RH=90% -> rh_dev = 15, band = 10 -> rh_dev/band = 1.5 -> clamped to 1.0 -> w2*rh = 0.30)
    # 20°C: temp_excess=20°C, q10=1.14, temp_term=0.2996, norm_temp=0.2305 -> SRI = 0.30 + 0.1153 = 0.4153
    # 30°C: temp_excess=30°C, q10=1.14, temp_term=0.4815, norm_temp=0.3250 -> SRI = 0.30 + 0.1625 = 0.4625
    # 35°C: temp_excess=35°C, q10=1.14, temp_term=0.5819, norm_temp=0.3678 -> SRI = 0.30 + 0.1839 = 0.4839
    assert results["onion"][20.0] == pytest.approx(0.4153, abs=1e-4)
    assert results["onion"][30.0] == pytest.approx(0.4625, abs=1e-4)
    assert results["onion"][35.0] == pytest.approx(0.4839, abs=1e-4)
    assert results["onion"][20.0] < results["onion"][30.0] < results["onion"][35.0]

    # 3. Potato: optimal_max = 10°C, optimal_rh = 80-100% (RH=90% -> rh_term = 0.0)
    # 20°C: temp_excess=10°C, q10=1.34, temp_term=0.3400, norm_temp=0.2537 -> SRI = 0.1269
    # 30°C: temp_excess=20°C, q10=1.34, temp_term=0.7956, norm_temp=0.4431 -> SRI = 0.2215
    # 35°C: temp_excess=25°C, q10=1.34, temp_term=1.0786, norm_temp=0.5189 -> SRI = 0.2594
    assert results["potato"][20.0] == pytest.approx(0.1269, abs=1e-4)
    assert results["potato"][30.0] == pytest.approx(0.2215, abs=1e-4)
    assert results["potato"][35.0] == pytest.approx(0.2594, abs=1e-4)
    assert results["potato"][20.0] < results["potato"][30.0] < results["potato"][35.0]

    # 4. Leafy Greens: optimal_max = 0°C, optimal_rh = 95-98% (RH=90% -> rh_dev = 5, band = 3 -> rh_dev/band = 1.6667 -> clamped to 1.0 -> w2*rh = 0.30)
    # 20°C: temp_excess=20°C, q10=2.09, temp_term=3.3681, norm_temp=0.7711 -> SRI = 0.30 + 0.3855 = 0.6855
    # 30°C: temp_excess=30°C, q10=2.09, temp_term=8.1293, norm_temp=0.8905 -> SRI = 0.30 + 0.4452 = 0.7452
    # 35°C: temp_excess=35°C, q10=2.09, temp_term=12.1981, norm_temp=0.9242 -> SRI = 0.30 + 0.4621 = 0.7621
    assert results["leafy_greens"][20.0] == pytest.approx(0.6855, abs=1e-4)
    assert results["leafy_greens"][30.0] == pytest.approx(0.7452, abs=1e-4)
    assert results["leafy_greens"][35.0] == pytest.approx(0.7621, abs=1e-4)
    assert results["leafy_greens"][20.0] < results["leafy_greens"][30.0] < results["leafy_greens"][35.0] < 1.0

