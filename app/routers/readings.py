"""Router for sensor readings ingress and history queries.

Endpoints:
- POST /devices/{device_id}/readings: Critical path for ESP32 sensor ingress.
- GET /devices/{device_id}/history: Time-range historical readings query.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.reading import Reading, ReadingCreate, ReadingResponse
from app.services.spoilage import (
    CalibrationNotFoundError,
    NoActiveAssignmentError,
    ProfileNotFoundError,
    SpoilageService,
)

router = APIRouter(tags=["readings"])


@router.post(
    "/devices/{device_id}/readings",
    response_model=ReadingResponse,
    status_code=status.HTTP_200_OK,
    summary="Push sensor reading from ESP32 and get fan command",
    description=(
        "Critical path endpoint: receives DHT22 (temp, RH) and MQ-135 (gas) raw readings, "
        "resolves active commodity and calibration, computes Spoilage Risk Index (SRI), "
        "evaluates chilling injury safety interlock, updates alert lifecycle, persists the reading, "
        "and returns fan actuation command synchronously."
    ),
)
async def create_device_reading(
    device_id: str,
    payload: ReadingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> ReadingResponse:
    """Process incoming sensor reading from ESP32."""
    service = SpoilageService(db)
    try:
        response = await service.process_reading(device_id=device_id, payload=payload)
        return response
    except NoActiveAssignmentError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except ProfileNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except CalibrationNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.get(
    "/devices/{device_id}/history",
    response_model=List[Reading],
    summary="Query historical readings for a device",
    description="Retrieve time-series sensor readings and computed SRI values for a shelf device.",
)
async def get_device_reading_history(
    device_id: str,
    start_time: Optional[datetime] = Query(None, description="Filter readings >= start_time (ISO format)"),
    end_time: Optional[datetime] = Query(None, description="Filter readings <= end_time (ISO format)"),
    limit: int = Query(100, ge=1, le=1000, description="Max readings to return (default 100, max 1000)"),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> List[Reading]:
    """Query time-range historical readings for device."""
    query: dict = {"device_id": device_id}
    time_filter: dict = {}
    if start_time:
        time_filter["$gte"] = start_time
    if end_time:
        time_filter["$lte"] = end_time
    if time_filter:
        query["device_timestamp"] = time_filter

    cursor = db["readings"].find(query).sort("device_timestamp", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Reading(**d) for d in docs]
