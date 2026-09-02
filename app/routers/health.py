"""Health and liveness router.

Endpoint:
- GET /health: Liveness and database connectivity probe.
"""

from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.db import get_database

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Service liveness and database health check",
)
async def health_check(
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> Dict[str, Any]:
    """Return application health status and database connectivity."""
    db_status = "unknown"
    try:
        # Ping database
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"unreachable: {str(e)}"

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.api_version,
        "database": db_status,
    }
