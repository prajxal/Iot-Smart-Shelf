"""Seed script for commodity_profiles collection.

Parses commodity-profiles.json and inserts one versioned document per commodity into
the MongoDB commodity_profiles collection.

Requirements (PRD §0, §3.1, §3.7):
- Copy every numeric field verbatim from commodity-profiles.json (no invented/hardcoded values).
- Set effective_from to the seed run's timestamp.
- Fully idempotent: running multiple times will NOT insert duplicate profile versions
  unless the source JSON content has actually changed (tracked via SHA256 content hash).
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to sys.path to allow imports from app
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import settings
from app.db import db_manager, DatabaseManager, init_db_indexes
from app.models.commodity_profile import CommodityProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_commodity_profiles")


def compute_content_hash(data: Dict[str, Any]) -> str:
    """Compute deterministic SHA256 hash of commodity data dict."""
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def parse_commodity(
    commodity_type: str,
    raw: Dict[str, Any],
    seed_timestamp: datetime,
) -> CommodityProfile:
    """Parse raw JSON dict from commodity-profiles.json into CommodityProfile model.

    Copies all fields verbatim without rounding, approximating, or inventing values.
    """
    # 1. Temperature bands (°C)
    opt_temp = raw.get("optimal_storage_temp_c")
    opt_temp_min: Optional[float] = None
    opt_temp_max: Optional[float] = None

    if isinstance(opt_temp, dict):
        # E.g. tomato: {"mature_green": [13, 21], "red_ripe": [7, 15]}
        # E.g. potato: {"fresh_consumption": [7, 10], ...}
        # Use mature_green for tomato (PRD §3.1) or fresh_consumption for potato
        if "mature_green" in opt_temp:
            opt_temp_min = float(opt_temp["mature_green"][0])
            opt_temp_max = float(opt_temp["mature_green"][1])
        elif "fresh_consumption" in opt_temp:
            opt_temp_min = float(opt_temp["fresh_consumption"][0])
            opt_temp_max = float(opt_temp["fresh_consumption"][1])
        else:
            first_val = next(iter(opt_temp.values()))
            opt_temp_min = float(first_val[0])
            opt_temp_max = float(first_val[1])
    elif isinstance(opt_temp, list) and len(opt_temp) == 2:
        # E.g. onion: [0, 0], leafy_greens: [0, 0]
        opt_temp_min = float(opt_temp[0])
        opt_temp_max = float(opt_temp[1])

    # 2. Relative Humidity bands (%)
    opt_rh_min: Optional[float] = None
    opt_rh_max: Optional[float] = None
    resolved_rh_key: Optional[str] = None

    if "optimal_rh_pct" in raw and isinstance(raw["optimal_rh_pct"], list):
        resolved_rh_key = "optimal_rh_pct"
        opt_rh_min = float(raw["optimal_rh_pct"][0])
        opt_rh_max = float(raw["optimal_rh_pct"][1])
    elif "ripening_rh_pct" in raw and isinstance(raw["ripening_rh_pct"], list):
        resolved_rh_key = "ripening_rh_pct"
        opt_rh_min = float(raw["ripening_rh_pct"][0])
        opt_rh_max = float(raw["ripening_rh_pct"][1])
    elif "curing_rh_pct" in raw and isinstance(raw["curing_rh_pct"], list):
        resolved_rh_key = "curing_rh_pct"
        opt_rh_min = float(raw["curing_rh_pct"][0])
        opt_rh_max = float(raw["curing_rh_pct"][1])

    if resolved_rh_key and resolved_rh_key != "optimal_rh_pct":
        logger.warning(
            "Commodity '%s': no optimal_rh_pct found; using '%s' as RH proxy. "
            "Check the profile's *_note field for the documented caveat.",
            commodity_type,
            resolved_rh_key,
        )

    # 3. Chilling injury thresholds
    chilling_threshold_c = (
        float(raw["chilling_injury_threshold_c"])
        if raw.get("chilling_injury_threshold_c") is not None
        else None
    )
    chilling_sensitivity = raw.get("chilling_note")

    # 4. Reference temperature (°C)
    ref_temp = raw.get("retail_display_temp_c")
    if ref_temp is None:
        ref_temp = raw.get("curing_conditions_c")
    reference_temp_c = float(ref_temp) if ref_temp is not None else None

    # 5. Q10 coefficients
    q10_raw = raw.get("q10") or raw.get("q10_mature_cured")
    q10_val: Optional[float] = None
    q10_bands: Optional[Dict[str, float]] = None

    if isinstance(q10_raw, dict):
        q10_bands = {k: float(v) for k, v in q10_raw.items()}
        # Select ambient band: preferably 10_20, 15_25, or 5_15
        if "10_20" in q10_bands:
            q10_val = q10_bands["10_20"]
        elif "15_25" in q10_bands:
            q10_val = q10_bands["15_25"]
        elif "5_15" in q10_bands:
            q10_val = q10_bands["5_15"]
        else:
            q10_val = next(iter(q10_bands.values()))
    elif isinstance(q10_raw, (int, float)):
        q10_val = float(q10_raw)

    # 6. Ethylene parameters
    eth_prod = raw.get("ethylene_production_uL_kg_h_at_20c")
    if isinstance(eth_prod, list):
        ethylene_production_uL_kg_h: Optional[Any] = [float(x) for x in eth_prod]
    elif isinstance(eth_prod, (int, float)):
        ethylene_production_uL_kg_h = float(eth_prod)
    else:
        ethylene_production_uL_kg_h = None

    eth_sens = raw.get("ethylene_sensitivity_threshold_uL_L")
    ethylene_sensitivity_uL_L = float(eth_sens) if eth_sens is not None else None

    source_pages = raw.get("source_pages")
    content_hash = compute_content_hash(raw)
    doc_id = f"{commodity_type}__{seed_timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    return CommodityProfile(
        id=doc_id,
        commodity_type=commodity_type,
        effective_from=seed_timestamp,
        optimal_temp_min=opt_temp_min,
        optimal_temp_max=opt_temp_max,
        optimal_rh_min=opt_rh_min,
        optimal_rh_max=opt_rh_max,
        chilling_sensitivity=chilling_sensitivity,
        chilling_threshold_c=chilling_threshold_c,
        reference_temp_c=reference_temp_c,
        q10=q10_val,
        q10_bands=q10_bands,
        ethylene_production_uL_kg_h=ethylene_production_uL_kg_h,
        ethylene_sensitivity_uL_L=ethylene_sensitivity_uL_L,
        source=source_pages,
        content_hash=content_hash,
        raw_data=raw,
    )


async def seed_commodity_profiles(
    json_path: Optional[Path] = None,
    db_manager_instance: Optional[DatabaseManager] = None,
) -> List[Tuple[str, str]]:
    """Seed commodity profiles into MongoDB.

    Returns:
        List of (commodity_type, action) tuples indicating 'inserted' or 'skipped'.
    """
    path = json_path or (project_root / "commodity-profiles.json")
    if not path.exists():
        raise FileNotFoundError(f"Commodity profiles source file not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    commodities = data.get("commodities", {})
    if not commodities:
        raise ValueError("No commodities found in JSON source file")

    mgr = db_manager_instance or db_manager
    db = mgr.get_db()

    # Ensure indexes exist
    await init_db_indexes(db)

    results: List[Tuple[str, str]] = []
    seed_now = datetime.now(timezone.utc)

    for commodity_type, raw in commodities.items():
        current_hash = compute_content_hash(raw)

        # Check latest profile for this commodity to guarantee idempotency
        latest_doc = await db["commodity_profiles"].find_one(
            {"commodity_type": commodity_type},
            sort=[("effective_from", -1)],
        )

        if latest_doc and latest_doc.get("content_hash") == current_hash:
            logger.info(
                "Profile for '%s' is up to date (hash: %s). Skipping insert.",
                commodity_type,
                current_hash[:8],
            )
            results.append((commodity_type, "skipped"))
            continue

        # Parse and insert new version
        profile = parse_commodity(commodity_type, raw, seed_now)
        doc_dict = profile.model_dump(by_alias=True)

        await db["commodity_profiles"].insert_one(doc_dict)
        logger.info(
            "Inserted new profile version for '%s' (id: %s, hash: %s, q10: %s, chilling_threshold: %s)",
            commodity_type,
            profile.id,
            current_hash[:8],
            profile.q10,
            profile.chilling_threshold_c,
        )
        results.append((commodity_type, "inserted"))

    return results


def main() -> None:
    """CLI entry point for seeding."""
    logger.info("Starting commodity profile seed script...")
    db_manager.connect()
    try:
        results = asyncio.run(seed_commodity_profiles(db_manager_instance=db_manager))
        logger.info("Seeding completed successfully: %s", results)
    finally:
        db_manager.close()


if __name__ == "__main__":
    main()
