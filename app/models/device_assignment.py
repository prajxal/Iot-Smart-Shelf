"""Pydantic models for device_assignments collection.

Source of truth: PRD §3.3 & §3.7.2.
Maintains history of which commodity was monitored by which shelf over time.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import PyObjectId


class DeviceAssignmentCreate(BaseModel):
    """Payload for assigning a commodity to a shelf device."""

    commodity_type: str = Field(..., description="Commodity type to assign (must exist in commodity_profiles)")
    start_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Effective start time for this assignment",
    )


class DeviceAssignment(BaseModel):
    """Device assignment document model."""

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    assignment_id: str = Field(..., description="Unique assignment identifier (e.g., 'asg-001')")
    device_id: str = Field(..., description="Device identifier (e.g., 'shelf-01')")
    commodity_type: str = Field(..., description="Assigned commodity (e.g., 'tomato')")
    start_at: datetime = Field(..., description="Start timestamp of this assignment")
    end_at: Optional[datetime] = Field(
        default=None,
        description="End timestamp. NULL indicates this assignment is currently active.",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )
