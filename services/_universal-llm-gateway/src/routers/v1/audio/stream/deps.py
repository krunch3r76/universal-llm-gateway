"""Shared FastAPI router and logger for live audio WebSocket transcription.

Provides the APIRouter instance that live_transcribe registers the streaming
endpoint on, preserving the historical `stream.router` import path.
"""

from fastapi import APIRouter
from universal_logging import get_logger

router = APIRouter()
logger = get_logger(__name__)
