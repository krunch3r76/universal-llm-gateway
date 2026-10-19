"""Shared routers for the frontier consult HTTP surface."""

from __future__ import annotations

from fastapi import APIRouter
from universal_logging import get_logger

team_router = APIRouter(prefix="/api/v1/team", tags=["team"])
frontier_router = APIRouter(prefix="/api/v1/frontier", tags=["frontier"])
implement_router = APIRouter(prefix="/api/v1/implement", tags=["implement"])
logger = get_logger("systems.frontier_consult.route")
