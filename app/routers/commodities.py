"""Router for querying reference commodity profiles.

Endpoints:
- GET /commodities: List available commodity profiles (id, current version, key thresholds).
- GET /commodities/{commodity_type}: Get latest profile for a given commodity.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.commodity_profile import CommodityProfile, CommodityProfileSummary

router = APIRouter(tags=["commodities"])


@router.get(
    "/commodities",
    response_model=List[CommodityProfileSummary],
    summary="List available commodity profiles",
    description="Returns the latest version and key thresholds for all seeded commodities.",
)
async def list_commodities(
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> List[CommodityProfileSummary]:
    """List current versions of all commodity profiles."""
    # Find all distinct commodity types
    commodity_types = await db["commodity_profiles"].distinct("commodity_type")
    summaries: List[CommodityProfileSummary] = []

    for c_type in commodity_types:
        doc = await db["commodity_profiles"].find_one(
            {"commodity_type": c_type},
            sort=[("effective_from", -1)],
        )
        if doc:
            summaries.append(
                CommodityProfileSummary(
                    commodity_type=doc["commodity_type"],
                    effective_from=doc["effective_from"],
                    optimal_temp_min=doc.get("optimal_temp_min"),
                    optimal_temp_max=doc.get("optimal_temp_max"),
                    optimal_rh_min=doc.get("optimal_rh_min"),
                    optimal_rh_max=doc.get("optimal_rh_max"),
                    chilling_threshold_c=doc.get("chilling_threshold_c"),
                    reference_temp_c=doc.get("reference_temp_c"),
                    q10=doc.get("q10"),
                    source=doc.get("source"),
                )
            )

    return summaries


@router.get(
    "/commodities/{commodity_type}",
    response_model=CommodityProfile,
    summary="Get full profile details for a specific commodity",
)
async def get_commodity_profile(
    commodity_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> CommodityProfile:
    """Fetch complete profile document for commodity."""
    doc = await db["commodity_profiles"].find_one(
        {"commodity_type": commodity_type},
        sort=[("effective_from", -1)],
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for commodity '{commodity_type}'.",
        )
    return CommodityProfile(**doc)
