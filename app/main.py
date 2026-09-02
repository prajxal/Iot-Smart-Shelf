"""FastAPI main application entry point for Smart Shelf backend.

Initializes database connections, indexes, lifecycle events, and mounts API routers.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import db_manager, init_db_indexes
from app.routers.commodities import router as commodities_router
from app.routers.devices import router as devices_router
from app.routers.health import router as health_router
from app.routers.readings import router as readings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smart_shelf.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for database connection and startup tasks."""
    logger.info("Starting up Smart Shelf backend...")
    if db_manager.db is None:
        db = db_manager.connect()
        try:
            await init_db_indexes(db)
        except Exception as err:
            logger.warning("Index initialization warning (could be pending replica/cluster connection): %s", err)

    yield

    if db_manager.client is not None:
        logger.info("Shutting down Smart Shelf backend...")
        db_manager.close()


def create_app() -> FastAPI:
    """Factory function to configure and instantiate the FastAPI application."""
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=settings.api_description,
        lifespan=lifespan,
    )

    # CORS configuration for kirana dashboard or web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers (PRD §4)
    app.include_router(health_router)
    app.include_router(readings_router)
    app.include_router(devices_router)
    app.include_router(commodities_router)

    return app


app = create_app()
