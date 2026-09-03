"""Common model utilities and MongoDB ObjectId converters for Pydantic v2."""

from datetime import datetime, timezone
from typing import Annotated, Any, Optional
from pydantic import BeforeValidator, PlainSerializer


def validate_object_id(v: Any) -> Optional[str]:
    """Convert MongoDB ObjectId or any ID representation to string."""
    if v is None:
        return None
    return str(v)


PyObjectId = Annotated[
    Optional[str],
    BeforeValidator(validate_object_id),
    PlainSerializer(lambda x: str(x) if x is not None else None, return_type=Optional[str]),
]


def ensure_utc(v: Any) -> Any:
    """Ensure datetime values are timezone-aware UTC."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
    return v


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(ensure_utc),
]

OptionalUtcDatetime = Annotated[
    Optional[datetime],
    BeforeValidator(ensure_utc),
]
