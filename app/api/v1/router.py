"""Aggregates v1 routers under the ``/api/v1`` prefix."""

from fastapi import APIRouter

from app.api.v1 import health, investigations

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(investigations.router)
