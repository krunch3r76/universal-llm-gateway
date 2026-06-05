"""Test session setup for agent_seat tests.

Sets fake cloud API keys so ``resolve_llm_adapter`` returns a real adapter
instance without requiring live credentials. Tests inject fake ``send_native``
callables, so no actual HTTP calls are made.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def fake_cloud_api_keys() -> None:
    """Ensure provider adapters resolve for all agent_seat tests."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-anthropic")
    os.environ.setdefault("OPENAI_API_KEY", "test-key-openai")
    os.environ.setdefault("XAI_API_KEY", "test-key-xai")
    os.environ.setdefault("GOOGLE_API_KEY", "test-key-google")
