"""Shared FastAPI router for resource history and monitoring status endpoints.

Holds the APIRouter instance imported by route modules that register handlers
for model resource snapshots, usage statistics, and monitor availability.
"""

from fastapi import APIRouter
from universal_logging import get_logger

router = APIRouter()
logger = get_logger(__name__)
