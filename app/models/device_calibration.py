"""Pydantic models for device_calibration collection.

Source of truth: PRD §3.4.
Versioned calibration data (e.g., MQ-135 clean air baseline resistance/ADC).
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import PyObjectId


class DeviceCalibrationCreate(BaseModel):
    """Payload for registering a calibration baseline for a shelf device."""

    mq135_baseline: float = Field(
        ...,
        gt=0,
        description="MQ-135 gas sensor baseline value in clean air (ADC reading or Ro)",
    )
    effective_from: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp from which this calibration is effective",
    )


class DeviceCalibration(BaseModel):
    """Device calibration document model."""

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    calibration_id: str = Field(..., description="Unique calibration identifier (e.g., 'cal-001')")
    device_id: str = Field(..., description="Device identifier (e.g., 'shelf-01')")
    mq135_baseline: float = Field(..., description="MQ-135 sensor baseline value")
    effective_from: datetime = Field(..., description="Timestamp from which this calibration is active")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )
