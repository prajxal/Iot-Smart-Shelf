"""Router for shelf devices, assignments, calibrations, alerts, and status.

Endpoints:
- GET /devices/{device_id}/status: Latest reading + SRI + fan state + active commodity.
- GET /devices/{device_id}/alerts: Alert history (open + resolved).
- GET /devices/{device_id}/assignment: Current commodity assignment.
- PUT /devices/{device_id}/assignment: Reassign commodity (enforces overlap protection).
- POST /devices: Register a new device.
- GET /devices: List all devices.
- POST /devices/{device_id}/calibration: Register device calibration.
- GET /devices/{device_id}/calibration: Get latest calibration.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.alert import Alert
from app.models.device import Device, DeviceCreate
from app.models.device_assignment import DeviceAssignment, DeviceAssignmentCreate
from app.models.device_calibration import DeviceCalibration, DeviceCalibrationCreate
from app.services.device_service import (
    CommodityNotFoundError,
    DeviceNotFoundError,
    DeviceService,
)

router = APIRouter(tags=["devices"])


@router.get(
    "/devices/{device_id}/status",
    summary="Get current operational status for a shelf device",
    description="Returns the latest sensor reading, computed SRI, active fan state, and assigned commodity.",
)
async def get_device_status(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> Dict[str, Any]:
    """Fetch real-time device status."""
    service = DeviceService(db)
    return await service.get_device_status(device_id)


@router.get(
    "/devices/{device_id}/alerts",
    response_model=List[Alert],
    summary="Get alert history for a device",
    description="Query open and resolved spoilage risk alerts with audit links.",
)
async def get_device_alerts(
    device_id: str,
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status: 'open' or 'resolved'"),
    limit: int = Query(50, ge=1, le=500, description="Max alerts to return"),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> List[Alert]:
    """Fetch alerts for device."""
    query: dict = {"device_id": device_id}
    if status_filter:
        query["status"] = status_filter

    cursor = db["alerts"].find(query).sort("opened_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Alert(**d) for d in docs]


@router.get(
    "/devices/{device_id}/assignment",
    response_model=DeviceAssignment,
    summary="Get active commodity assignment for a device",
    description="Returns the active assignment record (end_at is null).",
)
async def get_active_assignment(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> DeviceAssignment:
    """Fetch active assignment."""
    service = DeviceService(db)
    assignment = await service.get_active_assignment(device_id)
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active commodity assignment found for device '{device_id}'.",
        )
    return assignment


@router.put(
    "/devices/{device_id}/assignment",
    response_model=DeviceAssignment,
    status_code=status.HTTP_200_OK,
    summary="Reassign commodity to a shelf device",
    description=(
        "Closes any prior active assignment (sets end_at to now) and opens a new assignment. "
        "Rejects unknown commodity_type not found in commodity_profiles."
    ),
)
async def reassign_device_commodity(
    device_id: str,
    payload: DeviceAssignmentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> DeviceAssignment:
    """Reassign commodity for a shelf device."""
    service = DeviceService(db)
    try:
        assignment = await service.reassign_commodity(device_id, payload)
        return assignment
    except CommodityNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.post(
    "/devices",
    response_model=Device,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new shelf device",
)
async def register_device(
    payload: DeviceCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> Device:
    """Register a new shelf hardware unit."""
    service = DeviceService(db)
    return await service.register_device(payload)


@router.get(
    "/devices",
    response_model=List[Device],
    summary="List all registered shelf devices",
)
async def list_devices(
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> List[Device]:
    """List registered devices."""
    service = DeviceService(db)
    return await service.list_devices()


@router.post(
    "/devices/{device_id}/calibration",
    response_model=DeviceCalibration,
    status_code=status.HTTP_201_CREATED,
    summary="Set sensor calibration baseline for device",
    description="Registers a new calibration record (e.g., clean air MQ-135 baseline) effective from timestamp.",
)
async def add_device_calibration(
    device_id: str,
    payload: DeviceCalibrationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> DeviceCalibration:
    """Register calibration record for device."""
    service = DeviceService(db)
    return await service.add_calibration(device_id, payload)


@router.get(
    "/devices/{device_id}/calibration",
    response_model=DeviceCalibration,
    summary="Get latest calibration baseline for device",
)
async def get_latest_calibration(
    device_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> DeviceCalibration:
    """Fetch latest calibration record for device."""
    service = DeviceService(db)
    cal = await service.get_latest_calibration(device_id)
    if not cal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No calibration record found for device '{device_id}'.",
        )
    return cal
