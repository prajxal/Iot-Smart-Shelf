"""Spoilage Risk Index (SRI) and Fan Control Algorithm Service.

Implements the decision algorithm defined in PRD §5:
- §5.1: Context resolution (assignment -> profile -> calibration -> gas_signal)
- §5.2: SRI formula (Q10 temperature term, RH deviation term, gas signal term)
- §5.3: Chilling-injury safety interlock and hysteresis fan command
- §5.4: Alert lifecycle (open, peak tracking, auto-resolution with audit link)
- §5.5: Synchronous reading processing and resilience handling

All commodity numeric values are sourced dynamically from the seeded database/profile;
none are hardcoded, approximated, or invented in this codebase (PRD §0).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.models.alert import Alert
from app.models.commodity_profile import CommodityProfile
from app.models.device_assignment import DeviceAssignment
from app.models.device_calibration import DeviceCalibration
from app.models.reading import Reading, ReadingCreate, ReadingResponse

logger = logging.getLogger("smart_shelf.spoilage")


class SpoilageServiceError(Exception):
    """Base exception for spoilage service."""


class NoActiveAssignmentError(SpoilageServiceError):
    """Raised when no active commodity assignment exists for a device."""


class ProfileNotFoundError(SpoilageServiceError):
    """Raised when no commodity profile version matches the given criteria."""


class CalibrationNotFoundError(SpoilageServiceError):
    """Raised when no calibration record matches the device."""


class MissingProfileFieldError(SpoilageServiceError):
    """Raised when a commodity profile is missing a required numeric field for the algorithm (PRD §0)."""


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp a floating point value within [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def normalize_temp_term(temp_term: float, scale: Optional[float] = None) -> float:
    """Normalize temperature term to [0.0, 1.0].

    temp_term = q10^(excess/10) - 1.
    scale: scaling factor (default from config: temp_term_scale, e.g. 2.0).
    """
    s = scale or settings.temp_term_scale
    if s <= 0:
        s = 1.0
    return clamp(temp_term / s, 0.0, 1.0)


def normalize_gas_signal(gas_signal: float, span: Optional[float] = None) -> float:
    """Normalize gas signal (Rs/Ro ratio) to [0.0, 1.0].

    gas_signal = gas_raw / mq135_baseline.
    1.0 is baseline clean air. Above 1.0 indicates gas accumulation.
    """
    span_val = span or settings.gas_signal_span
    if span_val <= 0:
        span_val = 1.0
    excess = gas_signal - settings.gas_signal_baseline
    if excess <= 0:
        return 0.0
    return clamp(excess / span_val, 0.0, 1.0)


class SpoilageService:
    """Core algorithmic service for Spoilage Risk Index (SRI) and fan actuation."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def resolve_context(
        self,
        device_id: str,
        as_of: datetime,
    ) -> Tuple[DeviceAssignment, CommodityProfile, DeviceCalibration]:
        """PRD §5.1: Resolve active assignment, profile, and calibration as of timestamp.

        1. commodity_type <- active device_assignments row for device_id as of as_of.
        2. profile <- latest commodity_profiles row for commodity_type with effective_from <= as_of.
        3. calibration <- latest device_calibration row for device_id with effective_from <= as_of.
        """
        # Step 1: Resolve active assignment (PRD §3.3)
        # Active assignment has start_at <= as_of and (end_at is null or end_at > as_of)
        assignment_doc = await self.db["device_assignments"].find_one(
            {
                "device_id": device_id,
                "start_at": {"$lte": as_of},
                "$or": [{"end_at": None}, {"end_at": {"$gt": as_of}}],
            },
            sort=[("start_at", -1)],
        )

        # Fallback to currently open assignment if timestamps are slightly out of order
        if not assignment_doc:
            assignment_doc = await self.db["device_assignments"].find_one(
                {"device_id": device_id, "end_at": None},
                sort=[("start_at", -1)],
            )

        if not assignment_doc:
            raise NoActiveAssignmentError(
                f"No active commodity assignment found for device '{device_id}' as of {as_of.isoformat()}."
            )

        assignment = DeviceAssignment(**assignment_doc)
        commodity_type = assignment.commodity_type

        # Step 2: Resolve commodity profile (PRD §3.1)
        profile_doc = await self.db["commodity_profiles"].find_one(
            {
                "commodity_type": commodity_type,
                "effective_from": {"$lte": as_of},
            },
            sort=[("effective_from", -1)],
        )

        # Fallback to latest profile if timestamp is earlier than initial seed
        if not profile_doc:
            profile_doc = await self.db["commodity_profiles"].find_one(
                {"commodity_type": commodity_type},
                sort=[("effective_from", -1)],
            )

        if not profile_doc:
            raise ProfileNotFoundError(
                f"No profile found for commodity '{commodity_type}' in commodity_profiles collection."
            )

        profile = CommodityProfile(**profile_doc)

        # Step 3: Resolve calibration (PRD §3.4)
        cal_doc = await self.db["device_calibration"].find_one(
            {
                "device_id": device_id,
                "effective_from": {"$lte": as_of},
            },
            sort=[("effective_from", -1)],
        )

        # Fallback to latest calibration for device
        if not cal_doc:
            cal_doc = await self.db["device_calibration"].find_one(
                {"device_id": device_id},
                sort=[("effective_from", -1)],
            )

        if not cal_doc:
            raise CalibrationNotFoundError(
                f"No calibration record found for device '{device_id}'."
            )

        calibration = DeviceCalibration(**cal_doc)

        return assignment, profile, calibration

    def compute_sri(
        self,
        temp_c: float,
        humidity_pct: float,
        gas_raw: float,
        profile: CommodityProfile,
        calibration: DeviceCalibration,
    ) -> float:
        """PRD §5.2: Compute Spoilage Risk Index (SRI) using USDA reference values.

        Formula:
        temp_excess = max(0, temp_c - profile.optimal_temp_max)
        temp_term   = profile.q10 ** (temp_excess / 10) - 1        # 0 when within/below optimal max

        rh_dev      = max(0, profile.optimal_rh_min - humidity_pct, humidity_pct - profile.optimal_rh_max)
        rh_band     = profile.optimal_rh_max - profile.optimal_rh_min
        rh_term     = rh_dev / rh_band                              # 0 when within band

        gas_term    = normalize(gas_signal)                          # commodity-agnostic baseline

        SRI = clamp(w1 * normalize(temp_term) + w2 * rh_term + w3 * gas_term, 0, 1)
        """
        # Strict validation: verify profile contains required values (PRD §0)
        if profile.optimal_temp_max is None:
            raise MissingProfileFieldError(
                f"Profile for '{profile.commodity_type}' is missing required field 'optimal_temp_max'."
            )
        if profile.optimal_rh_min is None or profile.optimal_rh_max is None:
            raise MissingProfileFieldError(
                f"Profile for '{profile.commodity_type}' is missing required RH band fields."
            )

        # 1. Temperature term (USDA AH-66 Q10 rate response)
        temp_excess = max(0.0, temp_c - profile.optimal_temp_max)
        q10_value = profile.get_q10(temp_c)
        temp_term = (q10_value ** (temp_excess / 10.0)) - 1.0

        # 2. Relative Humidity term
        rh_band = profile.optimal_rh_max - profile.optimal_rh_min
        if rh_band <= 0:
            rh_band = 1.0

        rh_dev = max(
            0.0,
            profile.optimal_rh_min - humidity_pct,
            humidity_pct - profile.optimal_rh_max,
        )
        rh_term = rh_dev / rh_band

        # 3. Gas term (PRD §5.1 step 4 & §5.2)
        baseline = calibration.mq135_baseline
        gas_signal = (gas_raw / baseline) if baseline > 0 else 1.0
        gas_term = normalize_gas_signal(gas_signal)

        # 4. SRI Composite
        norm_temp = normalize_temp_term(temp_term)
        raw_sri = (
            (settings.w1 * norm_temp)
            + (settings.w2 * rh_term)
            + (settings.w3 * gas_term)
        )
        return clamp(raw_sri, 0.0, 1.0)

    async def get_previous_fan_state(self, device_id: str, before_time: datetime) -> str:
        """Fetch the previous fan actuation state for hysteresis control."""
        last_reading = await self.db["readings"].find_one(
            {
                "device_id": device_id,
                "device_timestamp": {"$lt": before_time},
            },
            sort=[("device_timestamp", -1)],
        )
        if last_reading and last_reading.get("fan_commanded") is True:
            return "on"
        return "off"

    def evaluate_fan_command(
        self,
        temp_c: float,
        sri: float,
        profile: CommodityProfile,
        previous_fan_state: str = "off",
    ) -> Tuple[str, bool]:
        """PRD §5.3: Evaluate fan command with Chilling Injury Safety Interlock.

        Returns:
            (fan_command, interlock_triggered)
        """
        # Safety interlock check (PRD §3.7.4, §5.3)
        # If temp_c <= profile.chilling_threshold_c (or optimal_temp_min if no explicit threshold):
        # fan_command = "off" unconditionally.
        chilling_limit = (
            profile.chilling_threshold_c
            if profile.chilling_threshold_c is not None
            else profile.optimal_temp_min
        )

        if chilling_limit is not None and temp_c <= chilling_limit:
            # SAFETY INTERLOCK TRIGGERED: Venting ambient air would risk chilling injury
            return "off", True

        # Hysteresis fan control (PRD §5.3)
        if previous_fan_state == "on":
            command = "off" if sri < settings.sri_off else "on"
        else:
            command = "on" if sri >= settings.sri_on else "off"

        return command, False

    async def update_alerts(
        self,
        device_id: str,
        sri: float,
        reading_id: str,
        timestamp: datetime,
    ) -> Optional[Alert]:
        """PRD §5.4: Alert lifecycle management.

        - If SRI >= alert_threshold and no alert open: open one with opened_by_reading_id.
        - While open: update peak_risk_value on higher SRI.
        - If SRI < alert_resolve_threshold and alert open: resolve it.
        """
        open_alert_doc = await self.db["alerts"].find_one(
            {"device_id": device_id, "status": "open"},
            sort=[("opened_at", -1)],
        )

        if sri >= settings.alert_threshold:
            if not open_alert_doc:
                # Open a new alert episode (PRD §3.6, §5.4)
                new_alert_id = f"alt-{uuid4().hex[:8]}"
                alert = Alert(
                    alert_id=new_alert_id,
                    device_id=device_id,
                    alert_type="high_spoilage_risk",
                    status="open",
                    opened_at=timestamp,
                    resolved_at=None,
                    peak_risk_value=sri,
                    opened_by_reading_id=reading_id,
                )
                alert_dict = alert.model_dump(by_alias=True)
                if alert_dict.get("_id") is None:
                    alert_dict.pop("_id", None)
                await self.db["alerts"].insert_one(alert_dict)
                logger.info("Opened new alert '%s' for device '%s' (SRI: %.3f)", new_alert_id, device_id, sri)
                return alert
            else:
                # Update peak risk value if current SRI is higher
                current_peak = open_alert_doc.get("peak_risk_value", 0.0)
                if sri > current_peak:
                    await self.db["alerts"].update_one(
                        {"_id": open_alert_doc["_id"]},
                        {"$set": {"peak_risk_value": sri}},
                    )
                    open_alert_doc["peak_risk_value"] = sri
                return Alert(**open_alert_doc)

        elif sri < settings.alert_resolve_threshold:
            if open_alert_doc:
                # Resolve active alert (PRD §5.4)
                await self.db["alerts"].update_one(
                    {"_id": open_alert_doc["_id"]},
                    {
                        "$set": {
                            "status": "resolved",
                            "resolved_at": timestamp,
                        }
                    },
                )
                open_alert_doc["status"] = "resolved"
                open_alert_doc["resolved_at"] = timestamp
                logger.info(
                    "Resolved alert '%s' for device '%s' (SRI dropped to %.3f)",
                    open_alert_doc["alert_id"],
                    device_id,
                    sri,
                )
                return Alert(**open_alert_doc)

        return None

    async def process_reading(
        self,
        device_id: str,
        payload: ReadingCreate,
    ) -> ReadingResponse:
        """PRD §4 / §5: Complete critical path for sensor reading ingress.

        Steps:
        1. Context resolution (§5.1)
        2. SRI computation (§5.2)
        3. Safety interlock & Fan command evaluation (§5.3)
        4. Reading persistence & Alert lifecycle (§5.4, §5.5)
        5. Synchronous actuation command response (§5.5)
        """
        device_ts = payload.device_timestamp or datetime.now(timezone.utc)
        server_rx = datetime.now(timezone.utc)
        reading_id = f"rd-{uuid4().hex[:8]}"

        # Step 1: Resolve context
        assignment, profile, calibration = await self.resolve_context(device_id, device_ts)

        # Step 2: Compute SRI
        sri = self.compute_sri(
            temp_c=payload.temp_c,
            humidity_pct=payload.humidity_pct,
            gas_raw=payload.gas_raw,
            profile=profile,
            calibration=calibration,
        )

        # Step 3: Fetch previous fan state & evaluate fan actuation + interlock
        prev_fan = await self.get_previous_fan_state(device_id, device_ts)
        fan_cmd, interlock_triggered = self.evaluate_fan_command(
            temp_c=payload.temp_c,
            sri=sri,
            profile=profile,
            previous_fan_state=prev_fan,
        )
        fan_commanded = (fan_cmd == "on")

        # Step 4: Construct reading document
        reading = Reading(
            reading_id=reading_id,
            device_id=device_id,
            device_seq=payload.device_seq,
            device_timestamp=device_ts,
            server_received_at=server_rx,
            temp_c=payload.temp_c,
            humidity_pct=payload.humidity_pct,
            gas_raw=payload.gas_raw,
            sensor_status=payload.sensor_status or "ok",
            spoilage_index=sri,
            fan_commanded=fan_commanded,
        )

        # Step 5: Persist reading and update alerts (PRD §6 resilience: do not block actuation on DB error)
        try:
            reading_dict = reading.model_dump(by_alias=True)
            if reading_dict.get("_id") is None:
                reading_dict.pop("_id", None)
            await self.db["readings"].insert_one(reading_dict)
            await self.update_alerts(device_id, sri, reading_id, device_ts)
        except Exception as err:
            logger.error("Database write error during reading processing: %s", err, exc_info=True)

        return ReadingResponse(
            reading_id=reading_id,
            device_id=device_id,
            fan_command=fan_cmd,
            spoilage_index=sri,
            interlock_triggered=interlock_triggered,
            sensor_status=payload.sensor_status or "ok",
        )
