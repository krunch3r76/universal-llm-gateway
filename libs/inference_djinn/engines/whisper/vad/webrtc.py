"""
WebRTC VAD implementation for speech boundary detection.
"""

from typing import Any

import numpy as np

from .base import BaseVAD, VADConfig
from .energy import EnergyVAD

# Module-level guard to avoid repeated ImportError warnings
_webrtcvad_import_warned = False


class WebRTCVAD(BaseVAD):
    """WebRTC VAD-based Voice Activity Detection."""

    def __init__(self, config: VADConfig, sample_rate: int = 16000):
        super().__init__(config, sample_rate)

        self.webrtc_vad = None
        self._energy_fallback: EnergyVAD | None = None  # Lazy init
        self._init_attempted = False

        # Pre-calculate frame parameters (uses config defaults)
        self.frame_duration_ms = self.config.webrtc_frame_duration_ms
        self.frame_size_samples = int(self.sample_rate * self.frame_duration_ms / 1000)
        self.frame_size_bytes = self.frame_size_samples * 2

        # Initialize WebRTC VAD
        self._initialize_webrtc()

    def _get_energy_fallback(self) -> EnergyVAD:
        """Lazy-initialize energy fallback."""
        if self._energy_fallback is None:
            self._energy_fallback = EnergyVAD(self.config, self.sample_rate)
        return self._energy_fallback

    def _initialize_webrtc(self):
        """Initialize WebRTC VAD with import guard."""
        global _webrtcvad_import_warned

        if self._init_attempted:
            return

        self._init_attempted = True

        try:
            import webrtcvad

            self.webrtc_vad = webrtcvad.Vad(self.config.webrtc_aggressiveness)
            self.logger.info(
                f"✅ WebRTC VAD initialized (aggressiveness={self.config.webrtc_aggressiveness})"
            )

        except ImportError:
            if not _webrtcvad_import_warned:
                self.logger.warning("⚠️ webrtcvad not installed - pip install webrtcvad")
                _webrtcvad_import_warned = True
            self.webrtc_vad = None

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize WebRTC VAD: {e}")
            self.webrtc_vad = None

    def is_available(self) -> bool:
        """Check if WebRTC VAD is properly initialized."""
        return self.webrtc_vad is not None

    def detect_boundary(self, audio: np.ndarray, check_duration_ms: int) -> bool | None:
        """
        Detect speech boundary using WebRTC VAD.

        Args:
            audio: Audio segment to analyze (float32, -1.0 to 1.0)
            check_duration_ms: Duration of audio segment in milliseconds

        Returns:
            True if silence boundary detected (send to transcription)
            False if speech is ongoing (keep accumulating audio)
            None if no speech detected (skip transcription entirely)
        """
        if not self.validate_audio(audio):
            return False

        if not self.is_available():
            self.logger.debug("WebRTC VAD not available - using energy fallback")
            return self._get_energy_fallback().detect_boundary(audio, check_duration_ms)

        try:
            audio_pcm = (audio * 32767).astype(np.int16)

            voice_frames = 0
            total_frames = 0

            for i in range(0, len(audio_pcm), self.frame_size_samples):
                frame = audio_pcm[i : i + self.frame_size_samples]

                if len(frame) == self.frame_size_samples:
                    is_speech = self.webrtc_vad.is_speech(
                        frame.tobytes(), self.sample_rate
                    )
                    if is_speech:
                        voice_frames += 1
                    total_frames += 1

            if total_frames == 0:
                return None

            if voice_frames == 0:
                self.logger.debug(
                    f"🎙️ WebRTC: No speech in {total_frames} frames - skipping"
                )
                return None

            voice_ratio = voice_frames / total_frames

            if voice_ratio < 0.2:
                self.logger.debug(
                    f"🎙️ WebRTC: Low voice ratio ({voice_ratio:.3f}) - no speech"
                )
                return None

            boundary_detected = voice_ratio < self.config.webrtc_voice_threshold

            self.logger.debug(
                f"🎙️ WebRTC: {voice_frames}/{total_frames} voice, "
                f"ratio={voice_ratio:.3f}, boundary={boundary_detected}"
            )

            return boundary_detected

        except Exception as e:
            self.logger.warning(f"⚠️ WebRTC VAD error: {e} - using energy fallback")
            return self._get_energy_fallback().detect_boundary(audio, check_duration_ms)

    def get_voice_activity_frames(self, audio: np.ndarray) -> list[dict[str, Any]]:
        """Get detailed voice activity analysis per frame."""
        if not self.validate_audio(audio):
            return []

        if not self.is_available():
            return []

        try:
            audio_pcm = (audio * 32767).astype(np.int16)
            frame_results = []

            for i, start_sample in enumerate(
                range(0, len(audio_pcm), self.frame_size_samples)
            ):
                frame = audio_pcm[start_sample : start_sample + self.frame_size_samples]

                if len(frame) == self.frame_size_samples:
                    start_time = start_sample / self.sample_rate
                    end_time = (
                        start_sample + self.frame_size_samples
                    ) / self.sample_rate

                    is_speech = self.webrtc_vad.is_speech(
                        frame.tobytes(), self.sample_rate
                    )

                    frame_results.append(
                        {
                            "frame_id": i,
                            "start_time": start_time,
                            "end_time": end_time,
                            "duration": self.frame_duration_ms / 1000,
                            "is_speech": is_speech,
                            "frame_size": len(frame),
                        }
                    )

            return frame_results

        except Exception as e:
            self.logger.error(f"❌ Error analyzing voice activity frames: {e}")
            return []

    def analyze_voice_activity(self, audio: np.ndarray) -> dict[str, Any]:
        """Perform detailed voice activity analysis using WebRTC VAD."""
        if not self.validate_audio(audio):
            return {}

        try:
            frame_results = self.get_voice_activity_frames(audio)

            if not frame_results:
                return {
                    "total_duration": len(audio) / self.sample_rate,
                    "voice_frames": 0,
                    "silence_frames": 0,
                    "total_frames": 0,
                    "voice_ratio": 0.0,
                    "silence_ratio": 1.0,
                    "frame_duration_ms": self.frame_duration_ms,
                }

            voice_frames = sum(1 for frame in frame_results if frame["is_speech"])
            total_frames = len(frame_results)
            silence_frames = total_frames - voice_frames

            return {
                "total_duration": len(audio) / self.sample_rate,
                "voice_frames": voice_frames,
                "silence_frames": silence_frames,
                "total_frames": total_frames,
                "voice_ratio": voice_frames / total_frames,
                "silence_ratio": silence_frames / total_frames,
                "frame_duration_ms": self.frame_duration_ms,
                "aggressiveness_level": self.config.webrtc_aggressiveness,
                "frame_results": frame_results,
            }

        except Exception as e:
            self.logger.error(f"❌ Error analyzing voice activity: {e}")
            return {}
