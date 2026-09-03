"""Pydantic models for devices collection.

Source of truth: PRD §3.2.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import OptionalUtcDatetime, PyObjectId, UtcDatetime


class DeviceCreate(BaseModel):
    """Payload for registering a new shelf device."""

    device_id: str = Field(..., description="Unique hardware identifier for the shelf unit")
    location: str = Field(..., description="Kirana store location or shelf label")
    installed_at: OptionalUtcDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Installation timestamp (always UTC)",
    )


class Device(BaseModel):
    """Device document model representing a shelf hardware unit."""

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    device_id: str = Field(..., description="Unique hardware identifier (e.g., 'shelf-01')")
    location: str = Field(..., description="Store location (e.g., 'kirana-store-A')")
    installed_at: UtcDatetime = Field(..., description="Timestamp of installation (always UTC)")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )
