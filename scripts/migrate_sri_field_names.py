"""Migration: rename the SRI-family stored fields on existing documents.

    readings.spoilage_index  -> readings.sri
    alerts.peak_risk_value   -> alerts.peak_sri

Pairs with the terminology canonicalization pass. The application code reads the
new names; this brings already-persisted documents in line.

Idempotent: each rename is filtered on the OLD field existing, so a second run
matches nothing and reports 0. Safe to run against a partially-migrated database
for the same reason -- documents already carrying the new name are not matched.

Makes no other modification: no field is added, removed, or recomputed.

Usage:
    python scripts/migrate_sri_field_names.py
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Add project root to sys.path to allow imports from app
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.db import db_manager, DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("migrate_sri_field_names")

# (collection, old_field, new_field)
FIELD_RENAMES: List[Tuple[str, str, str]] = [
    ("readings", "spoilage_index", "sri"),
    ("alerts", "peak_risk_value", "peak_sri"),
]


async def migrate_sri_field_names(
    db_manager_instance: Optional[DatabaseManager] = None,
) -> List[Tuple[str, str, int, int]]:
    """Rename the stored SRI fields in place.

    Returns:
        List of (collection, old_field, matched, modified) tuples.
    """
    mgr = db_manager_instance or db_manager
    db = mgr.get_db()

    results: List[Tuple[str, str, int, int]] = []

    for collection, old_field, new_field in FIELD_RENAMES:
        # Only documents still carrying the old field are touched. Documents
        # already migrated do not match, which is what makes a repeat run a no-op.
        remaining = await db[collection].count_documents({old_field: {"$exists": True}})

        if remaining == 0:
            logger.info(
                "%s: no documents carry '%s'; nothing to do.",
                collection,
                old_field,
            )
            results.append((collection, old_field, 0, 0))
            continue

        result = await db[collection].update_many(
            {old_field: {"$exists": True}},
            {"$rename": {old_field: new_field}},
        )
        logger.info(
            "%s: renamed '%s' -> '%s' on %d document(s) (matched %d).",
            collection,
            old_field,
            new_field,
            result.modified_count,
            result.matched_count,
        )
        results.append(
            (collection, old_field, result.matched_count, result.modified_count)
        )

    total = sum(modified for _, _, _, modified in results)
    logger.info("Migration complete. %d document(s) modified in total.", total)
    return results


def main() -> None:
    """CLI entry point for the migration."""
    logger.info("Starting SRI field name migration...")
    db_manager.connect()
    try:
        results = asyncio.run(migrate_sri_field_names(db_manager_instance=db_manager))
        for collection, old_field, matched, modified in results:
            logger.info(
                "  %-10s %-16s matched=%d modified=%d",
                collection,
                old_field,
                matched,
                modified,
            )
    finally:
        db_manager.close()


if __name__ == "__main__":
    main()
