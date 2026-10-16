"""Pytest plugin: hermetic network guard (import via pytest_plugins in test trees)."""

from __future__ import annotations

pytest_plugins = ["scripts.model_manager.testing.hermetic_network_guard"]
