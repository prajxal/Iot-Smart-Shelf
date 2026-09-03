"""Pydantic models for alerts collection.

Source of truth: PRD §3.6 and §3.7.3.
Tracks high spoilage risk episodes with audit trail back to triggering sensor reading.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import OptionalUtcDatetime, PyObjectId, UtcDatetime


class Alert(BaseModel):
    """Alert document model representing a spoilage risk event."""

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    alert_id: str = Field(..., description="Unique alert identifier (e.g., 'alt-001')")
    device_id: str = Field(..., description="Device identifier (e.g., 'shelf-01')")
    alert_type: str = Field(default="high_spoilage_risk", description="Alert category")
    status: str = Field(default="open", description="Alert status: 'open' or 'resolved'")
    opened_at: UtcDatetime = Field(..., description="Timestamp when alert was triggered (always UTC)")
    resolved_at: OptionalUtcDatetime = Field(default=None, description="Timestamp when risk subsided (always UTC)")
    peak_risk_value: float = Field(..., description="Highest SRI value recorded during this alert episode")
    opened_by_reading_id: Optional[str] = Field(
        default=None,
        description="ID of the reading that originally tripped the alert threshold (audit link)",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )
