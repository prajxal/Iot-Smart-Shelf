"""Device and Assignment Management Service.

Implements business logic for shelf devices, assignments (including overlap invariant §3.7.2),
calibration records, and status summaries.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.commodity_profile import CommodityProfile
from app.models.device import Device, DeviceCreate
from app.models.device_assignment import DeviceAssignment, DeviceAssignmentCreate
from app.models.device_calibration import DeviceCalibration, DeviceCalibrationCreate
from app.models.reading import Reading

logger = logging.getLogger("smart_shelf.devices")


class DeviceServiceError(Exception):
    """Base exception for device service."""


class CommodityNotFoundError(DeviceServiceError):
    """Raised when assigning an unknown commodity_type."""


class DeviceNotFoundError(DeviceServiceError):
    """Raised when a requested device is not found."""


class DeviceService:
    """Service for device lifecycle, assignments, and calibration."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def register_device(self, payload: DeviceCreate) -> Device:
        """Register a new shelf hardware unit."""
        existing = await self.db["devices"].find_one({"device_id": payload.device_id})
        if existing:
            return Device(**existing)

        device = Device(
            device_id=payload.device_id,
            location=payload.location,
            installed_at=payload.installed_at or datetime.now(timezone.utc),
        )
        doc = device.model_dump(by_alias=True)
        if doc.get("_id") is None:
            doc.pop("_id", None)
        await self.db["devices"].insert_one(doc)
        return device

    async def get_device(self, device_id: str) -> Optional[Device]:
        """Fetch device by ID."""
        doc = await self.db["devices"].find_one({"device_id": device_id})
        if doc:
            return Device(**doc)
        return None

    async def list_devices(self) -> List[Device]:
        """List all registered shelf devices."""
        cursor = self.db["devices"].find()
        docs = await cursor.to_list(length=1000)
        return [Device(**d) for d in docs]

    async def get_active_assignment(self, device_id: str) -> Optional[DeviceAssignment]:
        """Fetch current open assignment for device (end_at is null)."""
        doc = await self.db["device_assignments"].find_one(
            {"device_id": device_id, "end_at": None},
            sort=[("start_at", -1)],
        )
        if doc:
            return DeviceAssignment(**doc)
        return None

    async def reassign_commodity(
        self,
        device_id: str,
        payload: DeviceAssignmentCreate,
    ) -> DeviceAssignment:
        """PRD §3.3, §3.7.2, §4: Reassign commodity to device.

        Enforces invariants:
        1. Validate commodity_type exists in commodity_profiles.
        2. Close out (end_at = now) any existing open assignment for this device.
        3. Open new assignment record.
        """
        # Step 1: Validate commodity exists in commodity_profiles
        commodity_doc = await self.db["commodity_profiles"].find_one(
            {"commodity_type": payload.commodity_type}
        )
        if not commodity_doc:
            raise CommodityNotFoundError(
                f"Commodity type '{payload.commodity_type}' does not exist in commodity_profiles. "
                "Cannot assign an unregistered commodity."
            )

        now = datetime.now(timezone.utc)
        start_at = payload.start_at or now

        # Step 2: Close out any currently open assignments for this device (PRD §3.3, §3.7.2)
        await self.db["device_assignments"].update_many(
            {"device_id": device_id, "end_at": None},
            {"$set": {"end_at": now}},
        )

        # Step 3: Insert new assignment
        new_assignment_id = f"asg-{uuid4().hex[:8]}"
        assignment = DeviceAssignment(
            assignment_id=new_assignment_id,
            device_id=device_id,
            commodity_type=payload.commodity_type,
            start_at=start_at,
            end_at=None,
        )
        doc = assignment.model_dump(by_alias=True)
        if doc.get("_id") is None:
            doc.pop("_id", None)
        await self.db["device_assignments"].insert_one(doc)
        logger.info(
            "Assigned commodity '%s' to device '%s' (assignment_id: %s)",
            payload.commodity_type,
            device_id,
            new_assignment_id,
        )
        return assignment

    async def add_calibration(
        self,
        device_id: str,
        payload: DeviceCalibrationCreate,
    ) -> DeviceCalibration:
        """Register a new calibration record for device (PRD §3.4)."""
        effective_from = payload.effective_from or datetime.now(timezone.utc)
        cal_id = f"cal-{uuid4().hex[:8]}"
        cal = DeviceCalibration(
            calibration_id=cal_id,
            device_id=device_id,
            mq135_baseline=payload.mq135_baseline,
            effective_from=effective_from,
        )
        doc = cal.model_dump(by_alias=True)
        if doc.get("_id") is None:
            doc.pop("_id", None)
        await self.db["device_calibration"].insert_one(doc)
        return cal

    async def get_latest_calibration(self, device_id: str) -> Optional[DeviceCalibration]:
        """Fetch the most recent calibration for device."""
        doc = await self.db["device_calibration"].find_one(
            {"device_id": device_id},
            sort=[("effective_from", -1)],
        )
        if doc:
            return DeviceCalibration(**doc)
        return None

    async def get_device_status(self, device_id: str) -> Dict[str, Any]:
        """PRD §4: GET /devices/{device_id}/status.

        Returns latest reading + SRI + current fan state + active commodity.
        """
        # 1. Fetch active assignment
        assignment = await self.get_active_assignment(device_id)
        active_commodity = assignment.commodity_type if assignment else None

        # 2. Fetch latest reading
        reading_doc = await self.db["readings"].find_one(
            {"device_id": device_id},
            sort=[("device_timestamp", -1)],
        )

        if not reading_doc:
            return {
                "device_id": device_id,
                "active_commodity": active_commodity,
                "latest_reading": None,
                "spoilage_index": None,
                "fan_command": "off",
                "fan_commanded": False,
            }

        reading = Reading(**reading_doc)
        fan_cmd = "on" if reading.fan_commanded else "off"

        return {
            "device_id": device_id,
            "active_commodity": active_commodity,
            "latest_reading": reading,
            "spoilage_index": reading.spoilage_index,
            "fan_command": fan_cmd,
            "fan_commanded": reading.fan_commanded,
        }
