"""Pydantic models for readings collection and sensor ingress.

Source of truth: PRD §3.5, §4, and §5.5.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import PyObjectId


class ReadingCreate(BaseModel):
    """Sensor payload sent by ESP32 via HTTP POST."""

    device_seq: int = Field(..., description="Monotonically increasing sequence number from ESP32")
    device_timestamp: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp on device at the time of sampling",
    )
    temp_c: float = Field(..., description="Ambient temperature reading in Celsius from DHT22")
    humidity_pct: float = Field(..., description="Relative humidity reading in percent from DHT22")
    gas_raw: float = Field(..., description="Raw analog/ADC reading from MQ-135 sensor")
    sensor_status: Optional[str] = Field(
        default="ok",
        description="Sensor health flag ('ok', 'dht22_error', 'mq135_error')",
    )


class Reading(BaseModel):
    """Reading document stored in MongoDB `readings` collection."""

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    reading_id: str = Field(..., description="Unique reading identifier (e.g., 'rd-000001')")
    device_id: str = Field(..., description="Device identifier (e.g., 'shelf-01')")
    device_seq: int = Field(..., description="Sequence number from device")
    device_timestamp: datetime = Field(..., description="Timestamp on device")
    server_received_at: datetime = Field(..., description="Timestamp when received by backend")
    temp_c: float = Field(..., description="Temperature in Celsius")
    humidity_pct: float = Field(..., description="Relative humidity percentage")
    gas_raw: float = Field(..., description="Raw gas sensor reading")
    sensor_status: str = Field(default="ok", description="Sensor health status")
    spoilage_index: Optional[float] = Field(default=None, description="Computed Spoilage Risk Index (0.0 - 1.0)")
    fan_commanded: Optional[bool] = Field(default=None, description="True if fan was commanded ON, False if OFF")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


class ReadingResponse(BaseModel):
    """Synchronous response sent back to the ESP32."""

    reading_id: str = Field(..., description="Reading identifier")
    device_id: str = Field(..., description="Device identifier")
    fan_command: str = Field(..., description="Actuator command: 'on' or 'off'")
    spoilage_index: float = Field(..., description="Computed Spoilage Risk Index (SRI)")
    interlock_triggered: bool = Field(
        default=False,
        description="True if chilling injury safety interlock forced fan OFF",
    )
    gas_override_triggered: bool = Field(
        default=False,
        description="True if an extreme gas reading forced fan-on and alert-open independent of composite SRI",
    )
    sensor_status: str = Field(default="ok", description="Sensor status acknowledge")
