"""
Energy-based Voice Activity Detection implementation.
Enhanced with adaptive thresholding, vectorized computation, and temporal smoothing.

Note: This file is 304 SLOC - scheduled for refactor in Phase 2 to split
enhanced/legacy modes into separate classes.
"""

from collections import deque

import numpy as np

from .base import BaseVAD, VADConfig


class EnergyVAD(BaseVAD):
    """Enhanced Energy-based Voice Activity Detection."""

    def __init__(
        self, config: VADConfig, sample_rate: int = 16000, enhanced_mode: bool = True
    ):
        super().__init__(config, sample_rate)

        # Pre-calculate frame parameters
        self.frame_size_samples = int(
            self.sample_rate * self.config.frame_size_ms / 1000
        )

        # Enhanced mode flag
        self.enhanced_mode = enhanced_mode

        if self.enhanced_mode:
            # Adaptive noise floor estimation
            self.noise_floor_history: deque[float] = deque(maxlen=50)
            self.adaptive_factor = 1.5  # Multiplier for adaptive threshold

            # Temporal smoothing (hangover) for decision stability
            self.decision_history: deque[bool] = deque(maxlen=3)
            self.hangover_frames = 2  # Require 2 consecutive decisions to change

            # High-pass filter coefficients for DC removal (simple 1st order)
            self.dc_filter_alpha = 0.995  # High-pass cutoff ~30Hz at 16kHz
            self.dc_filter_state = 0.0

            self.logger.info(
                f"✅ Enhanced Energy VAD initialized: "
                f"threshold={self.config.energy_threshold:.4f}, "
                f"adaptive_factor={self.adaptive_factor:.1f}, "
                f"frame_size={self.config.frame_size_ms}ms, "
                f"hangover={self.hangover_frames} frames"
            )
        else:
            # Legacy mode for backward compatibility
            self.logger.info(
                f"✅ Energy VAD initialized (legacy mode): "
                f"threshold={self.config.energy_threshold:.4f}, "
                f"silence_ratio={self.config.silence_ratio_threshold:.2f}, "
                f"frame_size={self.config.frame_size_ms}ms"
            )

    def is_available(self) -> bool:
        """Energy-based VAD is always available (no external dependencies)."""
        return True

    def _preprocess_audio(self, audio: np.ndarray) -> np.ndarray:
        """
        Preprocess audio: convert to float32, normalize, and apply DC removal.

        Args:
            audio: Input audio array

        Returns:
            Preprocessed audio array (float32, -1.0 to 1.0, DC removed)
        """
        # Convert to float32 and normalize if needed
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        elif audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Clip to valid range
        audio = np.clip(audio, -1.0, 1.0)

        # Apply simple high-pass filter to remove DC offset
        filtered_audio = np.zeros_like(audio)
        for i in range(len(audio)):
            filtered_audio[i] = audio[i] - self.dc_filter_state
            self.dc_filter_state = self.dc_filter_state * self.dc_filter_alpha + audio[
                i
            ] * (1 - self.dc_filter_alpha)

        return filtered_audio

    def _compute_frame_energies(self, audio: np.ndarray) -> np.ndarray:
        """
        Compute RMS energy for each frame using vectorized operations.

        Args:
            audio: Preprocessed audio array

        Returns:
            Array of RMS energies for each frame
        """
        if len(audio) < self.frame_size_samples:
            # For very short audio, return single energy value
            return np.array([np.sqrt(np.mean(audio**2))])

        # Trim audio to multiple of frame size for clean reshaping
        n_complete_frames = len(audio) // self.frame_size_samples
        trimmed_audio = audio[: n_complete_frames * self.frame_size_samples]

        # Reshape into frames and compute RMS for each frame (vectorized)
        frames = trimmed_audio.reshape(n_complete_frames, self.frame_size_samples)
        frame_energies = np.sqrt(np.mean(frames**2, axis=1))

        # Handle remaining samples if any
        remaining_samples = len(audio) - n_complete_frames * self.frame_size_samples
        if remaining_samples >= self.frame_size_samples // 2:
            remaining_audio = audio[n_complete_frames * self.frame_size_samples :]
            remaining_energy = np.sqrt(np.mean(remaining_audio**2))
            frame_energies = np.append(frame_energies, remaining_energy)

        return frame_energies

    def _update_noise_floor(self, frame_energies: np.ndarray) -> float:
        """
        Update adaptive noise floor estimation.

        Args:
            frame_energies: Array of frame energies

        Returns:
            Current estimated noise floor
        """
        # Use 10th percentile for noise floor - represents true quiet background
        current_noise_floor = float(np.percentile(frame_energies, 10))

        # Add to history for smoothing
        self.noise_floor_history.append(current_noise_floor)

        # Return smoothed noise floor (median of recent history)
        if len(self.noise_floor_history) >= 5:
            return float(np.median(list(self.noise_floor_history)))
        else:
            return current_noise_floor

    def _compute_adaptive_threshold(self, noise_floor: float) -> float:
        """
        Compute adaptive threshold based on noise floor.

        Args:
            noise_floor: Current estimated noise floor

        Returns:
            Adaptive threshold for speech detection
        """
        # Use the higher of: adaptive threshold or configured minimum threshold
        adaptive_threshold = noise_floor * self.adaptive_factor

        # Conservative: don't let adaptive threshold go too far above static
        max_adaptive_threshold = self.config.energy_threshold * 5.0
        adaptive_threshold = min(adaptive_threshold, max_adaptive_threshold)

        return max(adaptive_threshold, self.config.energy_threshold)

    def _apply_temporal_smoothing(self, current_decision: bool) -> bool:
        """
        Apply temporal smoothing (hangover) to reduce decision jitter.

        Args:
            current_decision: Raw decision from current frame analysis

        Returns:
            Smoothed decision after hangover logic
        """
        self.decision_history.append(current_decision)

        # For the first few decisions, just return the current decision
        if len(self.decision_history) < self.hangover_frames:
            return current_decision

        # Get recent decisions for analysis
        recent_decisions = list(self.decision_history)[-self.hangover_frames :]

        # If all recent decisions agree, use that decision (strong consensus)
        if all(recent_decisions):  # All recent decisions are BOUNDARY
            return True
        elif not any(recent_decisions):  # All recent decisions are SPEECH
            return False

        # Mixed decisions - use majority vote among recent decisions
        boundary_count = sum(recent_decisions)
        return boundary_count > (self.hangover_frames // 2)

    def detect_boundary(self, audio: np.ndarray, check_duration_ms: int) -> bool | None:
        """
        Detect speech boundary using energy-based analysis.

        Args:
            audio: Audio segment to analyze
            check_duration_ms: Duration of audio segment in milliseconds

        Returns:
            True if silence boundary detected (send to transcription)
            False if speech is ongoing (keep accumulating audio)
            None if no speech detected (skip transcription entirely)
        """
        if not self.validate_audio(audio):
            return False

        try:
            if self.enhanced_mode:
                return self._detect_boundary_enhanced(audio, check_duration_ms)
            else:
                return self._detect_boundary_legacy(audio, check_duration_ms)

        except Exception as e:
            self.logger.error(f"❌ Energy VAD error: {e}")
            return False

    def _detect_boundary_enhanced(
        self, audio: np.ndarray, check_duration_ms: int
    ) -> bool | None:
        """Enhanced boundary detection with adaptive thresholding."""
        # Preprocess audio (normalize, DC removal)
        processed_audio = self._preprocess_audio(audio)

        # Compute frame energies (vectorized)
        frame_energies = self._compute_frame_energies(processed_audio)

        if len(frame_energies) == 0:
            return None  # Empty audio - no speech to transcribe

        # Update adaptive noise floor
        noise_floor = self._update_noise_floor(frame_energies)

        # Compute adaptive threshold
        adaptive_threshold = self._compute_adaptive_threshold(noise_floor)

        # Determine silent frames
        silent_frames = frame_energies < adaptive_threshold

        # Calculate silent duration in milliseconds
        silent_frame_count = int(np.sum(silent_frames))
        silent_duration_ms = silent_frame_count * self.config.frame_size_ms

        # Check if entire audio is silence - return None to skip transcription
        if silent_frame_count == len(frame_energies):
            self.logger.debug(
                f"⚡ Enhanced Energy VAD: Entire audio is silence "
                f"({len(frame_energies)} frames) - skipping transcription"
            )
            return None

        # Decision logic: use millisecond-based if min_silence_duration_ms configured
        if self.config.min_silence_duration_ms > 0:
            # New enhanced logic: millisecond-based decision
            raw_decision = silent_duration_ms >= self.config.min_silence_duration_ms
        else:
            # Legacy logic: ratio-based decision for backward compatibility
            silence_ratio = (
                silent_frame_count / len(frame_energies)
                if len(frame_energies) > 0
                else 0.0
            )
            raw_decision = silence_ratio >= self.config.silence_ratio_threshold

        # Apply temporal smoothing
        smoothed_decision = self._apply_temporal_smoothing(raw_decision)

        self.logger.debug(
            f"⚡ Enhanced Energy VAD: "
            f"frames={len(frame_energies)}, silent_frames={silent_frame_count}, "
            f"silent_ms={silent_duration_ms:.0f}, "
            f"noise_floor={noise_floor:.4f}, thresh={adaptive_threshold:.4f}, "
            f"raw={raw_decision}, smoothed={smoothed_decision}"
        )

        return smoothed_decision

    def _detect_boundary_legacy(
        self, audio: np.ndarray, check_duration_ms: int
    ) -> bool | None:
        """Legacy boundary detection for backward compatibility."""
        # Simple preprocessing - just convert to float32
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Calculate overall energy metrics
        rms_energy = np.sqrt(np.mean(audio**2))

        # Handle short audio segments
        if len(audio) < self.frame_size_samples:
            is_silent = rms_energy < self.config.energy_threshold
            self.logger.debug(
                f"⚡ Energy boundary check (short): "
                f"rms={rms_energy:.4f}, threshold={self.config.energy_threshold:.4f}, "
                f"silent={is_silent}"
            )
            return None if is_silent else False

        # Frame-based analysis for longer segments
        silent_frames = 0
        total_frames = 0

        for i in range(0, len(audio), self.frame_size_samples):
            frame = audio[i : i + self.frame_size_samples]
            if len(frame) < self.frame_size_samples // 2:
                continue

            frame_energy = np.sqrt(np.mean(frame**2))
            if frame_energy < self.config.energy_threshold:
                silent_frames += 1
            total_frames += 1

        # Calculate silence ratio and determine boundary
        silence_ratio = silent_frames / total_frames if total_frames > 0 else 0.0

        # If entire audio is silence, return None to skip transcription
        if silence_ratio >= 0.95:  # 95% or more silence
            self.logger.debug(
                f"⚡ Energy VAD (legacy): Mostly silence "
                f"(ratio={silence_ratio:.3f}) - skipping transcription"
            )
            return None

        boundary_detected = silence_ratio >= self.config.silence_ratio_threshold

        self.logger.debug(
            f"⚡ Energy boundary analysis (legacy): "
            f"rms_energy={rms_energy:.4f}, "
            f"silent_frames={silent_frames}/{total_frames}, "
            f"silence_ratio={silence_ratio:.3f}, "
            f"threshold={self.config.silence_ratio_threshold:.3f}, "
            f"boundary={boundary_detected}"
        )

        return boundary_detected

    def get_energy_stats(self, audio: np.ndarray) -> dict:
        """
        Get detailed energy statistics for debugging.

        Args:
            audio: Audio segment to analyze

        Returns:
            Dictionary with comprehensive energy statistics
        """
        if not self.validate_audio(audio):
            return {}

        try:
            if self.enhanced_mode:
                return self._get_energy_stats_enhanced(audio)
            else:
                return self._get_energy_stats_legacy(audio)

        except Exception as e:
            self.logger.error(f"❌ Error calculating energy stats: {e}")
            return {"error": str(e)}

    def _get_energy_stats_enhanced(self, audio: np.ndarray) -> dict:
        """Get enhanced energy statistics."""
        # Preprocess audio
        processed_audio = self._preprocess_audio(audio)

        # Compute frame energies
        frame_energies = self._compute_frame_energies(processed_audio)

        if len(frame_energies) == 0:
            return {"error": "No frames computed"}

        # Update noise floor for accurate stats
        noise_floor = self._update_noise_floor(frame_energies)
        adaptive_threshold = self._compute_adaptive_threshold(noise_floor)

        # Overall statistics
        rms_energy = float(np.sqrt(np.mean(processed_audio**2)))
        max_amplitude = float(np.max(np.abs(processed_audio)))

        # Frame-level statistics
        silent_frames = frame_energies < adaptive_threshold
        silent_frame_count = int(np.sum(silent_frames))
        silent_duration_ms = silent_frame_count * self.config.frame_size_ms

        return {
            "rms_energy": rms_energy,
            "max_amplitude": max_amplitude,
            "duration_ms": len(processed_audio) / self.sample_rate * 1000,
            "frame_count": len(frame_energies),
            "frame_energy_mean": float(np.mean(frame_energies)),
            "frame_energy_std": float(np.std(frame_energies)),
            "noise_floor": float(noise_floor),
            "adaptive_threshold": float(adaptive_threshold),
            "static_threshold": self.config.energy_threshold,
            "silent_frame_count": silent_frame_count,
            "silent_duration_ms": float(silent_duration_ms),
            "silence_ratio": float(silent_frame_count / len(frame_energies)),
        }

    def _get_energy_stats_legacy(self, audio: np.ndarray) -> dict:
        """Get legacy energy statistics."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        rms_energy = float(np.sqrt(np.mean(audio**2)))
        max_amplitude = float(np.max(np.abs(audio)))

        # Frame-level statistics
        frame_energies = []
        for i in range(0, len(audio), self.frame_size_samples):
            frame = audio[i : i + self.frame_size_samples]
            if len(frame) >= self.frame_size_samples // 2:
                frame_energy = np.sqrt(np.mean(frame**2))
                frame_energies.append(frame_energy)

        frame_energies_arr = np.array(frame_energies)

        return {
            "rms_energy": rms_energy,
            "max_amplitude": max_amplitude,
            "frame_count": len(frame_energies_arr),
            "frame_energy_mean": float(np.mean(frame_energies_arr))
            if len(frame_energies_arr) > 0
            else 0.0,
            "silence_ratio": float(
                np.mean(frame_energies_arr < self.config.energy_threshold)
            )
            if len(frame_energies_arr) > 0
            else 0.0,
        }

    def reset_state(self):
        """Reset internal state (useful for new audio sessions)."""
        if self.enhanced_mode:
            self.noise_floor_history.clear()
            self.decision_history.clear()
            self.dc_filter_state = 0.0
            self.logger.debug("🔄 Enhanced Energy VAD state reset")
        else:
            self.logger.debug("🔄 Energy VAD state reset (legacy - no state)")
