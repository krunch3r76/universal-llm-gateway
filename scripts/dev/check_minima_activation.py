#!/usr/bin/env python
"""
Verification script: Ensure frame-probability-based minima detection is active.

This script verifies that:
1. SileroVAD.get_frame_probabilities() returns valid probabilities
2. SpeechProbabilityAnalyzer can find boundaries via "minimum" source

Usage:
    python scripts/dev/check_minima_activation.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs"))

from inference_djinn.engines.whisper.streaming.sliding_window.probability_analyzer import (
    SpeechProbabilityAnalyzer,
)


def test_analyzer_minima_detection() -> bool:
    """Test that analyzer can find boundaries via probability minima."""
    print("Testing SpeechProbabilityAnalyzer minima detection...")

    analyzer = SpeechProbabilityAnalyzer(
        min_gap_ms=500,  # High threshold so gap detection won't trigger
        min_probability_for_gap=0.3,
        search_start_ratio=0.3,
    )

    # Simulate speech timestamps with no large gaps
    # (so gap detection won't work, forcing minima detection)
    speech_timestamps = [
        {"start": 0.0, "end": 1.0},
        {"start": 1.1, "end": 2.0},  # Only 100ms gap - too short
        {"start": 2.1, "end": 3.0},  # Only 100ms gap - too short
    ]

    # Simulate probability curve with a clear low region around 2.5s
    # 32ms per frame, so 3 seconds = ~94 frames
    n_frames = 94
    probabilities = np.ones(n_frames, dtype=np.float32) * 0.8

    # Create low-probability region around frame 78 (2.5s)
    probabilities[75:82] = 0.1

    buffer_duration_ms = 3000
    min_window_ms = 1000

    candidate = analyzer.find_best_boundary(
        speech_timestamps=speech_timestamps,
        buffer_duration_ms=buffer_duration_ms,
        min_window_ms=min_window_ms,
        probabilities=probabilities,
    )

    if candidate is None:
        print("  ❌ FAIL: No boundary found")
        return False

    if candidate.source != "minimum":
        print(f"  ❌ FAIL: Expected source='minimum', got '{candidate.source}'")
        return False

    print(
        f"  ✅ PASS: Found boundary via '{candidate.source}' at {candidate.offset_samples} samples"
    )
    print(
        f"           confidence={candidate.confidence:.2f}, probability={candidate.probability:.3f}"
    )
    return True


def test_silero_get_frame_probabilities() -> bool:
    """Test that SileroVAD.get_frame_probabilities exists and returns valid array."""
    print("Testing SileroVAD.get_frame_probabilities method...")

    try:
        from inference_djinn.engines.whisper.vad import SileroVAD, VADConfig

        config = VADConfig()
        vad = SileroVAD(config, sample_rate=16000)

        # Check method exists
        if not hasattr(vad, "get_frame_probabilities"):
            print("  ❌ FAIL: get_frame_probabilities method not found")
            return False

        print("  ✅ PASS: get_frame_probabilities method exists")

        # Test with short audio (should return empty array)
        short_audio = np.zeros(100, dtype=np.float32)
        result = vad.get_frame_probabilities(short_audio)

        if result is None or len(result) != 0:
            print(
                f"  ❌ FAIL: Short audio should return empty array, got {type(result)}"
            )
            return False

        print("  ✅ PASS: Short audio returns empty array")

        # Note: Full forward-pass test requires CUDA, skip if not available
        if not vad.can_load():
            print("  ⚠️  SKIP: CUDA not available, skipping forward-pass test")
            return True

        # Test with longer audio
        audio_1s = np.random.randn(16000).astype(np.float32) * 0.1
        result = vad.get_frame_probabilities(audio_1s)

        if result is None:
            print("  ❌ FAIL: Forward pass returned None")
            return False

        if len(result) == 0:
            print("  ❌ FAIL: Forward pass returned empty array")
            return False

        if not isinstance(result, np.ndarray):
            print(f"  ❌ FAIL: Expected ndarray, got {type(result)}")
            return False

        if result.dtype != np.float32:
            print(f"  ❌ FAIL: Expected float32, got {result.dtype}")
            return False

        print(f"  ✅ PASS: Forward pass returned {len(result)} probabilities")
        print(f"           range: [{result.min():.3f}, {result.max():.3f}]")
        return True

    except ImportError as e:
        print(f"  ❌ FAIL: Import error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ FAIL: Unexpected error: {e}")
        return False


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("Frame Probability Minima Activation Verification")
    print("=" * 60)
    print()

    results = []

    # Test 1: Analyzer minima detection (no GPU required)
    results.append(("Analyzer minima detection", test_analyzer_minima_detection()))
    print()

    # Test 2: SileroVAD method existence (GPU optional)
    results.append(
        ("SileroVAD.get_frame_probabilities", test_silero_get_frame_probabilities())
    )
    print()

    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All tests passed! Frame probability minima detection is active.")
        return 0
    else:
        print("Some tests failed. Review output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
