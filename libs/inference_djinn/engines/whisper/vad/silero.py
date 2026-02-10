"""
Silero VAD implementation for speech boundary detection.
Core detection logic - utilities extracted to silero_utils.py.

Architecture: Lazy initialization - torch.hub.load() and model.to(device)
are deferred until first use or explicit ensure_loaded() call.
"""

import asyncio
from typing import Any

import numpy as np
import torch

from .base import BaseVAD, VADConfig
from .silero_utils import (
    SILERO_FRAME_MS,
    SILERO_FRAME_SAMPLES_8K,
    SILERO_FRAME_SAMPLES_16K,
    align_to_frame_boundary,
    analyze_speech_activity,
    calculate_frame_boundaries,
    get_audio_frame_info,
    split_into_frames,
)


class SileroVAD(BaseVAD):
    """
    GPU-accelerated Silero VAD for speech boundary detection.

    Initialization is lazy - no blocking torch.hub.load() in __init__.
    Call ensure_loaded_async() in async contexts or _ensure_loaded_sync()
    will be called automatically on first use.
    """

    def __init__(self, config: VADConfig, sample_rate: int = 16000):
        super().__init__(config, sample_rate)

        # Model state - None until loaded
        self._silero_model = None
        self._silero_utils = None
        self._device = None
        self._load_attempted = False
        self._load_error: Exception | None = None

        # Set frame constants based on sample rate
        if sample_rate == 16000:
            self.frame_samples = SILERO_FRAME_SAMPLES_16K
        elif sample_rate == 8000:
            self.frame_samples = SILERO_FRAME_SAMPLES_8K
        else:
            raise ValueError(
                f"Silero VAD only supports 16kHz and 8kHz, got {sample_rate}Hz"
            )

    def can_load(self) -> bool:
        """Check if Silero VAD can potentially be loaded (CUDA available)."""
        return torch.cuda.is_available()

    def is_available(self) -> bool:
        """Check if Silero VAD is loaded and ready."""
        return self._silero_model is not None

    def _load_model_sync(self) -> None:
        """
        Synchronously load Silero model. BLOCKING - use ensure_loaded_async()
        in async contexts.
        """
        if self._load_attempted:
            return

        self._load_attempted = True

        if not torch.cuda.is_available():
            self._load_error = RuntimeError("CUDA not available")
            self.logger.warning("CUDA not available - Silero VAD disabled")
            return

        try:
            self.logger.info("⏳ Loading Silero VAD model (blocking)...")

            self._silero_model, self._silero_utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                verbose=False,
            )

            device = torch.device("cuda")
            self._silero_model = self._silero_model.to(device)
            self._device = device

            self.logger.info(f"✅ Silero VAD loaded on {device}")

        except Exception as e:
            self._load_error = e
            self.logger.error(f"❌ Failed to load Silero VAD: {e}")
            self._silero_model = None
            self._silero_utils = None
            self._device = None

    async def ensure_loaded_async(self) -> bool:
        """
        Asynchronously ensure model is loaded. Non-blocking.

        Returns:
            True if model is available, False otherwise.
        """
        if self._silero_model is not None:
            return True

        if self._load_attempted:
            return self._silero_model is not None

        if not torch.cuda.is_available():
            self._load_attempted = True
            self._load_error = RuntimeError("CUDA not available")
            self.logger.warning("CUDA not available - Silero VAD disabled")
            return False

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model_sync)
        return self._silero_model is not None

    def _ensure_loaded_sync(self) -> None:
        """Ensure model is loaded, blocking if necessary."""
        if self._silero_model is None and not self._load_attempted:
            self._load_model_sync()

    def detect_boundary(self, audio: np.ndarray, check_duration_ms: int) -> bool | None:
        """
        Detect speech boundary using Silero VAD.

        Note: Will block on first call if model not pre-loaded via
        ensure_loaded_async(). Prefer pre-loading in async setup.
        """
        if not self.validate_audio(audio):
            return False

        # Lazy load if needed (blocking)
        self._ensure_loaded_sync()

        if not self.is_available():
            raise RuntimeError(
                "Silero VAD not available – caller should switch to another method"
            )

        try:
            speech_timestamps = self.get_speech_timestamps(audio)

            if not speech_timestamps:
                audio_duration_ms = (len(audio) / self.sample_rate) * 1000
                audio_max = np.max(np.abs(audio))
                audio_rms = np.sqrt(np.mean(audio**2))
                self.logger.debug(
                    f"🔇 Silero: No speech in {audio_duration_ms:.0f}ms "
                    f"(max={audio_max:.4f}, rms={audio_rms:.6f}, "
                    f"thresh={self.config.silero_threshold}) - skipping"
                )
                return None

            last_speech_end = speech_timestamps[-1]["end"]
            audio_duration = len(audio) / self.sample_rate
            silence_duration = audio_duration - last_speech_end
            min_silence_duration = self.config.min_silence_duration_ms / 1000

            boundary_detected = silence_duration >= min_silence_duration

            self.logger.debug(
                f"🔍 Silero boundary: last_speech_end={last_speech_end:.3f}s, "
                f"audio_duration={audio_duration:.3f}s, "
                f"silence={silence_duration:.3f}s, "
                f"min_required={min_silence_duration:.3f}s, "
                f"boundary={boundary_detected}"
            )

            return boundary_detected

        except Exception as e:
            self.logger.error(f"❌ Silero VAD boundary detection error: {e}")
            raise

    def _chunk_audio_for_silero(self, audio: np.ndarray) -> list[np.ndarray]:
        """Split audio into fixed-size frames for Silero TorchScript model."""
        frame_size = 512 if self.sample_rate == 16000 else 256
        if len(audio) < frame_size:
            return []

        usable_len = (len(audio) // frame_size) * frame_size
        if usable_len == 0:
            return []

        trimmed = audio[:usable_len]
        frames = trimmed.reshape(-1, frame_size)
        return list(frames)

    def get_speech_timestamps(self, audio: np.ndarray) -> list[dict[str, Any]]:
        """Return detailed speech timestamps using Silero helper."""
        if not self.validate_audio(audio):
            return []

        min_required_samples = 512
        if len(audio) < min_required_samples:
            return []

        if (
            self.is_available()
            and self._silero_utils
            and "get_speech_timestamps" in self._silero_utils
        ):
            try:
                get_ts = self._silero_utils["get_speech_timestamps"]
                audio_tensor = torch.from_numpy(audio.astype(np.float32)).to(
                    self._device, dtype=torch.float32
                )

                with torch.no_grad():
                    ts = get_ts(
                        audio_tensor,
                        self._silero_model,
                        sampling_rate=self.sample_rate,
                        threshold=self.config.silero_threshold,
                    )

                del audio_tensor
                torch.cuda.empty_cache()

                detailed = []
                for i, seg in enumerate(ts):
                    detailed.append(
                        {
                            "id": i,
                            "start": seg["start"] / self.sample_rate,
                            "end": seg["end"] / self.sample_rate,
                            "duration": (seg["end"] - seg["start"]) / self.sample_rate,
                            "confidence": 1.0,
                        }
                    )
                return detailed

            except Exception as e:
                self.logger.warning(
                    f"⚠️ Upstream get_speech_timestamps failed: {e} – using fallback"
                )

        # Fallback to internal chunk-wise logic
        try:
            chunks = self._chunk_audio_for_silero(audio)
            if not chunks:
                return []

            chunk_duration = len(chunks[0]) / self.sample_rate

            speech_probs = []
            for chunk in chunks:
                with torch.no_grad():
                    audio_tensor = torch.from_numpy(chunk.astype(np.float32)).to(
                        self._device, dtype=torch.float32
                    )
                    prob = self._silero_model(audio_tensor, self.sample_rate).item()
                    speech_probs.append(prob)
                    del audio_tensor

            torch.cuda.empty_cache()

            speech_segments = []
            current_segment = None
            threshold = self.config.silero_threshold

            for i, prob in enumerate(speech_probs):
                start_time = i * chunk_duration
                end_time = (i + 1) * chunk_duration
                is_speech = prob >= threshold

                if is_speech:
                    if current_segment is None:
                        current_segment = {"start": start_time, "end": end_time}
                    else:
                        current_segment["end"] = end_time
                else:
                    if current_segment is not None:
                        speech_segments.append(current_segment)
                        current_segment = None

            if current_segment is not None:
                speech_segments.append(current_segment)

            return [
                {
                    "id": i,
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": seg["end"] - seg["start"],
                    "confidence": 1.0,
                }
                for i, seg in enumerate(speech_segments)
            ]

        except Exception as e:
            self.logger.error(f"❌ Error getting speech timestamps (fallback): {e}")
            return []

    def analyze_speech_activity(self, audio: np.ndarray) -> dict[str, Any]:
        """Perform detailed speech activity analysis."""
        if not self.validate_audio(audio):
            return {}

        try:
            speech_timestamps = self.get_speech_timestamps(audio)
            audio_duration = len(audio) / self.sample_rate

            return analyze_speech_activity(
                speech_timestamps, audio_duration, self.sample_rate
            )

        except Exception as e:
            self.logger.error(f"❌ Error analyzing speech activity: {e}")
            return {}

    # Frame alignment utilities (delegated to silero_utils)

    def calculate_frame_boundaries(self, duration_ms: float) -> dict[str, Any]:
        """Calculate 32ms frame boundaries for a given duration."""
        return calculate_frame_boundaries(
            duration_ms, self.sample_rate, self.frame_samples
        )

    def align_to_frame_boundary(self, byte_offset: int, align_up: bool = False) -> int:
        """Align a byte offset to the nearest 32ms frame boundary."""
        return align_to_frame_boundary(byte_offset, self.frame_samples, align_up)

    def get_audio_frame_info(self, audio: np.ndarray) -> dict[str, Any]:
        """Get 32ms frame alignment information for audio array."""
        return get_audio_frame_info(audio, self.sample_rate, self.frame_samples)

    def log_frame_alignment_info(self, audio: np.ndarray):
        """Log audio frame alignment information for debugging."""
        info = self.get_audio_frame_info(audio)
        self.logger.debug(
            f"📐 Audio {SILERO_FRAME_MS}ms alignment: "
            f"{info['complete_frames']} complete frames "
            f"({info['complete_frame_bytes']} bytes, "
            f"{info['frame_aligned_duration_ms']:.0f}ms) "
            f"+ {info['remainder_samples']} remainder samples"
        )

    def split_into_frames(
        self, audio: np.ndarray, require_complete_frames: bool = True
    ) -> list[np.ndarray]:
        """Split audio into 32ms frames suitable for Silero VAD processing."""
        if not self.validate_audio(audio):
            return []

        frames = split_into_frames(audio, self.frame_samples, require_complete_frames)
        self.logger.debug(
            f"📐 Split {len(audio)} samples into {len(frames)} frames "
            f"(complete: {require_complete_frames})"
        )

        return frames

    def get_frame_probabilities(self, audio_float: np.ndarray) -> np.ndarray:
        """
        Get frame-level speech probabilities for audio buffer.

        Runs Silero model on fixed-size frames to obtain per-frame speech
        likelihood. Used by boundary finder for local minima detection.

        Args:
            audio_float: Audio samples as float32 in range [-1.0, 1.0].
                         Must be 16kHz sample rate.

        Returns:
            1-D numpy array of float32 speech probabilities in [0.0, 1.0].
            Length = ⌈len(audio_float) / frame_size⌉ where frame_size=512.
            Returns empty array if audio is shorter than one frame.
            Guaranteed contiguous, C-order.

        Note:
            This is a synchronous, CPU/GPU-bound operation. It runs in a
            background worker thread, not in the request path.
        """
        frame_size = 512 if self.sample_rate == 16000 else 256

        if len(audio_float) < frame_size:
            return np.array([], dtype=np.float32)

        self._ensure_loaded_sync()

        if not self.is_available():
            return np.array([], dtype=np.float32)

        try:
            chunks = self._chunk_audio_for_silero(audio_float)
            if not chunks:
                return np.array([], dtype=np.float32)

            probabilities = []
            for chunk in chunks:
                with torch.no_grad():
                    audio_tensor = torch.from_numpy(chunk.astype(np.float32)).to(
                        self._device, dtype=torch.float32
                    )
                    prob = self._silero_model(audio_tensor, self.sample_rate).item()
                    probabilities.append(prob)
                    del audio_tensor

            torch.cuda.empty_cache()

            return np.ascontiguousarray(probabilities, dtype=np.float32)

        except Exception as e:
            self.logger.warning(f"⚠️ get_frame_probabilities failed: {e}")
            return np.array([], dtype=np.float32)

    def find_speech_boundary_from_timestamps(
        self,
        audio: np.ndarray,
        min_window_duration_s: float,
        use_second_nearest: bool = False,
    ) -> int | None:
        """
        Find optimal speech boundary using Silero timestamps.

        Returns the sample index of the speech boundary, or None if no good boundary found.
        """
        if not self.validate_audio(audio):
            return None

        try:
            speech_timestamps = self.get_speech_timestamps(audio)

            if not speech_timestamps:
                return None

            # Find the last speech segment
            last_segment = speech_timestamps[-1]
            last_speech_end_seconds = last_segment["end"]

            # Convert to sample index
            boundary_sample = int(last_speech_end_seconds * self.sample_rate)

            # Ensure we have enough audio for min_window_duration
            min_window_samples = int(min_window_duration_s * self.sample_rate)
            audio_duration_samples = len(audio)

            # If using second nearest and we have multiple segments
            if use_second_nearest and len(speech_timestamps) > 1:
                second_last = speech_timestamps[-2]
                boundary_sample = int(second_last["end"] * self.sample_rate)

            # Don't cut too early
            if boundary_sample < min_window_samples:
                return None

            # Don't go beyond audio length
            if boundary_sample >= audio_duration_samples:
                return None

            return boundary_sample

        except Exception as e:
            self.logger.error(f"❌ Error finding speech boundary: {e}")
            return None
