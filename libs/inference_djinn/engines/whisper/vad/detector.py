"""
Unified boundary detector providing access to all VAD methods.

Architecture: Supports both sync and async initialization patterns.
- Use create_async() factory for async contexts (non-blocking Silero load)
- Use __init__() for sync contexts (blocking, use sparingly)
"""

from typing import Any

import numpy as np
from universal_logging import get_logger

from .base import BaseVAD, VADConfig, VADMethod
from .energy import EnergyVAD
from .silero import SileroVAD
from .webrtc import WebRTCVAD


class BoundaryDetector:
    """
    Unified boundary detector with automatic fallback.

    Preferred usage (async):
        detector = await BoundaryDetector.create_async(config)

    Sync usage (blocking - avoid in async contexts):
        detector = BoundaryDetector(config)
    """

    def __init__(
        self,
        config: VADConfig | None = None,
        sample_rate: int = 16000,
        *,
        _skip_init: bool = False,
    ):
        """
        Initialize detector. Use create_async() in async contexts.

        Args:
            config: VAD configuration
            sample_rate: Audio sample rate in Hz
            _skip_init: Internal flag for async factory
        """
        self.config = config or VADConfig()
        self.sample_rate = sample_rate
        self.vad_methods: dict[VADMethod, BaseVAD] = {}
        self.default_method = VADMethod.ENERGY
        self.logger = get_logger("speech-vad.detector")

        if not _skip_init:
            self._initialize_vad_methods_sync()
            self.default_method = self._determine_default_method()
            self._log_init_status()

    @classmethod
    async def create_async(
        cls,
        config: VADConfig | None = None,
        sample_rate: int = 16000,
    ) -> "BoundaryDetector":
        """
        Async factory - non-blocking Silero initialization.

        This is the preferred way to create a BoundaryDetector in async contexts.
        """
        detector = cls(config, sample_rate, _skip_init=True)
        await detector._initialize_vad_methods_async()
        detector.default_method = detector._determine_default_method()
        detector._log_init_status()
        return detector

    def _log_init_status(self):
        """Log initialization status."""
        self.logger.info(
            f"🚀 BoundaryDetector: {len(self.vad_methods)} methods available"
        )
        self.logger.info(f"   📊 Methods: {[m.value for m in self.vad_methods.keys()]}")
        self.logger.info(f"   🎯 Default: {self.default_method.value}")

    def _initialize_vad_methods_sync(self):
        """Initialize VAD methods synchronously (may block on Silero)."""
        # Energy VAD - always available, cheap init
        self.vad_methods[VADMethod.ENERGY] = EnergyVAD(
            self.config, self.sample_rate, enhanced_mode=False
        )

        # Silero VAD - check can_load() first to avoid unnecessary init
        silero_vad = SileroVAD(self.config, self.sample_rate)
        if silero_vad.can_load():
            # This triggers blocking torch.hub.load() on first detect_boundary()
            # or we can force it now:
            silero_vad._ensure_loaded_sync()
            if silero_vad.is_available():
                self.vad_methods[VADMethod.SILERO] = silero_vad

        # WebRTC VAD - cheap init
        webrtc_vad = WebRTCVAD(self.config, self.sample_rate)
        if webrtc_vad.is_available():
            self.vad_methods[VADMethod.WEBRTC] = webrtc_vad

    async def _initialize_vad_methods_async(self):
        """Initialize VAD methods asynchronously (non-blocking)."""
        # Energy VAD - always available
        self.vad_methods[VADMethod.ENERGY] = EnergyVAD(
            self.config, self.sample_rate, enhanced_mode=False
        )

        # Silero VAD - async load via run_in_executor
        silero_vad = SileroVAD(self.config, self.sample_rate)
        if silero_vad.can_load():
            if await silero_vad.ensure_loaded_async():
                self.vad_methods[VADMethod.SILERO] = silero_vad

        # WebRTC VAD - cheap init
        webrtc_vad = WebRTCVAD(self.config, self.sample_rate)
        if webrtc_vad.is_available():
            self.vad_methods[VADMethod.WEBRTC] = webrtc_vad

    def _determine_default_method(self) -> VADMethod:
        """Determine best default VAD method based on availability."""
        if VADMethod.SILERO in self.vad_methods:
            return VADMethod.SILERO
        elif VADMethod.WEBRTC in self.vad_methods:
            return VADMethod.WEBRTC
        return VADMethod.ENERGY

    def detect_boundary(
        self,
        audio: np.ndarray,
        check_duration_ms: int,
        method: VADMethod | None = None,
        fallback_on_error: bool = True,
    ) -> bool | None:
        """
        Detect speech boundary using specified or default VAD method.

        Returns:
            True: Silence boundary detected (send to transcription)
            False: Speech ongoing (keep accumulating)
            None: No speech detected (skip transcription)
        """
        if method is None:
            method = self.default_method

        if method not in self.vad_methods:
            if fallback_on_error:
                self.logger.warning(
                    f"⚠️ {method.value} not available - using {self.default_method.value}"
                )
                method = self.default_method
            else:
                raise ValueError(f"VAD method {method} not available")

        try:
            vad = self.vad_methods[method]
            result = vad.detect_boundary(audio, check_duration_ms)
            self.logger.debug(f"🔍 {method.value} boundary: {result}")
            return result

        except Exception as e:
            if fallback_on_error and method != VADMethod.ENERGY:
                self.logger.warning(
                    f"⚠️ {method.value} error: {e} - falling back to energy"
                )
                return self.vad_methods[VADMethod.ENERGY].detect_boundary(
                    audio, check_duration_ms
                )
            raise

    def get_available_methods(self) -> list[VADMethod]:
        """Get list of available VAD methods."""
        return list(self.vad_methods.keys())

    def is_method_available(self, method: VADMethod) -> bool:
        """Check if a specific VAD method is available."""
        return method in self.vad_methods

    def get_method_info(self, method: VADMethod) -> dict[str, Any]:
        """Get information about a specific VAD method."""
        if method not in self.vad_methods:
            return {"available": False, "error": f"{method} not available"}

        vad = self.vad_methods[method]
        return {
            "available": True,
            "name": vad.get_name(),
            "method": method,
            "is_default": method == self.default_method,
        }

    def analyze_audio_with_all_methods(
        self, audio: np.ndarray, check_duration_ms: int
    ) -> dict[str, Any]:
        """Analyze audio with all available VAD methods for comparison."""
        results: dict[str, Any] = {
            "audio_duration": len(audio) / self.sample_rate,
            "check_duration_ms": check_duration_ms,
            "methods": {},
        }

        for method, vad in self.vad_methods.items():
            try:
                boundary = vad.detect_boundary(audio, check_duration_ms)

                method_result: dict[str, Any] = {
                    "boundary_detected": boundary,
                    "method_name": vad.get_name(),
                    "is_default": method == self.default_method,
                    "error": None,
                }

                if hasattr(vad, "get_energy_stats") and method == VADMethod.ENERGY:
                    method_result["energy_stats"] = vad.get_energy_stats(audio)
                elif (
                    hasattr(vad, "analyze_speech_activity")
                    and method == VADMethod.SILERO
                ):
                    method_result["speech_activity"] = vad.analyze_speech_activity(
                        audio
                    )
                elif (
                    hasattr(vad, "analyze_voice_activity")
                    and method == VADMethod.WEBRTC
                ):
                    method_result["voice_activity"] = vad.analyze_voice_activity(audio)

                results["methods"][method.value] = method_result

            except Exception as e:
                results["methods"][method.value] = {
                    "boundary_detected": None,
                    "method_name": vad.get_name(),
                    "is_default": method == self.default_method,
                    "error": str(e),
                }

        boundary_results = [
            r["boundary_detected"]
            for r in results["methods"].values()
            if r["boundary_detected"] is not None
        ]

        if boundary_results:
            results["consensus"] = {
                "total_methods": len(boundary_results),
                "boundary_detected_count": sum(boundary_results),
                "boundary_consensus_ratio": sum(boundary_results)
                / len(boundary_results),
                "unanimous_boundary": all(boundary_results),
                "unanimous_no_boundary": not any(boundary_results),
            }

        return results

    def set_default_method(self, method: VADMethod):
        """Set the default VAD method."""
        if method not in self.vad_methods:
            raise ValueError(f"Cannot set default to {method} - not available")

        old = self.default_method
        self.default_method = method
        self.logger.info(f"🔄 Default VAD: {old.value} → {method.value}")

    async def update_config_async(self, new_config: VADConfig):
        """Update configuration and reinitialize methods asynchronously."""
        self.config = new_config
        self.vad_methods.clear()
        await self._initialize_vad_methods_async()

        if self.default_method not in self.vad_methods:
            self.default_method = self._determine_default_method()

        self.logger.info(
            f"🔄 Config updated - {len(self.vad_methods)} methods available"
        )

    def update_config(self, new_config: VADConfig):
        """Update configuration and reinitialize methods synchronously."""
        self.config = new_config
        self.vad_methods.clear()
        self._initialize_vad_methods_sync()

        if self.default_method not in self.vad_methods:
            self.default_method = self._determine_default_method()

        self.logger.info(
            f"🔄 Config updated - {len(self.vad_methods)} methods available"
        )
