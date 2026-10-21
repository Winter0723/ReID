"""Focused routers for the supported scenic API surface."""

from fastapi import APIRouter

from app.api.scenic import (
    auth_routes,
    camera_routes,
    order_routes,
    project_routes,
    tourist_routes,
    vlog_routes,
    editing_routes,
    visitor_routes,
)


router = APIRouter()
router.include_router(auth_routes.router)
router.include_router(project_routes.router)
router.include_router(camera_routes.router)
router.include_router(tourist_routes.router)
router.include_router(vlog_routes.router)
router.include_router(order_routes.router)
router.include_router(editing_routes.router)
router.include_router(visitor_routes.router)
