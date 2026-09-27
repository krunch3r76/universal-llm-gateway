"""Registration-file pin: one config key, local path is not Jupiter."""

from __future__ import annotations

import pytest

from claude_bundles.cdp_registry_store import (
    ACTIVE_JSON,
    REGISTRATION_REGISTRY_SSH_ENV,
)

pytestmark = pytest.mark.offline


def test_registration_registry_ssh_env_is_the_pin() -> None:
    assert REGISTRATION_REGISTRY_SSH_ENV == "WHAT_IS_RUNNING_REGISTRY_SSH"
    assert ACTIVE_JSON.name == "active.json"
    assert ACTIVE_JSON.parent.name == "cdp-registry"
