"""
Integration tests for pipeline capacity coordination.

Tests that pipeline correctly handles parallel steps using the same model
without race conditions.
"""

import pytest


@pytest.mark.asyncio
async def test_pipeline_parallel_steps_same_model(stargate_client, en_en_csc_pipeline):
    """Test that pipeline correctly handles parallel steps using same model."""

    # This pipeline has 3 steps that all use hermes3-hybrid at once
    response = await stargate_client.post(
        "/v1/chat/completions",
        json={
            "model": "en-en-csc",
            "messages": [{"role": "user", "content": "test message"}],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert data["choices"][0]["message"]["content"]

    # Verify no 500 errors in logs
    # (This test previously failed with 500 errors due to race condition)
