"""MongoDB database client and connection management using Motor (async).

Handles database connection lifecycles and index initialization.
"""

import logging
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

logger = logging.getLogger("smart_shelf.db")


class DatabaseManager:
    """Manages async MongoDB client and database connections."""

    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None

    def connect(self, uri: Optional[str] = None, db_name: Optional[str] = None) -> AsyncIOMotorDatabase:
        """Initialize connection to MongoDB if not already initialized."""
        if self.db is not None:
            return self.db

        mongo_uri = uri or settings.mongodb_uri
        database_name = db_name or settings.mongodb_db_name

        logger.info("Connecting to MongoDB at %s (database: %s)", mongo_uri.split("@")[-1], database_name)
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[database_name]
        return self.db

    def close(self) -> None:
        """Close connection to MongoDB."""
        if self.client is not None:
            logger.info("Closing MongoDB connection.")
            self.client.close()
            self.client = None
        self.db = None

    def get_db(self) -> AsyncIOMotorDatabase:
        """Get the current database instance."""
        if self.db is None:
            self.connect()
        return self.db

    def set_db(self, db: Optional[AsyncIOMotorDatabase]) -> None:
        """Explicitly set database instance (useful for unit testing with mongomock-motor)."""
        self.db = db


db_manager = DatabaseManager()


def get_database() -> AsyncIOMotorDatabase:
    """Dependency helper to get active database."""
    return db_manager.get_db()


async def init_db_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes specified in PRD §6 and data models.

    Indexes:
    - commodity_profiles: {commodity_type: 1, effective_from: -1}
    - device_calibration: {device_id: 1, effective_from: -1}
    - device_assignments: {device_id: 1, end_at: 1}, {device_id: 1, start_at: -1}
    - readings: {device_id: 1, device_timestamp: -1}, {reading_id: 1} (unique)
    - alerts: {device_id: 1, status: 1}, {alert_id: 1} (unique)
    - devices: {device_id: 1} (unique)
    """
    logger.info("Initializing database indexes...")

    # commodity_profiles (PRD §3.1 / §6)
    await db["commodity_profiles"].create_index([("commodity_type", 1), ("effective_from", -1)])

    # device_calibration (PRD §3.4 / §6)
    await db["device_calibration"].create_index([("device_id", 1), ("effective_from", -1)])

    # device_assignments (PRD §3.3 / §6)
    await db["device_assignments"].create_index([("device_id", 1), ("end_at", 1)])
    await db["device_assignments"].create_index([("device_id", 1), ("start_at", -1)])

    # readings (PRD §3.5 / §6)
    await db["readings"].create_index([("device_id", 1), ("device_timestamp", -1)])
    await db["readings"].create_index([("reading_id", 1)], unique=True)

    # alerts (PRD §3.6 / §6)
    await db["alerts"].create_index([("device_id", 1), ("status", 1)])
    await db["alerts"].create_index([("alert_id", 1)], unique=True)

    # devices (PRD §3.2)
    await db["devices"].create_index([("device_id", 1)], unique=True)

    logger.info("Database indexes successfully initialized.")
