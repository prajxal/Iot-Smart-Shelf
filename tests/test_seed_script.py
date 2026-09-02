"""Unit and idempotency tests for commodity profiles seed script.

Validates PRD §0, §3.1, and §3.7:
- Seeding copies numeric data verbatim from commodity-profiles.json
- Idempotency: re-running does not create duplicate versions
"""

import json
from pathlib import Path
import pytest
from app.db import DatabaseManager
from scripts.seed_commodity_profiles import seed_commodity_profiles


@pytest.mark.asyncio
async def test_seed_script_idempotency_and_fidelity(mock_db):
    """Test initial seed and verify idempotency on subsequent execution."""
    json_path = Path(__file__).resolve().parent.parent / "commodity-profiles.json"
    with open(json_path, "r", encoding="utf-8") as f:
        source_json = json.load(f)

    # First seed run on empty DB
    results_1 = await seed_commodity_profiles(json_path=json_path)
    assert len(results_1) == 4
    for c_type, action in results_1:
        assert action == "inserted"

    # Verify document count in MongoDB
    count_1 = await mock_db["commodity_profiles"].count_documents({})
    assert count_1 == 4

    # Second seed run on same DB without changes -> MUST SKIP ALL
    results_2 = await seed_commodity_profiles(json_path=json_path)
    assert len(results_2) == 4
    for c_type, action in results_2:
        assert action == "skipped"

    # Count must still be exactly 4 (no duplicate versions created)
    count_2 = await mock_db["commodity_profiles"].count_documents({})
    assert count_2 == 4

    # -------------------------------------------------------------------------
    # Numerical Fidelity Verification against commodity-profiles.json
    # -------------------------------------------------------------------------
    tomato_doc = await mock_db["commodity_profiles"].find_one({"commodity_type": "tomato"})
    assert tomato_doc is not None
    assert tomato_doc["optimal_temp_min"] == 13.0
    assert tomato_doc["optimal_temp_max"] == 21.0
    assert tomato_doc["optimal_rh_min"] == 90.0
    assert tomato_doc["optimal_rh_max"] == 95.0
    assert tomato_doc["chilling_threshold_c"] == 13.0
    assert tomato_doc["reference_temp_c"] == 20.0
    assert tomato_doc["ethylene_production_uL_kg_h"] == [1.0, 10.0]
    assert tomato_doc["ethylene_sensitivity_uL_L"] == 0.5
    assert tomato_doc["source"] == "AH-66 pp. 581-585"

    onion_doc = await mock_db["commodity_profiles"].find_one({"commodity_type": "onion"})
    assert onion_doc is not None
    assert onion_doc["optimal_temp_min"] == 0.0
    assert onion_doc["optimal_temp_max"] == 0.0
    assert onion_doc["optimal_rh_min"] == 65.0
    assert onion_doc["optimal_rh_max"] == 75.0
    assert onion_doc["chilling_threshold_c"] is None
    assert onion_doc["reference_temp_c"] == 5.0
    assert onion_doc["q10"] == 1.14
    assert onion_doc["ethylene_production_uL_kg_h"] == 0.1
    assert onion_doc["ethylene_sensitivity_uL_L"] == 1500.0

    potato_doc = await mock_db["commodity_profiles"].find_one({"commodity_type": "potato"})
    assert potato_doc is not None
    assert potato_doc["optimal_temp_min"] == 7.0
    assert potato_doc["optimal_temp_max"] == 10.0
    assert potato_doc["optimal_rh_min"] == 80.0
    assert potato_doc["optimal_rh_max"] == 100.0
    assert potato_doc["chilling_threshold_c"] == 2.0
    assert potato_doc["reference_temp_c"] == 20.0
    assert potato_doc["q10"] == 1.34

    leafy_doc = await mock_db["commodity_profiles"].find_one({"commodity_type": "leafy_greens"})
    assert leafy_doc is not None
    assert leafy_doc["optimal_temp_min"] == 0.0
    assert leafy_doc["optimal_temp_max"] == 0.0
    assert leafy_doc["optimal_rh_min"] == 95.0
    assert leafy_doc["optimal_rh_max"] == 98.0
    assert leafy_doc["chilling_threshold_c"] is None
    assert leafy_doc["q10"] == 2.09
