"""
Speech Voice Activity Detection (VAD) Package

Provides multiple VAD methods for detecting speech boundaries in audio streams:
- Energy-based detection (simple, fast, always available)
- Silero VAD (AI-based, accurate, GPU-only)
- WebRTC VAD (industry standard)

Usage:
    from inference_djinn.engines.whisper.vad import VADMethod, BoundaryDetector

    detector = BoundaryDetector(config)
    boundary_found = detector.detect_boundary(audio, VADMethod.SILERO)
"""

from .base import BaseVAD, VADConfig, VADMethod
from .detector import BoundaryDetector
from .energy import EnergyVAD
from .silero import SileroVAD
from .webrtc import WebRTCVAD

__version__ = "1.0.0"

__all__ = [
    "VADMethod",
    "VADConfig",
    "BaseVAD",
    "BoundaryDetector",
    "EnergyVAD",
    "SileroVAD",
    "WebRTCVAD",
]
