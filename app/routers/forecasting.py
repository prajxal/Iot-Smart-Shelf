"""Router for Spoilage Risk Index (SRI) forecasting endpoints."""

from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.forecast import DeviceForecastResponse
from app.services.forecasting import ForecastingService

router = APIRouter(tags=["forecasting"])


@router.get(
    "/devices/{device_id}/forecast",
    response_model=DeviceForecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate SRI forecast for a shelf device",
    description=(
        "Computes time-bucket moving averages of recent readings (last 30-40 min), "
        "fits a least-squares linear trend over recent buckets, and extrapolates "
        "future Spoilage Risk Index trajectories for the given horizon."
    ),
)
async def get_device_forecast(
    device_id: str,
    horizon_minutes: int = Query(60, ge=5, le=360, description="Forecast horizon in minutes (default 60)"),
    step_minutes: int = Query(5, ge=1, le=30, description="Forecast step size in minutes (default 5)"),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> DeviceForecastResponse:
    """Fetch SRI trend forecast for shelf device."""
    service = ForecastingService(db)
    return await service.generate_forecast(
        device_id=device_id,
        horizon_minutes=horizon_minutes,
        step_minutes=step_minutes,
    )
