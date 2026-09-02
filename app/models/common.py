"""Common model utilities and MongoDB ObjectId converters for Pydantic v2."""

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
