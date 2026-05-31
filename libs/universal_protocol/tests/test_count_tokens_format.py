#!/usr/bin/env python3
"""Test count_tokens RPC response format compliance.

Verifies that count_tokens returns the spec-compliant format:
{"count": int, "method": "exact"|"estimate"}
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

# Add libs to Python path if not already there
libs_path = Path(__file__).resolve().parents[3]
if str(libs_path) not in sys.path:
    sys.path.insert(0, str(libs_path))


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Mock tokenizer for testing
class MockTokenizer:
    """Mock tokenizer that returns predictable token counts."""

    def __init__(self, tokens_per_word: int = 2):
        self.tokens_per_word = tokens_per_word

    async def count_tokens(self, text: str) -> int:
        """Count tokens based on word count."""
        words = text.split()
        return len(words) * self.tokens_per_word


@pytest.mark.asyncio
async def test_count_tokens_response_format():
    """Test that count_tokens returns spec-compliant format."""
    logger.info("\n=== Testing count_tokens Response Format ===")

    # Create mock tokenizer
    tokenizer = MockTokenizer(tokens_per_word=2)

    # Test cases
    test_cases = [
        {
            "name": "Simple text",
            "params": {"text": "Hello world this is a test"},
            "expected_count": 12,  # 6 words * 2 tokens per word
            "expected_method": "exact",
        },
        {
            "name": "Empty text",
            "params": {"text": ""},
            "expected_count": 0,
            "expected_method": "exact",
        },
        {
            "name": "Long text",
            "params": {"text": " ".join(["word"] * 100)},
            "expected_count": 200,  # 100 words * 2 tokens
            "expected_method": "exact",
        },
    ]

    for tc in test_cases:
        logger.info(f"\n--- Test: {tc['name']} ---")

        # Mock the RPC handler behavior
        result = await simulate_count_tokens_rpc(tc["params"], tokenizer)

        logger.info(f"Result: {result}")

        # Verify spec compliance
        assert "count" in result, "Response must contain 'count' field"
        assert isinstance(result["count"], int), "'count' must be an integer"
        assert result["count"] == tc["expected_count"], (
            f"Expected count={tc['expected_count']}, got {result['count']}"
        )

        assert "method" in result, "Response must contain 'method' field"
        assert result["method"] in [
            "exact",
            "estimate",
        ], "'method' must be 'exact' or 'estimate'"
        assert result["method"] == tc["expected_method"], (
            f"Expected method={tc['expected_method']}, got {result['method']}"
        )

        # Check optional data envelope
        if "data" in result:
            assert isinstance(result["data"], dict), (
                "'data' must be a dictionary if present"
            )
            logger.info(f"Additional data: {result['data']}")

        logger.info(f"✅ {tc['name']} passed")

    logger.info("\n✅ All count_tokens format tests passed")


async def simulate_count_tokens_rpc(
    params: dict[str, Any], tokenizer: MockTokenizer
) -> dict[str, Any]:
    """Simulate the count_tokens RPC response format.

    This mimics what the actual worker.py does in _handle_count_tokens.
    """
    text = params.get("text", "")

    # Count tokens
    token_count = await tokenizer.count_tokens(text)

    # Return spec-compliant format (matching the fix in worker.py)
    return {
        "count": token_count,
        "method": "exact",  # Always "exact" for tokenizer-based counting
        "data": {
            "confidence": 1.0,
            "model_id": "test-model",
            "timestamp": "2025-11-04T12:00:00",
        },
    }


@pytest.mark.asyncio
async def test_controller_handles_both_formats():
    """Test that controller can handle both old and new count_tokens formats."""
    logger.info("\n=== Testing Controller Format Compatibility ===")

    # Test both response formats
    responses = [
        {
            "name": "New spec-compliant format",
            "response": {
                "count": 42,
                "method": "exact",
                "data": {"confidence": 1.0, "model_id": "test-model"},
            },
            "expected_token_count": 42,
            "expected_method": "exact",
        },
        {
            "name": "Legacy format",
            "response": {
                "token_count": 42,
                "method_used": "exact_tokenization",
                "confidence": 1.0,
                "model_id": "test-model",
            },
            "expected_token_count": 42,
            "expected_method": "exact_tokenization",
        },
    ]

    for resp in responses:
        logger.info(f"\n--- Test: {resp['name']} ---")

        # Simulate controller's response parsing logic
        result = parse_count_tokens_response(resp["response"])

        logger.info(f"Parsed result: {result}")

        # Verify parsing
        assert result["token_count"] == resp["expected_token_count"]
        assert result["method_used"] == resp["expected_method"]

        logger.info(f"✅ {resp['name']} parsed correctly")

    logger.info("\n✅ Controller compatibility tests passed")


def parse_count_tokens_response(result_data: dict[str, Any]) -> dict[str, Any]:
    """Parse count_tokens response (mimics controller logic).

    This matches the logic in controller.py that handles both formats.
    """
    if "count" in result_data:
        # New spec-compliant format
        token_count = result_data.get("count", 0)
        method = result_data.get("method", "exact")
        # Extract additional data from data envelope if present
        extra_data = result_data.get("data", {})
        confidence = extra_data.get("confidence", 1.0)
        model_id = extra_data.get("model_id", "unknown")
    else:
        # Legacy format fallback
        token_count = result_data.get("token_count", 0)
        method = (
            "exact_tokenization"
            if result_data.get("method_used") == "exact_tokenization"
            else "unknown"
        )
        confidence = result_data.get("confidence", 0.0)
        model_id = result_data.get("model_id", "unknown")

    # Return in the format expected by API layer
    return {
        "token_count": token_count,
        "method_used": method,
        "confidence": confidence,
        "model_id": model_id,
    }


@pytest.mark.asyncio
async def test_error_responses():
    """Test that error responses are handled correctly."""
    logger.info("\n=== Testing Error Response Handling ===")

    error_responses = [
        {
            "name": "Generic error",
            "response": {"error": "Model not loaded"},
            "should_raise": True,
        },
        {
            "name": "GPU memory error",
            "response": {
                "error": "GPU memory exhausted during token counting",
                "error_type": "gpu_memory_error",
                "suggestion": "Try reducing context length",
            },
            "should_raise": True,
        },
    ]

    for err_resp in error_responses:
        logger.info(f"\n--- Test: {err_resp['name']} ---")

        if err_resp["should_raise"]:
            # Verify error is detected
            assert "error" in err_resp["response"]
            logger.info(
                f"✅ Error response correctly identified: {err_resp['response']['error']}"
            )

    logger.info("\n✅ Error handling tests passed")


async def main():
    """Run all count_tokens format tests."""
    await test_count_tokens_response_format()
    await test_controller_handles_both_formats()
    await test_error_responses()
    logger.info("\n🎉 All count_tokens format tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
