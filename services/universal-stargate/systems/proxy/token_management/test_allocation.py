"""Unit tests for allocation module"""

from .allocation import apply_safety_buffer, compute_final_max_tokens


class TestApplySafetyBuffer:
    def test_normal_case(self):
        assert apply_safety_buffer(1000, 50) == 950

    def test_exact_buffer(self):
        assert apply_safety_buffer(50, 50) == 0

    def test_exceeds_buffer(self):
        assert apply_safety_buffer(30, 50) == 0

    def test_zero_space(self):
        assert apply_safety_buffer(0, 50) == 0


class TestComputeFinalMaxTokens:
    def test_user_explicit_with_space(self):
        result = compute_final_max_tokens(
            available_generation_space=1000,
            user_requested_max_tokens=500,
            user_explicitly_specified_max_tokens=True,
            conservative_allocation_ratio=0.75,
        )
        assert result == 500

    def test_user_explicit_exceeds_space(self):
        result = compute_final_max_tokens(
            available_generation_space=300,
            user_requested_max_tokens=500,
            user_explicitly_specified_max_tokens=True,
            conservative_allocation_ratio=0.75,
        )
        assert result == 300

    def test_user_explicit_no_space(self):
        result = compute_final_max_tokens(
            available_generation_space=0,
            user_requested_max_tokens=500,
            user_explicitly_specified_max_tokens=True,
            conservative_allocation_ratio=0.75,
        )
        assert result == 500

    def test_auto_allocation(self):
        result = compute_final_max_tokens(
            available_generation_space=1000,
            user_requested_max_tokens=None,
            user_explicitly_specified_max_tokens=False,
            conservative_allocation_ratio=0.75,
        )
        assert result == 750

    def test_auto_allocation_no_space(self):
        result = compute_final_max_tokens(
            available_generation_space=0,
            user_requested_max_tokens=None,
            user_explicitly_specified_max_tokens=False,
            conservative_allocation_ratio=0.75,
        )
        assert result is None
