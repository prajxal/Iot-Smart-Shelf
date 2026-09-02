"""Routers package for Smart Shelf API."""

from app.routers.commodities import router as commodities_router
from app.routers.devices import router as devices_router
from app.routers.health import router as health_router
from app.routers.readings import router as readings_router

__all__ = [
    "commodities_router",
    "devices_router",
    "health_router",
    "readings_router",
]
