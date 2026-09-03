"""Pydantic models for Spoilage Risk Index (SRI) forecasting.

Source of truth: PRD §5 and forecasting service specifications.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from app.models.common import UtcDatetime


class ForecastPoint(BaseModel):
    """Single predicted future time-series point."""

    timestamp: UtcDatetime = Field(..., description="Target timestamp of forecast point (UTC)")
    predicted_sri: float = Field(..., description="Extrapolated Spoilage Risk Index (clamped >= 0.0)")


class DeviceForecastResponse(BaseModel):
    """Response model for GET /devices/{device_id}/forecast."""

    device_id: str = Field(..., description="Shelf device identifier")
    generated_at: UtcDatetime = Field(..., description="Timestamp when forecast was computed (UTC)")
    commodity: Optional[str] = Field(None, description="Active assigned commodity type")
    current_sri: Optional[float] = Field(None, description="Latest raw reading SRI value")
    fan_threshold: Optional[float] = Field(None, description="Fan ON SRI threshold for active commodity")
    alert_threshold: Optional[float] = Field(None, description="High risk alert SRI threshold for active commodity")
    trend_slope_per_min: Optional[float] = Field(None, description="Least-squares linear trend slope per minute")
    insufficient_data: bool = Field(..., description="True if <4 distinct readings or <4 MA buckets")
    forecast: Optional[List[ForecastPoint]] = Field(None, description="Series of extrapolated forecast points")
